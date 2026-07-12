#!/usr/bin/env python3
from __future__ import annotations
import argparse,json,os,statistics,time
from collections import Counter
from pathlib import Path
from semantic_alignment.bakeoff import ProviderClient,compare_n_results,create_four_way_blinding
from semantic_alignment.disagreement import disagreement,safeguarded_policies
from semantic_alignment.io import atomic_write_json,atomic_write_text,read_json
from semantic_alignment.large_bakeoff import (CEILINGS,COMBINED_CEILING,PROVIDERS,SharedBudget,Worker,
    build_manifest,preflight,run_concurrent,verify_manifest)
from semantic_alignment.meta_critic import analyse_case,policy_actions,priority

SOURCE=Path('semantic_alignment_research/runs/v2_20260711T111526Z'); OLD=Path('semantic_alignment_research/provider_bakeoff_25_20260712'); DEFAULT_RUN=Path('semantic_alignment_research/provider_bakeoff_250_20260712_v1')
def load_inputs():
    q=json.load(open(SOURCE/'quote_semantic_fingerprints.json'))['items']; i=json.load(open(SOURCE/'image_implied_messages_generated.json'))['items']; old=json.load(open(OLD/'cases.json'))['items']; val=json.load(open(SOURCE/'manual_validation_cases.json'))['items']; return q,i,old,val
def args_parser():
    p=argparse.ArgumentParser();p.add_argument('--run-dir',type=Path,default=DEFAULT_RUN);sub=p.add_subparsers(dest='command',required=True);sub.add_parser('prepare');sub.add_parser('dry-run');sub.add_parser('compare');e=sub.add_parser('execute');e.add_argument('--execute-grok',action='store_true');e.add_argument('--execute-openai',action='store_true');e.add_argument('--execute-claude',action='store_true');e.add_argument('--execute-gemini',action='store_true');e.add_argument('--confirm-grok-cost-limit-usd',type=float);e.add_argument('--confirm-openai-cost-limit-usd',type=float);e.add_argument('--confirm-claude-cost-limit-usd',type=float);e.add_argument('--confirm-gemini-cost-limit-usd',type=float);e.add_argument('--confirm-combined-cost-limit-usd',type=float);return p
def prepare(run,q,i,old,val):
    run.mkdir(parents=True,exist_ok=True);path=run/'cases.json'
    if not path.exists():atomic_write_json(path,build_manifest(q,i,old,val))
    manifest=json.load(open(path));verify_manifest(manifest,q,i,old);return manifest
