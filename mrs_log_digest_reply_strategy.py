"""Legacy reply-strategy evidence, observation projections and summaries.

Handlers project parsed payloads, coalesce local rejections and infer missing
outcomes through current coordinator callbacks; summaries only read supplied
events. Parsing, dispatch, event insertion and provenance remain with the
coordinator. There is no runtime I/O, publication authority or state shared
between analyses.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from mrs_log_digest_values import (
    _count_optional,
    _is_terminal_pipeline_failure,
    _is_writer_local_failure,
    _normalise_lane,
    _terminal_local_rejection_outcome,
    bounded_event_boolean,
    bounded_event_text,
    valid_string_public_post_id,
)


def _no_reply_category(value: Any) -> str:
    if _is_writer_local_failure(value):
        return "writer_local_failure"
    reason = " ".join(str(value or "").lower().replace("-", "_").split())
    if not reason:
        return "other_editorial_decline"
    if "exact_duplicate" in reason or "duplicate reply" in reason:
        return "duplicate_response_rejection"
    if any(term in reason for term in ("unverifiable", "unverified", "unsupported claim", "endorse")):
        return "no_reply_due_to_unverifiable_claim"
    if any(term in reason for term in ("bait", "abuse", "abusive", "prolong conflict", "needless conflict")):
        return "no_reply_due_to_bait_or_abuse"
    if any(term in reason for term in ("incoherent", "gibberish", "unintelligible")):
        return "no_reply_due_to_incoherent"
    if any(term in reason for term in ("no substantive", "nothing to reply", "no question")):
        return "no_substantive_prompt"
    if any(term in reason for term in ("repet", "low value", "not useful", "declin")):
        return "low_value_or_repetitive_engagement"
    return "other_editorial_decline"


def reply_strategy_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return the reply strategy summary."""
    raw_decisions = [event for event in events if event.get("kind") == "reply_strategy_decision"]
    decision_by_id: Dict[str, Dict[str, Any]] = {}
    for index, event in enumerate(raw_decisions):
        lane = _normalise_lane(event.get("lane"))
        target = str(event.get("target_id") or "")
        decision_id = f"{lane}:{target}" if target else f"missing:{index}"
        decision_by_id[decision_id] = event
    decisions = list(decision_by_id.values())
    terminal_local_rejections: Dict[Tuple[str, str], str] = {}
    terminal_local_rejection_reasons: Dict[Tuple[str, str], str] = {}
    for index, event in enumerate(decisions):
        raw_reason = event.get("no_reply_reason")
        outcome = _terminal_local_rejection_outcome(raw_reason)
        if outcome is None:
            raw_reason = event.get("reason")
            outcome = _terminal_local_rejection_outcome(raw_reason)
        if outcome is None:
            continue
        lane = _normalise_lane(event.get("lane"))
        target = str(event.get("target_id") or f"missing-decision-{index}")
        terminal_local_rejections[(lane, target)] = outcome
        terminal_local_rejection_reasons[(lane, target)] = str(raw_reason)
    for index, event in enumerate(events):
        if event.get("kind") != "reply_strategy_local_rejection":
            continue
        outcome = _terminal_local_rejection_outcome(event.get("reason"))
        if outcome is None:
            continue
        target = str(event.get("target_id") or f"missing-local-{index}")
        lane = _normalise_lane(event.get("lane"))
        matching_decision = next(
            (
                decision
                for decision in decisions
                if str(decision.get("target_id") or "") == target
            ),
            None,
        )
        if lane == "unavailable" and matching_decision is not None:
            lane = _normalise_lane(matching_decision.get("lane"))
        terminal_local_rejections[(lane, target)] = outcome
        terminal_local_rejection_reasons[(lane, target)] = str(
            event.get("reason") or ""
        )
    outcome_by_id: Dict[str, Dict[str, Any]] = {}
    for index, event in enumerate(events):
        if event.get("kind") != "reply_strategy_outcome":
            continue
        reply_post_id = str(event.get("reply_post_id") or "")
        outcome_id = (
            f"reply:{reply_post_id}"
            if reply_post_id
            else f"{event.get('status')}:{_normalise_lane(event.get('lane'))}:{event.get('target_id') or index}"
        )
        outcome_by_id.setdefault(outcome_id, event)
    outcomes = list(outcome_by_id.values())
    pipeline_failures = [
        event for event in events if event.get("kind") == "reply_strategy_failure"
    ]
    published_outcomes = [
        event for event in outcomes
        if str(event.get("status") or "confirmed") in {"confirmed", "posted"}
    ]
    published_identities = {
        (_normalise_lane(event.get("lane")), str(event.get("target_id") or ""))
        for event in published_outcomes
        if event.get("target_id")
    }
    pipeline_failures.extend(
        event
        for event in decisions
        if _is_terminal_pipeline_failure(
            event.get("reason") or event.get("no_reply_reason"),
            event.get("status"),
        )
        and (
            _normalise_lane(event.get("lane")),
            str(event.get("target_id") or ""),
        )
        not in published_identities
    )

    posted: list[tuple[str, str]] = []
    for event in events:
        kind = str(event.get("kind") or "")
        lane = {"mention_reply_posted": "mention", "hot_post_reply_posted": "hot-post",
                "quote_tweet_reply_posted": "quote-tweet"}.get(kind)
        if lane:
            target_field = {"mention": "mention_id", "hot-post": "hot_post_reply_id", "quote-tweet": "quote_tweet_id"}[lane]
            posted.append((lane, str(event.get(target_field) or "")))

    outcome_targets = {
        (_normalise_lane(event.get("lane")), str(event.get("target_id") or ""))
        for event in published_outcomes
        if event.get("target_id")
    }
    observations = list(published_outcomes)
    observed_decision_ids = set()
    for index, event in enumerate(decisions):
        lane = _normalise_lane(event.get("lane"))
        target = str(event.get("target_id") or f"missing-decision-{index}")
        identity = (lane, target)
        if identity in outcome_targets:
            continue
        if (
            event.get("mode") == "no_reply"
            and not _is_terminal_pipeline_failure(
                event.get("reason") or event.get("no_reply_reason"),
                event.get("status"),
            )
        ) or identity in terminal_local_rejections:
            observations.append(event)
            observed_decision_ids.add(id(event))
    targeted_decisions: Dict[tuple[str, str], list[Dict[str, Any]]] = {}
    anonymous_decisions: Dict[str, list[Dict[str, Any]]] = {}
    for event in decisions:
        if id(event) in observed_decision_ids:
            continue
        lane = _normalise_lane(event.get("lane"))
        target = str(event.get("target_id") or "")
        if target and (lane, target) in outcome_targets:
            continue
        if target:
            targeted_decisions.setdefault((lane, target), []).append(event)
        else:
            anonymous_decisions.setdefault(lane, []).append(event)
    for lane, target in posted:
        if target and (lane, target) in outcome_targets:
            continue
        candidates = targeted_decisions.get((lane, target), []) if target else []
        if candidates:
            observations.append(candidates.pop(0))
            continue
        anonymous = anonymous_decisions.get(lane, [])
        if anonymous:
            observations.append(anonymous.pop(0))
            continue
        observations.append({"kind": "reply_strategy_unavailable", "lane": lane, "target_id": target})

    modes = Counter({key: 0 for key in (
        "direct_factual_answer", "factual", "clarification", "opinion_or_principle", "light_humour", "courtesy",
        "historical_correction", "historical_context", "researched_principle", "principle_reply", "wry_reply",
        "playful_reply", "deadpan_reply", "warm_reply", "no_reply", "strategy metadata unavailable",
    )})
    valid_modes = set(modes) - {"strategy metadata unavailable"}
    modes.update(
        mode if mode in valid_modes else "strategy metadata unavailable"
        for mode in (str(event.get("mode") or "") for event in observations)
    )
    generated_modes = Counter({key: 0 for key in modes})
    generated_modes.update(
        mode if mode in valid_modes else "strategy metadata unavailable"
        for mode in (str(event.get("mode") or "") for event in decisions)
    )
    by_lane: Dict[str, Counter] = {lane: Counter() for lane in ("mention", "hot-post", "quote-tweet", "unavailable")}
    for event in observations:
        lane = _normalise_lane(event.get("lane"))
        mode = str(event.get("mode") or "")
        by_lane[lane][mode if mode in valid_modes else "strategy metadata unavailable"] += 1
    generated_by_lane: Dict[str, Counter] = {lane: Counter() for lane in by_lane}
    for event in decisions:
        lane = _normalise_lane(event.get("lane"))
        mode = str(event.get("mode") or "")
        generated_by_lane[lane][mode if mode in valid_modes else "strategy metadata unavailable"] += 1
    final_reply_kinds = {
        "factual",
        "clarification",
        "opinion_or_principle",
        "light_humour",
        "courtesy",
        "unknown",
        "no_reply",
    }

    def final_reply_kind_counts(rows: Iterable[Dict[str, Any]]) -> Dict[str, int]:
        counts = Counter()
        for event in rows:
            value = event.get("final_reply_kind")
            counts[
                value
                if isinstance(value, str) and value in final_reply_kinds
                else "metadata unavailable"
            ] += 1
        return dict(sorted(counts.items()))

    outcome_status_counts = Counter()
    for event in outcomes:
        status = str(event.get("status") or "confirmed")
        if status in {"confirmed", "posted"}:
            outcome_status_counts["posted"] += 1
        elif status.startswith("posting_failed"):
            outcome_status_counts["posting_failed"] += 1
        else:
            outcome_status_counts[status or "unavailable"] += 1
    outcome_status_counts["terminal_no_reply"] += sum(
        event.get("mode") == "no_reply"
        and not _is_terminal_pipeline_failure(
            event.get("reason") or event.get("no_reply_reason"),
            event.get("status"),
        )
        and (
            _normalise_lane(event.get("lane")),
            str(event.get("target_id") or ""),
        )
        not in terminal_local_rejections
        for event in decisions
    )
    outcome_status_counts["terminal_repetition_rejection"] += sum(
        outcome == "terminal_repetition_rejection"
        for outcome in terminal_local_rejections.values()
    )
    outcome_status_counts["terminal_clarification_mode_rejection"] += sum(
        outcome == "terminal_clarification_mode_rejection"
        for outcome in terminal_local_rejections.values()
    )
    outcome_status_counts["pipeline_failed"] += sum(
        _is_terminal_pipeline_failure(
            event.get("reason") or event.get("no_reply_reason"),
            event.get("status"),
        )
        and (
            _normalise_lane(event.get("lane")),
            str(event.get("target_id") or ""),
        )
        not in published_identities
        for event in decisions
    )
    retrieved = [int(event["retrieved_count"]) for event in observations if type(event.get("retrieved_count")) is int]
    generated_retrieved = [
        int(event["retrieved_count"])
        for event in decisions
        if type(event.get("retrieved_count")) is int
    ]
    evidence_references = [
        int(event["evidence_reference_count"])
        for event in observations
        if type(event.get("evidence_reference_count")) is int
    ]
    generated_evidence_references = [
        int(event["evidence_reference_count"])
        for event in decisions
        if type(event.get("evidence_reference_count")) is int
    ]
    rejection_reasons = Counter()
    no_reply_categories = Counter()
    routine_reasons = Counter()
    repetition_controls = Counter({key: 0 for key in (
        "exact_duplicate_rejected", "highly_similar_reply_rejected", "canned_formulation_rejected",
        "regenerated_after_style_rejection", "no_acceptable_reply",
    )})
    routine = {"author_daily_cap", "daily_cap", "spacing", "already_replied", "dry_run_already_seen", "own_account"}
    no_reply_targets = {
        (_normalise_lane(event.get("lane")), str(event.get("target_id") or ""))
        for event in decisions
        if event.get("mode") == "no_reply"
        and event.get("target_id")
        and not _is_terminal_pipeline_failure(
            event.get("reason") or event.get("no_reply_reason"),
            event.get("status"),
        )
    }
    terminal_local_targets = set(terminal_local_rejections)
    for (lane, target), outcome in terminal_local_rejections.items():
        reason = terminal_local_rejection_reasons.get((lane, target)) or (
            "near_duplicate_reply"
            if outcome == "terminal_repetition_rejection"
            else "clarification_not_direct_factual_answer"
        )
        rejection_reasons[reason] += 1
        if outcome == "terminal_repetition_rejection":
            repetition_key = (
                "exact_duplicate_rejected"
                if str(reason).strip().lower() == "exact_duplicate_reply"
                else "highly_similar_reply_rejected"
            )
            repetition_controls[repetition_key] += 1
    seen_skips = set()
    for event in events:
        if event.get("kind") == "reply_strategy_rejection":
            reason = str(event.get("reason") or "other")
            rejection_reasons[reason] += 1
            if reason in repetition_controls:
                repetition_controls[reason] += 1
        elif event.get("kind") == "candidate_skipped":
            reason = str(event.get("reason") or "other")
            if (
                reason == "no_usable_reply_generated"
                and (
                    _normalise_lane(event.get("lane")),
                    str(event.get("target_id") or ""),
                )
                in no_reply_targets | terminal_local_targets
            ):
                continue
            identity = (event.get("time"), event.get("lane"), event.get("target_id"), reason)
            if identity in seen_skips:
                continue
            seen_skips.add(identity)
            if reason == "no_usable_reply_generated":
                repetition_controls["no_acceptable_reply"] += 1
            (routine_reasons if reason in routine else rejection_reasons)[reason] += 1
    for event in decisions:
        if event.get("mode") == "no_reply" and not _is_terminal_pipeline_failure(
            event.get("reason") or event.get("no_reply_reason"),
            event.get("status"),
        ):
            target = (
                _normalise_lane(event.get("lane")),
                str(event.get("target_id") or ""),
            )
            if target in terminal_local_targets:
                continue
            writer_local_reason = next(
                (
                    str(value)
                    for value in (
                        event.get("effective_reason"),
                        event.get("reason"),
                        event.get("no_reply_reason"),
                    )
                    if _is_writer_local_failure(value)
                ),
                None,
            )
            reason = writer_local_reason or str(
                event.get("no_reply_reason") or "model-selected no_reply"
            )
            rejection_reasons[reason] += 1
            category = _no_reply_category(reason)
            no_reply_categories[category] += 1
            if category == "duplicate_response_rejection":
                repetition_controls["exact_duplicate_rejected"] += 1
    tone_values = ("firm", "dry", "wry", "warm", "neutral", "light", "playful", "deadpan", "none", "unknown", "unavailable")
    humour_counts = _count_optional(observations, "humour_tone", tone_values)
    confidence_values = ("high", "medium", "low", "none", "local_trusted_facts_supplied", "unavailable")
    confidence_counts = _count_optional(observations, "evidence_confidence", confidence_values)
    generated_humour_counts = _count_optional(decisions, "humour_tone", tone_values)
    generated_confidence_counts = _count_optional(decisions, "evidence_confidence", confidence_values)
    # Preserve the established sparse outcome-status result shape.
    # New terminal categories are present only when actually observed.
    for zero_only_key in (
        "terminal_repetition_rejection",
        "terminal_clarification_mode_rejection",
        "pipeline_failed",
    ):
        if not outcome_status_counts.get(zero_only_key):
            outcome_status_counts.pop(zero_only_key, None)

    return {
        "mode_counts": dict(sorted(modes.items())),
        "mode_counts_by_lane": {lane: dict(sorted(counts.items())) for lane, counts in sorted(by_lane.items())},
        "generated_mode_counts": dict(sorted(generated_modes.items())),
        "generated_mode_counts_by_lane": {
            lane: dict(sorted(counts.items())) for lane, counts in sorted(generated_by_lane.items())
        },
        "final_reply_kind_counts": final_reply_kind_counts(observations),
        "generated_final_reply_kind_counts": final_reply_kind_counts(decisions),
        "outcome_status_counts": dict(sorted(outcome_status_counts.items())),
        "humour_tone_counts": humour_counts,
        "confidence_counts": confidence_counts,
        "generated_humour_tone_counts": generated_humour_counts,
        "generated_confidence_counts": generated_confidence_counts,
        "confirmed_outcome_count": len(published_outcomes),
        "generated_grounded_count": sum(event.get("grounded") is True for event in decisions),
        "posted_grounded_count": sum(
            event.get("grounded") is True
            for event in observations
            if event.get("mode") != "no_reply"
        ),
        "generated_factual_claim_count": sum(event.get("factual_claim") is True for event in decisions),
        "generated_average_retrieved_packet_count": (
            sum(generated_retrieved) / len(generated_retrieved)
            if generated_retrieved else None
        ),
        "generated_maximum_retrieved_packet_count": max(generated_retrieved) if generated_retrieved else None,
        "generated_no_retrieved_packets_count": sum(value == 0 for value in generated_retrieved),
        "generated_average_evidence_reference_count": (
            sum(generated_evidence_references) / len(generated_evidence_references)
            if generated_evidence_references else None
        ),
        "generated_maximum_evidence_reference_count": (
            max(generated_evidence_references) if generated_evidence_references else None
        ),
        "generated_no_evidence_references_count": sum(
            value == 0 for value in generated_evidence_references
        ),
        "grounded_count": sum(event.get("grounded") is True for event in observations),
        "grounding_metadata_unavailable_count": sum(type(event.get("grounded")) is not bool for event in observations),
        "claim_free_opinion_or_principle_count": sum(
            event.get("factual_claim") is False
            and event.get("mode") in {"opinion_or_principle", "principle_reply"}
            for event in observations
        ),
        "humour_reply_count": sum(
            str(event.get("mode") or "") in {
                "light_humour", "wry_reply", "playful_reply", "deadpan_reply"
            }
            for event in observations
        ),
        "conversational_candidate_count": len(decisions),
        "writer_local_failure_count": int(
            no_reply_categories.get("writer_local_failure", 0)
        ),
        "deliberately_declined_count": sum(
            event.get("mode") == "no_reply"
            and not _is_terminal_pipeline_failure(
                event.get("reason") or event.get("no_reply_reason"),
                event.get("status"),
            )
            and not any(
                _is_writer_local_failure(value)
                for value in (
                    event.get("effective_reason"),
                    event.get("reason"),
                    event.get("no_reply_reason"),
                )
            )
            and (
                _normalise_lane(event.get("lane")),
                str(event.get("target_id") or ""),
            )
            not in terminal_local_targets
            for event in decisions
        ),
        "terminal_repetition_rejection_count": sum(
            outcome == "terminal_repetition_rejection"
            for outcome in terminal_local_rejections.values()
        ),
        "terminal_clarification_mode_rejection_count": sum(
            outcome == "terminal_clarification_mode_rejection"
            for outcome in terminal_local_rejections.values()
        ),
        "factual_claim_count": sum(event.get("factual_claim") is True for event in observations),
        "factual_claim_metadata_unavailable_count": sum(type(event.get("factual_claim")) is not bool for event in observations),
        "factual_rejected_insufficient_grounding_count": sum(
            "ground" in str(event.get("reason") or "").lower() or "confidence" in str(event.get("reason") or "").lower()
            for event in events if event.get("kind") == "reply_strategy_rejection"
        ),
        "average_retrieved_packet_count": (sum(retrieved) / len(retrieved)) if retrieved else None,
        "maximum_retrieved_packet_count": max(retrieved) if retrieved else None,
        "no_retrieved_packets_count": sum(value == 0 for value in retrieved),
        "retrieved_packet_metadata_unavailable_count": sum(type(event.get("retrieved_count")) is not int for event in observations),
        "average_evidence_reference_count": (
            sum(evidence_references) / len(evidence_references)
            if evidence_references else None
        ),
        "maximum_evidence_reference_count": (
            max(evidence_references) if evidence_references else None
        ),
        "no_evidence_references_count": sum(value == 0 for value in evidence_references),
        "evidence_reference_metadata_unavailable_count": sum(
            type(event.get("evidence_reference_count")) is not int
            for event in observations
        ),
        "rejection_reason_counts": dict(rejection_reasons.most_common()),
        "pipeline_failure_reason_counts": dict(Counter(
            str(event.get("reason") or "unknown_pipeline_failure")
            for event in pipeline_failures
        ).most_common()),
        "pipeline_failure_count": len(pipeline_failures),
        "no_reply_category_counts": dict(no_reply_categories),
        "routine_skip_reason_counts": dict(routine_reasons.most_common()),
        "repetition_control_counts": dict(repetition_controls),
    }


