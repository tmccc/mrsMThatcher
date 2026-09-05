"""Single-call conversational reply observations and emitted-event summaries.

Specific handlers project already parsed fields through the coordinator's
add_event callback. Event construction, provenance, truncation and statistics
remain there, as do parsing and publication/recovery evidence handling. This
module has no I/O, runtime initialisation or state shared between analyses.
"""
from __future__ import annotations

import math
from collections import Counter
from datetime import datetime
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from mrs_log_digest_values import (
    bounded_event_nonnegative_integer,
    bounded_event_nonnegative_integer_observation,
    bounded_event_text,
    normalise_reply_lane,
    valid_string_public_post_id,
)


def single_call_reply_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Summarise the sole production conversational decision architecture."""

    decisions = [
        item for item in events
        if item.get("kind") == "single_call_reply_decision"
    ]
    usage = [
        item for item in events
        if item.get("kind") == "single_call_reply_provider_usage"
    ]
    posting = [
        item for item in events
        if item.get("kind") == "single_call_reply_posting_outcome"
    ]
    recovered = [
        item for item in events
        if item.get("kind") == "single_call_reply_draft_recovered"
    ]

    def count_values(rows: List[Dict[str, Any]], field: str) -> Dict[str, int]:
        return dict(Counter(
            str(item.get(field) or "unavailable") for item in rows
        ).most_common())

    def average(field: str) -> Optional[float]:
        values = [
            item[field]
            for item in decisions
            if type(item.get(field)) is int
        ]
        return (sum(values) / len(values)) if values else None

    editorial_no_reply = [
        item for item in decisions
        if item.get("pipeline_status") == "no_reply"
        and item.get("outcome_type") == "editorial"
        and item.get("local_validation_status") == "passed"
    ]
    operational = [
        item for item in decisions
        if item.get("pipeline_status") == "operational_failure"
        or item.get("outcome_type") == "operational"
    ]
    model_attempts = [
        item for item in decisions
        if type(item.get("model_call_count")) is int
        and item["model_call_count"] > 0
    ]

    def candidate_key(item: Mapping[str, Any]) -> Tuple[str, str]:
        return (
            normalise_reply_lane(item.get("lane")),
            str(item.get("target_id") or ""),
        )
    invalid_call_counts = [
        item for item in decisions
        if type(item.get("model_call_count")) is not int
        or item["model_call_count"] not in {0, 1}
        or (
            item.get("pipeline_status") in {"reply", "no_reply"}
            and item["model_call_count"] != 1
        )
    ]
    model_attempt_decisions_per_candidate = Counter(
        candidate_key(item) for item in model_attempts
    )
    repeated_model_attempt_keys = {
        key
        for key, count in model_attempt_decisions_per_candidate.items()
        if count > 1
    }
    usage_per_candidate = Counter(candidate_key(item) for item in usage)
    excess_usage_keys = {
        key
        for key, count in usage_per_candidate.items()
        if count > max(1, model_attempt_decisions_per_candidate.get(key, 0))
    }

    attempt_violation_keys: set[Tuple[str, str]] = set()
    incomplete_attempt_keys: set[Tuple[str, str]] = set()
    incomplete_attempt_decision_ids: set[int] = set()
    authorised_retry_decisions: Counter[Tuple[str, str]] = Counter()
    authorised_retry_usage: Counter[Tuple[str, str]] = Counter()
    attempt_metadata_status_counts: Counter[str] = Counter()
    decision_attempt_counts: Dict[Tuple[str, str], set[int]] = {}
    usage_attempt_counts: Dict[Tuple[str, str], set[int]] = {}

    for item in [*decisions, *usage]:
        is_decision = item.get("kind") == "single_call_reply_decision"
        count_key = (
            "provider_request_attempt_count" if is_decision
            else "request_attempt_count"
        )
        status_key = f"{count_key}_status"
        status = item.get(status_key)
        if status not in {"available", "missing", "malformed", "out_of_range"}:
            if count_key not in item:
                status = "missing"
            elif type(item.get(count_key)) is int and item[count_key] >= 0:
                status = "available"
            else:
                status = "malformed"
        attempt_metadata_status_counts[str(status)] += 1
        key = candidate_key(item)
        if status in {"missing", "malformed"}:
            incomplete_attempt_keys.add(key)
            if is_decision:
                incomplete_attempt_decision_ids.add(id(item))
            continue
        if status == "out_of_range":
            attempt_violation_keys.add(key)
            continue

        attempt_count = item.get(count_key)
        assert type(attempt_count) is int
        if attempt_count == 2:
            (
                authorised_retry_decisions
                if is_decision
                else authorised_retry_usage
            )[key] += 1
        if attempt_count > 2:
            attempt_violation_keys.add(key)
        if is_decision:
            model_call_count = item.get("model_call_count")
            if model_call_count == 0 and attempt_count != 0:
                attempt_violation_keys.add(key)
            elif model_call_count == 1:
                decision_attempt_counts.setdefault(key, set()).add(attempt_count)
                if attempt_count not in {1, 2}:
                    attempt_violation_keys.add(key)
        else:
            usage_attempt_counts.setdefault(key, set()).add(attempt_count)
            if attempt_count not in {1, 2}:
                attempt_violation_keys.add(key)

    attempt_count_mismatch_keys = {
        key
        for key in decision_attempt_counts.keys() & usage_attempt_counts.keys()
        if model_attempt_decisions_per_candidate.get(key) == 1
        and usage_per_candidate.get(key) == 1
        and decision_attempt_counts[key] != usage_attempt_counts[key]
    }
    authorised_pre_execution_retry_count = sum(
        max(
            authorised_retry_decisions.get(key, 0),
            authorised_retry_usage.get(key, 0),
        )
        for key in authorised_retry_decisions.keys() | authorised_retry_usage.keys()
    )
    invalid_call_ids = {id(item) for item in invalid_call_counts}
    violating_candidate_keys = (
        excess_usage_keys
        | attempt_violation_keys
        | attempt_count_mismatch_keys
    )
    decision_candidate_keys = {candidate_key(item) for item in decisions}
    violating_decision_ids = invalid_call_ids | {
        id(item)
        for item in decisions
        if candidate_key(item) in violating_candidate_keys
    }
    incomplete_decision_ids = {
        id(item)
        for item in decisions
        if id(item) not in violating_decision_ids
        and (
            id(item) in incomplete_attempt_decision_ids
            or candidate_key(item) in incomplete_attempt_keys
        )
    }
    compliant_decisions = [
        item
        for item in decisions
        if id(item) not in violating_decision_ids
        and id(item) not in incomplete_decision_ids
    ]
    one_call_violations = len(violating_decision_ids) + len(
        violating_candidate_keys - decision_candidate_keys
    )
    one_call_incomplete = len(incomplete_decision_ids) + len(
        incomplete_attempt_keys
        - violating_candidate_keys
        - decision_candidate_keys
    )
    token_fields = (
        "input_tokens",
        "cached_input_tokens",
        "cache_write_input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "total_tokens",
    )
    token_totals = {
        field: sum(
            item[field] for item in usage if type(item.get(field)) is int
        )
        for field in token_fields
    }
    latencies = [
        item["provider_latency_ms"]
        for item in usage
        if type(item.get("provider_latency_ms")) is int
    ]
    confirmed_keys = {
        (normalise_reply_lane(item.get("lane")), str(item.get("target_id") or ""))
        for item in posting
        if item.get("status") == "confirmed"
    }
    posting_failures = [
        item for item in posting
        if item.get("status") not in {"confirmed", "dry_run"}
    ]
    schema_failure_count = sum(
        item.get("error_category") == "schema_validation"
        for item in decisions
    )
    local_failure_count = sum(
        item.get("local_validation_status") == "failed"
        and item.get("error_category") != "schema_validation"
        for item in decisions
    )
    return {
        "candidate_evaluation_count": len(decisions),
        "reply_decision_count": sum(
            item.get("pipeline_status") == "reply" for item in decisions
        ),
        "replies_posted_count": len(confirmed_keys),
        "editorial_no_reply_count": len(editorial_no_reply),
        "operational_failure_count": len(operational),
        "posting_failure_count": len(posting_failures),
        "recovered_draft_count": len(recovered),
        "model_attempt_count": len(model_attempts),
        "one_call_compliant_count": len(compliant_decisions),
        "one_call_violation_count": one_call_violations,
        "one_call_incomplete_count": one_call_incomplete,
        "one_call_compliance": (
            "failed" if one_call_violations else
            "incomplete" if one_call_incomplete else
            "passed" if decisions else "no_candidates"
        ),
        "repeated_model_attempt_candidate_count": len(
            repeated_model_attempt_keys
        ),
        "excess_provider_usage_candidate_count": len(excess_usage_keys),
        "authorised_pre_execution_retry_count": (
            authorised_pre_execution_retry_count
        ),
        "provider_request_attempt_metadata_status_counts": dict(
            attempt_metadata_status_counts.most_common()
        ),
        "provider_request_attempt_mismatch_candidate_count": len(
            attempt_count_mismatch_keys
        ),
        "strategy_version_counts": count_values(decisions, "strategy_version"),
        "model_counts": count_values(decisions, "model"),
        "lane_counts": count_values(decisions, "lane"),
        "reply_kind_counts": count_values(
            [item for item in decisions if item.get("pipeline_status") == "reply"],
            "reply_kind",
        ),
        "no_reply_reason_counts": count_values(editorial_no_reply, "reason_code"),
        "operational_failure_reason_counts": count_values(
            operational, "failure_reason"
        ),
        "error_category_counts": count_values(operational, "error_category"),
        "schema_validation_failure_count": schema_failure_count,
        "local_validation_failure_count": local_failure_count,
        "average_visible_turn_count": average("visible_turn_count"),
        "average_visible_character_count": average("visible_character_count"),
        "average_same_author_interaction_count": average(
            "same_author_interaction_count"
        ),
        "average_recent_conversational_reply_count": average(
            "recent_conversational_reply_count"
        ),
        "average_trusted_fact_count": average("trusted_fact_count"),
        "average_supplied_image_count": average("supplied_image_count"),
        "provider_usage_event_count": len(usage),
        "token_totals": token_totals,
        "provider_latency_average_ms": (
            sum(latencies) / len(latencies) if latencies else None
        ),
        "provider_latency_maximum_ms": max(latencies) if latencies else None,
        "provider_request_attempt_counts": count_values(
            usage, "request_attempt_count"
        ),
        "cost_total": {
            "status": "unavailable_during_log_analysis",
            "amount": None,
        },
    }


def record_single_call_reply_decision(
    event_obj: Dict[str, Any],
    ts: datetime,
    *,
    add_event: Callable[..., Dict[str, Any]],
) -> None:
    """Project single-call decision fields and emit through the coordinator."""
    temperature = event_obj.get("temperature")
    if type(temperature) not in {int, float} or not math.isfinite(
        float(temperature)
    ):
        temperature = None
    (
        provider_request_attempt_count,
        provider_request_attempt_count_status,
    ) = bounded_event_nonnegative_integer_observation(
        event_obj,
        "provider_request_attempt_count",
        maximum=1_000_000,
    )
    provider_status_code = event_obj.get("provider_status_code")
    if (
        type(provider_status_code) is not int
        or not 100 <= provider_status_code <= 599
    ):
        provider_status_code = None
    add_event(
        "single_call_reply_decision",
        ts,
        lane=normalise_reply_lane(event_obj.get("lane")),
        target_id=(
            event_obj.get("target_id")
            if valid_string_public_post_id(event_obj.get("target_id"))
            else ""
        ),
        strategy_version=bounded_event_text(
            event_obj.get("strategy_version"),
            default="unavailable",
            max_characters=200,
        ),
        model=bounded_event_text(
            event_obj.get("model"),
            default="unavailable",
            max_characters=200,
        ),
        reasoning_effort=bounded_event_text(
            event_obj.get("reasoning_effort"), max_characters=50
        ),
        temperature=temperature,
        prompt_sha256=bounded_event_text(
            event_obj.get("prompt_sha256"), max_characters=64
        ),
        response_schema_sha256=bounded_event_text(
            event_obj.get("response_schema_sha256"), max_characters=64
        ),
        payload_sha256=bounded_event_text(
            event_obj.get("payload_sha256"), max_characters=64
        ),
        decision=bounded_event_text(
            event_obj.get("decision"), max_characters=50
        ),
        reply_kind=bounded_event_text(
            event_obj.get("reply_kind"), max_characters=100
        ),
        reason_code=bounded_event_text(
            event_obj.get("reason_code"), max_characters=100
        ),
        used_fact_count=bounded_event_nonnegative_integer(
            event_obj.get("used_fact_count"), maximum=32
        ),
        visible_turn_count=bounded_event_nonnegative_integer(
            event_obj.get("visible_turn_count"), maximum=12
        ),
        visible_character_count=bounded_event_nonnegative_integer(
            event_obj.get("visible_character_count"), maximum=12_000
        ),
        same_author_interaction_count=bounded_event_nonnegative_integer(
            event_obj.get("same_author_interaction_count"), maximum=8
        ),
        recent_conversational_reply_count=bounded_event_nonnegative_integer(
            event_obj.get(
                "recent_conversational_reply_count",
                event_obj.get("recent_reply_count"),
            ),
            maximum=30,
        ),
        trusted_fact_count=bounded_event_nonnegative_integer(
            event_obj.get("trusted_fact_count"), maximum=32
        ),
        supplied_image_count=bounded_event_nonnegative_integer(
            event_obj.get("supplied_image_count"), maximum=2
        ),
        model_call_count=bounded_event_nonnegative_integer(
            event_obj.get("model_call_count"), maximum=1
        ),
        local_validation_status=bounded_event_text(
            event_obj.get("local_validation_status"), max_characters=50
        ),
        error_category=bounded_event_text(
            event_obj.get("error_category"), max_characters=100
        ),
        failure_reason=bounded_event_text(
            event_obj.get("failure_reason"), max_characters=200
        ),
        outcome_type=bounded_event_text(
            event_obj.get("outcome_type"), max_characters=50
        ),
        pipeline_status=bounded_event_text(
            event_obj.get("pipeline_status"), max_characters=50
        ),
        provider_latency_ms=bounded_event_nonnegative_integer(
            event_obj.get("provider_latency_ms"), maximum=86_400_000
        ),
        provider_request_attempt_count=provider_request_attempt_count,
        provider_request_attempt_count_status=(
            provider_request_attempt_count_status
        ),
        provider_status_code=provider_status_code,
        provider_reset_epoch=bounded_event_nonnegative_integer(
            event_obj.get("provider_reset_epoch")
        ),
        provider_retry_after_seconds=bounded_event_nonnegative_integer(
            event_obj.get("provider_retry_after_seconds"),
            maximum=7 * 24 * 60 * 60,
        ),
        provider_response_id=bounded_event_text(
            event_obj.get("provider_response_id"), max_characters=300
        ),
    )


def record_single_call_reply_provider_usage(
    event_obj: Dict[str, Any],
    ts: datetime,
    *,
    add_event: Callable[..., Dict[str, Any]],
) -> None:
    """Project single-call provider usage fields and emit through the coordinator."""
    (
        request_attempt_count,
        request_attempt_count_status,
    ) = bounded_event_nonnegative_integer_observation(
        event_obj,
        "request_attempt_count",
        maximum=1_000_000,
    )
    add_event(
        "single_call_reply_provider_usage",
        ts,
        lane=normalise_reply_lane(event_obj.get("lane")),
        target_id=(
            event_obj.get("target_id")
            if valid_string_public_post_id(event_obj.get("target_id"))
            else ""
        ),
        strategy_version=bounded_event_text(
            event_obj.get("strategy_version"),
            default="unavailable",
            max_characters=200,
        ),
        model=bounded_event_text(
            event_obj.get("model"),
            default="unavailable",
            max_characters=200,
        ),
        provider_response_id=bounded_event_text(
            event_obj.get("provider_response_id"), max_characters=300
        ),
        provider_latency_ms=bounded_event_nonnegative_integer(
            event_obj.get("provider_latency_ms"), maximum=86_400_000
        ),
        request_attempt_count=request_attempt_count,
        request_attempt_count_status=request_attempt_count_status,
        input_tokens=bounded_event_nonnegative_integer(
            event_obj.get("input_tokens"), maximum=100_000_000
        ),
        cached_input_tokens=bounded_event_nonnegative_integer(
            event_obj.get("cached_input_tokens"), maximum=100_000_000
        ),
        cache_write_input_tokens=bounded_event_nonnegative_integer(
            event_obj.get("cache_write_input_tokens"), maximum=100_000_000
        ),
        output_tokens=bounded_event_nonnegative_integer(
            event_obj.get("output_tokens"), maximum=100_000_000
        ),
        reasoning_tokens=bounded_event_nonnegative_integer(
            event_obj.get("reasoning_tokens"), maximum=100_000_000
        ),
        total_tokens=bounded_event_nonnegative_integer(
            event_obj.get("total_tokens"), maximum=100_000_000
        ),
    )


def record_single_call_reply_posting_outcome(
    event_obj: Dict[str, Any],
    ts: datetime,
    *,
    add_event: Callable[..., Dict[str, Any]],
) -> None:
    """Project single-call posting outcome fields and emit through the coordinator."""
    add_event(
        "single_call_reply_posting_outcome",
        ts,
        status=bounded_event_text(
            event_obj.get("status"), default="unavailable", max_characters=100
        ),
        lane=normalise_reply_lane(event_obj.get("lane")),
        target_id=(
            event_obj.get("target_id")
            if valid_string_public_post_id(event_obj.get("target_id"))
            else ""
        ),
        reply_post_id=(
            event_obj.get("reply_post_id")
            if valid_string_public_post_id(event_obj.get("reply_post_id"))
            else ""
        ),
        strategy_version=bounded_event_text(
            event_obj.get("strategy_version"), max_characters=200
        ),
        reply_kind=bounded_event_text(
            event_obj.get("reply_kind"), max_characters=100
        ),
        reason_code=bounded_event_text(
            event_obj.get("reason_code"), max_characters=100
        ),
        used_fact_count=bounded_event_nonnegative_integer(
            event_obj.get("used_fact_count"), maximum=32
        ),
        supplied_image_count=bounded_event_nonnegative_integer(
            event_obj.get("supplied_image_count"), maximum=2
        ),
        model_call_count=bounded_event_nonnegative_integer(
            event_obj.get("model_call_count"), maximum=1
        ),
        validated_draft_hash=bounded_event_text(
            event_obj.get("validated_draft_hash"), max_characters=64
        ),
        failure_reason=bounded_event_text(
            event_obj.get("failure_reason"), max_characters=200
        ),
    )


def record_single_call_reply_draft_recovered(
    event_obj: Dict[str, Any],
    ts: datetime,
    *,
    add_event: Callable[..., Dict[str, Any]],
) -> None:
    """Project single-call draft recovered fields and emit through the coordinator."""
    add_event(
        "single_call_reply_draft_recovered",
        ts,
        lane=normalise_reply_lane(event_obj.get("lane")),
        target_id=(
            event_obj.get("target_id")
            if valid_string_public_post_id(event_obj.get("target_id"))
            else ""
        ),
        strategy_version=bounded_event_text(
            event_obj.get("strategy_version"), max_characters=200
        ),
        model=bounded_event_text(
            event_obj.get("model"), max_characters=200
        ),
        validated_draft_hash=bounded_event_text(
            event_obj.get("validated_draft_hash"), max_characters=64
        ),
        model_call_count=bounded_event_nonnegative_integer(
            event_obj.get("model_call_count"), maximum=1
        ),
    )
