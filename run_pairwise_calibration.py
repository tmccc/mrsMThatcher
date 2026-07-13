#!/usr/bin/env python3
from __future__ import annotations

import argparse, hashlib, json, os, statistics, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

from semantic_alignment.bakeoff import PRICES, ProviderClient, estimate_tokens
from semantic_alignment.first_impression import StructuredProviderAdapter
from semantic_alignment.io import atomic_write_json
from semantic_alignment.pairwise_validation import (PAIRWISE_PROMPT_VERSION, PAIRWISE_SCHEMA,
    calibration_metrics, pairwise_model_prompt, provider_selection, validate_pairwise_judgement)
from semantic_alignment.vertex_recovery import GeminiVertexClient, validate_vertex_environment

ROOT=Path(__file__).resolve().parent
DEFAULT_RUN=ROOT/'semantic_alignment_research/pairwise_validation_001'
SOURCE=ROOT/'semantic_alignment_research/first_impression/v1_20260712'
FINGERPRINT_RUN=ROOT/'semantic_alignment_research/runs/v2_20260711T111526Z'
CEILINGS={'grok':2.0,'openai':3.0,'anthropic':2.0,'gemini':2.0}
KEYS={'grok':'XAI_API_KEY','openai':'OPENAI_API_KEY','anthropic':'ANTHROPIC_API_KEY','gemini':'GEMINI_API_KEY'}

def load(path): return json.loads(Path(path).read_text())

def sources():
    intents=load(SOURCE/'quote_visual_intents.json')['items']; first=load(SOURCE/'image_first_impressions.json')['items']
    quotes=load(FINGERPRINT_RUN/'quote_semantic_fingerprints.json')['items']; images=load(FINGERPRINT_RUN/'image_implied_messages_generated.json')['items']
    editorial_doc=load(ROOT/'generated_image_analysis.json'); editorial={n:editorial_doc['items'][editorial_doc['path_index'][n]] for n in editorial_doc['path_index']}
    return intents,first,quotes,images,editorial

def prompts(run, case_ids=None):
    manifest={x['case_id']:x for x in load(run/'pairwise_manifest.json')['items']}; case_ids=case_ids or load(run/'calibration_cases.json')['case_ids']; intents,first,quotes,images,editorial=sources(); out={}
    for cid in case_ids:
        row=manifest[cid]
        candidates=[]
        for name in (row['candidate_a'],row['candidate_b']): candidates.append({'first_impression':first[name],'semantic':images[name],'editorial':editorial[name]})
        out[cid]=pairwise_model_prompt(intents[row['quote_hash']],quotes[row['quote_hash']],*candidates)
    return out

def preflight(run):
    rendered=prompts(run); total=sum(estimate_tokens(x) for x in rendered.values()); providers={}
    for provider,ceiling in CEILINGS.items():
        expected=total*PRICES[provider]['input']/1e6+15*700*PRICES[provider]['output']/1e6
        maximum=total*PRICES[provider]['input']/1e6+15*1200*PRICES[provider]['output']/1e6
        providers[provider]={'calls':15,'estimated_input_tokens':total,'estimated_output_tokens':10500,'expected_cost_usd':expected,'conservative_maximum_cost_usd':maximum,'ceiling_usd':ceiling,'tools_enabled':False}
        if maximum>ceiling: raise RuntimeError(f'{provider} estimate exceeds ceiling')
    doc={'schema_version':1,'prompt_version':PAIRWISE_PROMPT_VERSION,'case_count':15,'providers':providers,
         'combined_expected_cost_usd':sum(x['expected_cost_usd'] for x in providers.values()),'combined_ceiling_usd':9.0}
    atomic_write_json(run/'calibration_preflight.json',doc);return doc

