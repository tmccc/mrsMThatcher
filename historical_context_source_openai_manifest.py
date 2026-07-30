"""Dependency-free validation for saved OpenAI source-research evidence."""
from __future__ import annotations

import copy
import hashlib
import re
from typing import Any
from urllib.parse import urlsplit

from historical_context_source_resolution import (
    _sanitise_transient_redirect_url,
)
from historical_context_source_research_manifest import (
    SOURCE_VALIDATION_POLICY_VERSION,
)


OPENAI_RESEARCH_SCHEMA_VERSION = 1
OPENAI_RESEARCH_POLICY_VERSION = (
    "source-role-openai-research-v1-forced-search-and-fetched-passage"
)
OPENAI_RESEARCH_FILENAME = "historical_context_source_openai_research.json"
OPENAI_RESEARCH_MODEL = "gpt-5.4-mini-2026-03-17"
OPENAI_RESEARCH_PROMPT_VERSION = "historical-context-openai-forced-web-search-v1"

_ROLES = {
    "wording_verification",
    "attribution_support",
    "source_event_support",
    "historical_context_support",
    "secondary_recollection",
}
_CLAIMS = {"wording", "attribution", "source_event", "date", "historical_context"}
_QUALITIES = {
    "strong_primary_evidence",
    "reliable_secondary_evidence",
    "secondary_recollection",
}


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sanitise_openai_research_manifest(
    manifest: dict[str, Any],
) -> tuple[dict[str, Any], int]:
    """Remove transient signing material from saved rejected-source URLs."""
    if not isinstance(manifest, dict):
        raise RuntimeError("OpenAI historical-context source manifest is invalid")
    result = copy.deepcopy(manifest)
    changed = 0
    items = result.get("items")
    if not isinstance(items, dict):
        raise RuntimeError("OpenAI historical-context source manifest is invalid")
    for item in items.values():
        if not isinstance(item, dict):
            raise RuntimeError("OpenAI researched item is invalid")
        rejected = item.get("rejected_sources", [])
        if not isinstance(rejected, list):
            raise RuntimeError("OpenAI rejected-source records are invalid")
        for row in rejected:
            source = row.get("source") if isinstance(row, dict) else None
            if not isinstance(source, dict):
                raise RuntimeError("OpenAI rejected-source record is invalid")
            url = source.get("url")
            if not isinstance(url, str):
                raise RuntimeError("OpenAI rejected-source URL is invalid")
            sanitised, modified = _sanitise_transient_redirect_url(url)
            if modified:
                source["url"] = sanitised
                changed += 1
    return result, changed


def validate_openai_research_manifest(
    manifest: dict[str, Any],
    packets: dict[str, dict[str, Any]],
) -> None:
    """Fail closed on incomplete, stale or identity-changing saved research."""
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != OPENAI_RESEARCH_SCHEMA_VERSION
        or manifest.get("policy_version") != OPENAI_RESEARCH_POLICY_VERSION
        or manifest.get("provider") != "openai"
        or manifest.get("model") != OPENAI_RESEARCH_MODEL
        or manifest.get("prompt_version") != OPENAI_RESEARCH_PROMPT_VERSION
    ):
        raise RuntimeError("OpenAI historical-context source manifest is incompatible")
    sanitised_manifest, changed_rejected_url_count = (
        sanitise_openai_research_manifest(manifest)
    )
    if changed_rejected_url_count or sanitised_manifest != manifest:
        raise RuntimeError(
            "OpenAI historical-context source manifest retains transient "
            "provider credentials"
        )
    queue = manifest.get("queue_quote_ids")
    items = manifest.get("items")
    failures = manifest.get("failures")
    if (
        not isinstance(queue, list)
        or len(queue) != len(set(queue))
        or any(quote_id not in packets for quote_id in queue)
        or not isinstance(items, dict)
        or not isinstance(failures, dict)
        or set(items) | set(failures) != set(queue)
        or set(items) & set(failures)
        or manifest.get("queue_count") != len(queue)
        or manifest.get("completed_quote_count") != len(items)
        or manifest.get("failure_count") != len(failures)
    ):
        raise RuntimeError("OpenAI historical-context source manifest coverage differs")
    if any(
        type(manifest.get(field)) not in {int, float}
        or float(manifest[field]) < 0
        for field in (
            "incremental_known_spend_usd",
            "cumulative_known_spend_usd",
            "cumulative_ambiguous_exposure_usd",
        )
    ):
        raise RuntimeError("OpenAI historical-context source cost accounting is invalid")
    for quote_id, item in items.items():
        packet = packets[quote_id]
        sources = item.get("validated_sources")
        if (
            item.get("quote_id") != quote_id
            or item.get("quote_text") != packet["quote_text"]
            or item.get("model") != OPENAI_RESEARCH_MODEL
            or item.get("prompt_version") != OPENAI_RESEARCH_PROMPT_VERSION
            or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("response_hash") or ""))
            or type(item.get("search_call_count")) is not int
            or not 1 <= item["search_call_count"] <= 4
            or not isinstance(sources, list)
            or item.get("final_outcome") not in {
                "reliable_sources_found", "no_locally_verified_source_from_search"
            }
            or (item["final_outcome"] == "reliable_sources_found") != bool(sources)
        ):
            raise RuntimeError(f"OpenAI researched item is invalid: {quote_id}")
        source_ids: set[str] = set()
        for source in sources:
            passage = source.get("exact_supporting_passage")
            source_url = str(source.get("url") or "")
            parsed = urlsplit(source_url)
            _sanitised_url, signed_transport_url = (
                _sanitise_transient_redirect_url(source_url)
            )
            match_kind = source.get("wording_match_kind")
            similarity = source.get("wording_similarity")
            if (
                not re.fullmatch(r"[0-9a-f]{64}", str(source.get("source_id") or ""))
                or source["source_id"] in source_ids
                or parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or signed_transport_url
                or not isinstance(passage, str)
                or not passage.strip()
                or source.get("exact_supporting_passage_sha256")
                != _sha256(passage.encode())
                or not re.fullmatch(
                    r"[0-9a-f]{64}", str(source.get("fetched_body_sha256") or "")
                )
                or not set(source.get("assigned_roles", [])) <= _ROLES
                or not source.get("assigned_roles")
                or not set(source.get("claims_supported", [])) <= _CLAIMS
                or not source.get("claims_supported")
                or source.get("source_quality_class") not in _QUALITIES
                or source.get("research_provider") != "openai"
                or source.get("research_model") != OPENAI_RESEARCH_MODEL
                or source.get("research_prompt_version")
                != OPENAI_RESEARCH_PROMPT_VERSION
                or source.get("source_validation_policy_version")
                != SOURCE_VALIDATION_POLICY_VERSION
                or match_kind not in {
                    "exact_token_sequence", "approximate_semantic_guarded"
                }
                or type(similarity) not in {int, float}
                or not 0.80 <= float(similarity) <= 1.0
                or (match_kind == "exact_token_sequence" and float(similarity) != 1.0)
            ):
                raise RuntimeError(
                    f"OpenAI researched source is invalid: {quote_id}"
                )
            source_ids.add(source["source_id"])
