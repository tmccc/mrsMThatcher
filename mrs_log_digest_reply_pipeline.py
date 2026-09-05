"""Legacy reply-pipeline observations, reconciliation and majority-review reports.

Handlers project parsed payloads through current coordinator callbacks. Effective
outcomes mutate supplied event dictionaries in place; summaries only read them.
Parsing, dispatch, event insertion and provenance remain with the coordinator.
There is no runtime I/O or state shared between analyses.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Tuple

from mrs_log_digest_values import (
    _normalise_lane,
    bounded_event_boolean,
    bounded_event_nonnegative_integer,
    bounded_event_text,
    valid_string_public_post_id,
)


MAJORITY_REVIEW_FAMILIES = (
    "reply_necessity",
    "allegation_review",
    "authentication_review",
)


MAJORITY_REVIEW_SUMMARY_FIELDS = (
    "family",
    "reviewer_calls_attempted",
    "valid_votes_obtained",
    "first_two_valid_votes_agreed",
    "reviewer_3_called",
    "reviewer_3_skipped_first_two_agreement",
)


def _valid_majority_review_summary(value: Any) -> Optional[Dict[str, Any]]:
    """Return one strict, allow-listed majority-review summary, if valid."""
    if (
        not isinstance(value, dict)
        or set(value) != set(MAJORITY_REVIEW_SUMMARY_FIELDS)
    ):
        return None
    family = value.get("family")
    calls = value.get("reviewer_calls_attempted")
    valid_votes = value.get("valid_votes_obtained")
    first_two_agreed = value.get("first_two_valid_votes_agreed")
    reviewer_3_called = value.get("reviewer_3_called")
    reviewer_3_skipped = value.get(
        "reviewer_3_skipped_first_two_agreement"
    )
    if (
        type(family) is not str
        or family not in MAJORITY_REVIEW_FAMILIES
        or type(calls) is not int
        or calls not in {2, 3}
        or type(valid_votes) is not int
        or not 0 <= valid_votes <= calls
        or type(first_two_agreed) is not bool
        or type(reviewer_3_called) is not bool
        or type(reviewer_3_skipped) is not bool
        or reviewer_3_called != (calls == 3)
    ):
        return None
    if calls == 2 and not (
        valid_votes == 2
        and first_two_agreed is True
        and reviewer_3_called is False
        and reviewer_3_skipped is True
    ):
        return None
    if calls == 3 and not (
        first_two_agreed is False
        and reviewer_3_called is True
        and reviewer_3_skipped is False
    ):
        return None
    return {field: value[field] for field in MAJORITY_REVIEW_SUMMARY_FIELDS}


def normalise_majority_review_telemetry(
    event: Dict[str, Any],
) -> Dict[str, Any]:
    """Validate majority-review telemetry without retaining malformed content."""
    present = "majority_review_summaries" in event
    result = {
        "present": present,
        "present_empty": False,
        "valid_entries": [],
        "malformed_entry_count": 0,
        "duplicate_family": False,
    }
    if not present:
        return result
    raw = event.get("majority_review_summaries")
    if not isinstance(raw, list):
        result["malformed_entry_count"] = 1
        return result
    result["present_empty"] = not raw

    family_occurrences = Counter(
        item.get("family")
        for item in raw
        if isinstance(item, dict)
        and type(item.get("family")) is str
        and item.get("family") in MAJORITY_REVIEW_FAMILIES
    )
    duplicate_families = {
        family for family, count in family_occurrences.items() if count > 1
    }
    result["duplicate_family"] = bool(duplicate_families)
    for item in raw:
        validated = _valid_majority_review_summary(item)
        if validated is None or validated["family"] in duplicate_families:
            result["malformed_entry_count"] += 1
            continue
        result["valid_entries"].append(validated)
    return result


def _majority_review_telemetry_for_event(
    event: Dict[str, Any],
) -> Dict[str, Any]:
    """Read raw test events or the digest parser's already-sanitised form."""
    parsed_presence = event.get("majority_review_telemetry_present")
    if type(parsed_presence) is not bool:
        return normalise_majority_review_telemetry(event)
    if not parsed_presence:
        return normalise_majority_review_telemetry({})

    validated = normalise_majority_review_telemetry({
        "majority_review_summaries": event.get("majority_review_summaries", []),
    })
    stored_malformed = event.get("majority_review_malformed_entry_count")
    if type(stored_malformed) is int and stored_malformed >= 0:
        validated["malformed_entry_count"] += stored_malformed
    stored_empty = event.get("majority_review_telemetry_present_empty")
    if type(stored_empty) is bool:
        validated["present_empty"] = stored_empty
    if event.get("majority_review_duplicate_family") is True:
        validated["duplicate_family"] = True
    return validated