def worker(provider,run,rendered,execute,vertex_fallback,*,stem='pairwise_calibration',ceiling=None):
    ceiling=CEILINGS[provider] if ceiling is None else ceiling
    result_path=run/f'{provider}_{stem}_results.json'; ledger_path=run/f'{provider}_{stem}_ledger.json'
    result=load(result_path) if result_path.exists() else {'schema_version':1,'provider':provider,'prompt_version':PAIRWISE_PROMPT_VERSION,'items':{},'failures':{},'calls':[]}
    ledger=load(ledger_path) if ledger_path.exists() else {'schema_version':1,'provider':provider,'attempts':[],'known_cost_usd':0.0}
    if not execute:return result
    key=os.getenv(KEYS[provider]) or (os.getenv('GOOGLE_API_KEY') if provider=='gemini' else None)
    client=StructuredProviderAdapter(ProviderClient(provider,key),PAIRWISE_SCHEMA,'pairwise_editorial_validation',1200)
    vertex=None
    for cid,prompt in rendered.items():
        if cid in result['items']:continue
        attempts=[x for x in ledger['attempts'] if x['case_id']==cid]
        while len(attempts)<2 and cid not in result['items']:
            maximum=estimate_tokens(prompt)*PRICES[provider]['input']/1e6+1200*PRICES[provider]['output']/1e6
            if ledger['known_cost_usd']+maximum>ceiling:raise RuntimeError(f'{provider} ceiling reached')
            attempt={'case_id':cid,'attempt_number':len(attempts)+1,'state':'prepared','input_hash':hashlib.sha256(prompt.encode()).hexdigest(),'timestamp':time.time(),'transport':'developer_api' if provider=='gemini' else provider}
            ledger['attempts'].append(attempt);atomic_write_json(ledger_path,ledger);attempt['state']='sending';atomic_write_json(ledger_path,ledger)
            try:response=client.call(prompt)
            except requests.HTTPError as exc:
                status=exc.response.status_code if exc.response is not None else None
                body=(exc.response.text[:8000] if exc.response is not None else None);provider_code=None
                if exc.response is not None:
                    try:
                        error_json=exc.response.json();provider_code=((error_json.get('error') or {}).get('code') if isinstance(error_json,dict) else None)
                    except ValueError:pass
                attempt.update(state='confirmed_failure',http_status=status,error=str(exc),provider_error_code=provider_code,response_body=body);atomic_write_json(ledger_path,ledger);attempts=[x for x in ledger['attempts'] if x['case_id']==cid]
                if provider=='gemini' and status==429 and len(attempts)>=2 and vertex_fallback:
                    try:
                        env=validate_vertex_environment(os.environ);vertex=vertex or GeminiVertexClient(project=env['project'],location=env['location'],model=client.model,response_schema=PAIRWISE_SCHEMA,max_output_tokens=1200);response=vertex.call(prompt);attempt={'case_id':cid,'attempt_number':1,'state':'response_received','transport':'vertex_ai','input_hash':hashlib.sha256(prompt.encode()).hexdigest(),'timestamp':time.time()};ledger['attempts'].append(attempt)
                    except Exception as vertex_exc: result['failures'][cid]={'error':f'Vertex fallback: {vertex_exc}'};atomic_write_json(result_path,result);break
                else:
                    if len(attempts)<2:time.sleep(2);continue
                    result['failures'][cid]={'error':str(exc),'http_status':status};atomic_write_json(result_path,result);break
            except Exception as exc:
                attempt.update(state='ambiguous_outcome',error=f'{type(exc).__name__}: {exc}');atomic_write_json(ledger_path,ledger);result['failures'][cid]={'error':attempt['error'],'ambiguous':True};atomic_write_json(result_path,result);break
            call={'case_id':cid,'request_id':response.get('request_id'),'cost_usd':response['cost_usd'],'latency_seconds':response['latency_seconds'],'transport':attempt['transport'],**response['usage']};result['calls'].append(call);ledger['known_cost_usd']+=response['cost_usd']
            try:value=validate_pairwise_judgement(response['content'])
            except ValueError as exc:
                attempt.update(state='confirmed_failure',schema_error=str(exc));atomic_write_json(ledger_path,ledger);attempts=[x for x in ledger['attempts'] if x['case_id']==cid]
                if len(attempts)<2:continue
                result['failures'][cid]={'error':str(exc)};atomic_write_json(result_path,result);break
            attempt['state']='completed';result['items'][cid]={'case_id':cid,**value,'provider':provider,'model':client.model,'prompt_version':PAIRWISE_PROMPT_VERSION,'transport':attempt['transport']};atomic_write_json(result_path,result);atomic_write_json(ledger_path,ledger);break
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument('--run-dir',type=Path,default=DEFAULT_RUN);p.add_argument('--dry-run',action='store_true');p.add_argument('--execute-calibration',action='store_true');p.add_argument('--execute-remaining',action='store_true');p.add_argument('--execute-openai-compatibility-check',action='store_true');p.add_argument('--execute-anthropic-compatibility-check',action='store_true');p.add_argument('--compatibility-output-dir',type=Path);p.add_argument('--enable-gemini-vertex-fallback',action='store_true');p.add_argument('--confirm-combined-cost-limit-usd',type=float);p.add_argument('--confirm-remaining-cost-limit-usd',type=float);p.add_argument('--confirm-openai-limit-usd',type=float);p.add_argument('--confirm-anthropic-limit-usd',type=float);args=p.parse_args();run=args.run_dir.resolve();pre=preflight(run);print(json.dumps(pre,indent=2))
    if args.dry_run:return
    compatibility=[p for p,enabled in (("openai",args.execute_openai_compatibility_check),("anthropic",args.execute_anthropic_compatibility_check)) if enabled]
    if compatibility:
        if set(compatibility)!={"openai","anthropic"} or args.compatibility_output_dir is None:raise SystemExit('compatibility check requires both providers and a separate output directory')
        if args.confirm_openai_limit_usd is None or not 0<args.confirm_openai_limit_usd<=1 or args.confirm_anthropic_limit_usd is None or not 0<args.confirm_anthropic_limit_usd<=1 or args.confirm_combined_cost_limit_usd is None or not 0<args.confirm_combined_cost_limit_usd<=2:raise SystemExit('compatibility ceilings must be OpenAI <=1, Anthropic <=1, combined <=2')
        output=args.compatibility_output_dir.resolve();output.mkdir(parents=True,exist_ok=True);ids=load(run/'calibration_cases.json')['case_ids'][:2];rendered=prompts(run,ids);results={}
        with ThreadPoolExecutor(max_workers=2,thread_name_prefix='pairwise-compatibility') as pool:
            futures={pool.submit(worker,pvd,output,rendered,True,False,stem='compatibility',ceiling=args.confirm_openai_limit_usd if pvd=='openai' else args.confirm_anthropic_limit_usd):pvd for pvd in compatibility}
            for future in as_completed(futures):results[futures[future]]=future.result()
        summary={'schema_version':1,'case_ids':ids,'providers':{p:{'completed':len(r['items']),'failures':len(r['failures']),'cost_usd':sum(x.get('cost_usd',0) for x in r['calls'])} for p,r in results.items()}}
        atomic_write_json(output/'provider_schema_compatibility_tests.json',summary);print(json.dumps(summary,indent=2));return
    if args.execute_remaining:
        decision=load(run/'provider_selection_decision.json')
        if decision.get('decision')!='use_one_provider' or len(decision.get('providers',[]))!=1:raise SystemExit('remaining execution requires a one-provider calibration decision')
        provider=decision['providers'][0]
        if args.confirm_remaining_cost_limit_usd is None or not 0<args.confirm_remaining_cost_limit_usd<=8:raise SystemExit('remaining execution requires --confirm-remaining-cost-limit-usd <= 8')
        all_ids=[x['case_id'] for x in load(run/'pairwise_manifest.json')['items']];cal=set(load(run/'calibration_cases.json')['case_ids']);remaining=[x for x in all_ids if x not in cal]
        result=worker(provider,run,prompts(run,remaining),True,args.enable_gemini_vertex_fallback,stem='remaining_pairwise',ceiling=args.confirm_remaining_cost_limit_usd)
        atomic_write_json(run/'remaining_pairwise_results.json',result);print(json.dumps({'provider':provider,'targeted':35,'completed':len(result['items']),'failures':len(result['failures'])},indent=2));return
    if not args.execute_calibration or args.confirm_combined_cost_limit_usd is None or args.confirm_combined_cost_limit_usd>9:raise SystemExit('execution requires --execute-calibration --confirm-combined-cost-limit-usd <= 9')
    human=load(run/'human_pairwise_reviews.json')['items'];ids=load(run/'calibration_cases.json')['case_ids'];
    if any(cid not in human for cid in ids):raise SystemExit('all calibration human labels must exist before model calls')
    rendered=prompts(run); results={}
    with ThreadPoolExecutor(max_workers=4,thread_name_prefix='pairwise-calibration') as pool:
        futures={pool.submit(worker,pvd,run,rendered,True,args.enable_gemini_vertex_fallback):pvd for pvd in CEILINGS}
        for future in as_completed(futures): results[futures[future]]=future.result()
    blind=load(run/'candidate_blind_map.json')['items'];metrics=calibration_metrics(results,human,blind,ids);atomic_write_json(run/'provider_calibration_results.json',metrics)
    decision=provider_selection(metrics);atomic_write_json(run/'provider_selection_decision.json',decision)
    print(json.dumps({'metrics':metrics,'decision':decision},indent=2))

if __name__=='__main__':main()
