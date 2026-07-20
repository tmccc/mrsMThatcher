"""Dependency-free schema and validation for quotation research packets.

Production consumers use this module to validate saved research packets
without importing the Gemini/Vertex research transport or either provider SDK.
"""

from __future__ import annotations

from typing import Any


VERIFICATION = {
    "exact",
    "normalised",
    "excerpt",
    "variant",
    "paraphrase",
    "composite",
    "misattributed",
    "unverified",
}
CONFIDENCE = {"high", "medium", "low"}
TOP_LEVEL_FIELDS = (
    "quote_id",
    "quote_text",
    "verification_status",
    "verified_text",
    "text_variation_notes",
    "speaker",
    "date",
    "source_event",
    "stable_locator",
    "historical_context",
    "immediate_subject",
    "intended_argument",
    "literal_meaning",
    "broader_principle",
    "mechanism",
    "claimed_consequence",
    "entities",
    "editorial_guidance",
    "research_confidence",
    "unresolved_questions",
    "sources",
)
EDITORIAL_FIELDS = (
    "desired_first_impression",
    "historical_requirements",
    "must_be_visually_dominant",
    "must_not_dominate",
    "common_visual_mistakes",
)
SOURCE_FIELDS = ("title", "url", "source_type", "supports")

STRING = {"type": "string"}
STRING_LIST = {
    "type": "array",
    "items": {"type": "string"},
    "maxItems": 16,
}
SOURCE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": STRING,
        "url": STRING,
        "source_type": STRING,
        "supports": {
            "type": "array",
            "items": {"type": "string"},
            "maxItems": 16,
        },
    },
    "required": list(SOURCE_FIELDS),
}
PACKET_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        **{
            field: STRING
            for field in TOP_LEVEL_FIELDS
            if field
            not in {
                "entities",
                "editorial_guidance",
                "unresolved_questions",
                "sources",
            }
        },
        "verification_status": {
            "type": "string",
            "enum": sorted(VERIFICATION),
        },
        "entities": STRING_LIST,
        "editorial_guidance": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "desired_first_impression": STRING,
                "historical_requirements": STRING_LIST,
                "must_be_visually_dominant": STRING_LIST,
                "must_not_dominate": STRING_LIST,
                "common_visual_mistakes": STRING_LIST,
            },
            "required": list(EDITORIAL_FIELDS),
        },
        "research_confidence": {
            "type": "string",
            "enum": sorted(CONFIDENCE),
        },
        "unresolved_questions": STRING_LIST,
        "sources": {
            "type": "array",
            "items": SOURCE_SCHEMA,
            "maxItems": 10,
        },
    },
    "required": list(TOP_LEVEL_FIELDS),
}


def validate_packet(
    value: Any,
    record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate one complete quotation research packet."""
    if not isinstance(value, dict) or set(value) != set(TOP_LEVEL_FIELDS):
        actual = set(value) if isinstance(value, dict) else set()
        missing = sorted(set(TOP_LEVEL_FIELDS) - actual)
        extra = sorted(actual - set(TOP_LEVEL_FIELDS))
        raise ValueError(f"packet fields mismatch missing={missing} extra={extra}")
    if (
        value["verification_status"] not in VERIFICATION
        or value["research_confidence"] not in CONFIDENCE
    ):
        raise ValueError("invalid packet enum")
    if record and (
        value["quote_id"] != record["quote_id"]
        or value["quote_text"] != record["quote_text"]
    ):
        raise ValueError("quote identity changed")
    for field in TOP_LEVEL_FIELDS:
        if field in {
            "entities",
            "editorial_guidance",
            "unresolved_questions",
            "sources",
        }:
            continue
        if not isinstance(value[field], str):
            raise ValueError(f"{field} must be text")
    for field in ("entities", "unresolved_questions"):
        if not isinstance(value[field], list) or any(
            type(item) is not str or not item.strip() for item in value[field]
        ):
            raise ValueError(f"{field} must contain non-empty strings")
    editorial = value["editorial_guidance"]
    if not isinstance(editorial, dict) or set(editorial) != set(EDITORIAL_FIELDS):
        raise ValueError("editorial_guidance fields mismatch")
    if (
        not isinstance(editorial["desired_first_impression"], str)
        or not editorial["desired_first_impression"].strip()
    ):
        raise ValueError("desired_first_impression is required")
    for field in EDITORIAL_FIELDS[1:]:
        if not isinstance(editorial[field], list) or any(
            type(item) is not str or not item.strip() for item in editorial[field]
        ):
            raise ValueError(f"invalid editorial {field}")
    if not isinstance(value["sources"], list) or not value["sources"]:
        raise ValueError("at least one grounded source is required")
    for source in value["sources"]:
        if not isinstance(source, dict) or set(source) != set(SOURCE_FIELDS):
            raise ValueError("source fields mismatch")
        if any(
            not isinstance(source[field], str) or not source[field].strip()
            for field in ("title", "url", "source_type")
        ):
            raise ValueError("source identity fields must be non-empty")
        if (
            not isinstance(source["supports"], list)
            or not source["supports"]
            or any(
                type(item) is not str or not item.strip()
                for item in source["supports"]
            )
        ):
            raise ValueError("source supports must be grounded non-empty text")
    return dict(value)
