#!/usr/bin/env python3
"""AI-first conversational reply proposal, evidence and independent review.

Natural-language interpretation belongs to the proposer and reviewer models.
This module supplies strict schemas, source-integrity checks and operational
limits; it does not infer meaning from hand-maintained keyword rules.
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import ipaddress
import json
import os
import re
import tempfile
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from reply_evidence import EvidencePassage, EvidenceRepository, value_hash


STRATEGY_VERSION = "ai-first-reply-v3"
DRAFT_SCHEMA_VERSION = 3
PROPOSER_PROMPT_VERSION = "ai-first-proposer-v6"
EVIDENCE_PROMPT_VERSION = "claim-evidence-entailment-v4"
REVIEWER_PROMPT_VERSION = "independent-reply-reviewer-v3"
LEGACY_DRAFT_AUDIT_SCHEMA_VERSION = 1

MODES = {
    "direct_factual_answer",
    "opinion_or_principle",
    "light_humour",
    "courtesy",
    "no_reply",
}
TONES = {"firm", "dry", "wry", "warm", "neutral", "light", "none"}
CONFIDENCE_LEVELS = {"low": 1, "medium": 2, "high": 3}
EVIDENCE_VERDICTS = {"supports", "contradicts", "insufficient"}
REVIEWER_VERDICTS = {"approve", "reject", "revise"}
LANES = {"mention", "hot_post_reply", "quote_tweet"}

URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.IGNORECASE)
DOMAIN_RE = re.compile(
    r"(?<![@A-Za-z0-9_])(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,62}[A-Za-z0-9])?\.)+"
    r"[A-Za-z]{2,63}(?:/\S*)?",
    re.IGNORECASE,
)
EMAIL_RE = re.compile(
    r"(?<![A-Za-z0-9._%+\-])[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,63}(?![A-Za-z0-9_])",
    re.IGNORECASE,
)
DOMAIN_CANDIDATE_RE = re.compile(
    r"(?<![@\w])(?:[^\W_][\w-]{0,62}\.)+[^\W_][\w-]{1,62}(?=$|[^\w-])",
    re.UNICODE,
)
IPV4_CANDIDATE_RE = re.compile(r"(?<![\w.])(?:[0-9]{1,3}\.){3}[0-9]{1,3}(?![\w.])")
IDNA_DOT_TRANSLATION = str.maketrans({"\u3002": ".", "\uff0e": ".", "\uff61": "."})
BLOCKED_REPLY_PATTERNS = (
    re.compile(r"\b(?:kill|suicide|shoot|stab)\b", re.IGNORECASE),
    re.compile(r"\b(?:go|should|deserves? to)\s+(?:die|hang)\b", re.IGNORECASE),
    re.compile(r"\btraitor should\b", re.IGNORECASE),
    re.compile(r"\bi am margaret thatcher\b", re.IGNORECASE),
    re.compile(r"\bas margaret thatcher\b", re.IGNORECASE),
)


class ReplyPipelineError(RuntimeError):
    """A local pipeline invariant failed; the caller must fail closed."""


class ModelCallLimitError(ReplyPipelineError):
    """The configured per-candidate model-call ceiling was reached."""


class AIReply(str):
    """A reviewer-approved reply carrying its immutable V3 draft record."""

    draft_record: dict[str, Any]
    pipeline_metadata: dict[str, Any]

    def __new__(
        cls,
        value: str,
        draft_record: dict[str, Any],
        pipeline_metadata: dict[str, Any],
    ) -> "AIReply":
        instance = str.__new__(cls, value)
        instance.draft_record = draft_record
        instance.pipeline_metadata = pipeline_metadata
        return instance


@dataclass(frozen=True)
class PipelineResult:
    """Final result of one bounded conversational reply pipeline."""

    reply: AIReply | None
    status: str
    reason: str
    model_call_count: int
    revision_count: int
    audit: tuple[dict[str, Any], ...]


ModelTransport = Callable[..., object]


def utc_now() -> str:
    """Return a UTC ISO-8601 timestamp."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def text_hash(text: str) -> str:
    """Return the SHA-256 digest of exact UTF-8 text."""
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def atomic_write_json(path: Path, value: Any) -> None:
    """Write JSON atomically and durably."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def stable_read_bytes(path: Path, attempts: int = 5) -> bytes:
    """Read a mutable local file only when its identity and metadata stay stable."""
    for _attempt in range(attempts):
        before = path.stat()
        content = path.read_bytes()
        after = path.stat()
        if (
            before.st_ino == after.st_ino
            and before.st_size == after.st_size == len(content)
            and before.st_mtime_ns == after.st_mtime_ns
        ):
            return content
        time.sleep(0.02)
    raise RuntimeError(f"could not obtain a stable read of {path}")


def _strict_object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def claim_schema() -> dict[str, Any]:
    """Return the strict proposer factual-claim schema."""
    properties = {
        "claim_id": {"type": "string", "pattern": r"^claim-[1-9][0-9]*$"},
        "claim_text": {"type": "string", "minLength": 1, "maxLength": 500},
        "requires_evidence": {"type": "boolean"},
        "actor": {"type": "string", "maxLength": 200},
        "action_or_relationship": {"type": "string", "maxLength": 300},
        "direction_or_polarity": {"type": "string", "maxLength": 200},
        "date_or_period": {"type": "string", "maxLength": 160},
        "quantity": {"type": "string", "maxLength": 160},
    }
    return _strict_object(properties, list(properties))


def proposer_schema(maximum_reply_length: int, maximum_claims: int) -> dict[str, Any]:
    """Return the strict structured-output schema for a proposer call."""
    properties = {
        "mode": {"type": "string", "enum": sorted(MODES)},
        "interpretation": {"type": "string", "minLength": 1, "maxLength": 800},
        "proposed_reply": {"type": "string", "maxLength": maximum_reply_length},
        "factual_claims": {
            "type": "array",
            "items": claim_schema(),
            "maxItems": maximum_claims,
        },
        "exact_thatcher_wording_used": {"type": "boolean"},
        "exact_thatcher_wording": {"type": "string", "maxLength": maximum_reply_length},
        "tone": {"type": "string", "enum": sorted(TONES)},
        "confidence": {"type": "string", "enum": sorted(CONFIDENCE_LEVELS)},
        "no_reply_reason": {"type": "string", "maxLength": 500},
    }
    return _strict_object(properties, list(properties))


def evidence_schema(maximum_claims: int, maximum_references: int) -> dict[str, Any]:
    """Return the strict structured-output schema for claim adjudication."""
    reference = _strict_object(
        {
            "evidence_id": {"type": "string", "pattern": r"^[0-9a-f]{64}$"},
            "exact_supporting_passage": {"type": "string", "minLength": 1},
            "relation": {"type": "string", "enum": sorted(EVIDENCE_VERDICTS)},
        },
        ["evidence_id", "exact_supporting_passage", "relation"],
    )
    result = _strict_object(
        {
            "claim_id": {"type": "string", "pattern": r"^claim-[1-9][0-9]*$"},
            "claim_text": {"type": "string", "minLength": 1, "maxLength": 500},
            "verdict": {"type": "string", "enum": sorted(EVIDENCE_VERDICTS)},
            "evidence": {"type": "array", "items": reference, "maxItems": maximum_references},
            "actor": {"type": "string", "maxLength": 200},
            "action_or_relationship": {"type": "string", "maxLength": 300},
            "direction_or_polarity": {"type": "string", "maxLength": 200},
            "date_or_period": {"type": "string", "maxLength": 160},
            "quantity": {"type": "string", "maxLength": 160},
            "explanation": {"type": "string", "maxLength": 600},
        },
        [
            "claim_id", "claim_text", "verdict", "evidence", "actor",
            "action_or_relationship", "direction_or_polarity", "date_or_period",
            "quantity", "explanation",
        ],
    )
    return _strict_object(
        {"claims": {"type": "array", "items": result, "maxItems": maximum_claims}},
        ["claims"],
    )


def reviewer_schema(maximum_claims: int) -> dict[str, Any]:
    """Return the strict structured-output schema for independent review."""
    properties = {
        "verdict": {"type": "string", "enum": sorted(REVIEWER_VERDICTS)},
        "summary": {"type": "string", "maxLength": 800},
        "reasons": {"type": "array", "items": {"type": "string", "maxLength": 300}, "maxItems": 12},
        "actual_factual_claims": {"type": "array", "items": {"type": "string", "maxLength": 500}, "maxItems": maximum_claims},
        "unsupported_factual_claims": {"type": "array", "items": {"type": "string", "maxLength": 500}, "maxItems": maximum_claims},
        "direct_question_present": {"type": "boolean"},
        "answers_direct_question_first_sentence": {"type": "boolean"},
        "topically_relevant": {"type": "boolean"},
        "endorses_unsupported_allegation": {"type": "boolean"},
        "contains_unsupported_factual_claims": {"type": "boolean"},
        "actor_action_relationship_correct": {"type": "boolean"},
        "direction_polarity_correct": {"type": "boolean"},
        "dates_quantities_correct": {"type": "boolean"},
        "quotation_attribution_correct": {"type": "boolean"},
        "original_prose_clearly_not_historical_quotation": {"type": "boolean"},
        "mode_and_tone_match": {"type": "boolean"},
        "suitable_for_account": {"type": "boolean"},
        "revision_instructions": {"type": "string", "maxLength": 800},
    }
    return _strict_object(properties, list(properties))


def validate_strategy_config(config: object) -> list[str]:
    """Return all structural and fail-closed configuration errors."""
    if not isinstance(config, dict):
        return ["ai_first_reply_strategy must be an object"]
    expected = {
        "enabled", "strategy_version", "proposer_model", "reviewer_model",
        "evidence_model", "research_corpus_path", "maximum_model_calls",
        "proposer_timeout_seconds", "evidence_timeout_seconds",
        "reviewer_timeout_seconds", "proposer_max_output_tokens",
        "evidence_max_output_tokens", "reviewer_max_output_tokens",
        "maximum_revisions", "maximum_invalid_response_retries", "maximum_claims",
        "maximum_evidence_packets_per_claim",
        "maximum_evidence_passages_per_claim", "maximum_reply_sentences",
        "fail_closed",
    }
    errors: list[str] = []
    if set(config) != expected:
        errors.append("ai_first_reply_strategy fields mismatch")
        return errors
    if type(config.get("enabled")) is not bool:
        errors.append("ai_first_reply_strategy.enabled must be boolean")
    if config.get("strategy_version") != STRATEGY_VERSION:
        errors.append(f"ai_first_reply_strategy.strategy_version must be {STRATEGY_VERSION}")
    for key in ("proposer_model", "reviewer_model", "evidence_model", "research_corpus_path"):
        value = config.get(key)
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            errors.append(f"ai_first_reply_strategy.{key} must be a non-empty trimmed string")
    for key in (
        "maximum_model_calls", "proposer_timeout_seconds", "evidence_timeout_seconds",
        "reviewer_timeout_seconds", "proposer_max_output_tokens", "evidence_max_output_tokens",
        "reviewer_max_output_tokens", "maximum_claims", "maximum_evidence_packets_per_claim",
        "maximum_evidence_passages_per_claim", "maximum_reply_sentences",
    ):
        value = config.get(key)
        if type(value) is not int or value <= 0:
            errors.append(f"ai_first_reply_strategy.{key} must be a positive integer")
    if type(config.get("maximum_revisions")) is not int or config.get("maximum_revisions") != 1:
        errors.append("ai_first_reply_strategy.maximum_revisions must be exactly 1")
    if (
        type(config.get("maximum_invalid_response_retries")) is not int
        or config.get("maximum_invalid_response_retries") not in {0, 1}
    ):
        errors.append(
            "ai_first_reply_strategy.maximum_invalid_response_retries must be 0 or 1"
        )
    upper_limits = {
        "maximum_model_calls": 6,
        "maximum_claims": 8,
        "maximum_evidence_packets_per_claim": 10,
        "maximum_evidence_passages_per_claim": 40,
        "maximum_reply_sentences": 3,
        "proposer_timeout_seconds": 120,
        "evidence_timeout_seconds": 120,
        "reviewer_timeout_seconds": 120,
        "proposer_max_output_tokens": 2_000,
        "evidence_max_output_tokens": 4_000,
        "reviewer_max_output_tokens": 2_000,
    }
    for key, maximum in upper_limits.items():
        value = config.get(key)
        if type(value) is int and value > maximum:
            errors.append(f"ai_first_reply_strategy.{key} must not exceed {maximum}")
    if config.get("fail_closed") is not True:
        errors.append("ai_first_reply_strategy.fail_closed must remain true")
    return errors


def validate_reply_context(context: object) -> dict[str, Any]:
    """Validate and return a clean, explicitly separated reply context."""
    if not isinstance(context, dict):
        raise ValueError("reply context must be an object")
    required = {
        "target_id", "thread_id", "lane", "incoming_contribution",
        "quoted_post", "parent_thread", "clarification_request", "current_date",
    }
    if set(context) != required:
        raise ValueError("reply context fields mismatch")
    target_id = str(context.get("target_id") or "")
    thread_id = str(context.get("thread_id") or "")
    lane = context.get("lane")
    incoming = context.get("incoming_contribution")
    current_date = context.get("current_date")
    if not target_id or not thread_id:
        raise ValueError("reply context target and thread IDs are required")
    if lane not in LANES:
        raise ValueError("reply context lane is invalid")
    if not isinstance(incoming, str) or not incoming.strip() or len(incoming) > 10_000:
        raise ValueError("incoming contribution must be 1..10000 characters")
    if not isinstance(current_date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", current_date):
        raise ValueError("reply context current_date must be YYYY-MM-DD")

    quoted = context.get("quoted_post")
    if quoted is not None:
        _validate_context_post(quoted, "quoted_post")
    parents = context.get("parent_thread")
    if not isinstance(parents, list) or len(parents) > 3:
        raise ValueError("parent_thread must contain at most three posts")
    for parent in parents:
        _validate_context_post(parent, "parent_thread")
    clarification = context.get("clarification_request")
    if clarification is not None:
        if not isinstance(clarification, dict) or set(clarification) != {
            "original_question", "correction",
        }:
            raise ValueError("clarification_request fields mismatch")
        original_question = clarification.get("original_question")
        correction = clarification.get("correction")
        if (
            not isinstance(original_question, str)
            or not original_question.strip()
            or len(original_question) > 10_000
        ):
            raise ValueError("clarification original_question is invalid")
        if (
            not isinstance(correction, str)
            or not correction.strip()
            or correction != incoming
        ):
            raise ValueError("clarification correction must equal the incoming contribution")
    return json.loads(json.dumps(context, ensure_ascii=False))


def _validate_context_post(value: object, label: str) -> None:
    if not isinstance(value, dict) or set(value) != {"post_id", "author_role", "text"}:
        raise ValueError(f"{label} record fields mismatch")
    if not str(value.get("post_id") or ""):
        raise ValueError(f"{label} post_id is required")
    if value.get("author_role") not in {"account", "user", "unknown"}:
        raise ValueError(f"{label} author_role is invalid")
    if not isinstance(value.get("text"), str) or len(value["text"]) > 2_000:
        raise ValueError(f"{label} text is invalid")


def _parse_object(value: object, stage: str) -> dict[str, Any]:
    if isinstance(value, str):
        parsed = json.loads(value)
    else:
        parsed = value
    if not isinstance(parsed, dict):
        raise ValueError(f"{stage} response must be a JSON object")
    return parsed


def _validate_exact_keys(value: dict[str, Any], expected: set[str], stage: str) -> None:
    if set(value) != expected:
        missing = sorted(expected - set(value))
        extra = sorted(set(value) - expected)
        raise ValueError(f"{stage} fields mismatch missing={missing} extra={extra}")


def validate_proposer(value: object, *, maximum_reply_length: int, maximum_claims: int) -> dict[str, Any]:
    """Validate untrusted proposer output independently of provider schemas."""
    item = _parse_object(value, "proposer")
    expected = {
        "mode", "interpretation", "proposed_reply", "factual_claims",
        "exact_thatcher_wording_used", "exact_thatcher_wording", "tone",
        "confidence", "no_reply_reason",
    }
    _validate_exact_keys(item, expected, "proposer")
    if item.get("mode") not in MODES or item.get("tone") not in TONES:
        raise ValueError("proposer mode or tone is invalid")
    if item.get("confidence") not in CONFIDENCE_LEVELS:
        raise ValueError("proposer confidence is invalid")
    for field, maximum in (
        ("interpretation", 800), ("proposed_reply", maximum_reply_length),
        ("exact_thatcher_wording", maximum_reply_length), ("no_reply_reason", 500),
    ):
        if not isinstance(item.get(field), str) or len(item[field]) > maximum:
            raise ValueError(f"proposer {field} is invalid")
    if not item["interpretation"].strip():
        raise ValueError("proposer interpretation is invalid")
    if type(item.get("exact_thatcher_wording_used")) is not bool:
        raise ValueError("proposer exact_thatcher_wording_used must be boolean")
    claims = item.get("factual_claims")
    if not isinstance(claims, list) or len(claims) > maximum_claims:
        raise ValueError("proposer factual_claims is invalid")
    claim_ids: set[str] = set()
    claim_texts: set[str] = set()
    for index, claim in enumerate(claims, start=1):
        validated = _validate_claim(claim)
        if validated["claim_id"] in claim_ids:
            raise ValueError("proposer claim IDs must be unique")
        claim_ids.add(validated["claim_id"])
        if validated["claim_id"] != f"claim-{index}":
            raise ValueError("proposer claim IDs must be sequential")
        if validated["requires_evidence"] is not True:
            raise ValueError("every factual claim must require evidence")
        normalised_claim = " ".join(validated["claim_text"].casefold().split())
        if normalised_claim in claim_texts:
            raise ValueError("proposer factual claims must be unique")
        claim_texts.add(normalised_claim)
    if item["mode"] == "no_reply":
        if item["proposed_reply"] or claims or item["exact_thatcher_wording_used"]:
            raise ValueError("no_reply must not contain a draft or factual claims")
        if item["exact_thatcher_wording"] or not item["no_reply_reason"].strip():
            raise ValueError("no_reply requires a reason and no quotation wording")
    else:
        if not item["proposed_reply"].strip() or item["no_reply_reason"]:
            raise ValueError("a proposed reply requires text and an empty no_reply_reason")
        if CONFIDENCE_LEVELS[item["confidence"]] < CONFIDENCE_LEVELS["medium"]:
            raise ValueError("a proposed reply requires at least medium proposer confidence")
        if item["mode"] == "direct_factual_answer" and not claims:
            raise ValueError("direct_factual_answer requires at least one factual claim")
    if item["exact_thatcher_wording_used"] != bool(item["exact_thatcher_wording"].strip()):
        raise ValueError("exact Thatcher wording fields contradict each other")
    return item


def _validate_claim(claim: object) -> dict[str, Any]:
    if not isinstance(claim, dict):
        raise ValueError("factual claim must be an object")
    expected = {
        "claim_id", "claim_text", "requires_evidence", "actor",
        "action_or_relationship", "direction_or_polarity", "date_or_period", "quantity",
    }
    _validate_exact_keys(claim, expected, "claim")
    if not isinstance(claim.get("claim_id"), str) or not re.fullmatch(r"claim-[1-9][0-9]*", claim["claim_id"]):
        raise ValueError("claim_id is invalid")
    if not isinstance(claim.get("claim_text"), str) or not claim["claim_text"].strip() or len(claim["claim_text"]) > 500:
        raise ValueError("claim_text is invalid")
    if type(claim.get("requires_evidence")) is not bool:
        raise ValueError("claim requires_evidence must be boolean")
    for field, maximum in (
        ("actor", 200), ("action_or_relationship", 300),
        ("direction_or_polarity", 200), ("date_or_period", 160), ("quantity", 160),
    ):
        if not isinstance(claim.get(field), str) or len(claim[field]) > maximum:
            raise ValueError(f"claim {field} is invalid")
    return claim


def validate_evidence_response(
    value: object,
    claims: list[dict[str, Any]],
    candidates: dict[str, list[EvidencePassage]],
    repository: EvidenceRepository,
    *,
    maximum_references: int,
) -> list[dict[str, Any]]:
    """Validate complete claim results and enrich exact local references."""
    document = _parse_object(value, "evidence")
    _validate_exact_keys(document, {"claims"}, "evidence")
    rows = document.get("claims")
    if not isinstance(rows, list) or len(rows) != len(claims):
        raise ValueError("evidence response must contain every supplied claim exactly once")
    expected_claims = {claim["claim_id"]: claim for claim in claims}
    seen: set[str] = set()
    enriched: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("evidence claim result must be an object")
        expected = {
            "claim_id", "claim_text", "verdict", "evidence", "actor",
            "action_or_relationship", "direction_or_polarity", "date_or_period",
            "quantity", "explanation",
        }
        _validate_exact_keys(row, expected, "evidence claim")
        claim_id = row.get("claim_id")
        if claim_id not in expected_claims or claim_id in seen:
            raise ValueError("evidence claim identity is missing, duplicated or unknown")
        seen.add(claim_id)
        if row.get("claim_text") != expected_claims[claim_id]["claim_text"]:
            raise ValueError("evidence claim text differs from proposer claim")
        if row.get("verdict") not in EVIDENCE_VERDICTS:
            raise ValueError("evidence verdict is invalid")
        references = row.get("evidence")
        if not isinstance(references, list) or len(references) > maximum_references:
            raise ValueError("evidence references are invalid")
        allowed_ids = {passage.evidence_id for passage in candidates.get(claim_id, [])}
        enriched_references: list[dict[str, Any]] = []
        seen_references: set[str] = set()
        for reference in references:
            if not isinstance(reference, dict):
                raise ValueError("evidence reference must be an object")
            _validate_exact_keys(
                reference,
                {"evidence_id", "exact_supporting_passage", "relation"},
                "evidence reference",
            )
            evidence_id = str(reference.get("evidence_id") or "")
            if evidence_id not in allowed_ids or evidence_id in seen_references:
                raise ValueError("evidence reference is duplicated or was not supplied")
            seen_references.add(evidence_id)
            if reference.get("relation") not in EVIDENCE_VERDICTS:
                raise ValueError("evidence reference relation is invalid")
            passage = repository.validate_reference(evidence_id, reference.get("exact_supporting_passage"))
            enriched_references.append({
                **reference,
                "source_hash": passage.source_hash,
                "evidence_input_hash": passage.model_input_hash(),
                "quote_id": passage.quote_id,
                "field": passage.field,
                "source_title": passage.source_title,
                "source_url": passage.source_url,
                "stable_locator": passage.stable_locator,
                "verification_status": passage.verification_status,
                "research_confidence": passage.research_confidence,
            })
        if row["verdict"] == "supports" and not any(ref["relation"] == "supports" for ref in enriched_references):
            raise ValueError("support verdict requires at least one exact supporting reference")
        if row["verdict"] == "supports" and any(ref["relation"] != "supports" for ref in enriched_references):
            raise ValueError("support verdict cannot include contradictory or insufficient references")
        if row["verdict"] == "supports" and any(
            ref["research_confidence"] not in {"high", "medium"}
            for ref in enriched_references
        ):
            raise ValueError("support verdict cannot rely on a low-confidence passage")
        if row["verdict"] != "supports" and any(ref["relation"] == "supports" for ref in enriched_references):
            raise ValueError("non-support verdict contradicts a supporting reference")
        for field, maximum in (
            ("actor", 200), ("action_or_relationship", 300),
            ("direction_or_polarity", 200), ("date_or_period", 160),
            ("quantity", 160), ("explanation", 600),
        ):
            if not isinstance(row.get(field), str) or len(row[field]) > maximum:
                raise ValueError(f"evidence {field} is invalid")
        if row["verdict"] == "supports" and any(
            row[field] != expected_claims[claim_id][field]
            for field in (
                "actor",
                "action_or_relationship",
                "direction_or_polarity",
                "date_or_period",
                "quantity",
            )
        ):
            raise ValueError("support verdict changed the claim semantic dimensions")
        enriched.append({**row, "evidence": enriched_references})
    enriched_by_claim = {row["claim_id"]: row for row in enriched}
    return [enriched_by_claim[claim["claim_id"]] for claim in claims]


def validate_reviewer(value: object, *, maximum_claims: int) -> dict[str, Any]:
    """Validate an independent reviewer response and its internal consistency."""
    item = _parse_object(value, "reviewer")
    expected = {
        "verdict", "summary", "reasons", "actual_factual_claims",
        "unsupported_factual_claims", "direct_question_present",
        "answers_direct_question_first_sentence", "topically_relevant",
        "endorses_unsupported_allegation", "contains_unsupported_factual_claims",
        "actor_action_relationship_correct", "direction_polarity_correct",
        "dates_quantities_correct", "quotation_attribution_correct",
        "original_prose_clearly_not_historical_quotation", "mode_and_tone_match",
        "suitable_for_account", "revision_instructions",
    }
    _validate_exact_keys(item, expected, "reviewer")
    if item.get("verdict") not in REVIEWER_VERDICTS:
        raise ValueError("reviewer verdict is invalid")
    for field, maximum in (("summary", 800), ("revision_instructions", 800)):
        if not isinstance(item.get(field), str) or len(item[field]) > maximum:
            raise ValueError(f"reviewer {field} is invalid")
    list_limits = {
        "reasons": (12, 300),
        "actual_factual_claims": (maximum_claims, 500),
        "unsupported_factual_claims": (maximum_claims, 500),
    }
    for field, (maximum_items, maximum_length) in list_limits.items():
        value_list = item.get(field)
        if (
            not isinstance(value_list, list)
            or len(value_list) > maximum_items
            or any(
                not isinstance(entry, str) or len(entry) > maximum_length
                for entry in value_list
            )
        ):
            raise ValueError(f"reviewer {field} is invalid")
    boolean_fields = expected - {
        "verdict", "summary", "reasons", "actual_factual_claims",
        "unsupported_factual_claims", "revision_instructions",
    }
    if any(type(item.get(field)) is not bool for field in boolean_fields):
        raise ValueError("reviewer boolean fields are invalid")
    if item["direct_question_present"] and not item["answers_direct_question_first_sentence"] and item["verdict"] == "approve":
        raise ValueError("reviewer cannot approve an evasive direct answer")
    if item["verdict"] == "revise" and not item["revision_instructions"].strip():
        raise ValueError("reviewer revision requires instructions")
    if item["verdict"] == "approve" and item["revision_instructions"]:
        raise ValueError("reviewer approval cannot include revision instructions")
    if item["verdict"] == "approve" and not reviewer_checks_approve(item):
        raise ValueError("reviewer approve verdict contradicts its safety findings")
    return item


def reviewer_checks_approve(review: dict[str, Any]) -> bool:
    """Return whether every independent approval condition is satisfied."""
    positive = (
        "topically_relevant",
        "actor_action_relationship_correct",
        "direction_polarity_correct",
        "dates_quantities_correct",
        "quotation_attribution_correct",
        "original_prose_clearly_not_historical_quotation",
        "mode_and_tone_match",
        "suitable_for_account",
    )
    return bool(
        all(review.get(field) is True for field in positive)
        and (
            review.get("direct_question_present") is False
            or review.get("answers_direct_question_first_sentence") is True
        )
        and review.get("endorses_unsupported_allegation") is False
        and review.get("contains_unsupported_factual_claims") is False
        and not review.get("unsupported_factual_claims")
    )


def _is_sentence_terminator(character: str) -> bool:
    """Return whether one Unicode character conventionally ends a sentence."""
    if character in {".", "!", "?", "\N{HORIZONTAL ELLIPSIS}"}:
        return True
    name = unicodedata.name(character, "")
    return bool(
        name.endswith("FULL STOP")
        or name.endswith("QUESTION MARK")
        or name.endswith("EXCLAMATION MARK")
        or "DANDA" in name
    )


_TITLE_OR_CONNECTOR_ABBREVIATIONS = frozenset({
    "dr", "hon", "mr", "mrs", "ms", "prof", "rt", "st", "vs",
})
_CONTEXTUAL_ABBREVIATIONS = frozenset({"etc", "govt", "mp"})


def _period_is_non_terminal_abbreviation(text: str, start: int, end: int) -> bool:
    """Return whether one full stop belongs to a mid-sentence abbreviation."""
    preceding_word = re.search(r"([A-Za-z]+)$", text[:start])
    if preceding_word is None:
        return False
    word = preceding_word.group(1)
    if len(word) == 1 and word.isupper():
        return True
    folded = word.casefold()
    remainder = text[end:].lstrip()
    if folded == "no":
        return bool(remainder and remainder[0].isdigit())
    if folded in _TITLE_OR_CONNECTOR_ABBREVIATIONS:
        return True
    if folded not in _CONTEXTUAL_ABBREVIATIONS:
        return False
    return bool(
        remainder
        and (remainder[0].islower() or remainder[0] in {",", ";", ":", ")", "]"})
    )


def sentence_count(text: str) -> int:
    """Count user-visible sentence terminators, including lower-case starts."""
    compact = " ".join(str(text or "").split())
    if not compact:
        return 0
    boundaries = 0
    last_boundary_end = 0
    index = 0
    while index < len(compact):
        if not _is_sentence_terminator(compact[index]):
            index += 1
            continue
        start = index
        index += 1
        while index < len(compact) and _is_sentence_terminator(compact[index]):
            index += 1
        punctuation = compact[start:index]
        if punctuation == ".":
            previous = compact[start - 1] if start else ""
            following = compact[index] if index < len(compact) else ""
            if previous.isdigit() and following.isdigit():
                continue
            if _period_is_non_terminal_abbreviation(compact, start, index):
                continue
        boundaries += 1
        last_boundary_end = index
    trailing = compact[last_boundary_end:].strip(" \t\r\n\"'\N{RIGHT SINGLE QUOTATION MARK}\N{RIGHT DOUBLE QUOTATION MARK}\N{RIGHT-POINTING DOUBLE ANGLE QUOTATION MARK})]}")
    if trailing:
        boundaries += 1
    return max(1, boundaries)


def contains_emoji(text: str) -> bool:
    """Return whether text includes pictographic or regional-indicator symbols."""
    for character in str(text or ""):
        codepoint = ord(character)
        if (
            0x1F000 <= codepoint <= 0x1FAFF
            or 0x2600 <= codepoint <= 0x27BF
            or 0x1F1E6 <= codepoint <= 0x1F1FF
            or codepoint in {0x20E3, 0xFE0F}
            or unicodedata.category(character) == "So"
        ):
            return True
    return False


def x_weighted_reply_length(text: str) -> int:
    """Return X's weighted length for link-free conversational reply text."""
    weight_one_ranges = (
        (0x0000, 0x10FF),
        (0x2000, 0x200D),
        (0x2010, 0x201F),
        (0x2032, 0x2037),
    )
    return sum(
        1 if any(low <= ord(character) <= high for low, high in weight_one_ranges) else 2
        for character in str(text or "")
    )


