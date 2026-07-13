#!/usr/bin/env python3
from __future__ import annotations
import json,os,time
from pathlib import Path
from google.genai import errors
from semantic_alignment.io import atomic_write_json
from semantic_alignment.pairwise_validation import PAIRWISE_SCHEMA,validate_pairwise_judgement
from semantic_alignment.vertex_recovery import GeminiVertexClient,error_details,validate_vertex_environment
from run_improved_pairwise_pilot import rendered_prompts

ROOT=Path(__file__).resolve().parent;RUN=ROOT/'semantic_alignment_research/pairwise_improved_pilot_20260713_run_v1'
def load(p):return json.loads(Path(p).read_text())
def main():
 original=load(RUN/'gemini_pilot_results.json');targets=sorted(original['failures'])
 if len(targets)>2:raise SystemExit('recovery is limited to the two confirmed failed Gemini cases')
 manifest=load(RUN/'pilot_manifest.json');prompts=rendered_prompts(manifest);env=validate_vertex_environment(os.environ);client=GeminiVertexClient(project=env['project'],location=env['location'],model='gemini-3.1-pro-preview',response_schema=PAIRWISE_SCHEMA,max_output_tokens=1200)
 rp=RUN/'gemini_vertex_recovery_results.json';lp=RUN/'gemini_vertex_recovery_ledger.json';results=load(rp) if rp.exists() else {'schema_version':1,'items':{},'failures':{}};ledger=load(lp) if lp.exists() else {'schema_version':1,'attempts':[],'calls':[],'known_cost_usd':0.0}
 for cid in targets:
  if cid in results['items']:continue
  attempts=[x for x in ledger['attempts'] if x['case_id']==cid]
  while len(attempts)<2:
   attempt={'case_id':cid,'attempt_number':len(attempts)+1,'transport':'vertex_ai_recovery','state':'sending','timestamp':time.time()};ledger['attempts'].append(attempt);atomic_write_json(lp,ledger)
   try:r=client.call(prompts[cid])
   except errors.APIError as exc:
    attempt.update(state='confirmed_failure',error=error_details(exc));atomic_write_json(lp,ledger);attempts=[x for x in ledger['attempts'] if x['case_id']==cid]
    if getattr(exc,'code',None) in {429,500,502,503,504} and len(attempts)<2:time.sleep(4);continue
    break
   value=validate_pairwise_judgement(r['content']);attempt['state']='completed';call={'case_id':cid,'attempt_number':attempt['attempt_number'],'request_id':r['request_id'],'cost_usd':r['cost_usd'],'latency_seconds':r['latency_seconds'],**r['usage']};ledger['calls'].append(call);ledger['known_cost_usd']+=r['cost_usd'];results['items'][cid]={'case_id':cid,**value,'provider':'gemini','model':client.model,'transport':'vertex_ai_recovery'};results['failures'].pop(cid,None);atomic_write_json(lp,ledger);atomic_write_json(rp,results);break
  if cid not in results['items']:results['failures'][cid]={'reason':'vertex_recovery_exhausted','attempts':len([x for x in ledger['attempts'] if x['case_id']==cid])};atomic_write_json(rp,results)
 logical={**original['items'],**results['items']};combined=load(RUN/'provider_results.json');combined['providers']['gemini']=logical;combined['failures']['gemini']={k:v for k,v in original['failures'].items() if k not in results['items']};atomic_write_json(RUN/'provider_results_recovered.json',combined)
 print(json.dumps({'targeted':len(targets),'recovered':len(results['items']),'still_missing':len(targets)-len(results['items']),'cost_usd':ledger['known_cost_usd']},indent=2))
if __name__=='__main__':main()
