"""Read-only durable confirmed reply evidence loading and validation.

Callers supply project paths, stable private-file reads, strict native-number
JSON parsing, the distinct canonical receipt/history encoders, timestamp
conversion and ISO parsing, the London timezone and validator callbacks.
The coordinator retains its entry points and supplies current dependencies on
each call; validation delegates through those callbacks without storing them.

Publication evidence remains distinct from drafts and unconfirmed observations.
Import performs no runtime I/O or service initialisation; this module owns no
publication, recovery, reconciliation or report-enrichment actions.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone, tzinfo
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from mrs_log_digest_values import (
    SHA256_LOWER_RE,
    bounded_exception_status,
    valid_bounded_utf8_text,
    valid_string_public_post_id,
)


CONFIRMED_REPLY_RECEIPT_MAX_BYTES = 1024 * 1024
HISTORICAL_REPLY_HISTORY_MAX_BYTES = 64 * 1024 * 1024
MIN_CONFIRMED_PUBLICATION_EPOCH = 1_500_000_000
MAX_CONFIRMED_PUBLICATION_EPOCH = 4_102_444_800


def valid_conversational_public_reply_text(value: Any) -> bool:
    """Validate durable exact reply text, including long and multiline posts."""

    # Publication receipts and current state describe text already confirmed by
    # X, not a draft to be revalidated against today's generation policy.  The
    # evidence contract therefore preserves valid long posts and embedded
    # newlines exactly, while retaining the digest's explicit safety bound.
    return valid_bounded_utf8_text(value)


def _structured_value_sha256(value: Any) -> str:
    """Hash one immutable draft value using the production approval encoding."""

    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _valid_durable_ai_reply_draft(
    draft: Any,
    *,
    context: Dict[str, Any],
    lane: str,
    target_id: str,
    conversation_id: str,
    reply_text: str,
    valid_utc_timestamp: Callable[[Any], bool],
) -> bool:
    """Validate immutable draft identity and its reviewer-approval hash."""

    if not isinstance(draft, dict):
        return False
    common = {
        "schema_version",
        "strategy_version",
        "target_id",
        "thread_id",
        "candidate_source",
        "contribution_hash",
        "context_hash",
        "proposed_reply",
        "mode",
        "reviewer_verdict",
        "creation_time",
        "approval_hash",
    }
    strategy = draft.get("strategy_version")
    if strategy == "tested-reply-pipeline-20260817":
        required = common | {
            "trusted_facts_hash",
            "final_reply_kind",
            "tone",
            "factual_claims",
            "evidence_ids",
            "trusted_facts_supplied_count",
            "trusted_fact_ids_supplied",
            "used_fact_count",
            "used_fact_ids",
            "claim_risk_categories",
            "model_call_count",
            "revision_count",
            "reply_requirement",
            "route_source",
        }
        optional_repair = {
            "direct_answer_repair_attempted",
            "direct_answer_repair_outcome",
            "original_local_rejection_reason",
            "original_proposed_reply",
        }
        if (
            type(draft.get("schema_version")) is not int
            or draft.get("schema_version") != 1
            or not required.issubset(draft)
            or set(draft) - required - optional_repair
            or not SHA256_LOWER_RE.fullmatch(
                str(draft.get("trusted_facts_hash") or "")
            )
        ):
            return False
        supplied_fact_ids = draft.get("trusted_fact_ids_supplied")
        supplied_fact_count = draft.get("trusted_facts_supplied_count")
        used_fact_count = draft.get("used_fact_count")
        used_fact_ids = draft.get("used_fact_ids")
        if (
            draft.get("mode")
            not in {"direct_factual_answer", "opinion_or_principle"}
            or draft.get("final_reply_kind") not in {"factual", "unknown"}
            or draft.get("tone") != "unknown"
            or not isinstance(draft.get("factual_claims"), list)
            or not all(
                isinstance(item, str) for item in draft["factual_claims"]
            )
            or draft.get("evidence_ids") is not None
            or type(supplied_fact_count) is not int
            or supplied_fact_count < 0
            or not isinstance(supplied_fact_ids, list)
            or not all(isinstance(item, str) for item in supplied_fact_ids)
            or supplied_fact_count != len(supplied_fact_ids)
            or (
                used_fact_count != "unknown"
                and (type(used_fact_count) is not int or used_fact_count < 0)
            )
            or (
                used_fact_count == "unknown" and used_fact_ids is not None
            )
            or (
                type(used_fact_count) is int
                and (
                    not isinstance(used_fact_ids, list)
                    or not all(isinstance(item, str) for item in used_fact_ids)
                    or used_fact_count != len(used_fact_ids)
                )
            )
            or not isinstance(draft.get("claim_risk_categories"), list)
            or not all(
                isinstance(item, str)
                for item in draft["claim_risk_categories"]
            )
            or type(draft.get("model_call_count")) is not int
            or draft["model_call_count"] < 1
            or type(draft.get("revision_count")) is not int
            or draft["revision_count"] < 0
            or draft.get("reply_requirement")
            not in {"general", "claim_free", "supported_factual", "premise_neutral"}
            or not isinstance(draft.get("route_source"), str)
            or not draft["route_source"]
            or len(draft["route_source"]) > 128
        ):
            return False
    elif strategy == "ai-first-reply-v3":
        required = common | {
            "direct_factual_question_present",
            "requested_answer_type",
            "direct_answer_text",
            "tone",
            "factual_claims",
            "exact_thatcher_wording_used",
            "exact_thatcher_wording",
            "evidence_ids",
            "claim_evidence",
            "source_hashes",
            "evidence_input_hashes",
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
        }
        if (
            type(draft.get("schema_version")) is not int
            or draft.get("schema_version") != 9
            or frozenset(draft)
            not in {
                frozenset(required),
                frozenset(required | {"retrieved_count"}),
            }
        ):
            return False
        if (
            draft.get("mode")
            not in {
                "direct_factual_answer",
                "opinion_or_principle",
                "light_humour",
                "courtesy",
            }
            or draft.get("tone")
            not in {"firm", "dry", "wry", "warm", "neutral", "light", "none"}
            or type(draft.get("model_call_count")) is not int
            or draft["model_call_count"] < 1
            or type(draft.get("revision_count")) is not int
            or draft["revision_count"] not in {0, 1}
            or (
                "retrieved_count" in draft
                and (
                    type(draft.get("retrieved_count")) is not int
                    or draft["retrieved_count"] < 0
                )
            )
        ):
            return False
    else:
        return False
    incoming = context.get("incoming_contribution")
    approval = draft.get("approval_hash")
    unsigned = {key: value for key, value in draft.items() if key != "approval_hash"}
    return bool(
        draft.get("target_id") == target_id
        and draft.get("thread_id") == conversation_id
        and draft.get("candidate_source") == lane
        and draft.get("proposed_reply") == reply_text
        and draft.get("reviewer_verdict") == "approve"
        and isinstance(draft.get("mode"), str)
        and bool(draft["mode"])
        and isinstance(incoming, str)
        and bool(incoming.strip())
        and draft.get("contribution_hash")
        == hashlib.sha256(incoming.encode("utf-8")).hexdigest()
        and draft.get("context_hash") == _structured_value_sha256(context)
        and valid_utc_timestamp(draft.get("creation_time"))
        and isinstance(approval, str)
        and SHA256_LOWER_RE.fullmatch(approval)
        and approval == _structured_value_sha256(unsigned)
    )


def _confirmed_conversational_receipt_evidence(
    receipt: Any,
    *,
    valid_text: Callable[[Any], bool],
    valid_draft: Callable[..., bool],
    fromtimestamp: Callable[..., datetime],
    london: tzinfo,
    encode_atomic_json: Callable[[Any], bytes],
) -> Optional[Dict[str, Any]]:
    """Project exact text from one narrowly validated confirmed receipt."""

    if not isinstance(receipt, dict):
        return None
    schema_version = receipt.get("schema_version")
    if type(schema_version) is not int or schema_version not in {2, 3, 4}:
        return None
    if schema_version == 2:
        if "lifecycle_state" in receipt:
            return None
    elif receipt.get("lifecycle_state") != "confirmed":
        # A sending receipt proves only an unresolved attempt, never publication.
        return None
    source = receipt.get("candidate_source")
    target_value = receipt.get("target_id")
    reply_post_value = receipt.get("reply_post_id")
    author_value = receipt.get("author_id")
    conversation_value = receipt.get("conversation_id")
    lane = (
        source
        if type(source) is str
        and source in {"mention", "hot_post_reply", "quote_tweet"}
        else "unavailable"
    )
    target_id = str(target_value or "")
    reply_post_id = str(reply_post_value or "")
    author_id = str(author_value or "")
    conversation_id = str(conversation_value or "")
    reply_text = receipt.get("reply_text")
    reply_epoch = receipt.get("reply_epoch")
    if (
        lane not in {"mention", "hot_post_reply", "quote_tweet"}
        or not valid_string_public_post_id(target_value)
        or not valid_string_public_post_id(reply_post_value)
        or not valid_string_public_post_id(author_value)
        or not valid_string_public_post_id(conversation_value)
        or not valid_text(reply_text)
        or type(reply_epoch) is not int
        or not (
            MIN_CONFIRMED_PUBLICATION_EPOCH
            <= reply_epoch
            <= MAX_CONFIRMED_PUBLICATION_EPOCH
        )
    ):
        return None
    context = receipt.get("reply_context")
    if (
        not isinstance(context, dict)
        or context.get("target_id") != target_id
        or context.get("thread_id") != conversation_id
        or context.get("lane") != lane
    ):
        return None
    draft = receipt.get("ai_reply_draft")
    if not valid_draft(
        draft,
        context=context,
        lane=lane,
        target_id=target_id,
        conversation_id=conversation_id,
        reply_text=reply_text,
    ):
        return None
    original_post_id = receipt.get("original_post_id")
    if lane == "quote_tweet":
        quoted_post = context.get("quoted_post")
        if (
            not valid_string_public_post_id(original_post_id)
            or not isinstance(quoted_post, dict)
            or type(quoted_post.get("post_id")) is not str
            or quoted_post.get("post_id") != original_post_id
        ):
            return None
        original_post_id = str(original_post_id)
    elif original_post_id is not None:
        return None
    else:
        original_post_id = ""
    if "mention_pagination" in receipt:
        pagination = receipt.get("mention_pagination")
        if (
            schema_version not in {3, 4}
            or lane != "mention"
            or not isinstance(pagination, dict)
            or set(pagination) != {"base_since_id", "next_token"}
            or type(pagination.get("base_since_id")) is not str
            or (
                pagination.get("base_since_id") != ""
                and not valid_string_public_post_id(
                    pagination.get("base_since_id")
                )
            )
            or type(pagination.get("next_token")) is not str
            or not pagination.get("next_token")
            or pagination.get("next_token")
            != pagination.get("next_token").strip()
            or any(
                character.isspace()
                for character in pagination.get("next_token")
            )
        ):
            return None
    clarification = receipt.get("clarification_reply")
    if clarification is not None:
        if (
            lane not in {"mention", "hot_post_reply"}
            or not isinstance(clarification, dict)
            or set(clarification)
            != {
                "thread_id",
                "prior_bot_reply_id",
                "original_question_id",
                "trigger",
            }
            or any(
                not valid_string_public_post_id(clarification.get(field))
                for field in (
                    "thread_id",
                    "prior_bot_reply_id",
                    "original_question_id",
                )
            )
            or clarification.get("trigger")
            not in {"explicit_correction", "restated_question"}
            or clarification.get("thread_id") != conversation_id
            or draft.get("mode") != "direct_factual_answer"
        ):
            return None
    if schema_version == 4:
        attempt_epoch = receipt.get("attempt_epoch")
        confirmation_epoch = receipt.get("confirmation_epoch")
        if (
            type(attempt_epoch) is not int
            or type(confirmation_epoch) is not int
            or not (
                MIN_CONFIRMED_PUBLICATION_EPOCH
                <= attempt_epoch
                <= confirmation_epoch
                <= MAX_CONFIRMED_PUBLICATION_EPOCH
            )
            or reply_epoch != confirmation_epoch
        ):
            return None
        confirmation_date = fromtimestamp(
            confirmation_epoch,
            tz=london,
        ).strftime("%Y-%m-%d")
        if receipt.get("daily_reply_date") != confirmation_date:
            return None
        if lane == "quote_tweet":
            if receipt.get("daily_quote_reply_date") != confirmation_date:
                return None
        elif "daily_quote_reply_date" in receipt:
            return None
        if "source_receipt_sha256" in receipt:
            source_hash = receipt.get("source_receipt_sha256")
            if not isinstance(source_hash, str) or not SHA256_LOWER_RE.fullmatch(
                source_hash
            ):
                return None
            sending = dict(receipt)
            sending.pop("reply_post_id", None)
            sending.pop("confirmation_epoch", None)
            sending.pop("source_receipt_sha256", None)
            sending["lifecycle_state"] = "sending"
            sending["reply_epoch"] = attempt_epoch
            attempt_date = fromtimestamp(
                attempt_epoch,
                tz=london,
            ).strftime("%Y-%m-%d")
            sending["daily_reply_date"] = attempt_date
            if lane == "quote_tweet":
                sending["daily_quote_reply_date"] = attempt_date
            else:
                sending.pop("daily_quote_reply_date", None)
            if hashlib.sha256(encode_atomic_json(sending)).hexdigest() != source_hash:
                return None
    else:
        for date_key in ("daily_reply_date", "daily_quote_reply_date"):
            if receipt.get(date_key) is not None and not isinstance(
                receipt.get(date_key),
                str,
            ):
                return None
        if "source_receipt_sha256" in receipt:
            return None
    return {
        "lane": lane,
        "target_id": target_id,
        "reply_post_id": reply_post_id,
        "original_post_id": original_post_id,
        "reply_text": reply_text,
        "reply_epoch": reply_epoch,
        "source": "confirmed_reply_receipt.json",
    }


def load_confirmed_reply_receipt_evidence(
    project_dir: Path,
    *,
    read_bytes: Callable[..., bytes],
    parse_json_object: Callable[..., Dict[str, Any]],
    encode_atomic_json: Callable[[Any], bytes],
    validate_receipt: Callable[[Any], Optional[Dict[str, Any]]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Load an active exact confirmed conversational receipt, if present."""

    path = Path(project_dir) / "confirmed_reply_receipt.json"
    status: Dict[str, Any] = {
        "source": path.name,
        "available": False,
        "status": "absent",
        "record_count": 0,
        "reason": "",
    }
    try:
        raw = read_bytes(
            path,
            maximum=CONFIRMED_REPLY_RECEIPT_MAX_BYTES,
        )
    except FileNotFoundError:
        return [], status
    except Exception as exc:
        status.update(
            {
                "status": "unavailable",
                "reason": bounded_exception_status("unavailable", exc),
            }
        )
        return [], status
    try:
        receipt = parse_json_object(
            raw,
            label="confirmed_reply_receipt.json",
        )
        if encode_atomic_json(receipt) != raw:
            raise ValueError("receipt is not canonical JSON")
        evidence = validate_receipt(receipt)
        if evidence is None:
            raise ValueError("receipt is not a confirmed publication authority")
    except Exception as exc:
        status.update(
            {
                "status": "unavailable",
                "reason": bounded_exception_status("unavailable", exc),
            }
        )
        return [], status
    status.update({"available": True, "status": "available", "record_count": 1})
    return [evidence], status