def contains_bare_network_address(text: str) -> bool:
    """Return whether text contains a valid bare IP address or IDNA domain."""
    normalised = str(text or "").translate(IDNA_DOT_TRANSLATION)
    for match in IPV4_CANDIDATE_RE.finditer(normalised):
        try:
            ipaddress.ip_address(match.group(0))
        except ValueError:
            continue
        return True
    for match in DOMAIN_CANDIDATE_RE.finditer(normalised):
        labels = match.group(0).split(".")
        if any(
            not label
            or label.startswith("-")
            or label.endswith("-")
            or any(
                character != "-"
                and unicodedata.category(character)[:1] not in {"L", "N", "M"}
                for character in label
            )
            for label in labels
        ):
            continue
        try:
            encoded_labels = [label.encode("idna").decode("ascii") for label in labels]
        except UnicodeError:
            continue
        if any(not 1 <= len(label) <= 63 for label in encoded_labels):
            continue
        top_level = labels[-1]
        if len(top_level) >= 2 and any(
            unicodedata.category(character).startswith("L")
            for character in top_level
        ):
            return True
    return False


def contains_named_symbol(text: str, *, literal: str, unicode_name_suffix: str) -> bool:
    """Return whether text contains an ASCII or compatibility-form symbol."""
    return any(
        character == literal
        or unicodedata.name(character, "").endswith(unicode_name_suffix)
        for character in str(text or "")
    )


