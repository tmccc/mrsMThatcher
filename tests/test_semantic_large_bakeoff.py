from __future__ import annotations
import json,threading
import pytest
from semantic_alignment.bakeoff import common_prompt
from semantic_alignment.large_bakeoff import SharedBudget,Worker,build_manifest,run_concurrent,verify_manifest

def quote(n):return {'schema_version':2,'quote_hash':f'{n:064x}','quote_text':f'Quote {n}','core_claim':'freedom creates prosperity','claims':[],'primary_themes':['freedom'],'secondary_themes':['growth'],'specific_concepts':['prosperity'],'dominant_message':'freedom','desired_visual_evidence':['market'],'explicit_contrasts':[],'not_about':[],'confidence':.9,'analysis_kind':'quote_semantic_fingerprint','primary_issue':'freedom'}
def image(n):return {'schema_version':2,'image_basename':f'tg_{n:064x}.png','sha256':f'{n:064x}','core_implied_claim':'prosperity','implied_claims':[],'primary_themes':['freedom'],'secondary_messages':['growth'],'specific_concepts':['prosperity'],'visual_evidence':['market'],'dominant_message':'prosperity','emotional_tone':[],'ambiguity':'low','confidence':.9,'analysis_kind':'image_implied_message','primary_issue':'freedom'}
def result():
    fields=('relevance_score','directness_score','mechanism_alignment_score','consequence_alignment_score','principle_alignment_score','specificity_score','editorial_power_score','overall_suitability_score');return {'primary_relationship':'direct_illustration','secondary_relationship':None,**{x:70 for x in fields},'keep_or_replace':'keep','quote_mechanism':['freedom'],'quote_claimed_consequences':['prosperity'],'quote_broader_principles':['liberty'],'image_depicted_subject':['market'],'image_implied_mechanism':['choice'],'image_depicted_consequences':['prosperity'],'image_ideological_framing':[],'matched_elements':['prosperity'],'unillustrated_primary_elements':[],'extraneous_image_arguments':[],'explanation':'Direct fit.','stronger_visual_direction':[]}
class Client:
    model='fake'
    def __init__(self,fail=0,barrier=None):self.fail=fail;self.calls=0;self.barrier=barrier;self.active=0;self.max_active=0
    def call(self,prompt):
        self.calls+=1;self.active+=1;self.max_active=max(self.max_active,self.active)
        if self.barrier and self.calls==1:self.barrier.wait(timeout=2)
        self.active-=1
        if self.calls<=self.fail:raise TimeoutError('ambiguous')
        return {'content':result(),'usage':{'input_tokens':10,'cached_tokens':0,'reasoning_tokens':0,'output_tokens':10},'cost_usd':.0001,'latency_seconds':.01,'request_id':str(self.calls)}
def inventories():
    q={x['quote_hash']:x for x in [quote(n) for n in range(260)]};i={x['image_basename']:x for x in [image(n) for n in range(79)]};old=[]
    for n in range(25):old.append({'case_id':__import__('hashlib').sha256(f'{n:064x}:tg_{n:064x}.png'.encode()).hexdigest()[:20],'quote_hash':f'{n:064x}','image_basename':f'tg_{n:064x}.png'})
    val=[{'quote_hash':f'{n:064x}','image_basename':f'tg_{n%79:064x}.png','forced_reason':'validation'} for n in range(150)];return q,i,old,val
def test_deterministic_250_preserves_original_and_free_trade_shape():
    q,i,o,v=inventories();a=build_manifest(q,i,o,v);b=build_manifest(q,i,o,v);assert a==b and len(a['items'])==250 and verify_manifest(a,q,i,o)['valid'];assert {x['case_id'] for x in o}<={x['case_id'] for x in a['items']}
def test_prompt_has_no_results_or_labels():
    p=common_prompt(quote(1),image(1));assert 'human_label' not in p and 'production_winner' not in p and 'another critic' in p
def small_cases(n=2):
    q={quote(x)['quote_hash']:quote(x) for x in range(n)};i={image(x)['image_basename']:image(x) for x in range(n)};cases=[]
    for x in range(n):
        prompt=common_prompt(q[f'{x:064x}'],i[f'tg_{x:064x}.png']);cases.append({'case_id':str(x),'quote_hash':f'{x:064x}','image_basename':f'tg_{x:064x}.png','normalised_input_hash':__import__('hashlib').sha256(prompt.encode()).hexdigest()})
    return cases,q,i
def test_four_workers_start_concurrently_and_one_active_each(tmp_path):
    cases,q,i=small_cases(1);barrier=threading.Barrier(4);workers=[];budget=SharedBudget(tmp_path)
    for p in ('grok','openai','anthropic','gemini'):
        c=Client(barrier=barrier);workers.append(Worker(p,cases,q,i,tmp_path,c,budget))
    summary=run_concurrent(workers);assert len(summary['providers'])==4 and all(w.max_active==1 for w in workers)
def test_one_retry_then_exhausted_and_no_attempt_three(tmp_path):
    cases,q,i=small_cases(1);c=Client(fail=2);w=Worker('grok',cases,q,i,tmp_path,c,SharedBudget(tmp_path),sleep=lambda _:None);s=w.run();assert s['exhausted']==1 and c.calls==2;w.run();assert c.calls==2
def test_retry_success_and_resume_skips_completed(tmp_path):
    cases,q,i=small_cases(1);c=Client(fail=1);w=Worker('grok',cases,q,i,tmp_path,c,SharedBudget(tmp_path),sleep=lambda _:None);assert w.run()['completed']==1 and c.calls==2;w.run();assert c.calls==2
def test_provider_failure_does_not_stop_others(tmp_path):
    cases,q,i=small_cases(1);workers=[];budget=SharedBudget(tmp_path)
    for p in ('grok','openai','anthropic','gemini'):workers.append(Worker(p,cases,q,i,tmp_path,Client(fail=2 if p=='grok' else 0),budget,sleep=lambda _:None))
    s=run_concurrent(workers);assert s['providers']['grok']['exhausted']==1 and all(s['providers'][p]['completed']==1 for p in ('openai','anthropic','gemini'))
def test_sending_state_becomes_ambiguous_on_resume(tmp_path):
    cases,q,i=small_cases(1);c=Client();w=Worker('grok',cases,q,i,tmp_path,c,SharedBudget(tmp_path));json.dump({'schema_version':1,'provider':'grok','model':'fake','attempts':[{'case_id':'0','attempt_number':1,'lifecycle_state':'sending','maximum_attempt_cost_usd':.1}],'calls':[],'exhausted':{},'confirmed_failures':[],'ambiguous_outcomes':[],'uncertain_possible_exposure_usd':0},open(w.ledger_path,'w'));w.run();d=json.load(open(w.ledger_path));assert d['uncertain_possible_exposure_usd']==.1 and len(d['attempts'])==2
def test_ceiling_enforced(tmp_path):
    b=SharedBudget(tmp_path)
    with pytest.raises(RuntimeError):b.guard('grok',9.99,.02)
    with pytest.raises(RuntimeError):b.guard('openai',14.99,.02)
def test_independent_ledgers(tmp_path):
    cases,q,i=small_cases(1);budget=SharedBudget(tmp_path);Worker('grok',cases,q,i,tmp_path,Client(),budget).run();Worker('openai',cases,q,i,tmp_path,Client(),budget).run();assert (tmp_path/'grok_ledger.json').exists() and (tmp_path/'openai_ledger.json').exists()