def _valid_canonical_utc_timestamp(
    value: Any,
    *,
    fromisoformat: Callable[[str], datetime],
) -> bool:
    """Return whether a value is one bounded canonical UTC timestamp."""

    if type(value) is not str or not value.endswith("Z") or len(value) > 64:
        return False
    try:
        parsed = fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return bool(
        parsed.tzinfo is not None
        and parsed.utcoffset() == timezone.utc.utcoffset(parsed)
        and parsed.isoformat().replace("+00:00", "Z") == value
    )


def _valid_historical_formatter_metadata(value: Any) -> bool:
    """Mirror the durable history store's bounded formatter metadata schema."""

    if not isinstance(value, dict):
        return False
    v2_keys = {
        "formatter_version",
        "template_variant",
        "meaning_included",
        "meaning_decision_reason",
        "raw_character_count",
        "weighted_character_count",
        "verification_label",
        "source_class",
        "historical_confidence",
        "shortening_applied",
    }
    v3_keys = v2_keys | {"confidence_dimensions", "source_role_audit_version"}
    v4_keys = v3_keys | {"rendering_mode"}
    keys = frozenset(value)
    if keys not in {frozenset(v2_keys), frozenset(v3_keys), frozenset(v4_keys)}:
        return False
    version = value.get("formatter_version")
    if keys == v3_keys and version != "historical_context_reply_schema_v3":
        return False
    if keys == v4_keys and version not in {
        "historical_context_reply_schema_v4",
        "historical_context_reply_schema_v5",
    }:
        return False
    if version in {
        "historical_context_reply_schema_v3",
        "historical_context_reply_schema_v4",
        "historical_context_reply_schema_v5",
    }:
        dimensions = value.get("confidence_dimensions")
        audit_versions = {
            "historical-context-source-roles-v1",
            "historical-context-source-roles-v2-recovered-citations",
            "historical-context-source-roles-v4-multi-provider-guarded-approximate-80",
            "historical-context-source-roles-v5-independent-review-and-exclusive-counts",
            "historical-context-source-roles-v7-curated-source-adjudications",
            "historical-context-source-roles-v8-claim-specific-public-context",
            "historical-context-source-roles-v9-archive-provenance",
        }
        if (
            not isinstance(dimensions, dict)
            or set(dimensions)
            != {
                "attribution",
                "wording",
                "source_event",
                "date",
                "historical_context",
                "interpretation",
            }
            or any(
                item not in {"high", "medium", "low", "unknown"}
                for item in dimensions.values()
            )
            or value.get("source_role_audit_version") not in audit_versions
        ):
            return False
    if version in {
        "historical_context_reply_schema_v4",
        "historical_context_reply_schema_v5",
    } and value.get("rendering_mode") not in {"public", "internal"}:
        return False
    return bool(
        isinstance(version, str)
        and version.startswith("historical_context_reply_schema_v")
        and isinstance(value.get("template_variant"), str)
        and bool(value["template_variant"])
        and type(value.get("meaning_included")) is bool
        and isinstance(value.get("meaning_decision_reason"), str)
        and bool(value["meaning_decision_reason"])
        and type(value.get("raw_character_count")) is int
        and value["raw_character_count"] >= 1
        and type(value.get("weighted_character_count")) is int
        and value["weighted_character_count"] >= 1
        and (
            value.get("verification_label") is None
            or isinstance(value["verification_label"], str)
        )
        and isinstance(value.get("source_class"), str)
        and value.get("historical_confidence")
        in {"high", "medium", "low", "unavailable"}
        and type(value.get("shortening_applied")) is bool
    )