def reply_repetition_reason(text: str, recent_replies: list[str]) -> str | None:
    """Reject exact or near-duplicate recent replies deterministically."""
    candidate = " ".join(text.casefold().split())
    for previous in recent_replies:
        normalised = " ".join(str(previous).casefold().split())
        if not normalised:
            continue
        if candidate == normalised:
            return "exact_duplicate_reply"
        if difflib.SequenceMatcher(None, candidate, normalised).ratio() >= 0.9:
            return "near_duplicate_reply"
    return None


def deterministic_reply_error(
    proposer: dict[str, Any],
    repository: EvidenceRepository,
    *,
    recent_replies: list[str],
    maximum_reply_length: int,
    maximum_sentences: int,
) -> str | None:
    """Apply hard syntax, safety, quote-integrity and duplicate limits."""
    reply = str(proposer.get("proposed_reply") or "")
    if (
        not reply
        or reply != reply.strip()
        or len(reply) > maximum_reply_length
        or x_weighted_reply_length(reply) > maximum_reply_length
    ):
        return "invalid_reply_length_or_whitespace"
    if len(reply) < 3:
        return "reply_too_short"
    if sentence_count(reply) > maximum_sentences:
        return "reply_sentence_limit_exceeded"
    if (
        URL_RE.search(reply)
        or DOMAIN_RE.search(reply)
        or EMAIL_RE.search(reply)
        or contains_bare_network_address(reply)
    ):
        return "reply_contains_link"
    if len(reply.splitlines()) > 1:
        return "reply_contains_line_break"
    if any(unicodedata.category(character) in {"Cc", "Cf", "Cs"} for character in reply):
        return "reply_contains_control_or_format_character"
    if contains_named_symbol(reply, literal="@", unicode_name_suffix="COMMERCIAL AT"):
        return "reply_contains_mention"
    if contains_named_symbol(reply, literal="#", unicode_name_suffix="NUMBER SIGN"):
        return "reply_contains_hashtag"
    if contains_emoji(reply):
        return "reply_contains_emoji"
    if any(pattern.search(reply) for pattern in BLOCKED_REPLY_PATTERNS):
        return "reply_matches_blocked_safety_pattern"
    repetition = reply_repetition_reason(reply, recent_replies)
    if repetition:
        return repetition

    exact_used = proposer["exact_thatcher_wording_used"]
    exact_wording = proposer["exact_thatcher_wording"]
    detected = repository.detected_authorised_quote_ids(reply)
    if exact_used:
        if not repository.exact_quote_occurs_in_reply(reply, exact_wording):
            return "declared_thatcher_wording_missing_from_reply"
        if not repository.exact_quote_is_authorised(exact_wording):
            return "unauthorised_thatcher_wording"
        if repository.detected_authorised_quote_ids_outside_exact(reply, exact_wording):
            return "undeclared_thatcher_wording_detected"
    elif detected:
        return "undeclared_thatcher_wording_detected"
    return None


