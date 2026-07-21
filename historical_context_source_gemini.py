"""Cost-bounded Gemini research for residual historical-context source gaps."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import time
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import requests
from google import genai
from google.genai import errors, types

from historical_context_formatter import atomic_write_json
from historical_context_source_resolution import MAXIMUM_BODY_BYTES, _page_text
from historical_context_source_research_manifest import (
    RESEARCH_FILENAME,
    RESEARCH_MODEL,
    RESEARCH_POLICY_VERSION,
    RESEARCH_SCHEMA_VERSION,
    SOURCE_VALIDATION_POLICY_VERSION,
)
from semantic_alignment.bakeoff import PRICES
from semantic_alignment.quote_research_gemini import (
    SEARCH_QUERY_PRICE,
    classify_http_failure,
    extract_grounding,
    parse_response_packet,
)


MODEL = RESEARCH_MODEL
PROMPT_VERSION = "historical-context-source-gap-search-v12-unstructured-grounding-pilot"
SCHEMA_VERSION = 1
SDK_API_VERSION_DEVELOPER = "v1beta"
SDK_API_VERSION_VERTEX = "v1"
THINKING_LEVEL = "LOW"
THINKING_BUDGET = None
TEMPERATURE = 1
MAX_OUTPUT_TOKENS = 128
# Gemini 3.1 Pro supports up to 64k output tokens, including billable thinking.
# Reserve that documented ceiling even though the visible JSON is capped lower.
MAX_BILLABLE_OUTPUT_TOKENS_GUARD = 65_536
MAX_SEARCH_QUERIES_GUARD = 12
SEARCH_TOOL_MODE = "google_search_web"
PLANNED_LIMIT_USD = 30.0
HARD_LIMIT_USD = 35.0
RESERVE_USD = HARD_LIMIT_USD - PLANNED_LIMIT_USD

ROLES = (
    "wording_verification",
    "attribution_support",
    "source_event_support",
    "historical_context_support",
    "secondary_recollection",
)
CLAIMS = ("wording", "attribution", "source_event", "date", "historical_context")
QUALITIES = (
    "strong_primary_evidence",
    "reliable_secondary_evidence",
    "secondary_recollection",
)
STRING = {"type": "string"}
SOURCE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "title": STRING,
        "url": STRING,
        "publisher": STRING,
        "stable_locator": STRING,
        "assigned_roles": {"type": "array", "items": {"type": "string", "enum": list(ROLES)}, "maxItems": 5},
        "source_quality_class": {"type": "string", "enum": list(QUALITIES)},
        "exact_supporting_passage": STRING,
        "claims_supported": {"type": "array", "items": {"type": "string", "enum": list(CLAIMS)}, "maxItems": 5},
        "support_explanation": STRING,
    },
    "required": [
        "title", "url", "publisher", "stable_locator", "assigned_roles",
        "source_quality_class", "exact_supporting_passage", "claims_supported",
        "support_explanation",
    ],
}
RESULT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "quote_id": STRING,
        "quote_text": STRING,
        "outcome": {"type": "string", "enum": ["reliable_sources_found", "no_reliable_source_located"]},
        "sources": {"type": "array", "items": SOURCE_SCHEMA, "maxItems": 4},
        "research_note": STRING,
    },
    "required": ["quote_id", "quote_text", "outcome", "sources", "research_note"],
}
SEARCH_RESULT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "quote_id": STRING,
        "quote_text": STRING,
        "search_completed": {"type": "boolean"},
        "current_top_result_title": STRING,
        "search_note": STRING,
    },
    "required": [
        "quote_id", "quote_text", "search_completed",
        "current_top_result_title", "search_note",
    ],
}

_PRIMARY_HOSTS = (
    "margaretthatcher.org", "parliament.uk", "hansard.parliament.uk",
    "gov.uk", "archives.gov", "reaganlibrary.gov",
)
_SECONDARY_HOSTS = (
    "bbc.co.uk", "bbc.com", "cam.ac.uk", "cambridge.org", "guardian.com",
    "cato.org", "heraldscotland.com", "independent.co.uk", "latimes.com",
    "newsweek.com", "nytimes.com", "oup.com", "ox.ac.uk", "spectator.co.uk",
    "telegraph.co.uk", "time.com", "washingtonpost.com",
)
_REJECTED_HOST_MARKERS = (
    "azquotes", "brainyquote", "goodreads", "libquotes", "medium.com",
    "pinterest", "quora.com", "quote", "reddit.com", "wikiquote",
    "wikipedia.org", "youtube.com",
)
_APPROXIMATE_WORDING_MINIMUM = 0.80
_NEGATION_TERMS = frozenset({"cannot", "neither", "never", "no", "nor", "not", "without"})
_CONTRAST_GROUPS = (
    frozenset({"east", "west"}),
    frozenset({"north", "south"}),
    frozenset({"before", "after"}),
    frozenset({"friend", "enemy"}),
    frozenset({"ally", "adversary"}),
    frozenset({"freedom", "coercion"}),
    frozenset({"increase", "decrease"}),
    frozenset({"higher", "lower"}),
    frozenset({"more", "less"}),
    frozenset({"rise", "fall"}),
    frozenset({"success", "failure"}),
    frozenset({"true", "false"}),
    frozenset({"socialism", "capitalism"}),
)
_NAME_EXCLUSIONS = frozenset({
    "a", "an", "and", "as", "but", "do", "for", "how", "i", "if", "in",
    "it", "my", "no", "not", "of", "on", "or", "our", "that", "the",
    "their", "then", "there", "these", "they", "this", "to", "we", "what",
    "when", "where", "which", "who", "why", "you", "your",
})


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _estimate_tokens(text: str) -> int:
    return math.ceil(len(text.encode()) / 3)


def _host(url: str) -> str:
    return urlsplit(url).netloc.casefold().removeprefix("www.")


def _canonical_public_url(url: str) -> str:
    """Remove tracking parameters without changing source-page identity."""
    parsed = urlsplit(url)
    query = urlencode([
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if not key.casefold().startswith("utm_")
        and key.casefold() not in {"gclid", "fbclid"}
    ])
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))


def _normalise(value: Any) -> str:
    return " ".join(str(value or "").replace("’", "'").replace("“", '"').replace("”", '"').split()).casefold()


def _tokens(value: Any) -> list[str]:
    return re.findall(r"[a-z0-9]+", _normalise(value).replace("&", " and "))


def _protected_name_tokens(value: Any) -> set[str]:
    """Return capitalised terms whose substitution could change the actor."""
    return {
        token.casefold()
        for token in re.findall(r"\b[A-Z][A-Za-z]+\b|\b[A-Z]{2,}\b", str(value or ""))
        if token.casefold() not in _NAME_EXCLUSIONS
    }


def _semantic_similarity(candidate: Any, passage: Any) -> float | None:
    """Measure lexical similarity while rejecting material semantic reversals."""
    candidate_tokens = _tokens(candidate)
    passage_tokens = _tokens(passage)
    if len(candidate_tokens) < 8 or len(passage_tokens) < 8:
        return None
    candidate_set = set(candidate_tokens)
    passage_set = set(passage_tokens)
    if (candidate_set & _NEGATION_TERMS) != (passage_set & _NEGATION_TERMS):
        return None
    candidate_numbers = [token for token in candidate_tokens if token.isdigit()]
    passage_numbers = [token for token in passage_tokens if token.isdigit()]
    if candidate_numbers != passage_numbers:
        return None
    if not _protected_name_tokens(candidate) <= passage_set:
        return None
    for group in _CONTRAST_GROUPS:
        if (candidate_set & group) != (passage_set & group):
            return None
        candidate_sequence = [token for token in candidate_tokens if token in group]
        passage_sequence = [token for token in passage_tokens if token in group]
        if candidate_sequence != passage_sequence:
            return None
    ratio = SequenceMatcher(
        a=candidate_tokens, b=passage_tokens, autojunk=False
    ).ratio()
    return ratio if ratio >= _APPROXIMATE_WORDING_MINIMUM else None


def research_prompt(packet: dict[str, Any], audit_item: dict[str, Any]) -> str:
    """Build one exact-phrase search prompt; local code adjudicates sources."""
    payload = {
        "quote_id": packet["quote_id"],
        "quote_text": packet["quote_text"],
        "verification_status": packet["verification_status"],
        "verified_text": packet["verified_text"],
    }
    return f"""Prompt version: {PROMPT_VERSION}
