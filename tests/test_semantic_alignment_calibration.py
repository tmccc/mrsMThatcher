from __future__ import annotations

import json
import threading
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from calibrate_semantic_alignment import require_labels
from semantic_alignment.calibration import (BANDS, RELATIONSHIPS,
    build_review_dataset, decompose_image, decompose_quote, select_review_cases,
    labelled_review_statistics, merge_review_data, migrate_review_dataset,
    review_is_complete, review_progress, spearman, threshold_analysis,
    validate_calibration_critic, validate_human_label)
from semantic_alignment.calibration_prompt import calibration_critic_prompt
from semantic_alignment.io import atomic_write_json
from tools.semantic_alignment_calibration_app import create_server

Q="a"*64; I="tg_"+"b"*64+".png"


def quote(): return {"quote_hash":Q,"quote_text":"Free trade creates competition, growth and consumer benefit.","core_claim":"Free trade creates growth and consumer benefit.","claims":[{"claim":"Free trade creates competition.","importance":"primary","confidence":.9},{"claim":"Competition creates growth and consumer benefit.","importance":"primary","confidence":.9}],"primary_themes":["free trade","economic freedom"],"secondary_themes":["open markets"],"specific_concepts":["free trade","competition","economic growth","consumer benefit"],"dominant_message":"Trade benefits consumers."}
def image(): return {"image_basename":I,"core_implied_claim":"Prosperity follows reform.","implied_claims":[{"claim":"Reform produces prosperity.","salience":"dominant","confidence":.9,"visual_support":["modern skyline"]}],"primary_themes":["prosperity","capitalism"],"secondary_messages":["economic freedom"],"visual_evidence":["modern skyline"],"dominant_message":"Prosperity."}
def critic(n=0,rel="unrelated"): return {"quote_hash":f"{n:064x}","image_basename":f"tg_{n:064x}.png","claim_relationship":rel,"semantic_alignment_score":n%100,"directness_score":10,"editorial_power_score":80}


def test_local_decomposition_separates_mechanism_consequence_principle():
    q=decompose_quote(quote()); i=decompose_image(image())
    assert q["core_mechanism"] and q["claimed_consequences"] and q["broader_principles"]
    assert i["depicted_consequence"] and i["broader_ideological_framing"]
    assert q["provenance"].startswith("deterministic")


def test_selection_is_deterministic_stratified_and_free_trade_first():
    rows=[critic(n,RELATIONSHIPS[n%len(RELATIONSHIPS)]) for n in range(150)]
    free=(rows[99]["quote_hash"],rows[99]["image_basename"])
    first=select_review_cases(rows,limit=100,free_trade_key=free)
    second=select_review_cases(list(reversed(rows)),limit=100,free_trade_key=free)
    assert [(x['quote_hash'],x['image_basename']) for x in first] == [(x['quote_hash'],x['image_basename']) for x in second]
    assert (first[0]['quote_hash'],first[0]['image_basename']) == free
    assert set(RELATIONSHIPS) <= {x['claim_relationship'] for x in first}


def test_review_dataset_has_fixed_splits_and_empty_human_labels():
    rows=[]; qs={}; ims={}
    for n in range(100):
        row=critic(n,RELATIONSHIPS[n%len(RELATIONSHIPS)]); rows.append(row)
        q=quote(); q['quote_hash']=row['quote_hash']; qs[row['quote_hash']]=q
        im=image(); im['image_basename']=row['image_basename']; ims[row['image_basename']]=im
    data=build_review_dataset(rows,qs,ims)
    assert [sum(x['split']==s for x in data['items']) for s in ('calibration','holdout','reserve')] == [25,25,50]
    assert all(x['human_label'] is None for x in data['items'])


def test_human_label_and_calibration_result_validation():
    label={"primary_relationship":"illustrates_claimed_consequence","secondary_relationship":None,"relevance":"high","directness":"moderate","appropriateness_rating":3,"publish_likelihood_rating":2,"image_decision":"prefer_different_generated_image","notes":"Outcome is visible."}
    assert validate_human_label(label)==label
    result={"primary_relationship":"illustrates_claimed_consequence","secondary_relationship":"none",**{x:70 for x in ("relevance","directness","mechanism_alignment","consequence_alignment","principle_alignment","specificity","editorial_power","overall_suitability")},"mechanism_matches":[],"consequence_matches":["prosperity"],"principle_matches":[],"extraneous_arguments":[],"missing_primary_claims":["trade"],"explanation":"The consequence is visible.","preferred_visual_direction":[],"confidence":.9}
    assert validate_calibration_critic(result)["consequence_alignment"]==70


