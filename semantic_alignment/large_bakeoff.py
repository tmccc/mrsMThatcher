"""Coordinate the budgeted, concurrent large semantic-alignment bake-off."""

from __future__ import annotations
import hashlib,json,random,threading,time
from concurrent.futures import ThreadPoolExecutor,as_completed
from typing import Any,Callable
import requests
from .bakeoff import (BAKEOFF_PROMPT_VERSION,BAKEOFF_SCHEMA_VERSION,MAX_OUTPUT_TOKENS,
    PRICES,PROVIDER_MODELS,common_prompt,estimate_tokens,validate_bakeoff_result)
from .io import atomic_write_json,read_json
from .pipeline import make_shortlists

PROVIDERS=("grok","openai","anthropic","gemini")
CEILINGS={"grok":10.0,"openai":15.0,"anthropic":10.0,"gemini":10.0}
COMBINED_CEILING=45.0

def _http_error_details(exc):
    response=exc.response
    details={'http_status':response.status_code if response is not None else None}
    if response is None:return details
    try:
        body=response.json(); error=body.get('error') if isinstance(body,dict) else None
        if isinstance(error,dict):
            if 'code' in error:details['provider_code']=error['code']
            if 'status' in error:details['provider_status']=error['status']
            if 'message' in error:details['message']=error['message']
            if isinstance(error.get('details'),list):details['details']=error['details']
    except (ValueError,TypeError):
        pass
    return details

def _hash(value:Any)->str:return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def case_id(q,b):
    """Return a stable case identifier."""
    return hashlib.sha256(f'{q}:{b}'.encode()).hexdigest()[:20]