Use Google Search now for exactly one historical quotation.
After using Google Search, return only the words SEARCH COMPLETE. Model prose is discarded.

MANDATORY TOOL USE: your first substantive action must be Google Search. Search the exact quotation
in quotation marks with Margaret Thatcher's name. If needed, search its verified wording and then the
most distinctive eight-to-twelve-word phrase. Do not answer from memory. A response without returned
Google Search grounding metadata is invalid.

PACKET:
{json.dumps(payload, ensure_ascii=False, sort_keys=True)}

RULES:
- Execute one to three focused searches even if you believe you already know the answer.
- Do not decide whether the quotation is authentic and do not propose source URLs in the answer.
- Search-result URLs and passages will be extracted from provider grounding metadata and checked locally.
- Do not alter the packet's wording, status or posting eligibility.
"""


def validate_model_result(value: Any, packet: dict[str, Any]) -> dict[str, Any]:
    """Validate model structure and immutable quotation identity."""
    if not isinstance(value, dict) or set(value) != set(RESULT_SCHEMA["required"]):
        raise ValueError("source research result fields differ")
    if value["quote_id"] != packet["quote_id"] or value["quote_text"] != packet["quote_text"]:
        raise ValueError("source research changed quotation identity")
    if value["outcome"] not in {"reliable_sources_found", "no_reliable_source_located"}:
        raise ValueError("invalid source research outcome")
    if not isinstance(value["sources"], list) or len(value["sources"]) > 4:
        raise ValueError("invalid source research source list")
    if (value["outcome"] == "reliable_sources_found") != bool(value["sources"]):
        raise ValueError("source research outcome/source list conflict")
    if not isinstance(value["research_note"], str):
        raise ValueError("source research note must be text")
    for source in value["sources"]:
        if not isinstance(source, dict) or set(source) != set(SOURCE_SCHEMA["required"]):
            raise ValueError("source research source fields differ")
        parsed = urlsplit(str(source["url"]).strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("source research URL is not direct HTTP(S)")
        if parsed.netloc.casefold().endswith("google.com") and parsed.path == "/search":
            raise ValueError("source research returned a Google search URL")
        if "grounding-api-redirect" in source["url"]:
            raise ValueError("source research returned an opaque grounding redirect")
        if not set(source["assigned_roles"]) <= set(ROLES) or not source["assigned_roles"]:
            raise ValueError("source research roles are invalid")
        if not set(source["claims_supported"]) <= set(CLAIMS) or not source["claims_supported"]:
            raise ValueError("source research claims are invalid")
        if source["source_quality_class"] not in QUALITIES:
            raise ValueError("source research quality is invalid")
        passage = str(source["exact_supporting_passage"])
        if not passage.strip() or len(passage) > 500:
            raise ValueError("source research passage is missing or excessive")
    return dict(value)


def validate_search_result(value: Any, packet: dict[str, Any]) -> dict[str, Any]:
    """Validate the search-only response and immutable quotation identity."""
    if not isinstance(value, dict) or set(value) != set(SEARCH_RESULT_SCHEMA["required"]):
        raise ValueError("source search result fields differ")
    if value["quote_id"] != packet["quote_id"] or value["quote_text"] != packet["quote_text"]:
        raise ValueError("source search changed quotation identity")
    if value["search_completed"] is not True:
        raise ValueError("source search did not report completion")
    if not isinstance(value["current_top_result_title"], str):
        raise ValueError("source search current result title must be text")
    if not isinstance(value["search_note"], str):
        raise ValueError("source search note must be text")
    return dict(value)


def residual_queue(audit: dict[str, Any]) -> list[str]:
    """Order the true no-source residual with mandatory regressions first."""
    mandatory = (
        "eb1d2ebaac7e321e67174d2db5761d2bd008341ebb04b1abac4d4a927cd7a7d4",
        "313172d18e2d915e514e4a202a8b1bcbb077472c2504dee63fe98edaf60e0b3a",
        "5f14e6e600773cf394a3f3a3ae21aef10f108691eef50093002571765a5a4a82",
        "677bda2ba3097d2452133f66a0eab9c9740a06a0be8d53bdd712f52b53ff7bab",
        "7c29b290a8e1898c86c39b0cdd23c60161ca73cc068d4a888a19f1f7f3967d0d",
    )
    priority = {quote_id: index for index, quote_id in enumerate(mandatory)}
    status_order = {"exact": 0, "normalised": 1, "excerpt": 2, "variant": 3,
                    "unverified": 4, "paraphrase": 5, "composite": 6,
                    "misattributed": 7}
    quote_ids = list(audit["summary"]["no_reliable_source_quote_ids"])
    return sorted(quote_ids, key=lambda quote_id: (
        0 if quote_id in priority else 1,
        priority.get(quote_id, 0),
        0 if audit["items"][quote_id]["attribution_eligible"] else 1,
        status_order.get(audit["items"][quote_id]["current_wording_status"], 99),
        quote_id,
    ))


def generation_config() -> dict[str, Any]:
    """Return the pinned Developer API generation configuration."""
    return {
        "maxOutputTokens": MAX_OUTPUT_TOKENS,
        "thinkingConfig": {"thinkingLevel": THINKING_LEVEL},
        "temperature": TEMPERATURE,
    }


def developer_search_tool() -> dict[str, Any]:
    """Return the explicit retrieval tool used by the bounded Developer pilot."""
    if SEARCH_TOOL_MODE == "google_search_retrieval_zero_threshold":
        return {
            "googleSearchRetrieval": {
                "dynamicRetrievalConfig": {
                    "mode": "MODE_DYNAMIC",
                    "dynamicThreshold": 0.0,
                }
            }
        }
    return {"googleSearch": {"searchTypes": {"webSearch": {}}}}


def maximum_call_cost(prompt: str) -> float:
    """Return conservative full-output plus guarded-search exposure."""
    return (
        _estimate_tokens(prompt) * PRICES["gemini"]["input"]
        + MAX_BILLABLE_OUTPUT_TOKENS_GUARD * PRICES["gemini"]["output"]
    ) / 1_000_000 + MAX_SEARCH_QUERIES_GUARD * SEARCH_QUERY_PRICE


def build_preflight(
    packets: dict[str, dict[str, Any]],
    audit: dict[str, Any],
    queue_ids: list[str],
    *,
    prior_known_spend_usd: float = 0.0,
) -> dict[str, Any]:
    """Build an exact queue and conservative cost preflight."""
    prompts = [research_prompt(packets[q], audit["items"][q]) for q in queue_ids]
    maxima = [maximum_call_cost(prompt) for prompt in prompts]
    new_work_exposure = sum(maxima)
    return {
        "schema_version": 1,
        "model": MODEL,
        "sdk_version": _sdk_version(),
        "developer_api_version": SDK_API_VERSION_DEVELOPER,
        "vertex_api_version": SDK_API_VERSION_VERTEX,
        "thinking_level": THINKING_LEVEL,
        "thinking_budget": THINKING_BUDGET,
        "temperature": TEMPERATURE,
        "response_schema_version": SCHEMA_VERSION,
        "prompt_version": PROMPT_VERSION,
        "pricing_version": "gemini-pricing-2026-07-21-project-pinned",
        "queue_count": len(queue_ids),
        "queue_quote_ids": queue_ids,
        "queue_sha256": _sha256(("\n".join(queue_ids) + "\n").encode()),
        "estimated_input_tokens": sum(_estimate_tokens(prompt) for prompt in prompts),
        "maximum_visible_output_tokens": len(queue_ids) * MAX_OUTPUT_TOKENS,
        "maximum_billable_output_tokens_guard": (
            len(queue_ids) * MAX_BILLABLE_OUTPUT_TOKENS_GUARD
        ),
        "maximum_search_queries": len(queue_ids) * MAX_SEARCH_QUERIES_GUARD,
        "prior_known_spend_usd": prior_known_spend_usd,
        "maximum_single_call_exposure_usd": round(max(maxima, default=0.0), 6),
        "conservative_one_attempt_new_work_usd": round(new_work_exposure, 6),
        "conservative_one_attempt_exposure_usd": round(
            prior_known_spend_usd + new_work_exposure, 6
        ),
        "conservative_two_attempt_exposure_usd": round(
            prior_known_spend_usd + new_work_exposure * 2, 6
        ),
        "full_queue_guaranteed_within_hard_limit": (
            prior_known_spend_usd + new_work_exposure <= HARD_LIMIT_USD
        ),
        "planned_limit_usd": PLANNED_LIMIT_USD,
        "protected_reserve_usd": RESERVE_USD,
        "hard_limit_usd": HARD_LIMIT_USD,
        "one_worker": True,
        "google_search_grounding": True,
        "developer_search_tool_mode": SEARCH_TOOL_MODE,
    }


def _sdk_version() -> str:
    import importlib.metadata

    return importlib.metadata.version("google-genai")


def _usage(raw: dict[str, Any]) -> dict[str, int]:
    usage = raw.get("usageMetadata") or raw.get("usage_metadata") or {}
    input_tokens = int(usage.get("promptTokenCount") or usage.get("prompt_token_count") or 0)
    candidates = int(usage.get("candidatesTokenCount") or usage.get("candidates_token_count") or 0)
    thinking = int(usage.get("thoughtsTokenCount") or usage.get("thoughts_token_count") or 0)
    cached = int(usage.get("cachedContentTokenCount") or usage.get("cached_content_token_count") or 0)
    if not input_tokens and not candidates and not thinking:
        raise ValueError("Gemini response lacks authoritative token usage")
    return {"input_tokens": input_tokens, "cached_tokens": cached,
            "thinking_tokens": thinking, "output_tokens": candidates + thinking}


def _cost(usage: dict[str, int], query_count: int) -> float:
    return (
        (usage["input_tokens"] - usage["cached_tokens"]) * PRICES["gemini"]["input"]
        + usage["cached_tokens"] * PRICES["gemini"]["cached_input"]
        + usage["output_tokens"] * PRICES["gemini"]["output"]
    ) / 1_000_000 + query_count * SEARCH_QUERY_PRICE


class DeveloperClient:
    """Call the pinned model through the Developer API."""
    transport = "developer_api"

    def __init__(self, api_key: str):
        """Configure a non-retrying Developer API client."""
        if not api_key:
            raise RuntimeError("Gemini Developer API key is unavailable")
        self.api_key = api_key

    def call(self, prompt: str) -> dict[str, Any]:
        """Submit one grounded source-search request and normalise its result."""
        started = time.monotonic()
        response = requests.post(
            f"https://generativelanguage.googleapis.com/{SDK_API_VERSION_DEVELOPER}/models/{MODEL}:generateContent",
            headers={"Content-Type": "application/json", "x-goog-api-key": self.api_key},
            json={"contents": [{"role": "user", "parts": [{"text": prompt}]}],
                  "tools": [developer_search_tool()], "generationConfig": generation_config()},
            timeout=(20, 300),
        )
        response.raise_for_status()
        raw = response.json()
        return _normalise_response(
            raw, time.monotonic() - started,
            response.headers.get("x-request-id") or response.headers.get("request-id"),
        )


class VertexClient:
    """Call the equivalent pinned model through Vertex AI."""
    transport = "vertex_ai"

    def __init__(self, project: str, location: str = "global"):
        """Configure the explicitly authorised Vertex fallback transport."""
        if not project:
            raise RuntimeError("GOOGLE_CLOUD_PROJECT is unavailable for Vertex fallback")
        self.client = genai.Client(vertexai=True, project=project, location=location)

    def call(self, prompt: str) -> dict[str, Any]:
        """Submit one grounded fallback request and normalise its result."""
        started = time.monotonic()
        response = self.client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                max_output_tokens=MAX_OUTPUT_TOKENS,
                thinking_config=types.ThinkingConfig(
                    thinking_level=types.ThinkingLevel.LOW
                ),
                temperature=TEMPERATURE,
                tools=[types.Tool(google_search=types.GoogleSearch())],
                http_options=types.HttpOptions(timeout=300_000, api_version=SDK_API_VERSION_VERTEX),
            ),
        )
        raw = response.model_dump(mode="json", exclude_none=True)
        return _normalise_response(raw, time.monotonic() - started, response.response_id)


def _normalise_response(raw: dict[str, Any], latency: float, request_id: str | None) -> dict[str, Any]:
    grounding = extract_grounding(raw)
    usage = _usage(raw)
    try:
        parsed, repairs = parse_response_packet(raw)
        parse_error = None
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        parsed, repairs = None, []
        parse_error = f"{type(exc).__name__}: {exc}"
    return {
        "raw": raw,
        "parsed": parsed,
        "parse_repairs": repairs,
        "parse_error": parse_error,
        "grounding": grounding,
        "usage": usage,
        "cost_usd": _cost(usage, len(grounding["queries"])),
        "latency_seconds": latency,
        "request_id": request_id,
    }


def _source_authority(url: str) -> str | None:
    host = _host(url)
    if any(host == item or host.endswith("." + item) for item in _PRIMARY_HOSTS):
        return "strong_primary_evidence"
    if any(host == item or host.endswith("." + item) for item in _SECONDARY_HOSTS):
        return "reliable_secondary_evidence"
    return None


def recognised_source_authority(url: str) -> str | None:
    """Expose the deterministic direct-source allow-list to offline runners."""
    return _source_authority(url)


def _source_years(value: Any) -> set[str]:
    return set(re.findall(r"\b(?:18|19|20)\d{2}\b", str(value or "")))


def _source_authority_for_packet(
    url: str,
    packet: dict[str, Any],
    page_title: str | None = None,
) -> str | None:
    """Do not mistake a later official quotation for a primary transcript."""
    authority = _source_authority(url)
    if authority != "strong_primary_evidence":
        return authority
    host = _host(url)
    if host == "parliament.uk" or host.endswith(".parliament.uk"):
        packet_years = _source_years(packet.get("date"))
        page_years = _source_years(f"{url} {page_title or ''}")
        if not packet_years or not (packet_years & page_years):
            return "reliable_secondary_evidence"
    return authority


def _passage_occurs(page_text: str, passage: str) -> bool:
    normalised_page = _normalise(page_text)
    normalised_passage = _normalise(passage)
    if len(normalised_passage) >= 20 and normalised_passage in normalised_page:
        return True
    words = _tokens(passage)
    page_words = _tokens(page_text)
    if len(words) < 8 or len(words) > len(page_words):
        return False
    return any(page_words[index:index + len(words)] == words
               for index in range(len(page_words) - len(words) + 1))


def _passage_supports_wording(packet: dict[str, Any], passage: str) -> bool:
    return _passage_wording_match(packet, passage) is not None


def _passage_wording_match(
    packet: dict[str, Any], passage: str
) -> tuple[str, float] | None:
    """Classify exact-token and guarded approximate source wording."""
    passage_tokens = _tokens(passage)
    for field in ("quote_text", "verified_text"):
        wording_tokens = _tokens(packet.get(field))
        if wording_tokens and len(wording_tokens) <= len(passage_tokens) and any(
            passage_tokens[index:index + len(wording_tokens)] == wording_tokens
            for index in range(len(passage_tokens) - len(wording_tokens) + 1)
        ):
            return "exact_token_sequence", 1.0
    similarities = [
        score
        for field in ("quote_text", "verified_text")
        if (score := _semantic_similarity(packet.get(field), passage)) is not None
    ]
    if similarities:
        return "approximate_semantic_guarded", max(similarities)
    return None


def verify_source(
    source: dict[str, Any],
    packet: dict[str, Any],
    request: Callable[..., Any] = requests.get,
) -> tuple[dict[str, Any] | None, str | None]:
    """Fetch a proposed source and retain it only when its passage exists."""
    url = str(source["url"]).strip()
    host = _host(url)
    if any(marker in host for marker in _REJECTED_HOST_MARKERS):
        return None, "rejected_source_class"
    authority = _source_authority(url)
    if authority is None and source["source_quality_class"] != "secondary_recollection":
        return None, "unrecognised_source_authority"
    try:
        response = request(
            url, allow_redirects=True, timeout=(10, 30), stream=True,
            headers={"User-Agent": "MrsMThatcher-source-audit/1.0"},
        )
        if not response.ok:
            return None, f"http_{response.status_code}"
        content = bytearray()
        for block in response.iter_content(64 * 1024):
            remaining = MAXIMUM_BODY_BYTES - len(content)
            if remaining <= 0:
                break
            content.extend(block[:remaining])
        page_text, page_title = _page_text(
            bytes(content), str(response.headers.get("content-type") or "")
        )
    except Exception as exc:
        return None, f"fetch_failed:{type(exc).__name__}"
    passage = str(source["exact_supporting_passage"]).strip()
    if not _passage_occurs(page_text, passage):
        return None, "supporting_passage_not_found"
    wording_match = _passage_wording_match(packet, passage)
    final_url = _canonical_public_url(str(response.url))
    roles = list(dict.fromkeys(source["assigned_roles"]))
    claims = list(dict.fromkeys(source["claims_supported"]))
    if "wording_verification" in roles:
        if wording_match is None:
            return None, "wording_not_found_in_supporting_passage"
    quality = (
        "secondary_recollection"
        if source["source_quality_class"] == "secondary_recollection"
        else _source_authority_for_packet(final_url, packet, page_title)
    )
    if quality is None:
        return None, "source_quality_not_confirmed"
    return {
        "source_id": _sha256(_canonical_json({"quote_id": packet["quote_id"], "url": final_url,
                                                "passage": passage, "roles": roles})),
        "title": str(source["title"]).strip() or page_title or final_url,
        "url": final_url,
        "publisher": str(source["publisher"]).strip(),
        "stable_locator": str(source["stable_locator"]).strip(),
        "assigned_roles": roles,
        "source_quality_class": quality,
        "exact_supporting_passage": passage,
        "exact_supporting_passage_sha256": _sha256(passage.encode()),
        "claims_supported": claims,
        "support_explanation": str(source["support_explanation"]).strip(),
        "fetched_body_sha256": _sha256(bytes(content)),
        "page_title": page_title,
        "wording_match_kind": wording_match[0] if wording_match else None,
        "wording_similarity": wording_match[1] if wording_match else None,
        "source_validation_policy_version": SOURCE_VALIDATION_POLICY_VERSION,
        "verified_at": _utc_now(),
    }, None


def _exact_wording_passage(
    page_text: str, packet: dict[str, Any]
) -> tuple[str | None, tuple[int, int] | None]:
    """Extract an actual page substring containing one authorised wording."""
    page_matches = list(re.finditer(r"[A-Za-z0-9]+", page_text))
    page_tokens = [match.group(0).casefold() for match in page_matches]
    candidates = [packet.get("quote_text"), packet.get("verified_text")]
    for candidate in sorted(
        (str(value) for value in candidates if value), key=len, reverse=True
    ):
        wanted = _tokens(candidate)
        if len(wanted) < 8 or len(wanted) > len(page_tokens):
            continue
        for index in range(len(page_tokens) - len(wanted) + 1):
            if page_tokens[index:index + len(wanted)] != wanted:
                continue
            start = page_matches[index].start()
            end = page_matches[index + len(wanted) - 1].end()
            passage = page_text[start:end].strip()
            if passage and len(passage) <= 500:
                return passage, (start, end)
    return None, None


def _approximate_wording_passage(
    page_text: str, packet: dict[str, Any]
) -> tuple[str | None, tuple[int, int] | None, float | None]:
    """Locate a guarded >=80% wording variant without treating it as exact."""
    page_matches = list(re.finditer(r"[A-Za-z0-9]+", page_text))
    page_tokens = [match.group(0).casefold() for match in page_matches]
    best: tuple[float, int, int] | None = None
    for candidate in (packet.get("quote_text"), packet.get("verified_text")):
        candidate_text = str(candidate or "")
        wanted = _tokens(candidate_text)
        if len(wanted) < 8 or not page_tokens:
            continue
        matcher = SequenceMatcher(a=wanted, b=page_tokens, autojunk=True)
        minimum_block = max(3, math.ceil(len(wanted) * 0.15))
        starts = {
            max(0, block.b - block.a + offset)
            for block in matcher.get_matching_blocks()
            if block.size >= minimum_block
            for offset in range(-2, 3)
        }
        minimum_length = max(8, math.floor(len(wanted) * 0.80))
        maximum_length = min(
            len(page_tokens), math.ceil(len(wanted) * 1.20)
        )
        lengths = range(minimum_length, maximum_length + 1)
        for start in starts:
            for length in lengths:
                end = start + length
                if end > len(page_tokens):
                    continue
                passage = page_text[
                    page_matches[start].start():page_matches[end - 1].end()
                ].strip()
                score = _semantic_similarity(candidate_text, passage)
                if score is not None and (best is None or score > best[0]):
                    best = (score, start, end)
    if best is None:
        return None, None, None
    score, start, end = best
    passage = page_text[page_matches[start].start():page_matches[end - 1].end()].strip()
    return passage, (page_matches[start].start(), page_matches[end - 1].end()), score


def verify_grounding_source(
    grounding_source: dict[str, Any],
    packet: dict[str, Any],
    request: Callable[..., Any] = requests.get,
) -> tuple[dict[str, Any] | None, str | None]:
    """Resolve a provider-linked search result and verify wording on its page."""
    url = str(grounding_source.get("url") or "").strip()
    if not url:
        return None, "grounding_url_missing"
    if (
        "grounding-api-redirect" not in url
        and _source_authority(url) is None
    ):
        return None, "unrecognised_source_authority"
    try:
        response = request(
            url, allow_redirects=True, timeout=(10, 30), stream=True,
            headers={"User-Agent": "MrsMThatcher-source-audit/1.0"},
        )
        if not response.ok:
            return None, f"http_{response.status_code}"
        content = bytearray()
        for block in response.iter_content(64 * 1024):
            remaining = MAXIMUM_BODY_BYTES - len(content)
            if remaining <= 0:
                break
            content.extend(block[:remaining])
        page_text, page_title = _page_text(
            bytes(content), str(response.headers.get("content-type") or "")
        )
    except Exception as exc:
        return None, f"fetch_failed:{type(exc).__name__}"
    final_url = _canonical_public_url(str(response.url))
    host = _host(final_url)
    if any(marker in host for marker in _REJECTED_HOST_MARKERS):
        return None, "rejected_source_class"
    authority = _source_authority_for_packet(final_url, packet, page_title)
    if authority is None:
        return None, "unrecognised_source_authority"
    passage, span = _exact_wording_passage(page_text, packet)
    match_kind = "exact_token_sequence"
    similarity = 1.0
    if passage is None or span is None:
        passage, span, similarity = _approximate_wording_passage(page_text, packet)
        match_kind = "approximate_semantic_guarded"
    if passage is None or span is None or similarity is None:
        return None, "quotation_wording_not_found"
    context = page_text[max(0, span[0] - 500):span[1] + 500]
    if "thatcher" not in _normalise(context + " " + str(page_title or "")):
        return None, "attribution_not_present_near_wording"
    wording_role_allowed = match_kind == "exact_token_sequence" or packet.get(
        "verification_status"
    ) in {"normalised", "excerpt", "variant", "paraphrase", "composite", "unverified"}
    roles = ["attribution_support"]
    claims = ["attribution"]
    if wording_role_allowed:
        roles.insert(0, "wording_verification")
        claims.insert(0, "wording")
    return {
        "source_id": _sha256(_canonical_json({
            "quote_id": packet["quote_id"], "url": final_url,
            "passage": passage, "roles": roles,
        })),
        "title": page_title or str(grounding_source.get("title") or final_url),
        "url": final_url,
        "publisher": str(grounding_source.get("title") or _host(final_url)),
        "stable_locator": final_url,
        "assigned_roles": roles,
        "source_quality_class": authority,
        "exact_supporting_passage": passage,
        "exact_supporting_passage_sha256": _sha256(passage.encode()),
        "claims_supported": claims,
        "support_explanation": (
            "The provider-grounded page contains "
            + ("the quotation wording" if match_kind == "exact_token_sequence" else "a guarded wording variant")
            + " and identifies Margaret Thatcher in the surrounding page context."
        ),
        "fetched_body_sha256": _sha256(bytes(content)),
        "page_title": page_title,
        "wording_match_kind": match_kind,
        "wording_similarity": round(similarity, 6),
        "grounding_result_url_sha256": _sha256(url.encode()),
        "source_validation_policy_version": SOURCE_VALIDATION_POLICY_VERSION,
        "verified_at": _utc_now(),
    }, None


def verify_grounding_sources(
    grounding_sources: list[dict[str, Any]], packet: dict[str, Any]
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Verify all grounded result pages, deduplicating final evidence."""
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for source in grounding_sources:
        verified, reason = verify_grounding_source(source, packet)
        if verified is None:
            rejected.append({"source": source, "reason": reason})
            continue
        key = (verified["url"], verified["exact_supporting_passage_sha256"])
        if key not in seen:
            accepted.append(verified)
            seen.add(key)
    return accepted, rejected


