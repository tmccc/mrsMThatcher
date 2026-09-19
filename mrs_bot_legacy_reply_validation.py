"""Validate frozen reply drafts from retired conversational reply strategies.

The fixed schema, strategy and hash definitions live here. Root constants remain
aliases, and adapters supply their current references, helper callbacks and size
limits on each call. Validation retains the historical bodies and native error
boundaries; current draft validation, lifecycle recovery and outbound authority
remain in their existing owners. This module imports only the standard library,
constructs fixed strings/frozensets, performs no runtime I/O or configuration work,
and retains no callbacks or mutable state.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from datetime import datetime, timedelta


_LEGACY_TESTED_REPLY_STRATEGY_VERSION = "tested-reply-pipeline-20260817"
_LEGACY_AI_FIRST_REPLY_STRATEGY_VERSION = "ai-first-reply-v3"
_LEGACY_SINGLE_SOL_REPLY_STRATEGY_VERSION = "single-sol-reply-20260904"
_LEGACY_SINGLE_SOL_PROMPT_SHA256 = (
    "7bfa91fb2d9b1175560abb33e43f2ced6910d8e63cadd1f8f04935b6dc2f2560"
)
_LEGACY_SINGLE_SOL_RESPONSE_SCHEMA_SHA256 = (
    "3b1e23015cebe3b75eacde04ebfd4344fa25117f047cdcf83241b0ce709872ce"
)
_LEGACY_MULTI_MODEL_REPLY_CONTEXT_FIELDS = frozenset(
    {
        "target_id",
        "thread_id",
        "lane",
        "incoming_contribution",
        "quoted_post",
        "parent_thread",
        "clarification_request",
        "current_date",
    }
)
_LEGACY_TESTED_REPLY_DRAFT_FIELDS = frozenset(
    {
        "schema_version",
        "strategy_version",
        "target_id",
        "thread_id",
        "candidate_source",
        "contribution_hash",
        "context_hash",
        "trusted_facts_hash",
        "proposed_reply",
        "mode",
        "final_reply_kind",
        "tone",
        "factual_claims",
        "evidence_ids",
        "trusted_facts_supplied_count",
        "trusted_fact_ids_supplied",
        "used_fact_count",
        "used_fact_ids",
        "claim_risk_categories",
        "reviewer_verdict",
        "model_call_count",
        "revision_count",
        "reply_requirement",
        "route_source",
        "creation_time",
        "approval_hash",
    }
)
_LEGACY_TESTED_REPLY_DIRECT_REPAIR_FIELDS = frozenset(
    {
        "direct_answer_repair_attempted",
        "direct_answer_repair_outcome",
        "original_local_rejection_reason",
        "original_proposed_reply",
    }
)
_LEGACY_AI_FIRST_REPLY_DRAFT_FIELDS = frozenset(
    {
        "schema_version",
        "strategy_version",
        "target_id",
        "thread_id",
        "candidate_source",
        "contribution_hash",
        "context_hash",
        "proposed_reply",
        "mode",
        "tone",
        "direct_factual_question_present",
        "requested_answer_type",
        "direct_answer_text",
        "factual_claims",
        "exact_thatcher_wording_used",
        "exact_thatcher_wording",
        "evidence_ids",
        "claim_evidence",
        "source_hashes",
        "evidence_input_hashes",
        "reviewer_verdict",
        "reviewer_reasons",
        "reviewer_sentence_assessments",
        "claim_auditor_sentence_assessments",
        "resolved_quote_id",
        "resolved_quote_context_hash",
        "proposer_model",
        "evidence_model",
        "reviewer_model",
        "claim_auditor_model",
        "proposer_prompt_version",
        "evidence_prompt_version",
        "reviewer_prompt_version",
        "claim_auditor_prompt_version",
        "model_call_count",
        "revision_count",
        "creation_time",
        "approval_hash",
    }
)
_LEGACY_SINGLE_SOL_REPLY_DRAFT_FIELDS = frozenset(
    {
        "draft_schema_version",
        "strategy_version",
        "target_id",
        "root_post_id",
        "parent_post_id",
        "candidate_source",
        "incoming_contribution_sha256",
        "canonical_visible_context_sha256",
        "model_payload_sha256",
        "prompt_sha256",
        "response_schema_sha256",
        "model",
        "reasoning_effort",
        "temperature",
        "proposed_reply",
        "reply_kind",
        "reason_code",
        "trusted_fact_ids",
        "used_fact_ids",
        "used_fact_sources",
        "supplied_images",
        "model_call_count",
        "created_at",
        "validated_draft_hash",
    }
)
_LEGACY_AI_FIRST_REPLY_MODES = frozenset(
    {"direct_factual_answer", "opinion_or_principle", "light_humour", "courtesy"}
)
_LEGACY_AI_FIRST_REPLY_TONES = frozenset(
    {"firm", "dry", "wry", "warm", "neutral", "light", "none"}
)
_LEGACY_AI_FIRST_ANSWER_TYPES = frozenset(
    {
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
)
_LEGACY_AI_FIRST_WORLD_CLAIM_FIELDS = frozenset(
    {
        "asserts_actor_state_or_action",
        "asserts_causal_or_predictive_relation",
        "asserts_comparison_or_outcome",
        "asserts_historical_date_or_quantity",
        "asserts_meaning_or_attribution",
        "purely_non_factual",
    }
)
_LEGACY_SINGLE_SOL_REPLY_KINDS = frozenset(
    {
        "social",
        "humour",
        "principle",
        "direct_factual",
        "premise_neutral",
        "clarification",
    }
)
_LEGACY_SINGLE_SOL_REASON_CODES = frozenset(
    {
        "useful_reply",
        "completed_exchange",
        "already_answered",
        "no_meaningful_content",
        "spam_or_abuse",
        "not_worth_amplifying",
        "unsupported_or_unverifiable",
        "insufficient_context",
        "irrelevant",
    }
)
_LEGACY_SINGLE_SOL_IMAGE_MIME_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/gif"}
)


def _legacy_reply_value_sha256(value: object) -> str | None:
    """Hash the compact sorted JSON form used by retired draft writers."""

    try:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8", errors="strict")
    except (TypeError, ValueError, UnicodeError):
        return None
    return hashlib.sha256(encoded).hexdigest()


def _legacy_reply_sha256_is_valid(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _legacy_reply_utc_timestamp_is_valid(value: object) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() == timedelta(0)


def _legacy_multi_model_context_post_is_valid(
    value: object,
    *,
    valid_string_post_id: Callable,
) -> bool:
    return bool(
        isinstance(value, dict)
        and set(value) == {"post_id", "author_role", "text"}
        and valid_string_post_id(value.get("post_id"))
        and value.get("author_role") in {"account", "user", "unknown"}
        and isinstance(value.get("text"), str)
        and len(value["text"]) <= 2_000
    )


def _legacy_multi_model_reply_context_is_valid(
    context: object,
    *,
    _LEGACY_MULTI_MODEL_REPLY_CONTEXT_FIELDS: frozenset[str],
    valid_string_post_id: Callable,
    _legacy_multi_model_context_post_is_valid: Callable,
) -> bool:
    """Validate the exact context object hashed by both retired strategies."""

    if not isinstance(context, dict) or set(context) != set(
        _LEGACY_MULTI_MODEL_REPLY_CONTEXT_FIELDS
    ):
        return False
    if (
        not valid_string_post_id(context.get("target_id"))
        or not valid_string_post_id(context.get("thread_id"))
        or context.get("lane") not in {"mention", "hot_post_reply", "quote_tweet"}
    ):
        return False
    incoming = context.get("incoming_contribution")
    if (
        not isinstance(incoming, str)
        or not incoming.strip()
        or len(incoming) > 10_000
    ):
        return False
    current_date = context.get("current_date")
    if not isinstance(current_date, str) or re.fullmatch(
        r"\d{4}-\d{2}-\d{2}", current_date
    ) is None:
        return False
    try:
        if datetime.strptime(current_date, "%Y-%m-%d").strftime("%Y-%m-%d") != current_date:
            return False
    except ValueError:
        return False
    quoted = context.get("quoted_post")
    if quoted is not None and not _legacy_multi_model_context_post_is_valid(quoted):
        return False
    parents = context.get("parent_thread")
    if (
        not isinstance(parents, list)
        or len(parents) > 3
        or any(not _legacy_multi_model_context_post_is_valid(row) for row in parents)
    ):
        return False
    clarification = context.get("clarification_request")
    if clarification is None:
        return True
    return bool(
        isinstance(clarification, dict)
        and set(clarification) == {"original_question", "correction"}
        and isinstance(clarification.get("original_question"), str)
        and bool(clarification["original_question"].strip())
        and len(clarification["original_question"]) <= 10_000
        and clarification.get("correction") == incoming
    )


def _legacy_tested_reply_draft_is_valid(
    draft: dict,
    *,
    context: dict,
    text: object,
    _LEGACY_TESTED_REPLY_DRAFT_FIELDS: frozenset[str],
    _LEGACY_TESTED_REPLY_DIRECT_REPAIR_FIELDS: frozenset[str],
    _LEGACY_TESTED_REPLY_STRATEGY_VERSION: str,
    _legacy_reply_utc_timestamp_is_valid: Callable,
    _legacy_reply_value_sha256: Callable,
    _legacy_reply_sha256_is_valid: Callable,
) -> bool:
    expected_fields = set(_LEGACY_TESTED_REPLY_DRAFT_FIELDS)
    if set(draft).intersection(_LEGACY_TESTED_REPLY_DIRECT_REPAIR_FIELDS):
        expected_fields.update(_LEGACY_TESTED_REPLY_DIRECT_REPAIR_FIELDS)
    if set(draft) != expected_fields:
        return False
    if (
        type(draft.get("schema_version")) is not int
        or draft.get("schema_version") != 1
        or draft.get("strategy_version")
        != _LEGACY_TESTED_REPLY_STRATEGY_VERSION
        or draft.get("target_id") != context.get("target_id")
        or draft.get("thread_id") != context.get("thread_id")
        or draft.get("candidate_source") != context.get("lane")
        or draft.get("proposed_reply") != text
        or draft.get("reviewer_verdict") != "approve"
        or draft.get("mode")
        not in {"direct_factual_answer", "opinion_or_principle"}
        or not isinstance(draft.get("final_reply_kind"), str)
        or not isinstance(draft.get("tone"), str)
        or not isinstance(draft.get("factual_claims"), list)
        or not isinstance(draft.get("claim_risk_categories"), list)
        or type(draft.get("model_call_count")) is not int
        or not 1 <= draft["model_call_count"] <= 20
        or type(draft.get("revision_count")) is not int
        or draft["revision_count"] < 0
        or not _legacy_reply_utc_timestamp_is_valid(draft.get("creation_time"))
    ):
        return False
    if (
        draft.get("contribution_hash")
        != hashlib.sha256(context["incoming_contribution"].encode("utf-8")).hexdigest()
        or draft.get("context_hash") != _legacy_reply_value_sha256(context)
        or not _legacy_reply_sha256_is_valid(draft.get("trusted_facts_hash"))
    ):
        return False
    if _LEGACY_TESTED_REPLY_DIRECT_REPAIR_FIELDS.issubset(draft) and (
        draft.get("direct_answer_repair_attempted") is not True
        or draft.get("direct_answer_repair_outcome") != "approved"
        or draft.get("mode") != "direct_factual_answer"
        or draft.get("reply_requirement") != "supported_factual"
        or not isinstance(draft.get("original_local_rejection_reason"), str)
        or not draft["original_local_rejection_reason"]
        or not isinstance(draft.get("original_proposed_reply"), str)
        or not draft["original_proposed_reply"]
    ):
        return False
    supplied_ids = draft.get("trusted_fact_ids_supplied")
    if (
        not isinstance(supplied_ids, list)
        or any(not isinstance(item, str) or not item for item in supplied_ids)
        or type(draft.get("trusted_facts_supplied_count")) is not int
        or draft["trusted_facts_supplied_count"] != len(supplied_ids)
    ):
        return False
    approval = draft.get("approval_hash")
    unsigned = {key: value for key, value in draft.items() if key != "approval_hash"}
    return bool(
        _legacy_reply_sha256_is_valid(approval)
        and approval == _legacy_reply_value_sha256(unsigned)
    )


def _legacy_ai_first_claim_is_valid(value: object, expected_index: int) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "claim_id",
        "claim_text",
        "requires_evidence",
        "actor",
        "action_or_relationship",
        "direction_or_polarity",
        "date_or_period",
        "quantity",
    }:
        return False
    return bool(
        value.get("claim_id") == f"claim-{expected_index}"
        and isinstance(value.get("claim_text"), str)
        and bool(value["claim_text"].strip())
        and len(value["claim_text"]) <= 500
        and value.get("requires_evidence") is True
        and all(
            isinstance(value.get(field), str)
            for field in (
                "actor",
                "action_or_relationship",
                "direction_or_polarity",
                "date_or_period",
                "quantity",
            )
        )
    )


def _legacy_ai_first_sentence_assessment_is_valid(
    value: object,
    *,
    _LEGACY_AI_FIRST_WORLD_CLAIM_FIELDS: frozenset[str],
) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "sentence_text",
        "classification",
        "factual_claims",
        "non_factual_basis",
        "world_claim_checks",
    }:
        return False
    checks = value.get("world_claim_checks")
    return bool(
        isinstance(value.get("sentence_text"), str)
        and value["sentence_text"]
        and isinstance(value.get("classification"), str)
        and isinstance(value.get("factual_claims"), list)
        and all(isinstance(item, str) and item for item in value["factual_claims"])
        and isinstance(value.get("non_factual_basis"), str)
        and isinstance(checks, dict)
        and set(checks) == set(_LEGACY_AI_FIRST_WORLD_CLAIM_FIELDS)
        and all(type(checks[field]) is bool for field in checks)
    )


def _legacy_ai_first_claim_audit_is_valid(
    value: object,
    *,
    _LEGACY_AI_FIRST_WORLD_CLAIM_FIELDS: frozenset[str],
) -> bool:
    if not isinstance(value, dict) or set(value) != {
        "sentence_text",
        "factual_claims",
        "world_claim_checks",
    }:
        return False
    checks = value.get("world_claim_checks")
    return bool(
        isinstance(value.get("sentence_text"), str)
        and value["sentence_text"]
        and isinstance(value.get("factual_claims"), list)
        and all(isinstance(item, str) and item for item in value["factual_claims"])
        and isinstance(checks, dict)
        and set(checks) == set(_LEGACY_AI_FIRST_WORLD_CLAIM_FIELDS)
        and all(type(checks[field]) is bool for field in checks)
    )


def _legacy_ai_first_reply_draft_is_valid(
    draft: dict,
    *,
    context: dict,
    text: object,
    _LEGACY_AI_FIRST_REPLY_DRAFT_FIELDS: frozenset[str],
    _LEGACY_AI_FIRST_REPLY_STRATEGY_VERSION: str,
    _LEGACY_AI_FIRST_REPLY_MODES: frozenset[str],
    _LEGACY_AI_FIRST_REPLY_TONES: frozenset[str],
    _legacy_reply_utc_timestamp_is_valid: Callable,
    _legacy_reply_value_sha256: Callable,
    _LEGACY_AI_FIRST_ANSWER_TYPES: frozenset[str],
    _legacy_ai_first_claim_is_valid: Callable,
    _legacy_reply_sha256_is_valid: Callable,
    _legacy_ai_first_sentence_assessment_is_valid: Callable,
    _legacy_ai_first_claim_audit_is_valid: Callable,
) -> bool:
    expected = set(_LEGACY_AI_FIRST_REPLY_DRAFT_FIELDS)
    if "retrieved_count" in draft:
        expected.add("retrieved_count")
    if set(draft) != expected:
        return False
    if (
        type(draft.get("schema_version")) is not int
        or draft.get("schema_version") != 9
        or draft.get("strategy_version") != _LEGACY_AI_FIRST_REPLY_STRATEGY_VERSION
        or draft.get("target_id") != context.get("target_id")
        or draft.get("thread_id") != context.get("thread_id")
        or draft.get("candidate_source") != context.get("lane")
        or draft.get("proposed_reply") != text
        or draft.get("reviewer_verdict") != "approve"
        or draft.get("mode") not in _LEGACY_AI_FIRST_REPLY_MODES
        or draft.get("tone") not in _LEGACY_AI_FIRST_REPLY_TONES
        or type(draft.get("model_call_count")) is not int
        or not 1 <= draft["model_call_count"] <= 6
        or type(draft.get("revision_count")) is not int
        or draft["revision_count"] not in {0, 1}
        or not _legacy_reply_utc_timestamp_is_valid(draft.get("creation_time"))
    ):
        return False
    if "retrieved_count" in draft and (
        type(draft["retrieved_count"]) is not int or draft["retrieved_count"] < 0
    ):
        return False
    if (
        draft.get("contribution_hash")
        != hashlib.sha256(context["incoming_contribution"].encode("utf-8")).hexdigest()
        or draft.get("context_hash") != _legacy_reply_value_sha256(context)
    ):
        return False
    expected_contract = {
        "proposer_prompt_version": "ai-first-proposer-v15",
        "evidence_prompt_version": "claim-evidence-entailment-v6",
        "reviewer_prompt_version": "independent-reply-reviewer-v13",
        "claim_auditor_prompt_version": "claim-inventory-auditor-v5",
    }
    if any(draft.get(key) != value for key, value in expected_contract.items()):
        return False
    for field in (
        "proposer_model",
        "evidence_model",
        "reviewer_model",
        "claim_auditor_model",
    ):
        value = draft.get(field)
        if not isinstance(value, str) or not value or len(value) > 200:
            return False
    if draft.get("claim_auditor_model") != draft.get("reviewer_model"):
        return False
    direct = draft.get("direct_factual_question_present")
    answer_type = draft.get("requested_answer_type")
    direct_text = draft.get("direct_answer_text")
    if (
        type(direct) is not bool
        or answer_type not in _LEGACY_AI_FIRST_ANSWER_TYPES
        or not isinstance(direct_text, str)
        or (
            draft["mode"] == "direct_factual_answer"
            and (direct is not True or answer_type == "none" or not direct_text)
        )
        or (
            draft["mode"] != "direct_factual_answer"
            and (direct or answer_type != "none" or direct_text)
        )
    ):
        return False
    claims = draft.get("factual_claims")
    if (
        not isinstance(claims, list)
        or len(claims) > 6
        or any(
            not _legacy_ai_first_claim_is_valid(claim, index)
            for index, claim in enumerate(claims, start=1)
        )
        or (draft["mode"] == "direct_factual_answer" and not claims)
    ):
        return False
    if (
        type(draft.get("exact_thatcher_wording_used")) is not bool
        or not isinstance(draft.get("exact_thatcher_wording"), str)
        or draft["exact_thatcher_wording_used"]
        != bool(draft["exact_thatcher_wording"].strip())
    ):
        return False
    evidence_ids = draft.get("evidence_ids")
    source_hashes = draft.get("source_hashes")
    evidence_input_hashes = draft.get("evidence_input_hashes")
    claim_evidence = draft.get("claim_evidence")
    if (
        not isinstance(evidence_ids, list)
        or evidence_ids != sorted(set(evidence_ids))
        or any(not isinstance(item, str) or not item for item in evidence_ids)
        or not isinstance(source_hashes, dict)
        or set(source_hashes) != set(evidence_ids)
        or any(not _legacy_reply_sha256_is_valid(value) for value in source_hashes.values())
        or not isinstance(evidence_input_hashes, dict)
        or set(evidence_input_hashes) != set(evidence_ids)
        or any(
            not _legacy_reply_sha256_is_valid(value)
            for value in evidence_input_hashes.values()
        )
        or not isinstance(claim_evidence, list)
        or len(claim_evidence) != len(claims)
    ):
        return False
    expected_claim_ids = [claim["claim_id"] for claim in claims]
    observed_claim_ids: list[str] = []
    mapped_evidence_ids: set[str] = set()
    for mapping in claim_evidence:
        if not isinstance(mapping, dict) or set(mapping) != {"claim_id", "evidence_ids"}:
            return False
        mapped = mapping.get("evidence_ids")
        if (
            mapping.get("claim_id") not in expected_claim_ids
            or not isinstance(mapped, list)
            or mapped != sorted(set(mapped))
            or any(item not in evidence_ids for item in mapped)
            or (claims and not mapped)
        ):
            return False
        observed_claim_ids.append(str(mapping["claim_id"]))
        mapped_evidence_ids.update(mapped)
    if observed_claim_ids != expected_claim_ids or mapped_evidence_ids != set(evidence_ids):
        return False
    reviewer_reasons = draft.get("reviewer_reasons")
    assessments = draft.get("reviewer_sentence_assessments")
    audits = draft.get("claim_auditor_sentence_assessments")
    if (
        not isinstance(reviewer_reasons, list)
        or len(reviewer_reasons) > 12
        or any(not isinstance(item, str) or len(item) > 300 for item in reviewer_reasons)
        or not isinstance(assessments, list)
        or not assessments
        or any(not _legacy_ai_first_sentence_assessment_is_valid(row) for row in assessments)
        or not isinstance(audits, list)
        or any(not _legacy_ai_first_claim_audit_is_valid(row) for row in audits)
    ):
        return False
    resolved_id = draft.get("resolved_quote_id")
    resolved_hash = draft.get("resolved_quote_context_hash")
    if (resolved_id is None) != (resolved_hash is None):
        return False
    if resolved_id is not None and (
        not isinstance(resolved_id, str)
        or not resolved_id
        or not _legacy_reply_sha256_is_valid(resolved_hash)
    ):
        return False
    approval = draft.get("approval_hash")
    unsigned = {key: value for key, value in draft.items() if key != "approval_hash"}
    return bool(
        _legacy_reply_sha256_is_valid(approval)
        and approval == _legacy_reply_value_sha256(unsigned)
    )


def _legacy_single_sol_reply_draft_is_valid(
    data: dict,
    draft: dict,
    *,
    context: dict,
    text: object,
    _LEGACY_SINGLE_SOL_REPLY_DRAFT_FIELDS: frozenset[str],
    _LEGACY_SINGLE_SOL_REPLY_STRATEGY_VERSION: str,
    _LEGACY_SINGLE_SOL_PROMPT_SHA256: str,
    _LEGACY_SINGLE_SOL_RESPONSE_SCHEMA_SHA256: str,
    _LEGACY_SINGLE_SOL_REPLY_KINDS: frozenset[str],
    _LEGACY_SINGLE_SOL_REASON_CODES: frozenset[str],
    _legacy_reply_utc_timestamp_is_valid: Callable,
    _legacy_reply_sha256_is_valid: Callable,
    _legacy_reply_value_sha256: Callable,
    valid_string_post_id: Callable,
    bound_visible_conversation: Callable,
    MAX_TRUSTED_FACTS: int,
    MAX_SUPPLIED_IMAGES: int,
    _LEGACY_SINGLE_SOL_IMAGE_MIME_TYPES: frozenset[str],
    SINGLE_CALL_MAX_IMAGE_BYTES: int,
) -> bool:
    schema_version = draft.get("draft_schema_version")
    if type(schema_version) is not int or schema_version not in {1, 2, 3}:
        return False
    expected = set(_LEGACY_SINGLE_SOL_REPLY_DRAFT_FIELDS)
    if schema_version >= 2:
        expected.add("target_author_id")
    if schema_version == 3:
        expected.add("quoted_subject_sha256")
    if set(draft) != expected:
        return False
    if (
        draft.get("strategy_version") != _LEGACY_SINGLE_SOL_REPLY_STRATEGY_VERSION
        or draft.get("model") != "gpt-5.6-sol"
        or draft.get("reasoning_effort") != "high"
        or type(draft.get("temperature")) is not int
        or draft.get("temperature") != 1
        or draft.get("model_call_count") != 1
        or draft.get("prompt_sha256") != _LEGACY_SINGLE_SOL_PROMPT_SHA256
        or draft.get("response_schema_sha256")
        != _LEGACY_SINGLE_SOL_RESPONSE_SCHEMA_SHA256
        or draft.get("proposed_reply") != text
        or draft.get("reply_kind") not in _LEGACY_SINGLE_SOL_REPLY_KINDS
        or draft.get("reason_code") not in _LEGACY_SINGLE_SOL_REASON_CODES
        or not _legacy_reply_utc_timestamp_is_valid(draft.get("created_at"))
    ):
        return False
    stored_hash = draft.get("validated_draft_hash")
    unsigned = {
        key: value for key, value in draft.items() if key != "validated_draft_hash"
    }
    if (
        not _legacy_reply_sha256_is_valid(stored_hash)
        or stored_hash != _legacy_reply_value_sha256(unsigned)
    ):
        return False
    context_author_id = context.get("target_author_id")
    if (
        not valid_string_post_id(context.get("target_id"))
        or not valid_string_post_id(context_author_id)
        or context.get("target_id") != data.get("target_id")
        or context_author_id != data.get("author_id")
        or draft.get("target_id") != context.get("target_id")
        or (schema_version >= 2 and draft.get("target_author_id") != context_author_id)
    ):
        return False
    try:
        visible = bound_visible_conversation(
            context.get("visible_conversation") or [],
            target_post_id=str(context["target_id"]),
        )
    except (KeyError, RuntimeError, TypeError, ValueError):
        return False
    root_id = str(context.get("root_post_id") or context.get("thread_id") or "")
    parent_id = context.get("parent_post_id")
    if (
        not valid_string_post_id(root_id)
        or (parent_id is not None and not valid_string_post_id(parent_id))
        or draft.get("root_post_id") != root_id
        or draft.get("parent_post_id") != parent_id
        or draft.get("candidate_source") != context.get("lane")
        or draft.get("incoming_contribution_sha256")
        != hashlib.sha256(visible[-1]["text"].encode("utf-8")).hexdigest()
        or draft.get("canonical_visible_context_sha256")
        != _legacy_reply_value_sha256(visible)
        or not _legacy_reply_sha256_is_valid(draft.get("model_payload_sha256"))
    ):
        return False
    if schema_version == 3:
        # This validator is exclusively for already-started lifecycle receipts;
        # it must never be used to approve a new send or recover a pending draft.
        from single_call_reply import _quoted_subject
        try:
            quoted = _quoted_subject(context, visible_post_ids={turn["post_id"] for turn in visible})
        except (KeyError, RuntimeError, TypeError, ValueError):
            return False
        expected_quoted_hash = _legacy_reply_value_sha256(quoted) if quoted is not None else None
        if draft.get("quoted_subject_sha256") != expected_quoted_hash:
            return False
    trusted_ids = draft.get("trusted_fact_ids")
    used_ids = draft.get("used_fact_ids")
    if (
        not isinstance(trusted_ids, list)
        or len(trusted_ids) > MAX_TRUSTED_FACTS
        or trusted_ids
        != [f"F{index}" for index in range(1, len(trusted_ids) + 1)]
        or not isinstance(used_ids, list)
        or len(used_ids) != len(set(used_ids))
        or not set(used_ids).issubset(set(trusted_ids))
    ):
        return False
    sources = draft.get("used_fact_sources")
    if not isinstance(sources, list) or len(sources) != len(used_ids):
        return False
    for index, source in enumerate(sources):
        if (
            not isinstance(source, dict)
            or set(source)
            != {"fact_id", "source_identity", "source_record_sha256"}
            or source.get("fact_id") != used_ids[index]
            or not isinstance(source.get("source_identity"), str)
            or not source["source_identity"]
            or not _legacy_reply_sha256_is_valid(source.get("source_record_sha256"))
        ):
            return False
    image_bindings = draft.get("supplied_images")
    if not isinstance(image_bindings, list) or len(image_bindings) > MAX_SUPPLIED_IMAGES:
        return False
    saw_quoted = False
    for binding in image_bindings:
        image_fields = {"identity", "sha256", "mime_type", "byte_count"}
        if schema_version == 3:
            image_fields.update({"attachment_role", "source_post_id"})
        if (
            not isinstance(binding, dict)
            or set(binding) != image_fields
            or not isinstance(binding.get("identity"), str)
            or not binding["identity"]
            or not _legacy_reply_sha256_is_valid(binding.get("sha256"))
            or binding.get("mime_type") not in _LEGACY_SINGLE_SOL_IMAGE_MIME_TYPES
            or type(binding.get("byte_count")) is not int
            or not 1 <= binding["byte_count"] <= SINGLE_CALL_MAX_IMAGE_BYTES
        ):
            return False
        if schema_version == 3:
            from single_call_reply import quoted_post_reference_id
            if binding.get("attachment_role") == "target_contribution":
                if saw_quoted or binding.get("source_post_id") != context.get("target_id"):
                    return False
            elif binding.get("attachment_role") == "quoted_subject":
                saw_quoted = True
                if not quoted_post_reference_id(context) or binding.get("source_post_id") != quoted_post_reference_id(context):
                    return False
            else:
                return False
    return True


def _legacy_ai_reply_receipt_draft_is_valid(
    data: dict,
    text: object,
    *,
    _LEGACY_TESTED_REPLY_STRATEGY_VERSION: str,
    _legacy_multi_model_reply_context_is_valid: Callable,
    _legacy_tested_reply_draft_is_valid: Callable,
    _LEGACY_AI_FIRST_REPLY_STRATEGY_VERSION: str,
    _legacy_ai_first_reply_draft_is_valid: Callable,
    _LEGACY_SINGLE_SOL_REPLY_STRATEGY_VERSION: str,
    _legacy_single_sol_reply_draft_is_valid: Callable,
) -> bool:
    """Validate only frozen drafts already protected by reply lifecycle state."""

    context = data.get("reply_context")
    draft = data.get("ai_reply_draft")
    if not isinstance(context, dict) or not isinstance(draft, dict):
        return False
    strategy = draft.get("strategy_version")
    if strategy == _LEGACY_TESTED_REPLY_STRATEGY_VERSION:
        return bool(
            _legacy_multi_model_reply_context_is_valid(context)
            and _legacy_tested_reply_draft_is_valid(
                draft,
                context=context,
                text=text,
            )
        )
    if strategy == _LEGACY_AI_FIRST_REPLY_STRATEGY_VERSION:
        return bool(
            _legacy_multi_model_reply_context_is_valid(context)
            and _legacy_ai_first_reply_draft_is_valid(
                draft,
                context=context,
                text=text,
            )
        )
    if strategy == _LEGACY_SINGLE_SOL_REPLY_STRATEGY_VERSION:
        return _legacy_single_sol_reply_draft_is_valid(
            data,
            draft,
            context=context,
            text=text,
        )
    return False
