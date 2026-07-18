import base64

import pytest

from semantic_alignment.generation_prompt_pilot import (
    ImageClient, STYLES, build_brief, candidate_id, execute_generation, preflight, prompt_for_style,
)


def sample_brief():
    quote={"quote_hash":"a"*64,"quote_text":"Q","core_claim":"enterprise creates prosperity","claims":[],"specific_concepts":["enterprise"],"dominant_message":"prosperity","primary_themes":["freedom"],"desired_visual_evidence":["shopkeeper serving customers"],"not_about":["communist conflict"]}
    intent={"desired_first_impression":"enterprise improving daily life","desired_primary_visual_subjects":["shopkeeper serving customers"],"desired_tone":["optimistic"],"undesired_dominant_messages":["communist conflict"]}
    return build_brief(quote,intent)


def test_brief_and_prompt_styles_are_deterministic_and_forbidden_messages_present():
    brief=sample_brief(); assert brief==sample_brief()
    prompts=[prompt_for_style(brief,s) for s in STYLES]
    assert len(set(prompts))==3
    assert all("communist conflict" in p for p in prompts)
    assert all("human label" not in p.lower() for p in prompts)


def test_preflight_ceiling():
    pf=preflight({"items":[{}]*20}); assert pf["generation_calls"]==60 and pf["conservative_maximum_cost_usd"]<20
    with pytest.raises(RuntimeError): preflight({"items":[{}]*21})


class Response:
    status_code=200; text=""
    def json(self): return {"data":[{"b64_json":base64.b64encode(b"png").decode()}],"usage":{}}


def test_image_client_and_execution_require_explicit_key_and_cap(tmp_path):
    with pytest.raises(RuntimeError): ImageClient("")
    client=ImageClient("x",transport=lambda *a,**k:Response())
    brief=sample_brief(); q=brief["quote_hash"]; manifest={"items":[{"case_id":"c","quote_hash":q}]}
    with pytest.raises(RuntimeError): execute_generation(tmp_path,manifest,{q:brief},client=client,confirmed_limit=19)


def test_candidate_identity_stable(): assert candidate_id("a"*64,"mechanism_first")==candidate_id("a"*64,"mechanism_first")