def _proposer_prompts(
    context: dict[str, Any],
    recent_replies: list[str],
    *,
    revision: dict[str, Any] | None,
) -> tuple[str, str]:
    system = (
        "You are the proposer for a Margaret Thatcher quotation account on X. "
        "Understand the user's actual contribution first, then either write one natural original reply "
        "or choose no_reply. Use only these modes: direct_factual_answer, opinion_or_principle, "
        "light_humour, courtesy, no_reply. The voice may be firm, dry, witty or warm, but must not "
        "pretend to be Margaret Thatcher. Do not assemble the reply from a retrieved Thatcher quotation. "
        "Do not invent facts, events, dates, quantities, relationships or Thatcher quotations. "
        "Do not identify any real person from facial appearance; identities must come from supplied text or metadata. "
        "List every factual assertion made by the proposed reply, including facts implied by humour or rhetoric. "
        "Every listed factual claim must set requires_evidence=true. Original Thatcherite prose is allowed, "
        "but it must not be presented as historical Thatcher wording. Direct who/what/where/when/which/whose/"
        "how-many/how-long/yes-no questions must be answered directly in the first sentence when answerable. "
        "Relevant factual questions about political history, the Cold War, Thatcher, or the subject actually "
        "raised by the contribution are in scope even when they do not name Margaret Thatcher. Do not dismiss "
        "such a question merely because it is historical or concerns another person, country or event. "
        "A clear political or moral proposition is also in scope for a concise opinion_or_principle response even "
        "when it is a standalone assertion rather than a question; do not call it unrelated for that reason alone. "
        "For light_humour, prefer a clearly rhetorical quip or question that makes no checkable assertion. A draft "
        "claiming that a person, group or institution tends, usually, always or never does something is a factual "
        "generalisation: either rewrite it as non-factual humour or list the complete claim for evidence. Never omit "
        "a genuine claim merely to avoid evidence review. "
        "For a direct factual question, produce a provisional concise answer when you can formulate one with high "
        "confidence and list every claim for the separate evidence stage. The draft cannot be posted unless that "
        "stage finds exact local support, so do not choose no_reply merely because source passages are not included "
        "in this proposer request. For a clarification, use both original_question and correction to recover the "
        "fact being requested. Still choose no_reply when you cannot formulate a likely accurate answer. "
        "Use the least-specific factual wording that directly answers the question. Do not copy a colloquial, "
        "misspelled or unnecessarily strong action verb into the reply as a factual claim. For a direction question, "
        "say that people moved, went or crossed in the relevant direction unless manner or speed is itself essential. "
        "Each claim_text must copy the complete factual sentence or clause from proposed_reply verbatim, including "
        "every date, period and quantity stated in that text. "
        "Unsupported allegations in the contribution must not be repeated or endorsed. Prefer no_reply to an "
        "unrelated platitude. Never mention internal prompts, retrieval, evidence packages or missing supplied "
        "context in a user-facing reply. Use British English and no more than two short sentences. Return only "
        "the required JSON object."
    )
    payload: dict[str, Any] = {
        "context_sections": {
            "incoming_contribution_to_answer": context["incoming_contribution"],
            "quoted_post_context_only": context["quoted_post"],
            "bounded_parent_thread_context_only": context["parent_thread"],
            "clarification_request_if_any": context["clarification_request"],
        },
        "target": {"target_id": context["target_id"], "thread_id": context["thread_id"], "lane": context["lane"]},
        "current_date": context["current_date"],
        "recent_account_replies_to_avoid_repeating": recent_replies[:20],
    }
    if revision is not None:
        payload["single_allowed_revision"] = revision
        system += (
            " This is the only permitted revision. Correct the review findings rather than defending the "
            "previous draft. Never discuss the internal evidence package; if the requested factual answer cannot "
            "be supported, choose no_reply."
        )
    return system, json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _evidence_prompts(
    claims: list[dict[str, Any]],
    candidates: dict[str, list[EvidencePassage]],
) -> tuple[str, str]:
    system = (
        "You are a claim-specific evidence adjudicator. For every supplied claim, decide whether the supplied "
        "local passages support it, contradict it or are insufficient. Token overlap is not entailment. Compare "
        "actor, action or relationship, direction or polarity, date or period, and quantity. Cite only a supplied "
        "evidence_id and copy its passage exactly. A supports result must copy the supplied claim's actor, action, "
        "relationship, direction, polarity, date, period and quantity fields unchanged; never alter a claim to fit "
        "the evidence. A passage marked with low research confidence cannot support a production claim. "
        "Support is semantic entailment and does not require identical wording: ordinary paraphrases such as moved, "
        "went, travelled and crossed may describe the same movement when actor and direction agree. A stronger "
        "claim about manner or speed, such as ran or rushed, remains unsupported unless the passage establishes it. "
        "Do not use outside knowledge or infer support from a broad shared "
        "topic. Return one result for every claim and only the required JSON object."
    )
    payload = {
        "claims": claims,
        "candidate_passages_by_claim": {
            claim_id: [passage.prompt_record() for passage in passages]
            for claim_id, passages in candidates.items()
        },
    }
    return system, json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _reviewer_prompts(
    context: dict[str, Any],
    proposer: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> tuple[str, str]:
    system = (
        "You are a fresh independent final reviewer. You did not participate in drafting. Judge only the incoming "
        "contribution, bounded context, proposed reply, selected mode and supplied evidence package. Do not assume "
        "the proposer's claim list or confidence is correct. Find omitted factual claims. Reject or request revision "
        "if the reply is off-topic, evades a direct question, endorses an unsupported allegation, lacks evidence, "
        "reverses actor, action, relationship, direction or polarity, gives a wrong date or quantity, fabricates or "
        "misattributes a quotation, or presents original prose as Thatcher's historical words. Broad thematic overlap "
        "is not enough. The user's own contribution has priority over quoted and parent text. Do not identify real "
        "people from appearance; treat an identity absent from supplied text or metadata as unknown. Approval is "
        "explicit and all safety findings must agree with it. In actual_factual_claims, copy every factual claim present in the "
        "proposed reply verbatim and in order, including any claim omitted by the proposer; where a proposer claim "
        "is accurate, copy its claim_text exactly. revision_instructions must be non-empty for revise and empty for "
        "approve. Use revise for a correctable draft when a safe, relevant reply remains plausible. Use reject only "
        "when the contribution should not be answered or the defect cannot be safely corrected in one revision. "
        "A reject is terminal; revision_instructions on a reject are optional and will not be acted upon. "
        "Return only the required JSON object."
    )
    payload = {
        "context_sections": {
            "incoming_contribution_to_answer": context["incoming_contribution"],
            "quoted_post_context_only": context["quoted_post"],
            "bounded_parent_thread_context_only": context["parent_thread"],
            "clarification_request_if_any": context["clarification_request"],
        },
        "mode": proposer["mode"],
        "tone": proposer["tone"],
        "proposed_reply": proposer["proposed_reply"],
        "proposer_listed_factual_claims_untrusted": proposer["factual_claims"],
        "exact_thatcher_wording_used": proposer["exact_thatcher_wording_used"],
        "exact_thatcher_wording": proposer["exact_thatcher_wording"],
        "evidence_package": evidence,
    }
    return system, json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _insufficient_evidence_rows(claims: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "claim_id": claim["claim_id"],
            "claim_text": claim["claim_text"],
            "verdict": "insufficient",
            "evidence": [],
            "actor": claim["actor"],
            "action_or_relationship": claim["action_or_relationship"],
            "direction_or_polarity": claim["direction_or_polarity"],
            "date_or_period": claim["date_or_period"],
            "quantity": claim["quantity"],
            "explanation": "No local candidate passage was available.",
        }
        for claim in claims
    ]


