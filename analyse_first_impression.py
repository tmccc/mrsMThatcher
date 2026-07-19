#!/usr/bin/env python3
"""Analyse first impression artefacts."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
from collections import Counter
from pathlib import Path

from semantic_alignment.bakeoff import PROVIDER_MODELS, ProviderClient
from semantic_alignment.first_impression import (
    ALIGNMENT_OUTPUT_SCHEMA, CRITIC_PROMPT_VERSION, EVEREST_IMAGE, EVEREST_QUOTE_HASH,
    IMAGE_FIRST_OUTPUT_SCHEMA, IMAGE_PROMPT_VERSION, PAIRWISE_OUTPUT_SCHEMA,
    SCHEMA_VERSION, AlignmentWorker, StructuredProviderAdapter, build_validation_cases,
    critic_prompt, derive_quote_intent, image_first_prompt, pairwise_cases, pairwise_prompt,
    pending_images, preflight, run_alignment_workers, strategy_comparison, validate_alignment,
    validate_image_first, validate_pairwise,
)
from semantic_alignment.gemini_fallback import GeminiFallbackWorker, verify_adc_access
from semantic_alignment.io import atomic_write_json, atomic_write_text, read_json, sha256_file
from semantic_alignment.pipeline import CostLedger, XAIClient, cached_call, generated_image_inventory, utc_now
from semantic_alignment.vertex_recovery import GeminiVertexClient

PROJECT = Path(__file__).resolve().parent
SOURCE = PROJECT / "semantic_alignment_research/runs/v2_20260711T111526Z"
LARGE = PROJECT / "semantic_alignment_research/provider_bakeoff_250_20260712_v1"
RECOVERED = PROJECT / "semantic_alignment_research/provider_bakeoff_250_20260712_v1_vertex_recovery/recovered_view"
DEFAULT_RUN = PROJECT / "semantic_alignment_research/first_impression/v1_20260712"
CEILINGS = {"grok": 3.0, "openai": 5.0, "anthropic": 3.0, "gemini": 3.0}


def parser():
    """Build the command-line argument parser."""
    p=argparse.ArgumentParser(description="Offline first-impression and tone-alignment research")
    p.add_argument("--project-dir",type=Path,default=PROJECT);p.add_argument("--run-dir",type=Path,default=DEFAULT_RUN)
    p.add_argument("--max-items",type=int);p.add_argument("--only-image");p.add_argument("--only-quote-hash")
    p.add_argument("--resume",action="store_true");p.add_argument("--validation-only",action="store_true")
    p.add_argument("--no-write",action="store_true")
    sub=p.add_subparsers(dest="command",required=True)
    sub.add_parser("prepare");sub.add_parser("dry-run")
    vision=sub.add_parser("vision");vision.add_argument("--execute-vision",action="store_true");vision.add_argument("--confirm-cost-limit-usd",type=float)
    critics=sub.add_parser("critics");critics.add_argument("--providers",default="grok,openai,anthropic,gemini");critics.add_argument("--execute-critics",action="store_true");critics.add_argument("--enable-gemini-vertex-fallback",action="store_true");critics.add_argument("--confirm-grok-cost-limit-usd",type=float);critics.add_argument("--confirm-openai-cost-limit-usd",type=float);critics.add_argument("--confirm-claude-cost-limit-usd",type=float);critics.add_argument("--confirm-gemini-cost-limit-usd",type=float);critics.add_argument("--confirm-gemini-vertex-fallback-cost-limit-usd",type=float);critics.add_argument("--confirm-combined-cost-limit-usd",type=float)
    pair=sub.add_parser("pairwise");pair.add_argument("--pairwise-only",action="store_true");pair.add_argument("--execute-critics",action="store_true");pair.add_argument("--provider",choices=("grok","openai","anthropic","gemini"),default="grok");pair.add_argument("--confirm-cost-limit-usd",type=float)
    sub.add_parser("report")
    return p


def inputs(project):
    """Return the inputs."""
    quotes=read_json(SOURCE/'quote_semantic_fingerprints.json')['items'];image_semantics=read_json(SOURCE/'image_implied_messages_generated.json')['items'];legacy=read_json(project/'quote_analysis.json',{}).get('items',{})
    cases=read_json(LARGE/'cases.json')['items'];human=read_json(RECOVERED/'human_validation.json',{}).get('items',{});dis=read_json(RECOVERED/'disagreement_results.json',{}).get('items',[])
    active_rows=generated_image_inventory(project);active={row['image_basename']:row for row in active_rows}
    return quotes,image_semantics,legacy,cases,human,dis,active


def prepare(run,project,no_write=False):
    """Prepare and preflight a first-impression analysis run."""
    quotes,image_semantics,legacy,cases,human,dis,active=inputs(project)
    existing=read_json(run/'validation_cases.json')
    validation=existing or build_validation_cases(cases,human,dis,set(active),limit=50)
    intents={q:derive_quote_intent(quotes[q],legacy.get(q)) for q in sorted({x['quote_hash'] for x in validation['items']})}
    if not no_write:
        run.mkdir(parents=True,exist_ok=True);atomic_write_json(run/'validation_cases.json',validation);atomic_write_json(run/'quote_visual_intents.json',{"schema_version":1,"analysis_kind":"quote_visual_intents","items":intents})
    return quotes,image_semantics,human,active,validation,intents


def image_db(run):
    """Load the image metadata database."""
    return read_json(run/'image_first_impressions.json') or {"schema_version":1,"analysis_kind":"image_first_impression_database","prompt_version":IMAGE_PROMPT_VERSION,"items":{},"failures":{}}


def dry(run,project,no_write=False):
    """Return the dry."""
    _,_,_,active,validation,intents=prepare(run,project,no_write)
    db=image_db(run);pending=pending_images(validation['items'],active,db);pf=preflight(validation['items'],intents,db['items'],vision_calls=len(pending));pf.update({"active_pool":len(active),"quarantined_included":0,"everest_present":any(x['quote_hash']==EVEREST_QUOTE_HASH and x['image_basename']==EVEREST_IMAGE for x in validation['items']),"free_trade_present":any(x['inclusion_reason']=='forced_free_trade_indirect_case' for x in validation['items']),"pending_images":len(pending),"pairwise_cases":len(pairwise_cases(validation['items']))})
    if not no_write:atomic_write_json(run/'preflight.json',pf)
    return pf


def run_vision(args,run,project):
    """Run vision."""
    if not args.execute_vision:raise RuntimeError('vision execution requires --execute-vision')
    if args.confirm_cost_limit_usd is None or not 0<args.confirm_cost_limit_usd<=3:raise RuntimeError('vision cost limit must be within $3')
    _,_,_,active,validation,_=prepare(run,project);db=image_db(run);rows=pending_images(validation['items'],active,db)
    if args.only_image:rows=[x for x in rows if x['image_basename']==args.only_image]
    rows=rows[:args.max_items]
    client=XAIClient(api_key=os.getenv('XAI_API_KEY',''));ledger=CostLedger(run/'vision_cost_ledger.json',run_id=run.name)
    for row in rows:
        key=row['image_basename']
        try:
            result=cached_call(stage='image',key=f'first-impression:{key}',prompt=image_first_prompt(),schema=IMAGE_FIRST_OUTPUT_SCHEMA,client=client,ledger=ledger,response_dir=run/'responses',confirmed_stage_limit=args.confirm_cost_limit_usd,image_path=row['path'],expected_sha256=row['sha256'])
            if sha256_file(row['path'])!=row['sha256']:raise ValueError('image changed after analysis')
            value=validate_image_first(result.content);db['items'][key]={"schema_version":1,"analysis_kind":"image_first_impression","image_basename":key,"sha256":row['sha256'],**value,"model":result.model,"prompt_version":IMAGE_PROMPT_VERSION,"analysed_at":utc_now(),"provenance":{"independent_input":"raw_image_pixels_only","scores_are_model_judgements":True}};db['failures'].pop(key,None)
        except Exception as exc:
            db['failures'][key]={"error":str(exc),"failed_at":utc_now()};atomic_write_json(run/'image_first_impressions.json',db);raise
        atomic_write_json(run/'image_first_impressions.json',db)
    return len(rows)


def run_critics(args,run,project):
    """Run critics."""
    if not args.execute_critics:raise RuntimeError('critic execution requires --execute-critics')
    limits={"grok":args.confirm_grok_cost_limit_usd,"openai":args.confirm_openai_cost_limit_usd,"anthropic":args.confirm_claude_cost_limit_usd,"gemini":args.confirm_gemini_cost_limit_usd}
    selected=tuple(dict.fromkeys(x.strip() for x in args.providers.split(',') if x.strip()))
    if not selected or any(p not in limits for p in selected):raise RuntimeError('invalid critic providers')
    if any(limits[p] is None or not 0<limits[p]<=CEILINGS[p] for p in selected):raise RuntimeError('provider limits must be explicitly confirmed within ceilings')
    if args.confirm_combined_cost_limit_usd is None or not 0<args.confirm_combined_cost_limit_usd<=14:raise RuntimeError('combined limit must be within $14')
    _,_,_,_,validation,intents=prepare(run,project);images=image_db(run)['items'];cases=[x for x in validation['items'] if x['image_basename'] in images]
    if args.only_quote_hash:cases=[x for x in cases if x['quote_hash']==args.only_quote_hash]
    cases=[{**case,"normalised_input_hash":hashlib.sha256(critic_prompt(intents[case['quote_hash']],images[case['image_basename']]).encode()).hexdigest(),"estimated_input_tokens":2500} for case in cases[:args.max_items]]
    keys={"grok":"XAI_API_KEY","openai":"OPENAI_API_KEY","anthropic":"ANTHROPIC_API_KEY","gemini":"GEMINI_API_KEY"};clients={p:StructuredProviderAdapter(ProviderClient(p,os.getenv(keys[p]) or (os.getenv('GOOGLE_API_KEY','') if p=='gemini' else '')),ALIGNMENT_OUTPUT_SCHEMA,'first_impression_alignment',1200) for p in selected}
    workers=[AlignmentWorker(p,cases,intents,images,run,clients[p],limits[p],sleep=lambda _:None) for p in selected if p!='gemini']
    if 'gemini' in selected and args.enable_gemini_vertex_fallback:
        env=verify_adc_access(dict(os.environ));vertex=GeminiVertexClient(project=env['project'],location=env['location'],model=clients['gemini'].model,response_schema=ALIGNMENT_OUTPUT_SCHEMA,max_output_tokens=1200)
        workers.append(GeminiFallbackWorker(cases=cases,quotes=intents,images=images,run_dir=run,developer_client=clients['gemini'],vertex_client=vertex,developer_limit=limits['gemini'],vertex_limit=args.confirm_gemini_vertex_fallback_cost_limit_usd,combined_limit=args.confirm_combined_cost_limit_usd,fallback_enabled=True,fallback_available=True,prompt_builder=critic_prompt,result_validator=validate_alignment,prompt_version=CRITIC_PROMPT_VERSION,schema_version=SCHEMA_VERSION))
    elif 'gemini' in selected:workers.append(AlignmentWorker('gemini',cases,intents,images,run,clients['gemini'],limits['gemini'],sleep=lambda _:None))
    summary=run_alignment_workers(workers);atomic_write_json(run/'critic_summary.json',summary);return summary


def run_pairwise(args,run,project):
    """Run pairwise."""
    if not args.pairwise_only or not args.execute_critics:raise RuntimeError('pairwise execution requires --pairwise-only --execute-critics')
    if args.confirm_cost_limit_usd is None or not 0<args.confirm_cost_limit_usd<=3:raise RuntimeError('pairwise limit must be within $3')
    _,_,_,_,validation,intents=prepare(run,project);images=image_db(run)['items'];pairs=pairwise_cases(validation['items'])[:args.max_items];out=read_json(run/'pairwise_rankings.json') or {"schema_version":1,"analysis_kind":"first_impression_pairwise_rankings","provider":args.provider,"items":{},"failures":{}}
    key={'grok':'XAI_API_KEY','openai':'OPENAI_API_KEY','anthropic':'ANTHROPIC_API_KEY','gemini':'GEMINI_API_KEY'}[args.provider];client=StructuredProviderAdapter(ProviderClient(args.provider,os.getenv(key) or (os.getenv('GOOGLE_API_KEY','') if args.provider=='gemini' else '')),PAIRWISE_OUTPUT_SCHEMA,'first_impression_pairwise',1000);spent=0.0
    for pair in pairs:
        if pair['pair_id'] in out['items'] or pair['candidate_a'] not in images or pair['candidate_b'] not in images:continue
        prompt=pairwise_prompt(intents[pair['quote_hash']],images[pair['candidate_a']],images[pair['candidate_b']])
        response=client.call(prompt);spent+=response['cost_usd']
        if spent>args.confirm_cost_limit_usd:raise RuntimeError('pairwise cost ceiling reached')
        out['items'][pair['pair_id']]={"schema_version":1,"analysis_kind":"first_impression_pairwise_ranking",**pair,**validate_pairwise(response['content']),"model":client.model,"prompt_version":"first-impression-pairwise-v1","cost_usd":response['cost_usd']};atomic_write_json(run/'pairwise_rankings.json',out)
    return len(out['items'])


def report(run,project):
    """Write the first-impression analysis report."""
    _,_,human,_,validation,intents=prepare(run,project);images=image_db(run)['items'];providers={p:(read_json(run/f'{p}_first_impression_results.json') or read_json(run/f'{p}_results.json') or {}).get('items',{}) for p in ('grok','openai','anthropic','gemini')};all_results={cid:[rows[cid] for rows in providers.values() if cid in rows] for cid in {x['case_id'] for x in validation['items']}}
    consensus={cid:{"provider_count":len(rows),"alignment_median":statistics.median([r['dominant_visual_message_alignment_score'] for r in rows]) if rows else None,"tone_median":statistics.median([r['tone_alignment_score'] for r in rows]) if rows else None,"risk":dict(Counter(r['editorial_risk'] for r in rows))} for cid,rows in all_results.items()}
    alignments={cid:rows[0] for cid,rows in all_results.items() if rows};strategies=strategy_comparison(validation['items'],alignments,human);atomic_write_json(run/'strategy_comparison.json',strategies);atomic_write_json(run/'first_impression_alignment_results.json',{"schema_version":1,"providers":providers,"consensus":consensus})
    everest=next(x for x in validation['items'] if x['quote_hash']==EVEREST_QUOTE_HASH and x['image_basename']==EVEREST_IMAGE);free=next(x for x in validation['items'] if x['inclusion_reason']=='forced_free_trade_indirect_case');pairwise=read_json(run/'pairwise_rankings.json',{"items":{}})
    def ledger_cost(name):
        data=read_json(run/name,{}) or {};return len(data.get('calls',[])),sum(float(x.get('cost_usd') or 0) for x in data.get('calls',[]))
    costs={'vision':ledger_cost('vision_cost_ledger.json'),'grok':ledger_cost('grok_first_impression_ledger.json'),'openai':ledger_cost('openai_first_impression_ledger.json'),'anthropic':ledger_cost('anthropic_first_impression_ledger.json'),'gemini_developer':ledger_cost('gemini_ledger.json'),'gemini_vertex':ledger_cost('gemini_vertex_fallback_ledger.json'),'pairwise':(len(pairwise.get('items',{})),sum(float(x.get('cost_usd') or 0) for x in pairwise.get('items',{}).values()))}
    total_cost=sum(x[1] for x in costs.values());summaries={p:{'completed':len(rows),'alignment_mean':round(statistics.mean(r['dominant_visual_message_alignment_score'] for r in rows.values()),2) if rows else None,'tone_mean':round(statistics.mean(r['tone_alignment_score'] for r in rows.values()),2) if rows else None,'high_risk':sum(r['editorial_risk']=='high' for r in rows.values())} for p,rows in providers.items()}
    everest_rows={p:rows.get(everest['case_id']) for p,rows in providers.items() if everest['case_id'] in rows};free_rows={p:rows.get(free['case_id']) for p,rows in providers.items() if free['case_id'] in rows}
    correction={"record_kind":"first_impression_gemini_provenance_correction","raw_records_immutable":True,"affected_results":len(providers['gemini']),"recorded_prompt_version":"provider-neutral-picture-editor-v1","actual_prompt_version":CRITIC_PROMPT_VERSION,"actual_schema_version":SCHEMA_VERSION,"basis":"input hashes were generated from first_impression.critic_prompt and the first-impression schema; generic fallback provenance defaults were corrected in code after execution"};atomic_write_json(run/'gemini_provenance_correction.json',correction)
    pair_counts=Counter(x['preferred_candidate'] for x in pairwise.get('items',{}).values())
    lines=['# First-Impression Alignment Validation Report','',f'- Validation cases: {len(validation["items"])}',f'- Independent image analyses: {len(images)}',f'- Provider results: { {p:len(v) for p,v in providers.items()} }',f'- Pairwise rankings: {len(pairwise.get("items",{}))}',f'- Total known spend: ${total_cost:.6f}','','## Architecture and isolation','','Image vision analysis was pixels-only. Quote intent was deterministically projected from existing quote analyses. Text critics received only the two new fingerprints. Pairwise judges received anonymous A/B fingerprints and cached semantic summaries only. No tools, search, winner labels or human labels were supplied. Scores are model judgements, not eye tracking.','','## Provider models, results and costs','','| Provider/transport | Model | Completed/calls | Known cost | Alignment mean | Tone mean | High risk |','|---|---|---:|---:|---:|---:|---:|']
    for p in ('grok','openai','anthropic'):
        lines.append(f'| {p} | {PROVIDER_MODELS[p]} | {summaries[p]["completed"]} | ${costs[p][1]:.6f} | {summaries[p]["alignment_mean"]} | {summaries[p]["tone_mean"]} | {summaries[p]["high_risk"]} |')
    lines += [f'| gemini Developer | {PROVIDER_MODELS["gemini"]} | {costs["gemini_developer"][0]} calls | ${costs["gemini_developer"][1]:.6f} |  |  |  |',f'| gemini Vertex | {PROVIDER_MODELS["gemini"]} | {costs["gemini_vertex"][0]} calls | ${costs["gemini_vertex"][1]:.6f} |  |  |  |',f'| gemini logical | {PROVIDER_MODELS["gemini"]} | {summaries["gemini"]["completed"]}/50 | ${costs["gemini_developer"][1]+costs["gemini_vertex"][1]:.6f} | {summaries["gemini"]["alignment_mean"]} | {summaries["gemini"]["tone_mean"]} | {summaries["gemini"]["high_risk"]} |',f'| vision | grok-4.5 | {costs["vision"][0]} | ${costs["vision"][1]:.6f} |  |  |  |',f'| pairwise | grok-4.5 | {costs["pairwise"][0]} | ${costs["pairwise"][1]:.6f} |  |  |  |','','Gemini stopped with one explicitly ambiguous Vertex request after 38 valid results; 12 cases were not attempted and denominators remain explicit. Raw records retain their original lifecycle; `gemini_provenance_correction.json` documents the generic-version metadata defect without rewriting them.','','## Everest case study','',f'- Quote desired first impression: {intents[EVEREST_QUOTE_HASH]["desired_first_impression"]}',f'- Desired tone: {intents[EVEREST_QUOTE_HASH]["desired_tone"]}',f'- Image actual first impression: {images.get(EVEREST_IMAGE,{}).get("first_impression_message","pending")}',f'- First object: {images.get(EVEREST_IMAGE,{}).get("first_object_noticed","pending")}',f'- Dominant symbol: {(images.get(EVEREST_IMAGE,{}).get("dominant_symbols") or ["pending"])[0]}',f'- Tone: {images.get(EVEREST_IMAGE,{}).get("primary_tone")} with warning={images.get(EVEREST_IMAGE,{}).get("warning_tone")} and inspirational={images.get(EVEREST_IMAGE,{}).get("inspirational_tone")}',f'- Visual competition: {images.get(EVEREST_IMAGE,{}).get("visual_competition_score")}',f'- Provider alignment scores: { {p:r["dominant_visual_message_alignment_score"] for p,r in everest_rows.items()} }',f'- Provider first-second fit: { {p:r["first_second_fit"] for p,r in everest_rows.items()} }','- Editorial conclusion: technically strong and semantically Thatcher-related, but the communist-warning message dominates and is wrong for achievement culminating in patriotic service.','- Preferred direction: lone climber, summit, national flag, sunrise, individual achievement becoming national representation.','', '## Free-trade case study','',f'- Case: {free["case_id"]}',f'- First impression: {images.get(free["image_basename"],{}).get("first_impression_message","pending")}',f'- Provider alignment scores: { {p:r["dominant_visual_message_alignment_score"] for p,r in free_rows.items()} }','', '## Pairwise ranking','',f'- Decisions: {dict(pair_counts)}',f'- Known cost: ${costs["pairwise"][1]:.6f}','- Five comparisons are too few for a ranking policy. The current pair manifests use anonymous deterministic A/B ordering; `would_replace_current_winner` is not treated as evidence because no winner identity was shown.','', '## Strategies A-E','', '```json',json.dumps(strategies,indent=2),'```','','These are observational validation-set calculations only. Generated share, candidate exhaustion and diversity cannot be reconstructed from these cases.','','## Human review','','Run: `python3 tools/first_impression_review.py --project-dir /disks/disk1/etc/mrsMThatcher --run-dir /disks/disk1/etc/mrsMThatcher/semantic_alignment_research/first_impression/v1_20260712 --host 127.0.0.1 --port 8771`','','## Limitations and recommendation','','The validation set is small and partly selected from previously reviewed difficult cases. Claude produced at least one internally inverted salience-interference score on Everest, so that field needs calibration before scaling. Gemini is incomplete. Human first-impression review is not yet complete. Full-corpus analysis is not justified until human review confirms that the new layer improves bad-case detection without rejecting strong indirect images.']
    atomic_write_text(run/'first_impression_report.md','\n'.join(lines)+'\n');return {"cases":len(validation['items']),"images":len(images),"provider_results":{p:len(v) for p,v in providers.items()},"pairwise":len(pairwise.get('items',{}))}


def main(argv=None):
    """Run the command-line entry point."""
    args=parser().parse_args(argv);project=args.project_dir.resolve();run=args.run_dir.resolve()
    if args.command=='prepare':prepare(run,project,args.no_write);print('validation_cases=50');return 0
    if args.command=='dry-run':print(json.dumps(dry(run,project,args.no_write),indent=2));return 0
    if args.command=='vision':print(f'completed={run_vision(args,run,project)}');return 0
    if args.command=='critics':print(json.dumps(run_critics(args,run,project),indent=2));return 0
    if args.command=='pairwise':print(f'pairwise={run_pairwise(args,run,project)}');return 0
    if args.command=='report':print(json.dumps(report(run,project),indent=2));return 0
    return 2

if __name__=='__main__':raise SystemExit(main())