def conversational_evidence_fields(
    event_obj: Dict[str, Any],
    *,
    evidence_ids: Any,
    factual_claim_count: Any,
    bounded_event_string_list: Callable[..., List[str]],
) -> Dict[str, Any]:
    """Project supplied and used conversational evidence with exact unknowns."""
    factual_claim = (
        factual_claim_count > 0
        if type(factual_claim_count) is int
        else None
    )
    confidence = bounded_event_text(
        event_obj.get("evidence_confidence"), max_characters=100
    )
    if not confidence:
        confidence = "none" if factual_claim is False else "unavailable"
    raw_supplied_ids = event_obj.get("trusted_fact_ids_supplied")
    has_explicit_supply = isinstance(raw_supplied_ids, list)
    supplied_ids = (
        bounded_event_string_list(
            raw_supplied_ids, limit=1000, item_max_characters=200
        )
        if has_explicit_supply
        else None
    )
    if not has_explicit_supply:
        supplied_ids = (
            bounded_event_string_list(
                evidence_ids, limit=1000, item_max_characters=200
            )
            if isinstance(evidence_ids, list)
            else None
        )
    supplied_count = event_obj.get("trusted_facts_supplied_count")
    if (
        type(supplied_count) is not int
        or not 0 <= supplied_count <= 1_000_000
    ):
        supplied_count = event_obj.get("retrieved_count")
    if (
        type(supplied_count) is not int
        or not 0 <= supplied_count <= 1_000_000
    ):
        supplied_count = (
            len(supplied_ids)
            if has_explicit_supply and isinstance(supplied_ids, list)
            else None
        )

    has_explicit_use = "used_fact_count" in event_obj
    used_count = event_obj.get("used_fact_count")
    if not has_explicit_use:
        used_count = event_obj.get("evidence_reference_count")
        if type(used_count) is not int or used_count < 0:
            used_count = (
                len(supplied_ids or [])
                if isinstance(evidence_ids, list)
                else None
            )
    elif not (
        type(used_count) is int and 0 <= used_count <= 1_000_000
    ):
        used_count = "unknown"
    raw_used_ids = (
        event_obj.get("used_fact_ids") if has_explicit_use else evidence_ids
    )
    used_ids = (
        bounded_event_string_list(
            raw_used_ids, limit=1000, item_max_characters=200
        )
        if isinstance(raw_used_ids, list)
        else None
    )
    reference_count = used_count if type(used_count) is int else None
    return {
        "evidence_confidence": confidence,
        "retrieved_count": supplied_count,
        "evidence_reference_count": reference_count,
        "trusted_facts_supplied_count": supplied_count,
        "trusted_fact_ids_supplied": supplied_ids,
        "used_fact_count": used_count,
        "used_fact_ids": used_ids,
        "factual_claim": factual_claim,
        "grounded": (
            used_count > 0
            if type(used_count) is int
            else None
        ),
    }


