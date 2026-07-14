from __future__ import annotations

import hashlib
import json
from typing import Any

from .bakeoff import FREE_TRADE_KEY
from .first_impression import EVEREST_QUOTE_HASH
from .scene_grammar_pilot import scene_spec as pilot_scene_spec

SCHEMA_VERSION = 1
ANALYSIS_KIND = "scene_grammar_plan"
SCENE_FIELDS = (
    "primary_subject",
    "secondary_subjects",
    "setting",
    "camera_position",
    "camera_height",
    "lens_style",
    "foreground",
    "midground",
    "background",
    "dominant_object",
    "largest_object",
    "viewer_eye_path",
    "scale_relationships",
    "must_include",
    "must_not_include",
    "forbidden_messages",
    "desired_first_second_message",
    "desired_tone",
    "success_test",
)


def _camera_height(camera_position: str) -> str:
    lowered = camera_position.lower()
    if "low" in lowered or "below" in lowered:
        return "low, below the primary subject"
    if "over" in lowered or "behind" in lowered:
        return "standing eye height with the stated over-shoulder relationship"
    return "natural standing eye height"


def _lens_style(camera_position: str) -> str:
    lowered = camera_position.lower()
    if "tight" in lowered or "close" in lowered:
        return "50mm natural-perspective editorial portrait lens"
    if "wide" in lowered:
        return "28-35mm documentary wide lens without exaggerated distortion"
    return "35-50mm natural-perspective documentary lens"


def build_scene_plan(case: dict[str, Any], brief: dict[str, Any], failure: dict[str, Any] | None) -> dict[str, Any]:
    source = pilot_scene_spec(case, brief, failure)
    eye_path = list(source["attention_hierarchy"][:3])
    while len(eye_path) < 3:
        eye_path.append(source["setting"] if len(eye_path) == 2 else source["primary_subject"])
    must_include = list(source["must_include"])
    scene = {
        "primary_subject": source["primary_subject"],
        "secondary_subjects": list(source["secondary_subjects"]),
        "setting": source["setting"],
        "camera_position": source["camera_view"],
        "camera_height": _camera_height(source["camera_view"]),
        "lens_style": _lens_style(source["camera_view"]),
        "foreground": must_include[1] if len(must_include) > 1 else "unobstructed contextual evidence supporting the primary action",
        "midground": source["primary_subject"],
        "background": f"{source['setting']}; {must_include[2] if len(must_include) > 2 else 'quiet location context'}",
        "dominant_object": eye_path[0],
        "largest_object": source["primary_subject"],
        "viewer_eye_path": eye_path,
        "scale_relationships": list(source["scale_relationships"]),
        "must_include": must_include,
        "must_not_include": list(source["must_avoid"]),
        "forbidden_messages": list(source["forbidden_dominant_messages"]),
        "desired_first_second_message": source["desired_first_second_message"],
        "desired_tone": list(source["desired_tone"]),
        "success_test": (
            f"At X timeline size, the first reading is '{source['desired_first_second_message']}'. "
            f"The eye moves through {' -> '.join(eye_path)}; every must_include element is physically visible; "
            "no must_not_include element or forbidden message is present or visually dominant."
        ),
    }
    if case["quote_hash"] == EVEREST_QUOTE_HASH:
        scene.update(
            foreground="the summit ridge rising directly to the climber",
            midground="climber standing at Everest's highest point and planting a recognisable Union Flag",
            background="all Himalayan peaks and cloud layers visibly below Everest's summit",
            dominant_object="Everest summit with climber and Union Flag",
            largest_object="Mount Everest, visibly the tallest mountain and taller than every surrounding peak",
            viewer_eye_path=["Everest summit and climber", "recognisable Union Flag", "lower surrounding peaks"],
            success_test="At X timeline size, Everest is unmistakably the tallest mountain, a climber stands at its summit, and a recognisable Union Flag communicates patriotic achievement. No communist, map, Cold War or geopolitical imagery appears.",
        )
    elif case["quote_hash"] == FREE_TRADE_KEY[0]:
        scene.update(
            foreground="real British-made goods and visible payment evidence",
            midground="British seller and overseas buyer freely completing an exchange",
            background="working British loading bay connected to an international port",
            dominant_object="voluntary exchange between seller and buyer",
            largest_object="seller, buyer and exchanged goods as one connected group",
            viewer_eye_path=["voluntary exchange", "real goods and payment", "international shipping context"],
            success_test="At X timeline size, a viewer immediately reads voluntary international exchange and a real consumer or growth consequence, not capitalism-versus-socialism conflict. No generic ideological symbolism dominates.",
        )
    validate_scene(scene)
    input_hash = hashlib.sha256(
        json.dumps({"case": case, "brief": brief, "failure": failure}, sort_keys=True).encode()
    ).hexdigest()
    return {
        "schema_version": SCHEMA_VERSION,
        "analysis_kind": ANALYSIS_KIND,
        "case_id": case["case_id"],
        "quote_hash": case["quote_hash"],
        "quote_text": case["quote_text"],
        "input_hash": input_hash,
        "source_failure_categories": list((failure or {}).get("categories", [])),
        "scene_spec": scene,
    }


def validate_scene(scene: Any) -> dict[str, Any]:
    if not isinstance(scene, dict) or set(scene) != set(SCENE_FIELDS):
        actual = set(scene) if isinstance(scene, dict) else set()
        raise ValueError(f"scene schema mismatch missing={sorted(set(SCENE_FIELDS)-actual)} extra={sorted(actual-set(SCENE_FIELDS))}")
    list_fields = {
        "secondary_subjects", "viewer_eye_path", "scale_relationships", "must_include",
        "must_not_include", "forbidden_messages", "desired_tone",
    }
    for field in SCENE_FIELDS:
        value = scene[field]
        if field in list_fields:
            if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
                raise ValueError(f"{field} must be a list of non-empty strings")
        elif not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} must be non-empty text")
    if len(scene["viewer_eye_path"]) != 3:
        raise ValueError("viewer_eye_path must contain exactly three stages")
    if not scene["scale_relationships"] or not scene["must_include"]:
        raise ValueError("scale_relationships and must_include cannot be empty")
    return scene


def schema_document() -> dict[str, Any]:
    properties = {}
    list_fields = {"secondary_subjects", "viewer_eye_path", "scale_relationships", "must_include", "must_not_include", "forbidden_messages", "desired_tone"}
    for field in SCENE_FIELDS:
        properties[field] = {"type": "array", "items": {"type": "string"}} if field in list_fields else {"type": "string", "minLength": 1}
    properties["viewer_eye_path"]["minItems"] = 3
    properties["viewer_eye_path"]["maxItems"] = 3
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "Scene Grammar Planning v1",
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": list(SCENE_FIELDS),
    }