def write_preflight(run,pf):
    lines=['# 250-case provider bake-off preflight','',f'- Cases: 250',f'- Combined expected cost: ${pf["combined_expected_cost_usd"]:.2f}',f'- Combined conservative known cost: ${pf["combined_conservative_known_cost_usd"]:.2f}',f'- Combined ceiling: ${pf["combined_ceiling_usd"]:.2f}','','| Provider | Model | Input tokens | Expected output | Expected cost | Base maximum | Single retry reserve | All-retry exposure | Ceiling |','|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for p,x in pf['providers'].items():lines.append(f'| {p} | {x["model"]} | {x["estimated_input_tokens"]} | {x["estimated_output_tokens"]} | ${x["expected_cost_usd"]:.2f} | ${x["conservative_maximum_base_cost_usd"]:.2f} | ${x["conservative_single_retry_reserve_usd"]:.2f} | ${x["maximum_ambiguous_exposure_usd"]:.2f} | ${x["ceiling_usd"]:.2f} |')
    lines += ['','All tools, search, grounding, retrieval and code execution are disabled. Four workers run concurrently, one active request per provider. Every next attempt is independently ceiling-gated. The theoretical all-case retry exposure cannot be incurred beyond the hard ceilings.','', 'Projected sequential duration: approximately 100–170 minutes from 25-case observed latencies. Projected concurrent duration: approximately 30–50 minutes.']
    atomic_write_text(run/'preflight_report.md','\n'.join(lines)+'\n');atomic_write_json(run/'preflight.json',pf)
def compare(run,manifest,q,i):
    results={p:read_json(run/f'{p}_results.json',{}) or {'items':{},'failures':{}} for p in PROVIDERS}; complete=[c for c in manifest['items'] if all(c['case_id'] in results[p].get('items',{}) for p in PROVIDERS)]; missing={p:[c['case_id'] for c in manifest['items'] if c['case_id'] not in results[p].get('items',{})] for p in PROVIDERS}
    comp=compare_n_results(complete,results) if complete else {'cases':0};comp['manifest_cases']=250;comp['four_provider_complete_cases']=len(complete);comp['missing_by_provider']={p:len(x) for p,x in missing.items()};comp['pairwise_full_denominators']={}
    for index,left in enumerate(PROVIDERS):
        for right in PROVIDERS[index+1:]:
            pair_cases=[c for c in manifest['items'] if c['case_id'] in results[left].get('items',{}) and c['case_id'] in results[right].get('items',{})]; pair=compare_n_results(pair_cases,{left:results[left],right:results[right]});comp['pairwise_full_denominators'][f'{left}_vs_{right}']={'cases':len(pair_cases),'comparison':next(iter(pair['pairwise'].values()))}
    atomic_write_json(run/'comparison.json',comp);create_four_way_blinding(run)
    metas=[];dis=[];queue=[]
    for c in complete:
        rows={p:results[p]['items'][c['case_id']] for p in PROVIDERS};m=analyse_case(c,rows);d=disagreement(c['case_id'],rows);m.update({'disagreement_score':d['disagreement_score'],'disagreement_band':d['disagreement_band']});metas.append(m);dis.append(d);band,reasons,value=priority(m,free_trade=c['selection_reason']=='original_25_regression' and c['case_id']=='55f5cd6d633c143f5170');queue.append({'case_id':c['case_id'],'priority':band,'review_reasons':reasons,'disagreement_reasons':d['explanation'],'disagreement_score':d['disagreement_score'],'disagreement_band':d['disagreement_band'],'recommended_action':m['recommended_action'],'confidence':m['confidence'],'automatic':not m['requires_human_review'],'provider_decisions':{p:rows[p]['keep_or_replace'] for p in PROVIDERS},'regression_case':bool(c.get('original_case_id')),'human_label':None})
    # Preserve old labels only as a labelled regression subset.
    old_h=read_json(Path('semantic_alignment_research/meta_critic/human_validation.json'),{}) or {}
    for x in queue:
        if x['case_id'] in old_h.get('items',{}):x['human_label']=old_h['items'][x['case_id']]['human_action']
    complete_ids={x['case_id'] for x in complete}
    for c in manifest['items']:
        if c['case_id'] in complete_ids:continue
        decisions={p:results[p].get('items',{}).get(c['case_id'],{}).get('keep_or_replace','missing') for p in PROVIDERS};queue.append({'case_id':c['case_id'],'priority':'urgent','review_reasons':['provider_retry_exhaustion'],'disagreement_reasons':['Gemini exhausted after the authorised retry.'],'disagreement_score':None,'disagreement_band':'unavailable','recommended_action':'defer','confidence':'insufficient','automatic':False,'provider_decisions':decisions,'regression_case':bool(c.get('original_case_id')),'human_label':None})
    rank={'urgent':0,'high':1,'medium':2,'low':3,'control':4};queue.sort(key=lambda x:(0 if 'provider_retry_exhaustion' in x['review_reasons'] else 1,rank[x['priority']],-(x['disagreement_score'] or -1),x['case_id']))
    policies={p:0 for p in 'ABCDEFGH'}
    for m,d in zip(metas,dis):
        base=policy_actions(m);extra=safeguarded_policies(m,d)
        for p in 'ABCDE':policies[p]+=base[p] in {'keep','replace'}
        for p in 'FGH':policies[p]+=extra[p] in {'keep','replace'}
    atomic_write_json(run/'meta_results.json',{'schema_version':1,'items':metas});atomic_write_json(run/'disagreement_results.json',{'schema_version':1,'items':dis});atomic_write_json(run/'human_review_queue.json',{'schema_version':1,'items':queue});atomic_write_json(run/'policy_comparison.json',{'schema_version':1,'denominator_complete_cases':len(complete),'automatic_coverage':{p:{'resolved':n,'coverage':n/len(complete) if complete else None} for p,n in policies.items()}})
    summaries={p:read_json(run/f'{p}_worker_summary.json',{}) for p in PROVIDERS};known=sum((x or {}).get('known_cost_usd',0) for x in summaries.values()); wall=(read_json(run/'coordinator_summary.json',{}) or {}).get('wall_clock_seconds');
    lines=['# 250-case four-provider critic execution report','',f'- Manifest cases: 250',f'- Four-provider complete cases: {len(complete)}',f'- Missing: {comp["missing_by_provider"]}',f'- Combined known spend: ${known:.6f}',f'- Wall-clock seconds: {wall}',f'- Meta automatic coverage: {sum(not x["requires_human_review"] for x in metas)}/{len(metas)}' if metas else '- Meta: unavailable',f'- Disagreement bands: {dict(Counter(x["disagreement_band"] for x in dis))}','','## Provider completion','']
    for p in PROVIDERS:
        s=summaries[p] or {};lines.append(f'- {p}: completed={s.get("completed")}, exhausted={s.get("exhausted")}, attempts={s.get("attempts")}, cost=${s.get("known_cost_usd",0):.6f}, elapsed={s.get("elapsed_seconds")}, uncertain exposure=${s.get("uncertain_possible_exposure_usd",0):.6f}')
    lines += ['','## Safety','','Fingerprints were reused. The original 25 are preserved as regression cases. No tools/search were enabled. No production files or behaviour were changed.']
    atomic_write_text(run/'execution_report.md','\n'.join(lines)+'\n');return comp
def main(argv=None):
    a=args_parser().parse_args(argv);run=a.run_dir.resolve();q,i,old,val=load_inputs();manifest=prepare(run,q,i,old,val);pf=preflight(manifest,q);write_preflight(run,pf)
    if a.command=='prepare':print('cases=250');return 0
    if a.command=='dry-run':print(json.dumps(pf,indent=2));return 0
    if a.command=='compare':compare(run,manifest,q,i);return 0
    flags={'grok':a.execute_grok,'openai':a.execute_openai,'anthropic':a.execute_claude,'gemini':a.execute_gemini};limits={'grok':a.confirm_grok_cost_limit_usd,'openai':a.confirm_openai_cost_limit_usd,'anthropic':a.confirm_claude_cost_limit_usd,'gemini':a.confirm_gemini_cost_limit_usd}
    if not all(flags.values()):raise SystemExit('all four explicit execution flags required')
    if a.confirm_combined_cost_limit_usd!=45 or any(limits[p]!=CEILINGS[p] for p in PROVIDERS):raise SystemExit('exact approved ceilings required')
    if any(pf['providers'][p]['conservative_maximum_base_cost_usd']>CEILINGS[p] for p in PROVIDERS) or pf['combined_conservative_known_cost_usd']>COMBINED_CEILING:raise SystemExit('preflight exceeds ceiling')
    env={'grok':'XAI_API_KEY','openai':'OPENAI_API_KEY','anthropic':'ANTHROPIC_API_KEY','gemini':'GEMINI_API_KEY'};clients={p:ProviderClient(p,os.getenv(env[p]) or (os.getenv('GOOGLE_API_KEY','') if p=='gemini' else '')) for p in PROVIDERS};budget=SharedBudget(run);workers=[Worker(p,manifest['items'],q,i,run,clients[p],budget) for p in PROVIDERS];summary=run_concurrent(workers);atomic_write_json(run/'coordinator_summary.json',summary);compare(run,manifest,q,i);print(json.dumps(summary,indent=2));return 0
if __name__=='__main__':raise SystemExit(main())
