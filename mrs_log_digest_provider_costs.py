"""Pure provider usage totals, cost attribution and currency presentation.

Reports consume supplied observations without mutating them or performing I/O.
Usage parsing, pending-call correlation and published-cost cache access remain
with their existing owners; shared reason classifiers come from values.
"""
from __future__ import annotations

from collections import Counter
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, List, Optional, Tuple

from mrs_log_digest_values import (
    _is_terminal_pipeline_failure,
    _is_writer_local_failure,
    _terminal_local_rejection_outcome,
    normalise_reply_lane,
)


USD_TICKS_PER_DOLLAR = 10_000_000_000
USD_DISPLAY_QUANTUM = Decimal("0.00000001")


def int_usage_value(value: Any) -> int:
    """Return the int usage value."""
    try:
        return int(value or 0)
    except Exception:
        return 0


def optional_int_usage_value(value: Any) -> Optional[int]:
    """Return a genuine integer usage value without turning missing data into zero."""
    if type(value) is not int or value < 0:
        return None
    return value


def format_usd_ticks(ticks: int, *, divisor: int = 1) -> str:
    """Render integer provider ticks as deterministic US dollars."""
    if divisor <= 0:
        raise ValueError("USD tick divisor must be positive")
    amount = (
        Decimal(int(ticks))
        / Decimal(divisor)
        / Decimal(USD_TICKS_PER_DOLLAR)
    )
    return f"US${amount.quantize(USD_DISPLAY_QUANTUM, rounding=ROUND_HALF_UP)}"


def format_reported_cost(row: Dict[str, Any]) -> str:
    """Render known provider cost without treating missing reports as zero."""
    costed = row.get("costed_successful_calls")
    if type(costed) is not int:
        successful = int(row.get("successful_usage_records", 0) or 0)
        uncosted = int(row.get("uncosted_successful_calls", 0) or 0)
        costed = max(0, successful - uncosted)
    if costed <= 0:
        return "unknown"
    return format_usd_ticks(int(row.get("known_cost_in_usd_ticks", 0) or 0))


