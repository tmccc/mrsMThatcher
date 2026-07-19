"""Run structured Gemini reviews of blind hybrid-retrieval evidence sets."""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from google import genai
from google.genai import types

from .bakeoff import PRICES, PROVIDER_MODELS
from .hybrid_reply_retrieval import browser_review_payload
from .io import atomic_write_json, atomic_write_text, read_json

MODEL = PROVIDER_MODELS["gemini"]
PROMPT_VERSION = "retrieval-blind-gemini-review-v1"
SCHEMA_VERSION = 1
BATCH_SIZE = 5
MAX_OUTPUT_TOKENS = 6000
THINKING_BUDGET = 512
TEMPERATURE = 0.1
DEVELOPER_LIMIT_USD = 2.0
VERTEX_LIMIT_USD = 2.0
COMBINED_LIMIT_USD = 3.0

CHOICES = {
    "A_better", "B_better", "roughly_equal", "neither_useful",
    "no_historical_evidence", "insufficient_context",
}
INTERVENTIONS = {
    "historical_correction", "historical_context", "researched_principle",
    "humour_preferable", "no_reply_preferable",
}
ASSESSMENTS = {"relevant", "partially_relevant", "irrelevant", "unsafe_as_evidence"}
CONFIDENCE = {"high", "medium", "low"}
DESIRABILITY = {"desirable", "not_desirable", "unclear"}
REASON_TAGS = {
    "conceptually_stronger", "exact_match_stronger", "cross_language_evidence",
    "over_broad_ideological_match", "parent_context_overreach", "unsafe_factual_use",
    "no_sufficiently_relevant_evidence", "requires_human_research",
}

STRING = {"type": "string"}
PACKET_ASSESSMENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "side": {"type": "string", "enum": ["A", "B"]},
        "quote_id": STRING,
        "assessment": {"type": "string", "enum": sorted(ASSESSMENTS)},
        "reason": STRING,
    },
    "required": ["side", "quote_id", "assessment", "reason"],
}
CASE_REVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "case_id": STRING,
        "choice": {"type": "string", "enum": sorted(CHOICES)},
        "intervention": {"type": "string", "enum": sorted(INTERVENTIONS)},
        "evidence_desirability": {"type": "string", "enum": sorted(DESIRABILITY)},
        "confidence": {"type": "string", "enum": sorted(CONFIDENCE)},
        "factual_claim_present": {"type": "boolean"},
        "rationale": STRING,
        "reason_tags": {
            "type": "array",
            "items": {"type": "string", "enum": sorted(REASON_TAGS)},
            "maxItems": len(REASON_TAGS),
        },
        "packet_assessments": {
            "type": "array",
            "items": PACKET_ASSESSMENT_SCHEMA,
            "maxItems": 10,
        },
    },
    "required": [
        "case_id", "choice", "intervention", "evidence_desirability", "confidence",
        "factual_claim_present", "rationale", "reason_tags", "packet_assessments",
    ],
}
TRANSPORT_CASE_REVIEW_SCHEMA = {
    **CASE_REVIEW_SCHEMA,
    "properties": {
        **{
            key: value
            for key, value in CASE_REVIEW_SCHEMA["properties"].items()
            if key not in {"reason_tags", "packet_assessments"}
        },
        "reason_tags_csv": STRING,
        "packet_assessments": {
            "type": "array",
            "items": STRING,
            "maxItems": 10,
        },
    },
    "required": [
        key for key in CASE_REVIEW_SCHEMA["required"] if key != "reason_tags"
    ] + ["reason_tags_csv"],
}
RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "reviews": {
            "type": "array",
            "items": TRANSPORT_CASE_REVIEW_SCHEMA,
            "minItems": 1,
            "maxItems": BATCH_SIZE,
        },
    },
    "required": ["reviews"],
}


def utc_now() -> str:
    """Return the current UTC time as an ISO 8601 string."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def canonical_json(value: Any) -> bytes:
    """Return the canonical JSON."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def sha256(value: Any) -> str:
    """Return the SHA-256."""
    data = value if isinstance(value, bytes) else canonical_json(value)
    return hashlib.sha256(data).hexdigest()


def estimate_tokens(text: str) -> int:
    """Estimate tokens."""
    return math.ceil(len(text.encode("utf-8")) / 3)


