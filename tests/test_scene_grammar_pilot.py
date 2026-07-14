import pytest
from semantic_alignment.scene_grammar_pilot import blinded_candidates,compile_prompt,preflight,scene_spec,validate_spec

def data(q="a"*64):
 c={"case_id":"c","quote_hash":q,"quote_text":"A British law quote"};b={"desired_primary_subject":"citizen at work","must_include":["citizen","British civic setting"],"must_avoid":["US flag"],"forbidden_dominant_messages":["foreign politics"],"desired_first_impression":"responsible freedom","desired_tone":["resolute"]};return c,b

def test_scene_spec_deterministic_and_physical():
 c,b=data();a=scene_spec(c,b);assert a==scene_spec(c,b);validate_spec(a);assert a["scale_relationships"] and a["camera_view"] and a["composition"]
def test_required_and_forbidden_compiled():
 c,b=data();p=compile_prompt(scene_spec(c,b),"scene_grammar_direct");assert "British civic setting" in p and "foreign politics" in p
def test_styles_have_equivalent_scene_payload():
 c,b=data();a=compile_prompt(scene_spec(c,b),"scene_grammar_direct");d=compile_prompt(scene_spec(c,b),"scene_grammar_cinematic");assert a.split("SCENE_SPECIFICATION:",1)[1]==d.split("SCENE_SPECIFICATION:",1)[1]
def test_no_human_label_leakage():
 c,b=data();p=compile_prompt(scene_spec(c,b),"scene_grammar_direct").lower();assert "human label" not in p and "expected winner" not in p
def test_cost_gate():assert preflight(20)["allowed"] and not preflight(21)["allowed"]
def test_pilot_preflight_accounts_for_named_person_audits():assert preflight(20)["identity_audit_calls"]==6
def test_missing_required_object_rejected():
 c,b=data();s=scene_spec(c,b);s["must_include"]=[]
 with pytest.raises(ValueError):validate_spec(s)

def test_blinded_review_projection_hides_style_and_prompt():
 rows=[
  {"candidate_id":"b","image_basename":"b.png","style":"scene_grammar_direct","prompt":"secret"},
  {"candidate_id":"a","image_basename":"a.png","style":"scene_grammar_cinematic","prompt":"secret"},
 ]
 assert blinded_candidates(rows)==[
  {"label":"A","candidate_id":"a","image_basename":"a.png"},
  {"label":"B","candidate_id":"b","image_basename":"b.png"},
 ]
