#!/usr/bin/env python3
"""Recover the final incomplete Gemini case through Vertex."""

from __future__ import annotations
import argparse,json,time
from pathlib import Path
from google.genai import errors
from semantic_alignment.bakeoff import PRICES,common_prompt,validate_bakeoff_result
from semantic_alignment.io import atomic_write_json,read_json
from semantic_alignment.vertex_recovery import GeminiVertexClient,VERTEX_MODEL,error_details,validate_vertex_environment

CASE_ID='378fbf96d18c2e10f5a3';PARENT=Path('semantic_alignment_research/provider_bakeoff_250_20260712_v1');SOURCE=Path('semantic_alignment_research/runs/v2_20260711T111526Z');ROOT=Path('semantic_alignment_research/provider_bakeoff_250_20260712_v1_vertex_recovery/vertex_final_case_retry_v1');LIMIT=.10

def main():
    """Run the command-line entry point."""
    p=argparse.ArgumentParser();p.add_argument('command',choices=('dry-run','execute'));p.add_argument('--execute',action='store_true');p.add_argument('--confirm-limit-usd',type=float);a=p.parse_args();env=validate_vertex_environment();case=next(x for x in json.load(open(PARENT/'cases.json'))['items'] if x['case_id']==CASE_ID);q=json.load(open(SOURCE/'quote_semantic_fingerprints.json'))['items'];i=json.load(open(SOURCE/'image_implied_messages_generated.json'))['items'];prompt=common_prompt(q[case['quote_hash']],i[case['image_basename']]);maximum=(case['estimated_input_tokens']*PRICES['gemini']['input']+1600*PRICES['gemini']['output'])/1e6
    ROOT.mkdir(parents=True,exist_ok=True);manifest={'schema_version':1,'record_kind':'operator_authorized_vertex_final_case_retry','parent_recovery':'provider_bakeoff_250_20260712_v1_vertex_recovery','case_id':CASE_ID,'model':VERTEX_MODEL,'input_hash':case['normalised_input_hash'],'maximum_attempts':1,'maximum_cost_usd':maximum,'ceiling_usd':LIMIT,'status':'prepared'};atomic_write_json(ROOT/'manifest.json',manifest)
    if a.command=='dry-run':print(json.dumps(manifest,indent=2));return
    if not a.execute or a.confirm_limit_usd!=LIMIT:raise SystemExit('exact execution flag and $0.10 limit required')
    if (ROOT/'result.json').exists():print('already complete');return
    ledger=read_json(ROOT/'ledger.json',{}) or {'attempts':[],'calls':[],'failures':[],'ambiguous':[]}
    if ledger['attempts']:raise SystemExit('the single supplemental attempt has already been consumed')
    attempt={'case_id':CASE_ID,'attempt_number':1,'model':VERTEX_MODEL,'input_hash':case['normalised_input_hash'],'timestamp':time.time(),'lifecycle_state':'prepared'};ledger['attempts'].append(attempt);atomic_write_json(ROOT/'ledger.json',ledger);attempt['lifecycle_state']='sending';atomic_write_json(ROOT/'ledger.json',ledger)
    client=GeminiVertexClient(project=env['project'],location=env['location'])
    try:response=client.call(prompt)
    except errors.APIError as exc:
        attempt['lifecycle_state']='confirmed_failure';ledger['failures'].append({'case_id':CASE_ID,**error_details(exc)});atomic_write_json(ROOT/'ledger.json',ledger);print(json.dumps(ledger['failures'][-1],indent=2));return
    except (TimeoutError,ConnectionError,OSError) as exc:
        attempt['lifecycle_state']='ambiguous_outcome';ledger['ambiguous'].append({'case_id':CASE_ID,**error_details(exc)});atomic_write_json(ROOT/'ledger.json',ledger);raise
    call={'case_id':CASE_ID,'request_id':response['request_id'],'latency_seconds':response['latency_seconds'],'cost_usd':response['cost_usd'],'model_version':response['model_version'],'traffic_type':response['traffic_type'],**response['usage']}
    if call['cost_usd']>LIMIT:raise RuntimeError('supplemental attempt exceeded cost ceiling')
    attempt['lifecycle_state']='response_received';ledger['calls'].append(call);atomic_write_json(ROOT/'ledger.json',ledger)
    try:value=validate_bakeoff_result(response['content'])
    except ValueError as exc:attempt['lifecycle_state']='confirmed_failure';ledger['failures'].append({'case_id':CASE_ID,'schema_error':str(exc),'charged_call':call});atomic_write_json(ROOT/'ledger.json',ledger);return
    attempt['lifecycle_state']='completed';item={'case_id':CASE_ID,'quote_hash':case['quote_hash'],'image_basename':case['image_basename'],**value,'model':VERTEX_MODEL,'recovered_via_vertex_ai':True,'recovery_provenance':{'kind':'operator_authorized_final_case_retry','parent_case_id':CASE_ID,'vertex_request_id':response['request_id'],'traffic_type':response['traffic_type']}};atomic_write_json(ROOT/'result.json',{'schema_version':1,'items':{CASE_ID:item}});atomic_write_json(ROOT/'ledger.json',ledger);print(json.dumps(call,indent=2))
if __name__=='__main__':main()