def _valid_historical_completed_item(
    parent_key: str,
    item: Any,
    *,
    encode_private_json: Callable[[Any], bytes],
) -> Optional[Dict[str, Any]]:
    """Validate and project one completed historical-context history row."""

    if not isinstance(item, dict) or item.get("status") != "completed":
        return None
    receipt = {key: value for key, value in item.items() if key != "status"}
    required = {
        "schema_version",
        "parent_post_id",
        "reply_post_id",
        "quote_id",
        "reply_text",
        "reply_epoch",
        "confirmed_at",
    }
    lifecycle = required | {"lifecycle_state", "started_at", "attempt_number"}
    permitted = {
        frozenset(required),
        frozenset(lifecycle),
        frozenset(lifecycle | {"formatter_metadata"}),
        frozenset(lifecycle | {"source_receipt_sha256"}),
        frozenset(lifecycle | {"formatter_metadata", "source_receipt_sha256"}),
    }
    parent_value = receipt.get("parent_post_id")
    reply_post_value = receipt.get("reply_post_id")
    parent_id = str(parent_value or "")
    reply_post_id = str(reply_post_value or "")
    quote_id = receipt.get("quote_id")
    reply_text = receipt.get("reply_text")
    if (
        frozenset(receipt) not in permitted
        or receipt.get("schema_version") != 1
        or type(receipt.get("schema_version")) is not int
        or parent_id != parent_key
        or not valid_string_public_post_id(parent_value)
        or not valid_string_public_post_id(reply_post_value)
        or not isinstance(quote_id, str)
        or not SHA256_LOWER_RE.fullmatch(quote_id)
        or not valid_bounded_utf8_text(reply_text)
        or not reply_text.strip()
        or type(receipt.get("reply_epoch")) is not int
        or receipt["reply_epoch"] < 0
        or not isinstance(receipt.get("confirmed_at"), str)
        or not receipt["confirmed_at"].strip()
    ):
        return None
    if "lifecycle_state" in receipt and receipt.get("lifecycle_state") != "confirmed":
        return None
    if "started_at" in receipt and (
        not isinstance(receipt["started_at"], str)
        or not receipt["started_at"].strip()
    ):
        return None
    if "attempt_number" in receipt and (
        type(receipt["attempt_number"]) is not int
        or receipt["attempt_number"] < 1
    ):
        return None
    if "formatter_metadata" in receipt and not _valid_historical_formatter_metadata(
        receipt["formatter_metadata"]
    ):
        return None
    if "source_receipt_sha256" in receipt:
        source_hash = receipt.get("source_receipt_sha256")
        if not isinstance(source_hash, str) or not SHA256_LOWER_RE.fullmatch(
            source_hash
        ):
            return None
        if not (
            MIN_CONFIRMED_PUBLICATION_EPOCH
            <= receipt["reply_epoch"]
            <= MAX_CONFIRMED_PUBLICATION_EPOCH
        ):
            return None
        sending = dict(receipt)
        sending.pop("reply_post_id", None)
        sending.pop("confirmed_at", None)
        sending.pop("source_receipt_sha256", None)
        sending["lifecycle_state"] = "sending"
        if hashlib.sha256(encode_private_json(sending)).hexdigest() != source_hash:
            return None
    return {
        "time": str(receipt.get("confirmed_at") or ""),
        "parent_post_id": parent_id,
        "reply_post_id": reply_post_id,
        "quote_id": quote_id,
        "authoritative": True,
        "reply_text": reply_text,
        "source": "historical_context_reply_history.json",
        "durable_only": True,
    }


