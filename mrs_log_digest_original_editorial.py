"""Original-editorial selection/shadow observations and companion accounting.

The coordinator retains branch precedence and supplies strict parsing, diagnostic
formatting, lazy source references and invocation-local collections. Observations
retain the parser's dictionary, replacing time and event_mode. Each selection
permits suppression of one later matching shadow; earlier shadows remain recorded.
Comparison-key and summary validation stay outside parsing. No I/O or runtime
initialisation is performed here.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
import statistics
from typing import Any, Callable, Dict, List, Tuple

from mrs_log_digest_values import most_common_with_cutoff_ties


def record_original_editorial_selection(
    message: str,
    timestamp: datetime,
    level: str,
    *,
    observations: List[Dict[str, Any]],
    pending_shadow_companions: Counter,
    stats: Counter,
    errors: List[Dict[str, Any]],
    parse_json_object: Callable[..., Dict[str, Any]],
    short_text: Callable[[Any, int], str],
    source_ref: Callable[[], Dict[str, Any]],
) -> None:
    """Record one matched ORIGINAL_EDITORIAL_SELECTION_RESULT observation.

    Mutate only the parser result and supplied invocation-local collections.
    Encoding/parsing failures retain their diagnostics and lazy source reference;
    comparison-key and summary failures remain outside that exception boundary.
    """
    raw = message.split("ORIGINAL_EDITORIAL_SELECTION_RESULT ", 1)[1].strip()
    try:
        parsed = parse_json_object(
            raw.encode("utf-8"),
            label="ORIGINAL_EDITORIAL_SELECTION_RESULT",
        )
    except Exception as exc:
        errors.append({
            "time": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "level": level,
            "message": f"Malformed ORIGINAL_EDITORIAL_SELECTION_RESULT: {exc}: {short_text(raw, 240)}",
            "source_refs": [source_ref()],
        })
        stats["original_editorial_selection_parse_errors"] += 1
        return
    parsed["time"] = timestamp.strftime("%Y-%m-%d %H:%M:%S")
    parsed["event_mode"] = "selection"
    observations.append(parsed)
    pending_shadow_companions[
        original_editorial_comparison_key(parsed)
    ] += 1
    stats["original_editorial_selection_observations"] += 1


def record_original_editorial_shadow(
    message: str,
    timestamp: datetime,
    level: str,
    *,
    observations: List[Dict[str, Any]],
    pending_shadow_companions: Counter,
    stats: Counter,
    errors: List[Dict[str, Any]],
    parse_json_object: Callable[..., Dict[str, Any]],
    short_text: Callable[[Any, int], str],
    source_ref: Callable[[], Dict[str, Any]],
) -> None:
    """Record one matched ORIGINAL_EDITORIAL_SHADOW_RESULT observation.

    Mutate only the parser result and supplied invocation-local collections.
    Encoding/parsing failures retain their diagnostics and lazy source reference;
    comparison-key and summary failures remain outside that exception boundary.
    """
    raw = message.split("ORIGINAL_EDITORIAL_SHADOW_RESULT ", 1)[1].strip()
    try:
        parsed = parse_json_object(
            raw.encode("utf-8"),
            label="ORIGINAL_EDITORIAL_SHADOW_RESULT",
        )
    except Exception as exc:
        errors.append({
            "time": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
            "level": level,
            "message": f"Malformed ORIGINAL_EDITORIAL_SHADOW_RESULT: {exc}: {short_text(raw, 240)}",
            "source_refs": [source_ref()],
        })
        stats["original_editorial_shadow_parse_errors"] += 1
        return
    parsed["time"] = timestamp.strftime("%Y-%m-%d %H:%M:%S")
    parsed["event_mode"] = "shadow"
    companion_key = original_editorial_comparison_key(parsed)
    if pending_shadow_companions[companion_key] > 0:
        pending_shadow_companions[companion_key] -= 1
        stats["original_editorial_shadow_companion_observations"] += 1
        stats["original_editorial_shadow_observations"] += 1
        return
    observations.append(parsed)
    stats["original_editorial_shadow_observations"] += 1


def original_editorial_comparison_key(item: Dict[str, Any]) -> Tuple[Any, ...]:
    """Return the fields shared by an active result and its shadow companion."""
    return (
        item.get("quote_hash"),
        item.get("line_no"),
        item.get("selection_phase"),
        item.get("production_source"),
        item.get("production_winner"),
        item.get("shadow_original_winner"),
    )


def original_editorial_shadow_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return the active and legacy original-editorial summary."""
    total = len(events)
    selection_events = [item for item in events if item.get("event_mode") == "selection"]
    legacy_shadow_events = [item for item in events if item.get("event_mode") != "selection"]
    selection_applied = [item for item in selection_events if item.get("selection_applied") is True]
    selection_not_applied = [item for item in selection_events if item.get("selection_applied") is not True]
    selected_winner_changes = [
        item
        for item in selection_applied
        if str(item.get("selected_winner") or "") != str(item.get("production_winner") or "")
    ]
    comparable_originals = [item for item in events if item.get("production_source") == "original"]
    production_original = len(comparable_originals)
    production_generated = sum(1 for item in events if item.get("production_source") == "generated")
    changed = [item for item in comparable_originals if item.get("winner_changed") is True]
    ranks = [int(item["production_shadow_rank"]) for item in events if item.get("production_shadow_rank") is not None]
    rank1 = sum(1 for rank in ranks if rank == 1)
    rank2_3 = sum(1 for rank in ranks if rank in {2, 3})
    rank10_or_worse = sum(1 for rank in ranks if rank >= 10)
    adjustments = []
    cap_hits = 0
    affinity = Counter()
    dimensions = Counter()
    winners = Counter()
    for item in events:
        winners[str(item.get("shadow_original_winner") or "")] += 1
        value = item.get("shadow_winner_editorial_adjustment")
        if isinstance(value, (int, float)):
            adjustments.append(abs(float(value)))
        if item.get("cap_hit"):
            cap_hits += 1
        affinity.update(str(value) for value in item.get("affinity_matches") or [])
        dimensions.update(str(value) for value in item.get("dimension_matches") or [])
    return {
        "observations": total,
        "active_selection_observations": len(selection_events),
        "legacy_shadow_observations": len(legacy_shadow_events),
        "selector_applied_observations": len(selection_applied),
        "selector_not_applied_observations": len(selection_not_applied),
        "selected_winner_changes": len(selected_winner_changes),
        "production_original": production_original,
        "production_generated": production_generated,
        "comparable_original_observations": production_original,
        "winner_changes": len(changed),
        "winner_change_percent": (len(changed) / production_original * 100.0) if production_original else 0.0,
        "average_production_winner_shadow_rank": (sum(ranks) / len(ranks)) if ranks else None,
        "median_production_winner_shadow_rank": statistics.median(ranks) if ranks else None,
        "worst_production_winner_shadow_rank": max(ranks) if ranks else None,
        "production_rank_1": rank1,
        "production_rank_2_or_3": rank2_3,
        "production_rank_10_or_worse": rank10_or_worse,
        "severe_disagreements": [
            item for item in events
            if type(item.get("production_shadow_rank")) is int
            and item["production_shadow_rank"] >= 10
        ],
        "shadow_winner_differed": len(changed),
        "most_frequent_shadow_winners": most_common_with_cutoff_ties(winners),
        "most_frequent_affinity_concepts": affinity.most_common(8),
        "most_frequent_active_dimensions": dimensions.most_common(8),
        "average_abs_editorial_adjustment": (sum(adjustments) / len(adjustments)) if adjustments else 0.0,
        "max_abs_editorial_adjustment": max(adjustments) if adjustments else 0.0,
        "cap_hit_count": cap_hits,
    }
