"""Generated-identity log observations and policy/shadow report summaries.

The coordinator keeps branch precedence and supplies strict parsing, diagnostic
formatting, lazy source references and invocation-local collections. Observations
retain the parser's dictionary, replacing only its time; summary validation stays
separate. This module performs no I/O or runtime initialisation.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Callable, Dict, List, TYPE_CHECKING

if TYPE_CHECKING:
    from mrs_log_digest_contracts import SourceReference

from mrs_log_digest_records import parse_prefixed_json_observation
from mrs_log_digest_values import most_common_with_cutoff_ties


def record_generated_identity_shadow(
    message: str,
    timestamp: datetime,
    level: str,
    *,
    observations: List[Dict[str, Any]],
    stats: Counter,
    errors: List[Dict[str, Any]],
    parse_json_object: Callable[..., Dict[str, Any]],
    short_text: Callable[[Any, int], str],
    source_ref: Callable[[], SourceReference],
) -> None:
    """Record one matched GENERATED_IDENTITY_POLICY_SHADOW_RESULT observation.

    Mutate only the parser result and supplied local lists/counter. Parse errors
    retain their original diagnostics; source_ref is called only on failure.
    Later summary validation is intentionally outside the parsing exception.
    """
    parsed_ok, parsed = parse_prefixed_json_observation(
        message, timestamp, level,
        marker="GENERATED_IDENTITY_POLICY_SHADOW_RESULT",
        parse_error_counter="generated_identity_shadow_parse_errors",
        stats=stats, errors=errors, parse_json_object=parse_json_object,
        short_text=short_text, source_ref=source_ref,
    )
    if not parsed_ok:
        return
    parsed["time"] = timestamp.strftime("%Y-%m-%d %H:%M:%S")
    observations.append(parsed)
    stats["generated_identity_shadow_observations"] += 1


def record_generated_identity_policy(
    message: str,
    timestamp: datetime,
    level: str,
    *,
    observations: List[Dict[str, Any]],
    stats: Counter,
    errors: List[Dict[str, Any]],
    parse_json_object: Callable[..., Dict[str, Any]],
    short_text: Callable[[Any, int], str],
    source_ref: Callable[[], SourceReference],
) -> None:
    """Record one matched GENERATED_IDENTITY_POLICY_APPLIED observation.

    Mutate only the parser result and supplied local lists/counter. Parse errors
    retain their original diagnostics; source_ref is called only on failure.
    Later summary validation is intentionally outside the parsing exception.
    """
    parsed_ok, parsed = parse_prefixed_json_observation(
        message, timestamp, level,
        marker="GENERATED_IDENTITY_POLICY_APPLIED",
        parse_error_counter="generated_identity_policy_parse_errors",
        stats=stats, errors=errors, parse_json_object=parse_json_object,
        short_text=short_text, source_ref=source_ref,
    )
    if not parsed_ok:
        return
    parsed["time"] = timestamp.strftime("%Y-%m-%d %H:%M:%S")
    observations.append(parsed)
    stats["generated_identity_policy_observations"] += 1


def generated_identity_shadow_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return the generated identity shadow summary."""
    def relevant(item: Dict[str, Any]) -> bool:
        return sum(
            int(item.get(key, 0) or 0)
            for key in ("small_penalty_count", "strong_penalty_count", "origin_quote_only_excluded_count")
        ) > 0

    def category(item: Dict[str, Any]) -> str:
        is_relevant = relevant(item)
        if item.get("counterfactual_comparison_version") == "generated_identity_counterfactual_v1":
            baseline = str(item.get("baseline_winner") or "")
            policy_winner = str(item.get("counterfactual_policy_winner") or "")
            expected_change = is_relevant and baseline != policy_winner
            if (
                item.get("counterfactual_comparison_valid") is not True
                or item.get("winner_changed_by_policy") is not expected_change
                or item.get("winner_changed") is not expected_change
            ):
                return "counterfactual_invariant_failure"
            if expected_change:
                return "identity_policy_winner_change"
            if is_relevant:
                return "identity_policy_scores_or_eligibility_only"
            return "no_policy_effect"
        if is_relevant and item.get("winner_changed") is True:
            return "legacy_policy_causation_unverified"
        if is_relevant:
            return "identity_policy_scores_or_eligibility_only"
        return "no_policy_effect"

    categories = [category(item) for item in events]
    relevant_events = [item for item in events if relevant(item)]
    changed = [item for item, item_category in zip(events, categories) if item_category == "identity_policy_winner_change"]
    effect_only = [item for item, item_category in zip(events, categories) if item_category == "identity_policy_scores_or_eligibility_only"]
    legacy_unverified = [item for item, item_category in zip(events, categories) if item_category == "legacy_policy_causation_unverified"]
    counterfactual_failures = [item for item, item_category in zip(events, categories) if item_category == "counterfactual_invariant_failure"]
    production_generated = [item for item in events if item.get("production_source") == "generated"]
    excluded = Counter()
    penalised = Counter()
    policies = Counter()
    winners = Counter()
    phases = Counter()
    for item in events:
        excluded.update(str(value) for value in item.get("excluded_generated_basenames") or [])
        penalised.update(str(value) for value in item.get("penalised_generated_basenames") or [])
        policies[str(item.get("production_identity_policy") or "original")] += 1
        winners[str(item.get("shadow_winner") or "no_shadow_winner")] += 1
        phases[str(item.get("selection_phase") or "unknown")] += 1
    return {
        "observations": len(events),
        "production_original": sum(item.get("production_source") == "original" for item in events),
        "production_generated": len(production_generated),
        "production_generated_origin_quote": sum(item.get("production_origin_quote_match") is True for item in production_generated),
        "production_generated_cross_quote": sum(item.get("production_origin_quote_match") is not True for item in production_generated),
        "policy_relevant_observations": len(relevant_events),
        "winner_changes": len(changed),
        "winner_change_percent": (len(changed) / (len(changed) + len(effect_only)) * 100.0) if changed or effect_only else 0.0,
        "policy_effect_without_winner_change": len(effect_only),
        "legacy_policy_causation_unverified": len(legacy_unverified),
        "counterfactual_invariant_failures": len(counterfactual_failures),
        "production_winner_origin_only_excluded": sum(item.get("production_identity_action") == "generated_cross_quote_origin_only_excluded" for item in events),
        "production_winner_small_penalty": sum(item.get("production_identity_action") == "generated_cross_quote_small_penalty" for item in events),
        "production_winner_strong_penalty": sum(item.get("production_identity_action") == "generated_cross_quote_strong_penalty" for item in events),
        "cross_quote_candidates_excluded": sum(int(item.get("origin_quote_only_excluded_count", 0) or 0) for item in events),
        "cross_quote_candidates_penalised": sum(int(item.get("small_penalty_count", 0) or 0) + int(item.get("strong_penalty_count", 0) or 0) for item in events),
        "most_frequent_excluded_images": excluded.most_common(8),
        "most_frequent_penalised_images": penalised.most_common(8),
        "most_frequent_production_policies": policies.most_common(8),
        "most_frequent_shadow_winners": most_common_with_cutoff_ties(winners),
        "selection_phases": phases.most_common(),
        "event_categories": categories,
    }