def build_manifest(quotes,images,original_cases,validation,*,seed=20260712,limit=250):
    """Build manifest."""
    chosen=[]; seen=set()
    def add(q,b,reason,subset,extra=None):
        key=(q,b)
        if key in seen or q not in quotes or b not in images:return
        prompt=common_prompt(quotes[q],images[b]); seen.add(key); chosen.append({'case_id':case_id(q,b),'quote_hash':q,'image_basename':b,'selection_reason':reason,'source_subset':subset,'quote_fingerprint_hash':_hash(quotes[q]),'image_fingerprint_hash':_hash(images[b]),'normalised_input_hash':hashlib.sha256(prompt.encode()).hexdigest(),'estimated_input_tokens':estimate_tokens(prompt),**(extra or {})})
    for c in original_cases:add(c['quote_hash'],c['image_basename'],'original_25_regression', 'fixed_regression',{'original_case_id':c['case_id']})
    for c in validation:
        if len(chosen)>=150:break
        add(c.get('quote_hash'),c.get('image_basename'),c.get('forced_reason','existing_validation'),'canonical_or_production_validation')
    rows=make_shortlists({'items':quotes},{'items':images},{'items':[]},limit=15)
    def category(row):
        s=row['signals']; score=row['pre_score']
        if s['not_about_conflict']>0:return 'contradictory_or_conflict'
        if s['primary_issue_match'] and s['desired_visual_overlap']>0:return 'direct_or_mechanism'
        if s['primary_issue_match']:return 'primary_issue_match'
        if s['secondary_concept_overlap']>=.2:return 'consequence_or_principle'
        if s['primary_theme_overlap']>0:return 'broader_or_ideological'
        if score>=.15:return 'secondary_theme_or_ambiguous'
        return 'weak_or_unrelated'
    buckets={};
    for row in rows:buckets.setdefault(category(row),[]).append(row)
    rng=random.Random(seed); order=sorted(buckets); target=max(1,(limit-len(chosen))//len(order))
    for name in order:
        pool=sorted(buckets[name],key=lambda x:(x['quote_hash'],x['image_basename'])); rng.shuffle(pool)
        for row in pool:
            if len([x for x in chosen if x['selection_reason']==name])>=target:break
            add(row['quote_hash'],row['image_basename'],name,'deterministic_shortlist_stratum',{'pre_score':row['pre_score'],'signals':row['signals']})
    fill=sorted(rows,key=lambda x:(hashlib.sha256(f"{seed}:{x['quote_hash']}:{x['image_basename']}".encode()).hexdigest()))
    for row in fill:
        if len(chosen)>=limit:break
        add(row['quote_hash'],row['image_basename'],'deterministic_fill','deterministic_shortlist_fill',{'pre_score':row['pre_score'],'signals':row['signals']})
    if len(chosen)!=limit:raise ValueError(f'could select only {len(chosen)} cases')
    if len({x['case_id'] for x in chosen})!=limit:raise ValueError('case ID collision')
    return {'schema_version':1,'analysis_kind':'provider_critic_bakeoff_250_cases','fixed_seed':seed,'case_count':limit,'prompt_version':BAKEOFF_PROMPT_VERSION,'critic_schema_version':BAKEOFF_SCHEMA_VERSION,'original_25_preserved':True,'items':chosen}

def verify_manifest(manifest,quotes,images,original_cases):
    """Verify manifest."""
    items=manifest['items']; errors=[]
    if len(items)!=250 or len({x['case_id'] for x in items})!=250 or len({(x['quote_hash'],x['image_basename']) for x in items})!=250:errors.append('manifest cardinality/uniqueness')
    for x in items:
        q,b=x['quote_hash'],x['image_basename']
        if q not in quotes or b not in images:errors.append(f'missing fingerprint:{x["case_id"]}');continue
        if x['quote_fingerprint_hash']!=_hash(quotes[q]) or x['image_fingerprint_hash']!=_hash(images[b]) or x['normalised_input_hash']!=hashlib.sha256(common_prompt(quotes[q],images[b]).encode()).hexdigest():errors.append(f'hash mismatch:{x["case_id"]}')
    old={(x['quote_hash'],x['image_basename'],x['case_id']) for x in original_cases}; new={(x['quote_hash'],x['image_basename'],x['case_id']) for x in items};
    if not old<=new:errors.append('original regression cases changed')
    if errors:raise ValueError('; '.join(errors[:20]))
    return {'valid':True,'cases':250,'original_25':25}

def maximum_attempt_cost(provider,prompt):
    """Estimate the maximum cost of one provider attempt."""
    return estimate_tokens(prompt)*PRICES[provider]['input']/1e6+MAX_OUTPUT_TOKENS*PRICES[provider]['output']/1e6

class SharedBudget:
    """Enforce provider and combined spend ceilings across workers."""

    def __init__(self,run_dir,combined_limit=COMBINED_CEILING):
        """Initialise the shared budget."""
        self.run_dir=run_dir;self.combined_limit=combined_limit;self.lock=threading.Lock()
    def known(self):
        """Return known completed-call spend across all providers."""
        return sum(sum(float(x['cost_usd']) for x in (read_json(self.run_dir/f'{p}_ledger.json',{}) or {}).get('calls',[])) for p in PROVIDERS)
    def guard(self,provider,provider_spend,next_max):
        """Reject a call that could exceed configured spend limits."""
        with self.lock:
            if provider_spend+next_max>CEILINGS[provider]:raise RuntimeError(f'{provider} ceiling reached')
            if self.known()+next_max>self.combined_limit:raise RuntimeError('combined ceiling reached')

class Worker:
    """Run one provider's resumable large-bake-off workload."""
    def __init__(self,provider,cases,quotes,images,run_dir,client,budget,sleep:Callable[[float],None]=time.sleep):
        """Initialise the worker."""
        self.provider=provider;self.cases=cases;self.quotes=quotes;self.images=images;self.run_dir=run_dir;self.client=client;self.budget=budget;self.sleep=sleep;self.ledger_path=run_dir/f'{provider}_ledger.json';self.results_path=run_dir/f'{provider}_results.json';self.active=0;self.max_active=0
    def load(self):
        """Load and reconcile this worker's durable ledger and results."""
        ledger=read_json(self.ledger_path,None) or {'schema_version':1,'provider':self.provider,'model':self.client.model,'attempts':[],'calls':[],'exhausted':{},'confirmed_failures':[],'ambiguous_outcomes':[],'uncertain_possible_exposure_usd':0}; results=read_json(self.results_path,None) or {'schema_version':1,'provider':self.provider,'model':self.client.model,'prompt_version':BAKEOFF_PROMPT_VERSION,'items':{},'failures':{}}
        # A prior sending state without a completed call is ambiguous and consumes that attempt.
        completed_attempts={(x['case_id'],x['attempt_number']) for x in ledger['calls']}
        for x in ledger['attempts']:
            if x['lifecycle_state']=='sending' and (x['case_id'],x['attempt_number']) not in completed_attempts:
                x['lifecycle_state']='ambiguous_outcome'; ledger['ambiguous_outcomes'].append({'case_id':x['case_id'],'attempt_number':x['attempt_number'],'recovered_on_resume':True,'maximum_possible_charge_usd':x['maximum_attempt_cost_usd']}); ledger['uncertain_possible_exposure_usd']+=x['maximum_attempt_cost_usd']
        atomic_write_json(self.ledger_path,ledger);atomic_write_json(self.results_path,results);return ledger,results
    def run(self):
        """Run incomplete cases while respecting retries and spend ceilings."""
        started=time.time();ledger,results=self.load(); paused=None
        try:
            for case in self.cases:
                cid=case['case_id']
                if cid in results['items'] or cid in ledger['exhausted']:continue
                prior=[x for x in ledger['attempts'] if x['case_id']==cid]
                while len(prior)<2 and cid not in results['items']:
                    attempt_no=len(prior)+1;prompt=common_prompt(self.quotes[case['quote_hash']],self.images[case['image_basename']]);max_cost=maximum_attempt_cost(self.provider,prompt);spend=sum(float(x['cost_usd']) for x in ledger['calls']);self.budget.guard(self.provider,spend,max_cost)
                    attempt={'run_id':self.run_dir.name,'provider':self.provider,'model':self.client.model,'case_id':cid,'quote_hash':case['quote_hash'],'image_basename':case['image_basename'],'request_sequence':len(ledger['attempts'])+1,'attempt_number':attempt_no,'prompt_version':BAKEOFF_PROMPT_VERSION,'schema_version':BAKEOFF_SCHEMA_VERSION,'input_hash':case['normalised_input_hash'],'timestamp':time.time(),'lifecycle_state':'prepared','maximum_attempt_cost_usd':max_cost};ledger['attempts'].append(attempt);atomic_write_json(self.ledger_path,ledger);attempt['lifecycle_state']='sending';atomic_write_json(self.ledger_path,ledger)
                    self.active+=1;self.max_active=max(self.max_active,self.active)
                    try: response=self.client.call(prompt)
                    except requests.HTTPError as exc:
                        self.active-=1;details=_http_error_details(exc);status=details['http_status']; permanent=status is not None and 400<=status<500 and status!=429;attempt['lifecycle_state']='confirmed_failure';attempt['error']=f'HTTP {status}';ledger['confirmed_failures'].append({'case_id':cid,'attempt_number':attempt_no,'status':status,**details});atomic_write_json(self.ledger_path,ledger)
                        if permanent:paused=f'permanent HTTP {status}';break
                        prior=[x for x in ledger['attempts'] if x['case_id']==cid];self.sleep(min(2**attempt_no,4));continue
                    except BaseException as exc:
                        self.active-=1;attempt['lifecycle_state']='ambiguous_outcome';attempt['error']=f'{type(exc).__name__}: {exc}';ledger['ambiguous_outcomes'].append({'case_id':cid,'attempt_number':attempt_no,'maximum_possible_charge_usd':max_cost});ledger['uncertain_possible_exposure_usd']+=max_cost;atomic_write_json(self.ledger_path,ledger);prior=[x for x in ledger['attempts'] if x['case_id']==cid];continue
                    self.active-=1;attempt['lifecycle_state']='response_received';attempt['request_id']=response.get('request_id');call={'case_id':cid,'attempt_number':attempt_no,'request_id':response.get('request_id'),'timestamp':time.time(),'latency_seconds':response['latency_seconds'],'cost_usd':response['cost_usd'],**response['usage']};ledger['calls'].append(call);atomic_write_json(self.ledger_path,ledger)
                    try: value=validate_bakeoff_result(response['content'])
                    except ValueError as exc:
                        attempt['lifecycle_state']='confirmed_failure';attempt['error']=str(exc);ledger['confirmed_failures'].append({'case_id':cid,'attempt_number':attempt_no,'schema_error':str(exc),'charged_call':call});results['failures'].setdefault(cid,[]).append({'attempt':attempt_no,'error':str(exc)});atomic_write_json(self.ledger_path,ledger);atomic_write_json(self.results_path,results);prior=[x for x in ledger['attempts'] if x['case_id']==cid];continue
                    attempt['lifecycle_state']='completed';results['items'][cid]={'case_id':cid,'quote_hash':case['quote_hash'],'image_basename':case['image_basename'],**value,'model':self.client.model};atomic_write_json(self.results_path,results);atomic_write_json(self.ledger_path,ledger);break
                if paused:break
                if cid not in results['items'] and len([x for x in ledger['attempts'] if x['case_id']==cid])>=2:ledger['exhausted'][cid]={'reason':'exhausted_after_retry'};atomic_write_json(self.ledger_path,ledger)
        finally:
            summary={'provider':self.provider,'started_at':started,'ended_at':time.time(),'elapsed_seconds':time.time()-started,'completed':len(results['items']),'exhausted':len(ledger['exhausted']),'known_cost_usd':sum(x['cost_usd'] for x in ledger['calls']),'uncertain_possible_exposure_usd':ledger['uncertain_possible_exposure_usd'],'attempts':len(ledger['attempts']),'max_active_requests':self.max_active,'paused_reason':paused};atomic_write_json(self.run_dir/f'{self.provider}_worker_summary.json',summary)
        return summary

def run_concurrent(workers):
    """Run concurrent."""
    started=time.time();summaries={};
    with ThreadPoolExecutor(max_workers=len(workers),thread_name_prefix='critic_provider') as pool:
        futures={pool.submit(w.run):w.provider for w in workers}
        for future in as_completed(futures):
            provider=futures[future]
            try:summaries[provider]=future.result()
            except BaseException as exc:summaries[provider]={'provider':provider,'worker_error':f'{type(exc).__name__}: {exc}'}
    elapsed=time.time()-started;return {'started_at':started,'ended_at':time.time(),'wall_clock_seconds':elapsed,'providers':summaries,'theoretical_sequential_seconds':sum(x.get('elapsed_seconds',0) for x in summaries.values()),'speedup':sum(x.get('elapsed_seconds',0) for x in summaries.values())/elapsed if elapsed else None}

def preflight(manifest,quotes):
    """Build the deterministic execution preflight."""
    input_tokens=sum(estimate_tokens_from_case(x) for x in manifest['items'])
    rows={}
    for p in PROVIDERS:
        expected_output=900*250;max_output=MAX_OUTPUT_TOKENS*250;expected=input_tokens*PRICES[p]['input']/1e6+expected_output*PRICES[p]['output']/1e6;base_max=input_tokens*PRICES[p]['input']/1e6+max_output*PRICES[p]['output']/1e6;retry_reserve=max((estimate_tokens_from_case(x)*PRICES[p]['input']/1e6+MAX_OUTPUT_TOKENS*PRICES[p]['output']/1e6 for x in manifest['items']),default=0)
        rows[p]={'model':PROVIDER_MODELS[p],'calls':250,'estimated_input_tokens':input_tokens,'estimated_output_tokens':expected_output,'maximum_output_tokens':max_output,'expected_cost_usd':expected,'conservative_maximum_base_cost_usd':base_max,'conservative_single_retry_reserve_usd':retry_reserve,'theoretical_all_cases_retry_cost_usd':base_max,'maximum_ambiguous_exposure_usd':base_max,'ceiling_usd':CEILINGS[p],'tools_enabled':False}
    return {'schema_version':1,'providers':rows,'combined_expected_cost_usd':sum(x['expected_cost_usd'] for x in rows.values()),'combined_conservative_known_cost_usd':sum(x['conservative_maximum_base_cost_usd']+x['conservative_single_retry_reserve_usd'] for x in rows.values()),'combined_ceiling_usd':COMBINED_CEILING,'note':'Execution ceilings prevent all-case retry maxima from being realised; each next attempt is gated independently.'}

def estimate_tokens_from_case(case):
    """Estimate prompt tokens for one comparison case."""
    return max(1,int(case.get('estimated_input_tokens') or 2100))
