from __future__ import annotations

import json
import threading
import urllib.parse
import urllib.request

import pytest

from semantic_alignment.bakeoff import (FREE_TRADE_KEY,
    ProviderClient, ProviderLedger, cohens_kappa, common_prompt,
    compare_n_results, compare_results, confusion, create_blinding,
    create_four_way_blinding, create_three_way_blinding, preflight, run_provider, select_cases,
    validate_bakeoff_result)
from semantic_alignment.io import atomic_write_json
from tools.semantic_alignment_bakeoff_review import create_server


def quote(qhash): return {"schema_version":2,"quote_hash":qhash,"quote_text":"Free trade creates competition and prosperity.","core_claim":"Trade creates prosperity.","claims":[{"claim":"Trade creates prosperity.","importance":"primary","confidence":.9}],"primary_themes":["free trade"],"secondary_themes":["economic freedom"],"specific_concepts":["free trade","competition","prosperity"],"dominant_message":"Trade benefits people.","desired_visual_evidence":[],"explicit_contrasts":[],"not_about":[],"confidence":.9,"analysis_kind":"quote_semantic_fingerprint","primary_issue":"trade"}
def image(name): return {"schema_version":2,"image_basename":name,"sha256":"b"*64,"core_implied_claim":"Prosperity follows reform.","implied_claims":[{"claim":"Reform produces prosperity.","salience":"dominant","confidence":.9,"visual_support":["skyline"]}],"primary_themes":["prosperity"],"secondary_messages":["economic freedom"],"specific_concepts":["prosperity"],"visual_evidence":["modern skyline"],"dominant_message":"Prosperity.","emotional_tone":[],"ambiguity":"medium","confidence":.9,"analysis_kind":"image_implied_message","primary_issue":"prosperity"}
def old(qhash,name,n,rel): return {"quote_hash":qhash,"image_basename":name,"claim_relationship":rel,"semantic_alignment_score":n%100,"directness_score":n%90,"editorial_power_score":80}
def result(rel="illustrates_claimed_consequence",decision="keep",score=70):
    value={"primary_relationship":rel,"secondary_relationship":None,**{x:score for x in ("relevance_score","directness_score","mechanism_alignment_score","consequence_alignment_score","principle_alignment_score","specificity_score","editorial_power_score","overall_suitability_score")},"keep_or_replace":decision,"quote_mechanism":["trade"],"quote_claimed_consequences":["prosperity"],"quote_broader_principles":["freedom"],"image_depicted_subject":["skyline"],"image_implied_mechanism":["reform"],"image_depicted_consequences":["prosperity"],"image_ideological_framing":[],"matched_elements":["prosperity"],"unillustrated_primary_elements":["trade"],"extraneous_image_arguments":[],"explanation":"The image depicts the claimed prosperity but not the trade mechanism.","stronger_visual_direction":["port"]}
    return value


def inventories():
    quotes={}; images={}; rows=[]; relationships=("unrelated","related_but_not_equivalent","generic_ideological_substitution","contradiction","strong_support")
    for n in range(150):
        qhash=f"{n:064x}"; name=f"tg_{n:064x}.png"; quotes[qhash]=quote(qhash); images[name]=image(name); rows.append(old(qhash,name,n,relationships[n%len(relationships)]))
    fq,fi=FREE_TRADE_KEY; quotes[fq]=quote(fq); images[fi]=image(fi); rows.append(old(fq,fi,1,"unrelated"))
    return quotes,images,rows


def test_deterministic_selection_and_free_trade():
    q,i,rows=inventories(); first=select_cases(rows,q,i); second=select_cases(list(reversed(rows)),q,i)
    assert first==second and len(first["items"])==25
    assert any((x["quote_hash"],x["image_basename"])==FREE_TRADE_KEY for x in first["items"])
    assert len({x["case_id"] for x in first["items"]})==25


def test_common_prompt_parity_and_isolation():
    q=quote("a"*64); i=image("tg.png"); q["human_label"]="SECRET"; i["production_winner"]="SECRET2"
    prompt=common_prompt(q,i); grok=ProviderClient("grok","fake",transport=lambda *a,**k:None).payload(prompt); openai=ProviderClient("openai","fake",transport=lambda *a,**k:None).payload(prompt); anthropic=ProviderClient("anthropic","fake",transport=lambda *a,**k:None).payload(prompt); gemini=ProviderClient("gemini","fake",transport=lambda *a,**k:None).payload(prompt)
    assert grok["messages"][0]["content"]==openai["input"]==anthropic["messages"][0]["content"]==gemini["contents"][0]["parts"][0]["text"]
    assert "another critic's judgement" in prompt and all("tools" not in x for x in (grok,openai,anthropic,gemini))
    # Input fingerprints are passed exactly; forbidden surrounding review/selection records are never arguments.
    assert "SECRET" not in prompt and "existing Grok critic" not in prompt and "expected answer" in prompt


