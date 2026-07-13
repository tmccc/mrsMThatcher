from __future__ import annotations
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from semantic_alignment.bakeoff import ProviderClient
from semantic_alignment.first_impression import *

def quote(q='q'*64):return {'quote_hash':q,'quote_text':'A climber plants a national flag.','dominant_message':'Achievement becomes national service.','desired_visual_evidence':['climber','summit','flag'],'not_about':['communist expansion'],'prompt_version':'q2'}
def legacy():return {'prompt_version':'old','analysis':{'summary':'Personal achievement culminates in patriotic service.','tone':['inspirational','patriotic','reflective'],'archive_image_preferences':{'strong_visual_mismatches':['Cold War warning']}}}
def image_result():return {'first_impression_message':'A climber reaches a summit.','dominant_visual_subject':'climber','first_object_noticed':'national flag','largest_visual_element':'mountain','highest_contrast_element':'flag','most_recognisable_symbol':'national flag','dominant_symbols':['flag'],'attention_hierarchy':[{'rank':1,'element':'flag','attention_strength':.9}], 'visual_competition':[],'concrete_visual_evidence':['climber at summit'],'face_prominence':10,'text_like_element_prominence':0,'background_dominance':50,'focal_clarity_score':90,'dominant_subject_strength':90,'distracting_symbol_score':5,'visual_competition_score':5,'visual_clutter_score':10,'primary_tone':'inspirational','secondary_tones':['patriotic'],'emotional_energy':'high','patriotic_tone':90,'inspirational_tone':90,'warning_tone':5,'defensive_tone':5,'optimistic_tone':80,'dominant_visual_message_confidence':.95,'ambiguity':'low'}
def alignment():return {'dominant_visual_message_alignment_score':85,'tone_alignment_score':90,'subject_alignment_score':88,'salience_interference_score':5,'first_second_fit':'strong','dominant_mismatch':'none material','quote_message_visually_present':True,'quote_message_visually_dominant':True,'competing_message':'none','competing_message_strength':'none','editorial_risk':'low','explanation':'Strong fit.','stronger_visual_direction':['none needed']}

def test_image_prompt_contains_no_quote_or_expected_context():
    p=image_first_prompt().lower();assert 'quotation or caption' in p and 'everest' not in p and 'communist' not in p
def test_quote_intent_contains_no_image_and_reuses_existing_fields():
    row=derive_quote_intent(quote(),legacy());assert row['desired_tone']==['inspirational','patriotic','reflective'] and 'image_basename' not in row
def test_critic_receives_fingerprints_only_and_no_leakage():
    intent=derive_quote_intent(quote(),legacy());intent['human_label']='SECRET_HUMAN';img={'schema_version':1,'analysis_kind':'image_first_impression','image_basename':'i.png','sha256':'a'*64,**image_result(),'generation_prompt':'SECRET_PROMPT','production_winner':'SECRET_WINNER'};p=critic_prompt(intent,img);assert all(secret not in p for secret in ('SECRET_HUMAN','SECRET_PROMPT','SECRET_WINNER'))
def test_first_object_symbol_tone_and_score_validation():
    row=validate_image_first(image_result());assert row['first_object_noticed']=='national flag'
    for mutation in ({'primary_tone':'joyful'},{'focal_clarity_score':101},{'focal_clarity_score':True}):
        bad={**image_result(),**mutation}
        with pytest.raises(ValueError):validate_image_first(bad)
def test_attention_ranks_must_be_contiguous():
    bad=image_result();bad['attention_hierarchy']=[{'rank':2,'element':'flag','attention_strength':.9}]
    with pytest.raises(ValueError):validate_image_first(bad)
def test_hash_change_and_resume_detection():
    cases=[{'image_basename':'i.png'}];inventory={'i.png':{'image_basename':'i.png','sha256':'a','path':Path('i')}}
    assert len(pending_images(cases,inventory,{'items':{}}))==1
    assert pending_images(cases,inventory,{'items':{'i.png':{'sha256':'a'}}})==[]
    assert len(pending_images(cases,inventory,{'items':{'i.png':{'sha256':'b'}}}))==1
