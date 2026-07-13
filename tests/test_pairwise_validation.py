import json
from pathlib import Path

import pytest

from semantic_alignment.pairwise_validation import (
    EVEREST_QUOTE_HASH, FREE_TRADE_QUOTE_HASH, build_manifest, pair_id,
    calibration_metrics, pairwise_model_prompt, provider_selection, review_progress,
    assert_report_rendered, save_review, select_calibration, unresolved_template_tokens,
    validate_pairwise_judgement, validate_review,
)
from semantic_alignment.bakeoff import ProviderClient, anthropic_output_schema, openai_output_schema
from semantic_alignment.pairwise_correction import (build_independent_pilot,
    eligible_trace_runner_up,validate_pilot_readiness,validate_reconciliation,
    validate_rendered_report)


def fixture(tmp_path: Path):
    cases=[]; human={}; intents={}; images={}; editorial={}; identity={}; paths={}
    labels=["keep"]*16+["replace"]*28+["unsure"]*6
    for i in range(50):
        q = EVEREST_QUOTE_HASH if i == 0 else FREE_TRADE_QUOTE_HASH if i == 1 else f"{i:064x}"
        name=f"image{i%20}.png"; cid=f"source{i}"
        cases.append({"case_id":cid,"quote_hash":q,"image_basename":name})
        human[cid]={"image_decision":labels[i]}
        intents[q]={"quote_hash":q,"desired_first_impression":f"message {i}","desired_primary_visual_subjects":[f"subject{i%5}"],"desired_tone":["calm"]}
    for i in range(20):
        name=f"image{i}.png"; p=tmp_path/name; p.write_bytes(str(i).encode());paths[name]=p
        images[name]={"image_basename":name,"first_impression_message":f"message {i}","dominant_visual_subject":f"subject{i%5}","dominant_symbols":[],"primary_tone":"calm","secondary_tones":[]}
        editorial[name]={"analysis":{"quality":{"overall":70}}}
        identity[name]={"analysis":{"recommended_cross_quote_policy":"unrestricted"}}
    return cases,human,intents,images,editorial,identity,paths,[]


def test_manifest_is_deterministic_blinded_and_has_required_cases(tmp_path):
    args=fixture(tmp_path); a,ab=build_manifest(*args,seed=20260712); b,bb=build_manifest(*args,seed=20260712)
    assert a==b and ab==bb and len(a["items"])==50
    assert {EVEREST_QUOTE_HASH,FREE_TRADE_QUOTE_HASH}<={x["quote_hash"] for x in a["items"]}
    assert "current_winner" not in json.dumps(a)
    assert all(x["candidate_a"]!=x["candidate_b"] for x in a["items"])


def test_randomisation_changes_with_seed(tmp_path):
    args=fixture(tmp_path); _,a=build_manifest(*args,seed=1);_,b=build_manifest(*args,seed=2)
    assert [x["current_position"] for x in a["items"].values()] != [x["current_position"] for x in b["items"].values()]


def test_review_validation_and_atomic_revision(tmp_path):
    path=tmp_path/"reviews.json"; cid=pair_id("q","a","b")
    row={"case_id":cid,"preferred_candidate":"A","preference_strength":"strong","reasons":["better_semantic_fit"],"notes":"x"}
    save_review(path,{cid},row); save_review(path,{cid},{**row,"preferred_candidate":"B"})
    saved=json.loads(path.read_text())["items"][cid]
    assert saved["case_id"]==cid and saved["preferred_candidate"]=="B"
    assert review_progress([cid],{cid:saved})=={"total":1,"complete":1,"incomplete":0}
    with pytest.raises(ValueError): validate_review({**row,"preferred_candidate":"current_winner"})


def test_calibration_is_15_and_contains_regressions(tmp_path):
    manifest,blind=build_manifest(*fixture(tmp_path),seed=20260712)
    selected=select_calibration(manifest,blind)
    rows={x["case_id"]:x for x in manifest["items"]}
    assert len(selected["case_ids"])==15
    assert {EVEREST_QUOTE_HASH,FREE_TRADE_QUOTE_HASH}<={rows[c]["quote_hash"] for c in selected["case_ids"]}