def test_schema_and_provider_parsing_and_costs():
    class Response:
        headers={"x-request-id":"req"}
        def __init__(self,provider): self.provider=provider
        def raise_for_status(self): pass
        def json(self):
            if self.provider=="grok": return {"model":"grok-4.5","usage":{"prompt_tokens":100,"completion_tokens":50,"cost_in_usd_ticks":5_000_000},"choices":[{"message":{"content":json.dumps(result())}}]}
            if self.provider=="openai": return {"id":"resp","usage":{"input_tokens":100,"output_tokens":50,"input_tokens_details":{"cached_tokens":20},"output_tokens_details":{"reasoning_tokens":10}},"output":[{"content":[{"type":"output_text","text":json.dumps(result())}]}]}
            if self.provider=="anthropic": return {"id":"msg","usage":{"input_tokens":100,"output_tokens":50,"cache_read_input_tokens":20},"content":[{"type":"text","text":json.dumps(result())}]}
            return {"usageMetadata":{"promptTokenCount":100,"candidatesTokenCount":40,"thoughtsTokenCount":10,"cachedContentTokenCount":20},"candidates":[{"content":{"parts":[{"text":json.dumps(result())}]}}]}
    g=ProviderClient("grok","fake",transport=lambda *a,**k:Response("grok")).call("prompt"); o=ProviderClient("openai","fake",transport=lambda *a,**k:Response("openai")).call("prompt"); a=ProviderClient("anthropic","fake",transport=lambda *x,**k:Response("anthropic")).call("prompt"); m=ProviderClient("gemini","fake",transport=lambda *x,**k:Response("gemini")).call("prompt")
    assert validate_bakeoff_result(g["content"])["keep_or_replace"]=="keep"
    assert validate_bakeoff_result(o["content"])["primary_relationship"]=="illustrates_claimed_consequence"
    assert g["cost_usd"]==.0005 and o["cost_usd"]==pytest.approx((80*5+20*.5+50*30)/1e6)
    assert a["cost_usd"]==pytest.approx((80*3+20*.3+50*15)/1e6)
    assert m["cost_usd"]==pytest.approx((80*2+20*.2+50*12)/1e6) and m["usage"]["reasoning_tokens"]==10


def test_preflight_under_limits_uses_rendered_prompts():
    q,i,rows=inventories(); cases=select_cases(rows,q,i)["items"]; estimate=preflight(cases,q,i)
    assert estimate["providers"]["grok"]["calls"]==25 and estimate["providers"]["openai"]["calls"]==25
    assert estimate["combined_conservative_maximum_cost_usd"]<3


def test_provider_resume_and_failure_isolation(tmp_path):
    qhash="a"*64; name="tg.png"; cases=[{"case_id":"c","quote_hash":qhash,"image_basename":name}]; q={qhash:quote(qhash)}; i={name:image(name)}
    class Response:
        headers={}
        def raise_for_status(self): pass
        def json(self): return {"usage":{"prompt_tokens":10,"completion_tokens":10,"cost_in_usd_ticks":1000},"choices":[{"message":{"content":json.dumps(result())}}]}
    client=ProviderClient("grok","fake",transport=lambda *a,**k:Response()); run_provider("grok",cases,q,i,tmp_path,client,.75); run_provider("grok",cases,q,i,tmp_path,client,.75)
    assert len(json.loads((tmp_path/'grok_cost_ledger.json').read_text())['calls'])==1
    bad=ProviderClient("openai","fake",transport=lambda *a,**k:(_ for _ in ()).throw(TimeoutError("ambiguous")))
    with pytest.raises(TimeoutError): run_provider("openai",cases,q,i,tmp_path,bad,1.75)
    assert json.loads((tmp_path/'openai_cost_ledger.json').read_text())['blocked'] is True
    assert len(json.loads((tmp_path/'grok_results.json').read_text())['items'])==1


def test_blinded_mapping_stable_and_private(tmp_path):
    first=create_blinding(tmp_path); second=create_blinding(tmp_path)
    assert first==second and set(first.values())=={"grok","openai"}
    assert (tmp_path/'sealed_provider_mapping.json').stat().st_mode & 0o777 == 0o600
    three=create_three_way_blinding(tmp_path); assert set(three.values())=={"grok","openai","anthropic"} and create_three_way_blinding(tmp_path)==three
    four=create_four_way_blinding(tmp_path); assert set(four.values())=={"grok","openai","anthropic","gemini"} and create_four_way_blinding(tmp_path)==four


