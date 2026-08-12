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
DRAFT_SCHEMA_VERSION = 9
PROPOSER_PROMPT_VERSION = "ai-first-proposer-v16"
EVIDENCE_PROMPT_VERSION = "claim-evidence-entailment-v6"
REVIEWER_PROMPT_VERSION = "independent-reply-reviewer-v14"
NO_REPLY_REVIEW_PROMPT_VERSION = "independent-no-reply-review-v1"
CLAIM_AUDITOR_PROMPT_VERSION = "claim-inventory-auditor-v5"
VALIDATION_RETRY_PROTOCOL_VERSION = "validator-guided-retry-v1"
LEGACY_DRAFT_AUDIT_SCHEMA_VERSION = 1

_VALIDATION_RETRY_REQUIRED_ACTION = (
    "Return a complete replacement JSON object satisfying the supplied response schema and "
    "correct the stated validation failure. Do not discuss the correction or return partial fields."
)

MODES = {
    "direct_factual_answer",
    "opinion_or_principle",
    "light_humour",
    "courtesy",
    "no_reply",
}
CLAIM_AUDITED_MODES = {"opinion_or_principle", "light_humour"}
CLARIFICATION_ALLOWED_MODES = {"direct_factual_answer", "no_reply"}
CLARIFICATION_MODE_REFUSAL_REASON = "clarification_not_direct_factual_answer"
TONES = {"firm", "dry", "wry", "warm", "neutral", "light", "none"}
CONFIDENCE_LEVELS = {"low": 1, "medium": 2, "high": 3}
EVIDENCE_VERDICTS = {"supports", "contradicts", "insufficient"}
REVIEWER_VERDICTS = {"approve", "reject", "revise"}
NO_REPLY_REVIEW_VERDICTS = {"confirm_no_reply", "require_reply"}
LANES = {"mention", "hot_post_reply", "quote_tweet"}
ANSWER_TYPES = {
    "none",
    "actor",
    "action",
    "location",
    "time",
    "choice",
    "ownership",
    "quantity",
    "duration",
    "yes_no",
    "meaning",
    "source_or_attribution",
    "other",
}
SENTENCE_CLASSIFICATIONS = {
    "factual_claim",
    "checkable_generalisation",
    "mixed_factual_and_opinion",
    "opinion_or_value_judgement",
    "humour",
    "courtesy",
    "other_non_factual",
}
FACTUAL_SENTENCE_CLASSIFICATIONS = {
    "factual_claim",
    "checkable_generalisation",
    "mixed_factual_and_opinion",
}
NON_FACTUAL_BASES = {
    "none",
    "normative_judgement",
    "recommendation",
    "courtesy",
    "rhetorical_question",
    "humour_without_world_claim",
}
NON_FACTUAL_BASES_BY_CLASSIFICATION = {
    "opinion_or_value_judgement": {"normative_judgement", "recommendation"},
    "humour": {"humour_without_world_claim"},
    "courtesy": {"courtesy"},
    "other_non_factual": {"rhetorical_question"},
}
WORLD_CLAIM_CHECK_FIELDS = (
    "asserts_actor_state_or_action",
    "asserts_causal_or_predictive_relation",
    "asserts_comparison_or_outcome",
    "asserts_historical_date_or_quantity",
    "asserts_meaning_or_attribution",
    "purely_non_factual",
)

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
    re.compile(r"^\s*as\s+margaret\s+thatcher\s*[,;:]", re.IGNORECASE),
    re.compile(
        r"\b(?:speaking|writing|replying|responding)\s+as\s+margaret\s+thatcher\b",
        re.IGNORECASE,
    ),
)


class ReplyPipelineError(RuntimeError):
    """A local pipeline invariant failed; the caller must fail closed."""


class ModelCallLimitError(ReplyPipelineError):
    """The configured per-candidate model-call ceiling was reached."""


class NonRetryableReviewerResponseError(ValueError):
    """A received reviewer response contains a substantive safety conflict."""


class _ValidationRetryPromptError(ReplyPipelineError):
    """A bounded corrective retry prompt could not be constructed safely."""


class AIReply(str):
    """A reviewer-approved reply carrying its immutable draft record."""

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


def _build_validation_retry_user_prompt(
    original_user_prompt: str,
    stage: str,
    attempt_number: int,
    validator_exception: BaseException,
) -> tuple[str, dict[str, object]]:
    """Build one fixed-size correction envelope from the immutable original prompt."""
    validator_error = str(validator_exception)
    validator_error_type = type(validator_exception).__name__
    if (
        not validator_error
        or "\x00" in validator_error
        or len(validator_error) > 500
    ):
        raise _ValidationRetryPromptError(
            "validation_error_not_safe_for_retry"
        )
    try:
        validator_error_sha256 = hashlib.sha256(
            validator_error.encode("utf-8")
        ).hexdigest()
    except UnicodeEncodeError:
        raise _ValidationRetryPromptError(
            "validation_error_not_safe_for_retry"
        ) from None

    def reject_non_json_constant(_value: str) -> None:
        raise ValueError("non-JSON numeric constant")

    try:
        payload = json.loads(
            original_user_prompt,
            parse_constant=reject_non_json_constant,
        )
    except (json.JSONDecodeError, TypeError, ValueError):
        raise _ValidationRetryPromptError(
            "validation_retry_prompt_construction_failed"
        ) from None
    if not isinstance(payload, dict) or "validation_correction" in payload:
        raise _ValidationRetryPromptError(
            "validation_retry_prompt_construction_failed"
        )

    payload["validation_correction"] = {
        "protocol_version": VALIDATION_RETRY_PROTOCOL_VERSION,
        "stage": stage,
        "attempt_number": attempt_number,
        "previous_response_rejected": True,
        "validator_error_type": validator_error_type,
        "validator_error": validator_error,
        "validator_error_sha256": validator_error_sha256,
        "required_action": _VALIDATION_RETRY_REQUIRED_ACTION,
    }
    try:
        corrective_user_prompt = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
        )
        original_user_prompt_sha256 = hashlib.sha256(
            original_user_prompt.encode("utf-8")
        ).hexdigest()
        corrective_user_prompt_sha256 = hashlib.sha256(
            corrective_user_prompt.encode("utf-8")
        ).hexdigest()
    except (TypeError, ValueError, UnicodeEncodeError):
        raise _ValidationRetryPromptError(
            "validation_retry_prompt_construction_failed"
        ) from None
    if original_user_prompt_sha256 == corrective_user_prompt_sha256:
        raise _ValidationRetryPromptError(
            "validation_retry_prompt_construction_failed"
        )
    return corrective_user_prompt, {
        "validation_retry_protocol_version": VALIDATION_RETRY_PROTOCOL_VERSION,
        "validator_error_type": validator_error_type,
        "validator_error": validator_error,
        "validator_error_sha256": validator_error_sha256,
        "original_user_prompt_sha256": original_user_prompt_sha256,
        "corrective_user_prompt_sha256": corrective_user_prompt_sha256,
    }


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
        "direct_factual_question_present": {"type": "boolean"},
        "requested_answer_type": {"type": "string", "enum": sorted(ANSWER_TYPES)},
        "direct_answer_text": {"type": "string", "maxLength": maximum_reply_length},
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


def world_claim_checks_schema() -> dict[str, Any]:
    """Return redundant per-sentence checks for externally testable claims."""
    return _strict_object(
        {field: {"type": "boolean"} for field in WORLD_CLAIM_CHECK_FIELDS},
        list(WORLD_CLAIM_CHECK_FIELDS),
    )


def claim_auditor_schema(maximum_claims: int) -> dict[str, Any]:
    """Return the strict schema for the fresh claim-inventory audit."""
    sentence_assessment = _strict_object(
        {
            "sentence_text": {"type": "string", "minLength": 1, "maxLength": 500},
            "factual_claims": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 500},
                "maxItems": maximum_claims,
            },
            "world_claim_checks": world_claim_checks_schema(),
        },
        ["sentence_text", "factual_claims", "world_claim_checks"],
    )
    return _strict_object(
        {
            "actual_factual_claims": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 500},
                "maxItems": maximum_claims,
            },
            "sentence_assessments": {
                "type": "array",
                "items": sentence_assessment,
                "minItems": 1,
                "maxItems": 3,
            },
        },
        ["actual_factual_claims", "sentence_assessments"],
    )