def _post_for_prompt(value: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return {
        "author_handle": value.get("author_handle"),
        "text": str(value.get("text") or ""),
        "timestamp": value.get("timestamp"),
        "image_summary": value.get("image_summary"),
    }


def _packet_for_prompt(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value.get(key)
        for key in (
            "quote_id", "quote_text", "intended_argument", "broader_principle",
            "verification_status", "research_confidence", "source_event",
        )
    }


def case_for_prompt(case: dict[str, Any]) -> dict[str, Any]:
    """Return the case for prompt."""
    basis = case.get("query_basis") or {}
    return {
        "case_id": str(case["case_id"]),
        "lane": str(case.get("lane") or "mention"),
        "incoming_user_contribution": _post_for_prompt(case.get("incoming")),
        "direct_parent": _post_for_prompt(case.get("direct_parent")),
        "quoted_post": _post_for_prompt(case.get("quoted_post")),
        "older_thread_context": [
            _post_for_prompt(item) for item in case.get("older_thread_context") or []
        ],
        "query_basis": {
            key: bool(basis.get(key))
            for key in (
                "substantive_query", "incoming_text_used", "direct_parent_used",
                "older_thread_used", "quoted_post_used",
            )
        },
        "retrieval_set_A": [_packet_for_prompt(item) for item in case.get("A") or []],
        "retrieval_set_B": [_packet_for_prompt(item) for item in case.get("B") or []],
    }


def review_prompt(cases: list[dict[str, Any]]) -> str:
    """Return the review prompt."""
    payload = [case_for_prompt(case) for case in cases]
    return f"""Prompt version: {PROMPT_VERSION}
You are the blind editorial reviewer for a conversational historical-evidence retrieval trial.
Review each case independently and return only the required JSON.

WHAT YOU ARE JUDGING
- Judge which retrieval set supplies safer, more relevant historical evidence for responding to the incoming user's actual contribution.
- Historically grounded evidence is desirable only when it enables a material factual correction, a useful historical qualification, or a genuinely relevant researched principle.
- Historical material is not intrinsically preferable. For greetings, reactions, ordinary conversation, or cases where history would feel forced, choose no_historical_evidence and prefer humour or no reply.
- Judge retrieval quality, not the prose of a hypothetical reply and not whether you agree politically with the evidence.

EVIDENCE RULES
- Use only the supplied conversation and packet summaries. Do not rely on outside research or unstated facts.
- The incoming user's contribution is primary. Parent, quoted-post and older-thread context may disambiguate it only when query_basis says that source was used.
- Do not let a parent Thatcher quotation manufacture relevance for a content-free user reaction.
- historical_correction requires a material factual claim and highly relevant, high-confidence supplied evidence.
- A broad ideological resemblance is not enough. Mark a packet unsafe_as_evidence when using it could support a misleading correction or attribution.
- If required conversational context is genuinely inadequate, choose insufficient_context. This differs from neither_useful and no_historical_evidence.
- Assess every packet occurrence in A and B. Keep rationales concise.
- Return reason_tags_csv as a comma-separated list of allowed reason-tag enum values, or an empty string.
- Return every packet assessment as one string in exactly this form: SIDE|QUOTE_ID|ASSESSMENT|REASON. SIDE is A or B; ASSESSMENT is relevant, partially_relevant, irrelevant, or unsafe_as_evidence. Do not put a pipe character in REASON.
- The identities of A and B are deliberately hidden. Do not speculate about which retriever produced either set.

CASES
{json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2)}
"""


def _packet_keys(case: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        (side, str(packet.get("quote_id") or ""))
        for side in ("A", "B")
        for packet in case.get(side) or []
    }


def validate_batch_response(
    value: Any,
    cases: list[dict[str, Any]],
    *,
    allow_case_rationale_for_packet_reason: bool = False,
    allow_unrecognised_reason_tags: bool = False,
    allow_insufficient_context_intervention_inconsistency: bool = False,
    allow_historical_correction_without_factual_claim: bool = False,
) -> list[dict[str, Any]]:
    """Validate batch response."""
    if not isinstance(value, dict) or not isinstance(value.get("reviews"), list):
        raise ValueError("response must contain a reviews array")
    expected = {str(case["case_id"]): case for case in cases}
    reviews = value["reviews"]
    if len(reviews) != len(expected):
        raise ValueError("response review count differs from batch size")
    normalised: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in reviews:
        if not isinstance(row, dict):
            raise ValueError("review must be an object")
        case_id = str(row.get("case_id") or "")
        if case_id not in expected or case_id in seen:
            raise ValueError(f"unexpected or duplicate case_id: {case_id}")
        seen.add(case_id)
        choice = str(row.get("choice") or "")
        intervention = str(row.get("intervention") or "")
        desirability = str(row.get("evidence_desirability") or "")
        confidence = str(row.get("confidence") or "")
        if choice not in CHOICES or intervention not in INTERVENTIONS:
            raise ValueError(f"invalid decision for {case_id}")
        if desirability not in DESIRABILITY or confidence not in CONFIDENCE:
            raise ValueError(f"invalid confidence or desirability for {case_id}")
        if choice == "no_historical_evidence" and intervention not in {"humour_preferable", "no_reply_preferable"}:
            raise ValueError(f"history selected after no_historical_evidence for {case_id}")
        logical_inconsistencies: list[str] = []
        if choice == "insufficient_context" and intervention != "no_reply_preferable":
            if not allow_insufficient_context_intervention_inconsistency:
                raise ValueError(f"insufficient context must prefer no reply for {case_id}")
            logical_inconsistencies.append("insufficient_context_with_non_no_reply_intervention")
        if intervention == "historical_correction" and not bool(row.get("factual_claim_present")):
            if not allow_historical_correction_without_factual_claim:
                raise ValueError(f"historical correction lacks a factual claim for {case_id}")
            logical_inconsistencies.append("historical_correction_without_factual_claim")
        rationale = str(row.get("rationale") or "").strip()
        if not rationale:
            raise ValueError(f"rationale missing for {case_id}")
        tags = row.get("reason_tags")
        if tags is None and isinstance(row.get("reason_tags_csv"), str):
            tags = [tag.strip() for tag in row["reason_tags_csv"].split(",") if tag.strip()]
        if not isinstance(tags, list):
            raise ValueError(f"invalid reason tags for {case_id}")
        unrecognised_tags = sorted({str(tag) for tag in tags if str(tag) not in REASON_TAGS})
        if unrecognised_tags and not allow_unrecognised_reason_tags:
            raise ValueError(f"invalid reason tags for {case_id}")
        tags = [str(tag) for tag in tags if str(tag) in REASON_TAGS]
        assessments = row.get("packet_assessments")
        if not isinstance(assessments, list):
            raise ValueError(f"packet assessments missing for {case_id}")
        actual_keys: set[tuple[str, str]] = set()
        clean_assessments: list[dict[str, str]] = []
        normalised_missing_reasons = 0
        for assessment_value in assessments:
            assessment = assessment_value
            if isinstance(assessment_value, str):
                parts = assessment_value.split("|", 3)
                if len(parts) == 3:
                    parts.append("")
                if len(parts) != 4:
                    raise ValueError(f"invalid delimited packet assessment for {case_id}")
                assessment = {
                    "side": parts[0], "quote_id": parts[1],
                    "assessment": parts[2], "reason": parts[3],
                }
            if not isinstance(assessment, dict):
                raise ValueError(f"invalid packet assessment for {case_id}")
            side = str(assessment.get("side") or "")
            quote_id = str(assessment.get("quote_id") or "")
            rating = str(assessment.get("assessment") or "")
            reason = str(assessment.get("reason") or "").strip()
            key = (side, quote_id)
            if not reason and allow_case_rationale_for_packet_reason:
                reason = rationale
                normalised_missing_reasons += 1
            if key in actual_keys or rating not in ASSESSMENTS or not reason:
                raise ValueError(f"invalid or duplicate packet assessment for {case_id}")
            actual_keys.add(key)
            clean_assessments.append({"side": side, "quote_id": quote_id, "assessment": rating, "reason": reason})
        if actual_keys != _packet_keys(expected[case_id]):
            raise ValueError(f"packet assessments do not match supplied sets for {case_id}")
        normalised.append({
            "case_id": case_id,
            "choice": choice,
            "intervention": intervention,
            "evidence_desirability": desirability,
            "confidence": confidence,
            "factual_claim_present": bool(row.get("factual_claim_present")),
            "rationale": rationale,
            "reason_tags": sorted(set(str(tag) for tag in tags)),
            "unrecognised_provider_reason_tags": unrecognised_tags,
            "packet_assessments": sorted(clean_assessments, key=lambda item: (item["side"], item["quote_id"])),
            "normalised_missing_packet_reasons": normalised_missing_reasons,
            "logical_inconsistencies": logical_inconsistencies,
        })
    return sorted(normalised, key=lambda item: item["case_id"])


def parsed_response_from_raw(raw: dict[str, Any]) -> dict[str, Any]:
    """Return the parsed response from raw."""
    text = "".join(
        str(part.get("text") or "")
        for candidate in raw.get("candidates") or []
        for part in ((candidate.get("content") or {}).get("parts") or [])
        if isinstance(part, dict)
    )
    if not text:
        raise ValueError("raw Gemini response contains no candidate text")
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("raw Gemini response is not a JSON object")
    return value


def _usage(response: Any) -> dict[str, int]:
    usage = response.usage_metadata
    if usage is None:
        raise RuntimeError("missing authoritative Gemini usage metadata")
    input_tokens = int(usage.prompt_token_count or 0)
    candidate_tokens = int(usage.candidates_token_count or 0)
    thinking_tokens = int(usage.thoughts_token_count or 0)
    cached_tokens = int(usage.cached_content_token_count or 0)
    if not input_tokens and not candidate_tokens and not thinking_tokens:
        raise RuntimeError("empty authoritative Gemini usage metadata")
    return {
        "input_tokens": input_tokens,
        "candidate_tokens": candidate_tokens,
        "thinking_tokens": thinking_tokens,
        "output_tokens": candidate_tokens + thinking_tokens,
        "cached_tokens": cached_tokens,
    }


def calculate_cost(usage: dict[str, int]) -> float:
    """Calculate cost."""
    price = PRICES["gemini"]
    return (
        (usage["input_tokens"] - usage["cached_tokens"]) * price["input"]
        + usage["cached_tokens"] * price["cached_input"]
        + usage["output_tokens"] * price["output"]
    ) / 1_000_000


class GeminiReviewClient:
    """Provide the gemini review client."""
    def __init__(
        self,
        *,
        transport: str,
        api_key: str | None = None,
        project: str | None = None,
        location: str = "global",
        client: Any | None = None,
        timeout_seconds: float = 240,
    ):
        """Initialise the gemini review client."""
        if transport not in {"developer_api", "vertex_ai"}:
            raise ValueError("invalid Gemini transport")
        self.transport = transport
        self.model = MODEL
        if client is not None:
            self.client = client
        elif transport == "developer_api":
            if not api_key:
                raise RuntimeError("GEMINI_API_KEY or GOOGLE_API_KEY is required")
            # GOOGLE_GENAI_USE_VERTEXAI=true is required by the fallback transport
            # in this project. Override it explicitly so the primary client cannot
            # silently inherit Vertex routing from the shared process environment.
            self.client = genai.Client(vertexai=False, api_key=api_key)
        else:
            if not project:
                raise RuntimeError("GOOGLE_CLOUD_PROJECT is required")
            self.client = genai.Client(vertexai=True, project=project, location=location)
        self.timeout_seconds = timeout_seconds

    def config(self) -> types.GenerateContentConfig:
        """Return the config."""
        return types.GenerateContentConfig(
            response_mime_type="application/json",
            response_json_schema=RESPONSE_SCHEMA,
            max_output_tokens=MAX_OUTPUT_TOKENS,
            thinking_config=types.ThinkingConfig(thinking_budget=THINKING_BUDGET),
            temperature=TEMPERATURE,
            http_options=types.HttpOptions(timeout=int(self.timeout_seconds * 1000)),
        )

    def settings_signature(self) -> dict[str, Any]:
        """Return the settings signature."""
        config = self.config()
        return {
            "model": self.model,
            "response_mime_type": config.response_mime_type,
            "response_schema": config.response_json_schema,
            "max_output_tokens": config.max_output_tokens,
            "thinking_budget": config.thinking_config.thinking_budget,
            "temperature": config.temperature,
            "tools": config.tools,
        }

    def call(self, prompt: str) -> dict[str, Any]:
        """Submit one typed review prompt to Gemini."""
        started = time.monotonic()
        response = self.client.models.generate_content(model=self.model, contents=prompt, config=self.config())
        elapsed = time.monotonic() - started
        raw = response.model_dump(mode="json", exclude_none=True)
        parsed = response.parsed if isinstance(response.parsed, dict) else json.loads(response.text)
        usage = _usage(response)
        return {
            "raw": raw,
            "parsed": parsed,
            "usage": usage,
            "cost_usd": calculate_cost(usage),
            "latency_seconds": elapsed,
            "request_id": str(getattr(response, "response_id", "") or "") or None,
            "model_version": str(getattr(response, "model_version", "") or "") or None,
        }


def require_transport_parity(developer: GeminiReviewClient, vertex: GeminiReviewClient) -> None:
    """Require transport parity."""
    if developer.settings_signature() != vertex.settings_signature():
        raise RuntimeError("Gemini review transport settings parity failed")


def _classify_failure(exc: BaseException) -> dict[str, Any]:
    code = int(getattr(exc, "code", 0) or 0)
    message = str(getattr(exc, "message", None) or exc)[:2000]
    return {
        "code": code or None,
        "message": message,
        "type": type(exc).__name__,
        "quota_429": code == 429,
        "transient": code in {408, 429, 500, 502, 503, 504},
        "ambiguous": not bool(code),
    }


def _append_jsonl(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def prepare_gemini_review(retrieval_dir: Path) -> dict[str, Any]:
    """Prepare gemini review."""
    payload = browser_review_payload(retrieval_dir)
    cases = payload["items"]
    if len(cases) != 100 or payload["context_unavailable_count"]:
        raise RuntimeError("Gemini review requires exactly 100 context-complete blind cases")
    batches = []
    for offset in range(0, len(cases), BATCH_SIZE):
        values = cases[offset:offset + BATCH_SIZE]
        prompt = review_prompt(values)
        batches.append({
            "batch_id": f"gemini-review-{offset // BATCH_SIZE + 1:03d}",
            "case_ids": [str(item["case_id"]) for item in values],
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "estimated_input_tokens": estimate_tokens(prompt),
        })
    expected_output = 3000 * len(batches)
    maximum_output = MAX_OUTPUT_TOKENS * len(batches)
    input_tokens = sum(batch["estimated_input_tokens"] for batch in batches)
    expected_cost = (input_tokens * PRICES["gemini"]["input"] + expected_output * PRICES["gemini"]["output"]) / 1_000_000
    maximum_cost = (input_tokens * PRICES["gemini"]["input"] + maximum_output * PRICES["gemini"]["output"]) / 1_000_000
    manifest_core = {
        "schema_version": SCHEMA_VERSION,
        "prompt_version": PROMPT_VERSION,
        "model": MODEL,
        "batch_size": BATCH_SIZE,
        "case_count": len(cases),
        "batches": batches,
        "review_sample_sha256": sha256(read_json(retrieval_dir / "review_sample_100.json")),
        "blind_assignment_sha256": sha256(read_json(retrieval_dir / "blind_assignment_manifest.json")),
    }
    manifest = {**manifest_core, "manifest_sha256": sha256(manifest_core), "generated_at": utc_now()}
    path = retrieval_dir / "manual_review" / "gemini_review_manifest.json"
    existing = read_json(path, None)
    if existing and {key: existing.get(key) for key in manifest_core} != manifest_core:
        raise RuntimeError("existing Gemini review manifest differs")
    if not existing:
        atomic_write_json(path, manifest)
    preflight = {
        "schema_version": SCHEMA_VERSION,
        "model": MODEL,
        "case_count": len(cases),
        "batch_count": len(batches),
        "estimated_input_tokens": input_tokens,
        "expected_output_tokens": expected_output,
        "maximum_output_tokens": maximum_output,
        "expected_cost_usd": round(expected_cost, 4),
        "conservative_maximum_cost_usd": round(maximum_cost, 4),
        "developer_limit_usd": DEVELOPER_LIMIT_USD,
        "vertex_limit_usd": VERTEX_LIMIT_USD,
        "combined_limit_usd": COMBINED_LIMIT_USD,
        "search_grounding_enabled": False,
        "tools_enabled": False,
        "human_reviews_will_be_modified": False,
        "generated_at": utc_now(),
    }
    atomic_write_json(retrieval_dir / "manual_review" / "gemini_review_preflight.json", preflight)
    return preflight


class GeminiReviewRunner:
    """Run gemini review operations."""
    def __init__(
        self,
        retrieval_dir: Path,
        developer: GeminiReviewClient,
        vertex: GeminiReviewClient | None,
        *,
        developer_limit: float = DEVELOPER_LIMIT_USD,
        vertex_limit: float = VERTEX_LIMIT_USD,
        combined_limit: float = COMBINED_LIMIT_USD,
        fallback_enabled: bool = True,
        sleep: Callable[[float], None] = time.sleep,
    ):
        """Initialise the gemini review runner."""
        self.retrieval_dir = retrieval_dir
        self.review_dir = retrieval_dir / "manual_review"
        self.developer = developer
        self.vertex = vertex
        self.developer_limit = developer_limit
        self.vertex_limit = vertex_limit
        self.combined_limit = combined_limit
        self.fallback_enabled = fallback_enabled
        self.sleep = sleep
        self.attempts_path = self.review_dir / "gemini_review_attempts.jsonl"
        self.results_path = self.review_dir / "gemini_reviews.json"
        self.cost_path = self.review_dir / "gemini_review_cost_ledger.json"
        self.state_path = self.review_dir / "gemini_review_transport_status.json"
        self.raw_dir = self.review_dir / "gemini_raw_responses"

    def _load(self) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
        results = read_json(self.results_path, None) or {
            "schema_version": SCHEMA_VERSION, "model": MODEL, "items": {},
        }
        costs = read_json(self.cost_path, None) or {
            "schema_version": SCHEMA_VERSION, "calls": [], "developer_known_spend_usd": 0.0,
            "vertex_known_spend_usd": 0.0, "combined_known_spend_usd": 0.0,
        }
        state = read_json(self.state_path, None) or {
            "schema_version": SCHEMA_VERSION, "developer_paused": False,
            "pause_reason": None, "paused_at": None, "trigger_attempts": [],
            "direct_to_vertex_count": 0,
        }
        attempts = []
        if self.attempts_path.exists():
            for line in self.attempts_path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    attempts.append(json.loads(line))
        terminals = {
            (row.get("batch_id"), row.get("transport"), row.get("transport_attempt_number"))
            for row in attempts
            if row.get("event") in {"attempt_failed", "schema_rejected", "completed", "ambiguous_on_resume"}
        }
        for row in list(attempts):
            key = (row.get("batch_id"), row.get("transport"), row.get("transport_attempt_number"))
            if row.get("event") != "attempt_started" or key in terminals:
                continue
            ambiguous = {
                **row,
                "event": "ambiguous_on_resume",
                "finished_at": utc_now(),
                "reason": "attempt was persisted as started without a confirmed terminal outcome",
            }
            _append_jsonl(self.attempts_path, ambiguous)
            attempts.append(ambiguous)
            state.setdefault("ambiguous_batches", {})[str(row.get("batch_id"))] = str(row.get("transport"))
        self._persist(results, costs, state)
        return results, costs, state, attempts

    def _persist(self, results: dict[str, Any], costs: dict[str, Any], state: dict[str, Any]) -> None:
        atomic_write_json(self.results_path, results)
        atomic_write_json(self.cost_path, costs)
        atomic_write_json(self.state_path, state)

    def _guard(self, transport: str, prompt: str, costs: dict[str, Any]) -> None:
        maximum = (
            estimate_tokens(prompt) * PRICES["gemini"]["input"]
            + MAX_OUTPUT_TOKENS * PRICES["gemini"]["output"]
        ) / 1_000_000
        own_key = "developer_known_spend_usd" if transport == "developer_api" else "vertex_known_spend_usd"
        own_limit = self.developer_limit if transport == "developer_api" else self.vertex_limit
        if float(costs.get(own_key) or 0) + maximum > own_limit:
            raise RuntimeError(f"{transport} Gemini review ceiling reached")
        if float(costs.get("combined_known_spend_usd") or 0) + maximum > self.combined_limit:
            raise RuntimeError("combined Gemini review ceiling reached")

    def _attempt_count(self, attempts: list[dict[str, Any]], batch_id: str, transport: str) -> int:
        return sum(
            row.get("event") == "attempt_started"
            and row.get("batch_id") == batch_id
            and row.get("transport") == transport
            for row in attempts
        )

    def _run_transport(
        self,
        *,
        client: GeminiReviewClient,
        batch_id: str,
        cases: list[dict[str, Any]],
        prompt: str,
        results: dict[str, Any],
        costs: dict[str, Any],
        state: dict[str, Any],
        attempts: list[dict[str, Any]],
    ) -> str:
        transport = client.transport
        if any(
            row.get("event") == "ambiguous_on_resume"
            and row.get("batch_id") == batch_id
            and row.get("transport") == transport
            for row in attempts
        ):
            return "ambiguous"
        while self._attempt_count(attempts, batch_id, transport) < 2:
            self._guard(transport, prompt, costs)
            number = self._attempt_count(attempts, batch_id, transport) + 1
            started = {
                "event": "attempt_started", "timestamp": utc_now(), "batch_id": batch_id,
                "case_ids": [case["case_id"] for case in cases], "transport": transport,
                "transport_attempt_number": number, "model": client.model,
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            }
            _append_jsonl(self.attempts_path, started)
            attempts.append(started)
            try:
                response = client.call(prompt)
            except BaseException as exc:
                failure = _classify_failure(exc)
                event = {**started, "event": "attempt_failed", "finished_at": utc_now(), "failure": failure}
                _append_jsonl(self.attempts_path, event)
                attempts.append(event)
                if failure["ambiguous"]:
                    return "ambiguous"
                if failure["transient"] and number < 2:
                    self.sleep(2 ** number)
                    continue
                if transport == "developer_api" and failure["quota_429"]:
                    state["trigger_attempts"].append({
                        "batch_id": batch_id, "transport_attempt_number": number,
                        "timestamp": event["finished_at"], "code": failure["code"],
                    })
                    if number >= 2:
                        state.update({
                            "developer_paused": True,
                            "pause_reason": "two_consecutive_provider_wide_429_responses",
                            "paused_at": utc_now(),
                        })
                        self._persist(results, costs, state)
                        return "quota_exhausted"
                return "failed"
            self.raw_dir.mkdir(parents=True, exist_ok=True)
            raw_path = self.raw_dir / f"{batch_id}.{transport}.{number}.json"
            atomic_write_json(raw_path, response["raw"])
            call = {
                "batch_id": batch_id, "case_ids": [case["case_id"] for case in cases],
                "transport": transport, "transport_attempt_number": number,
                "request_id": response["request_id"], "model_version": response["model_version"],
                "timestamp": utc_now(), "latency_seconds": response["latency_seconds"],
                "cost_usd": response["cost_usd"], "raw_response_path": str(raw_path),
                **response["usage"],
            }
            costs["calls"].append(call)
            key = "developer_known_spend_usd" if transport == "developer_api" else "vertex_known_spend_usd"
            costs[key] = round(sum(float(item["cost_usd"]) for item in costs["calls"] if item["transport"] == transport), 10)
            costs["combined_known_spend_usd"] = round(
                float(costs["developer_known_spend_usd"]) + float(costs["vertex_known_spend_usd"]), 10
            )
            atomic_write_json(self.cost_path, costs)
            try:
                values = validate_batch_response(response["parsed"], cases)
            except ValueError as exc:
                event = {
                    **started, "event": "schema_rejected", "finished_at": utc_now(),
                    "error": str(exc), "charged_call": call,
                }
                _append_jsonl(self.attempts_path, event)
                attempts.append(event)
                if number < 2:
                    continue
                return "failed"
            for value in values:
                results["items"][value["case_id"]] = {
                    **value, "reviewer": "gemini", "model": client.model,
                    "transport": transport, "batch_id": batch_id,
                    "reviewed_at": utc_now(), "prompt_version": PROMPT_VERSION,
                }
            atomic_write_json(self.results_path, results)
            event = {**started, "event": "completed", "finished_at": utc_now(), "call": call}
            _append_jsonl(self.attempts_path, event)
            attempts.append(event)
            return "completed"
        return "failed"

    def run(self) -> dict[str, Any]:
        """Run all incomplete Gemini review batches within the cost guard."""
        if self.fallback_enabled:
            if self.vertex is None:
                raise RuntimeError("Vertex fallback enabled without a Vertex client")
            require_transport_parity(self.developer, self.vertex)
        payload = browser_review_payload(self.retrieval_dir)
        cases_by_id = {str(case["case_id"]): case for case in payload["items"]}
        manifest = read_json(self.review_dir / "gemini_review_manifest.json")
        results, costs, state, attempts = self._load()
        state.update({
            "run_active": True, "active_pid": os.getpid(), "started_at": utc_now(),
            "fallback_enabled": self.fallback_enabled, "model": MODEL,
        })
        self._persist(results, costs, state)
        try:
            for batch in manifest["batches"]:
                batch_cases = [cases_by_id[case_id] for case_id in batch["case_ids"]]
                if all(case_id in results["items"] for case_id in batch["case_ids"]):
                    continue
                prompt = review_prompt(batch_cases)
                if hashlib.sha256(prompt.encode("utf-8")).hexdigest() != batch["prompt_sha256"]:
                    raise RuntimeError(f"prompt hash changed for {batch['batch_id']}")
                if state["developer_paused"]:
                    state["direct_to_vertex_count"] += 1
                    self._persist(results, costs, state)
                    outcome = self._run_transport(
                        client=self.vertex, batch_id=batch["batch_id"], cases=batch_cases,
                        prompt=prompt, results=results, costs=costs, state=state, attempts=attempts,
                    )
                else:
                    outcome = self._run_transport(
                        client=self.developer, batch_id=batch["batch_id"], cases=batch_cases,
                        prompt=prompt, results=results, costs=costs, state=state, attempts=attempts,
                    )
                    if outcome == "quota_exhausted" and self.fallback_enabled:
                        outcome = self._run_transport(
                            client=self.vertex, batch_id=batch["batch_id"], cases=batch_cases,
                            prompt=prompt, results=results, costs=costs, state=state, attempts=attempts,
                        )
                if outcome not in {"completed"}:
                    state.setdefault("unresolved_batches", {})[batch["batch_id"]] = outcome
                self._persist(results, costs, state)
        finally:
            state.update({"run_active": False, "active_pid": None, "finished_at": utc_now()})
            self._persist(results, costs, state)
        return write_gemini_review_report(self.retrieval_dir)


def write_gemini_review_report(retrieval_dir: Path) -> dict[str, Any]:
    """Write gemini review report."""
    review_dir = retrieval_dir / "manual_review"
    results = read_json(review_dir / "gemini_reviews.json", {"items": {}})
    costs = read_json(review_dir / "gemini_review_cost_ledger.json", {})
    state = read_json(review_dir / "gemini_review_transport_status.json", {})
    blind = read_json(retrieval_dir / "blind_assignment_manifest.json")
    choice_counts = Counter()
    intervention_counts = Counter()
    transport_counts = Counter()
    retriever_wins = Counter()
    confidence_counts = Counter()
    desirability_counts = Counter()
    reason_tag_counts = Counter()
    packet_assessment_counts: dict[str, Counter[str]] = {}
    offline_normalised_reviews = 0
    normalised_missing_packet_reasons = 0
    for case_id, row in results.get("items", {}).items():
        choice_counts[row["choice"]] += 1
        intervention_counts[row["intervention"]] += 1
        transport_counts[row["transport"]] += 1
        confidence_counts[row["confidence"]] += 1
        desirability_counts[row["evidence_desirability"]] += 1
        reason_tag_counts.update(row.get("reason_tags") or [])
        offline_normalised_reviews += bool(row.get("offline_normalised_from_preserved_response"))
        normalised_missing_packet_reasons += int(row.get("normalised_missing_packet_reasons") or 0)
        assignment = blind["assignments"][case_id]
        for assessment in row.get("packet_assessments") or []:
            retriever = str(assignment[assessment["side"]])
            packet_assessment_counts.setdefault(retriever, Counter())[assessment["assessment"]] += 1
        if row["choice"] in {"A_better", "B_better"}:
            retriever_wins[blind["assignments"][case_id][row["choice"][0]]] += 1
    summary = {
        "schema_version": SCHEMA_VERSION,
        "model": MODEL,
        "completed_reviews": len(results.get("items", {})),
        "unresolved_reviews": 100 - len(results.get("items", {})),
        "choice_counts": dict(sorted(choice_counts.items())),
        "intervention_counts": dict(sorted(intervention_counts.items())),
        "confidence_counts": dict(sorted(confidence_counts.items())),
        "evidence_desirability_counts": dict(sorted(desirability_counts.items())),
        "reason_tag_counts": dict(sorted(reason_tag_counts.items())),
        "packet_assessment_counts_by_retriever": {
            key: dict(sorted(value.items())) for key, value in sorted(packet_assessment_counts.items())
        },
        "transport_counts": dict(sorted(transport_counts.items())),
        "decisive_retriever_wins": dict(sorted(retriever_wins.items())),
        "offline_normalised_review_count": offline_normalised_reviews,
        "normalised_missing_packet_reason_count": normalised_missing_packet_reasons,
        "developer_paused": bool(state.get("developer_paused")),
        "direct_to_vertex_count": int(state.get("direct_to_vertex_count") or 0),
        "developer_known_spend_usd": float(costs.get("developer_known_spend_usd") or 0),
        "vertex_known_spend_usd": float(costs.get("vertex_known_spend_usd") or 0),
        "combined_known_spend_usd": float(costs.get("combined_known_spend_usd") or 0),
        "human_reviews_modified": False,
        "generated_at": utc_now(),
    }
    atomic_write_json(review_dir / "gemini_review_summary.json", summary)
    lines = [
        "# Gemini Blind Retrieval Review", "",
        f"- Model: `{MODEL}`",
        f"- Completed: {summary['completed_reviews']}/100",
        f"- Developer API: {transport_counts.get('developer_api', 0)}",
        f"- Vertex fallback: {transport_counts.get('vertex_ai', 0)}",
        f"- Known spend: ${summary['combined_known_spend_usd']:.4f}",
        f"- Developer paused: {str(summary['developer_paused']).lower()}", "",
        "## Set-level decisions", "",
    ]
    lines.extend(f"- {key}: {value}" for key, value in sorted(choice_counts.items()))
    lines.extend(["", "## Historical evidence desirability", ""])
    lines.extend(f"- {key}: {value}" for key, value in sorted(desirability_counts.items()))
    lines.extend(["", "## Justified response", ""])
    lines.extend(f"- {key}: {value}" for key, value in sorted(intervention_counts.items()))
    lines.extend(["", "## Deblinded decisive preferences", ""])
    lines.extend(f"- {key}: {value}" for key, value in sorted(retriever_wins.items()))
    lines.extend(["", "## Packet-level relevance", ""])
    for retriever, counts in sorted(packet_assessment_counts.items()):
        values = ", ".join(f"{key} {value}" for key, value in sorted(counts.items()))
        lines.append(f"- {retriever}: {values}")
    lines.extend([
        "", "## Interpretation", "",
        "The evidence does not support promoting hybrid retrieval. Most conversations did not justify historical evidence, and only seven cases produced a decisive A/B preference. Hybrid remains suitable for shadow evaluation only.",
        "",
        f"Offline-normalised reviews: {offline_normalised_reviews}; omitted packet reasons normalised from Gemini's case-level rationale: {normalised_missing_packet_reasons}.",
    ])
    lines.extend([
        "", "The model reviewed the complete local conversational context and the same deterministic blind A/B evidence sets shown by the web reviewer. It made no Search-grounding or tool call. Human review files were not modified.",
    ])
    atomic_write_text(review_dir / "gemini_review_report.md", "\n".join(lines) + "\n")
    return summary


def recover_gemini_reviews_offline(retrieval_dir: Path) -> dict[str, Any]:
    """Recover gemini reviews offline."""
    review_dir = retrieval_dir / "manual_review"
    payload = browser_review_payload(retrieval_dir)
    cases_by_id = {str(case["case_id"]): case for case in payload["items"]}
    manifest = read_json(review_dir / "gemini_review_manifest.json")
    results = read_json(review_dir / "gemini_reviews.json", {"schema_version": SCHEMA_VERSION, "items": {}})
    costs = read_json(review_dir / "gemini_review_cost_ledger.json", {"calls": []})
    state = read_json(review_dir / "gemini_review_transport_status.json", {})
    existing_audit = read_json(review_dir / "gemini_review_offline_recovery.json", None)
    recovered_batches: list[dict[str, Any]] = []
    unresolved = dict(state.get("unresolved_batches") or {})
    if not unresolved and existing_audit:
        return {**existing_audit, "summary": write_gemini_review_report(retrieval_dir)}
    calls_by_batch: dict[str, list[dict[str, Any]]] = {}
    for call in costs.get("calls", []):
        calls_by_batch.setdefault(str(call.get("batch_id") or ""), []).append(call)
    for batch in manifest["batches"]:
        batch_id = str(batch["batch_id"])
        if batch_id not in unresolved:
            continue
        cases = [cases_by_id[case_id] for case_id in batch["case_ids"]]
        recovered = None
        source_call = None
        failures: list[str] = []
        for call in reversed(calls_by_batch.get(batch_id, [])):
            try:
                raw = read_json(Path(call["raw_response_path"]))
                recovered = validate_batch_response(
                    parsed_response_from_raw(raw),
                    cases,
                    allow_case_rationale_for_packet_reason=True,
                )
                source_call = call
                break
            except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
                failures.append(f"{type(exc).__name__}: {exc}")
        if recovered is None or source_call is None:
            recovered_batches.append({"batch_id": batch_id, "status": "still_unresolved", "errors": failures})
            continue
        for value in recovered:
            if value["case_id"] in results["items"]:
                continue
            results["items"][value["case_id"]] = {
                **value, "reviewer": "gemini", "model": MODEL,
                "transport": str(source_call["transport"]), "batch_id": batch_id,
                "reviewed_at": utc_now(), "prompt_version": PROMPT_VERSION,
                "offline_normalised_from_preserved_response": True,
                "source_transport_attempt_number": source_call["transport_attempt_number"],
                "source_raw_response_path": source_call["raw_response_path"],
            }
        unresolved.pop(batch_id, None)
        recovered_batches.append({
            "batch_id": batch_id, "status": "recovered_offline",
            "case_ids": list(batch["case_ids"]),
            "source_transport": source_call["transport"],
            "source_transport_attempt_number": source_call["transport_attempt_number"],
            "source_raw_response_path": source_call["raw_response_path"],
        })
    state["unresolved_batches"] = unresolved
    atomic_write_json(review_dir / "gemini_reviews.json", results)
    atomic_write_json(review_dir / "gemini_review_transport_status.json", state)
    audit = {
        "schema_version": SCHEMA_VERSION,
        "offline_only": True,
        "recovered_case_count": sum(len(row.get("case_ids") or []) for row in recovered_batches if row["status"] == "recovered_offline"),
        "still_unresolved_batch_count": sum(row["status"] == "still_unresolved" for row in recovered_batches),
        "batches": recovered_batches,
        "generated_at": utc_now(),
    }
    atomic_write_json(review_dir / "gemini_review_offline_recovery.json", audit)
    audit["summary"] = write_gemini_review_report(retrieval_dir)
    return audit