def revalidate_saved_results(
    results: dict[str, Any],
    packets: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Re-run deterministic page checks without repeating a model request."""
    updated = json.loads(json.dumps(results))
    for quote_id, item in updated.get("items", {}).items():
        accepted = list(item.get("validated_sources", []))
        remaining = []
        for rejected in item.get("rejected_sources", []):
            verified, reason = verify_source(rejected["source"], packets[quote_id])
            if verified is None:
                remaining.append({"source": rejected["source"], "reason": reason})
            else:
                verified["prior_rejection_reason"] = rejected.get("reason")
                accepted.append(verified)
        item["validated_sources"] = accepted
        item["rejected_sources"] = remaining
        item["final_outcome"] = (
            "reliable_sources_found" if accepted else "no_reliable_source_located"
        )
        item["deterministic_revalidated_at"] = _utc_now()
    return updated


def recover_saved_grounding_results(
    run_dir: Path,
    results: dict[str, Any],
    packets: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Recover page-verified evidence from saved provider grounding metadata."""
    updated = json.loads(json.dumps(results))
    for quote_id, item in updated.get("items", {}).items():
        raw_dir = run_dir / "raw_responses" / quote_id
        matching_raw = None
        for path in sorted(raw_dir.glob("*.json")):
            raw = json.loads(path.read_text(encoding="utf-8"))
            if _sha256(_canonical_json(raw)) == item.get("response_hash"):
                matching_raw = raw
                break
        if matching_raw is None:
            item["grounding_recovery_error"] = "matching_saved_response_not_found"
            continue
        grounding = extract_grounding(matching_raw)
        accepted, rejected = verify_grounding_sources(
            grounding.get("sources", []), packets[quote_id]
        )
        evidence_keys = {
            (row["url"], row["exact_supporting_passage_sha256"])
            for row in item.get("validated_sources", [])
        }
        for source in accepted:
            key = (source["url"], source["exact_supporting_passage_sha256"])
            if key not in evidence_keys:
                item.setdefault("validated_sources", []).append(source)
                evidence_keys.add(key)
        item["rejected_grounding_sources"] = rejected
        item["final_outcome"] = (
            "reliable_sources_found"
            if item.get("validated_sources")
            else "no_reliable_source_located"
        )
        item["grounding_recovered_at"] = _utc_now()
    return updated


def _append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


class ResearchRunner:
    """Execute a resumable one-worker source research queue."""

    def __init__(
        self,
        run_dir: Path,
        research_dir: Path,
        packets: dict[str, dict[str, Any]],
        audit: dict[str, Any],
        queue_ids: list[str],
        developer: Any,
        vertex: Any | None,
        *,
        hard_limit_usd: float = HARD_LIMIT_USD,
        prior_known_spend_usd: float = 0.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        """Configure a resumable queue with persistent routing and cost state."""
        self.run_dir = run_dir
        self.research_dir = research_dir
        self.packets = packets
        self.audit = audit
        self.queue_ids = queue_ids
        self.developer = developer
        self.vertex = vertex
        self.hard_limit = hard_limit_usd
        self.prior_known_spend = float(prior_known_spend_usd)
        self.sleep = sleep
        self.ledger_path = run_dir / "api_cost_ledger.json"
        self.route_path = run_dir / "provider_route_state.json"
        self.results_path = run_dir / "research_results.json"
        self.attempts_path = run_dir / "provider_attempts.jsonl"

    def _load(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        self.run_dir.mkdir(parents=True, exist_ok=True)
        ledger = json.loads(self.ledger_path.read_text()) if self.ledger_path.exists() else {
            "schema_version": 1, "hard_limit_usd": self.hard_limit, "known_spend_usd": 0.0,
            "prior_known_spend_usd": self.prior_known_spend,
            "ambiguous_exposure_usd": 0.0, "calls": [], "ambiguous_attempts": [],
        }
        ledger.setdefault("prior_known_spend_usd", 0.0)
        if not math.isclose(
            float(ledger["prior_known_spend_usd"]), self.prior_known_spend,
            rel_tol=0.0, abs_tol=1e-9,
        ):
            raise RuntimeError("saved Gemini ledger prior-spend baseline differs")
        ledger["known_spend_usd"] = self.prior_known_spend + sum(
            float(row["known_cost_usd"]) for row in ledger.get("calls", [])
        )
        route = json.loads(self.route_path.read_text()) if self.route_path.exists() else {
            "schema_version": 1, "developer_unavailable": False,
            "consecutive_developer_429s": 0, "developer_grounding_failures": 0,
            "developer_grounding_unavailable": False,
        }
        route.setdefault("developer_grounding_failures", 0)
        route.setdefault("developer_grounding_unavailable", False)
        results = json.loads(self.results_path.read_text()) if self.results_path.exists() else {
            "schema_version": 1, "items": {}, "failures": {},
        }
        for path, value in ((self.ledger_path, ledger), (self.route_path, route),
                            (self.results_path, results)):
            atomic_write_json(path, value)
        return ledger, route, results

    def _guard(self, ledger: dict[str, Any], maximum: float) -> None:
        exposure = float(ledger["known_spend_usd"]) + float(ledger["ambiguous_exposure_usd"])
        if exposure + maximum > self.hard_limit:
            raise RuntimeError("historical-context source research hard cost limit reached")

    def _attempt(self, quote_id: str, client: Any, number: int,
                 ledger: dict[str, Any]) -> tuple[str, dict[str, Any] | None]:
        packet = self.packets[quote_id]
        prompt = research_prompt(packet, self.audit["items"][quote_id])
        maximum = maximum_call_cost(prompt)
        self._guard(ledger, maximum)
        base = {
            "logical_call_id": f"source-research:{quote_id}", "quote_id": quote_id,
            "transport": client.transport, "attempt_number": number, "model": MODEL,
            "prompt_hash": _sha256(prompt.encode()), "prompt_version": PROMPT_VERSION,
            "schema_version": SCHEMA_VERSION,
        }
        _append_jsonl(self.attempts_path, {**base, "status": "prepared", "timestamp": _utc_now(),
                                           "maximum_possible_cost_usd": maximum})
        _append_jsonl(self.attempts_path, {**base, "status": "sending", "timestamp": _utc_now(),
                                           "maximum_possible_cost_usd": maximum})
        try:
            response = client.call(prompt)
        except Exception as exc:
            if type(exc).__name__ == "NotFoundError" or getattr(exc, "status_code", None) == 404:
                failure = {"kind": "http_404", "message": f"{type(exc).__name__}: endpoint not found"}
            else:
                failure = classify_http_failure(exc)
            status = "429" if failure.get("kind") == "quota_429" else (
                "ambiguous" if failure.get("kind") == "ambiguous" else "failed"
            )
            _append_jsonl(self.attempts_path, {**base, "status": status, "timestamp": _utc_now(),
                                               "failure": failure})
            if status == "ambiguous":
                ledger["ambiguous_attempts"].append({**base, "maximum_possible_cost_usd": maximum})
                ledger["ambiguous_exposure_usd"] = sum(
                    row["maximum_possible_cost_usd"] for row in ledger["ambiguous_attempts"]
                )
                atomic_write_json(self.ledger_path, ledger)
            return failure.get("kind", "failed"), None
        raw_path = self.run_dir / "raw_responses" / quote_id / f"{client.transport}_attempt_{number}.json"
        atomic_write_json(raw_path, response["raw"])
        call = {
            **base, "status": "completed", "request_id": response["request_id"],
            "response_hash": _sha256(_canonical_json(response["raw"])),
            "input_tokens": response["usage"]["input_tokens"],
            "output_tokens": response["usage"]["output_tokens"],
            "thinking_tokens": response["usage"]["thinking_tokens"],
            "search_query_count": len(response["grounding"]["queries"]),
            "known_cost_usd": response["cost_usd"], "maximum_possible_cost_usd": maximum,
            "latency_seconds": response["latency_seconds"], "timestamp": _utc_now(),
        }
        ledger["calls"].append(call)
        ledger["known_spend_usd"] = self.prior_known_spend + sum(
            row["known_cost_usd"] for row in ledger["calls"]
        )
        atomic_write_json(self.ledger_path, ledger)
        _append_jsonl(self.attempts_path, call)
        response["transport"] = client.transport
        return "completed", response

    def run(self, *, maximum_new_calls: int | None = None) -> dict[str, Any]:
        """Process incomplete queue items sequentially within the hard cost limit."""
        ledger, route, results = self._load()
        new_calls = 0
        for quote_id in self.queue_ids:
            if quote_id in results["items"] or quote_id in results["failures"]:
                continue
            if maximum_new_calls is not None and new_calls >= maximum_new_calls:
                break
            direct_vertex = (
                route["developer_unavailable"] or route["developer_grounding_unavailable"]
            )
            clients = [self.vertex] if direct_vertex else [self.developer]
            if clients[0] is None:
                raise RuntimeError("Vertex routing is required but Vertex is not configured")
            client = clients[0]
            outcome, response = self._attempt(quote_id, client, 1, ledger)
            new_calls += 1
            if client.transport == "developer_api":
                if outcome == "quota_429":
                    route["consecutive_developer_429s"] += 1
                    atomic_write_json(self.route_path, route)
                    if route["consecutive_developer_429s"] < 2:
                        self.sleep(2)
                        outcome, response = self._attempt(quote_id, client, 2, ledger)
                        new_calls += 1
                        if outcome == "quota_429":
                            route["consecutive_developer_429s"] += 1
                            atomic_write_json(self.route_path, route)
                        else:
                            route["consecutive_developer_429s"] = 0
                            atomic_write_json(self.route_path, route)
                    if outcome == "quota_429" and route["consecutive_developer_429s"] >= 2:
                        route["developer_unavailable"] = True
                        atomic_write_json(self.route_path, route)
                        if self.vertex is None:
                            raise RuntimeError("Developer API unavailable and Vertex fallback is not configured")
                        outcome, response = self._attempt(quote_id, self.vertex, 1, ledger)
                        new_calls += 1
                else:
                    route["consecutive_developer_429s"] = 0
                    atomic_write_json(self.route_path, route)
            elif outcome == "quota_429":
                self.sleep(2)
                outcome, response = self._attempt(quote_id, client, 2, ledger)
                new_calls += 1
                if outcome == "quota_429":
                    raise RuntimeError("Vertex returned two consecutive 429 responses")
            if (
                outcome == "completed"
                and response is not None
                and not response["grounding"]["queries"]
                and response["transport"] == "developer_api"
            ):
                route["developer_grounding_failures"] += 1
                if self.vertex is not None and route["developer_grounding_failures"] >= 2:
                    route["developer_grounding_unavailable"] = True
                atomic_write_json(self.route_path, route)
                if self.vertex is not None:
                    outcome, response = self._attempt(quote_id, self.vertex, 1, ledger)
                    new_calls += 1
                else:
                    self.sleep(2)
                    outcome, response = self._attempt(quote_id, self.developer, 2, ledger)
                    new_calls += 1
                    if (
                        outcome == "completed"
                        and response is not None
                        and response["grounding"]["queries"]
                    ):
                        route["developer_grounding_failures"] = 0
                        atomic_write_json(self.route_path, route)
                    elif outcome == "completed" and response is not None:
                        route["developer_grounding_failures"] += 1
                        atomic_write_json(self.route_path, route)
                        outcome, response = "ungrounded", None
                if self.vertex is not None and route["developer_grounding_unavailable"] and (
                    response is None or not response["grounding"]["queries"]
                ):
                    raise RuntimeError(
                        "Developer API returned two consecutive ungrounded responses"
                    )
            elif (
                outcome == "completed"
                and response is not None
                and response["transport"] == "developer_api"
                and response["grounding"]["queries"]
            ):
                route["developer_grounding_failures"] = 0
                atomic_write_json(self.route_path, route)
            if outcome != "completed" or response is None:
                results["failures"][quote_id] = {"outcome": outcome, "recorded_at": _utc_now()}
                atomic_write_json(self.results_path, results)
                continue
            try:
                if not response["grounding"]["queries"]:
                    raise ValueError("Gemini did not use Google Search grounding")
                accepted, rejected = [], []
                grounded, rejected_grounding = verify_grounding_sources(
                    response["grounding"].get("sources", []),
                    self.packets[quote_id],
                )
                evidence_keys = {
                    (row["url"], row["exact_supporting_passage_sha256"])
                    for row in accepted
                }
                for source in grounded:
                    key = (source["url"], source["exact_supporting_passage_sha256"])
                    if key not in evidence_keys:
                        accepted.append(source)
                        evidence_keys.add(key)
                results["items"][quote_id] = {
                    "quote_id": quote_id,
                    "quote_text": self.packets[quote_id]["quote_text"],
                    "model_outcome": "grounded_search_completed",
                    "final_outcome": "reliable_sources_found" if accepted else "no_reliable_source_located",
                    "validated_sources": accepted,
                    "rejected_sources": rejected,
                    "rejected_grounding_sources": rejected_grounding,
                    "research_note": (
                        "Model prose discarded; evidence derived only from provider "
                        "grounding metadata and locally verified source pages."
                    ),
                    "transport": response["transport"],
                    "model": MODEL,
                    "prompt_version": PROMPT_VERSION,
                    "response_hash": _sha256(_canonical_json(response["raw"])),
                    "search_query_count": len(response["grounding"]["queries"]),
                    "completed_at": _utc_now(),
                }
            except (KeyError, TypeError, ValueError) as exc:
                results["failures"][quote_id] = {
                    "outcome": "validation_failure", "error": str(exc), "recorded_at": _utc_now()
                }
            atomic_write_json(self.results_path, results)
        return self.build_manifest(ledger, route, results)

    def build_manifest(self, ledger: dict[str, Any], route: dict[str, Any],
                       results: dict[str, Any]) -> dict[str, Any]:
        """Persist the dependency-free manifest for completed research results."""
        manifest = {
            "schema_version": RESEARCH_SCHEMA_VERSION,
            "policy_version": RESEARCH_POLICY_VERSION,
            "model": MODEL,
            "sdk_version": _sdk_version(),
            "developer_api_version": SDK_API_VERSION_DEVELOPER,
            "vertex_api_version": SDK_API_VERSION_VERTEX,
            "thinking_level": THINKING_LEVEL,
            "thinking_budget": THINKING_BUDGET,
            "temperature": TEMPERATURE,
            "prompt_version": PROMPT_VERSION,
            "response_schema_version": SCHEMA_VERSION,
            "queue_quote_ids": self.queue_ids,
            "completed_quote_count": len(results["items"]),
            "failure_count": len(results["failures"]),
            "known_spend_usd": ledger["known_spend_usd"],
            "prior_known_spend_usd": ledger["prior_known_spend_usd"],
            "ambiguous_exposure_usd": ledger["ambiguous_exposure_usd"],
            "developer_unavailable": route["developer_unavailable"],
            "developer_grounding_failure_count": route["developer_grounding_failures"],
            "developer_grounding_unavailable": route["developer_grounding_unavailable"],
            "items": dict(sorted(results["items"].items())),
            "failures": dict(sorted(results["failures"].items())),
        }
        atomic_write_json(self.research_dir / RESEARCH_FILENAME, manifest)
        return manifest


def _load_inputs(research_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    from historical_context_formatter import load_and_validate_corpus
    from historical_context_source_roles import AUDIT_FILENAME

    packets, _unresolved = load_and_validate_corpus(
        research_dir, require_source_role_audit=True
    )
    audit = json.loads((research_dir / AUDIT_FILENAME).read_text(encoding="utf-8"))
    return packets, audit


def _write_preflight(run_dir: Path, preflight: dict[str, Any]) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(run_dir / "gemini_source_research_preflight.json", preflight)
    lines = [
        "# Gemini Source Research Preflight",
        "",
        f"- Queue: **{preflight['queue_count']}** residual no-source packets",
        f"- Model: `{preflight['model']}`",
        f"- SDK: `{preflight['sdk_version']}`",
        f"- API versions: Developer `{preflight['developer_api_version']}`, Vertex `{preflight['vertex_api_version']}`",
        f"- Thinking/temperature: `{preflight['thinking_level']}` / `{preflight['temperature']}`",
        f"- Prompt/schema: `{preflight['prompt_version']}` / `{preflight['response_schema_version']}`",
        f"- Prior run-family spend: **${preflight['prior_known_spend_usd']:.6f}**",
        f"- Maximum reserved for the next single call: **${preflight['maximum_single_call_exposure_usd']:.6f}**",
        f"- Conservative one-attempt exposure: **${preflight['conservative_one_attempt_exposure_usd']:.6f}**",
        f"- Conservative two-attempt exposure: **${preflight['conservative_two_attempt_exposure_usd']:.6f}**",
        f"- Planned limit/reserve/hard stop: **${preflight['planned_limit_usd']:.2f} / ${preflight['protected_reserve_usd']:.2f} / ${preflight['hard_limit_usd']:.2f}**",
        "- One worker; Google Search grounding; every retained passage must be fetched and matched locally.",
        "- Requests are sequential and stop before the next full-output reservation could breach the hard limit.",
        "- The run cannot change quotation text, IDs, verification status or posting eligibility.",
        "",
    ]
    (run_dir / "gemini_source_research_preflight.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    """Run a Gemini source-research preflight or an explicitly authorised job."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("preflight", "execute"))
    parser.add_argument(
        "--research-dir", type=Path,
        default=Path("semantic_alignment_research/quote_research_full_001"),
    )
    parser.add_argument(
        "--run-dir", type=Path,
        default=Path("semantic_alignment_research/historical_context_source_audit_001/gemini_research"),
    )
    parser.add_argument("--execute-gemini", action="store_true")
    parser.add_argument("--confirm-hard-limit-usd", type=float)
    parser.add_argument("--maximum-new-calls", type=int)
    parser.add_argument("--prior-known-spend-usd", type=float, default=0.0)
    parser.add_argument("--enable-vertex-fallback", action="store_true")
    args = parser.parse_args(argv)
    packets, audit = _load_inputs(args.research_dir)
    queue_ids = residual_queue(audit)
    preflight = build_preflight(
        packets, audit, queue_ids,
        prior_known_spend_usd=args.prior_known_spend_usd,
    )
    _write_preflight(args.run_dir, preflight)
    if args.command == "preflight":
        print(json.dumps(preflight, indent=2, sort_keys=True))
        return 0
    if not args.execute_gemini or args.confirm_hard_limit_usd != HARD_LIMIT_USD:
        raise SystemExit(
            f"execution requires --execute-gemini --confirm-hard-limit-usd {HARD_LIMIT_USD:.2f}"
        )
    api_key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    developer = DeveloperClient(api_key or "")
    project = os.getenv("GOOGLE_CLOUD_PROJECT")
    vertex = (
        VertexClient(project)
        if args.enable_vertex_fallback and project
        else None
    )
    runner = ResearchRunner(
        args.run_dir, args.research_dir, packets, audit, queue_ids,
        developer, vertex, hard_limit_usd=HARD_LIMIT_USD,
        prior_known_spend_usd=args.prior_known_spend_usd,
    )
    manifest = runner.run(maximum_new_calls=args.maximum_new_calls)
    print(json.dumps({
        "queue_count": len(queue_ids),
        "completed_quote_count": manifest["completed_quote_count"],
        "failure_count": manifest["failure_count"],
        "known_spend_usd": manifest["known_spend_usd"],
        "ambiguous_exposure_usd": manifest["ambiguous_exposure_usd"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
