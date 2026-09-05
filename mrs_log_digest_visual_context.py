"""Pure validation and correlation of supplied reply visual-context observations.

Outer structured JSON parsing, event insertion and publication authority remain
with the digest coordinator. This module performs no runtime I/O.
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from typing import Any, Dict, List, Optional, Tuple

from mrs_log_digest_values import SHA256_LOWER_RE, _normalise_lane


REPLY_VISUAL_DESCRIPTION_EVENT_FIELDS = frozenset(
    {
        "analysis",
        "analysis_schema_version",
        "description_sha256",
        "event",
        "lane",
        "status",
        "supplied_image_count",
        "target_id",
        "visual_analysis_call_count",
    }
)
REPLY_VISUAL_DESCRIPTION_STATUSES = frozenset(
    {
        "analysed",
        "provider_error",
        "invalid_response",
        "invalid_supplied_media",
        "paused",
    }
)
REPLY_VISUAL_DESCRIPTION_MAX_SUPPORTED_IMAGES = 2
REPLY_VISUAL_DESCRIPTION_MAX_REPORTED_IMAGES = 2_147_483_647
REPLY_VISUAL_DESCRIPTION_MAX_CALL_COUNT = 1
REPLY_VISUAL_DESCRIPTION_MAX_SCHEMA_VERSION = 2_147_483_647


def parse_reply_visual_description_event(
    event: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    """Validate safe metadata and any bounded retained visual analysis."""
    if set(event) - REPLY_VISUAL_DESCRIPTION_EVENT_FIELDS:
        return None
    if event.get("event") != "reply_visual_description":
        return None
    target_id = event.get("target_id")
    if not isinstance(target_id, str) or not target_id.strip():
        return None
    if not isinstance(event.get("lane"), str):
        return None
    lane = _normalise_lane(event.get("lane"))
    if lane == "unavailable":
        return None
    status = event.get("status")
    if status not in REPLY_VISUAL_DESCRIPTION_STATUSES:
        return None
    supplied_image_count = event.get("supplied_image_count")
    call_count = event.get("visual_analysis_call_count")
    schema_version = event.get("analysis_schema_version")
    if (
        type(supplied_image_count) is not int
        or supplied_image_count < 0
        or supplied_image_count > REPLY_VISUAL_DESCRIPTION_MAX_REPORTED_IMAGES
        or type(call_count) is not int
        or call_count < 0
        or call_count > REPLY_VISUAL_DESCRIPTION_MAX_CALL_COUNT
        or type(schema_version) is not int
        or schema_version <= 0
        or schema_version > REPLY_VISUAL_DESCRIPTION_MAX_SCHEMA_VERSION
    ):
        return None
    has_analysis = "analysis" in event
    raw_hash = event.get("description_sha256")
    if raw_hash in (None, ""):
        description_sha256: Optional[str] = None
    elif isinstance(raw_hash, str) and SHA256_LOWER_RE.fullmatch(raw_hash):
        description_sha256 = raw_hash
    elif status == "analysed" and has_analysis:
        description_sha256 = None
    else:
        return None

    if status == "analysed":
        if (
            not 1
            <= supplied_image_count
            <= REPLY_VISUAL_DESCRIPTION_MAX_SUPPORTED_IMAGES
            or call_count != 1
            or (not has_analysis and description_sha256 is None)
        ):
            return None
    elif description_sha256 is not None:
        return None
    elif status in {"provider_error", "invalid_response"}:
        if (
            not 1
            <= supplied_image_count
            <= REPLY_VISUAL_DESCRIPTION_MAX_SUPPORTED_IMAGES
            or call_count != 1
        ):
            return None
    elif status == "paused":
        if (
            not 1
            <= supplied_image_count
            <= REPLY_VISUAL_DESCRIPTION_MAX_SUPPORTED_IMAGES
            or call_count != 0
        ):
            return None
    elif status == "invalid_supplied_media" and call_count != 0:
        return None

    parsed: Dict[str, Any] = {
        "analysis_schema_version": schema_version,
        "description_sha256": description_sha256,
        "lane": lane,
        "status": status,
        "supplied_image_count": supplied_image_count,
        "target_id": target_id.strip(),
        "visual_analysis_call_count": call_count,
    }
    if not has_analysis:
        return parsed
    if status != "analysed":
        parsed["analysis_anomaly"] = "unexpected_analysis_for_non_success_status"
        return parsed

    raw_analysis = event.get("analysis")
    if not isinstance(raw_analysis, dict):
        parsed["analysis_anomaly"] = "malformed_analysis"
        return parsed
    try:
        # Legacy visual-description events are retained only so old logs remain
        # readable.  The retired conversational module is deliberately not an
        # operational dependency of the version-3 digest.
        analysis = json.loads(
            json.dumps(raw_analysis, ensure_ascii=False, allow_nan=False)
        )
        if len(json.dumps(analysis, ensure_ascii=False)) > 25_000:
            raise ValueError("legacy visual analysis exceeds digest bound")
    except (json.JSONDecodeError, TypeError, ValueError):
        parsed["analysis_anomaly"] = "malformed_analysis"
        return parsed

    canonical_analysis = json.dumps(
        analysis,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    calculated_sha256 = hashlib.sha256(
        canonical_analysis.encode("utf-8")
    ).hexdigest()
    if description_sha256 is None:
        integrity = "unavailable_hash"
        parsed["analysis_anomaly"] = "description_hash_unavailable"
    elif calculated_sha256 == description_sha256:
        integrity = "verified"
    else:
        integrity = "mismatch"
        parsed["analysis_anomaly"] = "description_hash_mismatch"
    parsed.update(
        {
            "analysis": analysis,
            "analysis_integrity": integrity,
            "calculated_description_sha256": calculated_sha256,
        }
    )
    return parsed


def reply_visual_context_report(
    media_events: List[Dict[str, Any]],
    visual_events: List[Dict[str, Any]],
    *,
    malformed_event_count: int,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """Correlate window-local native-media and preliminary analysis evidence."""
    grouped_media: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for index, item in enumerate(media_events):
        target_id = str(item.get("target_id") or "").strip()
        if not target_id:
            continue
        photos_text = str(item.get("photos") or "")
        photo_count = int(photos_text) if photos_text.isdecimal() else 0
        key = (_normalise_lane(item.get("lane")), target_id)
        grouped_media.setdefault(key, []).append(
            {
                "index": index,
                "mode": str(item.get("mode") or ""),
                "photo_count": photo_count,
                "status": str(item.get("status") or "unavailable"),
                "time": str(item.get("time") or ""),
            }
        )

    grouped_visual: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for index, item in enumerate(visual_events):
        key = (
            _normalise_lane(item.get("lane")),
            str(item.get("target_id") or ""),
        )
        if not key[1]:
            continue
        grouped_visual.setdefault(key, []).append({**item, "index": index})

    rows: List[Dict[str, Any]] = []
    for lane, target_id in sorted(set(grouped_media) | set(grouped_visual)):
        collections = sorted(
            grouped_media.get((lane, target_id), []),
            key=lambda item: (str(item.get("time") or ""), int(item["index"])),
        )
        visual_events_for_target = sorted(
            grouped_visual.get((lane, target_id), []),
            key=lambda item: (str(item.get("time") or ""), int(item["index"])),
        )
        latest_collection = collections[-1] if collections else None
        latest_visual_event = (
            visual_events_for_target[-1] if visual_events_for_target else None
        )
        analysis_attempt_count = sum(
            int(item.get("visual_analysis_call_count") or 0)
            for item in visual_events_for_target
        )
        successful = [
            item
            for item in visual_events_for_target
            if item.get("status") == "analysed"
        ]
        successful_hashes = sorted(
            {
                str(item["description_sha256"])
                for item in successful
                if item.get("description_sha256")
            }
        )
        visual_description_results: List[Dict[str, Any]] = []
        for item in successful:
            result = {
                "analysis_schema_version": item.get("analysis_schema_version"),
                "description_sha256": item.get("description_sha256"),
                "status": "analysed",
                "time": item.get("time"),
            }
            for field in (
                "analysis",
                "analysis_anomaly",
                "analysis_integrity",
                "calculated_description_sha256",
            ):
                if field in item:
                    result[field] = item[field]
            visual_description_results.append(result)
        supplied_observed = any(
            item.get("status") == "supplied" and item.get("photo_count", 0) > 0
            for item in collections
        )
        native_photo_count_max = max(
            [int(item.get("photo_count") or 0) for item in collections]
            + [
                int(item.get("supplied_image_count") or 0)
                for item in visual_events_for_target
            ]
            + [0]
        )
        collected_native_photo_count_max = max(
            [
                int(item.get("photo_count") or 0)
                for item in collections
                if item.get("status") == "supplied"
            ]
            + [0]
        )
        if successful:
            analysis_observation_status = "analysed"
        elif analysis_attempt_count > 0:
            analysis_observation_status = "attempted_not_analysed"
        elif visual_events_for_target:
            analysis_observation_status = "not_attempted"
        elif supplied_observed:
            analysis_observation_status = "not_observed_in_selected_window"
        else:
            analysis_observation_status = "not_applicable"

        latest_collection_status = (
            str(latest_collection.get("status") or "unavailable")
            if latest_collection
            else "not_observed_in_selected_window"
        )
        if not collections and analysis_attempt_count > 0:
            correlation_status = "analysis_observed_collection_not_observed_in_selected_window"
        elif not collections and visual_events_for_target:
            correlation_status = "visual_event_observed_collection_not_observed_in_selected_window"
        elif latest_collection_status == "unavailable":
            correlation_status = "collection_unavailable"
        elif supplied_observed and successful:
            correlation_status = "collection_supplied_analysis_analysed"
        elif supplied_observed and analysis_attempt_count > 0:
            correlation_status = "collection_supplied_analysis_unsuccessful"
        elif supplied_observed and visual_events_for_target:
            correlation_status = "collection_supplied_analysis_not_attempted"
        elif supplied_observed:
            correlation_status = "collection_supplied_analysis_not_observed_in_selected_window"
        elif visual_events_for_target:
            correlation_status = "collection_observed_analysis_observed"
        else:
            correlation_status = "collection_observed_analysis_not_applicable"

        rows.append(
            {
                "analysis_observation_status": analysis_observation_status,
                "analysis_schema_versions": sorted(
                    {
                        int(item["analysis_schema_version"])
                        for item in visual_events_for_target
                    }
                ),
                "collected_native_photo_count_max": collected_native_photo_count_max,
                "collection_observation_count": len(collections),
                "collection_observation_status": (
                    "observed" if collections else "not_observed_in_selected_window"
                ),
                "collection_status": latest_collection_status,
                "correlation_status": correlation_status,
                "distinct_successful_description_count": len(successful_hashes),
                "lane": lane,
                "latest_successful_description_sha256": (
                    successful[-1].get("description_sha256") if successful else None
                ),
                "latest_visual_analysis_status": (
                    str(latest_visual_event.get("status"))
                    if latest_visual_event
                    else "not_observed_in_selected_window"
                ),
                "native_photo_count_max": native_photo_count_max,
                "successful_analysis_count": len(successful),
                "successful_description_sha256s": successful_hashes,
                "target_id": target_id,
                "visual_analysis_attempt_count": analysis_attempt_count,
                "visual_analysis_call_count": analysis_attempt_count,
                "visual_analysis_event_count": len(visual_events_for_target),
                "visual_description_results": visual_description_results,
            }
        )

    status_counts = Counter(
        str(item.get("status") or "unavailable") for item in visual_events
    )
    analysed_events = [
        item for item in visual_events if item.get("status") == "analysed"
    ]
    integrity_counts = Counter(
        str(item["analysis_integrity"])
        for item in analysed_events
        if item.get("analysis_integrity")
    )
    summary = {
        "malformed_visual_description_event_count": malformed_event_count,
        "visual_description_analysis_anomaly_count": sum(
            bool(item.get("analysis_anomaly")) for item in visual_events
        ),
        "retained_visual_description_count": sum(
            isinstance(item.get("analysis"), dict) for item in analysed_events
        ),
        "legacy_hash_only_visual_description_count": sum(
            "analysis" not in item and not item.get("analysis_anomaly")
            for item in analysed_events
        ),
        "visual_description_integrity_counts": dict(sorted(integrity_counts.items())),
        "target_count": len(rows),
        "targets_with_analysis_but_no_collection_observation_in_selected_window": sum(
            row["collection_observation_status"] == "not_observed_in_selected_window"
            and row["visual_analysis_attempt_count"] > 0
            for row in rows
        ),
        "targets_with_visual_events_but_no_collection_observation_in_selected_window": sum(
            row["collection_observation_status"] == "not_observed_in_selected_window"
            and row["visual_analysis_event_count"] > 0
            for row in rows
        ),
        "targets_with_attempted_but_unsuccessful_analysis": sum(
            row["visual_analysis_attempt_count"] > 0
            and row["successful_analysis_count"] == 0
            for row in rows
        ),
        "targets_with_visual_events_but_no_analysis_call": sum(
            row["visual_analysis_event_count"] > 0
            and row["visual_analysis_attempt_count"] == 0
            for row in rows
        ),
        "targets_with_collection_unavailable": sum(
            row["collection_status"] == "unavailable" for row in rows
        ),
        "targets_with_more_than_one_analysis_attempt": sum(
            row["visual_analysis_attempt_count"] > 1 for row in rows
        ),
        "targets_with_more_than_one_distinct_successful_description_hash": sum(
            row["distinct_successful_description_count"] > 1 for row in rows
        ),
        "targets_with_no_analysis_event_observed_in_selected_window": sum(
            row["analysis_observation_status"] == "not_observed_in_selected_window"
            for row in rows
        ),
        "targets_with_successful_visual_analysis": sum(
            row["successful_analysis_count"] > 0 for row in rows
        ),
        "targets_with_supplied_native_photos": sum(
            row["collected_native_photo_count_max"] > 0 for row in rows
        ),
        "visual_analysis_call_count": sum(
            int(item.get("visual_analysis_call_count") or 0)
            for item in visual_events
        ),
        "visual_analysis_attempt_count": sum(
            int(item.get("visual_analysis_call_count") or 0)
            for item in visual_events
        ),
        "visual_analysis_event_count": len(visual_events),
        "visual_analysis_status_counts": dict(sorted(status_counts.items())),
        "successful_visual_analysis_event_count": sum(
            item.get("status") == "analysed" for item in visual_events
        ),
    }
    return summary, rows
