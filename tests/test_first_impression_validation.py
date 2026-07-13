from __future__ import annotations
import hashlib
from pathlib import Path
from semantic_alignment.first_impression_validation import *

ROOT=Path(__file__).resolve().parents[1]
RUN=ROOT/'semantic_alignment_research/first_impression/v1_20260712'
SEM=ROOT/'semantic_alignment_research/provider_bakeoff_250_20260712_v1_vertex_recovery/recovered_view'

def test_50_completed_reviews_and_integrity():
    value=integrity(RUN,ROOT);assert value['completed_reviews']==50 and value['image_hashes_valid']
def test_unsure_excluded_from_binary_denominator():
    m=binary_metrics({'a':'keep','b':'replace','c':'keep'},{'a':'keep','b':'replace','c':'unsure'});assert m['evaluated']==2 and m['agreement']==1
def test_binary_false_keep_and_false_replace():
    m=binary_metrics({'a':'keep','b':'replace'},{'a':'replace','b':'keep'});assert m['false_keep']==1 and m['false_replace']==1
def test_scale_inversion_detection():
    row={'dominant_visual_message_alignment_score':10,'tone_alignment_score':20,'salience_interference_score':12,'first_second_fit':'poor','quote_message_visually_dominant':False,'explanation':'Symbols consume visual attention and overwhelm the quote.'}
    assert consistency_issues('claude',{'c':row})[0]['issue_type']=='salience_scale_inversion'
def test_fit_score_contradiction_detection():
    row={'dominant_visual_message_alignment_score':80,'tone_alignment_score':80,'salience_interference_score':20,'first_second_fit':'poor','quote_message_visually_dominant':False,'explanation':'Poor.'}
    assert any(x['issue_type']=='fit_score_contradiction' for x in consistency_issues('x',{'c':row}))
def test_hard_threshold_metrics():
    m=binary_metrics({'a':'replace','b':'keep'},{'a':'replace','b':'keep'});assert m['replace_precision']==1 and m['keep_recall']==1
def test_soft_penalty_curves_are_predefined():
    assert penalty(95,CURVES['soft_light'])==0 and penalty(20,CURVES['soft_light'])==10
    assert penalty(20,CURVES['soft_strong'])==20
def test_false_replace_classification_prefers_semantic_indirectness():
    assert classify_false_replace({'image_basename':'x'},20,50,70,50,{'x':{}},False)=='acceptable indirectness'
def test_known_bad_classification_tone():
    assert classify_bad({'image_basename':'x'},20,20,60,{'x':{'visual_competition_score':10,'first_impression_message':'scene','visual_clutter_score':10}})=='tone mismatch'
def test_pairwise_no_labels_remain_missing():
    data=read_json(RUN/'pairwise_human_reviews.json',{'items':{}});assert len(data['items'])==0
def test_everest_regression_human_replace():
    reviews=read_json(RUN/'human_reviews.json')['items'];assert reviews['20d35a30b82bf94b96b7']['image_decision']=='replace'
def test_free_trade_regression_human_replace():
    reviews=read_json(RUN/'human_reviews.json')['items'];assert reviews['55f5cd6d633c143f5170']['matches_quote']=='no'
def test_actual_analysis_is_deterministic():
    a=analyse(RUN,ROOT,SEM);b=analyse(RUN,ROOT,SEM);assert a['hard_thresholds']==b['hard_thresholds'] and a['strategies']==b['strategies']
def test_strategy_ranking_has_no_pairwise_claim():
    result=analyse(RUN,ROOT,SEM);h=next(x for x in result['strategies'] if x['strategy']=='H');assert h['cases_evaluated']==0 and h['deferrals']==50
def test_raw_provider_outputs_preserved_by_analysis():
    paths=[RUN/f'{p}_first_impression_results.json' for p in ('grok','openai','anthropic')]+[RUN/'gemini_results.json'];before={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths};analyse(RUN,ROOT,SEM);after={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in paths};assert before==after
def test_wilson_interval_bounded():
    low,high=wilson(8,10);assert 0<=low<=.8<=high<=1
def test_csv_generation_deterministic():
    rows=[{'a':1,'b':2}];assert csv_text(rows)==csv_text(rows)
def test_no_external_api_surface_in_analysis_module():
    source=(ROOT/'semantic_alignment/first_impression_validation.py').read_text();assert 'requests.' not in source and 'ProviderClient' not in source
