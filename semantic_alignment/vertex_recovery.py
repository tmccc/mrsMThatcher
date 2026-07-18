from __future__ import annotations

import json,os,time
from pathlib import Path
from typing import Any,Callable

from google import genai
from google.genai import errors,types

from .bakeoff import (BAKEOFF_OUTPUT_SCHEMA,BAKEOFF_PROMPT_VERSION,
    BAKEOFF_SCHEMA_VERSION,MAX_OUTPUT_TOKENS,PRICES,common_prompt,
    validate_bakeoff_result)
from .io import atomic_write_json,read_json

VERTEX_MODEL='gemini-3.1-pro-preview'
THINKING_BUDGET=512
VERTEX_LIMIT=2.0
COMBINED_RECOVERY_LIMIT=2.5

def adc_path()->Path:return Path.home()/'.config/gcloud/application_default_credentials.json'
def validate_vertex_environment(env=os.environ)->dict[str,str]:
    project=env.get('GOOGLE_CLOUD_PROJECT');location=env.get('GOOGLE_CLOUD_LOCATION','global');enabled=env.get('GOOGLE_GENAI_USE_VERTEXAI')
    if not project:raise RuntimeError('GOOGLE_CLOUD_PROJECT is required')
    if enabled!='true':raise RuntimeError('GOOGLE_GENAI_USE_VERTEXAI must be true')
    if not adc_path().is_file():raise RuntimeError(f'ADC file is missing: {adc_path()}')
    return {'project':project,'location':location,'vertexai':enabled}

class GeminiVertexClient:
    def __init__(self,*,project:str,location:str='global',model:str=VERTEX_MODEL,client=None,
                 response_schema:dict[str,Any]=BAKEOFF_OUTPUT_SCHEMA,max_output_tokens:int=MAX_OUTPUT_TOKENS):
        if model!=VERTEX_MODEL:raise RuntimeError(f'Vertex model must match original: {VERTEX_MODEL}')
        self.model=model;self.project=project;self.location=location
        self.response_schema=response_schema;self.max_output_tokens=max_output_tokens
        self.client=client or genai.Client(vertexai=True,project=project,location=location)
    def config(self):
        return types.GenerateContentConfig(
            max_output_tokens=self.max_output_tokens,
            response_mime_type='application/json',response_json_schema=self.response_schema,
            thinking_config=types.ThinkingConfig(thinking_budget=THINKING_BUDGET),
            http_options=types.HttpOptions(timeout=180_000),
        )
    def call(self,prompt:str)->dict[str,Any]:
        started=time.monotonic();response=self.client.models.generate_content(model=self.model,contents=prompt,config=self.config());latency=time.monotonic()-started
        usage=response.usage_metadata
        if usage is None:raise RuntimeError('missing authoritative Vertex usage metadata')
        input_tokens=int(usage.prompt_token_count or 0);completion=int(usage.candidates_token_count or 0);thinking=int(usage.thoughts_token_count or 0);cached=int(usage.cached_content_token_count or 0);output=completion+thinking
        if not input_tokens and not output:raise RuntimeError('empty authoritative Vertex usage metadata')
        cost=(input_tokens-cached)*PRICES['gemini']['input']/1e6+cached*PRICES['gemini']['cached_input']/1e6+output*PRICES['gemini']['output']/1e6
        content=response.parsed if isinstance(response.parsed,dict) else json.loads(response.text)
        return {'content':content,'usage':{'input_tokens':input_tokens,'cached_tokens':cached,'reasoning_tokens':thinking,'output_tokens':output},'cost_usd':cost,'latency_seconds':latency,'request_id':response.response_id,'model_version':response.model_version,'traffic_type':str(getattr(usage,'traffic_type',None))}

def error_details(exc:BaseException)->dict[str,Any]:
    code=getattr(exc,'code',None);message=str(getattr(exc,'message',None) or exc)
    return {'code':code,'message':message,'type':type(exc).__name__}