def _all_claims_supported(claims: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> bool:
    expected = {claim["claim_id"] for claim in claims}
    return bool(
        {row["claim_id"] for row in evidence} == expected
        and all(row["verdict"] == "supports" for row in evidence)
    )


def claim_retrieval_query(claim: dict[str, Any], context: dict[str, Any]) -> str:
    """Build a bounded query from the claim and user's own contribution only."""
    values = [
        claim.get("claim_text", ""),
        claim.get("actor", ""),
        claim.get("action_or_relationship", ""),
        claim.get("direction_or_polarity", ""),
        claim.get("date_or_period", ""),
        claim.get("quantity", ""),
        context.get("incoming_contribution", ""),
    ]
    return "\n".join(str(value).strip() for value in values if str(value).strip())


def build_draft_record(
    *,
    context: dict[str, Any],
    proposer: dict[str, Any],
    evidence: list[dict[str, Any]],
    reviewer: dict[str, Any],
    config: dict[str, Any],
    model_call_count: int,
    revision_count: int,
    creation_time: str,
) -> dict[str, Any]:
    """Build the immutable V3 persisted-draft record for an approved reply."""
    references = [reference for row in evidence for reference in row.get("evidence", [])]
    evidence_ids = sorted({str(reference["evidence_id"]) for reference in references})
    claim_evidence = [
        {
            "claim_id": str(row["claim_id"]),
            "evidence_ids": sorted({
                str(reference["evidence_id"])
                for reference in row.get("evidence", [])
                if reference.get("relation") == "supports"
            }),
        }
        for row in evidence
    ]
    source_hashes = {
        evidence_id: next(
            str(reference["source_hash"])
            for reference in references
            if reference["evidence_id"] == evidence_id
        )
        for evidence_id in evidence_ids
    }
    evidence_input_hashes = {
        evidence_id: next(
            str(reference["evidence_input_hash"])
            for reference in references
            if reference["evidence_id"] == evidence_id
        )
        for evidence_id in evidence_ids
    }
    record = {
        "schema_version": DRAFT_SCHEMA_VERSION,
        "strategy_version": STRATEGY_VERSION,
        "target_id": context["target_id"],
        "thread_id": context["thread_id"],
        "candidate_source": context["lane"],
        "contribution_hash": text_hash(context["incoming_contribution"]),
        "context_hash": value_hash(context),
        "proposed_reply": proposer["proposed_reply"],
        "mode": proposer["mode"],
        "tone": proposer["tone"],
        "factual_claims": proposer["factual_claims"],
        "exact_thatcher_wording_used": proposer["exact_thatcher_wording_used"],
        "exact_thatcher_wording": proposer["exact_thatcher_wording"],
        "evidence_ids": evidence_ids,
        "claim_evidence": claim_evidence,
        "source_hashes": source_hashes,
        "evidence_input_hashes": evidence_input_hashes,
        "reviewer_verdict": reviewer["verdict"],
        "reviewer_reasons": reviewer["reasons"],
        "proposer_model": config["proposer_model"],
        "evidence_model": config["evidence_model"],
        "reviewer_model": config["reviewer_model"],
        "proposer_prompt_version": PROPOSER_PROMPT_VERSION,
        "evidence_prompt_version": EVIDENCE_PROMPT_VERSION,
        "reviewer_prompt_version": REVIEWER_PROMPT_VERSION,
        "model_call_count": model_call_count,
        "revision_count": revision_count,
        "creation_time": creation_time,
    }
    record["approval_hash"] = value_hash(record)
    return record


def validate_persisted_draft(
    record: object,
    *,
    context: dict[str, Any],
    config: dict[str, Any],
    repository: EvidenceRepository,
    maximum_reply_length: int,
    recent_replies: list[str] | None = None,
) -> dict[str, Any]:
    """Validate a V3 draft against current context, models and evidence inputs."""
    if not isinstance(record, dict):
        raise ValueError("persisted AI reply draft must be an object")
    expected = {
        "schema_version", "strategy_version", "target_id", "thread_id", "candidate_source",
        "contribution_hash", "context_hash", "proposed_reply", "mode", "tone",
        "factual_claims", "exact_thatcher_wording_used", "exact_thatcher_wording",
        "evidence_ids", "claim_evidence", "source_hashes", "evidence_input_hashes",
        "reviewer_verdict", "reviewer_reasons",
        "proposer_model", "evidence_model", "reviewer_model", "proposer_prompt_version",
        "evidence_prompt_version", "reviewer_prompt_version", "model_call_count",
        "revision_count", "creation_time", "approval_hash",
    }
    _validate_exact_keys(record, expected, "persisted draft")
    clean_context = validate_reply_context(context)
    if record.get("schema_version") != DRAFT_SCHEMA_VERSION or record.get("strategy_version") != STRATEGY_VERSION:
        raise ValueError("persisted draft version is unsupported")
    identity = (clean_context["target_id"], clean_context["thread_id"], clean_context["lane"])
    if identity != (record.get("target_id"), record.get("thread_id"), record.get("candidate_source")):
        raise ValueError("persisted draft target identity mismatch")
    if record.get("contribution_hash") != text_hash(clean_context["incoming_contribution"]):
        raise ValueError("persisted draft contribution hash mismatch")
    if record.get("context_hash") != value_hash(clean_context):
        raise ValueError("persisted draft context hash mismatch")
    model_fields = {
        "proposer_model": config["proposer_model"],
        "evidence_model": config["evidence_model"],
        "reviewer_model": config["reviewer_model"],
        "proposer_prompt_version": PROPOSER_PROMPT_VERSION,
        "evidence_prompt_version": EVIDENCE_PROMPT_VERSION,
        "reviewer_prompt_version": REVIEWER_PROMPT_VERSION,
    }
    if any(record.get(key) != value for key, value in model_fields.items()):
        raise ValueError("persisted draft model or prompt version mismatch")
    if record.get("reviewer_verdict") != "approve":
        raise ValueError("persisted draft lacks explicit reviewer approval")
    if record.get("mode") not in MODES - {"no_reply"} or record.get("tone") not in TONES:
        raise ValueError("persisted draft mode or tone is invalid")
    reply = record.get("proposed_reply")
    if not isinstance(reply, str) or not reply or len(reply) > maximum_reply_length:
        raise ValueError("persisted draft reply text is invalid")
    if type(record.get("model_call_count")) is not int or not 1 <= record["model_call_count"] <= config["maximum_model_calls"]:
        raise ValueError("persisted draft model call count is invalid")
    if type(record.get("revision_count")) is not int or record["revision_count"] not in {0, 1}:
        raise ValueError("persisted draft revision count is invalid")
    if not isinstance(record.get("creation_time"), str) or not record["creation_time"].endswith("Z"):
        raise ValueError("persisted draft creation time is invalid")
    try:
        created = datetime.fromisoformat(record["creation_time"].replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("persisted draft creation time is invalid") from exc
    if created.tzinfo is None or created.utcoffset() != timezone.utc.utcoffset(created):
        raise ValueError("persisted draft creation time must be UTC")
    claims = record.get("factual_claims")
    if not isinstance(claims, list) or len(claims) > config["maximum_claims"]:
        raise ValueError("persisted draft factual claims are invalid")
    seen_claim_ids: set[str] = set()
    seen_claim_texts: set[str] = set()
    for index, claim in enumerate(claims, start=1):
        validated_claim = _validate_claim(claim)
        claim_id = validated_claim["claim_id"]
        normalised_text = " ".join(validated_claim["claim_text"].casefold().split())
        if claim_id in seen_claim_ids or claim_id != f"claim-{index}":
            raise ValueError("persisted draft claim IDs must be unique and sequential")
        if normalised_text in seen_claim_texts:
            raise ValueError("persisted draft factual claims must be unique")
        if validated_claim["requires_evidence"] is not True:
            raise ValueError("persisted draft factual claims must require evidence")
        seen_claim_ids.add(claim_id)
        seen_claim_texts.add(normalised_text)
    if record["mode"] == "direct_factual_answer" and not claims:
        raise ValueError("persisted direct factual answer requires a factual claim")
    reviewer_reasons = record.get("reviewer_reasons")
    if (
        not isinstance(reviewer_reasons, list)
        or len(reviewer_reasons) > 12
        or any(
            not isinstance(reason, str) or len(reason) > 300
            for reason in reviewer_reasons
        )
    ):
        raise ValueError("persisted draft reviewer reasons are invalid")
    evidence_ids = record.get("evidence_ids")
    claim_evidence = record.get("claim_evidence")
    source_hashes = record.get("source_hashes")
    evidence_input_hashes = record.get("evidence_input_hashes")
    if not isinstance(evidence_ids, list) or evidence_ids != sorted(set(evidence_ids)):
        raise ValueError("persisted draft evidence IDs are invalid")
    if not isinstance(source_hashes, dict) or set(source_hashes) != set(evidence_ids):
        raise ValueError("persisted draft source hashes are invalid")
    if (
        not isinstance(evidence_input_hashes, dict)
        or set(evidence_input_hashes) != set(evidence_ids)
        or any(
            not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
            for value in evidence_input_hashes.values()
        )
    ):
        raise ValueError("persisted draft evidence input hashes are invalid")
    expected_claim_ids = [claim["claim_id"] for claim in claims]
    if not isinstance(claim_evidence, list) or len(claim_evidence) != len(claims):
        raise ValueError("persisted draft claim evidence is incomplete")
    observed_claim_ids: list[str] = []
    mapped_evidence_ids: set[str] = set()
    for mapping in claim_evidence:
        if not isinstance(mapping, dict) or set(mapping) != {"claim_id", "evidence_ids"}:
            raise ValueError("persisted draft claim evidence fields mismatch")
        claim_id = mapping.get("claim_id")
        mapped_ids = mapping.get("evidence_ids")
        if (
            claim_id not in expected_claim_ids
            or not isinstance(mapped_ids, list)
            or not mapped_ids
            or mapped_ids != sorted(set(mapped_ids))
            or any(evidence_id not in evidence_ids for evidence_id in mapped_ids)
        ):
            raise ValueError("persisted draft claim evidence is invalid")
        observed_claim_ids.append(claim_id)
        mapped_evidence_ids.update(mapped_ids)
    if observed_claim_ids != expected_claim_ids or mapped_evidence_ids != set(evidence_ids):
        raise ValueError("persisted draft claim evidence does not cover the factual claims")
    for evidence_id in evidence_ids:
        passage = repository.passages.get(str(evidence_id))
        if passage is None or source_hashes[evidence_id] != passage.source_hash:
            raise ValueError("persisted draft evidence source changed or disappeared")
        if evidence_input_hashes[evidence_id] != passage.model_input_hash():
            raise ValueError("persisted draft evidence model input changed")
    if claims and not evidence_ids:
        raise ValueError("persisted factual draft has no evidence IDs")
    if type(record.get("exact_thatcher_wording_used")) is not bool or not isinstance(record.get("exact_thatcher_wording"), str):
        raise ValueError("persisted exact quotation fields are invalid")
    if record["exact_thatcher_wording_used"] != bool(record["exact_thatcher_wording"].strip()):
        raise ValueError("persisted exact quotation fields contradict each other")
    synthetic_proposer = {
        "proposed_reply": reply,
        "exact_thatcher_wording_used": record["exact_thatcher_wording_used"],
        "exact_thatcher_wording": record["exact_thatcher_wording"],
    }
    error = deterministic_reply_error(
        synthetic_proposer,
        repository,
        recent_replies=recent_replies or [],
        maximum_reply_length=maximum_reply_length,
        maximum_sentences=config["maximum_reply_sentences"],
    )
    if error:
        raise ValueError(f"persisted draft fails deterministic safety: {error}")
    approval_hash = record.get("approval_hash")
    unsigned = {key: value for key, value in record.items() if key != "approval_hash"}
    if not isinstance(approval_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", approval_hash):
        raise ValueError("persisted draft approval hash is invalid")
    if approval_hash != value_hash(unsigned):
        raise ValueError("persisted draft differs from the reviewer-approved record")
    return record


def run_reply_pipeline(
    *,
    context: dict[str, Any],
    config: dict[str, Any],
    repository: EvidenceRepository,
    transport: ModelTransport,
    maximum_reply_length: int,
    recent_replies: list[str] | None = None,
    media_context: dict[str, Any] | None = None,
    creation_time: str | None = None,
) -> PipelineResult:
    """Run proposer, claim evidence and fresh reviewer with at most one revision."""
    config_errors = validate_strategy_config(config)
    if config_errors:
        return PipelineResult(None, "operational_failure", "invalid_strategy_config", 0, 0, tuple({"error": error} for error in config_errors))
    if config["enabled"] is not True:
        return PipelineResult(None, "disabled", "strategy_disabled", 0, 0, ())
    clean_context = validate_reply_context(context)
    recent = [str(item) for item in (recent_replies or [])[:20]]
    call_count = 0
    audit: list[dict[str, Any]] = []
    revisions = 0

    def call(
        stage: str,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
        timeout: int,
        max_output_tokens: int,
        include_media: bool,
    ) -> object:
        nonlocal call_count
        if call_count >= config["maximum_model_calls"]:
            raise ModelCallLimitError("reply pipeline model-call ceiling reached")
        call_count += 1
        return transport(
            stage=stage,
            model=model,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_schema=schema,
            timeout_seconds=timeout,
            max_output_tokens=max_output_tokens,
            media_context=media_context if include_media else None,
        )

    def call_and_validate(
        stage: str,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        schema: dict[str, Any],
        timeout: int,
        max_output_tokens: int,
        include_media: bool,
        validator: Callable[[object], Any],
    ) -> Any | None:
        """Retry one fully received invalid model response, then fail closed."""
        maximum_retries = config["maximum_invalid_response_retries"]
        for attempt in range(maximum_retries + 1):
            try:
                raw = call(
                    stage,
                    model=model,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    schema=schema,
                    timeout=timeout,
                    max_output_tokens=max_output_tokens,
                    include_media=include_media,
                )
                return validator(raw)
            except ModelCallLimitError as exc:
                audit.append({
                    "stage": stage,
                    "status": "invalid",
                    "attempt": attempt + 1,
                    "reason": str(exc),
                })
                return None
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                if attempt >= maximum_retries:
                    audit.append({
                        "stage": stage,
                        "status": "invalid",
                        "attempt": attempt + 1,
                        "reason": str(exc),
                    })
                    return None
                audit.append({
                    "stage": stage,
                    "status": "invalid_response_retry",
                    "attempt": attempt + 1,
                    "reason": str(exc),
                    "next_attempt": attempt + 2,
                })
        return None

    revision_request: dict[str, Any] | None = None
    while True:
        proposer_stage = "revision_proposer" if revisions else "proposer"
        proposer_system, proposer_user = _proposer_prompts(clean_context, recent, revision=revision_request)
        proposer = call_and_validate(
            proposer_stage,
            model=config["proposer_model"],
            system_prompt=proposer_system,
            user_prompt=proposer_user,
            schema=proposer_schema(maximum_reply_length, config["maximum_claims"]),
            timeout=config["proposer_timeout_seconds"],
            max_output_tokens=config["proposer_max_output_tokens"],
            include_media=True,
            validator=lambda raw: validate_proposer(
                raw,
                maximum_reply_length=maximum_reply_length,
                maximum_claims=config["maximum_claims"],
            ),
        )
        if proposer is None:
            return PipelineResult(None, "operational_failure", f"{proposer_stage}_invalid", call_count, revisions, tuple(audit))
        audit.append({"stage": proposer_stage, "status": "completed", "mode": proposer["mode"]})
        if proposer["mode"] == "no_reply":
            return PipelineResult(None, "no_reply", proposer["no_reply_reason"], call_count, revisions, tuple(audit))

        hard_error = deterministic_reply_error(
            proposer,
            repository,
            recent_replies=recent,
            maximum_reply_length=maximum_reply_length,
            maximum_sentences=config["maximum_reply_sentences"],
        )
        if hard_error:
            audit.append({"stage": proposer_stage, "status": "rejected", "reason": hard_error})
            return PipelineResult(None, "no_reply", hard_error, call_count, revisions, tuple(audit))

        claims = proposer["factual_claims"]
        evidence: list[dict[str, Any]] = []
        if claims:
            candidates = {
                claim["claim_id"]: repository.candidate_passages(
                    claim_retrieval_query(claim, clean_context),
                    maximum_packets=config["maximum_evidence_packets_per_claim"],
                    maximum_passages=config["maximum_evidence_passages_per_claim"],
                )
                for claim in claims
            }
            if any(candidates.values()):
                evidence_stage = "revision_evidence" if revisions else "evidence"
                evidence_system, evidence_user = _evidence_prompts(claims, candidates)
                evidence_result = call_and_validate(
                    evidence_stage,
                    model=config["evidence_model"],
                    system_prompt=evidence_system,
                    user_prompt=evidence_user,
                    schema=evidence_schema(
                        config["maximum_claims"],
                        config["maximum_evidence_passages_per_claim"],
                    ),
                    timeout=config["evidence_timeout_seconds"],
                    max_output_tokens=config["evidence_max_output_tokens"],
                    include_media=False,
                    validator=lambda raw: validate_evidence_response(
                        raw,
                        claims,
                        candidates,
                        repository,
                        maximum_references=config["maximum_evidence_passages_per_claim"],
                    ),
                )
                if evidence_result is None:
                    return PipelineResult(None, "operational_failure", f"{evidence_stage}_invalid", call_count, revisions, tuple(audit))
                evidence = evidence_result
                audit.append({"stage": evidence_stage, "status": "completed", "supported": _all_claims_supported(claims, evidence)})
            else:
                evidence = _insufficient_evidence_rows(claims)
                audit.append({"stage": "evidence", "status": "insufficient", "reason": "claim_without_candidate_passage"})

        evidence_supported = _all_claims_supported(claims, evidence)
        if proposer["mode"] == "direct_factual_answer" and not evidence_supported:
            audit.append({
                "stage": "evidence",
                "status": "rejected",
                "reason": "direct_factual_answer_not_fully_supported",
            })
            return PipelineResult(
                None,
                "no_reply",
                "insufficient_claim_evidence",
                call_count,
                revisions,
                tuple(audit),
            )

        reviewer_stage = "revision_reviewer" if revisions else "reviewer"
        reviewer_system, reviewer_user = _reviewer_prompts(clean_context, proposer, evidence)
        reviewer = call_and_validate(
            reviewer_stage,
            model=config["reviewer_model"],
            system_prompt=reviewer_system,
            user_prompt=reviewer_user,
            schema=reviewer_schema(config["maximum_claims"]),
            timeout=config["reviewer_timeout_seconds"],
            max_output_tokens=config["reviewer_max_output_tokens"],
            include_media=True,
            validator=lambda raw: validate_reviewer(
                raw,
                maximum_claims=config["maximum_claims"],
            ),
        )
        if reviewer is None:
            return PipelineResult(None, "operational_failure", f"{reviewer_stage}_invalid", call_count, revisions, tuple(audit))
        expected_reviewer_claims = [claim["claim_text"] for claim in claims]
        if (
            reviewer["verdict"] == "approve"
            and reviewer["actual_factual_claims"] != expected_reviewer_claims
        ):
            audit.append({
                "stage": reviewer_stage,
                "status": "invalid",
                "reason": "reviewer_claim_inventory_mismatch",
            })
            if revisions >= config["maximum_revisions"]:
                return PipelineResult(
                    None,
                    "operational_failure",
                    "reviewer_claim_inventory_mismatch",
                    call_count,
                    revisions,
                    tuple(audit),
                )
            revision_request = {
                "previous_reply": proposer["proposed_reply"],
                "reviewer_reasons": reviewer["reasons"],
                "reviewer_actual_factual_claims": reviewer["actual_factual_claims"],
                "reviewer_revision_instructions": (
                    "Correct the factual-claim inventory. Each claim_text must copy the complete factual "
                    "sentence or clause from proposed_reply verbatim, including every date, period and "
                    "quantity. Do not add unsupported facts or discuss the evidence process."
                ),
                "evidence_status": [
                    {"claim_id": row["claim_id"], "verdict": row["verdict"]}
                    for row in evidence
                ],
            }
            revisions += 1
            continue
        audit.append({"stage": reviewer_stage, "status": "completed", "verdict": reviewer["verdict"]})

        explicit_approval = reviewer["verdict"] == "approve" and reviewer_checks_approve(reviewer)
        if explicit_approval and evidence_supported:
            draft = build_draft_record(
                context=clean_context,
                proposer=proposer,
                evidence=evidence,
                reviewer=reviewer,
                config=config,
                model_call_count=call_count,
                revision_count=revisions,
                creation_time=creation_time or utc_now(),
            )
            metadata = {
                "strategy_version": STRATEGY_VERSION,
                "mode": proposer["mode"],
                "tone": proposer["tone"],
                "confidence": proposer["confidence"],
                "factual_claim_count": len(claims),
                "evidence_ids": draft["evidence_ids"],
                "reviewer_verdict": "approve",
                "model_call_count": call_count,
                "revision_count": revisions,
            }
            reply = AIReply(proposer["proposed_reply"], draft, metadata)
            return PipelineResult(reply, "approved", "reviewer_approved", call_count, revisions, tuple(audit))

        if revisions >= config["maximum_revisions"] or reviewer["verdict"] == "reject":
            reason = "reviewer_rejected" if reviewer["verdict"] == "reject" else "revision_limit_reached"
            if not evidence_supported:
                reason = "insufficient_claim_evidence"
            return PipelineResult(None, "no_reply", reason, call_count, revisions, tuple(audit))

        revision_request = {
            "previous_reply": proposer["proposed_reply"],
            "reviewer_reasons": reviewer["reasons"],
            "reviewer_revision_instructions": (
                reviewer["revision_instructions"]
                or "Remove every unsupported factual claim and answer the actual contribution directly."
            ),
            "evidence_status": [
                {"claim_id": row["claim_id"], "verdict": row["verdict"]}
                for row in evidence
            ],
        }
        revisions += 1


def legacy_draft_audit(
    state: object,
    *,
    state_path: Path,
    source_state_sha256: str | None = None,
) -> dict[str, Any]:
    """Enumerate V1 drafts without interpreting or migrating their contents."""
    if not isinstance(state, dict):
        raise ValueError("state document must be an object")
    raw = state.get("pending_reply_drafts", {})
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise ValueError("legacy pending_reply_drafts is not an object")
    records = []
    for key, value in sorted(raw.items()):
        records.append({
            "draft_key": str(key),
            "record_hash": value_hash(value),
            "classification": "legacy_v1_nonpostable",
            "target_id": str(value.get("target_id") or "") if isinstance(value, dict) else "",
            "candidate_source": str(value.get("candidate_source") or "") if isinstance(value, dict) else "",
        })
    return {
        "schema_version": LEGACY_DRAFT_AUDIT_SCHEMA_VERSION,
        "strategy_version": STRATEGY_VERSION,
        "source_state_path": str(state_path),
        "source_state_sha256": source_state_sha256 or hashlib.sha256(state_path.read_bytes()).hexdigest(),
        "legacy_draft_count": len(records),
        "deployment_blocked": bool(records),
        "records": records,
        "created_at": utc_now(),
        "policy": "Do not migrate, reinterpret or post a V1 draft through the AI-first strategy.",
    }


def audit_cli(argv: list[str] | None = None) -> int:
    """Write a read-only audit of legacy V1 persisted reply drafts."""
    parser = argparse.ArgumentParser(description="Audit non-migratable V1 conversational reply drafts")
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    state_bytes = stable_read_bytes(args.state)
    state = json.loads(state_bytes.decode("utf-8"))
    result = legacy_draft_audit(
        state,
        state_path=args.state,
        source_state_sha256=hashlib.sha256(state_bytes).hexdigest(),
    )
    atomic_write_json(args.output, result)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2))
    return 2 if result["deployment_blocked"] else 0


if __name__ == "__main__":
    raise SystemExit(audit_cli())