def test_prompt_is_claimed_consequence_aware_and_excludes_human_label():
    rendered=calibration_critic_prompt(quote(),image(),decompose_quote(quote()),decompose_image(image()))
    assert "need not depict the causal" in rendered and "illustrates_claimed_consequence" in rendered
    assert "acceptable_for_posting" not in rendered and "human_label" not in rendered


def test_paid_split_refuses_unlabelled_cases():
    data={"items":[{"case_id":str(n),"split":"calibration","human_label":None} for n in range(25)]}
    with pytest.raises(RuntimeError,match="no paid execution"): require_labels(data,"calibration")


@pytest.mark.parametrize("rating",[1,2,3,4,5])
def test_valid_ratings(rating):
    label={"primary_relationship":"direct_illustration","secondary_relationship":None,"relevance":"high","directness":"high","appropriateness_rating":rating,"publish_likelihood_rating":rating,"image_decision":"keep_current_image","notes":""}
    assert validate_human_label(label)["appropriateness_rating"]==rating


@pytest.mark.parametrize("rating",[0,6,True,False,1.0,2.5,None,"3"])
def test_invalid_ratings(rating):
    label={"primary_relationship":"direct_illustration","secondary_relationship":None,"relevance":"high","directness":"high","appropriateness_rating":rating,"publish_likelihood_rating":3,"image_decision":"keep_current_image","notes":""}
    with pytest.raises(ValueError,match="appropriateness_rating"): validate_human_label(label)


def test_legacy_label_migrates_incomplete_without_inventing_ratings():
    label={"primary_relationship":"direct_illustration","secondary_relationship":None,"relevance":"high","directness":"high","notes":"preserve me"}
    data=migrate_review_dataset({"schema_version":1,"items":[{"case_id":"x","human_label":label}]})
    assert data["schema_version"]==3 and not data["items"][0]["review_complete"]
    assert data["items"][0]["human_label"]["notes"]=="preserve me" and "appropriateness_rating" not in data["items"][0]["human_label"]


def test_schema_v2_label_without_decision_remains_incomplete():
    label={"primary_relationship":"direct_illustration","secondary_relationship":None,"relevance":"high","directness":"high","appropriateness_rating":4,"publish_likelihood_rating":4,"notes":"ratings retained"}
    data=migrate_review_dataset({"schema_version":2,"items":[{"case_id":"x","human_label":label}]})
    assert data["schema_version"]==3 and not data["items"][0]["review_complete"]
    assert data["items"][0]["human_label"]==label and "image_decision" not in label


def test_merge_preserves_identity_and_completed_revision():
    label={"primary_relationship":"direct_illustration","secondary_relationship":None,"relevance":"high","directness":"high","appropriateness_rating":4,"publish_likelihood_rating":4,"image_decision":"keep_current_image","notes":"old"}
    fresh={"schema_version":2,"items":[{"case_id":"stable","human_label":None}]}; existing={"schema_version":1,"items":[{"case_id":"stable","human_label":label}]}
    merged=merge_review_data(fresh,existing)
    assert merged["items"][0]["case_id"]=="stable" and merged["items"][0]["human_label"]==label and review_is_complete(merged["items"][0])


def test_progress_statistics_correlations_and_thresholds():
    items=[]; model={"items":{}}
    for n in range(20):
        rating=1 if n<5 else 2 if n<10 else 4 if n<15 else 5
        decision="prefer_different_generated_image" if n<10 else "keep_current_image"
        label={"primary_relationship":"direct_illustration" if n>=10 else "unrelated","secondary_relationship":None,"relevance":"high","directness":"high","appropriateness_rating":rating,"publish_likelihood_rating":rating,"image_decision":decision,"notes":""}
        items.append({"case_id":str(n),"old_alignment":n*5,"human_label":label})
        model["items"][str(n)]={"relevance":n*5,"directness":n*5,"overall_suitability":n*5}
    data={"items":items}; stats=labelled_review_statistics(data,model); thresholds=threshold_analysis(data,model)
    assert review_progress(data)=={"total":20,"complete":20,"incomplete":0}
    assert stats["appropriateness_distribution"]=={1:5,2:5,4:5,5:5}
    assert stats["image_decision_distribution"]["keep_current_image"]["count"]==10
    assert stats["correlations"]["critic_overall_suitability_vs_publish_likelihood"]>0.9
    assert thresholds["available"] and thresholds["recommended_threshold"]["false_positive_rate"]==0
    assert spearman([1,2,3],[1,2,3])==pytest.approx(1)


def test_threshold_analysis_requires_sufficient_labels():
    assert not threshold_analysis({"items":[]},{"items":{}})["available"]


