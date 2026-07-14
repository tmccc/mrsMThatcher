import json

import pytest

from semantic_alignment.first_impression import EVEREST_QUOTE_HASH
from semantic_alignment.bakeoff import FREE_TRADE_KEY
from semantic_alignment.scene_grammar_planning import SCENE_FIELDS, build_scene_plan, validate_scene
from tools.scene_grammar_planning_review import save_review, validate_review


def inputs(quote_hash="a" * 64):
    case = {"case_id": "case", "quote_hash": quote_hash, "quote_text": "A British civic quotation"}
    brief = {
        "desired_primary_subject": "a citizen taking responsibility",
        "must_include": ["citizen", "British civic setting", "visible responsible action"],
        "must_avoid": ["US imagery"],
        "forbidden_dominant_messages": ["foreign politics"],
        "desired_first_impression": "responsible freedom",
        "desired_tone": ["resolute"],
    }
    return case, brief


def test_scene_plan_is_deterministic_and_exact():
    case, brief = inputs()
    first = build_scene_plan(case, brief, None)
    assert first == build_scene_plan(case, brief, None)
    assert set(first["scene_spec"]) == set(SCENE_FIELDS)
    validate_scene(first["scene_spec"])


def test_everest_controls_are_concrete():
    case, brief = inputs(EVEREST_QUOTE_HASH)
    scene = build_scene_plan(case, brief, None)["scene_spec"]
    assert "tallest" in scene["largest_object"]
    assert "Union Flag" in " ".join(scene["must_include"])
    assert "communist" in " ".join(scene["must_not_include"]).lower()


def test_free_trade_depicts_exchange_not_ideology():
    case, brief = inputs(FREE_TRADE_KEY[0])
    scene = build_scene_plan(case, brief, None)["scene_spec"]
    assert "voluntary exchange" in scene["dominant_object"]
    assert "ideological" in scene["success_test"]


def test_viewer_eye_path_requires_exactly_three_items():
    case, brief = inputs()
    scene = build_scene_plan(case, brief, None)["scene_spec"]
    scene["viewer_eye_path"] = ["one", "two"]
    with pytest.raises(ValueError):
        validate_scene(scene)


def test_review_validation_and_atomic_revision(tmp_path):
    path = tmp_path / "scene_review.json"
    path.write_text(json.dumps({"schema_version": 1, "items": {}}))
    assert validate_review("approve", " ok ") == {"decision": "approve", "notes": "ok"}
    save_review(path, "case", "revise", "move camera")
    save_review(path, "case", "approve", "fixed")
    saved = json.loads(path.read_text())["items"]["case"]
    assert saved["decision"] == "approve" and saved["notes"] == "fixed"


def test_invalid_review_rejected():
    with pytest.raises(ValueError):
        validate_review("maybe", "")