class VertexRecoveryWorker:
    def __init__(self,*,run_dir:Path,cases:list[dict[str,Any]],quotes,images,client:GeminiVertexClient,sleep:Callable[[float],None]=time.sleep):
        self.run_dir=run_dir;self.cases=cases;self.quotes=quotes;self.images=images;self.client=client;self.sleep=sleep
        self.ledger_path=run_dir/'vertex_ledger.json';self.results_path=run_dir/'vertex_results.json'
    def load(self):
        ledger=read_json(self.ledger_path,None) or {'schema_version':1,'provider':'gemini_vertex','model':self.client.model,'attempts':[],'calls':[],'failures':[],'ambiguous':[],'final_failures':{},'known_cost_usd':0.0}
        results=read_json(self.results_path,None) or {'schema_version':1,'provider':'gemini_vertex','model':self.client.model,'prompt_version':BAKEOFF_PROMPT_VERSION,'items':{}}
        completed={(x['case_id'],x['attempt_number']) for x in ledger['calls']}
        for attempt in ledger['attempts']:
            if attempt['lifecycle_state']=='sending' and (attempt['case_id'],attempt['attempt_number']) not in completed:
                attempt['lifecycle_state']='ambiguous_outcome';ledger['ambiguous'].append({'case_id':attempt['case_id'],'attempt_number':attempt['attempt_number'],'reason':'sending state found on resume'})
        atomic_write_json(self.ledger_path,ledger);atomic_write_json(self.results_path,results);return ledger,results
    def run(self):
        ledger,results=self.load();started=time.time()
        for case in self.cases:
            cid=case['case_id']
            if cid in results['items'] or cid in ledger['final_failures']:continue
            attempts=[x for x in ledger['attempts'] if x['case_id']==cid]
            if any(x['lifecycle_state']=='ambiguous_outcome' for x in attempts):
                ledger['final_failures'][cid]={'reason':'ambiguous_outcome_requires_explicit_resolution'};atomic_write_json(self.ledger_path,ledger);continue
            while len(attempts)<2 and cid not in results['items']:
                prompt=common_prompt(self.quotes[case['quote_hash']],self.images[case['image_basename']]);number=len(attempts)+1
                maximum=(case['estimated_input_tokens']*PRICES['gemini']['input']+MAX_OUTPUT_TOKENS*PRICES['gemini']['output'])/1e6
                if ledger['known_cost_usd']+maximum>VERTEX_LIMIT:raise RuntimeError('Vertex recovery ceiling reached')
                attempt={'recovery_run_id':self.run_dir.name,'parent_run_id':'provider_bakeoff_250_20260712_v1','case_id':cid,'quote_hash':case['quote_hash'],'image_basename':case['image_basename'],'vertex_model':self.client.model,'input_hash':case['normalised_input_hash'],'prompt_version':BAKEOFF_PROMPT_VERSION,'schema_version':BAKEOFF_SCHEMA_VERSION,'attempt_number':number,'timestamp':time.time(),'lifecycle_state':'prepared'}
                ledger['attempts'].append(attempt);atomic_write_json(self.ledger_path,ledger);attempt['lifecycle_state']='sending';atomic_write_json(self.ledger_path,ledger)
                try:response=self.client.call(prompt)
                except errors.APIError as exc:
                    detail=error_details(exc);code=detail['code'];attempt['lifecycle_state']='confirmed_failure';attempt['error']=detail;ledger['failures'].append({'case_id':cid,'attempt_number':number,**detail});atomic_write_json(self.ledger_path,ledger)
                    attempts=[x for x in ledger['attempts'] if x['case_id']==cid]
                    if code in {429,500,502,503,504} and len(attempts)<2:self.sleep(4);continue
                    break
                except (TimeoutError,ConnectionError,OSError) as exc:
                    attempt['lifecycle_state']='ambiguous_outcome';ledger['ambiguous'].append({'case_id':cid,'attempt_number':number,**error_details(exc)});atomic_write_json(self.ledger_path,ledger);break
                attempt['lifecycle_state']='response_received';call={'case_id':cid,'attempt_number':number,'request_id':response['request_id'],'timestamp':time.time(),'latency_seconds':response['latency_seconds'],'cost_usd':response['cost_usd'],'model_version':response['model_version'],'traffic_type':response['traffic_type'],**response['usage']};ledger['calls'].append(call);ledger['known_cost_usd']+=response['cost_usd'];atomic_write_json(self.ledger_path,ledger)
                try:value=validate_bakeoff_result(response['content'])
                except ValueError as exc:
                    attempt['lifecycle_state']='confirmed_failure';ledger['failures'].append({'case_id':cid,'attempt_number':number,'schema_error':str(exc),'charged_call':call});atomic_write_json(self.ledger_path,ledger);attempts=[x for x in ledger['attempts'] if x['case_id']==cid];continue
                attempt['lifecycle_state']='completed';results['items'][cid]={'case_id':cid,'quote_hash':case['quote_hash'],'image_basename':case['image_basename'],**value,'model':self.client.model,'recovered_via_vertex_ai':True,'recovery_provenance':{'parent_run_id':'provider_bakeoff_250_20260712_v1','parent_case_id':cid,'original_attempts':'gemini_ledger.json','vertex_request_id':response['request_id'],'traffic_type':response['traffic_type']}};atomic_write_json(self.results_path,results);atomic_write_json(self.ledger_path,ledger);break
            if cid not in results['items']:
                attempts=[x for x in ledger['attempts'] if x['case_id']==cid]
                reason='ambiguous_outcome' if any(x['lifecycle_state']=='ambiguous_outcome' for x in attempts) else 'recovery_failed_final'
                ledger['final_failures'][cid]={'reason':reason,'attempts':len(attempts)};atomic_write_json(self.ledger_path,ledger)
        summary={'targeted':len(self.cases),'completed':len(results['items']),'still_missing':len(self.cases)-len(results['items']),'attempts':len(ledger['attempts']),'known_cost_usd':ledger['known_cost_usd'],'ambiguous':len(ledger['ambiguous']),'elapsed_seconds':time.time()-started}
        atomic_write_json(self.run_dir/'vertex_summary.json',summary);return summary