def _majority_review_utilisation_counts(
    entries: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Aggregate reviewer-call savings for validated family resolutions."""
    completed = len(entries)
    two_call = sum(item["reviewer_calls_attempted"] == 2 for item in entries)
    three_call = sum(item["reviewer_calls_attempted"] == 3 for item in entries)
    calls_attempted = sum(item["reviewer_calls_attempted"] for item in entries)
    valid_votes = sum(item["valid_votes_obtained"] for item in entries)
    invalid_votes = calls_attempted - valid_votes
    baseline = completed * 3
    calls_saved = baseline - calls_attempted
    return {
        "completed_family_resolutions": completed,
        "two_call_resolutions": two_call,
        "three_call_resolutions": three_call,
        "first_two_agreement_resolutions": sum(
            item["first_two_valid_votes_agreed"] is True for item in entries
        ),
        "reviewer_3_calls": sum(
            item["reviewer_3_called"] is True for item in entries
        ),
        "reviewer_3_skips_due_to_matching_first_two_votes": sum(
            item["reviewer_3_skipped_first_two_agreement"] is True
            for item in entries
        ),
        "actual_reviewer_calls_attempted": calls_attempted,
        "fixed_three_call_baseline": baseline,
        "reviewer_calls_saved": calls_saved,
        "reviewer_call_reduction_percentage": (
            calls_saved / baseline * 100.0 if baseline else None
        ),
        "short_circuit_rate_percentage": (
            two_call / completed * 100.0 if completed else None
        ),
        "valid_votes_obtained": valid_votes,
        "total_invalid_or_unusable_votes": invalid_votes,
        "family_resolutions_with_invalid_or_unusable_votes": sum(
            item["valid_votes_obtained"] < item["reviewer_calls_attempted"]
            for item in entries
        ),
        "three_call_resolutions_with_all_three_votes_valid": sum(
            item["reviewer_calls_attempted"] == 3
            and item["valid_votes_obtained"] == 3
            for item in entries
        ),
        "three_call_resolutions_with_invalid_or_unusable_votes": sum(
            item["reviewer_calls_attempted"] == 3
            and item["valid_votes_obtained"] < 3
            for item in entries
        ),
    }


def majority_review_utilisation(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return safe coverage, overall, and per-family majority utilisation."""
    entries: List[Dict[str, Any]] = []
    present_events = 0
    absent_events = 0
    empty_events = 0
    malformed_entries = 0
    duplicate_events = 0
    for row in rows:
        telemetry = _majority_review_telemetry_for_event(row)
        if telemetry["present"]:
            present_events += 1
            empty_events += int(telemetry["present_empty"])
        else:
            absent_events += 1
        malformed_entries += int(telemetry["malformed_entry_count"])
        duplicate_events += int(telemetry["duplicate_family"])
        entries.extend(telemetry["valid_entries"])

    per_family = {
        family: _majority_review_utilisation_counts([
            item for item in entries if item["family"] == family
        ])
        for family in MAJORITY_REVIEW_FAMILIES
    }
    return {
        "coverage": {
            "stage_summary_events_examined": len(rows),
            "events_with_majority_review_summaries": present_events,
            "events_without_majority_review_summaries": absent_events,
            "events_with_empty_majority_review_summaries": empty_events,
            "valid_majority_family_entries": len(entries),
            "malformed_entries": malformed_entries,
            "events_with_duplicate_family_entries": duplicate_events,
        },
        "overall": _majority_review_utilisation_counts(entries),
        "per_family": per_family,
    }


def reply_pipeline_stage_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate safe tested-pipeline stage telemetry across evaluations."""
    def tested_version(row: Dict[str, Any]) -> bool:
        return str(row.get("strategy_version") or "").startswith(
            "tested-reply-pipeline-"
        )

    rows = [
        event for event in events
        if event.get("kind") == "reply_pipeline_stage_summary"
        and tested_version(event)
    ]
    decisions = [
        event for event in events
        if event.get("kind") == "reply_strategy_decision"
        and tested_version(event)
    ]

    def identity(row: Dict[str, Any], index: int) -> Tuple[str, str, str]:
        version = str(row.get("strategy_version") or "unavailable")
        lane = _normalise_lane(row.get("lane"))
        target = str(row.get("target_id") or "")
        return (
            (version, lane, target)
            if target
            else (version, lane, f"missing:{index}:{row.get('time')}")
        )

    decision_ids = {identity(row, index) for index, row in enumerate(decisions)}
    stage_ids = {identity(row, index) for index, row in enumerate(rows)}
    complete_ids = decision_ids & stage_ids
    versions = sorted({
        str(row.get("strategy_version")) for row in [*decisions, *rows]
    })
    latest_version = versions[-1] if versions else None
    by_version: Dict[str, Dict[str, int]] = {}
    for version in versions:
        version_decisions = {
            item for item in decision_ids if item[0] == version
        }
        version_stages = {item for item in stage_ids if item[0] == version}
        version_complete = version_decisions & version_stages
        by_version[version] = {
            "decision_count": len(version_decisions),
            "stage_summary_count": sum(
                str(row.get("strategy_version")) == version for row in rows
            ),
            "complete_stage_telemetry_count": len(version_complete),
            "partial_or_legacy_telemetry_count": len(
                version_decisions - version_complete
            ),
        }

    def value_counts(field: str) -> Dict[str, int]:
        return dict(sorted(Counter(
            str(row[field]) for row in rows if row.get(field) not in {None, ""}
        ).items()))

    def list_counts(field: str) -> Dict[str, int]:
        return dict(sorted(Counter(
            str(value)
            for row in rows
            for value in (row.get(field) if isinstance(row.get(field), list) else [])
            if str(value)
        ).items()))

    def majority_resolution_counts(
        outcome_field: str,
        resolvable_field: str,
    ) -> Dict[str, int]:
        return dict(sorted(Counter(
            "true" if row[resolvable_field] else "false"
            for row in rows
            if row.get(outcome_field) not in {None, ""}
            and type(row.get(resolvable_field)) is bool
        ).items()))

    provider_calls: Counter = Counter()
    invalid_stages: Counter = Counter()
    claim_audit_outcomes: Counter = Counter()
    for row in rows:
        counts = row.get("provider_call_counts")
        if isinstance(counts, dict):
            for provider in ("xAI", "OpenAI"):
                count = counts.get(provider)
                if type(count) is int and count >= 0:
                    provider_calls[provider] += count
        for stage in row.get("schema_invalid_stages") or []:
            invalid_stages[str(stage)] += 1
        for item in row.get("claim_audit_outcomes") or []:
            if isinstance(item, dict) and item.get("outcome"):
                claim_audit_outcomes[str(item["outcome"])] += 1

    reply_required = {
        "require_claim_free_reply",
        "require_supported_factual_reply",
    }
    return {
        "evaluation_count": len(rows),
        "all_stage_summary_event_count": len(rows),
        "majority_review_utilisation": majority_review_utilisation(rows),
        "tested_pipeline_decision_count": len(decision_ids),
        "complete_stage_telemetry_count": len(complete_ids),
        "partial_or_legacy_telemetry_count": len(decision_ids - complete_ids),
        "strategy_version_counts": by_version,
        "latest_strategy_version": latest_version,
        "latest_strategy_version_decision_count": (
            by_version.get(latest_version, {}).get("decision_count", 0)
            if latest_version
            else 0
        ),
        "latest_strategy_version_stage_summary_count": (
            by_version.get(latest_version, {}).get("stage_summary_count", 0)
            if latest_version
            else 0
        ),
        "provider_call_counts": dict(sorted(provider_calls.items())),
        "schema_invalid_call_count": sum(invalid_stages.values()),
        "schema_invalid_stage_counts": dict(sorted(invalid_stages.items())),
        "deterministic_suppression_count": sum(
            row.get("deterministic_suppressed") is True for row in rows
        ),
        "deterministic_suppression_reason_counts": value_counts("deterministic_reason"),
        "gate_decision_counts": value_counts("xai_gate_decision"),
        "reply_necessity_review_count": sum(
            row.get("reply_necessity_outcome") not in {None, ""} for row in rows
        ),
        "reply_necessity_outcome_counts": value_counts("reply_necessity_outcome"),
        "reply_necessity_majority_resolvable_counts": majority_resolution_counts(
            "reply_necessity_outcome",
            "reply_necessity_majority_resolvable",
        ),
        "reply_necessity_overturn_count": sum(
            row.get("xai_gate_decision") == "no_reply"
            and row.get("reply_necessity_outcome") in reply_required
            for row in rows
        ),
        "reply_necessity_invalid_call_count": sum(
            int(row.get("reply_necessity_invalid_calls") or 0) for row in rows
        ),
        "group_hostility_candidate_count": sum(
            row.get("group_hostility_candidate") is True for row in rows
        ),
        "group_hostility_review_count": sum(
            row.get("group_hostility_outcome") not in {None, ""} for row in rows
        ),
        "group_hostility_outcome_counts": value_counts("group_hostility_outcome"),
        "group_hostility_suppression_count": sum(
            row.get("group_hostility_outcome") == "suppress_group_hostility"
            for row in rows
        ),
        "allegation_conspiracy_candidate_count": sum(
            row.get("allegation_conspiracy_candidate") is True for row in rows
        ),
        "allegation_conspiracy_review_count": sum(
            row.get("allegation_conspiracy_outcome") not in {None, ""} for row in rows
        ),
        "allegation_conspiracy_category_counts": list_counts(
            "allegation_conspiracy_categories"
        ),
        "allegation_conspiracy_outcome_counts": value_counts(
            "allegation_conspiracy_outcome"
        ),
        "allegation_conspiracy_suppression_count": sum(
            row.get("allegation_conspiracy_outcome") in {
                "confirm_no_reply",
                "confirm_no_reply_spam_or_abuse",
            }
            for row in rows
        ),
        "allegation_conspiracy_majority_resolvable_counts": (
            majority_resolution_counts(
                "allegation_conspiracy_outcome",
                "allegation_conspiracy_majority_resolvable",
            )
        ),
        "allegation_conspiracy_invalid_call_count": sum(
            int(row.get("allegation_conspiracy_invalid_calls") or 0)
            for row in rows
        ),
        "attribution_route_counts": value_counts("attribution_route"),
        "authentication_review_count": sum(
            row.get("authentication_outcome") not in {None, ""} for row in rows
        ),
        "authentication_outcome_counts": value_counts("authentication_outcome"),
        "claim_risk_evaluation_count": sum(
            bool(row.get("claim_risk_categories")) for row in rows
        ),
        "claim_risk_category_counts": list_counts("claim_risk_categories"),
        "claim_audit_outcome_counts": dict(sorted(claim_audit_outcomes.items())),
        "claim_cleanup_count": sum(row.get("claim_cleanup_called") is True for row in rows),
        "exact_duplicate_count": sum(
            row.get("exact_duplicate_detected") is True for row in rows
        ),
        "near_duplicate_candidate_count": sum(
            type(row.get("near_duplicate_count")) is int
            and row.get("near_duplicate_count", 0) > 0
            for row in rows
        ),
        "duplicate_repair_count": sum(
            row.get("duplicate_repair_called") is True for row in rows
        ),
        "duplicate_repair_outcome_counts": value_counts("duplicate_repair_outcome"),
        "final_validation_counts": value_counts("final_validation"),
    }


def record_ai_reply_pipeline_decision(
    event_obj: Dict[str, Any],
    ts: datetime,
    *,
    add_event: Callable[..., Dict[str, Any]],
    add_or_merge_local_rejection: Callable[..., Dict[str, Any]],
    conversational_evidence_fields: Callable[..., Dict[str, Any]],
    bounded_event_string_list: Callable[..., List[str]],
) -> None:
    """Project ai_reply_pipeline_decision through the current coordinator callbacks."""
    evidence_ids = event_obj.get("evidence_ids")
    factual_claim_count = event_obj.get("factual_claim_count")
    evidence_fields = conversational_evidence_fields(
        event_obj,
        evidence_ids=evidence_ids,
        factual_claim_count=factual_claim_count,
    )
    decision_status = bounded_event_text(
        event_obj.get("status"), default="", max_characters=100
    )
    final_reply_kind = bounded_event_text(
        event_obj.get("final_reply_kind"), max_characters=100
    )
    effective_mode = (
        bounded_event_text(
            event_obj.get("mode"), max_characters=100
        )
        or ("no_reply" if decision_status == "no_reply" else None)
    )
    add_event(
        "reply_strategy_decision",
        ts,
        lane=bounded_event_text(event_obj.get("lane"), default="unavailable", max_characters=100),
        target_id=(event_obj.get("target_id") if valid_string_public_post_id(event_obj.get("target_id")) else ""),
        strategy_version=bounded_event_text(event_obj.get("strategy_version"), default="unavailable", max_characters=200),
        status=decision_status or "unavailable",
        mode=effective_mode,
        proposer_mode=bounded_event_text(event_obj.get("proposer_mode"), max_characters=100) or effective_mode,
        final_reply_kind=final_reply_kind,
        reply_requirement=bounded_event_text(event_obj.get("reply_requirement"), max_characters=100),
        route_source=bounded_event_text(event_obj.get("route_source"), max_characters=100),
        claim_risk_categories=bounded_event_string_list(
            event_obj.get("claim_risk_categories")
        ),
        humour_tone=bounded_event_text(event_obj.get("tone"), max_characters=100),
        tone=bounded_event_text(event_obj.get("tone"), max_characters=100),
        **evidence_fields,
        no_reply_reason=bounded_event_text(event_obj.get("reason"), max_characters=500),
        reason=bounded_event_text(event_obj.get("reason"), max_characters=500),
        reviewer_verdict=bounded_event_text(event_obj.get("reviewer_verdict"), max_characters=100),
        model_call_count=bounded_event_nonnegative_integer(event_obj.get("model_call_count"), maximum=1000),
        revision_count=bounded_event_nonnegative_integer(event_obj.get("revision_count"), maximum=1000),
        author_quarantine_evidence=bounded_event_text(event_obj.get("author_quarantine_evidence"), max_characters=200),
        pipeline_stage_status=(
            bounded_event_text(event_obj.get("pipeline_stage_status"), max_characters=100)
            or decision_status
            or "unavailable"
        ),
        effective_status=bounded_event_text(event_obj.get("effective_status"), max_characters=100),
        effective_reason=bounded_event_text(event_obj.get("effective_reason"), max_characters=500),
        original_local_rejection_reason=bounded_event_text(event_obj.get("original_local_rejection_reason"), max_characters=500),
        direct_answer_repair_attempted=bounded_event_boolean(event_obj.get("direct_answer_repair_attempted")),
        direct_answer_repair_outcome=bounded_event_text(event_obj.get("direct_answer_repair_outcome"), max_characters=100),
        incoming_contribution=bounded_event_text(event_obj.get("incoming_contribution"), max_characters=25_000),
        proposed_draft=bounded_event_text(event_obj.get("proposed_draft"), max_characters=25_000),
        repaired_draft=bounded_event_text(event_obj.get("repaired_draft"), max_characters=25_000),
    )
    if event_obj.get("effective_status") == "local_rejection":
        add_or_merge_local_rejection(
            ts,
            lane=event_obj.get("lane") or "unavailable",
            target_id=event_obj.get("target_id") or "",
            strategy_version=event_obj.get("strategy_version") or "unavailable",
            pipeline_stage_status=(
                event_obj.get("pipeline_stage_status")
                or decision_status
                or "unavailable"
            ),
            effective_status="local_rejection",
            effective_reason=event_obj.get("effective_reason") or "",
            reason=(
                event_obj.get("original_local_rejection_reason")
                or "clarification_not_direct_factual_answer"
            ),
            original_local_rejection_reason=event_obj.get(
                "original_local_rejection_reason"
            ),
            direct_answer_repair_attempted=event_obj.get(
                "direct_answer_repair_attempted"
            ),
            direct_answer_repair_outcome=event_obj.get(
                "direct_answer_repair_outcome"
            ),
            incoming_contribution=event_obj.get(
                "incoming_contribution"
            ),
            proposed_draft=event_obj.get("proposed_draft"),
            repaired_draft=event_obj.get("repaired_draft"),
        )


def record_ai_reply_pipeline_stage_summary(
    event_obj: Dict[str, Any],
    ts: datetime,
    *,
    add_event: Callable[..., Dict[str, Any]],
    bounded_event_string_list: Callable[..., List[str]],
    normalise_majority_review_telemetry: Callable[[Dict[str, Any]], Dict[str, Any]],
) -> None:
    """Project ai_reply_pipeline_stage_summary through the current coordinator callbacks."""
    raw_provider_counts = event_obj.get("provider_call_counts")
    provider_call_counts = {
        provider: count
        for provider in ("xAI", "OpenAI")
        if isinstance(raw_provider_counts, dict)
        and type(count := raw_provider_counts.get(provider)) is int
        and 0 <= count <= 1_000_000
    }
    schema_invalid_stages = event_obj.get("schema_invalid_stages")
    allegation_categories = event_obj.get(
        "allegation_conspiracy_categories"
    )
    claim_risk_categories = event_obj.get("claim_risk_categories")
    raw_claim_outcomes = event_obj.get("claim_audit_outcomes")
    claim_audit_outcomes = [
        {
            "stage": stage,
            "outcome": outcome,
        }
        for item in list(
            raw_claim_outcomes
            if isinstance(raw_claim_outcomes, list)
            else []
        )[:100]
        if isinstance(item, dict)
        and (
            stage := bounded_event_text(
                item.get("stage"), max_characters=100
            )
        )
        and (
            outcome := bounded_event_text(
                item.get("outcome"), max_characters=100
            )
        )
    ]
    majority_review = normalise_majority_review_telemetry(
        event_obj
    )
    add_event(
        "reply_pipeline_stage_summary",
        ts,
        lane=bounded_event_text(event_obj.get("lane"), default="unavailable", max_characters=100),
        target_id=(event_obj.get("target_id") if valid_string_public_post_id(event_obj.get("target_id")) else ""),
        strategy_version=bounded_event_text(event_obj.get("strategy_version"), default="unavailable", max_characters=200),
        status=bounded_event_text(event_obj.get("status"), default="unavailable", max_characters=100),
        pipeline_stage_status=(
            bounded_event_text(event_obj.get("pipeline_stage_status"), max_characters=100)
            or bounded_event_text(event_obj.get("status"), max_characters=100)
            or "unavailable"
        ),
        pipeline_stage_reason=(
            bounded_event_text(event_obj.get("terminal_reason"), default="", max_characters=500)
        ),
        terminal_reason=bounded_event_text(event_obj.get("terminal_reason"), default="", max_characters=500),
        effective_status=bounded_event_text(event_obj.get("effective_status"), max_characters=100),
        effective_reason=bounded_event_text(event_obj.get("effective_reason"), max_characters=500),
        original_local_rejection_reason=bounded_event_text(event_obj.get("original_local_rejection_reason"), max_characters=500),
        direct_answer_repair_attempted=bounded_event_boolean(event_obj.get("direct_answer_repair_attempted")),
        direct_answer_repair_outcome=bounded_event_text(event_obj.get("direct_answer_repair_outcome"), max_characters=100),
        model_call_count=bounded_event_nonnegative_integer(event_obj.get("model_call_count"), maximum=1000),
        revision_count=bounded_event_nonnegative_integer(event_obj.get("revision_count"), maximum=1000),
        provider_call_counts=provider_call_counts,
        majority_review_telemetry_present=majority_review[
            "present"
        ],
        majority_review_telemetry_present_empty=majority_review[
            "present_empty"
        ],
        majority_review_summaries=(
            majority_review["valid_entries"]
            if majority_review["present"]
            else None
        ),
        majority_review_malformed_entry_count=majority_review[
            "malformed_entry_count"
        ],
        majority_review_duplicate_family=majority_review[
            "duplicate_family"
        ],
        reply_requirement=bounded_event_text(event_obj.get("reply_requirement"), max_characters=100),
        route_source=bounded_event_text(event_obj.get("route_source"), max_characters=100),
        trusted_facts_supplied_count=bounded_event_nonnegative_integer(event_obj.get("trusted_facts_supplied_count"), maximum=1_000_000),
        trusted_fact_ids_supplied=bounded_event_string_list(event_obj.get("trusted_fact_ids_supplied"), limit=1000, item_max_characters=200),
        schema_invalid_stages=bounded_event_string_list(schema_invalid_stages),
        deterministic_suppressed=bounded_event_boolean(event_obj.get("deterministic_suppressed")),
        deterministic_reason=bounded_event_text(event_obj.get("deterministic_reason"), max_characters=500),
        xai_gate_decision=bounded_event_text(event_obj.get("xai_gate_decision"), max_characters=100),
        reply_necessity_outcome=bounded_event_text(event_obj.get("reply_necessity_outcome"), max_characters=100),
        reply_necessity_majority_resolvable=(
            event_obj.get("reply_necessity_majority_resolvable")
            if type(
                event_obj.get("reply_necessity_majority_resolvable")
            ) is bool
            else None
        ),
        reply_necessity_invalid_calls=(
            event_obj.get("reply_necessity_invalid_calls")
            if type(event_obj.get("reply_necessity_invalid_calls")) is int
            and event_obj.get("reply_necessity_invalid_calls") >= 0
            else 0
        ),
        group_hostility_candidate=bounded_event_boolean(event_obj.get("group_hostility_candidate")),
        group_hostility_outcome=bounded_event_text(event_obj.get("group_hostility_outcome"), max_characters=100),
        allegation_conspiracy_candidate=bounded_event_boolean(event_obj.get("allegation_conspiracy_candidate")),
        allegation_conspiracy_categories=bounded_event_string_list(allegation_categories),
        allegation_conspiracy_outcome=bounded_event_text(event_obj.get("allegation_conspiracy_outcome"), max_characters=100),
        allegation_conspiracy_majority_resolvable=(
            event_obj.get(
                "allegation_conspiracy_majority_resolvable"
            )
            if type(
                event_obj.get(
                    "allegation_conspiracy_majority_resolvable"
                )
            ) is bool
            else None
        ),
        allegation_conspiracy_invalid_calls=(
            event_obj.get("allegation_conspiracy_invalid_calls")
            if type(event_obj.get("allegation_conspiracy_invalid_calls")) is int
            and event_obj.get("allegation_conspiracy_invalid_calls") >= 0
            else 0
        ),
        attribution_route=bounded_event_text(event_obj.get("attribution_route"), max_characters=100),
        attribution_reply_requirement=bounded_event_text(event_obj.get("attribution_reply_requirement"), max_characters=100),
        authentication_outcome=bounded_event_text(event_obj.get("authentication_outcome"), max_characters=100),
        claim_risk_categories=bounded_event_string_list(claim_risk_categories),
        claim_audit_outcomes=claim_audit_outcomes,
        claim_cleanup_called=bounded_event_boolean(event_obj.get("claim_cleanup_called")),
        exact_duplicate_detected=bounded_event_boolean(event_obj.get("exact_duplicate_detected")),
        near_duplicate_count=(
            event_obj.get("near_duplicate_count")
            if type(event_obj.get("near_duplicate_count")) is int
            and event_obj.get("near_duplicate_count") >= 0
            else None
        ),
        duplicate_repair_called=bounded_event_boolean(event_obj.get("duplicate_repair_called")),
        duplicate_repair_outcome=bounded_event_text(event_obj.get("duplicate_repair_outcome"), max_characters=100),
        final_validation=bounded_event_text(event_obj.get("final_validation"), max_characters=100),
    )


def record_ai_reply_pipeline_effective_outcome(
    event_obj: Dict[str, Any],
    ts: datetime,
    *,
    add_or_merge_local_rejection: Callable[..., Dict[str, Any]],
) -> None:
    """Project ai_reply_pipeline_effective_outcome through the current coordinator callbacks."""
    if event_obj.get("effective_status") == "local_rejection":
        add_or_merge_local_rejection(
            ts,
            lane=event_obj.get("lane") or "unavailable",
            target_id=event_obj.get("target_id") or "",
            strategy_version=event_obj.get("strategy_version") or "unavailable",
            pipeline_stage_status=(
                event_obj.get("pipeline_stage_status") or "unavailable"
            ),
            effective_status="local_rejection",
            effective_reason=event_obj.get("effective_reason") or "",
            reason=(
                event_obj.get("original_local_rejection_reason")
                or event_obj.get("effective_reason")
                or "clarification_not_direct_factual_answer"
            ),
            original_local_rejection_reason=event_obj.get(
                "original_local_rejection_reason"
            ),
            direct_answer_repair_attempted=event_obj.get(
                "direct_answer_repair_attempted"
            ),
            direct_answer_repair_outcome=event_obj.get(
                "direct_answer_repair_outcome"
            ),
            incoming_contribution=event_obj.get(
                "incoming_contribution"
            ),
            proposed_draft=event_obj.get("proposed_draft"),
            repaired_draft=event_obj.get("repaired_draft"),
        )


def record_ai_reply_pipeline_failure(
    event_obj: Dict[str, Any],
    ts: datetime,
    *,
    add_event: Callable[..., Dict[str, Any]],
) -> None:
    """Project ai_reply_pipeline_failure through the current coordinator callbacks."""
    add_event(
        "reply_strategy_failure",
        ts,
        status=bounded_event_text(event_obj.get("status"), default="operational_failure", max_characters=100),
        lane=bounded_event_text(event_obj.get("lane"), default="unavailable", max_characters=100),
        target_id=(event_obj.get("target_id") if valid_string_public_post_id(event_obj.get("target_id")) else ""),
        strategy_version=bounded_event_text(event_obj.get("strategy_version"), default="unavailable", max_characters=200),
        reason=bounded_event_text(event_obj.get("reason"), default="unknown_pipeline_failure", max_characters=1000),
        model_call_count=bounded_event_nonnegative_integer(event_obj.get("model_call_count"), maximum=1000),
        revision_count=bounded_event_nonnegative_integer(event_obj.get("revision_count"), maximum=1000),
        author_quarantine_evidence=bounded_event_text(event_obj.get("author_quarantine_evidence"), max_characters=200),
    )


def record_ai_reply_pipeline_outcome(
    event_obj: Dict[str, Any],
    ts: datetime,
    *,
    add_event: Callable[..., Dict[str, Any]],
    conversational_evidence_fields: Callable[..., Dict[str, Any]],
    bounded_event_string_list: Callable[..., List[str]],
) -> None:
    """Project ai_reply_pipeline_outcome through the current coordinator callbacks."""
    evidence_ids = event_obj.get("evidence_ids")
    factual_claim_count = event_obj.get("factual_claim_count")
    evidence_fields = conversational_evidence_fields(
        event_obj,
        evidence_ids=evidence_ids,
        factual_claim_count=factual_claim_count,
    )
    add_event(
        "reply_strategy_outcome",
        ts,
        status=bounded_event_text(event_obj.get("status"), default="confirmed", max_characters=100),
        lane=bounded_event_text(event_obj.get("lane"), default="unavailable", max_characters=100),
        target_id=(event_obj.get("target_id") if valid_string_public_post_id(event_obj.get("target_id")) else ""),
        reply_post_id=(event_obj.get("reply_post_id") if valid_string_public_post_id(event_obj.get("reply_post_id")) else ""),
        strategy_version=bounded_event_text(event_obj.get("strategy_version"), default="unavailable", max_characters=200),
        mode=bounded_event_text(event_obj.get("mode"), max_characters=100),
        final_reply_kind=bounded_event_text(event_obj.get("final_reply_kind"), max_characters=100),
        reply_requirement=bounded_event_text(event_obj.get("reply_requirement"), max_characters=100),
        route_source=bounded_event_text(event_obj.get("route_source"), max_characters=100),
        claim_risk_categories=bounded_event_string_list(event_obj.get("claim_risk_categories")),
        humour_tone=bounded_event_text(event_obj.get("tone"), max_characters=100),
        tone=bounded_event_text(event_obj.get("tone"), max_characters=100),
        **evidence_fields,
        reviewer_verdict=bounded_event_text(event_obj.get("reviewer_verdict"), max_characters=100),
        model_call_count=bounded_event_nonnegative_integer(event_obj.get("model_call_count"), maximum=1000),
        revision_count=bounded_event_nonnegative_integer(event_obj.get("revision_count"), maximum=1000),
        failure_reason=bounded_event_text(event_obj.get("failure_reason"), default="", max_characters=1000),
    )


def reconcile_reply_pipeline_effective_outcomes(
    events: List[Dict[str, Any]],
    *,
    _normalise_lane: Callable[[Any], str],
) -> None:
    """Attach later terminal/public observations to stage-only telemetry."""
    decisions: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
    local_rejections: Dict[Tuple[str, str], Dict[str, Any]] = {}
    outcomes: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for event in events:
        lane = _normalise_lane(event.get("lane"))
        target = str(event.get("target_id") or "")
        if not target:
            continue
        kind = event.get("kind")
        if kind == "reply_strategy_decision":
            version = str(event.get("strategy_version") or "unavailable")
            decisions[(version, lane, target)] = event
        elif kind == "reply_strategy_local_rejection":
            local_rejections[(lane, target)] = event
        elif kind == "reply_strategy_outcome":
            outcomes[(lane, target)] = event

    for decision in decisions.values():
        lane = _normalise_lane(decision.get("lane"))
        target = str(decision.get("target_id") or "")
        local = local_rejections.get((lane, target))
        outcome = outcomes.get((lane, target))
        if local is not None:
            for field in (
                "effective_status",
                "effective_reason",
                "original_local_rejection_reason",
                "direct_answer_repair_attempted",
                "direct_answer_repair_outcome",
            ):
                if local.get(field) is not None:
                    decision[field] = local.get(field)
        elif outcome is not None:
            outcome_status = str(outcome.get("status") or "confirmed")
            decision["effective_status"] = (
                "published"
                if outcome_status in {"confirmed", "posted"}
                else outcome_status
            )
            decision["effective_reason"] = (
                outcome.get("failure_reason") or outcome_status
            )
        elif decision.get("status") == "no_reply":
            decision["effective_status"] = "no_reply"
            decision["effective_reason"] = (
                decision.get("reason")
                or decision.get("no_reply_reason")
                or "no_reply"
            )
        elif not decision.get("effective_status"):
            decision["effective_status"] = "not_observed_in_window"
            decision["effective_reason"] = (
                "no_terminal_or_public_outcome_observed"
            )

    for stage in events:
        if stage.get("kind") != "reply_pipeline_stage_summary":
            continue
        version = str(stage.get("strategy_version") or "unavailable")
        lane = _normalise_lane(stage.get("lane"))
        target = str(stage.get("target_id") or "")
        stage["pipeline_stage_status"] = (
            stage.get("pipeline_stage_status")
            or stage.get("status")
            or "unavailable"
        )
        stage["pipeline_stage_reason"] = (
            stage.get("pipeline_stage_reason")
            or stage.get("terminal_reason")
            or ""
        )
        decision = decisions.get((version, lane, target))
        local = local_rejections.get((lane, target))
        outcome = outcomes.get((lane, target))
        if local is not None:
            source = local
        elif outcome is not None:
            outcome_status = str(outcome.get("status") or "confirmed")
            stage["effective_status"] = (
                "published"
                if outcome_status in {"confirmed", "posted"}
                else outcome_status
            )
            stage["effective_reason"] = (
                outcome.get("failure_reason") or outcome_status
            )
            source = None
        elif decision and decision.get("effective_status"):
            source = decision
        elif stage.get("effective_status"):
            source = None
        elif stage.get("pipeline_stage_status") == "no_reply":
            stage["effective_status"] = "no_reply"
            stage["effective_reason"] = stage.get("pipeline_stage_reason")
            source = None
        else:
            stage["effective_status"] = "not_observed_in_window"
            stage["effective_reason"] = "no_terminal_or_public_outcome_observed"
            source = None
        if source is not None:
            for field in (
                "effective_status",
                "effective_reason",
                "original_local_rejection_reason",
                "direct_answer_repair_attempted",
                "direct_answer_repair_outcome",
            ):
                if source.get(field) is not None:
                    stage[field] = source.get(field)
