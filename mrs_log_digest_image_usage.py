"""Pure image utilisation, current-cycle runway and selection summaries.

Callers supply prepared pool, post-rate, configuration and event observations.
History scans, snapshot reads, clock selection and report assembly remain with
their existing owners; this module performs no runtime I/O.
"""
from __future__ import annotations

import statistics
from datetime import datetime
from typing import Any, Dict, List


def generated_image_utilisation(pool: Dict[str, Any], rates: Dict[str, Any], limit: int = 10) -> Dict[str, Any]:
    """Summarise successful generated posts observed in the bounded history scan."""
    active = set(pool.get("active_basenames") or [])
    quarantined = set(pool.get("quarantined_basenames") or [])
    by_image: Dict[str, List[datetime]] = {}
    for post in rates.get("successful_regular_posts") or []:
        name = str(post.get("basename") or "")
        if name not in active or not post.get("generated"):
            continue
        try:
            timestamp = datetime.fromisoformat(str(post.get("timestamp") or ""))
        except (TypeError, ValueError):
            continue
        by_image.setdefault(name, []).append(timestamp)

    counts = {name: len(timestamps) for name, timestamps in by_image.items()}
    lasts = {name: max(timestamps) for name, timestamps in by_image.items()}
    used = set(counts)
    never = active - used
    total = sum(counts.values())
    ranked = sorted(used, key=lambda name: (-counts[name], -lasts[name].timestamp(), name))
    longest = sorted(active, key=lambda name: (name in used, lasts.get(name, datetime.min), name))
    origins = pool.get("active_origin_quote_hashes") or {}

    def row(name: str) -> Dict[str, Any]:
        return {"image": name, "successful_posts": counts.get(name, 0),
                "last_successful_post": lasts[name].isoformat(sep=" ") if name in lasts else None}

    top_count = sum(counts[name] for name in ranked[:10])
    current_used = int(pool.get("active_previously_used", 0) or 0)
    current_unused = int(pool.get("active_never_used", 0) or 0)
    observed_percentage = (len(used) / len(active) * 100.0) if active else None
    return {
        "usage_metric_schema_version": 2,
        "active_generated_images": len(active),
        "active_images_used_in_observed_logs": len(used),
        "active_images_not_seen_in_observed_logs": len(never),
        "active_pool_observed_usage_percentage": observed_percentage,
        # Compatibility aliases retained for machine-readable consumers. These
        # have always described the bounded structured-log scan, not all time.
        "active_images_used_ever": len(used),
        "active_images_never_used": len(never),
        "active_pool_ever_used_percentage": observed_percentage,
        "deprecated_metric_aliases": {
            "active_images_used_ever": "active_images_used_in_observed_logs",
            "active_images_never_used": "active_images_not_seen_in_observed_logs",
            "active_pool_ever_used_percentage": "active_pool_observed_usage_percentage",
        },
        "deprecated_alias_removal_plan": "remove only in a future major digest schema version",
        "active_images_used_in_current_cycle": current_used,
        "active_images_unused_in_current_cycle": current_unused,
        "total_successful_generated_posts_observed": total,
        "median_successful_posts_per_used_image": statistics.median(counts.values()) if counts else None,
        "maximum_successful_posts_for_one_image": max(counts.values()) if counts else 0,
        "top_10_share_of_successful_generated_posts": (top_count / total * 100.0) if total else None,
        "most_frequently_used": [row(name) for name in ranked[:limit]],
        "never_used": [{"image": name, "origin_quote_hash": origins.get(name)} for name in sorted(never)[:limit]],
        "never_used_total": len(never),
        "unused_longest": [row(name) for name in longest[:limit]],
        "unused_longest_total": len(longest),
        "quarantined_generated_images": len(quarantined),
        "history_coverage_start": rates.get("coverage_start"),
        "history_coverage_end": rates.get("coverage_end"),
        "history_scope": "bounded available structured production logs; not guaranteed all-time",
    }