def record_reply_strategy_decision(
    event_obj: Dict[str, Any],
    ts: datetime,
    *,
    add_event: Callable[..., Dict[str, Any]],
    conversational_evidence_fields: Callable[..., Dict[str, Any]],
) -> None:
    """Project reply_strategy_decision through the current coordinator callbacks."""
    retrieved_ids = event_obj.get("retrieved_quote_ids")
    evidence_fields = conversational_evidence_fields(
        event_obj,
        evidence_ids=event_obj.get("evidence_ids"),
        factual_claim_count=(
            1 if event_obj.get("factual_claim_made") is True else 0
            if event_obj.get("factual_claim_made") is False else None
        ),
    )
    if type(event_obj.get("retrieved_count")) is not int:
        evidence_fields["retrieved_count"] = (
            min(len(retrieved_ids), 1_000_000)
            if isinstance(retrieved_ids, list)
            else None
        )
    evidence_fields["grounded"] = bounded_event_boolean(
        event_obj.get("grounded")
    )
    decision_event = add_event(
        "reply_strategy_decision",
        ts,
        lane=bounded_event_text(
            event_obj.get("lane"),
            default="unavailable",
            max_characters=100,
        ),
        target_id=(event_obj.get("target_id") if valid_string_public_post_id(event_obj.get("target_id")) else ""),
        mode=bounded_event_text(
            event_obj.get("mode"), max_characters=100
        ),
        humour_tone=bounded_event_text(
            event_obj.get("humour_tone"), max_characters=100
        ),
        tone=bounded_event_text(
            event_obj.get("humour_tone"), max_characters=100
        ),
        **evidence_fields,
        no_reply_reason=bounded_event_text(
            event_obj.get("no_reply_reason"), max_characters=500
        ),
    )