def _valid_historical_failed_item(
    parent_key: str,
    item: Any,
    *,
    valid_utc_timestamp: Callable[[Any], bool],
) -> bool:
    """Validate enough of a failed row to prove the history object is coherent."""

    if not isinstance(item, dict) or item.get("status") != "failed":
        return False
    required = {
        "parent_post_id",
        "quote_id",
        "reply_text",
        "status",
        "failure",
        "attempt_count",
        "updated_at",
    }
    source_proof = {
        "remote_outcome",
        "source_receipt_sha256",
        "source_receipt_attempt_number",
    }
    allowed_sets = {
        frozenset(required),
        frozenset(required | {"formatter_metadata"}),
        frozenset(required | source_proof),
        frozenset(required | source_proof | {"formatter_metadata"}),
    }
    if (
        frozenset(item) not in allowed_sets
        or type(item.get("parent_post_id")) is not str
        or item.get("parent_post_id") != parent_key
        or not valid_string_public_post_id(parent_key)
        or not isinstance(item.get("quote_id"), str)
        or not SHA256_LOWER_RE.fullmatch(item["quote_id"])
        or not valid_bounded_utf8_text(item.get("reply_text"))
        or not item["reply_text"].strip()
        or not isinstance(item.get("failure"), str)
        or not item["failure"].strip()
        or type(item.get("attempt_count")) is not int
        or item["attempt_count"] < 1
        or not valid_utc_timestamp(item.get("updated_at"))
        or (
            "formatter_metadata" in item
            and not _valid_historical_formatter_metadata(
                item["formatter_metadata"]
            )
        )
    ):
        return False
    if source_proof & set(item):
        return bool(
            source_proof.issubset(item)
            and item.get("remote_outcome") == "proved_non_success"
            and isinstance(item.get("source_receipt_sha256"), str)
            and SHA256_LOWER_RE.fullmatch(item["source_receipt_sha256"])
            and type(item.get("source_receipt_attempt_number")) is int
            and item["source_receipt_attempt_number"] >= 1
            and item["source_receipt_attempt_number"] == item["attempt_count"]
        )
    return True


