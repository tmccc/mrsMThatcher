"""Dependency-free validation for saved Gemini source-research evidence."""
from __future__ import annotations

import hashlib
from typing import Any


RESEARCH_SCHEMA_VERSION = 1
RESEARCH_POLICY_VERSION = "source-role-research-v2-grounding-and-fetched-passage-required"
RESEARCH_FILENAME = "historical_context_source_research.json"
RESEARCH_MODEL = "gemini-3.1-pro-preview"
SOURCE_VALIDATION_POLICY_VERSION = (
    "fetched-page-wording-attribution-v3-guarded-approximate-80"
)


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def validate_research_manifest(
    manifest: dict[str, Any],
    packets: dict[str, dict[str, Any]],
) -> None:
    """Fail closed on tampered or identity-incompatible researched evidence."""
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != RESEARCH_SCHEMA_VERSION
        or manifest.get("policy_version") != RESEARCH_POLICY_VERSION
        or manifest.get("model") != RESEARCH_MODEL
    ):
        raise RuntimeError("historical-context source research manifest is incompatible")
    if any(quote_id not in packets for quote_id in manifest.get("queue_quote_ids", [])):
        raise RuntimeError("historical-context source research has unknown queue quotation")
    for quote_id, item in manifest.get("items", {}).items():
        if quote_id not in packets or item.get("quote_text") != packets[quote_id]["quote_text"]:
            raise RuntimeError("historical-context source research changed quotation identity")
        if type(item.get("search_query_count")) is not int or item["search_query_count"] < 1:
            raise RuntimeError("historical-context source research lacks Google Search grounding")
        for source in item.get("validated_sources", []):
            passage = source.get("exact_supporting_passage")
            if (
                not passage
                or source.get("exact_supporting_passage_sha256") != _sha256(passage.encode())
            ):
                raise RuntimeError("historical-context researched passage hash differs")