def test_missing_or_origin_only_candidate_is_excluded(tmp_path):
    args=list(fixture(tmp_path)); identity=args[5]; identity["image1.png"]["analysis"]["recommended_cross_quote_policy"]="origin_quote_only"
    _,blind=build_manifest(*args,seed=20260712)
    assert all(x["challenger"]!="image1.png" for x in blind["items"].values())


def judgement(choice="A"):
    return {"preferred_candidate":choice,"preference_strength":"moderate","better_semantic_fit":"A",
        "better_first_impression":"A","better_tone_match":"equal","better_visual_impact":"B",
        "less_distracting":"A","would_publish_preferred":choice in {"A","B"},"reason":"A is clearer.","risk_flags":[]}


def test_pairwise_schema_is_strict():
    assert validate_pairwise_judgement(judgement())["preferred_candidate"]=="A"
    with pytest.raises(ValueError):validate_pairwise_judgement({**judgement(),"preferred_candidate":"winner"})
    with pytest.raises(ValueError):validate_pairwise_judgement({**judgement(),"extra":1})


def test_model_prompt_has_no_winner_human_or_provider_leakage():
    intent={"desired_first_impression":"freedom","desired_primary_visual_subjects":["flag"],"desired_tone":["calm"],"undesired_dominant_messages":[]}
    quote={"dominant_message":"freedom","core_claim":"freedom matters","claims":[],"primary_themes":[],"secondary_themes":[],"specific_concepts":[],"not_about":[]}
    first={"first_impression_message":"a flag","dominant_visual_subject":"flag","first_object_noticed":"flag","dominant_symbols":[],"visual_competition":[],"focal_clarity_score":80,"dominant_subject_strength":80,"distracting_symbol_score":0,"visual_competition_score":0,"primary_tone":"calm","secondary_tones":[]}
    semantic={"dominant_message":"freedom","core_implied_claim":"freedom","implied_claims":[],"primary_themes":[],"secondary_messages":[],"specific_concepts":[],"visual_evidence":[],"emotional_tone":[]}
    candidate={"first_impression":first,"semantic":semantic,"editorial":{"analysis":{"quality":{"overall":80}}}}
    prompt=pairwise_model_prompt(intent,quote,candidate,candidate)
    assert "human labels" in prompt and "current_winner" not in prompt and "production_winner" not in prompt
    assert "grok" not in prompt.lower() and "openai" not in prompt.lower()


def test_calibration_metrics_and_stop_gate():
    ids=[str(i) for i in range(15)];reviews={i:{"preferred_candidate":"A"} for i in ids};blind={i:{"prior_human_label":"replace"} for i in ids}
    results={"good":{"items":{i:judgement("A") for i in ids},"failures":{},"calls":[]},"bad":{"items":{i:judgement("B") for i in ids},"failures":{},"calls":[]}}
    metrics=calibration_metrics(results,reviews,blind,ids)
    assert metrics["providers"]["good"]["exact_agreement"]==1
    assert provider_selection(metrics)["providers"]==["good"]
    stopped=provider_selection(calibration_metrics({"bad":results["bad"]},reviews,blind,ids))
    assert stopped["decision"]=="do_not_proceed"


def test_unresolved_report_placeholder_detection():
    text="Agreement {metrics['providers']['grok']:.1%}; cost ${costs['grok']:.4f}; {exact}/50"
    assert len(unresolved_template_tokens(text))==3
    with pytest.raises(ValueError):assert_report_rendered(text)
    assert_report_rendered("Agreement 60.0%; cost $0.4445; 33/50")


def test_provider_schema_serialisers_preserve_logical_fields():
    openai=openai_output_schema(__import__('semantic_alignment.pairwise_validation',fromlist=['PAIRWISE_SCHEMA']).PAIRWISE_SCHEMA)
    anthropic=anthropic_output_schema(__import__('semantic_alignment.pairwise_validation',fromlist=['PAIRWISE_SCHEMA']).PAIRWISE_SCHEMA)
    assert "uniqueItems" not in json.dumps(openai) and "uniqueItems" not in json.dumps(anthropic)
    assert set(openai['properties'])==set(anthropic['properties'])
    assert openai['required']==anthropic['required']


