#!/usr/bin/env python3
from __future__ import annotations

import argparse,json,os,shutil
from concurrent.futures import ThreadPoolExecutor,as_completed
from pathlib import Path

from semantic_alignment.bakeoff import PRICES,estimate_tokens
from semantic_alignment.io import atomic_write_json
from semantic_alignment.pairwise_validation import pairwise_model_prompt
from run_pairwise_calibration import load,worker

ROOT=Path(__file__).resolve().parent
READY=ROOT/'semantic_alignment_research/pairwise_improved_pilot_20260713_ready_v1'
SUPPLEMENT=ROOT/'semantic_alignment_research/pairwise_improved_pilot_20260713_fingerprint_supplement/image_first_impression_supplement.json'
SOURCE=ROOT/'semantic_alignment_research/first_impression/v1_20260712'
FINGERPRINTS=ROOT/'semantic_alignment_research/runs/v2_20260711T111526Z'
DEFAULT_RUN=ROOT/'semantic_alignment_research/pairwise_improved_pilot_20260713_run_v1'
LIMITS={'grok':3.0,'openai':5.0,'anthropic':3.0,'gemini':3.0}

def prepare(run:Path):
 run.mkdir(parents=True,exist_ok=True)
 manifest=load(READY/'pairwise_improved_pilot_manifest.json');blind=load(READY/'candidate_blind_map.json')
 if not manifest['readiness']['ready'] or manifest['case_count']!=25:raise RuntimeError('pilot readiness gate failed')
 for name,value in (('pilot_manifest.json',manifest),('candidate_blind_map.json',blind)):atomic_write_json(run/name,value)
 if not (run/'human_pairwise_reviews.json').exists():atomic_write_json(run/'human_pairwise_reviews.json',{'schema_version':1,'analysis_kind':'pairwise_human_reviews','items':{}})
 return manifest

def rendered_prompts(manifest):
 intents=load(SOURCE/'quote_visual_intents.json')['items'];first=load(SOURCE/'image_first_impressions.json')['items'];supplement=load(SUPPLEMENT)['items'];
 if set(first)&set(supplement):raise RuntimeError('supplement overlaps original first-impression records')
 first={**first,**supplement};quotes=load(FINGERPRINTS/'quote_semantic_fingerprints.json')['items'];images=load(FINGERPRINTS/'image_implied_messages_generated.json')['items'];ed=load(ROOT/'generated_image_analysis.json');editorial={n:ed['items'][ed['path_index'][n]] for n in ed['path_index']};out={}
 for row in manifest['items']:
  candidates=[]
  for name in (row['candidate_a'],row['candidate_b']):candidates.append({'first_impression':first[name],'semantic':images[name],'editorial':editorial[name]})
  out[row['case_id']]=pairwise_model_prompt(intents[row['quote_hash']],quotes[row['quote_hash']],*candidates)
 return out

def preflight(run,manifest,prompts):
 tokens=sum(estimate_tokens(x) for x in prompts.values());providers={}
 for p,limit in LIMITS.items():
  expected=tokens*PRICES[p]['input']/1e6+25*700*PRICES[p]['output']/1e6;maximum=tokens*PRICES[p]['input']/1e6+25*1200*PRICES[p]['output']/1e6
  if maximum>limit:raise RuntimeError(f'{p} conservative estimate exceeds limit')
  providers[p]={'calls':25,'estimated_input_tokens':tokens,'estimated_output_tokens':17500,'expected_cost_usd':expected,'conservative_maximum_cost_usd':maximum,'limit_usd':limit,'tools_enabled':False}
 doc={'schema_version':1,'cases':25,'providers':providers,'combined_expected_cost_usd':sum(x['expected_cost_usd'] for x in providers.values()),'combined_conservative_maximum_cost_usd':sum(x['conservative_maximum_cost_usd'] for x in providers.values()),'combined_limit_usd':16.0,'prompt_parity':True,'human_labels_in_prompts':False,'winner_identity_in_prompts':False};atomic_write_json(run/'preflight.json',doc);return doc

def main():
 p=argparse.ArgumentParser();p.add_argument('--run-dir',type=Path,default=DEFAULT_RUN);p.add_argument('--dry-run',action='store_true');p.add_argument('--execute-grok',action='store_true');p.add_argument('--execute-openai',action='store_true');p.add_argument('--execute-claude',action='store_true');p.add_argument('--execute-gemini',action='store_true');p.add_argument('--enable-gemini-vertex-fallback',action='store_true');p.add_argument('--confirm-grok-limit-usd',type=float);p.add_argument('--confirm-openai-limit-usd',type=float);p.add_argument('--confirm-claude-limit-usd',type=float);p.add_argument('--confirm-gemini-developer-limit-usd',type=float);p.add_argument('--confirm-gemini-vertex-fallback-limit-usd',type=float);p.add_argument('--confirm-combined-limit-usd',type=float);p.add_argument('--resume',action='store_true');a=p.parse_args();run=a.run_dir.resolve();manifest=prepare(run);prompts=rendered_prompts(manifest);pf=preflight(run,manifest,prompts);print(json.dumps(pf,indent=2))
 if a.dry_run:return
 flags=[a.execute_grok,a.execute_openai,a.execute_claude,a.execute_gemini]
 confirmed={'grok':a.confirm_grok_limit_usd,'openai':a.confirm_openai_limit_usd,'anthropic':a.confirm_claude_limit_usd,'gemini':a.confirm_gemini_developer_limit_usd}
 if not all(flags) or not a.enable_gemini_vertex_fallback:raise SystemExit('all four explicit execution flags and Gemini fallback are required')
 if any(confirmed[p] is None or confirmed[p]!=LIMITS[p] for p in LIMITS) or a.confirm_gemini_vertex_fallback_limit_usd!=2 or a.confirm_combined_limit_usd!=16:raise SystemExit('confirmed limits must exactly match the pilot ceilings')
 results={}
 with ThreadPoolExecutor(max_workers=4,thread_name_prefix='improved-pairwise-pilot') as pool:
  futures={pool.submit(worker,pvd,run,prompts,True,a.enable_gemini_vertex_fallback,stem='pilot',ceiling=LIMITS[pvd]):pvd for pvd in LIMITS}
  for future in as_completed(futures):results[futures[future]]=future.result()
 atomic_write_json(run/'provider_results.json',{'schema_version':1,'providers':{p:r['items'] for p,r in results.items()},'failures':{p:r['failures'] for p,r in results.items()}})
 with (run/'provider_attempts.jsonl').open('w',encoding='utf-8') as f:
  for pvd in sorted(LIMITS):
   ledger=load(run/f'{pvd}_pilot_ledger.json')
   for row in ledger['attempts']:f.write(json.dumps({'provider':pvd,**row},sort_keys=True)+'\n')
 print(json.dumps({p:{'completed':len(r['items']),'failures':len(r['failures']),'cost_usd':sum(x.get('cost_usd',0) for x in r['calls'])} for p,r in results.items()},indent=2))
if __name__=='__main__':main()
