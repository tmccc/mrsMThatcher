import json
from pathlib import Path

import pytest

from semantic_alignment.visualisability_audit import (
    ACTIONS, GRADES, HUMAN_DECISIONS, SCORE_FIELDS, TREATMENTS, _dedupe_best,
    _filter_sort_records, _page_css, calibrate, human_review_summary,
    load_calibration, rubric, run_audit, save_human_review, score_packet,
)
from semantic_alignment.openai_quality_trial import load_corpus
from tests.helpers.historical_corpus import (
    RESEARCH_RELATIVE,
    historical_corpus_root,
)

ROOT=Path(__file__).resolve().parents[1]/'semantic_alignment_research'
REVIEWS=[ROOT/'openai_quality_trial_001',ROOT/'openai_quality_trial_002']


@pytest.fixture
def historical_research(historical_corpus_root, monkeypatch):
    # The original quality trial pins 627 packets. Its visualisability audit
    # also reads quote_analysis.json from cwd, so use the matching metadata.
    monkeypatch.chdir(historical_corpus_root)
    return historical_corpus_root / RESEARCH_RELATIVE


def test_original_627_schema_identity_and_unresolved_exclusion(historical_research):
    packets,unresolved,digest=load_corpus(historical_research)
    assert len(packets)==627 and len(unresolved)==5 and not(set(packets)&unresolved) and len(digest)==64

def test_calibration_has_40_and_18_neither(historical_research):
    packets,_,_=load_corpus(historical_research);qa=json.loads(Path('quote_analysis.json').read_text())['items']
    result=load_calibration(REVIEWS,packets,qa)
    assert result['case_count']==40 and result['neither_count']==18
    assert rubric(result)['calibration_summary']['neither_rate']==.45

def test_deterministic_scores_bounds_and_consistency(historical_research):
    packets,_,_=load_corpus(historical_research);qa=json.loads(Path('quote_analysis.json').read_text())['items'];cal=load_calibration(REVIEWS,packets,qa)
    qid=next(iter(sorted(packets)));a=score_packet(qid,packets[qid],qa.get(qid),cal);b=score_packet(qid,packets[qid],qa.get(qid),cal)
    assert a==b and a['grade'] in GRADES and a['recommended_action'] in ACTIONS and a['preferred_treatment'] in TREATMENTS
    assert all(1<=a['scores'][x]<=5 for x in SCORE_FIELDS)
    if a['grade']=='D':assert a['recommended_action']=='skip'
    if a['grade']=='A':assert not a['visible_text_required']
    if a['preferred_treatment']=='portrait_or_likeness':assert a['scores']['portrait_dependency']>=3

def test_best_50_rejects_near_duplicates():
    base={'generation_priority_score':90,'category':'leadership','grade':'A'}
    rows=[{**base,'quote_id':'a','quote_text':'This is a distinctive quotation about national leadership'},
          {**base,'quote_id':'b','quote_text':'This is a distinctive quotation about national leadership indeed'},
          {**base,'quote_id':'c','quote_text':'A completely different scene concerning liberty and law'}]
    selected=_dedupe_best(rows,2)
    assert len(selected)==2 and not {'a','b'}<=set(x['quote_id'] for x in selected)

def test_full_audit_is_deterministic_resume_and_offline(tmp_path,monkeypatch,historical_research):
    def blocked(*args,**kwargs):raise AssertionError('network forbidden')
    monkeypatch.setattr('socket.create_connection',blocked)
    out=tmp_path/'audit';calibrate(historical_research,REVIEWS,out);first=run_audit(historical_research,out,REVIEWS);second=run_audit(historical_research,out,REVIEWS)
    assert first==second and len(first)==627
    assert json.loads((out/'best_50_generation_candidates.json').read_text())['count']==50
    assert not (tmp_path/'mrsMThatcher2.py').exists()

def test_manual_interface_files_are_separate(tmp_path,historical_research):
    out=tmp_path/'audit';calibrate(historical_research,REVIEWS,out);run_audit(historical_research,out,REVIEWS)
    assert json.loads((out/'manual_review_interface/overrides.json').read_text())['items']=={}
    assert (out/'manual_review_interface/audit_trail.jsonl').read_text()==''
    assert json.loads((out/'manual_review_queue.json').read_text())['count']>=0


def _review_fixture(tmp_path,research):
    out=tmp_path/'audit';calibrate(research,REVIEWS,out);run_audit(research,out,REVIEWS)
    return out,json.loads((out/'quote_visualisability.json').read_text())['items']


def test_four_button_mapping_and_automated_data_immutable(tmp_path,historical_research):
    out,base=_review_fixture(tmp_path,historical_research);qid=next(iter(base));before=(out/'quote_visualisability.json').read_bytes()
    for grade,(label,action) in HUMAN_DECISIONS.items():
        record,_=save_human_review(out,qid,grade)
        assert (record['human_decision'],record['human_action'])==(label,action)
    assert (out/'quote_visualisability.json').read_bytes()==before
    assert 'human_grade' not in base[qid]


def test_autosave_store_idempotence_revision_audit_and_restart(tmp_path,historical_research):
    out,base=_review_fixture(tmp_path,historical_research);qid=next(iter(base))
    first,changed=save_human_review(out,qid,'A','note');assert changed and first['review_revision']==1
    again,changed=save_human_review(out,qid,'A','note');assert not changed and again['review_revision']==1
    revised,changed=save_human_review(out,qid,'B','revised');assert changed and revised['review_revision']==2
    lines=(out/'manual_review_interface/human_review_audit.jsonl').read_text().splitlines()
    assert len(lines)==2 and json.loads(lines[-1])['previous_value']['human_grade']=='A'
    assert (out/'manual_review_interface/human_reviews.json.backup').exists()
    assert human_review_summary(out)['reviewed_count']==1


def test_filters_sorts_and_next_unreviewed_inputs(tmp_path,historical_research):
    out,base=_review_fixture(tmp_path,historical_research);ids=list(base);save_human_review(out,ids[0],'A')
    reviews=json.loads((out/'manual_review_interface/human_reviews.json').read_text())['items']
    assert len(_filter_sort_records(base,reviews,'reviewed'))==1
    assert len(_filter_sort_records(base,reviews,'unreviewed'))==626
    assert all(x['grade']=='A' for x in _filter_sort_records(base,reviews,'auto_a'))
    assert _filter_sort_records(base,reviews,'review_state')[0]['quote_id'] not in reviews
    for sort in ('priority','quote_order','confidence','risk','review_state'):
        assert len(_filter_sort_records(base,reviews,'all',sort))==627


def test_responsive_styles_and_disabled_generation_copy():
    css=_page_css()
    source=Path('semantic_alignment/visualisability_audit.py').read_text()
    assert '@media(max-width:760px)' in css
    assert 'Generate test image' in source and 'disabled title=' in source
    assert "if('1234'.includes(e.key))" in source and "e.key==='Enter'" in source