def load_historical_reply_history_evidence(
    project_dir: Path,
    *,
    read_bytes: Callable[..., bytes],
    parse_json_object: Callable[..., Dict[str, Any]],
    encode_private_json: Callable[[Any], bytes],
    validate_completed_item: Callable[[str, Any], Optional[Dict[str, Any]]],
    validate_failed_item: Callable[[str, Any], bool],
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Load bounded exact text from completed durable historical reply history."""

    path = Path(project_dir) / "historical_context_reply_history.json"
    status: Dict[str, Any] = {
        "source": path.name,
        "available": False,
        "status": "absent",
        "completed_record_count": 0,
        "failed_record_count": 0,
        "reason": "",
        "byte_limit": HISTORICAL_REPLY_HISTORY_MAX_BYTES,
    }
    try:
        raw = read_bytes(
            path,
            maximum=HISTORICAL_REPLY_HISTORY_MAX_BYTES,
        )
    except FileNotFoundError:
        return [], status
    except Exception as exc:
        status.update(
            {
                "status": "unavailable",
                "reason": bounded_exception_status("unavailable", exc),
            }
        )
        return [], status
    try:
        history = parse_json_object(
            raw,
            label="historical_context_reply_history.json",
        )
        if encode_private_json(history) != raw:
            raise ValueError("history is not canonical JSON")
        if (
            set(history) != {"schema_version", "items"}
            or type(history.get("schema_version")) is not int
            or history.get("schema_version") != 1
            or not isinstance(history.get("items"), dict)
        ):
            raise ValueError("history root schema is invalid")
        evidence: List[Dict[str, Any]] = []
        failed_count = 0
        for position, (parent_key, item) in enumerate(
            history["items"].items(),
            1,
        ):
            projected = validate_completed_item(parent_key, item)
            if projected is not None:
                evidence.append(projected)
            elif validate_failed_item(parent_key, item):
                failed_count += 1
            else:
                raise ValueError(
                    f"invalid history item at position {position}"
                )
    except Exception as exc:
        status.update(
            {
                "status": "unavailable",
                "reason": bounded_exception_status("unavailable", exc),
            }
        )
        return [], status
    status.update(
        {
            "available": True,
            "status": "available",
            "completed_record_count": len(evidence),
            "failed_record_count": failed_count,
        }
    )
    return evidence, status
