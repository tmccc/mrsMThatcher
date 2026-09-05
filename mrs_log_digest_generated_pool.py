"""Read-only generated-image pool snapshots, post rates and runway inputs.

Paths, strict JSON parsers, file hashing and time dependencies are supplied by
callers. Import performs no runtime reads, directory scans, home lookup or
service initialisation. Post-rate readers/parsers and runway defaults are current
named inputs. Utilisation and runway calculation consume these observations in
the image-usage owner; report assembly stays in the digest. This module imports
only the shared values leaf, never the digest or bot.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from mrs_log_digest_values import GENERATED_POLICIES


GENERATED_BASENAME_RE = re.compile(r"tg_([0-9a-f]{64})\.png\Z")
GENERATED_ANALYSIS_SCHEMA_VERSION = 3
GENERATED_ANALYSIS_KIND = "images"
GENERATED_AUDIT_SCHEMA_VERSION = 1
GENERATED_AUDIT_KIND = "generated_image_identity_dependence_audit"


def generated_pool_health_snapshot(
    base_dir: Path,
    now: Optional[datetime] = None,
    *,
    parse_json_object: Callable[..., Dict[str, Any]],
    parse_json_value: Callable[..., Any],
    sha256_file: Callable[[Path], str],
    clock_now: Callable[[], datetime],
    fromisoformat: Callable[[str], datetime],
) -> Dict[str, Any]:
    """Read active images, metadata, curation records and used-image history.

    Reads select the existing locations relative to the explicit base directory
    using the supplied parsers and file-hash reader. When ``now`` is absent,
    ``clock_now`` is sampled after active-image validation, before curation reads.
    Naive times retain host-local timezone handling; the supplied ISO parser
    handles transaction timestamps. No files are mutated.
    """
    base_dir = base_dir.resolve()
    generated_dir = base_dir / "generated_review_approved_images"
    quarantine_dir = base_dir / "generated_image_quarantine" / "transactions"
    analysis_path = base_dir / "generated_image_analysis.json"
    audit_path = base_dir / "generated_image_identity_dependence_audit.json"
    used_path = base_dir / "images_used.json"
    warnings: List[Dict[str, str]] = []

    def warning(kind: str, basename: str, detail: str) -> None:
        warnings.append({"kind": kind, "basename": basename, "detail": detail})

    active_paths: Dict[str, Path] = {}
    try:
        for path in generated_dir.iterdir():
            if not path.is_file() or path.suffix.lower() != ".png":
                continue
            if not GENERATED_BASENAME_RE.fullmatch(path.name):
                warning("invalid_active_basename", path.name, "expected tg_<64 lowercase hex>.png")
                continue
            if path.name in active_paths:
                warning("duplicate_active_basename", path.name, "duplicate basename")
            active_paths[path.name] = path
    except FileNotFoundError:
        warning("active_pool_unavailable", "", str(generated_dir))
    except Exception as exc:
        warning("active_pool_error", "", str(exc))

    analysis: Dict[str, Any] = {}
    audit: Dict[str, Any] = {}
    parsed = {"analysis": False, "audit": False}
    for label, path, target in (("analysis", analysis_path, "analysis"), ("audit", audit_path, "audit")):
        try:
            value = parse_json_object(
                path.read_bytes(), label=f"generated image {label}"
            )
            if target == "analysis": analysis = value
            else: audit = value
            parsed[label] = True
        except FileNotFoundError:
            warning(f"{label}_unavailable", "", str(path))
        except Exception as exc:
            warning(f"{label}_malformed", "", str(exc))

    if parsed["analysis"]:
        if analysis.get("schema_version") != GENERATED_ANALYSIS_SCHEMA_VERSION: warning("analysis_schema_invalid", "", repr(analysis.get("schema_version")))
        if analysis.get("analysis_kind") != GENERATED_ANALYSIS_KIND: warning("analysis_kind_invalid", "", repr(analysis.get("analysis_kind")))
        if not isinstance(analysis.get("path_index"), dict): warning("analysis_path_index_invalid", "", "expected object")
        if not isinstance(analysis.get("items"), dict): warning("analysis_items_invalid", "", "expected object")
    if parsed["audit"]:
        if audit.get("schema_version") != GENERATED_AUDIT_SCHEMA_VERSION: warning("audit_schema_invalid", "", repr(audit.get("schema_version")))
        if audit.get("analysis_kind") != GENERATED_AUDIT_KIND: warning("audit_kind_invalid", "", repr(audit.get("analysis_kind")))
        if not isinstance(audit.get("items"), dict): warning("audit_items_invalid", "", "expected object")

    analysis_index = analysis.get("path_index") if isinstance(analysis.get("path_index"), dict) else {}
    analysis_items = analysis.get("items") if isinstance(analysis.get("items"), dict) else {}
    audit_items = audit.get("items") if isinstance(audit.get("items"), dict) else {}
    active_names = set(active_paths)
    analysis_names = set(str(value) for value in analysis_index)
    audit_names = set(str(value) for value in audit_items)
    for name in sorted(active_names - analysis_names): warning("missing_analysis", name, "active image absent from generated analysis path_index")
    for name in sorted(analysis_names - active_names): warning("unexpected_analysis", name, "analysis record has no active image")
    for name in sorted(active_names - audit_names): warning("missing_audit", name, "active image absent from identity audit")
    for name in sorted(audit_names - active_names): warning("unexpected_audit", name, "audit record has no active image")

    valid_hashes = 0
    policy_counts = Counter()
    for name, path in sorted(active_paths.items()):
        try: actual_hash = sha256_file(path)
        except Exception as exc:
            warning("hash_error", name, str(exc)); continue
        expected_analysis = analysis_index.get(name)
        audit_record = audit_items.get(name) if isinstance(audit_items.get(name), dict) else {}
        if name in audit_items and not isinstance(audit_items.get(name), dict): warning("audit_item_invalid", name, "expected object")
        expected_audit = audit_record.get("image_sha256")
        if audit_record and str(audit_record.get("basename") or name) != name: warning("audit_basename_mismatch", name, repr(audit_record.get("basename")))
        item = analysis_items.get(str(expected_analysis))
        if expected_analysis is not None and not re.fullmatch(r"[0-9a-f]{64}", str(expected_analysis)): warning("analysis_hash_invalid", name, repr(expected_analysis))
        if expected_audit is not None and not re.fullmatch(r"[0-9a-f]{64}", str(expected_audit)): warning("audit_hash_invalid", name, repr(expected_audit))
        if expected_analysis != actual_hash or expected_audit != actual_hash or not isinstance(item, dict):
            warning("hash_mismatch", name, f"actual={actual_hash} analysis={expected_analysis} audit={expected_audit}")
        else:
            valid_hashes += 1
        match = GENERATED_BASENAME_RE.fullmatch(name)
        if str(audit_record.get("origin_quote_hash") or "").lower() != (match.group(1) if match else ""):
            warning("origin_hash_mismatch", name, str(audit_record.get("origin_quote_hash") or "missing"))
        identity = audit_record.get("analysis") if isinstance(audit_record.get("analysis"), dict) else {}
        if name in audit_names:
            missing_audit = [key for key in ("image_sha256", "origin_quote_hash", "analysis") if key not in audit_record]
            if missing_audit: warning("audit_item_missing_fields", name, ",".join(missing_audit))
            if not isinstance(audit_record.get("analysis"), dict): warning("audit_analysis_invalid", name, "expected object")
            else:
                required_identity = (
                    "recommended_cross_quote_policy", "identity_dependence", "contains_specific_intended_person",
                    "recognisability_to_typical_viewer", "recognisability_to_politically_interested_viewer",
                    "meaning_retention_without_identity", "origin_quote_suitability", "recommended_penalty_strength", "confidence",
                )
                missing_identity = [key for key in required_identity if key not in identity]
                if missing_identity: warning("audit_analysis_missing_fields", name, ",".join(missing_identity))
                if identity.get("identity_dependence") not in {"none", "low", "medium", "high", "essential"}: warning("audit_identity_dependence_invalid", name, repr(identity.get("identity_dependence")))
                if type(identity.get("contains_specific_intended_person")) is not bool: warning("audit_person_flag_invalid", name, repr(identity.get("contains_specific_intended_person")))
                for numeric_key in required_identity[3:]:
                    value = identity.get(numeric_key)
                    maximum = 1.0 if numeric_key == "confidence" else 10.0
                    if (isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value))
                            or not 0.0 <= float(value) <= maximum):
                        warning("audit_numeric_invalid", name, f"{numeric_key}={value!r}")
        policy = identity.get("recommended_cross_quote_policy")
        if policy in GENERATED_POLICIES: policy_counts[str(policy)] += 1
        elif name in audit_names: warning("invalid_policy", name, repr(policy))

    now = now or clock_now().astimezone()
    if now.tzinfo is None:
        now = now.astimezone()
    completed_quarantines = 0
    completed_restores = 0
    latest_quarantine: Optional[Dict[str, Any]] = None
    latest_restore: Optional[Dict[str, Any]] = None
    quarantined: Dict[str, Dict[str, Any]] = {}
    curation_events: List[Tuple[str, datetime, set[str]]] = []
    if quarantine_dir.exists():
        for manifest_path in sorted(quarantine_dir.glob("*/manifest.json")):
            try:
                transaction = parse_json_object(
                    manifest_path.read_bytes(),
                    label="generated image curation manifest",
                )
            except Exception as exc:
                warning("transaction_malformed", manifest_path.parent.name, str(exc)); continue
            if transaction.get("status") != "completed":
                continue
            kind = transaction.get("kind")
            image_values = transaction.get("images")
            if not isinstance(image_values, list):
                warning(
                    "transaction_schema_invalid",
                    manifest_path.parent.name,
                    "images must be a list",
                )
                continue
            try:
                timestamp = fromisoformat(str(transaction.get("created_at") or "").replace("Z", "+00:00"))
                if timestamp.tzinfo is None: timestamp = timestamp.replace(tzinfo=now.tzinfo)
                timestamp = timestamp.astimezone(now.tzinfo)
                event_names = {str(item.get("basename") if isinstance(item, dict) else item) for item in image_values}
                event_names.discard("")
                if kind in {"quarantine", "restore"}: curation_events.append((str(kind), timestamp, event_names))
            except Exception as exc:
                warning("transaction_timestamp_malformed", manifest_path.parent.name, str(exc))
            if kind == "quarantine":
                completed_quarantines += 1
                if latest_quarantine is None or str(transaction.get("created_at") or "") > str(latest_quarantine.get("created_at") or ""):
                    latest_quarantine = transaction
                for entry in image_values:
                    if not isinstance(entry, dict): continue
                    name = str(entry.get("basename") or "")
                    image_path = manifest_path.parent / "images" / name
                    if image_path.is_file(): quarantined[name] = {"entry": entry, "path": image_path, "transaction_id": transaction.get("transaction_id")}
            elif kind == "restore":
                completed_restores += 1
                if latest_restore is None or str(transaction.get("created_at") or "") > str(latest_restore.get("created_at") or ""):
                    latest_restore = transaction

    quarantined_policy_counts = Counter()
    for name, current in sorted(quarantined.items()):
        entry, path = current["entry"], current["path"]
        required = ("original_relative_path", "sha256", "analysis_record", "audit_record")
        missing = [key for key in required if key not in entry]
        if missing: warning("incomplete_quarantine", name, "missing " + ",".join(missing))
        try:
            if entry.get("sha256") != sha256_file(path): warning("quarantine_hash_mismatch", name, "preserved image hash differs from manifest")
        except Exception as exc: warning("quarantine_hash_error", name, str(exc))
        preserved_audit = entry.get("audit_record") if isinstance(entry.get("audit_record"), dict) else {}
        preserved_identity = preserved_audit.get("analysis") if isinstance(preserved_audit.get("analysis"), dict) else {}
        policy = preserved_identity.get("recommended_cross_quote_policy")
        if policy in GENERATED_POLICIES: quarantined_policy_counts[str(policy)] += 1

    used_generated: set[str] = set()
    try:
        raw_used = parse_json_value(
            used_path.read_bytes(), label="images_used.json"
        )
        if not isinstance(raw_used, list): raise ValueError("expected JSON list")
        used_generated = {str(value) for value in raw_used if GENERATED_BASENAME_RE.fullmatch(str(value))}
    except FileNotFoundError: warning("used_history_unavailable", "", str(used_path))
    except Exception as exc: warning("used_history_malformed", "", str(exc))

    active_used = active_names & used_generated
    quarantine_names = set(quarantined)
    metadata_available = parsed["analysis"] and parsed["audit"]
    structural_warning_kinds = {
        "analysis_schema_invalid", "analysis_kind_invalid", "analysis_path_index_invalid", "analysis_items_invalid",
        "audit_schema_invalid", "audit_kind_invalid", "audit_items_invalid", "audit_item_invalid",
        "audit_item_missing_fields", "audit_analysis_invalid", "audit_analysis_missing_fields",
        "audit_identity_dependence_invalid", "audit_person_flag_invalid", "audit_numeric_invalid", "audit_basename_mismatch",
    }
    coverage_complete = metadata_available and active_names == analysis_names == audit_names and not any(item["kind"] in structural_warning_kinds for item in warnings)
    def curation_days(days: int) -> Dict[str, int]:
        cutoff = now - timedelta(days=days)
        quarantined_count = sum(len(names) for kind, timestamp, names in curation_events if kind == "quarantine" and timestamp >= cutoff)
        restored_count = sum(len(names) for kind, timestamp, names in curation_events if kind == "restore" and timestamp >= cutoff)
        return {"quarantined": quarantined_count, "restored": restored_count, "net_active_change": restored_count - quarantined_count}

    return {
        "snapshot_base_dir": str(base_dir), "active_generated_images": len(active_names), "quarantined_generated_images": len(quarantine_names),
        "total_known_generated_images": len(active_names | quarantine_names), "active_analysis_records": len(analysis_names), "active_identity_records": len(audit_names),
        "metadata_coverage": "complete" if coverage_complete else "inconsistent" if metadata_available else "unavailable",
        "hash_valid": valid_hashes, "hash_total": len(active_names), "hash_validation": "complete" if valid_hashes == len(active_names) and not any(item["kind"].startswith("hash_") for item in warnings) else "inconsistent",
        "active_policy_counts": {policy: policy_counts.get(policy, 0) for policy in GENERATED_POLICIES},
        "quarantined_policy_counts": {policy: quarantined_policy_counts.get(policy, 0) for policy in GENERATED_POLICIES},
        "generated_images_in_used_history": len(used_generated), "active_previously_used": len(active_used), "active_never_used": len(active_names - used_generated),
        "quarantined_previously_used": len(quarantine_names & used_generated), "quarantined_never_used": len(quarantine_names - used_generated),
        "completed_quarantine_transactions": completed_quarantines, "completed_restore_transactions": completed_restores,
        "latest_quarantine": ({"transaction_id": latest_quarantine.get("transaction_id"), "timestamp": latest_quarantine.get("created_at"), "image_count": len(latest_quarantine.get("images") or [])} if latest_quarantine else None),
        "latest_restore": ({"transaction_id": latest_restore.get("transaction_id"), "timestamp": latest_restore.get("created_at"), "image_count": len(latest_restore.get("images") or [])} if latest_restore else None),
        "curation_7d": curation_days(7), "curation_30d": curation_days(30),
        "active_basenames": sorted(active_names), "quarantined_basenames": sorted(quarantine_names),
        "active_origin_quote_hashes": {name: (GENERATED_BASENAME_RE.fullmatch(name).group(1) if GENERATED_BASENAME_RE.fullmatch(name) else None) for name in sorted(active_names)},
        "warnings": warnings, "warning_count": len(warnings), "health": "OK" if not warnings else "WARNING",
    }


def generated_post_rate_history(
    logs: List[Path],
    now: Optional[datetime] = None,
    days: int = 30,
    *,
    read_records: Callable[..., List[Any]],
    try_parse_strict_json_object_from_msg: Callable[[str], Optional[Dict[str, Any]]],
    GENERATED_BASENAME_RE: re.Pattern[str],
    clock_now: Callable[[], datetime],
) -> Dict[str, Any]:
    """Scan bounded production history once and count structured successful regular posts."""
    now = now or clock_now()
    if now.tzinfo is not None: now = now.replace(tzinfo=None)
    cutoff = now - timedelta(days=days)
    records = read_records(logs, cutoff, now)
    marker_fragments = ("/tmp/pytest-", "/tmp/pytest-of-", "mrs_test_mode", "dummy credentials", "127.0.0.1")
    contaminated_seconds = {record.ts for record in records if any(fragment in record.msg.lower() for fragment in marker_fragments)}
    posts: Dict[str, Dict[str, Any]] = {}
    for record in records:
        if record.ts in contaminated_seconds or not record.msg.startswith("EVENT "):
            continue
        event = try_parse_strict_json_object_from_msg(record.msg)
        if not event or event.get("event") != "main_post_posted" or event.get("lane") != "quote_image":
            continue
        post_id = str(event.get("post_id") or "")
        if not post_id:
            continue
        basename = str(event.get("image_basename") or "")
        posts.setdefault(post_id, {"timestamp": record.ts, "basename": basename, "generated": bool(GENERATED_BASENAME_RE.fullmatch(basename))})
    clean_timestamps = [record.ts for record in records if record.ts not in contaminated_seconds]
    earliest = min(clean_timestamps) if clean_timestamps else None
    windows: Dict[str, Any] = {}
    for window_days in (7, 30):
        window_cutoff = now - timedelta(days=window_days)
        selected = [post for post in posts.values() if post["timestamp"] >= window_cutoff]
        generated = sum(post["generated"] for post in selected)
        window_timestamps = sorted(ts for ts in clean_timestamps if ts >= window_cutoff)
        coverage_start = max(window_cutoff, earliest) if earliest else None
        coverage_days = max((now - coverage_start).total_seconds() / 86400.0, 0.0) if coverage_start else 0.0
        gap_threshold_seconds = 15 * 60
        points = ([coverage_start] if coverage_start else []) + window_timestamps + ([now] if coverage_start else [])
        gaps = [(later - earlier).total_seconds() for earlier, later in zip(points, points[1:])]
        largest_gap = max(gaps, default=0.0)
        material_gaps = sum(gap > gap_threshold_seconds for gap in gaps)
        observed_seconds = sum(min(max(gap, 0.0), gap_threshold_seconds) for gap in gaps)
        coverage_quality = "unavailable" if not coverage_start else "continuous" if material_gaps == 0 else "gapped"
        regular_per_day = len(selected) / coverage_days if coverage_days > 0 else None
        generated_per_day = generated / coverage_days if coverage_days > 0 else None
        windows[f"trailing_{window_days}d"] = {
            "regular_posts": len(selected), "generated_posts": generated,
            "generated_share_percent": (generated / len(selected) * 100.0) if selected else None,
            "coverage_days": coverage_days, "calendar_span_days": coverage_days,
            "observed_logging_days": observed_seconds / 86400.0,
            "coverage_quality": coverage_quality, "largest_detected_gap_seconds": largest_gap,
            "material_gap_count": material_gaps, "gap_threshold_seconds": gap_threshold_seconds,
            "regular_posts_per_day": regular_per_day, "generated_posts_per_day": generated_per_day,
        }
    post_history = [
        {"post_id": post_id, "timestamp": item["timestamp"].isoformat(sep=" "), "basename": item["basename"], "generated": item["generated"]}
        for post_id, item in sorted(posts.items(), key=lambda pair: (pair[1]["timestamp"], pair[0]))
    ]
    return {"windows": windows, "scanned_records": len(records), "unique_regular_posts": len(posts), "contaminated_seconds_excluded": len(contaminated_seconds),
            "coverage_start": earliest.isoformat(sep=" ") if earliest else None, "coverage_end": now.isoformat(sep=" "), "files_scanned": len(logs),
            "successful_regular_posts": post_history}


RUNWAY_CONFIG_DEFAULTS: Dict[str, Any] = {
    "ENABLE_GENERATED_IMAGE_POOL": False,
    "POST_SLEEP_MIN": 7200,
    "POST_SLEEP_MAX": 9000,
    "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN": 2,
}


def load_runway_config(
    project_dir: Path,
    observed_config: Dict[str, Any],
    *,
    RUNWAY_CONFIG_DEFAULTS: Dict[str, Any],
    parse_json_object: Callable[..., Dict[str, Any]],
) -> Dict[str, Any]:
    """Resolve standalone runway inputs without importing production code."""
    result = dict(RUNWAY_CONFIG_DEFAULTS)
    result.update({key: value for key, value in observed_config.items() if key in result})
    local_path = project_dir / "mrsMThatcher.local.json"
    if not local_path.exists():
        return result
    try:
        local_config = parse_json_object(
            local_path.read_bytes(), label="mrsMThatcher.local.json"
        )
    except Exception as exc:
        return {
            "_runway_config_error": (
                "cannot read valid local config: "
                f"{type(exc).__name__}"
            )
        }
    result.update({key: value for key, value in local_config.items() if key in result})
    return result