def record_reply_strategy_outcome(
    event_obj: Dict[str, Any],
    ts: datetime,
    *,
    add_event: Callable[..., Dict[str, Any]],
    conversational_evidence_fields: Callable[..., Dict[str, Any]],
) -> None:
    """Project reply_strategy_outcome through the current coordinator callbacks."""
    retrieved_ids = event_obj.get("retrieved_quote_ids")
    evidence_fields = conversational_evidence_fields(
        event_obj,
        evidence_ids=event_obj.get("evidence_ids"),
        factual_claim_count=(
            1 if event_obj.get("factual_claim_made") is True else 0
            if event_obj.get("factual_claim_made") is False else None
        ),
    )
    if type(event_obj.get("retrieved_count")) is not int:
        evidence_fields["retrieved_count"] = (
            min(len(retrieved_ids), 1_000_000)
            if isinstance(retrieved_ids, list)
            else None
        )
    evidence_fields["grounded"] = bounded_event_boolean(
        event_obj.get("grounded")
    )
    add_event(
        "reply_strategy_outcome", ts,
        status=bounded_event_text(event_obj.get("status"), default="confirmed", max_characters=100),
        lane=bounded_event_text(event_obj.get("lane"), default="unavailable", max_characters=100),
        target_id=(event_obj.get("target_id") if valid_string_public_post_id(event_obj.get("target_id")) else ""),
        reply_post_id=(event_obj.get("reply_post_id") if valid_string_public_post_id(event_obj.get("reply_post_id")) else ""),
        mode=bounded_event_text(event_obj.get("mode"), max_characters=100),
        final_reply_kind=bounded_event_text(event_obj.get("final_reply_kind"), max_characters=100),
        humour_tone=bounded_event_text(event_obj.get("humour_tone"), max_characters=100),
        tone=bounded_event_text(event_obj.get("humour_tone"), max_characters=100),
        **evidence_fields,
        no_reply_reason=bounded_event_text(event_obj.get("no_reply_reason"), max_characters=500),
        failure_reason=bounded_event_text(event_obj.get("failure_reason"), default="", max_characters=1000),
    )


