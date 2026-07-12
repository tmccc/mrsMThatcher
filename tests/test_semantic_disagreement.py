from __future__ import annotations
import pytest
from semantic_alignment.disagreement import disagreement,evaluation,relationship_distance,safeguarded_policies

P=('grok','openai','anthropic','gemini')
def row(action='replace',primary='unrelated',secondary=None,score=40,rationale='shared'):
    d={'keep_or_replace':action,'primary_relationship':primary,'secondary_relationship':secondary,'quote_mechanism':[rationale],'quote_claimed_consequences':[rationale],'quote_broader_principles':[rationale],'image_depicted_consequences':[rationale],'image_ideological_framing':[rationale],'matched_elements':[rationale],'unillustrated_primary_elements':[],'extraneous_image_arguments':[],'explanation':'x'}
    for f in ('relevance_score','directness_score','mechanism_alignment_score','consequence_alignment_score','principle_alignment_score','specificity_score','editorial_power_score','overall_suitability_score'): d[f]=score
    return d
def analyse(rows): return disagreement('x',{p:r for p,r in zip(P,rows)})

def test_operational_components():
    assert analyse([row('keep')]*4)['components']['operational']==0
    assert analyse([row('keep')]*3+[row('replace')])['components']['operational']==25
    assert analyse([row('keep')]*2+[row('replace')]*2)['components']['operational']==80

def test_relationship_distance_and_primary_secondary_reversal():
    assert relationship_distance('illustrates_claimed_consequence','illustrates_broader_principle')<relationship_distance('direct_illustration','unrelated')
    reversed_rows=[row(primary='illustrates_claimed_consequence',secondary='related_ideological_substitution'),row(primary='related_ideological_substitution',secondary='illustrates_claimed_consequence')]*2
    plain=[row(primary='direct_illustration'),row(primary='unrelated'),row(primary='contradictory'),row(primary='ambiguous')]
    assert analyse(reversed_rows)['components']['taxonomy']<analyse(plain)['components']['taxonomy']

def test_score_dispersion_and_outlier():
    assert analyse([row(score=50)]*4)['components']['score_dispersion']==0
    one=analyse([row(score=10),row(score=10),row(score=10),row(score=90)]); assert one['score_outliers']
    broad=analyse([row(score=5),row(score=35),row(score=65),row(score=95)]); assert broad['components']['score_dispersion']>one['components']['score_dispersion']

def test_rationale_overlap_and_divergence():
    same=analyse([row(rationale='trade growth')]*4); different=analyse([row(rationale=x) for x in ('ports','tax cuts','war defence','union strike')]); assert same['components']['rationale']<different['components']['rationale']

def test_internal_inconsistency():
    bad=row(primary='unrelated',score=80); value=analyse([bad]+[row()]*3); assert value['internal_inconsistencies'] and value['components']['internal_inconsistency']>0

def test_bands_and_confidence_independence():
    low=analyse([row()]*4); extreme=analyse([row('keep','direct_illustration',score=90,rationale='ports'),row('replace','unrelated',score=5,rationale='war'),row('keep','contradictory',score=20,rationale='tax'),row('replace','ambiguous',score=70,rationale='union')]); assert low['disagreement_band']=='low' and extreme['disagreement_band'] in {'high','extreme'}
    assert 'confidence' not in low

def test_policies_fgh():
    meta={'operational_votes':{'replace':3,'keep':1}}
    low={'disagreement_band':'moderate','components':{'internal_inconsistency':0}}; high={'disagreement_band':'high','components':{'internal_inconsistency':75}}
    assert safeguarded_policies(meta,low)=={'F':'replace','G':'defer','H':'replace'}
    assert safeguarded_policies(meta,high)=={'F':'defer','G':'defer','H':'defer'}

def test_evaluation_and_review_efficiency():
    e=evaluation({'a':'keep','b':'replace','c':'defer'},{'a':'replace','b':'replace'}); assert e['coverage']==pytest.approx(2/3) and e['accuracy']==.5 and e['false_keep']==1 and e['review_efficiency']==.5

def test_free_trade_shape():
    rows=[row('replace','related_ideological_substitution',score=28),row('replace','illustrates_claimed_consequence',score=49),row('replace','illustrates_broader_principle',score=22),row('replace','illustrates_broader_principle',score=55)]; d=analyse(rows); assert d['components']['operational']==0 and d['disagreement_band'] in {'low','moderate','high'}