def generated_pool_runway(pool: Dict[str, Any], rates: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
    """Return the generated pool runway."""
    remaining = max(0, int(pool.get("active_never_used", 0) or 0))
    if config.get("_runway_config_error"):
        reason = str(config["_runway_config_error"])
        unavailable = {"available": False, "reason": reason}
        return {
            "remaining_active_generated_in_current_cycle": remaining,
            "primary_basis": None,
            "observed": {"trailing_7d": dict(unavailable), "trailing_30d": dict(unavailable)},
            "schedule": dict(unavailable),
            "estimate_semantics": "current image cycle, not all-time posting history",
        }
    enabled = config.get("ENABLE_GENERATED_IMAGE_POOL")
    schedule: Dict[str, Any] = {"available": False}
    try:
        sleep_min, sleep_max = float(config["POST_SLEEP_MIN"]), float(config["POST_SLEEP_MAX"])
        spacing = int(config["GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN"])
        if isinstance(enabled, str): enabled = enabled.lower() in {"1", "true", "yes", "on"}
        if sleep_min <= 0 or sleep_max < sleep_min or spacing < 0: raise ValueError("invalid schedule values")
        midpoint = (sleep_min + sleep_max) / 2.0
        regular_per_day = 86400.0 / midpoint
        maximum_share = 1.0 / (spacing + 1)
        generated_per_day = regular_per_day * maximum_share
        schedule = {"available": bool(enabled), "midpoint_regular_interval_seconds": midpoint, "regular_posts_per_day": regular_per_day,
                    "maximum_generated_share_percent": maximum_share * 100.0, "generated_posts_per_day": generated_per_day,
                    "regular_posts_to_cycle_exhaustion": remaining * (spacing + 1), "days_to_cycle_exhaustion": remaining / generated_per_day if generated_per_day > 0 else None}
    except Exception as exc:
        schedule = {"available": False, "reason": f"malformed or unavailable schedule config: {exc}"}

    observed: Dict[str, Any] = {}
    primary = None
    for label in ("trailing_7d", "trailing_30d"):
        window = (rates.get("windows") or {}).get(label) or {}
        generated_per_day = window.get("generated_posts_per_day")
        share = window.get("generated_share_percent")
        coverage_quality = window.get("coverage_quality")
        reliable = ((window.get("coverage_days") or 0) >= 1.0 and (window.get("regular_posts") or 0) > 0
                    and (window.get("generated_posts") or 0) > 0 and coverage_quality in (None, "continuous"))
        estimate = {"available": reliable, "reason": None}
        if reliable:
            share_fraction = float(share) / 100.0
            estimate.update({"regular_posts_to_cycle_exhaustion": int(round(remaining / share_fraction)) if share_fraction > 0 else None,
                             "days_to_cycle_exhaustion": remaining / float(generated_per_day), "generated_posts_per_day": generated_per_day})
            if primary is None: primary = label
        else:
            estimate["reason"] = "insufficient clean regular/generated posts, less than one day of coverage, or material log gaps"
        observed[label] = estimate
    if remaining == 0:
        primary = "complete"
        for estimate in observed.values(): estimate.update({"available": True, "reason": None, "regular_posts_to_cycle_exhaustion": 0, "days_to_cycle_exhaustion": 0.0})
    elif not enabled:
        primary = None
        for estimate in observed.values(): estimate.update({"available": False, "reason": "generated image pool disabled"})
    return {"remaining_active_generated_in_current_cycle": remaining, "primary_basis": primary or ("schedule_model" if schedule.get("available") else None),
            "observed": observed, "schedule": schedule, "estimate_semantics": "current image cycle, not all-time posting history"}


def regular_image_usage_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return the regular image usage summary."""
    total = len(events)
    original = sum(1 for item in events if item.get("source") == "original")
    generated = sum(1 for item in events if item.get("source") == "generated")
    origin_matches = sum(1 for item in events if item.get("source") == "generated" and item.get("origin_quote_match") == "true")
    cross_quote = generated - origin_matches
    made_with_ai_true = sum(1 for item in events if item.get("made_with_ai") == "true")
    made_with_ai_false = sum(1 for item in events if item.get("made_with_ai") == "false")
    made_with_ai_unknown = total - made_with_ai_true - made_with_ai_false
    generated_share = (generated / total * 100.0) if total else 0.0
    origin_match_share = (origin_matches / generated * 100.0) if generated else 0.0
    return {
        "selections": total,
        "original": original,
        "generated": generated,
        "origin_matches": origin_matches,
        "cross_quote": cross_quote,
        "made_with_ai_true": made_with_ai_true,
        "made_with_ai_false": made_with_ai_false,
        "made_with_ai_unknown": made_with_ai_unknown,
        "generated_share": generated_share,
        "origin_match_share": origin_match_share,
    }