@pytest.mark.parametrize("decision",["keep_current_image","prefer_different_generated_image","unsure"])
def test_valid_image_decisions(decision):
    label={"primary_relationship":"direct_illustration","secondary_relationship":None,"relevance":"high","directness":"high","appropriateness_rating":4,"publish_likelihood_rating":4,"image_decision":decision,"notes":""}
    assert validate_human_label(label)["image_decision"]==decision


@pytest.mark.parametrize("decision",["", "different", True, False, 1, 1.0, None, [], {}])
def test_invalid_image_decisions(decision):
    label={"primary_relationship":"direct_illustration","secondary_relationship":None,"relevance":"high","directness":"high","appropriateness_rating":4,"publish_likelihood_rating":4,"image_decision":decision,"notes":""}
    with pytest.raises(ValueError,match="image_decision"): validate_human_label(label)


def test_decision_reporting_anomalies_and_threshold_excludes_unsure():
    items=[]; model={"items":{}}
    decisions=["prefer_different_generated_image"]*8+["keep_current_image"]*8+["unsure"]*4
    for n,decision in enumerate(decisions):
        label={"primary_relationship":"direct_illustration","secondary_relationship":None,"relevance":"high","directness":"high","appropriateness_rating":4 if n==0 else 3,"publish_likelihood_rating":2 if n==8 else 4,"image_decision":decision,"notes":""}
        items.append({"case_id":str(n),"old_alignment":n*5,"human_label":label}); model["items"][str(n)]={"relevance":n*5,"directness":n*5,"overall_suitability":90 if n==0 else 10 if n==8 else n*5}
    data={"items":items}; stats=labelled_review_statistics(data,model); result=threshold_analysis(data,model,target="image_decision",minimum_cases=10)
    assert stats["high_critic_prefer_different"]==["0"] and stats["low_critic_keep_current"]==["8"]
    assert stats["high_appropriateness_prefer_different"]==["0"] and stats["low_publish_keep_current"]==["8"]
    assert len(stats["unsure_cases"])==4 and result["available"] and result["excluded_indeterminate"]==4
    assert "specificity" in result["candidate_thresholds"][0]


def test_review_app_shows_case_and_saves_label(tmp_path):
    project=tmp_path/'project'; images=project/'generated_review_approved_images'; images.mkdir(parents=True); (images/I).write_bytes(b'png')
    run=tmp_path/'run'; run.mkdir(); q=quote(); im=image(); old={"explanation":"Old severe verdict"}
    atomic_write_json(run/'quote_semantic_fingerprints.json',{"items":{Q:q}}); atomic_write_json(run/'image_implied_messages_generated.json',{"items":{I:im}}); atomic_write_json(run/'semantic_alignment_critic.json',{"items":{f'{Q}:{I}':old}})
    case={"case_id":"c1","split":"calibration","quote_hash":Q,"image_basename":I,"quote_decomposition":decompose_quote(q),"image_decomposition":decompose_image(im),"human_label":None}
    dataset=run/'human_review_cases.json'; atomic_write_json(dataset,{"items":[case]})
    server=create_server(project_dir=project,dataset_path=dataset,host='127.0.0.1',port=0); thread=threading.Thread(target=server.serve_forever); thread.start()
    try:
        base=f'http://127.0.0.1:{server.server_port}'; page=urllib.request.urlopen(base+'/case/c1').read().decode(); assert 'Old severe verdict' in page
        assert 'How appropriate is this image' in page and 'publish_likelihood_rating' in page and 'If this were today’s scheduled post' in page and 'Incomplete' in page and 'Previous' in page
        csrf=page.split("name=csrf value='")[1].split("'")[0]
        body=urllib.parse.urlencode({"csrf":csrf,"primary_relationship":"illustrates_claimed_consequence","secondary_relationship":"","relevance":"high","directness":"moderate","appropriateness_rating":"3","publish_likelihood_rating":"2","image_decision":"prefer_different_generated_image","notes":"Good"}).encode()
        urllib.request.urlopen(urllib.request.Request(base+'/case/c1',data=body)).read()
        saved=json.loads(dataset.read_text())['items'][0]
        assert saved['human_label']['appropriateness_rating']==3 and saved['human_label']['image_decision']=='prefer_different_generated_image' and saved['review_complete'] is True and saved['case_id']=='c1'
        reopened=urllib.request.urlopen(base+'/case/c1').read().decode()
        assert 'Complete' in reopened and 'name=appropriateness_rating value=3 checked' in reopened and "value='prefer_different_generated_image' checked" in reopened
    finally: server.shutdown(); thread.join(); server.server_close()
