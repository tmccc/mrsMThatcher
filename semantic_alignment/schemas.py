from __future__ import annotations

import math
import re
from typing import Any

from . import CRITIC_SCHEMA_VERSION, IMAGE_SCHEMA_VERSION, QUOTE_SCHEMA_VERSION

HEX64 = re.compile(r"^[0-9a-f]{64}$")
RELATIONSHIPS = {
    "direct_equivalence", "strong_support", "partial_support",
    "related_but_not_equivalent", "generic_ideological_substitution",
    "secondary_theme_only", "contradiction", "unrelated", "ambiguous",
}

STRING = {"type": "string", "minLength": 1, "maxLength": 1200}
STRING_LIST = {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 240}, "maxItems": 16}
CONFIDENCE = {"type": "number", "minimum": 0, "maximum": 1}
QUOTE_CLAIM = {
    "type": "object", "additionalProperties": False,
    "properties": {"claim": STRING, "importance": {"type": "string", "enum": ["primary", "secondary"]}, "confidence": CONFIDENCE},
    "required": ["claim", "importance", "confidence"],
}
IMAGE_CLAIM = {
    "type": "object", "additionalProperties": False,
    "properties": {"claim": STRING, "salience": {"type": "string", "enum": ["dominant", "secondary", "speculative"]}, "confidence": CONFIDENCE, "visual_support": STRING_LIST},
    "required": ["claim", "salience", "confidence", "visual_support"],
}
MATCHED_CLAIM = {
    "type": "object", "additionalProperties": False,
    "properties": {"quote_claim": STRING, "image_claim": STRING, "match_strength": CONFIDENCE},
    "required": ["quote_claim", "image_claim", "match_strength"],
}

QUOTE_OUTPUT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "dominant_message": STRING, "core_claim": STRING,
        "claims": {"type": "array", "items": QUOTE_CLAIM, "minItems": 1, "maxItems": 10},
        "primary_issue": {**STRING, "maxLength": 120},
        "primary_themes": STRING_LIST, "secondary_themes": STRING_LIST,
        "specific_concepts": STRING_LIST, "desired_visual_evidence": STRING_LIST,
        "explicit_contrasts": STRING_LIST, "not_about": STRING_LIST,
        "confidence": CONFIDENCE,
    },
    "required": ["dominant_message", "core_claim", "claims", "primary_issue", "primary_themes", "secondary_themes", "specific_concepts", "desired_visual_evidence", "explicit_contrasts", "not_about", "confidence"],
}

IMAGE_OUTPUT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "dominant_message": STRING, "core_implied_claim": STRING,
        "implied_claims": {"type": "array", "items": IMAGE_CLAIM, "minItems": 1, "maxItems": 10},
        "primary_issue": {**STRING, "maxLength": 120},
        "primary_themes": STRING_LIST, "secondary_messages": STRING_LIST,
        "specific_concepts": STRING_LIST, "visual_evidence": STRING_LIST,
        "emotional_tone": STRING_LIST,
        "ambiguity": {"type": "string", "enum": ["low", "medium", "high"]},
        "confidence": CONFIDENCE,
    },
    "required": ["dominant_message", "core_implied_claim", "implied_claims", "primary_issue", "primary_themes", "secondary_messages", "specific_concepts", "visual_evidence", "emotional_tone", "ambiguity", "confidence"],
}

CRITIC_OUTPUT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "quote_core_claim": STRING, "image_core_implied_claim": STRING,
        "claim_relationship": {"type": "string", "enum": sorted(RELATIONSHIPS)},
        "matched_claims": {"type": "array", "items": MATCHED_CLAIM, "maxItems": 12},
        "unillustrated_primary_claims": STRING_LIST,
        "image_claims_not_required_by_quote": STRING_LIST,
        "semantic_alignment_score": {"type": "number", "minimum": 0, "maximum": 100},
        "directness_score": {"type": "number", "minimum": 0, "maximum": 100},
        "editorial_power_score": {"type": "number", "minimum": 0, "maximum": 100},
        "visual_specificity_score": {"type": "number", "minimum": 0, "maximum": 100},
        "primary_issue_match": {"type": "boolean"},
        "dominant_message_match": {"type": "string", "enum": ["strong", "partial", "weak", "none", "contradictory"]},
        "mismatch_type": {"type": "string", "enum": sorted(RELATIONSHIPS)},
        "explanation": STRING, "stronger_visual_direction": STRING_LIST,
        "confidence": CONFIDENCE,
    },
    "required": ["quote_core_claim", "image_core_implied_claim", "claim_relationship", "matched_claims", "unillustrated_primary_claims", "image_claims_not_required_by_quote", "semantic_alignment_score", "directness_score", "editorial_power_score", "visual_specificity_score", "primary_issue_match", "dominant_message_match", "mismatch_type", "explanation", "stronger_visual_direction", "confidence"],
}


