import json
from pathlib import Path
import pytest
from google.genai import errors
from semantic_alignment.bakeoff import common_prompt
from semantic_alignment.vertex_recovery import (GeminiVertexClient,THINKING_BUDGET,
    VERTEX_MODEL,VertexRecoveryWorker,validate_vertex_environment)

def result():
    scores={x:70 for x in ('relevance_score','directness_score','mechanism_alignment_score','consequence_alignment_score','principle_alignment_score','specificity_score','editorial_power_score','overall_suitability_score')}
    return {'primary_relationship':'direct_illustration','secondary_relationship':None,**scores,'keep_or_replace':'keep','quote_mechanism':['trade'],'quote_claimed_consequences':['prosperity'],'quote_broader_principles':['freedom'],'image_depicted_subject':['port'],'image_implied_mechanism':['trade'],'image_depicted_consequences':['prosperity'],'image_ideological_framing':[],'matched_elements':['trade'],'unillustrated_primary_elements':[],'extraneous_image_arguments':[],'explanation':'Direct illustration.','stronger_visual_direction':[]}
def quote():return {'quote_text':'Trade creates prosperity.'}
def image():return {'dominant_message':'Trade and prosperity.'}
def case():
    return {'case_id':'c','quote_hash':'q','image_basename':'i','normalised_input_hash':'h','estimated_input_tokens':100}

def test_environment_and_adc_validation(monkeypatch,tmp_path):
    import semantic_alignment.vertex_recovery as m
    adc=tmp_path/'adc.json';adc.write_text('{}');monkeypatch.setattr(m,'adc_path',lambda:adc)
    assert validate_vertex_environment({'GOOGLE_CLOUD_PROJECT':'p','GOOGLE_GENAI_USE_VERTEXAI':'true'})['location']=='global'
    with pytest.raises(RuntimeError):validate_vertex_environment({'GOOGLE_GENAI_USE_VERTEXAI':'true'})
    adc.unlink()
    with pytest.raises(RuntimeError):validate_vertex_environment({'GOOGLE_CLOUD_PROJECT':'p','GOOGLE_GENAI_USE_VERTEXAI':'true'})

def test_vertex_client_model_and_setting_parity():
    client=GeminiVertexClient(project='p',client=object())
    config=client.config()
    assert client.model==VERTEX_MODEL and config.thinking_config.thinking_budget==THINKING_BUDGET
    assert config.max_output_tokens==1600 and config.response_mime_type=='application/json'
    assert config.tools is None
    with pytest.raises(RuntimeError):GeminiVertexClient(project='p',model='gemini-flash',client=object())

def test_semantic_prompt_is_transport_independent():
    prompt=common_prompt(quote(),image())
    assert prompt==common_prompt(quote(),image())
    assert 'human label' not in prompt.lower() and 'provider result' not in prompt.lower()

class Fake:
    model=VERTEX_MODEL
    def __init__(self,fail=0):self.calls=0;self.fail=fail
    def call(self,prompt):
        self.calls+=1
        if self.calls<=self.fail:raise errors.ServerError(503,{'error':{'message':'temporary'}})
        return {'content':result(),'usage':{'input_tokens':10,'cached_tokens':0,'reasoning_tokens':2,'output_tokens':12},'cost_usd':.001,'latency_seconds':.1,'request_id':'r','model_version':VERTEX_MODEL,'traffic_type':'ON_DEMAND'}

def test_vertex_lifecycle_retry_once_and_resume(tmp_path):
    client=Fake(fail=1);worker=VertexRecoveryWorker(run_dir=tmp_path,cases=[case()],quotes={'q':quote()},images={'i':image()},client=client,sleep=lambda _:None)
    summary=worker.run();assert summary['completed']==1 and client.calls==2
    worker.run();assert client.calls==2
    ledger=json.load(open(tmp_path/'vertex_ledger.json'));assert len(ledger['attempts'])==2 and ledger['attempts'][-1]['lifecycle_state']=='completed'
    item=json.load(open(tmp_path/'vertex_results.json'))['items']['c'];assert item['recovered_via_vertex_ai'] is True

def test_third_attempt_impossible(tmp_path):
    client=Fake(fail=3);worker=VertexRecoveryWorker(run_dir=tmp_path,cases=[case()],quotes={'q':quote()},images={'i':image()},client=client,sleep=lambda _:None)
    summary=worker.run();assert summary['still_missing']==1 and client.calls==2
    worker.run();assert client.calls==2

def test_sending_resume_is_ambiguous_and_not_retried(tmp_path):
    (tmp_path/'vertex_ledger.json').write_text(json.dumps({'schema_version':1,'provider':'gemini_vertex','model':VERTEX_MODEL,'attempts':[{'case_id':'c','attempt_number':1,'lifecycle_state':'sending'}],'calls':[],'failures':[],'ambiguous':[],'final_failures':{},'known_cost_usd':0}))
    client=Fake();worker=VertexRecoveryWorker(run_dir=tmp_path,cases=[case()],quotes={'q':quote()},images={'i':image()},client=client)
    worker.run();assert client.calls==0
    assert json.load(open(tmp_path/'vertex_ledger.json'))['final_failures']['c']['reason']=='ambiguous_outcome_requires_explicit_resolution'