def record_reply_target_terminal(
    event_obj: Dict[str, Any],
    ts: datetime,
    *,
    add_event: Callable[..., Dict[str, Any]],
) -> None:
    """Project reply_target_terminal through the current coordinator callbacks."""
    add_event(
        "reply_target_terminal", ts,
        lane=bounded_event_text(event_obj.get("lane"), default="unavailable", max_characters=100),
        target_id=(event_obj.get("target_id") if valid_string_public_post_id(event_obj.get("target_id")) else ""),
        outcome=bounded_event_text(event_obj.get("outcome"), default="reply_not_permitted", max_characters=100),
        reason=bounded_event_text(event_obj.get("reason"), default="", max_characters=500),
    )


def record_reply_strategy_rejection(
    event_obj: Dict[str, Any],
    ts: datetime,
    *,
    add_event: Callable[..., Dict[str, Any]],
) -> None:
    """Project reply_strategy_rejection through the current coordinator callbacks."""
    add_event(
        "reply_strategy_rejection", ts,
        lane=bounded_event_text(event_obj.get("lane"), default="unavailable", max_characters=100),
        reason=bounded_event_text(event_obj.get("reason"), default="other", max_characters=500),
    )


def prepare_inferred_reply_strategy_outcomes(
    *,
    events: List[Dict[str, Any]],
    handled_api_restrictions: List[Dict[str, Any]],
    _normalise_lane: Callable[[Any], str],
    parse_dt: Callable[[Any], Optional[datetime]],
    add_event: Callable[..., Dict[str, Any]],
) -> None:
    """Infer missing terminal outcomes from restrictions and the latest decisions."""
    explicit_strategy_outcome_targets = {
        (_normalise_lane(event.get("lane")), str(event.get("target_id") or ""))
        for event in events
        if event.get("kind") == "reply_strategy_outcome" and event.get("target_id")
    }
    strategy_decisions_by_target = {
        (_normalise_lane(event.get("lane")), str(event.get("target_id") or "")): event
        for event in events
        if event.get("kind") == "reply_strategy_decision" and event.get("target_id")
    }
    for restriction in handled_api_restrictions:
        key = (
            _normalise_lane(restriction.get("lane")),
            str(restriction.get("target_id") or ""),
        )
        if not key[1] or key in explicit_strategy_outcome_targets:
            continue
        decision = strategy_decisions_by_target.get(key)
        if decision is None:
            continue
        restriction_time = parse_dt(str(restriction.get("time") or ""))
        if restriction_time is None:
            continue
        add_event(
            "reply_strategy_outcome",
            restriction_time,
            status="posting_failed_terminal",
            lane=restriction.get("lane") or "unavailable",
            target_id=key[1],
            reply_post_id="",
            mode=decision.get("mode"),
            humour_tone=decision.get("humour_tone"),
            tone=decision.get("tone") or decision.get("humour_tone"),
            evidence_confidence=decision.get("evidence_confidence"),
            retrieved_count=decision.get("retrieved_count"),
            factual_claim=decision.get("factual_claim"),
            grounded=decision.get("grounded"),
            no_reply_reason=decision.get("no_reply_reason"),
            failure_reason="reply_not_permitted",
            legacy_inferred=True,
        )
        explicit_strategy_outcome_targets.add(key)