def _text(value: Any, key: str) -> str:
    if type(value) is not str or not value.strip():
        raise ValueError(f"{key} must be a non-empty string")
    return value


def _strings(value: Any, key: str) -> list[str]:
    if not isinstance(value, list) or any(type(item) is not str for item in value):
        raise ValueError(f"{key} must be a list of strings")
    return value


def _confidence(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ValueError("confidence must be finite")
    number = float(value)
    if not 0 <= number <= 1:
        raise ValueError("confidence must be between 0 and 1")
    return number


def _claims(value: Any, key: str, *, image: bool = False) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{key} must be a non-empty list")
    for index, claim in enumerate(value):
        if not isinstance(claim, dict):
            raise ValueError(f"{key}[{index}] must be an object")
        _text(claim.get("claim"), f"{key}[{index}].claim")
        _confidence(claim.get("confidence"))
        if image:
            if claim.get("salience") not in {"dominant", "secondary", "speculative"}:
                raise ValueError("invalid claim salience")
            support = _strings(claim.get("visual_support"), "visual_support")
            if not support:
                raise ValueError("image claim requires visual support")
        elif claim.get("importance") not in {"primary", "secondary"}:
            raise ValueError("invalid claim importance")
    return value


def validate_quote_fingerprint(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict) or item.get("schema_version") != QUOTE_SCHEMA_VERSION or item.get("analysis_kind") != "quote_semantic_fingerprint":
        raise ValueError("invalid quote fingerprint header")
    if not HEX64.fullmatch(str(item.get("quote_hash", ""))):
        raise ValueError("invalid quote_hash")
    for key in ("quote_text", "dominant_message", "core_claim", "primary_issue"):
        _text(item.get(key), key)
    _claims(item.get("claims"), "claims")
    for key in ("primary_themes", "secondary_themes", "specific_concepts", "desired_visual_evidence", "explicit_contrasts", "not_about"):
        _strings(item.get(key), key)
    _confidence(item.get("confidence"))
    return dict(item)


def validate_image_fingerprint(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict) or item.get("schema_version") != IMAGE_SCHEMA_VERSION or item.get("analysis_kind") != "image_implied_message":
        raise ValueError("invalid image fingerprint header")
    _text(item.get("image_basename"), "image_basename")
    if not HEX64.fullmatch(str(item.get("sha256", ""))):
        raise ValueError("invalid sha256")
    for key in ("dominant_message", "core_implied_claim", "primary_issue", "ambiguity"):
        _text(item.get(key), key)
    _claims(item.get("implied_claims"), "implied_claims", image=True)
    for key in ("primary_themes", "secondary_messages", "specific_concepts", "visual_evidence", "emotional_tone"):
        _strings(item.get(key), key)
    _confidence(item.get("confidence"))
    return dict(item)


def validate_critic_result(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict) or item.get("schema_version") != CRITIC_SCHEMA_VERSION or item.get("analysis_kind") != "quote_image_semantic_alignment":
        raise ValueError("invalid critic result header")
    if not HEX64.fullmatch(str(item.get("quote_hash", ""))):
        raise ValueError("invalid quote_hash")
    _text(item.get("image_basename"), "image_basename")
    for key in ("semantic_alignment_score", "directness_score", "editorial_power_score", "visual_specificity_score"):
        value = item.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)) or not 0 <= float(value) <= 100:
            raise ValueError(f"{key} must be finite and between 0 and 100")
    for key in ("quote_core_claim", "image_core_implied_claim"):
        _text(item.get(key), key)
    if item.get("claim_relationship") not in RELATIONSHIPS:
        raise ValueError("invalid claim_relationship")
    if item.get("mismatch_type") not in RELATIONSHIPS:
        raise ValueError("invalid mismatch_type")
    _strings(item.get("unillustrated_primary_claims"), "unillustrated_primary_claims")
    _strings(item.get("image_claims_not_required_by_quote"), "image_claims_not_required_by_quote")
    matched = item.get("matched_claims")
    if not isinstance(matched, list):
        raise ValueError("matched_claims must be a list")
    for match in matched:
        if not isinstance(match, dict): raise ValueError("matched claim must be an object")
        _text(match.get("quote_claim"), "quote_claim"); _text(match.get("image_claim"), "image_claim"); _confidence(match.get("match_strength"))
    _text(item.get("explanation"), "explanation")
    _strings(item.get("stronger_visual_direction"), "stronger_visual_direction")
    _confidence(item.get("confidence"))
    return dict(item)
