#!/usr/bin/env python3
"""Analyse missing first impressions artefacts."""

from __future__ import annotations

import argparse,json,os
from pathlib import Path

from semantic_alignment.first_impression import (IMAGE_FIRST_OUTPUT_SCHEMA,IMAGE_PROMPT_VERSION,
    SCHEMA_VERSION,image_first_prompt,validate_image_first)
from semantic_alignment.io import atomic_write_json,sha256_file
from semantic_alignment.pipeline import CostLedger,XAIClient,cached_call,generated_image_inventory,utc_now

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'semantic_alignment_research/pairwise_improved_pilot_20260713_fingerprint_supplement'
ALLOWED={
 'tg_093fe18b5e8a685a1ec1a38813fa2dfddce5ca84853723c13d1dfc2a6ac4a429.png',
 'tg_d4013aa361485ab2894f71e426c705de78455e93d37fe3f4d2ae27727c70c88c.png'}

def main():
 """Run the command-line entry point."""
 p=argparse.ArgumentParser();p.add_argument('--execute-vision',action='store_true');p.add_argument('--confirm-cost-limit-usd',type=float);p.add_argument('--resume',action='store_true');a=p.parse_args()
 inventory={x['image_basename']:x for x in generated_image_inventory(ROOT)}
 if set(ALLOWED)-set(inventory):raise SystemExit('one or more whitelisted images are not active')
 estimate={'images':2,'model':'grok-4.5','prompt_version':IMAGE_PROMPT_VERSION,'expected_cost_usd':0.10,'conservative_ceiling_usd':0.50,'tools_enabled':False}
 OUT.mkdir(parents=True,exist_ok=True);atomic_write_json(OUT/'preflight.json',estimate);print(json.dumps(estimate,indent=2))
 if not a.execute_vision:return
 if a.confirm_cost_limit_usd is None or not 0<a.confirm_cost_limit_usd<=0.50:raise SystemExit('explicit cost limit <= $0.50 required')
 db_path=OUT/'image_first_impression_supplement.json';db=json.loads(db_path.read_text()) if db_path.exists() else {'schema_version':SCHEMA_VERSION,'analysis_kind':'image_first_impression_supplement','prompt_version':IMAGE_PROMPT_VERSION,'items':{},'failures':{}}
 ledger=CostLedger(OUT/'vision_cost_ledger.json',run_id=OUT.name);client=XAIClient(api_key=os.getenv('XAI_API_KEY',''))
 for name in sorted(ALLOWED):
  row=inventory[name]
  if name in db['items'] and db['items'][name].get('sha256')==row['sha256']:continue
  try:
   result=cached_call(stage='image',key=f'pairwise-readiness-first-impression:{name}',prompt=image_first_prompt(),schema=IMAGE_FIRST_OUTPUT_SCHEMA,client=client,ledger=ledger,response_dir=OUT/'responses',confirmed_stage_limit=a.confirm_cost_limit_usd,image_path=row['path'],expected_sha256=row['sha256'])
   if sha256_file(row['path'])!=row['sha256']:raise ValueError('image changed after analysis')
   value=validate_image_first(result.content);db['items'][name]={'schema_version':SCHEMA_VERSION,'analysis_kind':'image_first_impression','image_basename':name,'sha256':row['sha256'],**value,'model':result.model,'prompt_version':IMAGE_PROMPT_VERSION,'analysed_at':utc_now(),'provenance':{'independent_input':'raw_image_pixels_only','scores_are_model_judgements':True,'supplement_reason':'exact_selector_runner_up_readiness'}};db['failures'].pop(name,None)
  except Exception as exc:
   db['failures'][name]={'error':f'{type(exc).__name__}: {exc}','failed_at':utc_now()};atomic_write_json(db_path,db);raise
  atomic_write_json(db_path,db)
 print(json.dumps({'completed':len(db['items']),'failures':len(db['failures']),'output':str(db_path)},indent=2))
if __name__=='__main__':main()