def _cache_metric_coverage(
    events: List[Dict[str, Any]],
    metric_name: str,
) -> Dict[str, Any]:
    """Return explicit reporting coverage for one nullable cache metric."""

    def coverage_for_scope(
        scope_events: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        reported_values = [
            value
            for item in scope_events
            if (
                value := optional_int_usage_value(item.get(metric_name))
            ) is not None
        ]
        successful_call_count = len(scope_events)
        reporting_call_count = len(reported_values)
        missing_call_count = successful_call_count - reporting_call_count
        if successful_call_count and not missing_call_count:
            coverage_status = "complete"
        elif reporting_call_count:
            coverage_status = "partial"
        else:
            coverage_status = "unavailable"
        return {
            "coverage_status": coverage_status,
            "successful_call_count": successful_call_count,
            "reporting_call_count": reporting_call_count,
            "missing_call_count": missing_call_count,
            "reported_subtotal": (
                sum(reported_values) if reporting_call_count else None
            ),
        }

    coverage = coverage_for_scope(events)
    coverage["by_provider"] = {
        provider: coverage_for_scope([
            item
            for item in events
            if str(item.get("provider") or "xAI") == provider
        ])
        for provider in ("OpenAI", "xAI")
    }
    return coverage


def _format_cache_metric_coverage_line(
    metric_name: str,
    coverage: Dict[str, Any],
    *,
    provider: str = "",
) -> str:
    """Format one cache-metric total without overstating missing coverage."""
    successful_calls = int(coverage.get("successful_call_count", 0) or 0)
    reporting_calls = int(coverage.get("reporting_call_count", 0) or 0)
    call_noun = "call" if successful_calls == 1 else "calls"
    scope_noun = call_noun if provider else f"successful {call_noun}"
    prefix = f"{provider} " if provider else ""
    coverage_status = coverage.get("coverage_status")
    if coverage_status == "complete":
        return (
            f"{prefix}{metric_name} = {coverage.get('reported_subtotal')} "
            f"(complete coverage: {reporting_calls}/{successful_calls} "
            f"{scope_noun})"
        )
    if coverage_status == "partial":
        return (
            f"{prefix}{metric_name} = partial; reported subtotal "
            f"{coverage.get('reported_subtotal')} across "
            f"{reporting_calls}/{successful_calls} {scope_noun}"
        )
    return (
        f"{prefix}{metric_name} = unavailable "
        f"({reporting_calls}/{successful_calls} {scope_noun} reported the metric)"
    )


def xai_usage_totals(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return backward-compatible totals for all conversational providers."""
    reported_costs = [
        value
        for item in events
        if (value := optional_int_usage_value(item.get("cost_in_usd_ticks")))
        is not None
    ]
    provider_counts = Counter(
        str(item.get("provider") or "xAI") for item in events
    )
    cache_metric_coverage = {
        metric_name: _cache_metric_coverage(events, metric_name)
        for metric_name in (
            "cache_creation_input_tokens",
            "cache_write_input_tokens",
        )
    }
    cache_creation_coverage = cache_metric_coverage[
        "cache_creation_input_tokens"
    ]
    cache_write_coverage = cache_metric_coverage[
        "cache_write_input_tokens"
    ]
    return {
        "successful_provider_calls": len(events),
        "successful_xai_calls": provider_counts["xAI"],
        "successful_openai_calls": provider_counts["OpenAI"],
        "prompt_tokens": sum(int_usage_value(item.get("prompt_tokens")) for item in events),
        "cached_tokens": sum(int_usage_value(item.get("cached_tokens")) for item in events),
        "cache_read_input_tokens": sum(
            int_usage_value(
                item.get("cache_read_input_tokens", item.get("cached_tokens"))
            )
            for item in events
        ),
        "cache_creation_input_tokens": (
            cache_creation_coverage["reported_subtotal"]
            if cache_creation_coverage["coverage_status"] == "complete"
            else None
        ),
        "cache_write_input_tokens": (
            cache_write_coverage["reported_subtotal"]
            if cache_write_coverage["coverage_status"] == "complete"
            else None
        ),
        "cache_metric_coverage": cache_metric_coverage,
        "image_tokens": sum(int_usage_value(item.get("image_tokens")) for item in events),
        "reasoning_tokens": sum(int_usage_value(item.get("reasoning_tokens")) for item in events),
        "completion_tokens": sum(int_usage_value(item.get("completion_tokens")) for item in events),
        "total_tokens": sum(int_usage_value(item.get("total_tokens")) for item in events),
        "sources_used": sum(int_usage_value(item.get("num_sources_used")) for item in events),
        "cost_in_usd_ticks": sum(reported_costs),
        "costed_call_count": len(reported_costs),
        "uncosted_successful_call_count": len(events) - len(reported_costs),
    }


def _candidate_reply_disposition(
    decision: Optional[Dict[str, Any]],
    outcome: Optional[Dict[str, Any]],
    failure: Optional[Dict[str, Any]],
    local_rejection: Optional[Dict[str, Any]],
) -> str:
    """Select candidate disposition with eager classification and ordered precedence."""
    terminal_local_outcome = _terminal_local_rejection_outcome(
        (decision or {}).get("no_reply_reason")
    ) or _terminal_local_rejection_outcome(
        (local_rejection or {}).get("reason")
    )
    writer_local_failure = any(
        _is_writer_local_failure(value)
        for value in (
            (decision or {}).get("effective_reason"),
            (decision or {}).get("reason"),
            (decision or {}).get("no_reply_reason"),
            (local_rejection or {}).get("effective_reason"),
            (local_rejection or {}).get("reason"),
        )
    )
    outcome_status = str((outcome or {}).get("status") or "")
    decision_terminal_failure = _is_terminal_pipeline_failure(
        (decision or {}).get("reason")
        or (decision or {}).get("no_reply_reason"),
        (decision or {}).get("status"),
    )
    if outcome_status in {"confirmed", "posted"}:
        disposition = "published"
    elif outcome is not None and (
        "fail" in outcome_status or outcome_status not in {"", "confirmed"}
    ):
        disposition = "posting_failed"
    elif writer_local_failure:
        disposition = "writer_local_failure"
    elif terminal_local_outcome is not None:
        disposition = terminal_local_outcome
    elif (
        decision is not None
        and (
            decision.get("mode") == "no_reply"
            or decision.get("status") == "no_reply"
        )
        and not decision_terminal_failure
    ):
        disposition = "deliberately_declined"
    elif decision_terminal_failure or failure is not None:
        disposition = "pipeline_failed"
    elif decision is not None:
        disposition = "approved_not_confirmed_in_window"
    else:
        disposition = "outcome_unavailable"
    return disposition


def xai_reply_cost_summary(
    usage_events: List[Dict[str, Any]],
    reply_events: List[Dict[str, Any]],
    call_attempts: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Attribute logged conversational provider cost without inventing missing spend."""
    attempts = list(call_attempts or [])
    decisions: Dict[Tuple[str, str], Dict[str, Any]] = {}
    outcomes: Dict[Tuple[str, str], Dict[str, Any]] = {}
    failures: Dict[Tuple[str, str], Dict[str, Any]] = {}
    local_rejections: Dict[Tuple[str, str], Dict[str, Any]] = {}
    local_rejections_by_target: Dict[str, Dict[str, Any]] = {}
    execution_event_counts: Counter = Counter()
    for item in reply_events:
        target_id = str(item.get("target_id") or "")
        if not target_id:
            continue
        key = (normalise_reply_lane(item.get("lane")), target_id)
        kind = item.get("kind")
        if kind == "reply_strategy_decision":
            decisions[key] = item
            execution_event_counts[key] += 1
        elif kind == "reply_strategy_outcome":
            outcomes[key] = item
        elif kind == "reply_strategy_failure":
            failures[key] = item
            execution_event_counts[key] += 1
        elif kind == "reply_strategy_local_rejection":
            local_rejections[key] = item
            local_rejections_by_target[target_id] = item

    grouped_usage: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    unattributed_usage: List[Dict[str, Any]] = []
    for item in usage_events:
        lane = normalise_reply_lane(item.get("lane"))
        context_id = str(item.get("context_id") or "")
        if not context_id or lane == "unknown":
            unattributed_usage.append(item)
            continue
        grouped_usage.setdefault((lane, context_id), []).append(item)

    grouped_attempts: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    unattributed_attempts: List[Dict[str, Any]] = []
    for item in attempts:
        lane = normalise_reply_lane(item.get("lane"))
        context_id = str(item.get("context_id") or "")
        if not context_id or lane == "unknown":
            unattributed_attempts.append(item)
            continue
        grouped_attempts.setdefault((lane, context_id), []).append(item)

    event_keys_with_reported_calls = {
        key
        for mapping in (decisions, outcomes, failures)
        for key, item in mapping.items()
        if (optional_int_usage_value(item.get("model_call_count")) or 0) > 0
    }

    def candidate_first_time(key: Tuple[str, str]) -> str:
        rows = (
            grouped_usage.get(key, [])
            + grouped_attempts.get(key, [])
            + [
                item
                for item in (
                    decisions.get(key),
                    outcomes.get(key),
                    failures.get(key),
                )
                if item is not None
            ]
        )
        return min(
            [str(item.get("time") or "") for item in rows] or [""]
        )

    candidate_keys = sorted(
        set(grouped_usage)
        | set(grouped_attempts)
        | event_keys_with_reported_calls,
        key=lambda key: (candidate_first_time(key), key),
    )

    candidates: List[Dict[str, Any]] = []
    for key in candidate_keys:
        lane, context_id = key
        calls = grouped_usage.get(key, [])
        candidate_attempts = grouped_attempts.get(key, [])
        decision = decisions.get(key)
        outcome = outcomes.get(key)
        failure = failures.get(key)
        local_rejection = local_rejections.get(key) or local_rejections_by_target.get(
            context_id
        )
        disposition = _candidate_reply_disposition(
            decision, outcome, failure, local_rejection
        )

        reported_call_count: Optional[int] = None
        for source in (outcome, decision, failure):
            if source is None:
                continue
            value = optional_int_usage_value(source.get("model_call_count"))
            if value is not None:
                reported_call_count = value
                break

        observed_call_count = len(calls)
        started_call_count = len(candidate_attempts)
        unmatched_successful_call_count = sum(
            item.get("call_start_matched") is not True for item in calls
        )
        execution_event_count = execution_event_counts[key]
        if execution_event_count > 1:
            call_coverage = "multiple_pipeline_executions"
        elif unmatched_successful_call_count:
            call_coverage = "successful_usage_without_call_start"
        elif reported_call_count is None:
            call_coverage = "reported_call_count_unavailable"
        elif observed_call_count < reported_call_count:
            call_coverage = "successful_usage_missing"
        elif observed_call_count > reported_call_count:
            call_coverage = "unexpected_extra_usage"
        elif (
            started_call_count
            and (
                started_call_count != reported_call_count
                or any(
                    attempt.get("usage_observed") is not True
                    for attempt in candidate_attempts
                )
            )
        ):
            call_coverage = "call_start_usage_mismatch"
        else:
            call_coverage = "complete"

        reported_costs = [
            value
            for item in calls
            if (
                value := optional_int_usage_value(
                    item.get("cost_in_usd_ticks")
                )
            )
            is not None
        ]
        stage_counts = Counter(
            str(item.get("stage") or "unavailable") for item in calls
        )
        provider_counts = Counter(
            str(item.get("provider") or "xAI") for item in calls
        )
        candidates.append(
            {
                "lane": lane,
                "context_id": context_id,
                "outcome": disposition,
                "observed_successful_calls": observed_call_count,
                "started_calls": started_call_count,
                "unmatched_successful_calls": unmatched_successful_call_count,
                "reported_model_call_count": reported_call_count,
                "pipeline_execution_event_count": execution_event_count,
                "call_coverage": call_coverage,
                "stages": dict(sorted(stage_counts.items())),
                "providers": dict(sorted(provider_counts.items())),
                "total_tokens": sum(
                    int_usage_value(item.get("total_tokens")) for item in calls
                ),
                "known_cost_in_usd_ticks": sum(reported_costs),
                "costed_successful_calls": len(reported_costs),
                "uncosted_successful_calls": observed_call_count
                - len(reported_costs),
            }
        )

    stage_keys = sorted(
        {
            (
                str(item.get("provider") or "xAI"),
                str(item.get("stage") or "unavailable"),
            )
            for item in usage_events + attempts
        }
    )
    stages: List[Dict[str, Any]] = []
    for provider, stage in stage_keys:
        stage_usage = [
            item
            for item in usage_events
            if str(item.get("provider") or "xAI") == provider
            and str(item.get("stage") or "unavailable") == stage
        ]
        stage_attempts = [
            item
            for item in attempts
            if str(item.get("provider") or "xAI") == provider
            if str(item.get("stage") or "unavailable") == stage
        ]
        reported_costs = [
            value
            for item in stage_usage
            if (
                value := optional_int_usage_value(
                    item.get("cost_in_usd_ticks")
                )
            )
            is not None
        ]
        stages.append(
            {
                "provider": provider,
                "stage": stage,
                "started_calls": len(stage_attempts),
                "successful_usage_records": len(stage_usage),
                "call_starts_without_usage": sum(
                    1
                    for item in stage_attempts
                    if item.get("usage_observed") is not True
                ),
                "total_tokens": sum(
                    int_usage_value(item.get("total_tokens"))
                    for item in stage_usage
                ),
                "known_cost_in_usd_ticks": sum(reported_costs),
                "costed_successful_calls": len(reported_costs),
                "uncosted_successful_calls": len(stage_usage)
                - len(reported_costs),
            }
        )

    providers: List[Dict[str, Any]] = []
    provider_names = sorted(
        {str(item.get("provider") or "xAI") for item in usage_events + attempts}
    )
    for provider in provider_names:
        provider_usage = [
            item for item in usage_events
            if str(item.get("provider") or "xAI") == provider
        ]
        provider_attempts = [
            item for item in attempts
            if str(item.get("provider") or "xAI") == provider
        ]
        reported_costs = [
            value
            for item in provider_usage
            if (
                value := optional_int_usage_value(item.get("cost_in_usd_ticks"))
            ) is not None
        ]
        providers.append({
            "provider": provider,
            "started_calls": len(provider_attempts),
            "successful_usage_records": len(provider_usage),
            "call_starts_without_usage": sum(
                item.get("usage_observed") is not True for item in provider_attempts
            ),
            "total_tokens": sum(
                int_usage_value(item.get("total_tokens")) for item in provider_usage
            ),
            "known_cost_in_usd_ticks": sum(reported_costs),
            "costed_successful_calls": len(reported_costs),
            "uncosted_successful_calls": len(provider_usage) - len(reported_costs),
        })

    outcome_rows: List[Dict[str, Any]] = []
    for disposition in sorted(
        {str(item.get("outcome") or "outcome_unavailable") for item in candidates}
    ):
        rows = [item for item in candidates if item["outcome"] == disposition]
        outcome_rows.append(
            {
                "outcome": disposition,
                "candidate_count": len(rows),
                "observed_successful_calls": sum(
                    item["observed_successful_calls"] for item in rows
                ),
                "total_tokens": sum(item["total_tokens"] for item in rows),
                "known_cost_in_usd_ticks": sum(
                    item["known_cost_in_usd_ticks"] for item in rows
                ),
                "costed_successful_calls": sum(
                    item["costed_successful_calls"] for item in rows
                ),
                "uncosted_successful_calls": sum(
                    item["uncosted_successful_calls"] for item in rows
                ),
            }
        )

    totals = xai_usage_totals(usage_events)
    terminal_outcomes = {
        "published",
        "deliberately_declined",
        "posting_failed",
        "pipeline_failed",
        "terminal_repetition_rejection",
        "terminal_clarification_mode_rejection",
        "writer_local_failure",
    }
    coverage_reasons: List[str] = []
    if unattributed_usage:
        coverage_reasons.append("successful usage records lack candidate attribution")
    if unattributed_attempts:
        coverage_reasons.append("call starts lack candidate attribution")
    if any(
        item.get("call_start_matched") is not True for item in usage_events
    ):
        coverage_reasons.append(
            "one or more successful usage records lack a matching provider call start"
        )
    if totals["uncosted_successful_call_count"]:
        coverage_reasons.append("successful responses lack provider cost")
    if any(item["call_coverage"] != "complete" for item in candidates):
        coverage_reasons.append(
            "one or more candidate call histories are incomplete or ambiguous"
        )
    if any(item["outcome"] not in terminal_outcomes for item in candidates):
        coverage_reasons.append("one or more candidate outcomes are incomplete")
    coverage_complete = bool(candidates) and not coverage_reasons
    published_count = sum(
        1 for item in candidates if item["outcome"] == "published"
    )
    total_known_ticks = totals["cost_in_usd_ticks"]
    return {
        "usd_ticks_per_dollar": USD_TICKS_PER_DOLLAR,
        "coverage_complete": coverage_complete,
        "coverage_reasons": coverage_reasons,
        "candidate_count": len(candidates),
        "published_candidate_count": published_count,
        "unattributed_successful_call_count": len(unattributed_usage),
        "unattributed_call_start_count": len(unattributed_attempts),
        "unmatched_successful_call_count": sum(
            item.get("call_start_matched") is not True for item in usage_events
        ),
        "known_cost_in_usd_ticks": total_known_ticks,
        "known_cost_per_costed_call": (
            {
                "ticks": total_known_ticks,
                "divisor": totals["costed_call_count"],
            }
            if totals["costed_call_count"]
            else None
        ),
        "per_reviewed_candidate": (
            {"ticks": total_known_ticks, "divisor": len(candidates)}
            if coverage_complete and candidates
            else None
        ),
        "effective_per_published_reply": (
            {"ticks": total_known_ticks, "divisor": published_count}
            if coverage_complete and published_count
            else None
        ),
        "candidates": candidates,
        "outcomes": outcome_rows,
        "providers": providers,
        "stages": stages,
    }
