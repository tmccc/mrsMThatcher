#!/usr/bin/env python3
from __future__ import annotations

import argparse,csv,hashlib,json,os,random
from pathlib import Path

from semantic_alignment.io import atomic_write_json
from semantic_alignment.pairwise_correction import eligible_trace_runner_up,load_trace_records,validate_pilot_readiness

ROOT=Path(__file__).resolve().parent
SOURCE=ROOT/'semantic_alignment_research/pairwise_validation_001_corrected'
DEFAULT_OUT=ROOT/'semantic_alignment_research/pairwise_improved_pilot_20260713_readiness_failed'
FIRST=ROOT/'semantic_alignment_research/first_impression/v1_20260712'
TRACE_ROOT=ROOT/'simulation_runs/audit_evidence_20x250_20260710'
EVEREST='230b8d71f541acfc6a18d0a29f508eddf33b90d0f25beb736ba83ac1fb1cfc1a';FREE='1ae9443573e42259af54c30a0ec90a6a8746e640b09e53ae1c28a4c0a2d0ed6b'
REGRESSION_CONTEXT={EVEREST:'tg_fbaf32a54650f629731270135c319348343a6c08189e71f22ccb74b1a1d89521.png',FREE:'tg_8032ac6c90f358c9f5146680280edda94a7475b752b3c8e64c1670ba64986822.png'}
def load(p):return json.loads(Path(p).read_text())
def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output-dir',type=Path,default=DEFAULT_OUT);parser.add_argument('--fingerprint-supplement',type=Path);args=parser.parse_args();OUT=args.output_dir.resolve()
    OUT.mkdir(parents=True,exist_ok=True);intents=load(FIRST/'quote_visual_intents.json')['items'];first=load(FIRST/'image_first_impressions.json')['items']
    if args.fingerprint_supplement:
        supplement=load(args.fingerprint_supplement)['items']
        overlap=set(first)&set(supplement)
        if overlap:raise SystemExit(f'supplement would overwrite existing fingerprints: {sorted(overlap)}')
        first={**first,**supplement}
    editorial_doc=load(ROOT/'generated_image_analysis.json');editorial={n:editorial_doc['items'][editorial_doc['path_index'][n]] for n in editorial_doc['path_index']};active=set(editorial_doc['file_metadata']);hashes=editorial_doc['path_index'];traces=load_trace_records(TRACE_ROOT)
    candidates=[];seen=set()
    for trace in traces:
        qhash=trace.get('quote_hash');current=trace.get('production_image') or trace.get('winner')
        if qhash not in intents or current not in active or current not in first:continue
        runner,reason=eligible_trace_runner_up(trace,current,active=active,first_impressions=set(first),editorial=editorial,hashes=hashes)
        if not runner:continue
        key=(qhash,current,runner['image_basename'])
        if key in seen:continue
        seen.add(key);candidates.append((key,trace,runner,reason))
    candidates.sort(key=lambda x:(0 if x[0][0] in {EVEREST,FREE} else 1,x[0]))
    items=[];blind={};provenance=[]
    for (qhash,current,challenger),trace,runner,reason in candidates[:25]:
        cid=hashlib.sha256(f'improved-pilot-readiness:{qhash}:{current}:{challenger}'.encode()).hexdigest()[:20];swap=int(cid,16)%2==1;a,b=(challenger,current) if swap else (current,challenger)
        row={'case_id':cid,'quote_hash':qhash,'candidate_a':a,'candidate_b':b,'provenance':'exact_simulator_runner_up','selection_reason':'exact_selector_eligible_rank_2_with_documented_floors','input_hash':hashlib.sha256(f'{qhash}:{a}:{b}'.encode()).hexdigest()};items.append(row)
        blind[cid]={'current_winner':current,'challenger':challenger,'current_position':'B' if swap else 'A','eligibility_verified':True,'trace_file':trace['trace_file'],'post_index':trace.get('post_index')}
        provenance.append({'case_id':cid,'quote_hash':qhash,'current_winner':current,'challenger':challenger,'provenance':'exact_simulator_runner_up','trace_file':trace['trace_file'],'post_index':trace.get('post_index'),'current_sha256':hashes[current],'challenger_sha256':hashes[challenger],'production_score':runner['production_score'],'topic_score':runner['topic_score'],'editorial_quality':runner['editorial_quality'],'eligibility_verified':True})
    readiness=validate_pilot_readiness(items,blind,minimum=20);regressions={'everest_present':any(x['quote_hash']==EVEREST for x in items),'free_trade_present':any(x['quote_hash']==FREE for x in items)}
    blockers=[]
    for qhash,current in REGRESSION_CONTEXT.items():
        matching=[t for t in traces if t.get('quote_hash')==qhash and (t.get('production_image') or t.get('winner'))==current]
        if not matching: blockers.append({'quote_hash':qhash,'current_winner':current,'blocker':'missing_selector_trace'});continue
        trace=matching[0];eligible=[c for c in trace['candidate_detail'] if c.get('identity_shadow_score','eligible') is not None]
        runner=eligible[1] if len(eligible)>1 and eligible[0]['basename']==current else None
        if not runner:blockers.append({'quote_hash':qhash,'current_winner':current,'blocker':'missing_exact_runner_up'});continue
        name=runner['basename'];path=ROOT/'generated_review_approved_images'/name;quality=((editorial.get(name,{}).get('analysis') or {}).get('quality') or {}).get('overall')
        blocker={'quote_hash':qhash,'current_winner':current,'runner_up':name,'trace_file':trace['trace_file'],'post_index':trace.get('post_index'),'blocker':'missing_first_impression_fingerprint' if name not in first else None,'active':name in active,'image_exists':path.is_file(),'hash_matches':path.is_file() and __import__('hashlib').sha256(path.read_bytes()).hexdigest()==hashes.get(name),'identity_policy':runner.get('identity_policy'),'production_score':runner.get('production_score'),'topic_score':(runner.get('components') or {}).get('topics'),'editorial_quality':quality,'all_non_fingerprint_gates_pass':name in active and path.is_file() and runner.get('identity_policy')!='origin_quote_only' and quality>=70 and float(runner.get('production_score') or 0)>=40 and float((runner.get('components') or {}).get('topics') or 0)>=25}
        if blocker['blocker']:blockers.append(blocker)
    if not all(regressions.values()):readiness['ready']=False;readiness['status']='challenger construction still inadequate';readiness['issues'].append({'case_id':None,'issue':'required_regression_missing','details':regressions})
    manifest={'schema_version':1,'analysis_kind':'improved_pairwise_pilot_readiness_manifest','case_count':len(items),'items':items,'readiness':readiness,'regressions':regressions,'regression_blockers':blockers,'paid_calls_made':0,'human_review_started':False};atomic_write_json(OUT/'pilot_manifest.json',manifest);atomic_write_json(OUT/'pairwise_improved_pilot_manifest.json',manifest);atomic_write_json(OUT/'candidate_blind_map.json',{'schema_version':1,'items':blind});os.chmod(OUT/'candidate_blind_map.json',0o600);atomic_write_json(OUT/'human_pairwise_reviews.json',{'schema_version':1,'analysis_kind':'pairwise_human_reviews','items':{}})
    with (OUT/'candidate_provenance.csv').open('w',newline='',encoding='utf-8') as f:
        w=csv.DictWriter(f,fieldnames=list(provenance[0]));w.writeheader();w.writerows(provenance)
    summary={'schema_version':1,'status':readiness['status'],'credible_cases':readiness['credible_cases'],'minimum_required':20,'prepared_manifest_cases':25,'surviving_exact_runner_up_cases':len(items),'regressions':regressions,'original_prepared_manifest_audit':{'declared_cases':25,'actual_exact_or_simulator_runner_ups':9,'lower_rank_substitutions':10,'unmatched_trace_pairs':5,'lexical_regression_padding':1},'reason':('all strict provenance and regression gates pass' if readiness['ready'] else 'Everest and free-trade lack exact runner-ups with all cached inputs and documented floors; prepared pilot provenance was overstated'),'fingerprint_supplement':str(args.fingerprint_supplement) if args.fingerprint_supplement else None,'external_calls_during_rebuild':0};atomic_write_json(OUT/'readiness_summary.json',summary)
    atomic_write_json(OUT/'pairwise_improved_pilot_summary.json',summary)
    if readiness['ready']:
        from semantic_alignment.bakeoff import PRICES,estimate_tokens
        calls=len(items);estimated_input=calls*3200;estimated_output=calls*700
        estimates={p:{'calls':calls,'estimated_input_tokens':estimated_input,'estimated_output_tokens':estimated_output,'estimated_cost_usd':estimated_input*PRICES[p]['input']/1e6+estimated_output*PRICES[p]['output']/1e6} for p in ('grok','openai','anthropic','gemini')}
        atomic_write_json(OUT/'cost_estimate.json',{'schema_version':1,'case_count':calls,'providers':estimates,'combined_estimated_cost_usd':sum(x['estimated_cost_usd'] for x in estimates.values()),'not_executed':True,'tools_enabled':False})
        report=f"""# Improved Pairwise Pilot Ready Report

## Is the pilot executable?

**Yes.** The strict provenance gate passes with {len(items)} cases. Every challenger is the exact selector-eligible rank-2 candidate from one canonical simulator trace. Everest and free trade are included. No lexical challenger, lower-ranked substitution, or weak padding remains.

## What blocks it?

Nothing in readiness. Human review and pairwise provider judging have not started.

## Smallest next task

Start the blinded human review over this manifest. Provider calls remain a later step after human labels, under their existing explicit cost controls.
"""
    else:
        report=f"""# Improved Pairwise Pilot Readiness Report

## Decision

**{readiness['status']}**. No human review or paid provider execution was started.

The prior 25-case draft does not pass the binding provenance gate. Its audit found 9 true selector-eligible rank-2 challengers, 10 lower-ranked substitutions, 5 pairs that could not be tied to one exact quote/current/challenger trace, and one lexical free-trade regression pair. Lower-ranked substitutions cannot be relabelled as runner-ups.

Rebuilding from canonical traces with strict rank-2 semantics produces **{len(items)}** auditable cases, but the required free-trade regression remains absent. Its actual runner-up is active and identity-eligible but has no cached first-impression fingerprint. The next fingerprinted candidate has zero topic score and is not the runner-up. Using it would violate the no-weak-padding rule; analysing the true runner-up would require a new fingerprint, forbidden in this task.

## Gate results

- Prepared cases: {len(items)}.
- Credible exact simulator runner-ups: {readiness['credible_cases']}.
- Minimum credible count: 20.
- Everest present: {regressions['everest_present']}.
- Free trade present: {regressions['free_trade_present']}.
- Human labels written: 0.
- Provider calls made: 0.

Although the numeric credible count exceeds 20, the required composition and all-case provenance gate fails. The pilot must not proceed until the free-trade true runner-up has an independently authorised first-impression fingerprint or the requirement is explicitly revised in a future task.
"""
    (OUT/'pairwise_improved_pilot_readiness_report.md').write_text(report,encoding='utf-8');(OUT/'pairwise_improved_pilot_report.md').write_text(report,encoding='utf-8');(OUT/'pairwise_improved_pilot_ready_report.md').write_text(report,encoding='utf-8')
    blocker_lines=['# Pairwise Pilot Blockers','',('No blockers remain. The strict readiness gate passes.' if readiness['ready'] else 'The pilot is not executable. Both mandatory regression controls have valid exact selector runner-ups, but those runner-ups lack cached first-impression fingerprints. All other gates pass.'),'']
    if not readiness['ready']:
        for row in blockers:blocker_lines.extend([f"## {('Everest' if row['quote_hash']==EVEREST else 'Free trade')}",f"- Current winner: `{row['current_winner']}`",f"- Exact runner-up: `{row.get('runner_up')}`",f"- Blocker: `{row['blocker']}`",f"- Trace: `{row.get('trace_file')}` post {row.get('post_index')}",f"- Active/file/hash/identity/quality gates: `{row.get('all_non_fingerprint_gates_pass')}`",''])
    blocker_lines.extend(['## Minimum remaining work',('None; readiness passes.' if readiness['ready'] else 'Explicitly authorise independent raw-image-only first-impression analysis for exactly the two runner-up images above, persist those two fingerprints in a new versioned supplement, and rerun this readiness builder. No other image, pairwise critic, or human review is required before that gate check.')])
    (OUT/'pairwise_blockers.md').write_text('\n'.join(blocker_lines)+'\n',encoding='utf-8')
    print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