def reviewer_schema(maximum_claims: int) -> dict[str, Any]:
    """Return the strict structured-output schema for independent review."""
    sentence_assessment = _strict_object(
        {
            "sentence_text": {"type": "string", "minLength": 1, "maxLength": 500},
            "classification": {
                "type": "string",
                "enum": sorted(SENTENCE_CLASSIFICATIONS),
            },
            "factual_claims": {
                "type": "array",
                "items": {"type": "string", "minLength": 1, "maxLength": 500},
                "maxItems": maximum_claims,
            },
            "non_factual_basis": {
                "type": "string",
                "enum": sorted(NON_FACTUAL_BASES),
            },
            "world_claim_checks": world_claim_checks_schema(),
        },
        [
            "sentence_text", "classification", "factual_claims",
            "non_factual_basis", "world_claim_checks",
        ],
    )
    properties = {
        "verdict": {"type": "string", "enum": sorted(REVIEWER_VERDICTS)},
        "summary": {"type": "string", "maxLength": 800},
        "reasons": {"type": "array", "items": {"type": "string", "maxLength": 300}, "maxItems": 12},
        "actual_factual_claims": {"type": "array", "items": {"type": "string", "maxLength": 500}, "maxItems": maximum_claims},
        "unsupported_factual_claims": {"type": "array", "items": {"type": "string", "maxLength": 500}, "maxItems": maximum_claims},
        "sentence_assessments": {
            "type": "array",
            "items": sentence_assessment,
            "minItems": 1,
            "maxItems": 3,
        },
        "direct_factual_question_present": {"type": "boolean"},
        "requested_answer_type": {"type": "string", "enum": sorted(ANSWER_TYPES)},
        "direct_answer_complete": {"type": "boolean"},
        "direct_answer_text": {"type": "string", "maxLength": 500},
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


def no_reply_review_schema() -> dict[str, Any]:
    """Return the strict schema for independent review of proposed silence."""
    properties = {
        "verdict": {
            "type": "string",
            "enum": sorted(NO_REPLY_REVIEW_VERDICTS),
        },
        "reasons": {
            "type": "array",
            "items": {"type": "string", "minLength": 1, "maxLength": 300},
            "minItems": 1,
            "maxItems": 8,
        },
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
        "direct_factual_question_present", "requested_answer_type",
        "direct_answer_text",
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
        ("direct_answer_text", maximum_reply_length),
        ("exact_thatcher_wording", maximum_reply_length), ("no_reply_reason", 500),
    ):
        if not isinstance(item.get(field), str) or len(item[field]) > maximum:
            raise ValueError(f"proposer {field} is invalid")
    if not item["interpretation"].strip():
        raise ValueError("proposer interpretation is invalid")
    if type(item.get("exact_thatcher_wording_used")) is not bool:
        raise ValueError("proposer exact_thatcher_wording_used must be boolean")
    if type(item.get("direct_factual_question_present")) is not bool:
        raise ValueError("proposer direct_factual_question_present must be boolean")
    if item.get("requested_answer_type") not in ANSWER_TYPES:
        raise ValueError("proposer requested_answer_type is invalid")
    if not isinstance(item.get("direct_answer_text"), str):
        raise ValueError("proposer direct_answer_text is invalid")
    if item["direct_factual_question_present"]:
        if item["requested_answer_type"] == "none":
            raise ValueError("direct factual question requires an answer type")
    elif item["requested_answer_type"] != "none" or item["direct_answer_text"]:
        raise ValueError("non-direct contribution cannot declare a direct answer")
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
        compact_reply = " ".join(str(item.get("proposed_reply") or "").split())
        compact_claim = " ".join(validated["claim_text"].split())
        if compact_claim not in compact_reply:
            raise ValueError("proposer factual claim must occur verbatim in the reply")
        normalised_claim = " ".join(validated["claim_text"].casefold().split())
        if normalised_claim in claim_texts:
            raise ValueError("proposer factual claims must be unique")
        claim_texts.add(normalised_claim)
    if item["mode"] == "no_reply":
        if item["proposed_reply"] or claims or item["exact_thatcher_wording_used"]:
            raise ValueError("no_reply must not contain a draft or factual claims")
        if item["exact_thatcher_wording"] or not item["no_reply_reason"].strip():
            raise ValueError("no_reply requires a reason and no quotation wording")
        if item["direct_answer_text"]:
            raise ValueError("no_reply cannot contain a direct answer")
    else:
        if not item["proposed_reply"].strip() or item["no_reply_reason"]:
            raise ValueError("a proposed reply requires text and an empty no_reply_reason")
        if CONFIDENCE_LEVELS[item["confidence"]] < CONFIDENCE_LEVELS["medium"]:
            raise ValueError("a proposed reply requires at least medium proposer confidence")
        if item["mode"] == "direct_factual_answer" and not claims:
            raise ValueError("direct_factual_answer requires at least one factual claim")
        if item["direct_factual_question_present"] != (item["mode"] == "direct_factual_answer"):
            raise ValueError("direct factual question and mode contradict each other")
        if item["mode"] == "direct_factual_answer":
            sentences = split_reply_sentences(item["proposed_reply"])
            if not sentences or item["direct_answer_text"] != sentences[0]:
                raise ValueError("direct factual answer must bind its complete first sentence")
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


def _validate_world_claim_checks(
    value: object,
    *,
    has_factual_claims: bool,
    label: str,
) -> None:
    """Validate redundant world-claim flags against the declared inventory."""
    if (
        not isinstance(value, dict)
        or set(value) != set(WORLD_CLAIM_CHECK_FIELDS)
        or any(type(value[field]) is not bool for field in WORLD_CLAIM_CHECK_FIELDS)
    ):
        raise ValueError(f"{label} world-claim checks are invalid")
    has_world_claim = any(
        value[field]
        for field in WORLD_CLAIM_CHECK_FIELDS
        if field != "purely_non_factual"
    )
    if value["purely_non_factual"] == has_world_claim:
        raise NonRetryableReviewerResponseError(
            f"{label} world-claim checks contradict each other"
        )
    if has_world_claim != has_factual_claims:
        raise NonRetryableReviewerResponseError(
            f"{label} world-claim checks contradict its claim inventory"
        )


def validate_claim_auditor(
    value: object,
    *,
    maximum_claims: int,
    proposed_reply: str,
) -> dict[str, Any]:
    """Validate a fresh claim audit against every exact reply sentence."""
    item = _parse_object(value, "claim auditor")
    _validate_exact_keys(
        item,
        {"actual_factual_claims", "sentence_assessments"},
        "claim auditor",
    )
    actual_claims = item.get("actual_factual_claims")
    assessments = item.get("sentence_assessments")
    if (
        not isinstance(actual_claims, list)
        or len(actual_claims) > maximum_claims
        or any(
            not isinstance(claim_text, str)
            or not claim_text.strip()
            or len(claim_text) > 500
            for claim_text in actual_claims
        )
    ):
        raise ValueError("claim auditor factual claims are invalid")
    if not isinstance(assessments, list) or not 1 <= len(assessments) <= 3:
        raise ValueError("claim auditor sentence assessments are invalid")
    assessed_sentences: list[str] = []
    assessed_claims: list[str] = []
    for assessment in assessments:
        if not isinstance(assessment, dict):
            raise ValueError("claim auditor sentence assessment must be an object")
        _validate_exact_keys(
            assessment,
            {"sentence_text", "factual_claims", "world_claim_checks"},
            "claim auditor sentence assessment",
        )
        sentence_text = assessment.get("sentence_text")
        sentence_claims = assessment.get("factual_claims")
        if not isinstance(sentence_text, str) or not sentence_text.strip() or len(sentence_text) > 500:
            raise ValueError("claim auditor sentence text is invalid")
        if (
            not isinstance(sentence_claims, list)
            or len(sentence_claims) > maximum_claims
            or any(
                not isinstance(claim_text, str)
                or not claim_text.strip()
                or len(claim_text) > 500
                for claim_text in sentence_claims
            )
        ):
            raise ValueError("claim auditor sentence claims are invalid")
        compact_sentence = " ".join(sentence_text.split())
        if any(" ".join(claim_text.split()) not in compact_sentence for claim_text in sentence_claims):
            raise ValueError("claim auditor factual claim is not a verbatim sentence clause")
        _validate_world_claim_checks(
            assessment.get("world_claim_checks"),
            has_factual_claims=bool(sentence_claims),
            label="claim auditor sentence",
        )
        assessed_sentences.append(sentence_text)
        assessed_claims.extend(sentence_claims)
    if assessed_sentences != split_reply_sentences(proposed_reply):
        raise ValueError("claim auditor does not cover the exact reply")
    if assessed_claims != actual_claims:
        raise NonRetryableReviewerResponseError(
            "claim auditor sentence inventory is incomplete or out of order"
        )
    return item


def validate_reviewer(
    value: object,
    *,
    maximum_claims: int,
    proposed_reply: str | None = None,
) -> dict[str, Any]:
    """Validate an independent reviewer response and its internal consistency."""
    item = _parse_object(value, "reviewer")
    expected = {
        "verdict", "summary", "reasons", "actual_factual_claims",
        "unsupported_factual_claims", "sentence_assessments",
        "direct_factual_question_present", "requested_answer_type",
        "direct_answer_complete", "direct_answer_text", "topically_relevant",
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
    assessments = item.get("sentence_assessments")
    if not isinstance(assessments, list) or not 1 <= len(assessments) <= 3:
        raise ValueError("reviewer sentence_assessments is invalid")
    assessed_claims: list[str] = []
    assessed_sentences: list[str] = []
    for assessment in assessments:
        if not isinstance(assessment, dict):
            raise ValueError("reviewer sentence assessment must be an object")
        _validate_exact_keys(
            assessment,
            {
                "sentence_text", "classification", "factual_claims",
                "non_factual_basis", "world_claim_checks",
            },
            "reviewer sentence assessment",
        )
        sentence_text = assessment.get("sentence_text")
        classification = assessment.get("classification")
        sentence_claims = assessment.get("factual_claims")
        non_factual_basis = assessment.get("non_factual_basis")
        world_claim_checks = assessment.get("world_claim_checks")
        if not isinstance(sentence_text, str) or not sentence_text.strip() or len(sentence_text) > 500:
            raise ValueError("reviewer assessed sentence text is invalid")
        if classification not in SENTENCE_CLASSIFICATIONS:
            raise ValueError("reviewer sentence classification is invalid")
        if (
            not isinstance(sentence_claims, list)
            or len(sentence_claims) > maximum_claims
            or any(
                not isinstance(claim_text, str)
                or not claim_text.strip()
                or len(claim_text) > 500
                for claim_text in sentence_claims
            )
        ):
            raise ValueError("reviewer sentence factual claims are invalid")
        compact_sentence = " ".join(sentence_text.split())
        if any(" ".join(claim_text.split()) not in compact_sentence for claim_text in sentence_claims):
            raise ValueError("reviewer factual claim is not a verbatim clause of its sentence")
        is_factual = classification in FACTUAL_SENTENCE_CLASSIFICATIONS
        if is_factual != bool(sentence_claims):
            raise NonRetryableReviewerResponseError(
                "reviewer sentence classification contradicts its factual claims"
            )
        _validate_world_claim_checks(
            world_claim_checks,
            has_factual_claims=is_factual,
            label="reviewer sentence",
        )
        expected_bases = (
            {"none"}
            if is_factual
            else NON_FACTUAL_BASES_BY_CLASSIFICATION.get(str(classification), set())
        )
        if non_factual_basis not in expected_bases:
            raise NonRetryableReviewerResponseError(
                "reviewer sentence classification contradicts its non-factual basis"
            )
        assessed_sentences.append(sentence_text)
        assessed_claims.extend(sentence_claims)
    if assessed_claims != item["actual_factual_claims"]:
        raise NonRetryableReviewerResponseError(
            "reviewer sentence claim inventory is incomplete or out of order"
        )
    if proposed_reply is not None and assessed_sentences != split_reply_sentences(proposed_reply):
        raise ValueError("reviewer sentence assessment does not cover the exact reply")
    if item.get("requested_answer_type") not in ANSWER_TYPES:
        raise ValueError("reviewer requested_answer_type is invalid")
    if not isinstance(item.get("direct_answer_text"), str) or len(item["direct_answer_text"]) > 500:
        raise ValueError("reviewer direct_answer_text is invalid")
    if item.get("direct_factual_question_present"):
        if item["requested_answer_type"] == "none":
            raise ValueError("reviewer direct factual question requires an answer type")
        if proposed_reply is not None:
            sentences = split_reply_sentences(proposed_reply)
            if item["direct_answer_complete"] and (
                not sentences or item["direct_answer_text"] != sentences[0]
            ):
                raise ValueError("reviewer must bind the complete first sentence")
            if not item["direct_answer_complete"] and item["direct_answer_text"] not in {
                "", sentences[0] if sentences else "",
            }:
                raise ValueError("reviewer incomplete direct answer text is invalid")
    elif (
        item["requested_answer_type"] != "none"
        or item["direct_answer_complete"]
        or item["direct_answer_text"]
    ):
        raise ValueError("reviewer non-direct contribution cannot declare a direct answer")
    if item["contains_unsupported_factual_claims"] != bool(item["unsupported_factual_claims"]):
        raise NonRetryableReviewerResponseError(
            "reviewer unsupported-claim fields contradict each other"
        )
    if any(
        claim_text not in item["actual_factual_claims"]
        for claim_text in item["unsupported_factual_claims"]
    ):
        raise ValueError("reviewer unsupported claim is absent from the actual claim inventory")
    boolean_fields = expected - {
        "verdict", "summary", "reasons", "actual_factual_claims",
        "unsupported_factual_claims", "sentence_assessments",
        "requested_answer_type", "direct_answer_text", "revision_instructions",
    }
    if any(type(item.get(field)) is not bool for field in boolean_fields):
        raise ValueError("reviewer boolean fields are invalid")
    if item["direct_factual_question_present"] and not item["direct_answer_complete"] and item["verdict"] == "approve":
        raise NonRetryableReviewerResponseError(
            "reviewer cannot approve an evasive direct answer"
        )
    if item["verdict"] == "revise" and not item["revision_instructions"].strip():
        raise ValueError("reviewer revision requires instructions")
    if item["verdict"] == "approve" and item["revision_instructions"]:
        raise ValueError("reviewer approval cannot include revision instructions")
    if item["verdict"] == "approve" and not reviewer_checks_approve(item):
        raise NonRetryableReviewerResponseError(
            "reviewer approve verdict contradicts its safety findings"
        )
    return item


def validate_no_reply_review(value: object) -> dict[str, Any]:
    """Validate an independent decision to confirm silence or require a reply."""
    item = _parse_object(value, "no-reply reviewer")
    _validate_exact_keys(
        item,
        {"verdict", "reasons", "revision_instructions"},
        "no-reply reviewer",
    )
    if item.get("verdict") not in NO_REPLY_REVIEW_VERDICTS:
        raise ValueError("no-reply reviewer verdict is invalid")
    reasons = item.get("reasons")
    if (
        not isinstance(reasons, list)
        or not 1 <= len(reasons) <= 8
        or any(
            not isinstance(reason, str)
            or not reason.strip()
            or len(reason) > 300
            for reason in reasons
        )
    ):
        raise ValueError("no-reply reviewer reasons are invalid")
    instructions = item.get("revision_instructions")
    if not isinstance(instructions, str) or len(instructions) > 800:
        raise ValueError("no-reply reviewer revision instructions are invalid")
    if item["verdict"] == "require_reply" and not instructions.strip():
        raise ValueError("require_reply needs revision instructions")
    if item["verdict"] == "confirm_no_reply" and instructions:
        raise ValueError("confirm_no_reply cannot include revision instructions")
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
            review.get("direct_factual_question_present") is False
            or review.get("direct_answer_complete") is True
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


def split_reply_sentences(text: str) -> list[str]:
    """Split compact reply text at user-visible sentence boundaries."""
    compact = " ".join(str(text or "").split())
    if not compact:
        return []
    sentences: list[str] = []
    sentence_start = 0
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
        while index < len(compact) and compact[index] in {
            '"', "'", "\N{RIGHT SINGLE QUOTATION MARK}",
            "\N{RIGHT DOUBLE QUOTATION MARK}",
            "\N{RIGHT-POINTING DOUBLE ANGLE QUOTATION MARK}", ")", "]", "}",
        }:
            index += 1
        sentence = compact[sentence_start:index].strip()
        if sentence:
            sentences.append(sentence)
        while index < len(compact) and compact[index].isspace():
            index += 1
        sentence_start = index
    trailing = compact[sentence_start:].strip()
    if trailing:
        sentences.append(trailing)
    return sentences or [compact]


def sentence_count(text: str) -> int:
    """Count user-visible sentences, including lower-case starts."""
    return len(split_reply_sentences(text))


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
    resolved_quotation: dict[str, Any] | None,
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
        "Priority order: 1. factual and safety correctness; 2. direct relevance; 3. specificity and added value; "
        "4. brevity. Civil, intelligible, relevant and safe contributions normally receive replies. No question, "
        "disagreement, new factual claim, new subject or @mention is required, and absence alone never warrants "
        "no_reply. Quote-tweets remain first-class engagement. Use courtesy only for essentially social thanks, "
        "praise, affection, sympathy, remembrance, greetings, simple support or celebration. For a purely social "
        "contribution, short natural courtesy, even generic, is acceptable without manufactured politics or "
        "added-value elaboration. Argument, analogy, distinction, criticism, recommendation, policy observation, "
        "political observation, moral proposition and reasoned agreement normally use opinion_or_principle, not mere "
        "ceremonial courtesy. Non-social replies must pass Specificity: not sensible beneath several unrelated "
        "contributions; and Added value: more than paraphrase, thanks or acknowledgement. Add a sharp distinction, "
        "concise principle, particular recommendation or standard, pointed rhetorical question or contribution-derived "
        "dry turn; no "
        "factual assertion is required. Outside social courtesy, avoid stock acknowledgements: 'well noted', "
        "'well made', 'point taken', 'thank you for sharing', 'an important reminder'. These examples are illustrative, "
        "not a permanent phrase "
        "blacklist: interchangeable or empty wording, not ordinary reused words, is defective. Do not reuse "
        "conspicuous sentence frames from recent replies. Motive, value, political-principle and moral-concept "
        "questions use opinion_or_principle unless factual; wording alone is not direct_factual_question_present. Put "
        "invited yes/no or qualification first, then explain the principle. Keep normative replies claim-free: assert "
        "no historical proof, inevitable effect, habitual behaviour or result. "
        "A normative wrapper does not hide a factual premise: 'a nation's history of defending freedom' asserts "
        "that the nation defended freedom, and 'the programme its conference endorsed' asserts an endorsement. "
        "A claim-free draft must omit such historical noun phrases and action-bearing relative clauses; otherwise "
        "copy the complete embedded premise into factual_claims for evidence. "
        "For a relevant contribution containing an unsupported prediction, causal generalisation or other factual "
        "assertion, do not repeat or endorse it; when safe and natural, address its theme in claim-free normative "
        "language. Choose no_reply for spam or advertising; incoherence or unintelligibility; abuse or harassment; "
        "clear bad-faith bait; repetition demonstrated by the bounded thread; dangerous amplification of serious "
        "unsupported accusations or conspiracy claims; wholly unrelated material; or inability to produce a safe, "
        "relevant and original response. Do not weaken or broaden these safeguards. Do not infer repetition from "
        "quoted text or unrelated recent replies; disagreement alone is not bait. "
        "Use light_humour only for a natural, contribution-specific, claim-free dry or wry line arising from a "
        "particular word, contrast, irony or implication in the contribution. Do not force humour. Do not use wit for "
        "grief, distress, abuse, serious unsupported allegations or sensitive factual correction. A draft "
        "claiming that a person, group or institution tends, usually, always or never does something is a factual "
        "generalisation: either rewrite it as non-factual humour or list the complete claim for evidence. Never omit "
        "a genuine claim merely to avoid evidence review. "
        "For a direct factual question, produce a provisional concise answer when you can formulate one with high "
        "confidence and list every claim for the separate evidence stage. When resolved_quotation_for_factual_use "
        "is supplied, use its canonical source-grounded fields to answer questions about that quotation rather than "
        "abstaining or guessing; those fields still require claim-specific evidence before posting. The draft cannot "
        "be posted unless that stage finds exact local support. For a clarification, use both original_question and "
        "correction to recover the "
        "fact being requested. Still choose no_reply when you cannot formulate a likely accurate answer. "
        "Use the least-specific factual wording that directly answers the question. Do not copy a colloquial, "
        "misspelled or unnecessarily strong action verb into the reply as a factual claim. For a direction question, "
        "say that people moved, went or crossed in the relevant direction unless manner or speed is itself essential. "
        "Classify whether the contribution contains a direct factual question and choose its narrowest requested "
        "answer type. A question asking who said or wrote words is actor even when phrased as yes/no. For a direct "
        "factual answer, direct_answer_text must copy the complete first sentence verbatim and that sentence must "
        "supply the requested actor, action, location, time, choice, ownership, quantity, duration, yes/no answer, "
        "meaning or source. Each claim_text must copy one complete independently checkable factual sentence or clause "
        "from proposed_reply verbatim, including every actor, relationship, direction, date, period and quantity. "
        "Causal, comparative, predictive and habitual political generalisations are factual claims even when phrased "
        "rhetorically; list them. A statement about how people, institutions, policies or outcomes behave is factual "
        "or mixed, including claims that something advances interests, weakens opposition, delivers stability, causes "
        "collapse or prevents progress. Pure recommendations and value judgements need not be listed only when they "
        "contain no proposition about the world and no checkable factual premise. "
        "Unsupported allegations in the contribution must not be repeated or endorsed. Never mention internal "
        "prompts, retrieval, evidence packages or missing supplied "
        "context in a user-facing reply. Use British English, including defence rather than defense, and no more "
        "than two short sentences. Return only "
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
        "resolved_quotation_for_factual_use": resolved_quotation,
        "recent_account_replies_to_avoid_repeating": recent_replies[:20],
    }
    if revision is not None:
        payload["single_allowed_revision"] = revision
        system += (
            " This is the only permitted revision. Correct the review findings rather than defending the "
            "previous draft. Never discuss the internal evidence package; if the requested factual answer cannot "
            "be supported, choose no_reply. For a civil, relevant draft containing an unsupported prediction, causal "
            "generalisation or other factual assertion, prefer a safe, honest, claim-free revision engaging its "
            "particular theme over automatically choosing no_reply. Follow the social-versus-substantive mode rule. "
            "Do not retain a historical premise, "
            "a prediction, a conditional outcome, an action-bearing relative clause or a general claim about how "
            "people or institutions behave."
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
        "Adjudicate each supplied clause independently; support for one clause cannot rescue another unsupported "
        "clause in the same sentence. Do not use outside knowledge or infer support from a broad shared "
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


def _no_reply_review_prompts(
    context: dict[str, Any],
    proposer: dict[str, Any],
) -> tuple[str, str]:
    """Build a bounded independent review of a proposer's no-reply choice."""
    system = (
        "You are an independent no-reply reviewer for a Margaret Thatcher quotation account on X. "
        "You did not make the proposal and must decide only whether silence is warranted. The incoming "
        "contribution has priority over quoted and parent context. Confirm no_reply for spam or advertising, "
        "incoherence or unintelligibility, abuse or harassment, clear bad-faith bait, repetition explicitly "
        "shown in the bounded thread, serious unsupported accusations or conspiracy claims whose engagement "
        "would dangerously amplify them, wholly unrelated material, or when no safe and genuinely relevant "
        "response is possible. Do not weaken those safeguards. Absence of a question, disagreement, challenge "
        "or new factual matter is not sufficient by itself to confirm silence. Civil, intelligible and relevant "
        "agreement, support, appreciation, nostalgia, thanks, a friendly comparison or a thoughtful related "
        "observation normally warrants a brief natural response. An unsupported claim must not be repeated or "
        "endorsed, but require a reply when a safe claim-free acknowledgement, recommendation or value judgement "
        "can address the underlying theme without amplification. Use confirm_no_reply only when silence is "
        "warranted; use require_reply when the single revision proposer should produce a safe, relevant response. "
        "You must not write the public reply. For require_reply, give concise revision instructions to the separate "
        "proposer. Return only the required JSON object."
    )
    payload = {
        "context_sections": {
            "incoming_contribution_to_answer": context["incoming_contribution"],
            "quoted_post_context_only": context["quoted_post"],
            "bounded_parent_thread_context_only": context["parent_thread"],
            "clarification_request_if_any": context["clarification_request"],
        },
        "proposer_interpretation_untrusted": proposer["interpretation"],
        "proposer_no_reply_reason_untrusted": proposer["no_reply_reason"],
    }
    return system, json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _claim_auditor_prompts(proposed_reply: str) -> tuple[str, str]:
    """Build a fresh factual-claim audit with no proposer classifications."""
    system = (
        "You are a fresh claim-inventory auditor. You see only a proposed public reply, not the proposer's "
        "claim list or reasoning. Copy every visible sentence exactly and identify every externally checkable "
        "factual clause verbatim. Complete all five world-claim checks independently for every sentence before "
        "deciding whether it is purely non-factual. Treat general propositions as factual when they say what a "
        "person, group, institution, policy or society does, senses, requires or produces; when they assert a "
        "cause, prediction, condition, comparison or outcome; or when they make a historical, date or quantity "
        "claim. Meaning or attribution includes claims about what quoted words mean and who authored, said or "
        "delivered them. A noun phrase asserting a record or history of an actor doing something asserts that the "
        "action occurred and is factual; calling that record a reason or warrant does not erase the historical premise. "
        "For example, 'A nation's history of defending freedom ought to be valued' contains the factual clause "
        "'A nation's history of defending freedom'. 'A party ought to be judged by the programme its conference "
        "endorsed' contains the factual clause 'the programme its conference endorsed'. Mark "
        "asserts_actor_state_or_action=true and copy those exact contiguous clauses verbatim; never rewrite their "
        "grammar or substitute an inferred actor. "
        "In particular, 'essential to survival or prosperity', 'serves everyone better', 'rests on mutual "
        "advantage', 'rescued Europe', 'erodes independence', and claims about what supporters sense are world "
        "claims, even when embedded in political opinion. A sentence is purely_non_factual only when it is wholly "
        "a value judgement, recommendation, courtesy, rhetorical question or humour with no proposition about "
        "the world. Words such as should or must do not make an embedded factual premise non-factual. Flatten all "
        "sentence claims into actual_factual_claims in reading order. Return only the required JSON object."
    )
    return system, json.dumps(
        {"proposed_reply_to_audit": proposed_reply},
        ensure_ascii=False,
        sort_keys=True,
    )


def _reviewer_prompts(
    context: dict[str, Any],
    proposer: dict[str, Any],
    evidence: list[dict[str, Any]],
    resolved_quotation: dict[str, Any] | None,
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
        "is accurate, copy its claim_text exactly. Assess every visible reply sentence exactly once and copy the "
        "sentence text verbatim. Classify causal, comparative, predictive and habitual political generalisations as "
        "checkable; identify every independently checkable clause within each sentence. The flattened sentence claim "
        "inventory must exactly equal actual_factual_claims in reading order. Complete every world_claim_checks field "
        "independently before choosing the sentence classification. Actor states or actions include claims about what "
        "supporters sense or what institutions do. Causal or predictive relations include claims that a policy is "
        "essential to survival, prosperity, stability or decline. Comparisons and outcomes include claims that one "
        "course serves people or a country better, or that exchange rests on a stated mechanism. Historical claims "
        "include assertions that a country rescued another. Meaning or attribution includes claims about what quoted "
        "words mean and who authored, said or delivered them. A noun phrase asserting a record or history of an "
        "actor doing something contains a factual actor-action premise even when the sentence uses that record as "
        "a reason, warrant or value judgement. Likewise, an action-bearing relative clause remains factual inside a "
        "recommendation: 'the programme its conference endorsed' asserts that the conference endorsed it. Mark "
        "asserts_actor_state_or_action=true and list these embedded clauses. If any of those five checks is true, purely_non_factual "
        "must be false, the classification must be factual or mixed, and all factual clauses must be listed. If all "
        "five are false, purely_non_factual must be true. For each factual or mixed sentence set "
        "non_factual_basis=none. A sentence may use another non_factual_basis only when it is wholly a normative "
        "judgement, recommendation, courtesy, rhetorical question or humour without any proposition about how people, "
        "institutions, policies or outcomes behave. Causal or habitual claims such as advancing interests, weakening "
        "opposition, delivering stability, producing collapse or preventing progress remain factual even when they "
        "sound like political opinion. Independently classify any direct factual "
        "question and its narrowest requested answer type. For a direct question, copy the complete first sentence to "
        "direct_answer_text only when that sentence is the complete answer; otherwise leave direct_answer_text empty "
        "and mark direct_answer_complete=false. Mark it complete only if it actually supplies the requested identity, "
        "direction, date, quantity or other requested fact. A question about who said or wrote words requires the "
        "author's identity, even when phrased as yes/no. Principle/value/motive questions use opinion_or_principle "
        "unless factual; interrogative form alone isn't direct_factual_question_present. Put invited yes/no first; "
        "revise evasion. Courtesy suits essentially social praise, affection, remembrance, gratitude, sympathy, "
        "greetings or support; approve brief natural courtesy without political lecture; brevity isn't defective. "
        "Substantive argument, analogy, distinction, criticism, recommendation, policy/political observation, "
        "moral proposition and reasoned agreement normally deserve specific opinion_or_principle, not courtesy. "
        "For non-social replies, harmlessness and topicality are necessary yet insufficient. Require Specificity: not "
        "fitting several unrelated contributions; and Added value: beyond paraphrase or acknowledgement. Natural, "
        "contribution-derived, claim-free dry/wry wit is optional; reject generic banter; never request humour for "
        "grief, distress or serious allegations. Revise safe stock acknowledgement or unsupported assertions: name "
        "its particular idea/distinction/principle, require non-template claim-free wording, prescribe no complete "
        "reply or new factual claim. Reject spam, incoherence, abuse, bad-faith bait, thread-proven "
        "repetition, dangerous unsupported-accusation amplification, unrelatedness or uncorrectable "
        "safety/evidence/relevance defects. revision_instructions: non-empty for revise, empty for approve. Revise if "
        "safely correctable once; otherwise reject. Reject is terminal; instructions optional. "
        "Require British English in the public reply, including defence rather than defense; request revision for "
        "an American spelling. "
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
        "proposer_direct_factual_question_present": proposer["direct_factual_question_present"],
        "proposer_requested_answer_type": proposer["requested_answer_type"],
        "proposer_direct_answer_text": proposer["direct_answer_text"],
        "proposer_listed_factual_claims_untrusted": proposer["factual_claims"],
        "exact_thatcher_wording_used": proposer["exact_thatcher_wording_used"],
        "exact_thatcher_wording": proposer["exact_thatcher_wording"],
        "evidence_package": evidence,
        "resolved_quotation_for_independent_check": resolved_quotation,
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


def direct_answer_binding_error(
    proposer: dict[str, Any],
    reviewer: dict[str, Any],
    resolved_quotation: dict[str, Any] | None,
) -> str | None:
    """Return a deterministic mismatch in direct-question metadata or identity."""
    proposer_fields = (
        proposer["direct_factual_question_present"],
        proposer["requested_answer_type"],
        proposer["direct_answer_text"],
    )
    reviewer_fields = (
        reviewer["direct_factual_question_present"],
        reviewer["requested_answer_type"],
        reviewer["direct_answer_text"],
    )
    if proposer_fields != reviewer_fields:
        return "direct_answer_metadata_mismatch"
    if not proposer["direct_factual_question_present"]:
        return None
    if not reviewer["direct_answer_complete"]:
        return "direct_answer_incomplete"
    if proposer["requested_answer_type"] == "actor" and resolved_quotation is not None:
        speaker_words = tuple(
            word.casefold()
            for word in re.findall(r"[^\W_]+", str(resolved_quotation.get("speaker") or ""))
        )
        answer_words = tuple(
            word.casefold()
            for word in re.findall(r"[^\W_]+", proposer["direct_answer_text"])
        )
        if speaker_words and not any(
            answer_words[index:index + len(speaker_words)] == speaker_words
            for index in range(len(answer_words) - len(speaker_words) + 1)
        ):
            return "direct_answer_missing_resolved_actor"
    return None


def build_draft_record(
    *,
    context: dict[str, Any],
    proposer: dict[str, Any],
    evidence: list[dict[str, Any]],
    reviewer: dict[str, Any],
    resolved_quotation: dict[str, Any] | None,
    config: dict[str, Any],
    model_call_count: int,
    revision_count: int,
    creation_time: str,
    retrieved_count: int | None = None,
    claim_auditor: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the immutable persisted-draft record for an approved reply."""
    if retrieved_count is not None and (
        type(retrieved_count) is not int or retrieved_count < 0
    ):
        raise ValueError("persisted draft retrieved count is invalid")
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
        "direct_factual_question_present": proposer["direct_factual_question_present"],
        "requested_answer_type": proposer["requested_answer_type"],
        "direct_answer_text": proposer["direct_answer_text"],
        "factual_claims": proposer["factual_claims"],
        "exact_thatcher_wording_used": proposer["exact_thatcher_wording_used"],
        "exact_thatcher_wording": proposer["exact_thatcher_wording"],
        "evidence_ids": evidence_ids,
        "claim_evidence": claim_evidence,
        "source_hashes": source_hashes,
        "evidence_input_hashes": evidence_input_hashes,
        "reviewer_verdict": reviewer["verdict"],
        "reviewer_reasons": reviewer["reasons"],
        "reviewer_sentence_assessments": reviewer["sentence_assessments"],
        "claim_auditor_sentence_assessments": (
            claim_auditor["sentence_assessments"] if claim_auditor is not None else []
        ),
        "resolved_quote_id": (
            str(resolved_quotation["quote_id"]) if resolved_quotation is not None else None
        ),
        "resolved_quote_context_hash": (
            str(resolved_quotation["resolved_context_hash"])
            if resolved_quotation is not None
            else None
        ),
        "proposer_model": config["proposer_model"],
        "evidence_model": config["evidence_model"],
        "reviewer_model": config["reviewer_model"],
        "claim_auditor_model": config["reviewer_model"],
        "proposer_prompt_version": PROPOSER_PROMPT_VERSION,
        "evidence_prompt_version": EVIDENCE_PROMPT_VERSION,
        "reviewer_prompt_version": REVIEWER_PROMPT_VERSION,
        "claim_auditor_prompt_version": CLAIM_AUDITOR_PROMPT_VERSION,
        "model_call_count": model_call_count,
        "revision_count": revision_count,
        "creation_time": creation_time,
    }
    if retrieved_count is not None:
        record["retrieved_count"] = retrieved_count
    record["approval_hash"] = value_hash(record)
    return record


def evidence_telemetry(
    draft_record: dict[str, Any],
    repository: EvidenceRepository,
) -> dict[str, Any]:
    """Summarise stored retrieval and selected evidence without new retrieval."""

    evidence_ids = draft_record.get("evidence_ids")
    retrieved_count = draft_record.get("retrieved_count")
    if type(retrieved_count) is not int or retrieved_count < 0:
        retrieved_count = None
    if not isinstance(evidence_ids, list):
        return {
            "evidence_confidence": "unavailable",
            "retrieved_count": retrieved_count,
            "evidence_reference_count": None,
        }
    if not evidence_ids:
        return {
            "evidence_confidence": "none",
            "retrieved_count": retrieved_count,
            "evidence_reference_count": 0,
        }
    passages = [repository.passages.get(str(value)) for value in evidence_ids]
    if any(passage is None for passage in passages):
        return {
            "evidence_confidence": "unavailable",
            "retrieved_count": retrieved_count,
            "evidence_reference_count": len(evidence_ids),
        }
    confidence_order = {"low": 1, "medium": 2, "high": 3}
    confidence_values = [
        str(passage.research_confidence)
        for passage in passages
        if passage is not None
    ]
    evidence_confidence = (
        min(confidence_values, key=lambda value: confidence_order[value])
        if confidence_values
        and all(value in confidence_order for value in confidence_values)
        else "unavailable"
    )
    return {
        "evidence_confidence": evidence_confidence,
        "retrieved_count": retrieved_count,
        "evidence_reference_count": len(evidence_ids),
    }


def outcome_telemetry(result: PipelineResult) -> dict[str, Any]:
    """Derive truthful non-content outcome telemetry from a pipeline audit."""
    proposer_mode = "not_run"
    proposer_tone = "none"
    factual_claim_count: int | None = None
    terminal_stage = "not_run"
    reviewer_verdict = "not_run"
    claim_auditor_status = "not_run"
    evidence_status = "not_run"

    for row in result.audit:
        if not isinstance(row, dict):
            continue
        stage = str(row.get("stage") or "")
        status = str(row.get("status") or "")
        if stage:
            terminal_stage = stage
        if stage in {"proposer", "revision_proposer"} and status == "completed":
            proposer_mode = str(row.get("mode") or "not_run")
            proposer_tone = str(row.get("tone") or "none")
            count = row.get("factual_claim_count")
            factual_claim_count = count if type(count) is int and count >= 0 else None
        if stage in {
            "no_reply_reviewer", "revision_no_reply_reviewer",
            "reviewer", "revision_reviewer",
        }:
            verdict = row.get("verdict")
            if verdict in NO_REPLY_REVIEW_VERDICTS | REVIEWER_VERDICTS:
                reviewer_verdict = str(verdict)
            elif status == "invalid":
                reviewer_verdict = "invalid"
        if stage in {"claim_auditor", "revision_claim_auditor"}:
            claim_auditor_status = "invalid" if status == "invalid" else "completed"
        if stage in {"evidence", "revision_evidence"}:
            if status in {"completed", "insufficient", "rejected", "invalid"}:
                evidence_status = (
                    "insufficient"
                    if status == "completed" and row.get("supported") is False
                    else status
                )

    return {
        "proposer_mode": proposer_mode,
        "proposer_tone": proposer_tone,
        "factual_claim_count": factual_claim_count,
        "terminal_stage": terminal_stage,
        "reviewer_verdict": reviewer_verdict,
        "claim_auditor_status": claim_auditor_status,
        "evidence_status": evidence_status,
    }


def validate_persisted_draft(
    record: object,
    *,
    context: dict[str, Any],
    config: dict[str, Any],
    repository: EvidenceRepository,
    maximum_reply_length: int,
    recent_replies: list[str] | None = None,
) -> dict[str, Any]:
    """Validate a draft against current context, models and evidence inputs."""
    if not isinstance(record, dict):
        raise ValueError("persisted AI reply draft must be an object")
    expected = {
        "schema_version", "strategy_version", "target_id", "thread_id", "candidate_source",
        "contribution_hash", "context_hash", "proposed_reply", "mode", "tone",
        "direct_factual_question_present", "requested_answer_type", "direct_answer_text",
        "factual_claims", "exact_thatcher_wording_used", "exact_thatcher_wording",
        "evidence_ids", "claim_evidence", "source_hashes", "evidence_input_hashes",
        "reviewer_verdict", "reviewer_reasons", "reviewer_sentence_assessments",
        "claim_auditor_sentence_assessments",
        "resolved_quote_id", "resolved_quote_context_hash",
        "proposer_model", "evidence_model", "reviewer_model", "proposer_prompt_version",
        "claim_auditor_model", "evidence_prompt_version", "reviewer_prompt_version",
        "claim_auditor_prompt_version", "model_call_count",
        "revision_count", "creation_time", "approval_hash",
    }
    if "retrieved_count" in record:
        expected.add("retrieved_count")
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
    resolved_quotation = repository.resolve_context_quotation(clean_context)
    resolved_identity = (
        (
            str(resolved_quotation["quote_id"]),
            str(resolved_quotation["resolved_context_hash"]),
        )
        if resolved_quotation is not None
        else (None, None)
    )
    if resolved_identity != (
        record.get("resolved_quote_id"),
        record.get("resolved_quote_context_hash"),
    ):
        raise ValueError("persisted draft resolved quotation changed")
    model_fields = {
        "proposer_model": config["proposer_model"],
        "evidence_model": config["evidence_model"],
        "reviewer_model": config["reviewer_model"],
        "claim_auditor_model": config["reviewer_model"],
        "proposer_prompt_version": PROPOSER_PROMPT_VERSION,
        "evidence_prompt_version": EVIDENCE_PROMPT_VERSION,
        "reviewer_prompt_version": REVIEWER_PROMPT_VERSION,
        "claim_auditor_prompt_version": CLAIM_AUDITOR_PROMPT_VERSION,
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
    if "retrieved_count" in record and (
        type(record.get("retrieved_count")) is not int
        or record["retrieved_count"] < 0
    ):
        raise ValueError("persisted draft retrieved count is invalid")
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
    if type(record.get("direct_factual_question_present")) is not bool:
        raise ValueError("persisted direct factual question flag is invalid")
    if record.get("requested_answer_type") not in ANSWER_TYPES:
        raise ValueError("persisted requested answer type is invalid")
    if not isinstance(record.get("direct_answer_text"), str):
        raise ValueError("persisted direct answer text is invalid")
    if record["mode"] == "direct_factual_answer":
        sentences = split_reply_sentences(reply)
        if (
            record["direct_factual_question_present"] is not True
            or record["requested_answer_type"] == "none"
            or not sentences
            or record["direct_answer_text"] != sentences[0]
        ):
            raise ValueError("persisted direct answer binding is invalid")
    elif (
        record["direct_factual_question_present"]
        or record["requested_answer_type"] != "none"
        or record["direct_answer_text"]
    ):
        raise ValueError("persisted non-direct reply has direct answer metadata")
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
    assessments = record.get("reviewer_sentence_assessments")
    if not isinstance(assessments, list) or not assessments:
        raise ValueError("persisted reviewer sentence assessments are invalid")
    assessment_sentences: list[str] = []
    assessment_claims: list[str] = []
    for assessment in assessments:
        if not isinstance(assessment, dict) or set(assessment) != {
            "sentence_text", "classification", "factual_claims",
            "non_factual_basis", "world_claim_checks",
        }:
            raise ValueError("persisted reviewer sentence assessment fields mismatch")
        classification = assessment.get("classification")
        sentence_claims = assessment.get("factual_claims")
        non_factual_basis = assessment.get("non_factual_basis")
        world_claim_checks = assessment.get("world_claim_checks")
        if (
            not isinstance(assessment.get("sentence_text"), str)
            or classification not in SENTENCE_CLASSIFICATIONS
            or not isinstance(sentence_claims, list)
            or any(not isinstance(value, str) or not value for value in sentence_claims)
        ):
            raise ValueError("persisted reviewer sentence assessment is invalid")
        if any(
            " ".join(value.split()) not in " ".join(assessment["sentence_text"].split())
            for value in sentence_claims
        ):
            raise ValueError("persisted reviewer claim is absent from its sentence")
        is_factual = classification in FACTUAL_SENTENCE_CLASSIFICATIONS
        if is_factual != bool(sentence_claims):
            raise ValueError("persisted reviewer sentence assessment is contradictory")
        expected_bases = (
            {"none"}
            if is_factual
            else NON_FACTUAL_BASES_BY_CLASSIFICATION.get(str(classification), set())
        )
        if non_factual_basis not in expected_bases:
            raise ValueError("persisted reviewer sentence non-factual basis is contradictory")
        if (
            not isinstance(world_claim_checks, dict)
            or set(world_claim_checks) != set(WORLD_CLAIM_CHECK_FIELDS)
            or any(type(world_claim_checks[field]) is not bool for field in WORLD_CLAIM_CHECK_FIELDS)
        ):
            raise ValueError("persisted reviewer sentence world-claim checks are invalid")
        has_world_claim = any(
            world_claim_checks[field]
            for field in WORLD_CLAIM_CHECK_FIELDS
            if field != "purely_non_factual"
        )
        if (
            world_claim_checks["purely_non_factual"] == has_world_claim
            or has_world_claim != is_factual
        ):
            raise ValueError("persisted reviewer sentence world-claim checks are contradictory")
        assessment_sentences.append(assessment["sentence_text"])
        assessment_claims.extend(sentence_claims)
    if assessment_sentences != split_reply_sentences(reply):
        raise ValueError("persisted reviewer sentence coverage is invalid")
    if assessment_claims != [claim["claim_text"] for claim in claims]:
        raise ValueError("persisted reviewer claim inventory is invalid")
    claim_auditor_assessments = record.get("claim_auditor_sentence_assessments")
    if claims or record["mode"] not in CLAIM_AUDITED_MODES:
        if claim_auditor_assessments != []:
            raise ValueError("persisted factual draft has an inapplicable claim audit")
    else:
        validate_claim_auditor(
            {
                "actual_factual_claims": [],
                "sentence_assessments": claim_auditor_assessments,
            },
            maximum_claims=config["maximum_claims"],
            proposed_reply=reply,
        )
    if record["requested_answer_type"] == "actor" and resolved_quotation is not None:
        binding_error = direct_answer_binding_error(
            {
                "direct_factual_question_present": record["direct_factual_question_present"],
                "requested_answer_type": record["requested_answer_type"],
                "direct_answer_text": record["direct_answer_text"],
            },
            {
                "direct_factual_question_present": record["direct_factual_question_present"],
                "requested_answer_type": record["requested_answer_type"],
                "direct_answer_text": record["direct_answer_text"],
                "direct_answer_complete": True,
            },
            resolved_quotation,
        )
        if binding_error:
            raise ValueError(f"persisted direct answer fails identity binding: {binding_error}")
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
    if (
        record["mode"] == "direct_factual_answer"
        and resolved_quotation is not None
        and {
            repository.passages[str(evidence_id)].quote_id
            for evidence_id in evidence_ids
        }
        != {str(resolved_quotation["quote_id"])}
    ):
        raise ValueError("persisted direct answer evidence is not confined to the resolved quotation")
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
        return PipelineResult(
            None,
            "operational_failure",
            "invalid_strategy_config",
            0,
            0,
            tuple(
                {"stage": "strategy_config", "status": "invalid", "error": error}
                for error in config_errors
            ),
        )
    if config["enabled"] is not True:
        return PipelineResult(None, "disabled", "strategy_disabled", 0, 0, ())
    clean_context = validate_reply_context(context)
    recent = [str(item) for item in (recent_replies or [])[:20]]
    resolved_quotation = repository.resolve_context_quotation(clean_context)
    call_count = 0
    audit: list[dict[str, Any]] = [{
        "stage": "quotation_resolution",
        "status": "resolved" if resolved_quotation is not None else "not_resolved",
        "quote_id": (
            str(resolved_quotation["quote_id"]) if resolved_quotation is not None else None
        ),
        "context_section": (
            str(resolved_quotation["matched_context_section"])
            if resolved_quotation is not None
            else None
        ),
    }]
    revisions = 0
    reviewer_claim_constraints: list[str] = []

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
        original_user_prompt = user_prompt
        current_user_prompt = original_user_prompt
        pending_retry_audit: dict[str, object] | None = None
        pending_retry_origin_attempt: int | None = None
        for attempt in range(maximum_retries + 1):
            try:
                raw = call(
                    stage,
                    model=model,
                    system_prompt=system_prompt,
                    user_prompt=current_user_prompt,
                    schema=schema,
                    timeout=timeout,
                    max_output_tokens=max_output_tokens,
                    include_media=include_media,
                )
            except ModelCallLimitError as exc:
                row: dict[str, object] = {
                    "stage": stage,
                    "status": "invalid",
                    "attempt": attempt + 1,
                    "reason": str(exc),
                    "corrective_retry_applied": False,
                }
                if pending_retry_audit is not None:
                    row.update(pending_retry_audit)
                    row["corrective_retry_applied"] = False
                audit.append(row)
                return None

            if pending_retry_audit is not None:
                audit.append({
                    "stage": stage,
                    "status": "invalid_response_retry",
                    "attempt": pending_retry_origin_attempt,
                    "reason": pending_retry_audit["validator_error"],
                    "next_attempt": attempt + 1,
                    **pending_retry_audit,
                    "corrective_retry_applied": True,
                })

            try:
                return validator(raw)
            except NonRetryableReviewerResponseError as exc:
                audit.append({
                    "stage": stage,
                    "status": "invalid",
                    "attempt": attempt + 1,
                    "reason": str(exc),
                    "retry_suppressed": True,
                    "corrective_retry_applied": False,
                })
                return None
            except (json.JSONDecodeError, TypeError, ValueError) as exc:
                validator_error = str(exc)
                validator_error_type = type(exc).__name__
                try:
                    validator_error_sha256 = hashlib.sha256(
                        validator_error.encode("utf-8")
                    ).hexdigest()
                except UnicodeEncodeError:
                    audit.append({
                        "stage": stage,
                        "status": "invalid",
                        "attempt": attempt + 1,
                        "reason": "validation_error_not_safe_for_retry",
                        "validator_error_type": validator_error_type,
                        "corrective_retry_applied": False,
                    })
                    return None
                if (
                    not validator_error
                    or "\x00" in validator_error
                    or len(validator_error) > 500
                ):
                    audit.append({
                        "stage": stage,
                        "status": "invalid",
                        "attempt": attempt + 1,
                        "reason": "validation_error_not_safe_for_retry",
                        "validator_error_type": validator_error_type,
                        "validator_error_sha256": validator_error_sha256,
                        "corrective_retry_applied": False,
                    })
                    return None
                if attempt >= maximum_retries:
                    row = {
                        "stage": stage,
                        "status": "invalid",
                        "attempt": attempt + 1,
                        "reason": validator_error,
                        "validator_error_type": validator_error_type,
                        "validator_error": validator_error,
                        "validator_error_sha256": validator_error_sha256,
                        "corrective_retry_applied": pending_retry_audit is not None,
                    }
                    if pending_retry_audit is not None:
                        row.update({
                            "validation_retry_protocol_version": (
                                VALIDATION_RETRY_PROTOCOL_VERSION
                            ),
                            "original_user_prompt_sha256": pending_retry_audit[
                                "original_user_prompt_sha256"
                            ],
                            "corrective_user_prompt_sha256": pending_retry_audit[
                                "corrective_user_prompt_sha256"
                            ],
                        })
                    audit.append(row)
                    return None
                try:
                    current_user_prompt, pending_retry_audit = (
                        _build_validation_retry_user_prompt(
                            original_user_prompt,
                            stage,
                            attempt + 2,
                            exc,
                        )
                    )
                except _ValidationRetryPromptError as prompt_exc:
                    reason = str(prompt_exc)
                    row = {
                        "stage": stage,
                        "status": "invalid",
                        "attempt": attempt + 1,
                        "reason": reason,
                        "validator_error_type": validator_error_type,
                        "validator_error_sha256": validator_error_sha256,
                        "corrective_retry_applied": False,
                    }
                    if reason != "validation_error_not_safe_for_retry":
                        row["validator_error"] = validator_error
                    audit.append(row)
                    return None
                pending_retry_origin_attempt = attempt + 1
        return None

    revision_request: dict[str, Any] | None = None
    while True:
        claim_auditor_result: dict[str, Any] | None = None
        proposer_stage = "revision_proposer" if revisions else "proposer"
        proposer_system, proposer_user = _proposer_prompts(
            clean_context,
            recent,
            resolved_quotation=resolved_quotation,
            revision=revision_request,
        )
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
        audit.append({
            "stage": proposer_stage,
            "status": "completed",
            "mode": proposer["mode"],
            "tone": proposer["tone"],
            "factual_claim_count": len(proposer["factual_claims"]),
        })
        if (
            clean_context["clarification_request"] is not None
            and proposer["mode"] not in CLARIFICATION_ALLOWED_MODES
        ):
            audit.append({
                "stage": proposer_stage,
                "status": "rejected",
                "reason": CLARIFICATION_MODE_REFUSAL_REASON,
            })
            return PipelineResult(
                None,
                "no_reply",
                CLARIFICATION_MODE_REFUSAL_REASON,
                call_count,
                revisions,
                tuple(audit),
            )
        if proposer["mode"] == "no_reply":
            no_reply_reviewer_stage = (
                "revision_no_reply_reviewer" if revisions else "no_reply_reviewer"
            )
            no_reply_system, no_reply_user = _no_reply_review_prompts(
                clean_context,
                proposer,
            )
            no_reply_review = call_and_validate(
                no_reply_reviewer_stage,
                model=config["reviewer_model"],
                system_prompt=no_reply_system,
                user_prompt=no_reply_user,
                schema=no_reply_review_schema(),
                timeout=config["reviewer_timeout_seconds"],
                max_output_tokens=config["reviewer_max_output_tokens"],
                include_media=True,
                validator=validate_no_reply_review,
            )
            if no_reply_review is None:
                return PipelineResult(
                    None,
                    "operational_failure",
                    f"{no_reply_reviewer_stage}_invalid",
                    call_count,
                    revisions,
                    tuple(audit),
                )
            audit.append({
                "stage": no_reply_reviewer_stage,
                "status": "completed",
                "verdict": no_reply_review["verdict"],
            })
            if no_reply_review["verdict"] == "confirm_no_reply":
                return PipelineResult(
                    None,
                    "no_reply",
                    "independent_no_reply_confirmed",
                    call_count,
                    revisions,
                    tuple(audit),
                )
            if revisions >= config["maximum_revisions"]:
                return PipelineResult(
                    None,
                    "operational_failure",
                    "no_reply_review_requires_reply_after_revision",
                    call_count,
                    revisions,
                    tuple(audit),
                )
            revision_request = {
                "previous_reply": "",
                "reviewer_reasons": no_reply_review["reasons"],
                "reviewer_revision_instructions": (
                    "Produce a safe, relevant public response; do not choose silence merely because the "
                    "contribution lacks a question or new factual matter. Do not repeat or endorse unsupported "
                    "claims. " + no_reply_review["revision_instructions"]
                ),
                "evidence_status": [],
            }
            revisions += 1
            continue

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

        if reviewer_claim_constraints:
            compact_reply = " ".join(proposer["proposed_reply"].split())
            declared_claims = {
                " ".join(claim["claim_text"].split())
                for claim in proposer["factual_claims"]
            }
            dropped_constraints = [
                constraint
                for constraint in reviewer_claim_constraints
                if " ".join(constraint.split()) in compact_reply
                and " ".join(constraint.split()) not in declared_claims
            ]
            if dropped_constraints:
                audit.append({
                    "stage": proposer_stage,
                    "status": "rejected",
                    "reason": "revision_dropped_previously_identified_factual_claim",
                    "claim_count": len(dropped_constraints),
                })
                return PipelineResult(
                    None,
                    "no_reply",
                    "revision_dropped_previously_identified_factual_claim",
                    call_count,
                    revisions,
                    tuple(audit),
                )

        claims = proposer["factual_claims"]
        if not claims and proposer["mode"] in CLAIM_AUDITED_MODES:
            claim_auditor_stage = (
                "revision_claim_auditor" if revisions else "claim_auditor"
            )
            auditor_system, auditor_user = _claim_auditor_prompts(
                proposer["proposed_reply"]
            )
            claim_auditor_result = call_and_validate(
                claim_auditor_stage,
                model=config["reviewer_model"],
                system_prompt=auditor_system,
                user_prompt=auditor_user,
                schema=claim_auditor_schema(config["maximum_claims"]),
                timeout=config["reviewer_timeout_seconds"],
                max_output_tokens=config["reviewer_max_output_tokens"],
                include_media=False,
                validator=lambda raw: validate_claim_auditor(
                    raw,
                    maximum_claims=config["maximum_claims"],
                    proposed_reply=proposer["proposed_reply"],
                ),
            )
            if claim_auditor_result is None:
                return PipelineResult(
                    None,
                    "operational_failure",
                    f"{claim_auditor_stage}_invalid",
                    call_count,
                    revisions,
                    tuple(audit),
                )
            auditor_claims = claim_auditor_result["actual_factual_claims"]
            audit.append({
                "stage": claim_auditor_stage,
                "status": "completed",
                "factual_claim_count": len(auditor_claims),
            })
            if auditor_claims:
                for claim_text in auditor_claims:
                    if claim_text not in reviewer_claim_constraints:
                        reviewer_claim_constraints.append(claim_text)
                if revisions >= config["maximum_revisions"]:
                    return PipelineResult(
                        None,
                        "no_reply",
                        "claim_auditor_detected_unresolved_factual_claim",
                        call_count,
                        revisions,
                        tuple(audit),
                    )
                revision_request = {
                    "previous_reply": proposer["proposed_reply"],
                    "claim_auditor_actual_factual_claims": auditor_claims,
                    "reviewer_reasons": [
                        "A fresh claim audit found factual clauses omitted by the proposer."
                    ],
                    "reviewer_revision_instructions": (
                        "Either remove every listed factual clause completely or declare each remaining exact "
                        "clause in factual_claims so it can receive claim-specific evidence."
                    ),
                    "evidence_status": [],
                }
                revisions += 1
                continue
        evidence: list[dict[str, Any]] = []
        retrieved_count = 0
        if claims:
            restrict_to_resolved_quote = bool(
                proposer["mode"] == "direct_factual_answer"
                and resolved_quotation is not None
            )
            candidates = {
                claim["claim_id"]: repository.candidate_passages(
                    claim_retrieval_query(claim, clean_context),
                    maximum_packets=config["maximum_evidence_packets_per_claim"],
                    maximum_passages=config["maximum_evidence_passages_per_claim"],
                    preferred_quote_id=(
                        str(resolved_quotation["quote_id"])
                        if resolved_quotation is not None
                        else None
                    ),
                    restrict_to_preferred_quote=restrict_to_resolved_quote,
                )
                for claim in claims
            }
            retrieved_count = len({
                str(passage.quote_id)
                for passages in candidates.values()
                for passage in passages
            })
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
                adjudication_supported = _all_claims_supported(claims, evidence)
                audit.append({
                    "stage": evidence_stage,
                    "status": "completed" if adjudication_supported else "insufficient",
                    "supported": adjudication_supported,
                })
            else:
                evidence = _insufficient_evidence_rows(claims)
                audit.append({"stage": "evidence", "status": "insufficient", "reason": "claim_without_candidate_passage"})

        evidence_supported = _all_claims_supported(claims, evidence)
        supporting_references = [
            reference
            for row in evidence
            if row["verdict"] == "supports"
            for reference in row.get("evidence", [])
            if reference.get("relation") == "supports"
        ]
        resolved_quote_supported = bool(
            resolved_quotation is None
            or any(
                reference.get("quote_id") == resolved_quotation["quote_id"]
                for reference in supporting_references
            )
        )
        if (
            proposer["mode"] == "direct_factual_answer"
            and resolved_quotation is not None
            and not resolved_quote_supported
        ):
            audit.append({
                "stage": "evidence",
                "status": "rejected",
                "reason": "resolved_quotation_not_supported",
                "quote_id": resolved_quotation["quote_id"],
            })
            return PipelineResult(
                None,
                "no_reply",
                "resolved_quotation_not_supported",
                call_count,
                revisions,
                tuple(audit),
            )
        if (
            proposer["mode"] == "direct_factual_answer"
            and resolved_quotation is not None
            and any(
                reference.get("quote_id") != resolved_quotation["quote_id"]
                for reference in supporting_references
            )
        ):
            audit.append({
                "stage": "evidence",
                "status": "rejected",
                "reason": "resolved_quotation_evidence_scope_violation",
                "quote_id": resolved_quotation["quote_id"],
            })
            return PipelineResult(
                None,
                "no_reply",
                "resolved_quotation_evidence_scope_violation",
                call_count,
                revisions,
                tuple(audit),
            )
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
        reviewer_system, reviewer_user = _reviewer_prompts(
            clean_context,
            proposer,
            evidence,
            resolved_quotation,
        )
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
                proposed_reply=proposer["proposed_reply"],
            ),
        )
        if reviewer is None:
            return PipelineResult(None, "operational_failure", f"{reviewer_stage}_invalid", call_count, revisions, tuple(audit))
        expected_reviewer_claims = [claim["claim_text"] for claim in claims]
        claim_inventory_mismatch = (
            reviewer["actual_factual_claims"] != expected_reviewer_claims
        )
        if claim_inventory_mismatch:
            for claim_text in reviewer["actual_factual_claims"]:
                if (
                    claim_text not in expected_reviewer_claims
                    and claim_text not in reviewer_claim_constraints
                ):
                    reviewer_claim_constraints.append(claim_text)
        direct_binding_error = direct_answer_binding_error(
            proposer,
            reviewer,
            resolved_quotation,
        )
        if reviewer["verdict"] == "approve" and (
            claim_inventory_mismatch or direct_binding_error is not None
        ):
            mismatch_reason = (
                "reviewer_claim_inventory_mismatch"
                if claim_inventory_mismatch
                else str(direct_binding_error)
            )
            audit.append({
                "stage": reviewer_stage,
                "status": "invalid",
                "reason": mismatch_reason,
            })
            if revisions >= config["maximum_revisions"]:
                return PipelineResult(
                    None,
                    "operational_failure",
                    mismatch_reason,
                    call_count,
                    revisions,
                    tuple(audit),
                )
            revision_request = {
                "previous_reply": proposer["proposed_reply"],
                "reviewer_reasons": reviewer["reasons"],
                "reviewer_actual_factual_claims": reviewer["actual_factual_claims"],
                "reviewer_revision_instructions": (
                    "Correct the factual-claim inventory and direct answer. Each claim_text must copy one "
                    "complete independently checkable clause from proposed_reply verbatim. The first sentence "
                    "must explicitly supply the requested fact. Do not add unsupported facts or discuss the "
                    "evidence process."
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
                resolved_quotation=resolved_quotation,
                config=config,
                model_call_count=call_count,
                revision_count=revisions,
                creation_time=creation_time or utc_now(),
                retrieved_count=retrieved_count,
                claim_auditor=claim_auditor_result,
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
                "claim_auditor_ran": claim_auditor_result is not None,
                **evidence_telemetry(draft, repository),
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
            "reviewer_actual_factual_claims": reviewer["actual_factual_claims"],
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