def test_openai_and_anthropic_payloads_use_compatible_schema():
    schema=__import__('semantic_alignment.pairwise_validation',fromlist=['PAIRWISE_SCHEMA']).PAIRWISE_SCHEMA
    openai=ProviderClient('openai','test',transport=lambda *a,**k:None).payload('same',schema=schema)
    anthropic=ProviderClient('anthropic','test',transport=lambda *a,**k:None).payload('same',schema=schema)
    assert "uniqueItems" not in json.dumps(openai)
    assert "uniqueItems" not in json.dumps(anthropic)
    assert openai['input']=='same' and anthropic['messages'][0]['content']=='same'


def test_reconciliation_and_rendered_report_validation():
    validate_reconciliation([{'computed_value':'60.0%','rendered_value':'60.0%','match':True}])
    with pytest.raises(ValueError):validate_reconciliation([{'computed_value':'60%','rendered_value':'{value}','match':False}])
    validate_rendered_report('Grok agreement: 60.0%; cost $0.4445.')


def test_trace_runner_up_enforces_identity_duplicate_and_quality_floors(tmp_path):
    trace={'trace_file':'trace','post_index':1,'candidate_detail':[{'basename':'current.png','source':'generated','identity_shadow_score':100,'production_score':100,'components':{'topics':90}},{'basename':'good.png','source':'generated','identity_shadow_score':80,'production_score':80,'components':{'topics':50}},{'basename':'lower.png','source':'generated','identity_shadow_score':70,'production_score':70,'components':{'topics':40}}]}
    editorial={'good.png':{'analysis':{'quality':{'overall':80}}},'lower.png':{'analysis':{'quality':{'overall':80}}}}
    row,_=eligible_trace_runner_up(trace,'current.png',active={'good.png','lower.png'},first_impressions={'good.png','lower.png'},editorial=editorial,hashes={'current.png':'a','good.png':'c','lower.png':'d'})
    assert row['image_basename']=='good.png'
    duplicate,_=eligible_trace_runner_up(trace,'current.png',active={'good.png'},first_impressions={'good.png'},editorial=editorial,hashes={'current.png':'c','good.png':'c'})
    assert duplicate is None


def test_failed_true_runner_up_is_not_replaced_by_lower_rank():
    trace={'trace_file':'trace','candidate_detail':[{'basename':'current','source':'generated','identity_shadow_score':10,'production_score':90,'components':{'topics':80}},{'basename':'runner','source':'generated','identity_shadow_score':9,'production_score':80,'components':{'topics':0}},{'basename':'lower','source':'generated','identity_shadow_score':8,'production_score':70,'components':{'topics':60}}]}
    editorial={x:{'analysis':{'quality':{'overall':90}}} for x in ('runner','lower')}
    row,reason=eligible_trace_runner_up(trace,'current',active={'runner','lower'},first_impressions={'runner','lower'},editorial=editorial,hashes={'current':'a','runner':'b','lower':'c'})
    assert row is None and 'topic-score floor' in reason


def test_independent_pilot_excludes_original_pairs():
    trace={'trace_file':'trace','post_index':1,'quote_hash':'q','production_image':'current.png','candidate_detail':[{'basename':'current.png','source':'generated','identity_shadow_score':90,'production_score':90,'components':{'topics':70}},{'basename':'new.png','source':'generated','identity_shadow_score':70,'production_score':70,'components':{'topics':40}}]}
    editorial={'new.png':{'analysis':{'quality':{'overall':80}}}}
    pilot=build_independent_pilot([trace],{('q','current.png','old.png')},active={'current.png','new.png'},first_impressions={'current.png','new.png'},editorial=editorial,hashes={'current.png':'a','new.png':'b'},quote_intents={'q'})
    assert pilot['case_count']==1 and pilot['items'][0]['candidate_b'] in {'current.png','new.png'}


def test_readiness_requires_twenty_credible_pairs_and_allowed_provenance():
    items=[{'case_id':str(i),'candidate_a':'a','candidate_b':'b','provenance':'exact_simulator_runner_up'} for i in range(20)]
    blind={str(i):{'eligibility_verified':True} for i in range(20)}
    assert validate_pilot_readiness(items,blind)['ready']
    assert not validate_pilot_readiness(items[:19],blind)['ready']
    items[0]['provenance']='lexical_overlap'
    assert not validate_pilot_readiness(items,blind)['ready']