def test_active_quarantine_separation_and_validation_cases():
    cases=[{'case_id':'x','quote_hash':'a'*64,'image_basename':'active.png'}];human={'x':{'human_action':'keep'}};dis=[]
    with pytest.raises(ValueError):build_validation_cases(cases,human,dis,{'active.png'},limit=2)
    assert 'quarantine.png' not in {'active.png'}
def test_everest_and_free_trade_forced_in_real_shape():
    rows=[{'case_id':'free','quote_hash':FREE_TRADE_KEY[0],'image_basename':FREE_TRADE_KEY[1]}]
    active={EVEREST_IMAGE,FREE_TRADE_KEY[1]}
    result=build_validation_cases(rows,{'free':{'human_action':'replace'}},[],active,limit=2)
    assert {(x['quote_hash'],x['image_basename']) for x in result['items']}=={(EVEREST_QUOTE_HASH,EVEREST_IMAGE),FREE_TRADE_KEY}
def test_first_impression_mismatch_fixture():
    bad={**alignment(),'dominant_visual_message_alignment_score':20,'tone_alignment_score':15,'first_second_fit':'poor','editorial_risk':'high'}
    assert validate_alignment(bad)['first_second_fit']=='poor'
def test_pairwise_prompt_has_no_winner_or_human_leakage():
    intent=derive_quote_intent(quote(),legacy());a={'image_basename':'a',**image_result()};b={'image_basename':'b',**image_result()};p=pairwise_prompt(intent,a,b)
    assert 'production' in p and 'winner status' in p and 'human labels' in p and 'current_winner' not in p
def test_pairwise_schema():
    row={'preferred_candidate':'A','preference_strength':'strong','better_first_impression':'A','better_tone_match':'A','less_distracting':'A','better_semantic_fit':'B','overall_reason':'A is clearer.','would_replace_current_winner':True}
    assert validate_pairwise(row)['preferred_candidate']=='A'
def test_provider_payload_parity_uses_same_semantic_prompt():
    prompt='IDENTICAL';clients=[]
    for provider in ('grok','openai','anthropic','gemini'):
        base=object.__new__(ProviderClient);base.provider=provider;base.model='m';base.api_key='x';base.transport=None
        adapter=StructuredProviderAdapter(base,ALIGNMENT_OUTPUT_SCHEMA,'first_impression_alignment',1200);payload=adapter.payload(prompt);clients.append(json.dumps(payload,sort_keys=True))
    assert all('IDENTICAL' in payload for payload in clients) and all('human_label' not in payload for payload in clients)
def test_gemini_vertex_schema_compatibility():
    from semantic_alignment.vertex_recovery import GeminiVertexClient
    client=GeminiVertexClient(project='p',client=object(),response_schema=ALIGNMENT_OUTPUT_SCHEMA,max_output_tokens=1200)
    assert client.config().response_json_schema==ALIGNMENT_OUTPUT_SCHEMA and client.config().max_output_tokens==1200
def test_cost_preflight_within_ceilings():
    intent=derive_quote_intent(quote(),legacy());img={'schema_version':1,'analysis_kind':'image_first_impression','image_basename':'i','sha256':'a'*64,**image_result()};case={'quote_hash':quote()['quote_hash'],'image_basename':'i'}
    result=preflight([case]*50,{quote()['quote_hash']:intent},{'i':img},vision_calls=1)
    assert result['combined_expected_cost_usd']<14 and all(x['conservative_maximum_cost_usd']<=x['hard_ceiling_usd'] for x in result['providers'].values())
def test_strategy_is_observational():
    result=strategy_comparison([{'case_id':'c'}],{'c':alignment()},{'c':{'human_action':'keep'}})
    assert result['observational_only'] is True and result['strategies']['A']['human_agreement']==1
def test_no_external_call_without_execution_flag(tmp_path):
    from analyse_first_impression import main
    with pytest.raises(RuntimeError,match='--execute-vision'):main(['--project-dir',str(tmp_path),'--run-dir',str(tmp_path/'run'),'vision'])