def add_or_merge_local_rejection(
    ts: datetime,
    kwargs: Dict[str, Any],
    *,
    lane: Any,
    target_id: Any,
    local_rejections_by_identity: Dict[Tuple[str, str], Dict[str, Any]],
    max_text: int,
    valid_string_public_post_id: Callable[[Any], bool],
    _normalise_lane: Callable[[Any], str],
    bounded_event_text: Callable[..., str],
    bounded_event_string_list: Callable[..., List[str]],
    short: Callable[..., str],
    add_event: Callable[..., Dict[str, Any]],
) -> Dict[str, Any]:
    """Keep one enriched effective local-rejection record per target."""
    target = (
        target_id if valid_string_public_post_id(target_id) else ""
    )
    normalised_lane = _normalise_lane(lane)
    safe_lane = bounded_event_text(
        lane, default="unavailable", max_characters=100
    )
    safe_kwargs: Dict[str, Any] = {}
    for field, value in kwargs.items():
        if type(value) is str:
            safe_kwargs[field] = short(value, max_text)
        elif type(value) is bool:
            safe_kwargs[field] = value
        elif type(value) is int and 0 <= value <= 1_000_000:
            safe_kwargs[field] = value
        elif isinstance(value, list):
            safe_kwargs[field] = bounded_event_string_list(value)
        elif value is None:
            safe_kwargs[field] = None
    key = (normalised_lane, target)
    existing = local_rejections_by_identity.get(key)
    if existing is None and target:
        existing = next(
            (
                item
                for (item_lane, item_target), item in local_rejections_by_identity.items()
                if item_target == target
                and (normalised_lane == "unavailable" or item_lane == "unavailable")
            ),
            None,
        )
    if existing is None:
        existing = add_event(
            "reply_strategy_local_rejection",
            ts,
            lane=safe_lane,
            target_id=target,
            **safe_kwargs,
        )
        local_rejections_by_identity[key] = existing
        return existing
    if _normalise_lane(existing.get("lane")) == "unavailable" and normalised_lane != "unavailable":
        existing["lane"] = safe_lane
        local_rejections_by_identity.pop(("unavailable", target), None)
        local_rejections_by_identity[key] = existing
    for field, value in safe_kwargs.items():
        existing_value = existing.get(field)
        if (
            value is not None
            and value != ""
            and (existing_value is None or existing_value == "")
        ):
            existing[field] = short(value, max_text) if isinstance(value, str) else value
    return existing
