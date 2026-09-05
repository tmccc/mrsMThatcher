"""Pure legacy reply-pipeline and majority-review reporting for the log digest.

These helpers consume supplied event dictionaries without runtime I/O. Event
parsing and effective-outcome reconciliation remain with the digest coordinator.
"""
from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from mrs_log_digest_values import _normalise_lane


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