def generated_identity_policy_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return the generated identity policy summary."""
    def relevant(item: Dict[str, Any]) -> bool:
        return sum(
            int(item.get(key, 0) or 0)
            for key in ("small_penalty_count", "strong_penalty_count", "origin_quote_only_excluded_count")
        ) > 0

    def winner_differs(item: Dict[str, Any]) -> bool:
        explicit = item.get("baseline_winner_differs")
        if type(explicit) is bool:
            return explicit
        return str(item.get("baseline_winner") or "") != str(item.get("production_winner") or "")

    def category(item: Dict[str, Any]) -> str:
        is_relevant = relevant(item)
        differs = winner_differs(item)
        if item.get("counterfactual_comparison_version") == "generated_identity_counterfactual_v1":
            expected_change = is_relevant and differs
            if (
                item.get("counterfactual_comparison_valid") is not True
                or item.get("winner_changed_by_policy") is not expected_change
            ):
                return "counterfactual_invariant_failure"
            if expected_change:
                return "identity_policy_winner_change"
            if is_relevant:
                return "identity_policy_scores_or_eligibility_only"
            return "no_policy_effect"
        if is_relevant and item.get("winner_changed_by_policy") is True:
            return "legacy_policy_causation_unverified"
        if is_relevant:
            return "identity_policy_scores_or_eligibility_only"
        if differs:
            try:
                equal_score = float(item.get("baseline_winner_score")) == float(item.get("production_policy_score"))
            except (TypeError, ValueError):
                equal_score = False
            if equal_score:
                return "policy_neutral_equal_score_tie_resolution"
            return "policy_neutral_downstream_winner_difference"
        return "winner_unchanged"

    categories = {id(item): category(item) for item in events}
    relevant_events = [item for item in events if relevant(item)]
    changed = [item for item in events if categories[id(item)] == "identity_policy_winner_change"]
    effect_only = [item for item in events if categories[id(item)] == "identity_policy_scores_or_eligibility_only"]
    legacy_unverified = [item for item in events if categories[id(item)] == "legacy_policy_causation_unverified"]
    counterfactual_failures = [item for item in events if categories[id(item)] == "counterfactual_invariant_failure"]
    neutral_differences = [item for item in events if categories[id(item)].startswith("policy_neutral_")]
    excluded = Counter()
    replacements = Counter()
    phases = Counter()
    for item in events:
        excluded.update(str(value) for value in item.get("excluded_generated_basenames") or [])
        if categories[id(item)] == "identity_policy_winner_change":
            replacements[str(item.get("production_winner") or "no_winner")] += 1
        phases[str(item.get("selection_phase") or "unknown")] += 1
    return {
        "observations": len(events),
        "policy_relevant_observations": len(relevant_events),
        "identity_policy_winner_changes": len(changed),
        # Compatibility alias: now explicitly uses the same policy-causal
        # definition as identity_policy_winner_changes and the rendered rows.
        "winner_changes": len(changed),
        "winner_change_percent": (len(changed) / (len(changed) + len(effect_only)) * 100.0) if changed or effect_only else 0.0,
        "policy_effect_without_winner_change": len(effect_only),
        "legacy_policy_causation_unverified": len(legacy_unverified),
        "counterfactual_invariant_failures": len(counterfactual_failures),
        "policy_neutral_baseline_differences": len(neutral_differences),
        "policy_neutral_equal_score_tie_resolutions": sum(
            categories[id(item)] == "policy_neutral_equal_score_tie_resolution" for item in events
        ),
        "baseline_origin_only_prevented": sum(item.get("baseline_identity_action") == "generated_cross_quote_origin_only_excluded" and categories[id(item)] == "identity_policy_winner_change" for item in events),
        "baseline_small_penalty_displaced": sum(item.get("baseline_identity_action") == "generated_cross_quote_small_penalty" and categories[id(item)] == "identity_policy_winner_change" for item in events),
        "baseline_strong_penalty_displaced": sum(item.get("baseline_identity_action") == "generated_cross_quote_strong_penalty" and categories[id(item)] == "identity_policy_winner_change" for item in events),
        "replacement_source_transitions": Counter(str(item.get("replacement_source_transition") or "unknown") for item in changed).most_common(),
        "cross_quote_candidates_excluded": sum(int(item.get("origin_quote_only_excluded_count", 0) or 0) for item in events),
        "cross_quote_candidates_penalised": sum(int(item.get("small_penalty_count", 0) or 0) + int(item.get("strong_penalty_count", 0) or 0) for item in events),
        "most_frequent_excluded_images": excluded.most_common(8),
        "most_frequent_replacement_images": replacements.most_common(8),
        "selection_phases": phases.most_common(),
        "recovery_observations": sum(str(item.get("recovery_effect") or "none") != "none" for item in events),
        "no_valid_candidate_events": 0,
        "event_categories": [category(item) for item in events],
    }