def test_comparison_metrics_consequence_and_keep_replace():
    cases=[]; g={"items":{}}; o={"items":{}}
    for n in range(4):
        case={"case_id":str(n)}; cases.append(case); gr=result("unrelated","replace",20+n); op=result("illustrates_claimed_consequence","keep",70+n); gr.update(case_id=str(n)); op.update(case_id=str(n)); g["items"][str(n)]=gr; o["items"][str(n)]=op
    compared=compare_results(cases,g,o)
    assert compared["primary_exact_agreement"]==0 and compared["unrelated_counts"]=={"grok":4,"openai":0}
    assert len([x for x in compared["consequence_recognition_events"] if x["pattern"]=="grok_unrelated_openai_consequence"])==4
    assert compared["keep_replace"]["agreement"]==0 and compared["provisional_recommendation"]=="Insufficient human evidence"
    assert confusion(["a","b"],["a","a"])["b"]["a"]==1 and cohens_kappa(["a","b"],["a","a"])==0


def test_three_provider_comparison():
    cases=[{"case_id":str(n)} for n in range(3)]; providers={}
    rels={"grok":["unrelated","illustrates_mechanism","unrelated"],"openai":["illustrates_claimed_consequence","illustrates_mechanism","unrelated"],"anthropic":["illustrates_claimed_consequence","illustrates_mechanism","secondary_theme_match"]}
    for provider in rels:
        providers[provider]={"model":provider,"items":{str(n):{"case_id":str(n),**result(rel,score=40+n*20)} for n,rel in enumerate(rels[provider])},"failures":{}}
    value=compare_n_results(cases,providers)
    assert len(value["pairwise"])==3 and value["three_way"]["exact_primary_agreement"]==pytest.approx(1/3)
    assert value["three_way"]["unique_unrelated"] and value["three_way"]["two_against_one"]


def test_four_provider_consensus():
    cases=[{"case_id":str(n)} for n in range(3)]; rels={"grok":["unrelated","unrelated","ambiguous"],"openai":["unrelated","unrelated","secondary_theme_match"],"anthropic":["unrelated","ambiguous","unrelated"],"gemini":["illustrates_claimed_consequence","ambiguous","contradictory"]}; data={}
    for p,values in rels.items(): data[p]={"model":p,"items":{str(n):{"case_id":str(n),**result(v)} for n,v in enumerate(values)},"failures":{}}
    v=compare_n_results(cases,data)["multi_provider"]; assert v["three_against_one"]==["0"] and v["two_versus_two"]==["1"] and v["complete_disagreement"]==["2"] and len(v["consensus"])==3


@pytest.mark.allow_loopback_network
def test_blinded_review_storage_no_provider_leak(tmp_path):
    project=tmp_path/'project'; root=project/'generated_review_approved_images'; root.mkdir(parents=True); source=tmp_path/'source'; source.mkdir(); bake=tmp_path/'bake'; bake.mkdir()
    qhash="a"*64; name="tg.png"; q=quote(qhash); q["model"]="grok-4.5"; im=image(name); im["model"]="grok-4.5"; (root/name).write_bytes(b'png'); case={"case_id":"c","quote_hash":qhash,"image_basename":name}; atomic_write_json(bake/'cases.json',{"items":[case]}); atomic_write_json(source/'quote_semantic_fingerprints.json',{"items":{qhash:q}}); atomic_write_json(source/'image_implied_messages_generated.json',{"items":{name:im}}); [atomic_write_json(bake/f'{p}_results.json',{"items":{"c":{"case_id":"c","model":p,**result()}}}) for p in ('grok','openai','anthropic','gemini')]; atomic_write_json(bake/'sealed_provider_mapping_four_way.json',{"Critic A":"grok","Critic B":"openai","Critic C":"anthropic","Critic D":"gemini"})
    server=create_server(project_dir=project,source_run=source,bakeoff_dir=bake,host='127.0.0.1',port=0); thread=threading.Thread(target=server.serve_forever); thread.start()
    try:
        base=f'http://127.0.0.1:{server.server_port}'; page=urllib.request.urlopen(base+'/case/c').read().decode(); assert all(x in page for x in ('Critic A','Critic B','Critic C','Critic D')) and all(x not in page for x in ('grok-4.5','gpt-5.6-sol','claude-sonnet','gemini-2.5'))
        csrf=page.split("name=csrf value='")[1].split("'")[0]; body=urllib.parse.urlencode({"csrf":csrf,"critique_preference":"D","second_best":"A","image_decision_agreement":"all","notes":"D clearer"}).encode(); urllib.request.urlopen(urllib.request.Request(base+'/case/c',data=body)).read()
        saved=json.loads((bake/'blinded_preferences_four_way.json').read_text()); serialised=json.dumps(saved); assert saved['items']['c']['critique_preference']=='D' and all(x not in serialised for x in ('grok','openai','anthropic','gemini'))
    finally: server.shutdown(); thread.join(); server.server_close()


def test_no_external_call_without_explicit_cli_flag(tmp_path):
    ledger=ProviderLedger(tmp_path/'ledger.json','grok'); assert ledger.data['calls']==[]
