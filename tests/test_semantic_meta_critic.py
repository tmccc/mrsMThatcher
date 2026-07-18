from __future__ import annotations
import json
import pytest
from semantic_alignment.meta_critic import analyse_case,policy_summary,priority,validate_weights

P=('grok','openai','anthropic','gemini')
def row(action='replace',rel='unrelated',score=60,**kw):
    d={'keep_or_replace':action,'primary_relationship':rel,'secondary_relationship':None,'relevance_score':score,'directness_score':score,'overall_suitability_score':score,'consequence_alignment_score':score}; d.update(kw); return d
def case(actions,rels=None,scores=None):
    rels=rels or ['unrelated']*4; scores=scores or [60]*4
    return analyse_case({'case_id':'x'},{p:row(a,r,s) for p,a,r,s in zip(P,actions,rels,scores)})

@pytest.mark.parametrize('actions,expected,confidence,review',[
    (['keep']*4,'keep','high',False),(['replace']*4,'replace','high',False),
    (['keep']*3+['replace'],'keep','moderate',False),(['replace']*3+['keep'],'replace','moderate',False),
    (['keep']*2+['replace']*2,'unsure','low',True),(['keep']*2+['replace','unsure'],'unsure','low',True)])
def test_operational_rules(actions,expected,confidence,review):
    x=case(actions); assert (x['recommended_action'],x['confidence'],x['requires_human_review'])==(expected,confidence,review)

def test_complete_taxonomy_disagreement_and_outlier():
    x=case(['replace']*3+['keep'],['unrelated','contradictory','illustrates_mechanism','illustrates_claimed_consequence']); assert x['relationship_consensus']=='complete_disagreement' and x['single_provider_outliers'][0]['provider']=='gemini'

def test_score_inconsistency_and_dispersion():
    x=case(['replace']*4,scores=[10,20,90,95]); assert x['requires_human_review'] and 'wide_score_spread' in x['review_reasons']
    x=case(['keep']*4,scores=[10]*4); assert 'low_median_but_majority_keep' in x['review_reasons']

def test_consequence_ideology_split():
    x=case(['replace']*4,['illustrates_claimed_consequence']*2+['related_ideological_substitution']*2); assert x['relationship_consensus']=='two_two_split' and x['dominant_interpretation'] is None and x['secondary_interpretation']=='related_ideological_substitution'

def test_equal_and_validated_weights():
    w,source,n=validate_weights(None); assert set(w.values())=={1.0} and source=='equal_default' and n==0
    with pytest.raises(ValueError): validate_weights({'sample_size':19,'weights':{p:1 for p in P}})
    with pytest.raises(ValueError): validate_weights({'sample_size':20,'weights':{**{p:1 for p in P},'grok':2}})
    w,_,_=validate_weights({'sample_size':20,'weights':{'grok':1.2,'openai':1,'anthropic':.8,'gemini':1},'source':'human'}); assert w['grok']==1.2

def test_weighted_vote_and_policies():
    weights={'grok':1.5,'openai':1.5,'anthropic':.7,'gemini':.7}; rows={p:row(a) for p,a in zip(P,['keep','keep','replace','replace'])}; x=analyse_case({'case_id':'x'},rows,weights=weights); assert x['weighted_recommended_action']=='keep'
    items=[case(['keep']*4),case(['replace']*3+['keep']),case(['keep']*2+['replace']*2)]; [x.update(case_id=str(i)) for i,x in enumerate(items)]; policies=policy_summary(items,{'0':'keep','1':'replace'}); assert policies['A']['coverage']==pytest.approx(1/3) and policies['B']['coverage']==pytest.approx(2/3) and policies['B']['accuracy']==1

def test_priority_ordering():
    split=case(['keep']*2+['replace']*2); unanimous=case(['keep']*4); assert priority(split)[0]=='urgent' and priority(unanimous)[0]=='control'

def test_raw_fixture_not_mutated(tmp_path):
    rows={p:row() for p in P}; before=json.dumps(rows,sort_keys=True); analyse_case({'case_id':'x'},rows); assert json.dumps(rows,sort_keys=True)==before
    assert not any(tmp_path.iterdir())
