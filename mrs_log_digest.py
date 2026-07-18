#!/usr/bin/env python3
"""
Summarise MrsMThatcher bot logs into a compact, ChatGPT-friendly digest.

Examples:
  # First run in the bot log directory, with an explicit starting point:
  cd /disks/disk1/etc/mrsMThatcher
  ./mrs_log_digest.py --since "2026-06-25 08:00" > digest.md

  # Later runs automatically resume after the last log timestamp previously analysed:
  ./mrs_log_digest.py > digest.md

  # JSON output:
  ./mrs_log_digest.py --json > digest.json

By default this expects to be run in the directory containing mrsMThatcher*.log*
files. It stores its resume timestamp in .mrs_log_digest_state.json.

No third-party dependencies.

Enhanced v7: keeps the v6 meme-scheduler reporting and fixes config
back-scan across multiple/rotated log files. Earlier v5/v6 scanned log files
in path order, so an older rotated file could overwrite newer Config values.
v7 sorts all candidate Config records chronologically before applying them.
"""
from __future__ import annotations

import argparse
import ast
import fcntl
import hashlib
import json
import math
import os
import re
import statistics
import sys
from collections import Counter
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

LOG_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+"
    r"(?P<level>[A-Z]+)\s+"
    r"(?P<src>[^:]+?)(?::(?P<line>\d+))? - (?P<msg>.*)$"
)
GENERATED_BASENAME_RE = re.compile(r"tg_([0-9a-f]{64})\.png\Z")
GENERATED_POLICIES = ("unrestricted", "small_penalty", "strong_penalty", "origin_quote_only")
GENERATED_ANALYSIS_SCHEMA_VERSION = 3
GENERATED_ANALYSIS_KIND = "images"
GENERATED_AUDIT_SCHEMA_VERSION = 1
GENERATED_AUDIT_KIND = "generated_image_identity_dependence_audit"


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def hybrid_retrieval_shadow_snapshot(project_dir: Path) -> Dict[str, Any]:
    """Read local shadow telemetry without importing or running the embedder."""
    path = project_dir / "hybrid_reply_retrieval_runtime" / "shadow_status.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("status root is not an object")
        counts = {
            key: int(value.get(key, 0) or 0)
            for key in (
                "events", "completed", "failures", "hybrid_changed_evidence_set",
                "hybrid_only_evidence", "lexical_only_evidence", "no_evidence_disagreements",
            )
        }
        measurements = {
            key: None if value.get(key) is None else float(value[key])
            for key in (
                "top_5_overlap_percent", "latency_p50_ms", "latency_p95_ms", "latency_max_ms",
            )
        }
    except FileNotFoundError:
        return {"available": False, "reason": "shadow mode disabled or no events observed"}
    except Exception as exc:
        return {"available": False, "reason": f"shadow status unavailable: {type(exc).__name__}"}
    return {
        "available": True,
        **counts,
        **measurements,
        "index_version": str(value.get("index_version") or "unavailable"),
        "model_revision": str(value.get("model_revision") or "unavailable"),
        "updated_at": value.get("updated_at"),
    }


def quote_image_semantic_veto_shadow_snapshot(project_dir: Path) -> Dict[str, Any]:
    """Read the local material-veto shadow status without any provider access."""
    path = project_dir / "quote_image_semantic_veto_runtime" / "shadow_status.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("status root is not an object")
    except FileNotFoundError:
        return {"available": False, "reason": "shadow mode disabled or no events observed"}
    except Exception as exc:
        return {"available": False, "reason": f"shadow status unavailable: {type(exc).__name__}"}
    return {
        "available": True,
        "events": int(value.get("events", 0) or 0),
        "allowed": int(value.get("allowed", 0) or 0),
        "vetoed": int(value.get("vetoed", 0) or 0),
        "unknown": int(value.get("unknown", 0) or 0),
        "generated_out_of_scope": int(value.get("generated_out_of_scope", 0) or 0),
        "manifest_unavailable": int(value.get("manifest_unavailable", 0) or 0),
        "manifest_stale": int(value.get("manifest_stale", 0) or 0),
        "vetoed_with_alternative": int(value.get("vetoed_with_alternative", 0) or 0),
        "vetoed_without_alternative": int(value.get("vetoed_without_alternative", 0) or 0),
        "selection_error_candidate_available": int(
            value.get("selection_error_candidate_available", value.get("vetoed_with_alternative", 0)) or 0
        ),
        "coverage_gap_no_safe_image": int(value.get("coverage_gap_no_safe_image", 0) or 0),
        "quotes_with_no_globally_allowed_candidate": int(value.get("quotes_with_no_globally_allowed_candidate", 0) or 0),
        "veto_reason_counts": value.get("veto_reason_counts") if isinstance(value.get("veto_reason_counts"), dict) else {},
        "alternative_score_delta_median": value.get("alternative_score_delta_median"),
        "lookup_latency_p50_ms": value.get("lookup_latency_p50_ms"),
        "lookup_latency_p95_ms": value.get("lookup_latency_p95_ms"),
        "lookup_latency_max_ms": value.get("lookup_latency_max_ms"),
        "manifest_policy_version": str(value.get("manifest_policy_version") or "unavailable"),
        "manifest_sha256": str(value.get("manifest_sha256") or ""),
        "production_selection_change_failures": int(value.get("production_selection_change_failures", 0) or 0),
        "updated_at": value.get("updated_at"),
    }


def generated_pool_health_snapshot(base_dir: Path, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Read current generated-pool health without mutating project files."""
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
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("root is not an object")
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
        try: actual_hash = file_sha256(path)
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

    now = now or datetime.now().astimezone()
    completed_quarantines = 0
    completed_restores = 0
    latest_quarantine: Optional[Dict[str, Any]] = None
    latest_restore: Optional[Dict[str, Any]] = None
    quarantined: Dict[str, Dict[str, Any]] = {}
    curation_events: List[Tuple[str, datetime, set[str]]] = []
    if quarantine_dir.exists():
        for manifest_path in sorted(quarantine_dir.glob("*/manifest.json")):
            try:
                transaction = json.loads(manifest_path.read_text(encoding="utf-8"))
                if not isinstance(transaction, dict): raise ValueError("manifest root is not an object")
            except Exception as exc:
                warning("transaction_malformed", manifest_path.parent.name, str(exc)); continue
            if transaction.get("status") != "completed":
                continue
            kind = transaction.get("kind")
            try:
                timestamp = datetime.fromisoformat(str(transaction.get("created_at") or "").replace("Z", "+00:00"))
                if timestamp.tzinfo is None: timestamp = timestamp.replace(tzinfo=now.tzinfo)
                timestamp = timestamp.astimezone(now.tzinfo)
                image_values = transaction.get("images") or []
                event_names = {str(item.get("basename") if isinstance(item, dict) else item) for item in image_values}
                event_names.discard("")
                if kind in {"quarantine", "restore"}: curation_events.append((str(kind), timestamp, event_names))
            except Exception as exc:
                warning("transaction_timestamp_malformed", manifest_path.parent.name, str(exc))
            if kind == "quarantine":
                completed_quarantines += 1
                if latest_quarantine is None or str(transaction.get("created_at") or "") > str(latest_quarantine.get("created_at") or ""):
                    latest_quarantine = transaction
                for entry in transaction.get("images") or []:
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
            if entry.get("sha256") != file_sha256(path): warning("quarantine_hash_mismatch", name, "preserved image hash differs from manifest")
        except Exception as exc: warning("quarantine_hash_error", name, str(exc))
        preserved_audit = entry.get("audit_record") if isinstance(entry.get("audit_record"), dict) else {}
        preserved_identity = preserved_audit.get("analysis") if isinstance(preserved_audit.get("analysis"), dict) else {}
        policy = preserved_identity.get("recommended_cross_quote_policy")
        if policy in GENERATED_POLICIES: quarantined_policy_counts[str(policy)] += 1

    used_generated: set[str] = set()
    try:
        raw_used = json.loads(used_path.read_text(encoding="utf-8"))
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


def generated_post_rate_history(logs: List[Path], now: Optional[datetime] = None, days: int = 30) -> Dict[str, Any]:
    """Scan bounded production history once and count structured successful regular posts."""
    now = now or datetime.now()
    if now.tzinfo is not None: now = now.replace(tzinfo=None)
    cutoff = now - timedelta(days=days)
    records = read_records(logs, cutoff, now)
    marker_fragments = ("/tmp/pytest-", "/tmp/pytest-of-", "mrs_test_mode", "dummy credentials", "127.0.0.1")
    contaminated_seconds = {record.ts for record in records if any(fragment in record.msg.lower() for fragment in marker_fragments)}
    posts: Dict[str, Dict[str, Any]] = {}
    for record in records:
        if record.ts in contaminated_seconds or not record.msg.startswith("EVENT "):
            continue
        event = try_parse_json_object_from_msg(record.msg)
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
        "quarantined_generated_images": len(quarantined),
        "history_coverage_start": rates.get("coverage_start"),
        "history_coverage_end": rates.get("coverage_end"),
        "history_scope": "bounded available structured production logs; not guaranteed all-time",
    }


def generated_pool_runway(pool: Dict[str, Any], rates: Dict[str, Any], config: Dict[str, Any]) -> Dict[str, Any]:
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


RUNWAY_CONFIG_DEFAULTS: Dict[str, Any] = {
    "ENABLE_GENERATED_IMAGE_POOL": False,
    "POST_SLEEP_MIN": 7200,
    "POST_SLEEP_MAX": 9000,
    "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN": 2,
}


def load_runway_config(project_dir: Path, observed_config: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve standalone runway inputs without importing production code."""
    result = dict(RUNWAY_CONFIG_DEFAULTS)
    result.update({key: value for key, value in observed_config.items() if key in result})
    local_path = project_dir / "mrsMThatcher.local.json"
    if not local_path.exists():
        return result
    try:
        local_config = json.loads(local_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"_runway_config_error": f"cannot read valid local config {local_path}: {exc}"}
    if not isinstance(local_config, dict):
        return {"_runway_config_error": f"local config is not a JSON object: {local_path}"}
    result.update({key: value for key, value in local_config.items() if key in result})
    return result


def parse_dt(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    value = value.strip().replace("T", " ")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        value += " 00:00:00"
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            pass
    raise SystemExit(f"Could not parse datetime: {value!r}. Use e.g. '2026-06-25 08:00'.")


def dt_text(value: datetime) -> str:
    return value.strftime("%Y-%m-%d %H:%M:%S")


def read_resume_data(state_file: Path) -> Dict[str, Any]:
    """Read the digest resume file.

    The timestamp is used for auto-resume. Newer versions also keep the last
    observed bot state/config so short quiet windows can still show budget and
    priority context.
    """
    if not state_file.exists():
        return {}
    try:
        data = json.loads(state_file.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception as e:
        print(f"WARNING: could not read state file {state_file}: {e}", file=sys.stderr)
        return {}


def save_resume_time(
    state_file: Path,
    last_ts: datetime,
    records: List["Record"],
    report: Dict[str, Any],
    logs: List[Path],
    *,
    preserve_existing_context: bool = True,
) -> None:
    old = read_resume_data(state_file) if preserve_existing_context else {}

    latest_state = merge_context(
        report.get("latest_state") or {},
        old.get("last_known_latest_state") or {},
    )
    latest_config = merge_context(
        report.get("latest_config") or {},
        old.get("last_known_latest_config") or {},
    )

    # Persist clean context only; _carried_forward/_filled_from_previous are
    # rendering annotations for this run, not durable bot facts.
    latest_state_clean = strip_internal_context_markers(latest_state)
    latest_config_clean = strip_internal_context_markers(latest_config)
    latest_generated_image_spacing = report.get("generated_image_spacing", {}).get("latest") or old.get("last_known_generated_image_spacing") or {}
    if isinstance(latest_generated_image_spacing, dict):
        latest_generated_image_spacing = {
            key: value
            for key, value in latest_generated_image_spacing.items()
            if not str(key).startswith("_")
        }
    boundary_fingerprints = {
        record_fingerprint(record)
        for record in records
        if record.ts == last_ts
    }
    try:
        old_last_ts = parse_dt(old.get("last_log_entry_time"))
    except Exception:
        old_last_ts = None
    if old_last_ts == last_ts:
        boundary_fingerprints.update(
            str(value)
            for value in old.get("last_log_entry_fingerprints", [])
            if value
        )

    data = {
        "last_log_entry_time": dt_text(last_ts),
        "last_log_entry_fingerprints": sorted(boundary_fingerprints),
        "last_run_record_count": report.get("summary", {}).get("record_count"),
        "last_run_time_start": report.get("summary", {}).get("time_start"),
        "last_run_time_end": report.get("summary", {}).get("time_end"),
        "last_run_logs": [str(p) for p in logs],
        "last_known_latest_state": latest_state_clean,
        "last_known_latest_config": latest_config_clean,
        "last_known_generated_image_spacing": latest_generated_image_spacing,
        "last_active_xai_context": report.get("resume_context", {}).get("active_xai_context"),
        "last_pending_mention": report.get("resume_context", {}).get("pending_mention"),
        "last_pending_qt": report.get("resume_context", {}).get("pending_qt"),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    tmp = state_file.with_suffix(state_file.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(state_file)


def discover_logs(directory: Path, pattern: str) -> List[Path]:
    paths = []
    for p in directory.glob(pattern):
        if not p.is_file():
            continue
        # Avoid accidentally ingesting digest outputs or state files if a broad pattern is used.
        name = p.name.lower()
        if (
            name.endswith(".json")
            or name.endswith(".md")
            or "digest" in name
            or is_selftest_log_path(p)
        ):
            continue
        paths.append(p)
    # Deterministic order; the records are later sorted by timestamp anyway.
    return sorted(paths, key=lambda p: p.name)


def is_selftest_log_path(path: Path | str) -> bool:
    """Return whether *path* is a self-test log, never production evidence."""
    name = Path(path).name.lower()
    return re.search(r"(?:^|[._-])self-?test(?:[._-]|$)", name) is not None


def resolve_explicit_logs(paths: Iterable[Path], project_dir: Path) -> List[Path]:
    """Resolve explicit inputs and expand only canonical numeric rotations."""
    resolved: list[Path] = []
    seen: set[Path] = set()

    def add(path: Path) -> None:
        path = path.expanduser()
        if not path.is_absolute():
            path = project_dir / path
        path = path.resolve()
        if path not in seen:
            seen.add(path)
            resolved.append(path)

    supplied = list(paths)
    for path in supplied:
        add(path)
    for path in supplied:
        candidate = path.expanduser()
        if not candidate.is_absolute():
            candidate = project_dir / candidate
        candidate = candidate.resolve()
        if candidate.name != "mrsMThatcher.log":
            continue
        rotations = []
        for sibling in candidate.parent.glob("mrsMThatcher.log.*"):
            suffix = sibling.name.removeprefix("mrsMThatcher.log.")
            if sibling.is_file() and suffix.isdigit():
                rotations.append((int(suffix), sibling))
        for _number, sibling in sorted(rotations):
            add(sibling)
    return resolved


@contextmanager
def digest_execution_lock(path: Path):
    """Hold a separate, nonblocking lock for one stateful/output digest run."""
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+", encoding="utf-8")
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.seek(0)
            owner = handle.read().strip() or "owner unavailable"
            raise RuntimeError(f"Another digest process holds {path}: {owner}") from exc
        handle.seek(0)
        handle.truncate()
        handle.write(f"pid={os.getpid()} acquired_at={datetime.now().isoformat(timespec='seconds')}\n")
        handle.flush()
        yield
    finally:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def epoch_to_human(value: Any) -> Optional[str]:
    try:
        n = int(value)
    except Exception:
        return None
    if n <= 0:
        return None
    return datetime.fromtimestamp(n).strftime("%Y-%m-%d %H:%M:%S")


def int_or_none(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(str(value).strip())
    except Exception:
        return None


def cooldown_state_text(until_epoch: Any, state_time_text: Any) -> str:
    until = int_or_none(until_epoch)
    state_time = parse_dt(state_time_text) if state_time_text else None
    if not until or not state_time:
        return ""
    return "active" if int(state_time.timestamp()) < until else "expired"


@dataclass(frozen=True)
class Record:
    ts: datetime
    level: str
    src: str
    line: int
    msg: str
    path: str
    ordinal: int


def record_fingerprint(record: Record) -> str:
    body = "\x1f".join(
        [
            dt_text(record.ts),
            record.level,
            record.src,
            str(record.line),
            record.msg,
        ]
    )
    return hashlib.sha256(body.encode("utf-8", errors="replace")).hexdigest()


def iter_records(path: Path) -> Iterable[Record]:
    current: Optional[Dict[str, Any]] = None
    ordinal = 0

    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.rstrip("\n")
            m = LOG_RE.match(line)
            if m:
                if current is not None:
                    yield Record(**current)
                ordinal += 1
                current = {
                    "ts": datetime.strptime(m.group("ts"), "%Y-%m-%d %H:%M:%S"),
                    "level": m.group("level"),
                    "src": m.group("src").strip(),
                    "line": int(m.group("line") or 0),
                    "msg": m.group("msg"),
                    "path": str(path),
                    "ordinal": ordinal,
                }
            elif current is not None:
                current["msg"] += "\n" + line
            else:
                # Ignore leading junk before first timestamp.
                pass

    if current is not None:
        yield Record(**current)


def read_records(
    paths: List[Path],
    since: Optional[datetime],
    until: Optional[datetime],
    *,
    since_exclusive: bool = False,
) -> List[Record]:
    seen = set()
    out: List[Record] = []
    for path in paths:
        if not path.exists():
            print(f"WARNING: missing log file: {path}", file=sys.stderr)
            continue
        for r in iter_records(path):
            if since:
                if since_exclusive:
                    if r.ts <= since:
                        continue
                elif r.ts < since:
                    continue
            if until and r.ts > until:
                continue
            # Logs are often uploaded with overlap; dedupe exact records.
            key = (r.ts, r.level, r.src, r.line, r.msg)
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
    out.sort(key=lambda r: (r.ts, r.path, r.ordinal))
    return out


def summarize_input_files(
    paths: List[Path],
    since: Optional[datetime],
    until: Optional[datetime],
    *,
    since_exclusive: bool = False,
) -> List[Dict[str, Any]]:
    summaries: List[Dict[str, Any]] = []

    for path in paths:
        summary: Dict[str, Any] = {
            "path": str(path),
            "exists": path.exists(),
            "size": None,
            "mtime": None,
            "total_records": 0,
            "first_timestamp": None,
            "last_timestamp": None,
            "records_after_since": 0,
            "records_in_window": 0,
        }

        if not path.exists():
            summaries.append(summary)
            continue

        try:
            stat = path.stat()
            summary["size"] = stat.st_size
            summary["mtime"] = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        except OSError:
            pass

        for record in iter_records(path):
            summary["total_records"] += 1
            ts_text = dt_text(record.ts)
            if summary["first_timestamp"] is None:
                summary["first_timestamp"] = ts_text
            summary["last_timestamp"] = ts_text

            after_since = True
            if since is not None:
                after_since = record.ts > since if since_exclusive else record.ts >= since
            if after_since:
                summary["records_after_since"] += 1

            selected = after_since
            if until is not None and record.ts > until:
                selected = False
            if selected:
                summary["records_in_window"] += 1

        summaries.append(summary)

    return summaries


def lit(value: str) -> str:
    """Parse a Python repr string when possible, otherwise return raw."""
    value = value.strip()
    try:
        return ast.literal_eval(value)
    except Exception:
        return value.strip("'\"")


def short(value: Any, n: int) -> str:
    if value is None:
        return ""
    s = str(value).replace("\n", "\\n")
    if len(s) <= n:
        return s
    return s[: max(0, n - 1)] + "…"


UNKNOWN_MISSING_STATE_FIELD = "unknown (not present in latest snapshot)"
UNKNOWN_INVALID_STATE_FIELD = "unknown (invalid in latest snapshot)"


def state_list_count(state: Dict[str, Any], key: str) -> Any:
    if key not in state:
        return UNKNOWN_MISSING_STATE_FIELD
    value = state.get(key)
    if isinstance(value, list):
        return len(value)
    return UNKNOWN_INVALID_STATE_FIELD


def state_list_tail(state: Dict[str, Any], key: str, count: int) -> Optional[List[Any]]:
    if key not in state:
        return None
    value = state.get(key)
    if isinstance(value, list):
        return value[-count:]
    return None


def state_list_head(state: Dict[str, Any], key: str, count: int) -> Optional[List[Any]]:
    if key not in state:
        return None
    value = state.get(key)
    if isinstance(value, list):
        return value[:count]
    return None


def summarize_latest_state(
    latest_state: Dict[str, Any],
    latest_state_ts: Optional[datetime],
    *,
    source: str = "log snapshot",
    source_path: Optional[Path] = None,
) -> Dict[str, Any]:
    summary = {
        "time": latest_state_ts.strftime("%Y-%m-%d %H:%M:%S") if latest_state_ts else None,
        "_state_source": source,
        "daily_reply_date": latest_state.get("daily_reply_date"),
        "daily_reply_count": latest_state.get("daily_reply_count"),
        "daily_quote_reply_date": latest_state.get("daily_quote_reply_date"),
        "daily_quote_reply_count": latest_state.get("daily_quote_reply_count"),
        "last_seen_mention_id": latest_state.get("last_seen_mention_id"),
        "last_main_post_id": latest_state.get("last_main_post_id"),
        "last_reply_epoch": latest_state.get("last_reply_epoch"),
        "last_reply_human": epoch_to_human(latest_state.get("last_reply_epoch")),
        "last_quote_post_epoch": latest_state.get("last_quote_post_epoch"),
        "last_quote_post_human": epoch_to_human(latest_state.get("last_quote_post_epoch")),
        "last_meme_post_epoch": latest_state.get("last_meme_post_epoch"),
        "last_meme_post_human": epoch_to_human(latest_state.get("last_meme_post_epoch")),
        "next_quote_post_epoch": latest_state.get("next_quote_post_epoch"),
        "next_quote_post_human": epoch_to_human(latest_state.get("next_quote_post_epoch")),
        "next_meme_post_epoch": latest_state.get("next_meme_post_epoch"),
        "next_meme_post_human": epoch_to_human(latest_state.get("next_meme_post_epoch")),
        "next_meme_schedule_mode": latest_state.get("next_meme_schedule_mode"),
        "next_meme_schedule_date": latest_state.get("next_meme_schedule_date"),
        "meme_anchor_quote_post_epoch": latest_state.get("meme_anchor_quote_post_epoch"),
        "meme_anchor_quote_post_human": epoch_to_human(latest_state.get("meme_anchor_quote_post_epoch")),
        "meme_schedule_version": latest_state.get("meme_schedule_version"),
        "api_cooldown_until_epoch": latest_state.get("api_cooldown_until_epoch"),
        "api_cooldown_until_human": epoch_to_human(latest_state.get("api_cooldown_until_epoch")),
        "api_cooldown_reason": latest_state.get("api_cooldown_reason"),
        "x_write_api_cooldown_until_epoch": latest_state.get("x_write_api_cooldown_until_epoch"),
        "x_write_api_cooldown_until_human": epoch_to_human(latest_state.get("x_write_api_cooldown_until_epoch")),
        "x_write_api_cooldown_reason": latest_state.get("x_write_api_cooldown_reason"),
        "xai_api_cooldown_until_epoch": latest_state.get("xai_api_cooldown_until_epoch"),
        "xai_api_cooldown_until_human": epoch_to_human(latest_state.get("xai_api_cooldown_until_epoch")),
        "xai_api_cooldown_reason": latest_state.get("xai_api_cooldown_reason"),
        "quote_api_cooldown_until_epoch": latest_state.get("quote_api_cooldown_until_epoch"),
        "quote_api_cooldown_until_human": epoch_to_human(latest_state.get("quote_api_cooldown_until_epoch")),
        "quote_api_cooldown_reason": latest_state.get("quote_api_cooldown_reason"),
        "quote_spam_author_count": state_list_count(latest_state, "quote_spam_author_ids"),
        "posted_meme_count": state_list_count(latest_state, "posted_meme_filenames"),
        "posted_meme_filenames_tail": state_list_tail(latest_state, "posted_meme_filenames", 8),
        "recent_own_post_ids_head": state_list_head(latest_state, "recent_own_post_ids", 5),
        "next_reply_lane_priority": latest_state.get("next_reply_lane_priority"),
        "skipped_hot_reply_count": state_list_count(latest_state, "skipped_hot_reply_ids"),
    }
    if latest_state.get("_partial"):
        summary["_partial"] = True
    if source_path is not None:
        summary["_state_source_path"] = str(source_path)
    return summary


def load_authoritative_state_for_logs(logs: List[Path]) -> Tuple[Optional[Dict[str, Any]], Optional[Path], Optional[datetime]]:
    seen_dirs: set[Path] = set()
    for log in logs:
        if is_selftest_log_path(log):
            continue
        directory = log.parent.resolve()
        if directory in seen_dirs:
            continue
        seen_dirs.add(directory)
        path = directory / "bot_state.json"
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                print(f"WARNING: ignoring non-object bot state {path}", file=sys.stderr)
                continue
            mtime = datetime.fromtimestamp(path.stat().st_mtime)
            return data, path, mtime
        except Exception as e:
            print(f"WARNING: could not read authoritative bot state {path}: {e}", file=sys.stderr)
    return None, None, None


def state_context_is_within_window(state: Dict[str, Any], window_end: Optional[datetime]) -> bool:
    if window_end is None:
        return True
    try:
        state_time = parse_dt(state.get("time"))
    except Exception:
        state_time = None
    return state_time is None or state_time <= window_end


INTERNAL_CONTEXT_KEYS = {
    "_carried_forward",
    "_filled_from_previous",
    "_filled_from_log_backscan",
    "_carried_from_log_backscan",
    "_log_backscan_timestamp",
    "_partial",
    "_state_source",
    "_state_source_path",
}


def strip_internal_context_markers(value: Any) -> Any:
    """Remove digest-only annotations before persisting context."""
    if isinstance(value, dict):
        return {
            k: strip_internal_context_markers(v)
            for k, v in value.items()
            if k not in INTERNAL_CONTEXT_KEYS
        }
    if isinstance(value, list):
        return [strip_internal_context_markers(v) for v in value]
    return value


def merge_context(current: Dict[str, Any], previous: Dict[str, Any]) -> Dict[str, Any]:
    """Fill missing/None fields in current from previous, preserving current values.

    v3 only carried state/config forward when the whole object was absent. v4
    merges per field, so a quiet or partial window can still show reply budgets
    from the last known config while using the current state snapshot.
    """
    cur = dict(current or {})
    prev = strip_internal_context_markers(previous or {})
    if not prev:
        return cur

    if not cur:
        cur = dict(prev)
        cur["_carried_forward"] = True
        return cur

    filled = False
    for k, v in prev.items():
        if k in INTERNAL_CONTEXT_KEYS:
            continue
        if k not in cur or cur.get(k) is None:
            cur[k] = v
            filled = True
    if filled:
        cur["_filled_from_previous"] = True
    return cur




def extract_config_pairs(msg: str) -> Dict[str, str]:
    """Return KEY=VALUE pairs from a bot Config log message."""
    if not msg.startswith("Config: "):
        return {}
    body = msg[len("Config: "):]
    return {key: val.strip() for key, val in re.findall(r"([A-Z0-9_]+)=([^\s]+)", body)}


def merge_context_from_log_backscan(
    current: Dict[str, Any],
    previous: Dict[str, Any],
    *,
    backscan_ts: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Fill missing config fields from earlier records in the same log files.

    This is deliberately separate from saved-state carry-forward: it means an
    incremental digest can recover the latest startup Config values even when
    .mrs_log_digest_state.json has no stored config yet.
    """
    cur = dict(current or {})
    prev = strip_internal_context_markers(previous or {})
    if not prev:
        return cur

    ts_text = dt_text(backscan_ts) if backscan_ts else None
    if not cur:
        cur = dict(prev)
        cur["_carried_from_log_backscan"] = True
        if ts_text:
            cur["_log_backscan_timestamp"] = ts_text
        return cur

    filled = False
    for k, v in prev.items():
        if k in INTERNAL_CONTEXT_KEYS:
            continue
        if k not in cur or cur.get(k) is None:
            cur[k] = v
            filled = True
    if filled:
        cur["_filled_from_log_backscan"] = True
        if ts_text:
            cur["_log_backscan_timestamp"] = ts_text
    return cur


def find_latest_config_before(paths: List[Path], before: Optional[datetime]) -> Tuple[Dict[str, str], Optional[datetime]]:
    """Scan earlier log records for the latest known Config values before a cutoff.

    Config is emitted as multiple `Config: KEY=VALUE` records at startup. v7
    collects matching records from *all* log files, de-duplicates them, then
    sorts chronologically before applying values. This matters with rotated logs:
    path/glob order is not guaranteed to be chronological, and an older rotated
    file must never overwrite newer config from the live log.
    """
    if before is None:
        return {}, None

    seen = set()
    candidates: List[Record] = []
    for path in paths:
        if is_selftest_log_path(path):
            continue
        if not path.exists():
            continue
        for r in iter_records(path):
            if r.ts >= before:
                continue
            if not extract_config_pairs(r.msg):
                continue
            key = (r.ts, r.level, r.src, r.line, r.msg)
            if key in seen:
                continue
            seen.add(key)
            candidates.append(r)

    candidates.sort(key=lambda r: (r.ts, r.path, r.ordinal))

    configs: Dict[str, str] = {}
    latest_ts: Optional[datetime] = None
    for r in candidates:
        configs.update(extract_config_pairs(r.msg))
        latest_ts = r.ts

    return configs, latest_ts

def try_parse_response_id_text(msg: str) -> Tuple[Optional[str], Optional[str]]:
    marker = "response="
    if marker not in msg:
        return None, None
    raw = msg.split(marker, 1)[1].strip()
    try:
        data = ast.literal_eval(raw)
        d = data.get("data") or {}
        return str(d.get("id")) if d.get("id") is not None else None, d.get("text")
    except Exception:
        m = re.search(r"'id': '([^']+)'", raw)
        return (m.group(1) if m else None), None


def try_parse_json_object_from_msg(msg: str) -> Optional[Dict[str, Any]]:
    start = msg.find("{")
    if start < 0:
        return None
    raw = msg[start:]
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            return obj
    except Exception:
        return None
    return None


def parse_partial_state_from_msg(msg: str) -> Optional[Dict[str, Any]]:
    """Best-effort extraction from log_json_debug state dumps that may be truncated."""
    if "State being saved:" not in msg and "Loaded state:" not in msg:
        return None
    keys = [
        "api_cooldown_reason", "api_cooldown_until_epoch",
        "quote_api_cooldown_reason", "quote_api_cooldown_until_epoch",
        "daily_quote_reply_count", "daily_quote_reply_date",
        "daily_reply_count", "daily_reply_date",
        "last_main_post_id", "last_meme_post_epoch", "last_quote_post_epoch",
        "last_quote_tweet_check_epoch", "last_reply_epoch", "last_seen_mention_id",
        "next_meme_post_epoch", "next_meme_schedule_mode", "next_meme_schedule_date",
        "meme_anchor_quote_post_epoch", "meme_schedule_version", "next_quote_post_epoch",
        "next_reply_lane_priority", "skipped_hot_reply_ids",
    ]
    out: Dict[str, Any] = {"_partial": True}
    for key in keys:
        m = re.search(r'"' + re.escape(key) + r'"\s*:\s*("(?:\\.|[^"])*"|-?\d+|true|false|null)', msg)
        if not m:
            continue
        raw = m.group(1)
        try:
            out[key] = json.loads(raw)
        except Exception:
            out[key] = raw.strip('"')

    # Count arrays only when their full array appears before truncation.
    for key in ("quote_spam_author_ids", "posted_meme_filenames", "recent_own_post_ids"):
        m = re.search(r'"' + re.escape(key) + r'"\s*:\s*(\[[\s\S]*?\])\s*,?\n\s*"', msg)
        if m:
            try:
                val = json.loads(m.group(1))
                out[key] = val
            except json.JSONDecodeError:
                continue
    return out if len(out) > 1 else None


def seconds_between(a: datetime, b: datetime) -> float:
    return abs((a - b).total_seconds())


def is_media_v2_request_failure(record: Record) -> bool:
    return (
        record.level in {"ERROR", "CRITICAL"}
        and record.src == "x_request"
        and "X request failed before receiving response" in record.msg
    )


def is_reply_target_eligibility_restriction(message: str) -> bool:
    text = str(message or "").lower()
    return any(
        marker in text
        for marker in (
            "only reply to or quote posts where you are mentioned or are the author",
            "reply to this conversation is not allowed",
            "not been mentioned or otherwise engaged by the author",
            "not allowed to reply",
        )
    )


def is_media_fallback_warning(record: Record) -> bool:
    return (
        record.level in {"ERROR", "CRITICAL", "WARNING"}
        and "v2 media upload failed; trying v1.1 fallback" in record.msg
    )


def is_media_v1_success(record: Record) -> bool:
    return "Uploaded media via v1.1." in record.msg


def is_media_v1_failure(record: Record) -> bool:
    return (
        record.level in {"ERROR", "CRITICAL"}
        and record.src in {"upload_media", "upload_media_v1_1", "x_request"}
        and (
            "v1.1" in record.msg
            or "legacy v1.1" in record.msg
            or "media upload failed" in record.msg
        )
    )


def is_main_post_success(record: Record) -> bool:
    return (
        "Quote/image posted successfully." in record.msg
        or "Daily meme posted successfully." in record.msg
        or ('EVENT {"event":"main_post_posted"' in record.msg)
    )


def find_recent_media_path(records: List[Record], index: int) -> Optional[str]:
    for earlier in reversed(records[max(0, index - 20):index + 1]):
        m = re.search(r"Uploading media via X API v2: (.+)$", earlier.msg)
        if m:
            return m.group(1).strip()
        m = re.search(r"Detected MIME type for (.+?):", earlier.msg)
        if m:
            return m.group(1).strip()
    return None


def correlate_media_upload_incidents(records: List[Record], max_text: int) -> Tuple[List[Dict[str, Any]], set[str]]:
    incidents: List[Dict[str, Any]] = []
    suppressed: set[str] = set()
    used_fallbacks: set[int] = set()

    for idx, record in enumerate(records):
        if not is_media_fallback_warning(record) or idx in used_fallbacks:
            continue
        used_fallbacks.add(idx)
        media_path = find_recent_media_path(records, idx)
        prior_failures = [
            candidate
            for candidate in records[max(0, idx - 8):idx]
            if is_media_v2_request_failure(candidate) and seconds_between(candidate.ts, record.ts) <= 90
        ]
        later = [
            candidate
            for candidate in records[idx + 1:idx + 40]
            if 0 <= (candidate.ts - record.ts).total_seconds() <= 180
        ]
        v1_success = next((candidate for candidate in later if is_media_v1_success(candidate)), None)
        post_success = next((candidate for candidate in later if is_main_post_success(candidate)), None)
        v1_failures = [candidate for candidate in later if is_media_v1_failure(candidate) and candidate is not v1_success]

        chain_records = [record, *prior_failures]
        if v1_success:
            chain_records.append(v1_success)
        if post_success:
            chain_records.append(post_success)
        chain_records.extend(v1_failures)
        for item in chain_records:
            suppressed.add(record_fingerprint(item))

        handled = bool(v1_success and post_success and not v1_failures)
        status = "handled" if handled else "unrecovered"
        detail = "v2 upload failed"
        if prior_failures:
            detail = short(prior_failures[-1].msg, max_text)
        incidents.append({
            "time": record.ts.strftime("%Y-%m-%d %H:%M:%S"),
            "status": status,
            "media": media_path or "",
            "v2_failure": detail,
            "fallback": short(record.msg, max_text),
            "v1_result": "succeeded" if v1_success else ("failed" if v1_failures else "not observed"),
            "post_result": "succeeded" if post_success else "not observed",
            "summary": (
                "v2 upload failed; v1.1 fallback succeeded and final post completed"
                if handled
                else "v2 upload failed and media/post completion was not observed"
            ),
        })

    return incidents, suppressed


def int_usage_value(value: Any) -> int:
    try:
        return int(value or 0)
    except Exception:
        return 0


def parse_xai_usage_from_msg(msg: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    marker = "xAI usage="
    if marker not in msg:
        return None, None
    raw = msg.split(marker, 1)[1].strip()
    try:
        parsed = ast.literal_eval(raw)
    except Exception as exc:
        return None, f"could not parse xAI usage dictionary: {exc}"
    if not isinstance(parsed, dict):
        return None, f"xAI usage payload was {type(parsed).__name__}, not dict"
    return parsed, None


def xai_usage_context_from_pending(pending_mention: Dict[str, Any], pending_qt: Dict[str, Any]) -> Dict[str, Any]:
    mention_seq = pending_mention.get("considered_seq", -1) if pending_mention else -1
    quote_seq = pending_qt.get("considered_seq", -1) if pending_qt else -1
    if pending_qt and quote_seq >= mention_seq:
        return {
            "lane": "quote-tweet",
            "context_id": pending_qt.get("quote_tweet_id", ""),
            "author_id": pending_qt.get("author_id", ""),
        }
    if pending_mention:
        return {
            "lane": "mention",
            "context_id": pending_mention.get("mention_id") or pending_mention.get("hot_post_reply_id") or "",
            "author_id": pending_mention.get("author_id", ""),
        }
    return {"lane": "unknown", "context_id": "", "author_id": ""}


def unknown_xai_usage_context() -> Dict[str, Any]:
    return {"lane": "unknown", "context_id": "", "author_id": ""}


def summarize_xai_usage_event(
    record: Record,
    usage: Dict[str, Any],
    context: Dict[str, Any],
) -> Dict[str, Any]:
    prompt_details = usage.get("prompt_tokens_details")
    if not isinstance(prompt_details, dict):
        prompt_details = {}
    completion_details = usage.get("completion_tokens_details")
    if not isinstance(completion_details, dict):
        completion_details = {}
    return {
        "time": record.ts.strftime("%Y-%m-%d %H:%M:%S"),
        "lane": context.get("lane", "unknown"),
        "context_id": context.get("context_id", ""),
        "author_id": context.get("author_id", ""),
        "prompt_tokens": int_usage_value(usage.get("prompt_tokens")),
        "cached_tokens": int_usage_value(prompt_details.get("cached_tokens")),
        "image_tokens": int_usage_value(prompt_details.get("image_tokens")),
        "reasoning_tokens": int_usage_value(completion_details.get("reasoning_tokens")),
        "completion_tokens": int_usage_value(usage.get("completion_tokens")),
        "total_tokens": int_usage_value(usage.get("total_tokens")),
        "num_sources_used": int_usage_value(usage.get("num_sources_used")),
        "cost_in_usd_ticks": int_usage_value(usage.get("cost_in_usd_ticks")),
    }


def xai_usage_totals(events: List[Dict[str, Any]]) -> Dict[str, int]:
    return {
        "successful_xai_calls": len(events),
        "prompt_tokens": sum(int_usage_value(item.get("prompt_tokens")) for item in events),
        "cached_tokens": sum(int_usage_value(item.get("cached_tokens")) for item in events),
        "image_tokens": sum(int_usage_value(item.get("image_tokens")) for item in events),
        "reasoning_tokens": sum(int_usage_value(item.get("reasoning_tokens")) for item in events),
        "completion_tokens": sum(int_usage_value(item.get("completion_tokens")) for item in events),
        "total_tokens": sum(int_usage_value(item.get("total_tokens")) for item in events),
        "sources_used": sum(int_usage_value(item.get("num_sources_used")) for item in events),
        "cost_in_usd_ticks": sum(int_usage_value(item.get("cost_in_usd_ticks")) for item in events),
    }


def regular_image_usage_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
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


def original_editorial_shadow_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(events)
    comparable_originals = [item for item in events if item.get("production_source") == "original"]
    production_original = len(comparable_originals)
    production_generated = sum(1 for item in events if item.get("production_source") == "generated")
    changed = [item for item in comparable_originals if item.get("winner_changed") is True]
    ranks = [int(item["production_shadow_rank"]) for item in events if item.get("production_shadow_rank") is not None]
    rank1 = sum(1 for rank in ranks if rank == 1)
    rank2_3 = sum(1 for rank in ranks if rank in {2, 3})
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
        "production_original": production_original,
        "production_generated": production_generated,
        "comparable_original_observations": production_original,
        "winner_changes": len(changed),
        "winner_change_percent": (len(changed) / production_original * 100.0) if production_original else 0.0,
        "average_production_winner_shadow_rank": (sum(ranks) / len(ranks)) if ranks else None,
        "production_rank_1": rank1,
        "production_rank_2_or_3": rank2_3,
        "shadow_winner_differed": len(changed),
        "most_frequent_shadow_winners": winners.most_common(8),
        "most_frequent_affinity_concepts": affinity.most_common(8),
        "most_frequent_active_dimensions": dimensions.most_common(8),
        "average_abs_editorial_adjustment": (sum(adjustments) / len(adjustments)) if adjustments else 0.0,
        "max_abs_editorial_adjustment": max(adjustments) if adjustments else 0.0,
        "cap_hit_count": cap_hits,
    }


def generated_identity_shadow_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
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
        "most_frequent_shadow_winners": winners.most_common(8),
        "selection_phases": phases.most_common(),
        "event_categories": categories,
    }


def generated_identity_policy_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
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


def _count_optional(events: List[Dict[str, Any]], field: str, values: tuple[str, ...]) -> Dict[str, int]:
    counts = Counter({value: 0 for value in values})
    for event in events:
        value = event.get(field)
        key = str(value) if value not in (None, "") else "unavailable"
        counts[key if not values or key in values else "unavailable"] += 1
    return dict(sorted(counts.items()))


def historical_context_quality_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    items = [event for event in events if event.get("kind") == "historical_context_reply"]
    statuses = Counter({key: 0 for key in ("completed", "already_completed", "failed", "skipped", "dry_run", "unavailable")})
    skip_reasons = Counter()
    for event in items:
        status = str(event.get("status") or "unavailable")
        if status.startswith("skipped"):
            statuses["skipped"] += 1
            skip_reasons[str(event.get("reason") or status)] += 1
        elif status in statuses:
            statuses[status] += 1
        else:
            statuses["unavailable"] += 1
    rendered_items = [event for event in items if event.get("status") in {"completed", "dry_run"}]
    weighted = [int(event["character_count"]) for event in rendered_items
                if type(event.get("character_count")) is int and event["character_count"] > 0]
    raw = [int(event["raw_character_count"]) for event in rendered_items
           if type(event.get("raw_character_count")) is int and event["raw_character_count"] > 0]
    attempted = sum(str(event.get("status") or "") in {"completed", "failed", "dry_run"} for event in items)
    return {
        "attempted_count": attempted,
        "status_counts": dict(sorted(statuses.items())),
        "skip_reason_counts": dict(skip_reasons.most_common()),
        "verification_counts": _count_optional(rendered_items, "verification_label", (
            "Exact wording", "Normalised wording", "Verified excerpt", "Historically verified variant",
            "Historical paraphrase", "Composite wording", "Commonly misattributed wording",
            "Exact wording not verified", "unavailable",
        )),
        "source_class_counts": _count_optional(rendered_items, "source_class", (
            "Margaret Thatcher Foundation", "Hansard", "original speech transcript",
            "Thatcher-authored publication", "contemporary interview", "official Conservative publication",
            "other authoritative source", "canonical locator only", "no public URL", "unavailable",
        )),
        "confidence_counts": _count_optional(rendered_items, "historical_confidence", ("high", "medium", "low", "unavailable")),
        "average_raw_characters": (sum(raw) / len(raw)) if raw else None,
        "average_weighted_characters": (sum(weighted) / len(weighted)) if weighted else None,
        "raw_length_observation_count": len(raw),
        "raw_length_metadata_unavailable_count": len(rendered_items) - len(raw),
        "weighted_length_observation_count": len(weighted),
        "weighted_length_metadata_unavailable_count": len(rendered_items) - len(weighted),
        "minimum_weighted_characters": min(weighted) if weighted else None,
        "maximum_weighted_characters": max(weighted) if weighted else None,
        "shortened_count": sum(event.get("shortening_applied") is True for event in rendered_items),
        "meaning_omitted_count": sum(event.get("meaning_omitted") is True for event in rendered_items),
        "source_omitted_count": sum(event.get("source_omitted") is True for event in rendered_items),
        "verification_omitted_count": sum(event.get("verification_omitted") is True for event in rendered_items),
        "shortening_metadata_unavailable_count": sum(type(event.get("shortening_applied")) is not bool for event in rendered_items),
        "meaning_omitted_metadata_unavailable_count": sum(type(event.get("meaning_omitted")) is not bool for event in rendered_items),
        "source_omitted_metadata_unavailable_count": sum(type(event.get("source_omitted")) is not bool for event in rendered_items),
        "verification_omitted_metadata_unavailable_count": sum(type(event.get("verification_omitted")) is not bool for event in rendered_items),
        "omission_metadata_unavailable_count": sum(
            any(type(event.get(field)) is not bool for field in ("meaning_omitted", "source_omitted", "verification_omitted"))
            for event in rendered_items
        ),
        "metadata_unavailable_count": sum(event.get("verification_label") in (None, "", "unavailable") for event in rendered_items),
    }


def _normalise_lane(value: Any) -> str:
    lane = str(value or "unavailable").replace("_reply", "").replace("_", "-")
    return {"hot-post": "hot-post", "quote-tweet": "quote-tweet", "mention": "mention"}.get(lane, "unavailable")


def _no_reply_category(value: Any) -> str | None:
    reason = " ".join(str(value or "").lower().replace("-", "_").split())
    if not reason:
        return None
    exact = {
        "no_reply_due_to_unverifiable_claim",
        "no_reply_due_to_bait_or_abuse",
        "no_reply_due_to_incoherent",
    }
    if reason in exact:
        return reason
    if any(term in reason for term in ("unverifiable", "unverified", "unsupported claim", "endorse")):
        return "no_reply_due_to_unverifiable_claim"
    if any(term in reason for term in ("bait", "abuse", "abusive", "prolong conflict", "needless conflict")):
        return "no_reply_due_to_bait_or_abuse"
    if any(term in reason for term in ("incoherent", "gibberish", "unintelligible")):
        return "no_reply_due_to_incoherent"
    return None


def reply_strategy_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    raw_decisions = [event for event in events if event.get("kind") == "reply_strategy_decision"]
    decision_by_id: Dict[str, Dict[str, Any]] = {}
    for index, event in enumerate(raw_decisions):
        lane = _normalise_lane(event.get("lane"))
        target = str(event.get("target_id") or "")
        decision_id = f"{lane}:{target}" if target else f"missing:{index}"
        decision_by_id.setdefault(decision_id, event)
    decisions = list(decision_by_id.values())
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
    published_outcomes = [
        event for event in outcomes
        if str(event.get("status") or "confirmed") in {"confirmed", "posted"}
    ]

    posted: list[tuple[str, str]] = []
    for event in events:
        kind = str(event.get("kind") or "")
        lane = {"mention_reply_posted": "mention", "hot_post_reply_posted": "hot-post",
                "quote_tweet_reply_posted": "quote-tweet"}.get(kind)
        if lane:
            target_field = {"mention": "mention_id", "hot-post": "hot_post_reply_id", "quote-tweet": "quote_tweet_id"}[lane]
            posted.append((lane, str(event.get(target_field) or "")))

    observations = list(published_outcomes)
    observations.extend(event for event in decisions if event.get("mode") == "no_reply")
    outcome_targets = {
        (_normalise_lane(event.get("lane")), str(event.get("target_id") or ""))
        for event in published_outcomes
        if event.get("target_id")
    }
    targeted_decisions: Dict[tuple[str, str], list[Dict[str, Any]]] = {}
    anonymous_decisions: Dict[str, list[Dict[str, Any]]] = {}
    for event in decisions:
        if event.get("mode") == "no_reply":
            continue
        lane = _normalise_lane(event.get("lane"))
        target = str(event.get("target_id") or "")
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
    outcome_status_counts = Counter()
    for event in outcomes:
        status = str(event.get("status") or "confirmed")
        if status in {"confirmed", "posted"}:
            outcome_status_counts["posted"] += 1
        elif status.startswith("posting_failed"):
            outcome_status_counts["posting_failed"] += 1
        else:
            outcome_status_counts[status or "unavailable"] += 1
    outcome_status_counts["terminal_no_reply"] += sum(event.get("mode") == "no_reply" for event in decisions)
    retrieved = [int(event["retrieved_count"]) for event in observations if type(event.get("retrieved_count")) is int]
    generated_retrieved = [
        int(event["retrieved_count"])
        for event in decisions
        if type(event.get("retrieved_count")) is int
    ]
    rejection_reasons = Counter()
    no_reply_categories = Counter({key: 0 for key in (
        "no_reply_due_to_unverifiable_claim",
        "no_reply_due_to_bait_or_abuse",
        "no_reply_due_to_incoherent",
    )})
    routine_reasons = Counter()
    repetition_controls = Counter({key: 0 for key in (
        "exact_duplicate_rejected", "highly_similar_reply_rejected", "canned_formulation_rejected",
        "regenerated_after_style_rejection", "no_acceptable_reply",
    )})
    routine = {"author_daily_cap", "daily_cap", "spacing", "already_replied", "dry_run_already_seen", "own_account"}
    no_reply_targets = {
        (_normalise_lane(event.get("lane")), str(event.get("target_id") or ""))
        for event in decisions
        if event.get("mode") == "no_reply" and event.get("target_id")
    }
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
                and (_normalise_lane(event.get("lane")), str(event.get("target_id") or "")) in no_reply_targets
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
        if event.get("mode") == "no_reply":
            reason = str(event.get("no_reply_reason") or "model-selected no_reply")
            rejection_reasons[reason] += 1
            category = _no_reply_category(reason)
            if category:
                no_reply_categories[category] += 1
    humour_counts = _count_optional(observations, "humour_tone", ("dry", "wry", "playful", "deadpan", "warm", "none", "unavailable"))
    confidence_counts = _count_optional(observations, "evidence_confidence", ("high", "medium", "low", "none", "unavailable"))
    generated_humour_counts = _count_optional(decisions, "humour_tone", ("dry", "wry", "playful", "deadpan", "warm", "none", "unavailable"))
    generated_confidence_counts = _count_optional(decisions, "evidence_confidence", ("high", "medium", "low", "none", "unavailable"))
    return {
        "mode_counts": dict(sorted(modes.items())),
        "mode_counts_by_lane": {lane: dict(sorted(counts.items())) for lane, counts in sorted(by_lane.items())},
        "generated_mode_counts": dict(sorted(generated_modes.items())),
        "generated_mode_counts_by_lane": {
            lane: dict(sorted(counts.items())) for lane, counts in sorted(generated_by_lane.items())
        },
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
        "grounded_count": sum(event.get("grounded") is True for event in observations),
        "grounding_metadata_unavailable_count": sum(type(event.get("grounded")) is not bool for event in observations),
        "ungrounded_humour_only_count": sum(event.get("grounded") is False and event.get("factual_claim") is False and event.get("mode") not in {"no_reply", None} for event in observations),
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
        "rejection_reason_counts": dict(rejection_reasons.most_common()),
        "no_reply_category_counts": dict(no_reply_categories),
        "routine_skip_reason_counts": dict(routine_reasons.most_common()),
        "repetition_control_counts": dict(repetition_controls),
    }


def quote_image_semantic_veto_category(event: Dict[str, Any]) -> Optional[str]:
    """Return a category for new events and infer one from compatible legacy fields."""
    if event.get("shadow_status") != "veto":
        return None
    category = event.get("veto_category")
    if category in {"selection_error_candidate_available", "coverage_gap_no_safe_image"}:
        return str(category)
    if event.get("alternative_available") is True:
        return "selection_error_candidate_available"
    if event.get("quote_has_no_allowed_candidate_globally") is True:
        return "coverage_gap_no_safe_image"
    return None


def quote_image_semantic_veto_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    statuses = Counter(str(event.get("shadow_status") or "unknown") for event in events)
    vetoed = [event for event in events if event.get("shadow_status") == "veto"]
    deltas = [
        float(event["score_delta_from_production_winner"])
        for event in vetoed
        if isinstance(event.get("score_delta_from_production_winner"), (int, float))
    ]
    reasons = Counter(code for event in vetoed for code in (event.get("veto_reason_codes") or []))
    categories = Counter(quote_image_semantic_veto_category(event) for event in vetoed)
    no_safe_quote_ids = {
        str(event.get("quote_id"))
        for event in events
        if event.get("quote_has_no_allowed_candidate_globally") is True and event.get("quote_id")
    }
    examples = []
    for event in vetoed[:5]:
        examples.append({
            "quote_preview": event.get("quote_preview") or event.get("quote_id") or "",
            "production_image": event.get("selected_image_basename") or "",
            "veto_category": quote_image_semantic_veto_category(event),
            "veto_reason": ", ".join(event.get("veto_reason_codes") or []) or event.get("veto_explanation") or "",
            "alternative": event.get("alternative_image_basename") or "none",
            "score_delta": event.get("score_delta_from_production_winner"),
            "confirmed_post": event.get("confirmed_post") is True,
        })
    return {
        "available": bool(events),
        "selection_time_observations": len(events),
        "confirmed_successful_posts": sum(event.get("confirmed_post") is True for event in events),
        "in_scope_historical_selections": statuses["allow"] + statuses["veto"] + statuses["unknown_unjudged"],
        "allowed_production_winners": statuses["allow"],
        "vetoed_production_winners": statuses["veto"],
        "unknown_unjudged": statuses["unknown_unjudged"],
        "generated_out_of_scope": statuses["out_of_scope_generated"],
        "vetoed_with_allowed_alternative": sum(event.get("alternative_available") is True for event in vetoed),
        "vetoed_without_allowed_alternative": sum(event.get("alternative_available") is not True for event in vetoed),
        "selection_error_candidate_available": categories["selection_error_candidate_available"],
        "coverage_gap_no_safe_image": categories["coverage_gap_no_safe_image"],
        "quotes_with_no_globally_allowed_candidate": len(no_safe_quote_ids),
        "median_alternative_score_delta": statistics.median(deltas) if deltas else None,
        "manifest_policy_version": next((event.get("manifest_policy_version") for event in reversed(events) if event.get("manifest_policy_version")), "unavailable"),
        "manifest_sha256": next((event.get("manifest_sha256") for event in reversed(events) if event.get("manifest_sha256")), ""),
        "lookup_failures": statuses["manifest_unavailable"] + statuses["manifest_stale"],
        "production_selection_change_failures": sum(event.get("production_selection_changed") is not False for event in events),
        "veto_reason_counts": dict(reasons.most_common()),
        "examples": examples,
    }


def analyse(
    records: List[Record],
    max_text: int = 280,
    *,
    initial_active_xai_context: Optional[Dict[str, Any]] = None,
    initial_pending_mention: Optional[Dict[str, Any]] = None,
    initial_pending_qt: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    stats = Counter()
    events: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    self_test_errors: List[Dict[str, Any]] = []
    api_errors: List[Dict[str, Any]] = []
    handled_api_restrictions: List[Dict[str, Any]] = []
    receipt_events: List[Dict[str, Any]] = []
    confirmed_post_recovery: List[Dict[str, Any]] = []
    confirmed_reply_receipts: List[Dict[str, Any]] = []
    confirmed_reply_recovery: List[Dict[str, Any]] = []
    asset_health: List[Dict[str, Any]] = []
    reply_media_context: List[Dict[str, Any]] = []
    media_upload_incidents: List[Dict[str, Any]] = []
    xai_usage_events: List[Dict[str, Any]] = []
    xai_usage_parse_errors: List[Dict[str, Any]] = []
    regular_image_usage_events: List[Dict[str, Any]] = []
    original_editorial_shadow_events: List[Dict[str, Any]] = []
    generated_identity_shadow_events: List[Dict[str, Any]] = []
    generated_identity_policy_events: List[Dict[str, Any]] = []
    generated_image_spacing_events: List[Dict[str, Any]] = []
    quote_image_semantic_veto_events: List[Dict[str, Any]] = []
    latest_generated_image_spacing: Dict[str, Any] = {}
    cooldown_active: List[Dict[str, Any]] = []
    lifecycle: List[Dict[str, Any]] = []
    routine_skip_counts = Counter()
    configs: Dict[str, str] = {}
    latest_state: Optional[Dict[str, Any]] = None
    latest_state_ts: Optional[datetime] = None

    pending_quote: Dict[str, Any] = {}
    pending_meme: Dict[str, Any] = {}
    pending_mention: Dict[str, Any] = dict(initial_pending_mention or {})
    pending_qt: Dict[str, Any] = dict(initial_pending_qt or {})
    pending_confirmed_reply_receipt: Dict[str, Any] = {}
    active_xai_context: Optional[Dict[str, Any]] = dict(initial_active_xai_context or {}) or None
    last_created_post: Dict[str, Any] = {}
    pending_semantic_veto_event: Optional[Dict[str, Any]] = None

    def add_event(kind: str, ts: datetime, **kwargs: Any) -> Dict[str, Any]:
        ev = {"time": ts.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind}
        for k, v in kwargs.items():
            if isinstance(v, str):
                ev[k] = short(v, max_text)
            else:
                ev[k] = v
        events.append(ev)
        stats[kind] += 1
        return ev

    def add_receipt_event(kind: str, r: Record, **kwargs: Any) -> None:
        item = {
            "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
            "kind": kind,
            "level": r.level,
            "message": short(r.msg, 500),
        }
        item.update(kwargs)
        receipt_events.append(item)
        stats[f"receipt_{kind}"] += 1

    def add_confirmed_reply_receipt_event(kind: str, r: Record, **kwargs: Any) -> None:
        nonlocal pending_confirmed_reply_receipt
        if kind == "removed" and pending_confirmed_reply_receipt:
            for key in ("lane", "target_id", "reply_post_id"):
                kwargs.setdefault(key, pending_confirmed_reply_receipt.get(key, ""))
        item = {
            "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
            "kind": kind,
            "level": r.level,
            "message": short(r.msg, 500),
        }
        item.update(kwargs)
        confirmed_reply_receipts.append(item)
        stats[f"confirmed_reply_receipt_{kind}"] += 1
        if kind in {"written", "reconciled"}:
            pending_confirmed_reply_receipt = {
                key: item.get(key, "")
                for key in ("lane", "target_id", "reply_post_id")
                if item.get(key, "")
            }
        elif kind == "removed":
            pending_confirmed_reply_receipt = {}

    def add_asset_health(kind: str, r: Record, **kwargs: Any) -> None:
        item = {
            "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
            "kind": kind,
            "level": r.level,
            "message": short(r.msg, 500),
        }
        item.update(kwargs)
        asset_health.append(item)
        stats[f"asset_{kind}"] += 1

    def add_reply_media_context_event(r: Record, **kwargs: Any) -> None:
        item = {
            "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
            "level": r.level,
            "message": short(r.msg, 500),
        }
        item.update(kwargs)
        reply_media_context.append(item)
        stats["reply_media_context_events"] += 1

    for record_index, r in enumerate(records):
        msg = r.msg
        production_record = not is_selftest_log_path(r.path)

        # Lifecycle/config/state
        if production_record and (
            msg == "Bot starting"
            or msg == "Bot started successfully"
            or "Bot stopped by KeyboardInterrupt" in msg
        ):
            lifecycle.append({"time": r.ts.strftime("%Y-%m-%d %H:%M:%S"), "level": r.level, "message": msg.splitlines()[0]})

        config_pairs = extract_config_pairs(msg) if production_record else {}
        if config_pairs:
            configs.update(config_pairs)

        if production_record and (msg.startswith("State being saved:") or msg.startswith("Loaded state:")):
            state = try_parse_json_object_from_msg(msg) or parse_partial_state_from_msg(msg)
            if state is not None:
                latest_state = state
                latest_state_ts = r.ts

        is_self_test_error = (
            msg.startswith("SELFTEST FAIL:")
            or msg.startswith("Self-test finished with ")
            or ("Missing X credentials." in msg and any(e.get("message", "").startswith("SELFTEST FAIL:") for e in self_test_errors))
            or ("ENABLE_AUTO_REPLIES is True, but XAI_API_KEY is not set." in msg and any(e.get("message", "").startswith("SELFTEST FAIL:") for e in self_test_errors))
        )
        is_handled_reply_restriction = (
            is_reply_target_eligibility_restriction(msg)
            or "reply not allowed" in msg.lower()
            or "marking quote tweet as skipped without consuming reply quota" in msg.lower()
            or "not allowed to reply" in msg.lower()
            or "author has restricted who can reply" in msg.lower()
        )
        is_receipt_routine = (
            "Wrote confirmed regular-post receipt pending local reconciliation" in msg
            or "Wrote confirmed meme-post receipt pending local reconciliation" in msg
            or "Wrote confirmed reply receipt pending local reconciliation" in msg
            or "Removed reconciled regular-post receipt" in msg
            or "Removed reconciled meme-post receipt" in msg
            or "Removed reconciled confirmed-reply receipt" in msg
            or "Reconciling confirmed regular quote/image post receipt" in msg
            or "Reconciling confirmed meme post receipt" in msg
            or "Reconciling confirmed reply receipt" in msg
            or "Reconciled confirmed reply receipt before checking" in msg
            or "Reconciled regular quote/image receipt; not creating a second regular post" in msg
            or "Reconciled meme post receipt; not creating a second meme post" in msg
        )
        is_confirmed_post_recovery = (
            "Confirmed regular quote/image post_id=" in msg
            or "Confirmed meme post_id=" in msg
            or "Confirmed regular quote/image post " in msg
            or "Confirmed meme post " in msg
            or "REMOTE X POST WAS CONFIRMED; DO NOT RETRY MANUALLY" in msg
        )
        is_confirmed_reply_recovery = (
            "Malformed confirmed-reply receipt blocks" in msg
            or "Invalid confirmed-reply receipt blocks" in msg
            or "Semantically invalid confirmed-reply receipt blocks" in msg
            or "Confirmed reply receipt was applied in memory but state save failed" in msg
            or "Confirmed reply receipt state was saved but receipt removal failed" in msg
            or "Confirmed reply id=" in msg
            or "Confirmed quote-tweet reply id=" in msg
        )
        is_asset_metadata_warning = (
            "Quote analysis" in msg
            or "quote analysis" in msg
            or "Image analysis" in msg
            or "image analysis" in msg
            or "Skipping unanalysed current quote" in msg
            or "Image metadata stale" in msg
            or "absent from image analysis" in msg
            or "no valid per-image analysis" in msg
            or "Could not hash current image" in msg
            or "No analysed currently eligible regular-post images" in msg
            or "Image used-history still contains legacy integer entries" in msg
        )
        is_reply_media_context = msg.startswith("Reply media context")

        # Error/warning collection. Exclude routine KeyboardInterrupt, expected
        # self-test failures, and handled target restrictions from operational errors.
        if is_self_test_error:
            self_test_errors.append({
                "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                "level": r.level,
                "where": f"{r.src}:{r.line}",
                "message": short(msg, 900),
            })
        elif is_confirmed_post_recovery and r.level in {"ERROR", "CRITICAL", "WARNING"}:
            confirmed_post_recovery.append({
                "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                "level": r.level,
                "where": f"{r.src}:{r.line}",
                "message": short(msg, 900),
            })
        elif is_confirmed_reply_recovery and r.level in {"ERROR", "CRITICAL", "WARNING"}:
            confirmed_reply_recovery.append({
                "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                "level": r.level,
                "where": f"{r.src}:{r.line}",
                "message": short(msg, 900),
            })
        elif is_receipt_routine and r.level in {"ERROR", "CRITICAL", "WARNING"}:
            pass
        elif is_asset_metadata_warning and r.level in {"ERROR", "CRITICAL", "WARNING"}:
            pass
        elif is_reply_media_context and r.level in {"ERROR", "CRITICAL", "WARNING"}:
            pass
        elif is_handled_reply_restriction and r.level in {"ERROR", "CRITICAL", "WARNING"}:
            # The raw X API 403 is classified below. Follow-up warnings such as
            # "marking skipped without consuming quota" are expected handling.
            pass
        elif r.level in {"ERROR", "CRITICAL"} or (r.level == "WARNING" and "Bot stopped by KeyboardInterrupt" not in msg):
            errors.append({
                "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                "level": r.level,
                "where": f"{r.src}:{r.line}",
                "message": short(msg, 900),
                "_fingerprint": record_fingerprint(r),
            })

        if r.src == "ask_grok_for_reply" and msg.startswith("Asking Grok for reply."):
            active_xai_context = xai_usage_context_from_pending(pending_mention, pending_qt)

        usage, usage_error = parse_xai_usage_from_msg(msg)
        if usage is not None:
            xai_usage_events.append(
                summarize_xai_usage_event(
                    r,
                    usage,
                    active_xai_context or unknown_xai_usage_context(),
                )
            )
            stats["xai_usage_successes"] += 1
        elif usage_error is not None:
            xai_usage_parse_errors.append({
                "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                "where": f"{r.src}:{r.line}",
                "message": short(msg, 500),
                "error": usage_error,
            })
            stats["xai_usage_parse_errors"] += 1

        # Stable structured EVENT lines are used only to enrich pending state;
        # older human-readable success lines still define the final digest event.
        if msg.startswith("EVENT "):
            event_obj = try_parse_json_object_from_msg(msg)
            if event_obj and event_obj.get("event") == "main_post_posted":
                if event_obj.get("lane") == "quote_image":
                    pending_quote.update({
                        "post_id": event_obj.get("post_id"),
                        "line_no": event_obj.get("line_no"),
                        "image_no": event_obj.get("image_no"),
                        "image_basename": event_obj.get("image_basename"),
                        "image_score": event_obj.get("image_score"),
                    })
                    if pending_semantic_veto_event is not None:
                        pending_semantic_veto_event["confirmed_post"] = True
                        pending_semantic_veto_event["post_id"] = event_obj.get("post_id") or ""
                        pending_semantic_veto_event = None
                elif event_obj.get("lane") == "daily_meme":
                    pending_meme.update({
                        "post_id": event_obj.get("post_id"),
                        "file": event_obj.get("filename"),
                    })
            elif event_obj and event_obj.get("event") == "quote_image_semantic_veto_shadow":
                pending_semantic_veto_event = add_event(
                    "quote_image_semantic_veto_shadow",
                    r.ts,
                    quote_id=event_obj.get("quote_id") or "",
                    quote_hash=event_obj.get("quote_hash") or "",
                    quote_preview=event_obj.get("quote_preview") or "",
                    selected_image_hash=event_obj.get("selected_image_hash") or "",
                    selected_image_basename=event_obj.get("selected_image_basename") or "",
                    selected_image_source=event_obj.get("selected_image_source") or "other",
                    selected_score=event_obj.get("selected_score"),
                    shadow_status=event_obj.get("shadow_status") or "unknown",
                    would_veto_production_winner=event_obj.get("would_veto_production_winner"),
                    veto_category=event_obj.get("veto_category"),
                    veto_reason_codes=event_obj.get("veto_reason_codes") if isinstance(event_obj.get("veto_reason_codes"), list) else [],
                    veto_explanation=event_obj.get("veto_explanation") or "",
                    alternative_available=event_obj.get("alternative_available"),
                    alternative_image_basename=event_obj.get("alternative_image_basename"),
                    alternative_score=event_obj.get("alternative_score"),
                    score_delta_from_production_winner=event_obj.get("score_delta_from_production_winner"),
                    quote_has_no_allowed_candidate_globally=event_obj.get("quote_has_no_allowed_candidate_globally"),
                    manifest_policy_version=event_obj.get("manifest_policy_version") or "unavailable",
                    manifest_sha256=event_obj.get("manifest_sha256") or "",
                    lookup_latency_ms=event_obj.get("lookup_latency_ms"),
                    production_selection_changed=event_obj.get("production_selection_changed"),
                    confirmed_post=False,
                )
                quote_image_semantic_veto_events.append(pending_semantic_veto_event)
            elif event_obj and event_obj.get("event") == "historical_context_reply":
                status = str(event_obj.get("status") or "unknown")
                add_event(
                    "historical_context_reply",
                    r.ts,
                    status=status,
                    parent_post_id=event_obj.get("parent_post_id"),
                    quote_id=event_obj.get("quote_id"),
                    character_count=event_obj.get("character_count"),
                    weighted_character_count=event_obj.get("character_count"),
                    raw_character_count=event_obj.get("raw_character_count"),
                    verification_label=event_obj.get("verification_label") or "unavailable",
                    source_class=event_obj.get("source_class") or "unavailable",
                    historical_confidence=event_obj.get("historical_confidence") or "unavailable",
                    shortening_applied=event_obj.get("shortening_applied"),
                    meaning_omitted=event_obj.get("meaning_omitted"),
                    source_omitted=event_obj.get("source_omitted"),
                    verification_omitted=event_obj.get("verification_omitted"),
                    reason=event_obj.get("reason") or "",
                    reply_preview=event_obj.get("reply_preview") or "",
                )
                stats[f"historical_context_reply_status_{status}"] += 1
            elif event_obj and event_obj.get("event") == "reply_strategy_decision":
                retrieved_ids = event_obj.get("retrieved_quote_ids")
                add_event(
                    "reply_strategy_decision",
                    r.ts,
                    lane=event_obj.get("lane") or "unavailable",
                    target_id=event_obj.get("target_id") or "",
                    mode=event_obj.get("mode"),
                    humour_tone=event_obj.get("humour_tone"),
                    evidence_confidence=event_obj.get("evidence_confidence"),
                    retrieved_count=len(retrieved_ids) if isinstance(retrieved_ids, list) else None,
                    factual_claim=event_obj.get("factual_claim_made"),
                    grounded=event_obj.get("grounded"),
                    no_reply_reason=event_obj.get("no_reply_reason"),
                )
            elif event_obj and event_obj.get("event") == "reply_strategy_outcome":
                retrieved_ids = event_obj.get("retrieved_quote_ids")
                add_event(
                    "reply_strategy_outcome", r.ts,
                    status=event_obj.get("status") or "confirmed",
                    lane=event_obj.get("lane") or "unavailable",
                    target_id=event_obj.get("target_id") or "",
                    reply_post_id=event_obj.get("reply_post_id") or "",
                    mode=event_obj.get("mode"),
                    humour_tone=event_obj.get("humour_tone"),
                    evidence_confidence=event_obj.get("evidence_confidence"),
                    retrieved_count=len(retrieved_ids) if isinstance(retrieved_ids, list) else None,
                    factual_claim=event_obj.get("factual_claim_made"),
                    grounded=event_obj.get("grounded"),
                    no_reply_reason=event_obj.get("no_reply_reason"),
                    failure_reason=event_obj.get("failure_reason") or "",
                )
            elif event_obj and event_obj.get("event") == "reply_target_terminal":
                add_event(
                    "reply_target_terminal", r.ts,
                    lane=event_obj.get("lane") or "unavailable",
                    target_id=event_obj.get("target_id") or "",
                    outcome=event_obj.get("outcome") or "reply_not_permitted",
                    reason=event_obj.get("reason") or "",
                )
            elif event_obj and event_obj.get("event") == "reply_strategy_rejection":
                add_event(
                    "reply_strategy_rejection", r.ts,
                    lane=event_obj.get("lane") or "unavailable",
                    reason=event_obj.get("reason") or "other",
                )
            elif event_obj and event_obj.get("event") == "candidate_skipped":
                add_event(
                    "candidate_skipped", r.ts,
                    lane=event_obj.get("lane") or "unavailable",
                    target_id=event_obj.get("id") or "",
                    reason=event_obj.get("reason") or "other",
                )
            continue

        if "Wrote confirmed regular-post receipt pending local reconciliation" in msg:
            add_receipt_event("regular_written", r, lane="quote_image")
            continue
        if "Wrote confirmed meme-post receipt pending local reconciliation" in msg:
            add_receipt_event("meme_written", r, lane="daily_meme")
            continue
        m = re.search(
            r"Wrote confirmed reply receipt pending local reconciliation"
            r"(?: source=([^\s]+) target_id=([^\s]+) reply_post_id=([^\s]+))?",
            msg,
        )
        if m:
            kwargs: Dict[str, Any] = {}
            if m.group(1):
                kwargs.update({"lane": m.group(1), "target_id": m.group(2), "reply_post_id": m.group(3)})
            add_confirmed_reply_receipt_event("written", r, **kwargs)
            continue
        if "Removed reconciled regular-post receipt" in msg:
            add_receipt_event("regular_removed", r, lane="quote_image")
            continue
        if "Removed reconciled meme-post receipt" in msg:
            add_receipt_event("meme_removed", r, lane="daily_meme")
            continue
        m = re.search(
            r"Removed reconciled confirmed-reply receipt"
            r"(?: source=([^\s]+) target_id=([^\s]+) reply_post_id=([^\s]+))?",
            msg,
        )
        if m:
            kwargs = {}
            if m.group(1):
                kwargs.update({"lane": m.group(1), "target_id": m.group(2), "reply_post_id": m.group(3)})
            add_confirmed_reply_receipt_event("removed", r, **kwargs)
            continue
        m = re.search(r"Reconciling confirmed regular quote/image post receipt post_id=([^\s]+) quote_hash=([^\s]+) image=([^\s]+)", msg)
        if m:
            add_receipt_event("regular_reconciled", r, lane="quote_image", post_id=m.group(1), quote_hash=m.group(2), image=m.group(3))
            continue
        m = re.search(r"Reconciling confirmed meme post receipt post_id=([^\s]+) meme=([^\s]+)", msg)
        if m:
            add_receipt_event("meme_reconciled", r, lane="daily_meme", post_id=m.group(1), file=m.group(2))
            continue
        m = re.search(
            r"Reconciling confirmed reply receipt"
            r"(?: source=([^\s]+))? target_id=([^\s]+) reply_post_id=([^\s]+)",
            msg,
        )
        if m:
            lane = m.group(1) or pending_confirmed_reply_receipt.get("lane", "")
            add_confirmed_reply_receipt_event(
                "reconciled",
                r,
                lane=lane,
                target_id=m.group(2),
                reply_post_id=m.group(3),
            )
            continue
        if "Reconciled confirmed reply receipt before checking new mention candidates" in msg:
            add_confirmed_reply_receipt_event("replay_suppressed_mention_check", r, lane="mention")
            continue
        if "Reconciled confirmed reply receipt before checking new quote-tweet candidates" in msg:
            add_confirmed_reply_receipt_event("replay_suppressed_quote_tweet_check", r, lane="quote_tweet")
            continue
        if "Reconciled regular quote/image receipt; not creating a second regular post" in msg:
            add_receipt_event("regular_replay_suppressed_second_post", r, lane="quote_image")
            continue
        if "Reconciled meme post receipt; not creating a second meme post" in msg:
            add_receipt_event("meme_replay_suppressed_second_post", r, lane="daily_meme")
            continue
        if "Both regular and meme confirmed-post receipts exist" in msg:
            add_receipt_event("simultaneous_receipts_blocked", r, lane="main")
            continue
        if "regular-post receipt blocks" in msg or "meme-post receipt blocks" in msg:
            lane = "daily_meme" if "meme-post" in msg else "quote_image"
            add_receipt_event("invalid_or_unresolved_blocked", r, lane=lane)
            continue
        if "confirmed-reply receipt blocks" in msg:
            add_confirmed_reply_receipt_event("invalid_or_malformed_blocked", r)
            continue

        m = re.search(
            r"Reply media context fallback lane=([^\s]+) target_id=([^\s]+) "
            r"photos_expected=(\d+) initial_mode=([^\s]+) final_mode=([^\s]+) "
            r"status=([^\s]+) http_status=([^\s]+)",
            msg,
        )
        if m:
            add_reply_media_context_event(
                r,
                lane=m.group(1),
                target_id=m.group(2),
                photos=m.group(3),
                mode=m.group(5),
                status=m.group(6),
                http_status=m.group(7),
            )
            continue

        m = re.search(
            r"Reply media context(?: unavailable)? lane=([^\s]+) target_id=([^\s]+) "
            r"(?:photos=(\d+)|photos_expected=(\d+)) mode=([^\s]+) status=([^\s]+)",
            msg,
        )
        if m:
            add_reply_media_context_event(
                r,
                lane=m.group(1),
                target_id=m.group(2),
                photos=m.group(3) or m.group(4) or "",
                mode=m.group(5),
                status=m.group(6),
            )
            continue

        if is_asset_metadata_warning and r.level in {"ERROR", "CRITICAL", "WARNING"}:
            kind = "metadata_warning"
            if "Quote analysis" in msg or "quote analysis" in msg or "Skipping unanalysed current quote" in msg:
                kind = "quote_metadata_warning"
            elif "Image analysis" in msg or "image analysis" in msg or "Image metadata" in msg or "image analysis" in msg:
                kind = "image_metadata_warning"
            add_asset_health(kind, r)
            continue

        if ("API cooldown active" in msg or "due to API cooldown" in msg or "Skipping quote-tweet check due to API cooldown" in msg or "Skipping mention check due to API cooldown" in msg):
            stats["cooldown_mentions"] += 1
        m = re.search(r"API cooldown active until ([^:]+:\d{2}:\d{2}): (.+)$", msg)
        if m:
            cooldown_active.append({
                "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                "until": m.group(1).strip(),
                "reason": m.group(2).strip(),
            })
        m = re.search(r"Entering API cooldown after (429|repeated errors) until (.+)$", msg)
        if m:
            add_event("api_cooldown_entered", r.ts, reason=m.group(1), until=m.group(2).strip())
            continue
        m = re.search(r"Migrated legacy pickle file (.+) to JSON file (.+)$", msg)
        if m:
            add_event("used_history_migrated", r.ts, legacy_file=m.group(1).strip(), json_file=m.group(2).strip())
            continue
        m = re.search(r"Normalized used-history JSON ordering in (.+)$", msg)
        if m:
            add_event("used_history_normalized", r.ts, json_file=m.group(1).strip())
            continue
        x_error_match = None
        if r.src in {"x_request", "x_bearer_request"}:
            x_error_match = re.search(r"^X(?: bearer)? API error (\d+):", msg)
        if x_error_match:
            stats["x_api_errors"] += 1
            service = "X bearer" if "X bearer API error" in msg else "X OAuth"
            endpoint = "quote_tweets" if service == "X bearer" else "mentions/hot-post"
            status_code = x_error_match.group(1)
            if status_code == "403" and is_handled_reply_restriction:
                endpoint = "post/reply"
            target_id = str(
                pending_mention.get("mention_id")
                or pending_qt.get("quote_tweet_id")
                or ""
            )
            lane = (
                str(pending_mention.get("source") or "mention")
                if pending_mention
                else ("quote_tweet" if pending_qt else "unavailable")
            )
            api_error = {
                "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                "service": service,
                "endpoint": endpoint,
                "status": status_code,
                "target_id": target_id,
                "lane": lane,
                "message": short(msg, 240),
            }
            if status_code == "403" and is_handled_reply_restriction:
                handled_api_restrictions.append(api_error)
            else:
                api_errors.append(api_error)
            if status_code == "503":
                stats[f"x_api_503_{endpoint.replace('/', '_').replace('-', '_')}"] += 1
            elif status_code == "429":
                stats["x_api_429_rate_limit"] += 1
        if r.src in {"ask_grok_for_reply", "xai_request"} and msg.startswith("xAI error"):
            stats["xai_errors"] += 1
            m = re.search(r"xAI error (\d+):", msg)
            api_errors.append({
                "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                "service": "xAI",
                "endpoint": "chat",
                "status": m.group(1) if m else "",
                "message": short(msg, 240),
            })
            active_xai_context = None
        if r.src == "ask_grok_for_reply" and (
            msg.startswith("Grok generated usable reply:")
            or msg.startswith("Grok chose to skip")
        ):
            active_xai_context = None
        if api_errors:
            if msg.startswith("Rate Limit:"):
                api_errors[-1]["rate_limit"] = msg.split(":", 1)[1].strip()
            elif msg.startswith("Remaining:"):
                api_errors[-1]["remaining"] = msg.split(":", 1)[1].strip()
            else:
                m = re.search(r"Recorded (?:quote/)?x API error\. status_code=(\d+) errors_in_window=(\d+/\d+)", msg)
                if m:
                    api_errors[-1]["errors_in_window"] = m.group(2)
        if "Traceback" in msg:
            stats["tracebacks"] += 1

        # General quiet counters.
        if "No mentions returned" in msg:
            stats["no_mentions_checks"] += 1
        if "Starting mention reply check" in msg:
            stats["mention_checks"] += 1
        if msg.startswith("Fetching mentions."):
            stats["mention_fetch_attempts"] += 1
        if "Starting quote-tweet reply check" in msg:
            stats["quote_tweet_checks"] += 1
        if msg == "Due to check mentions":
            stats["normal_lane_due_checks"] += 1
        if msg == "Due to check quote tweets":
            stats["quote_lane_due_checks"] += 1

        # Hot-post reply watch / alternating-lane diagnostics.
        if "Hot-post reply check loaded" in msg:
            stats["hot_post_reply_watch_loads"] += 1
        if "/2/tweets/search/recent" in msg:
            stats["hot_post_recent_search_calls"] += 1
        m = re.search(r"Fetched (\d+) hot-post conversation candidate\(s\) for post_id=(\d+)", msg)
        if m:
            stats["hot_post_recent_search_successes"] += 1
            add_event(
                "hot_post_search_result",
                r.ts,
                original_post_id=m.group(2),
                candidates=int(m.group(1)),
            )
            continue
        m = re.search(r"Hot-post reply check returning (\d+) candidate\(s\)", msg)
        if m:
            stats["hot_post_reply_candidate_batches"] += 1
            stats["hot_post_reply_candidates_returned"] += int(m.group(1))
        if "Quote-tweet check is due, but normal/hot-post reply lane has priority" in msg:
            stats["priority_forced_normal_before_quote"] += 1
        if "Normal/hot-post reply lane posted; next reply-lane priority=quote" in msg:
            stats["priority_flipped_to_quote"] += 1
        if "Quote-tweet reply lane posted; next reply-lane priority=normal" in msg:
            stats["priority_flipped_to_normal"] += 1
        if "Normal/hot-post reply lane did not post; quote-tweet lane may use this slot" in msg:
            stats["priority_normal_first_refusal_no_post"] += 1
        m = re.search(r"Quote-tweet check status=([a-z_]+)", msg)
        if m:
            status = m.group(1)
            stats[f"quote_tweet_status_{status}"] += 1
            if status != "posted":
                stats["quote_tweet_checks_no_post"] += 1

        # Quote/image posts.
        m = re.search(
            r"Selected quote line_no=(\d+) quote_hash=([0-9a-fA-F]+) weight=([0-9.]+) seasonal_boost=(True|False)",
            msg,
        )
        if m:
            pending_quote.update({
                "line_no": int(m.group(1)),
                "quote_hash": m.group(2),
                "quote_weight": m.group(3),
                "seasonal_boost": m.group(4),
            })
            add_event(
                "quote_selected",
                r.ts,
                line_no=int(m.group(1)),
                quote_hash=m.group(2),
                weight=m.group(3),
                seasonal_boost=m.group(4),
            )
            continue

        m = re.search(
            r"Image cycle status: used_count=(\d+) currently_eligible=(\d+) remaining_count=(\d+) seasonally_excluded=(\d+) stale_excluded=(\d+) cycle_reset=(True|False)",
            msg,
        )
        if m:
            add_event(
                "image_cycle_status",
                r.ts,
                used_count=int(m.group(1)),
                currently_eligible=int(m.group(2)),
                remaining_count=int(m.group(3)),
                seasonally_excluded=int(m.group(4)),
                stale_excluded=int(m.group(5)),
                cycle_reset=m.group(6),
            )
            continue

        m = re.search(r"Selected matched image basename=([^\s]+) image_no=(\d+) score=([^\s]+) components=(.*)$", msg)
        if m:
            pending_quote.update({
                "image_basename": m.group(1),
                "image_no": int(m.group(2)),
                "image_score": m.group(3),
                "image_components": m.group(4).strip(),
            })
            add_event(
                "matched_image_selected",
                r.ts,
                image=m.group(1),
                image_no=int(m.group(2)),
                score=m.group(3),
                components=m.group(4).strip(),
            )
            continue

        m = re.search(
            r"REGULAR_IMAGE_SELECTED source=(original|generated) basename=([^\s]+) score=([^\s]+) "
            r"origin_quote_hash=([0-9a-fA-F]{64}|) origin_quote_match=(true|false) origin_quote_boost=([^\s]+)",
            msg,
        )
        if m:
            item = {
                "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                "source": m.group(1),
                "basename": m.group(2),
                "score": m.group(3),
                "origin_quote_hash": m.group(4),
                "origin_quote_match": m.group(5),
                "origin_quote_boost": m.group(6),
            }
            regular_image_usage_events.append(item)
            add_event(
                "regular_image_selected",
                r.ts,
                source=item["source"],
                basename=item["basename"],
                score=item["score"],
                origin_quote_hash=item["origin_quote_hash"],
                origin_quote_match=item["origin_quote_match"],
                origin_quote_boost=item["origin_quote_boost"],
            )
            continue

        if "ORIGINAL_EDITORIAL_SHADOW_RESULT " in msg:
            raw = msg.split("ORIGINAL_EDITORIAL_SHADOW_RESULT ", 1)[1].strip()
            try:
                parsed = json.loads(raw)
            except Exception as exc:
                errors.append({
                    "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                    "level": r.level,
                    "message": f"Malformed ORIGINAL_EDITORIAL_SHADOW_RESULT: {exc}: {short(raw, 240)}",
                })
                stats["original_editorial_shadow_parse_errors"] += 1
                continue
            if isinstance(parsed, dict):
                parsed["time"] = r.ts.strftime("%Y-%m-%d %H:%M:%S")
                original_editorial_shadow_events.append(parsed)
                stats["original_editorial_shadow_observations"] += 1
            continue

        if "GENERATED_IDENTITY_POLICY_SHADOW_RESULT " in msg:
            raw = msg.split("GENERATED_IDENTITY_POLICY_SHADOW_RESULT ", 1)[1].strip()
            try:
                parsed = json.loads(raw)
            except Exception as exc:
                errors.append({
                    "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                    "level": r.level,
                    "message": f"Malformed GENERATED_IDENTITY_POLICY_SHADOW_RESULT: {exc}: {short(raw, 240)}",
                })
                stats["generated_identity_shadow_parse_errors"] += 1
                continue
            if isinstance(parsed, dict):
                parsed["time"] = r.ts.strftime("%Y-%m-%d %H:%M:%S")
                generated_identity_shadow_events.append(parsed)
                stats["generated_identity_shadow_observations"] += 1
            continue

        if "GENERATED_IDENTITY_POLICY_APPLIED " in msg:
            raw = msg.split("GENERATED_IDENTITY_POLICY_APPLIED ", 1)[1].strip()
            try:
                parsed = json.loads(raw)
            except Exception as exc:
                errors.append({"time": r.ts.strftime("%Y-%m-%d %H:%M:%S"), "level": r.level, "message": f"Malformed GENERATED_IDENTITY_POLICY_APPLIED: {exc}: {short(raw, 240)}"})
                stats["generated_identity_policy_parse_errors"] += 1
                continue
            if isinstance(parsed, dict):
                parsed["time"] = r.ts.strftime("%Y-%m-%d %H:%M:%S")
                generated_identity_policy_events.append(parsed)
                stats["generated_identity_policy_observations"] += 1
            continue

        m = re.search(
            r"GENERATED_IMAGE_SPACING_STATUS pool_enabled=(true|false) allowed=(true|false) "
            r"original_posts_since_generated=(\d+) required=(\d+)",
            msg,
        )
        if m:
            item = {
                "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                "kind": "status",
                "pool_enabled": m.group(1),
                "allowed": m.group(2),
                "original_posts_since_generated": int(m.group(3)),
                "required": int(m.group(4)),
            }
            latest_generated_image_spacing = item
            generated_image_spacing_events.append(item)
            continue

        m = re.search(
            r"GENERATED_IMAGE_SPACING_STATE_UPDATED pool_enabled=(true|false) allowed=(true|false) "
            r"original_posts_since_generated=(\d+) required=(\d+) image_source=(\S+) image=(\S+)",
            msg,
        )
        if m:
            item = {
                "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                "kind": "state_updated",
                "pool_enabled": m.group(1),
                "allowed": m.group(2),
                "original_posts_since_generated": int(m.group(3)),
                "required": int(m.group(4)),
                "image_source": m.group(5),
                "image": m.group(6),
            }
            latest_generated_image_spacing = item
            generated_image_spacing_events.append(item)
            continue

        m = re.search(
            r"GENERATED_IMAGE_POOL_BLOCKED_BY_SPACING original_posts_since_generated=(\d+) required=(\d+)",
            msg,
        )
        if m:
            generated_image_spacing_events.append(
                {
                    "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                    "kind": "blocked",
                    "original_posts_since_generated": int(m.group(1)),
                    "required": int(m.group(2)),
                }
            )
            continue

        m = re.search(r"Quote cycle is seasonally exhausted: (\d+) unused quote\(s\) are hard-excluded today; resetting quote cycle", msg)
        if m:
            add_event("quote_cycle_reset", r.ts, reason="seasonal_exhaustion", affected=m.group(1))
            continue

        m = re.search(r"Quote cycle is exhausted by currently nonselectable quote\(s\); resetting quote cycle\. unused_non_empty=(\d+) full_selectable=(\d+) full_hard_excluded=(\d+)", msg)
        if m:
            add_event(
                "quote_cycle_reset",
                r.ts,
                reason="nonselectable_exhaustion",
                affected=m.group(1),
                full_selectable=m.group(2),
                full_hard_excluded=m.group(3),
            )
            continue

        m = re.search(r"Selected line_no=(\d+) text=(.*)$", msg, re.S)
        if m:
            pending_quote["line_no"] = int(m.group(1))
            pending_quote["text"] = lit(m.group(2))
            continue

        m = re.search(r"Posting quote/image\. line_no=(\d+) image_no=(\d+) image=(.*)$", msg)
        if m:
            pending_quote.update({
                "start_time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                "line_no": int(m.group(1)),
                "image_no": int(m.group(2)),
                "image": m.group(3).strip(),
            })
            continue

        if msg.startswith("Quote text="):
            pending_quote["text"] = lit(msg.split("=", 1)[1])
            continue

        m = re.search(r"Creating X post\. reply_to_id=([^\s]+) media_count=(\d+) made_with_ai=(True|False)\b", msg)
        if m:
            reply_to_id = m.group(1)
            media_count = int(m.group(2))
            made_with_ai = m.group(3).lower()
            if pending_quote and reply_to_id == "None" and media_count > 0:
                pending_quote["made_with_ai"] = made_with_ai
                image_basename = pending_quote.get("image_basename")
                for item in reversed(regular_image_usage_events):
                    if item.get("basename") == image_basename and not item.get("made_with_ai"):
                        item["made_with_ai"] = made_with_ai
                        break
            continue

        m = re.search(r"Quote/image posted successfully\. posted_id=(\d+)", msg)
        if m:
            add_event(
                "quote_image_posted",
                r.ts,
                post_id=m.group(1),
                line_no=pending_quote.get("line_no"),
                image_no=pending_quote.get("image_no"),
                image_basename=pending_quote.get("image_basename"),
                image_score=pending_quote.get("image_score"),
                quote_hash=pending_quote.get("quote_hash"),
                text=pending_quote.get("text", ""),
                image=pending_quote.get("image", ""),
                made_with_ai=pending_quote.get("made_with_ai", ""),
            )
            pending_quote = {}
            continue

        # Daily meme posts.
        if msg.startswith("Posting meme image:"):
            pending_meme = {"start_time": r.ts.strftime("%Y-%m-%d %H:%M:%S"), "image": msg.split(":", 1)[1].strip()}
            continue
        if msg.startswith("Meme image summary for cache:"):
            pending_meme["summary"] = lit(msg.split(":", 1)[1])
            continue
        m = re.search(r"Daily meme posted successfully\. posted_id=(\d+) file=(.+)$", msg)
        if m:
            add_event(
                "daily_meme_posted",
                r.ts,
                post_id=m.group(1),
                file=m.group(2).strip(),
                summary=pending_meme.get("summary", ""),
                image=pending_meme.get("image", ""),
            )
            pending_meme = {}
            continue

        # Created X post: remember it so reply/post events can attach if needed.
        if "Created X post successfully" in msg:
            post_id, post_text = try_parse_response_id_text(msg)
            last_created_post = {"time": r.ts, "post_id": post_id, "post_text": post_text}
            stats["created_x_posts"] += 1
            continue

        # Normal mention lane, including synthetic hot-post reply candidates.
        m = re.search(r"Considering (mention|hot_post_reply) id=(\d+) author_id=([^\s]+) text=(.*)$", msg, re.S)
        if m:
            source = m.group(1)
            id_key = "mention_id" if source == "mention" else "hot_post_reply_id"
            pending_mention = {
                "source": source,
                id_key: m.group(2),
                "mention_id": m.group(2),  # kept for backward-compatible post/reply matching
                "author_id": m.group(3),
                "incoming_text": lit(m.group(4)),
                "considered_at": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                "considered_seq": record_index,
            }
            continue

        m = re.search(r"Generated reply to mention (\d+): (.*)$", msg, re.S)
        if m:
            if pending_mention.get("mention_id") != m.group(1):
                pending_mention = {"mention_id": m.group(1), "source": "unknown"}
            pending_mention["reply"] = lit(m.group(2))
            pending_mention["generated_at"] = r.ts.strftime("%Y-%m-%d %H:%M:%S")
            active_xai_context = None
            continue

        m = re.search(r"Recorded and cached own auto-reply id=(\d+)", msg)
        if m and pending_mention:
            pending_mention["reply_post_id"] = m.group(1)
            continue

        if msg == "Reply posted successfully" and pending_mention:
            if not pending_mention.get("reply_post_id") and last_created_post.get("post_id"):
                pending_mention["reply_post_id"] = last_created_post.get("post_id")
            source = pending_mention.get("source", "mention")
            if source == "hot_post_reply":
                data = dict(pending_mention)
                data.pop("mention_id", None)
                data.pop("source", None)
                add_event("hot_post_reply_posted", r.ts, **data)
            else:
                data = dict(pending_mention)
                data.pop("source", None)
                add_event("mention_reply_posted", r.ts, **data)
            pending_mention = {}
            continue

        m = re.search(r"No usable reply generated for (mention|hot_post_reply) (\d+)", msg)
        if m:
            source = m.group(1)
            if source == "hot_post_reply":
                add_event(
                    "hot_post_reply_grok_skip",
                    r.ts,
                    hot_post_reply_id=m.group(2),
                    author_id=pending_mention.get("author_id"),
                    incoming_text=pending_mention.get("incoming_text", ""),
                )
            else:
                add_event(
                    "mention_grok_skip",
                    r.ts,
                    mention_id=m.group(2),
                    author_id=pending_mention.get("author_id"),
                    incoming_text=pending_mention.get("incoming_text", ""),
                )
            pending_mention = {}
            active_xai_context = None
            continue

        m = re.search(r"Skipping (mention|hot_post_reply) (\d+): (.*)$", msg)
        if m:
            source, ident, reason = m.group(1), m.group(2), m.group(3).strip()
            if source == "hot_post_reply":
                if "already replied/skipped" in reason:
                    routine_skip_counts["hot_post_reply_already_handled"] += 1
                else:
                    add_event(
                        "hot_post_reply_skipped",
                        r.ts,
                        hot_post_reply_id=ident,
                        author_id=pending_mention.get("author_id"),
                        incoming_text=pending_mention.get("incoming_text", ""),
                        reason=reason,
                    )
            else:
                add_event(
                    "mention_skipped",
                    r.ts,
                    mention_id=ident,
                    author_id=pending_mention.get("author_id"),
                    incoming_text=pending_mention.get("incoming_text", ""),
                    reason=reason,
                )
            pending_mention = {}
            active_xai_context = None
            continue

        m = re.search(r"Skipping hot-post candidate (\d+): (.*)$", msg, re.S)
        if m:
            add_event("hot_post_reply_skipped", r.ts, hot_post_reply_id=m.group(1), reason=m.group(2).strip())
            continue

        # Quote tweet lane.
        m = re.search(r"Considering quote tweet id=(\d+) author_id=([^\s]+) original_post_id=(\d+) text=(.*)$", msg, re.S)
        if m:
            pending_qt = {
                "quote_tweet_id": m.group(1),
                "author_id": m.group(2),
                "original_post_id": m.group(3),
                "incoming_text": lit(m.group(4)),
                "considered_at": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                "considered_seq": record_index,
            }
            continue

        m = re.search(r"Generated reply to quote tweet (\d+): (.*)$", msg, re.S)
        if m:
            if pending_qt.get("quote_tweet_id") != m.group(1):
                pending_qt = {"quote_tweet_id": m.group(1)}
            pending_qt["reply"] = lit(m.group(2))
            pending_qt["generated_at"] = r.ts.strftime("%Y-%m-%d %H:%M:%S")
            active_xai_context = None
            continue

        m = re.search(r"Recorded and cached own quote-tweet auto-reply id=(\d+)", msg)
        if m and pending_qt:
            pending_qt["reply_post_id"] = m.group(1)
            continue

        if msg == "Quote-tweet reply posted successfully" and pending_qt:
            if not pending_qt.get("reply_post_id") and last_created_post.get("post_id"):
                pending_qt["reply_post_id"] = last_created_post.get("post_id")
            add_event("quote_tweet_reply_posted", r.ts, **pending_qt)
            pending_qt = {}
            continue

        m = re.search(r"No usable reply generated for quote tweet (\d+)", msg)
        if m:
            add_event(
                "quote_tweet_grok_skip",
                r.ts,
                quote_tweet_id=m.group(1),
                author_id=pending_qt.get("author_id"),
                original_post_id=pending_qt.get("original_post_id"),
                incoming_text=pending_qt.get("incoming_text", ""),
            )
            pending_qt = {}
            active_xai_context = None
            continue

        m = re.search(r"Skipping quote tweet (\d+): (.*)$", msg, re.S)
        if m:
            reason = m.group(2).strip()
            if reason == "already seen/replied/skipped":
                routine_skip_counts["quote_tweet_already_seen"] += 1
            elif "not a direct quote" in reason:
                routine_skip_counts["quote_tweet_not_direct"] += 1
                add_event("quote_tweet_skipped", r.ts, quote_tweet_id=m.group(1), reason=reason)
            elif "authored by own account" in reason:
                routine_skip_counts["quote_tweet_self_authored"] += 1
                add_event("quote_tweet_skipped", r.ts, quote_tweet_id=m.group(1), reason=reason)
            else:
                add_event("quote_tweet_skipped", r.ts, quote_tweet_id=m.group(1), reason=reason)
            pending_qt = {}
            active_xai_context = None
            continue

        # Other interesting skip/rate/cap messages.
        if msg in {
            "Daily generated/replied cap reached",
            "Skipping mention check: minimum interval between replies not reached",
            "Skipping quote-tweet check: total daily reply cap reached",
            "Skipping quote-tweet check: daily quote-reply cap reached",
        }:
            routine_skip_counts[msg] += 1
            if msg == "Skipping mention check: minimum interval between replies not reached":
                stats["mention_checks_skipped_spacing"] += 1

    latest_state_summary: Dict[str, Any] = {}
    if latest_state is not None:
        latest_state_summary = summarize_latest_state(latest_state, latest_state_ts)

    self_test_times = {str(item.get("time")) for item in self_test_errors}
    api_error_times = {str(item.get("time")) for item in api_errors}
    handled_restriction_times = [
        datetime.strptime(str(item["time"]), "%Y-%m-%d %H:%M:%S")
        for item in handled_api_restrictions
        if item.get("time")
    ]
    media_upload_incidents, media_suppressed_fingerprints = correlate_media_upload_incidents(records, max_text)
    remaining_errors: List[Dict[str, Any]] = []
    for item in errors:
        message = str(item.get("message", ""))
        timestamp = str(item.get("time", ""))
        if item.get("_fingerprint") in media_suppressed_fingerprints:
            continue
        if timestamp in self_test_times and (
            "Missing X credentials." in message
            or "ENABLE_AUTO_REPLIES is True, but XAI_API_KEY is not set." in message
        ):
            self_test_errors.append(item)
            continue
        if timestamp in api_error_times and (
            message.startswith("Failed to get mention")
            or message.startswith("Failed to get quote")
            or message.startswith("Failed to fetch quote")
        ):
            continue
        if message.startswith(("Failed to post generated reply", "Unexpected failure posting generated reply")):
            try:
                error_time = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                error_time = None
            if error_time is not None and any(
                seconds_between(error_time, restriction_time) <= 5
                for restriction_time in handled_restriction_times
            ):
                continue
        if message.startswith("Entering API cooldown after repeated errors"):
            try:
                error_time = datetime.strptime(timestamp, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                error_time = None
            if error_time is not None and any(
                seconds_between(error_time, restriction_time) <= 5
                for restriction_time in handled_restriction_times
            ):
                continue
        remaining_errors.append(item)
    errors = remaining_errors

    # Build a short automatic headline.
    serious_errors = [e for e in errors if e["level"] in {"ERROR", "CRITICAL"}]
    headline = []
    headline.append(f"{stats.get('quote_image_posted', 0)} quote/image post(s)")
    headline.append(f"{stats.get('daily_meme_posted', 0)} daily meme(s)")
    headline.append(f"{stats.get('mention_reply_posted', 0)} mention reply/replies")
    headline.append(f"{stats.get('hot_post_reply_posted', 0)} hot-post reply/replies")
    headline.append(f"{stats.get('quote_tweet_reply_posted', 0)} quote-tweet reply/replies")
    headline.append(
        f"{stats.get('historical_context_reply_status_completed', 0)} "
        "historical context reply/replies completed"
    )
    headline.append(f"{stats.get('mention_grok_skip', 0) + stats.get('hot_post_reply_grok_skip', 0) + stats.get('quote_tweet_grok_skip', 0)} Grok skip(s)")
    if serious_errors:
        headline.append(f"{len(serious_errors)} operational error(s)")
    else:
        headline.append("no serious errors")
    if handled_api_restrictions:
        handled_incidents = {
            (
                str(item.get("service") or ""),
                str(item.get("status") or ""),
                str(item.get("target_id") or item.get("message") or ""),
            )
            for item in handled_api_restrictions
        }
        headline.append(f"{len(handled_incidents)} handled API restriction incident(s)")
    handled_media_fallbacks = [item for item in media_upload_incidents if item.get("status") == "handled"]
    unrecovered_media = [item for item in media_upload_incidents if item.get("status") != "handled"]
    if handled_media_fallbacks:
        headline.append(f"{len(handled_media_fallbacks)} handled media-upload fallback(s)")
    if unrecovered_media:
        headline.append(f"{len(unrecovered_media)} unrecovered media-upload failure(s)")
    if self_test_errors:
        selftest_fail_checks = sum(1 for e in self_test_errors if str(e.get("message", "")).startswith("SELFTEST FAIL:"))
        headline.append(f"self-test failures: {selftest_fail_checks} check(s)")
    if confirmed_post_recovery:
        headline.append(f"{len(confirmed_post_recovery)} confirmed-post recovery warning(s)")
    if confirmed_reply_recovery:
        headline.append(f"{len(confirmed_reply_recovery)} confirmed-reply recovery warning(s)")
    blocking_receipts = [
        item for item in receipt_events
        if item.get("kind") in {"invalid_or_unresolved_blocked", "simultaneous_receipts_blocked"}
    ]
    if blocking_receipts:
        headline.append(f"{len(blocking_receipts)} receipt block(s)")
    if asset_health:
        headline.append(f"{len(asset_health)} asset metadata warning(s)")
    cooldown_until_epoch = int_or_none(latest_state_summary.get("api_cooldown_until_epoch"))
    x_write_cooldown_until_epoch = int_or_none(latest_state_summary.get("x_write_api_cooldown_until_epoch"))
    xai_cooldown_until_epoch = int_or_none(latest_state_summary.get("xai_api_cooldown_until_epoch"))
    quote_cooldown_until_epoch = int_or_none(latest_state_summary.get("quote_api_cooldown_until_epoch"))
    latest_state_time = parse_dt(latest_state_summary.get("time"))
    window_start_epoch = int(records[0].ts.timestamp()) if records else None

    def cooldown_headline(until_epoch: int | None, *, label: str) -> str | None:
        if not until_epoch or not latest_state_time:
            return None
        latest_state_epoch = int(latest_state_time.timestamp())
        if latest_state_epoch < until_epoch:
            return f"{label} cooldown active now"
        if window_start_epoch is not None and until_epoch >= window_start_epoch:
            return f"{label} cooldown occurred, now expired"
        return None

    cooldown_labels = [
        label
        for label in (
            cooldown_headline(cooldown_until_epoch, label="X read API"),
            cooldown_headline(x_write_cooldown_until_epoch, label="X write API"),
            cooldown_headline(xai_cooldown_until_epoch, label="xAI"),
            cooldown_headline(quote_cooldown_until_epoch, label="quote API"),
        )
        if label
    ]
    if cooldown_labels:
        headline.extend(cooldown_labels)
    elif stats.get("api_cooldown_entered", 0):
        headline.append("API cooldown occurred")
    else:
        headline.append("no API cooldown")

    max_auto = int_or_none(configs.get("MAX_AUTO_REPLIES_PER_DAY"))
    max_quote = int_or_none(configs.get("MAX_QUOTE_REPLIES_PER_DAY"))
    used_auto = int_or_none(latest_state_summary.get("daily_reply_count"))
    used_quote = int_or_none(latest_state_summary.get("daily_quote_reply_count"))
    derived = {
        "reply_budget": {
            "auto_used": used_auto,
            "auto_limit": max_auto,
            "auto_remaining": (max_auto - used_auto) if max_auto is not None and used_auto is not None else None,
            "quote_used": used_quote,
            "quote_limit": max_quote,
            "quote_remaining": (max_quote - used_quote) if max_quote is not None and used_quote is not None else None,
        },
        "reply_lane_priority": {
            "current_next_priority": latest_state_summary.get("next_reply_lane_priority"),
            "flipped_to_quote": stats.get("priority_flipped_to_quote", 0),
            "flipped_to_normal": stats.get("priority_flipped_to_normal", 0),
            "forced_normal_before_quote": stats.get("priority_forced_normal_before_quote", 0),
            "normal_first_refusal_no_post": stats.get("priority_normal_first_refusal_no_post", 0),
        },
    }

    not_rate_limited = any(
        str(item.get("remaining", "")).isdigit()
        and int(str(item.get("remaining"))) > 0
        and str(item.get("status")) != "429"
        for item in api_errors
    )
    all_api_failures = [*api_errors, *handled_api_restrictions]
    api_status_counts = Counter(str(item.get("status") or "unavailable") for item in all_api_failures)
    target_eligibility_403_count = sum(
        str(item.get("status")) == "403"
        and is_reply_target_eligibility_restriction(str(item.get("message") or ""))
        for item in all_api_failures
    )
    posting_attempt_count = sum(item.get("endpoint") == "post/reply" for item in all_api_failures)
    transient_failure_count = sum(
        str(item.get("status") or "") in {"408", "425"}
        or str(item.get("status") or "").startswith("5")
        for item in all_api_failures
    )
    rate_limit_failure_count = sum(str(item.get("status") or "") == "429" for item in all_api_failures)
    legacy_cooldown_from_target_restriction_count = sum(
        any(
            seconds_between(
                parse_dt(str(event.get("time") or "")) or datetime.min,
                restriction_time,
            ) <= 5
            for restriction_time in handled_restriction_times
        )
        for event in events
        if event.get("kind") == "api_cooldown_entered" and event.get("reason") == "repeated errors"
    )
    unique_api_incidents = {
        (
            str(item.get("service") or ""),
            str(item.get("status") or ""),
            str(item.get("target_id") or item.get("endpoint") or ""),
            "" if item.get("target_id") else str(item.get("message") or ""),
        )
        for item in all_api_failures
    }
    post_cooldown_errors: List[Dict[str, Any]] = []
    cooldown_events = [ev for ev in events if ev.get("kind") == "api_cooldown_entered"]
    for item in api_errors:
        try:
            item_ts = datetime.strptime(item["time"], "%Y-%m-%d %H:%M:%S")
        except (KeyError, TypeError, ValueError):
            continue
        for ev in cooldown_events:
            until = parse_dt(str(ev.get("until", "")))
            if until and item_ts > until:
                post_cooldown_errors.append(item)
                break

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
            evidence_confidence=decision.get("evidence_confidence"),
            retrieved_count=decision.get("retrieved_count"),
            factual_claim=decision.get("factual_claim"),
            grounded=decision.get("grounded"),
            no_reply_reason=decision.get("no_reply_reason"),
            failure_reason="reply_not_permitted",
            legacy_inferred=True,
        )
        explicit_strategy_outcome_targets.add(key)

    context_quality = historical_context_quality_summary(events)
    strategy_quality = reply_strategy_summary(events)
    routine_reason_map = {
        "Daily generated/replied cap reached": "daily_cap",
        "Skipping mention check: minimum interval between replies not reached": "spacing",
        "Skipping quote-tweet check: total daily reply cap reached": "daily_cap",
        "Skipping quote-tweet check: daily quote-reply cap reached": "daily_cap",
        "hot_post_reply_already_handled": "already_replied",
        "quote_tweet_already_seen": "already_replied",
        "quote_tweet_not_direct": "not_direct_quote",
        "quote_tweet_self_authored": "own_account",
    }
    compact_routine = Counter(strategy_quality.get("routine_skip_reason_counts") or {})
    for reason, count in routine_skip_counts.items():
        compact_routine[routine_reason_map.get(reason, reason)] += count
    strategy_quality["routine_skip_reason_counts"] = dict(compact_routine.most_common())

    return {
        "summary": {
            "record_count": len(records),
            "time_start": records[0].ts.strftime("%Y-%m-%d %H:%M:%S") if records else None,
            "time_end": records[-1].ts.strftime("%Y-%m-%d %H:%M:%S") if records else None,
            "headline": "; ".join(headline),
            "stats": dict(stats),
            "routine_skip_counts": dict(routine_skip_counts),
        },
        "latest_config": configs,
        "latest_state": latest_state_summary,
        "derived": derived,
        "api_health": {
            "errors": api_errors,
            "handled_restrictions": handled_api_restrictions,
            "cooldown_active": cooldown_active,
            "post_cooldown_errors": post_cooldown_errors,
            "not_rate_limited": not_rate_limited,
            "has_5xx_failures": any(str(item.get("status") or "").startswith("5") for item in all_api_failures),
            "status_counts": dict(sorted(api_status_counts.items())),
            "unique_incident_count": len(unique_api_incidents),
            "posting_attempt_count": posting_attempt_count,
            "target_eligibility_403_count": target_eligibility_403_count,
            "transient_failure_count": transient_failure_count,
            "rate_limit_failure_count": rate_limit_failure_count,
            "legacy_cooldown_from_target_restriction_count": legacy_cooldown_from_target_restriction_count,
        },
        "main_post_recovery": {
            "receipt_events": receipt_events,
            "confirmed_post_recovery": confirmed_post_recovery,
        },
        "confirmed_reply_recovery": {
            "receipt_events": confirmed_reply_receipts,
            "warnings": confirmed_reply_recovery,
        },
        "historical_context_replies": {
            "events": [item for item in events if item.get("kind") == "historical_context_reply"],
            "status_counts": {
                key.removeprefix("historical_context_reply_status_"): value
                for key, value in sorted(stats.items())
                if key.startswith("historical_context_reply_status_")
            },
        },
        "historical_context_quality": context_quality,
        "reply_strategy": strategy_quality,
        "reply_media_context": reply_media_context,
        "asset_health": asset_health,
        "media_upload": {
            "incidents": media_upload_incidents,
            "handled_fallbacks": handled_media_fallbacks,
            "unrecovered_failures": unrecovered_media,
        },
        "regular_image_usage": {
            "events": regular_image_usage_events,
            "summary": regular_image_usage_summary(regular_image_usage_events),
        },
        "original_editorial_shadow": {
            "events": original_editorial_shadow_events,
            "summary": original_editorial_shadow_summary(original_editorial_shadow_events),
        },
        "generated_identity_shadow": {
            "events": generated_identity_shadow_events,
            "summary": generated_identity_shadow_summary(generated_identity_shadow_events),
        },
        "generated_identity_policy": {
            "events": generated_identity_policy_events,
            "summary": generated_identity_policy_summary(generated_identity_policy_events),
        },
        "quote_image_semantic_veto_shadow": {
            "events": quote_image_semantic_veto_events,
            "summary": quote_image_semantic_veto_summary(quote_image_semantic_veto_events),
        },
        "generated_image_spacing": {
            "latest": latest_generated_image_spacing,
            "events": generated_image_spacing_events,
        },
        "xai_usage": {
            "events": xai_usage_events,
            "totals": xai_usage_totals(xai_usage_events),
            "parse_errors": xai_usage_parse_errors,
        },
        "resume_context": {
            "active_xai_context": active_xai_context,
            "pending_mention": pending_mention if active_xai_context else None,
            "pending_qt": pending_qt if active_xai_context else None,
        },
        "lifecycle": lifecycle[-12:],
        "events": events,
        "self_test_errors": self_test_errors[-40:],
        "errors_and_warnings": errors[-40:],
    }


def md_table_row(cols: List[Any]) -> str:
    def esc(x: Any) -> str:
        s = short(x, 240).replace("|", "\\|")
        return s
    return "| " + " | ".join(esc(c) for c in cols) + " |"


def refresh_derived(report: Dict[str, Any]) -> None:
    """Recalculate derived sections after any carried-forward context is applied."""
    configs = report.get("latest_config") or {}
    st = report.get("latest_state") or {}
    stats = report.get("summary", {}).get("stats", {}) or {}

    # Cooldown human timestamps are derived from the epoch. Recompute after
    # saved-context merging so a cleared epoch=0 cannot keep an old date/reason.
    for prefix in ("api_cooldown", "x_write_api_cooldown", "xai_api_cooldown", "quote_api_cooldown"):
        epoch_key = f"{prefix}_until_epoch"
        human_key = f"{prefix}_until_human"
        reason_key = f"{prefix}_reason"
        if epoch_key not in st:
            continue

        until = int_or_none(st.get(epoch_key))
        if until and until > 0:
            st[human_key] = epoch_to_human(until)
        else:
            st[human_key] = None
            st[reason_key] = ""

    max_auto = int_or_none(configs.get("MAX_AUTO_REPLIES_PER_DAY"))
    max_quote = int_or_none(configs.get("MAX_QUOTE_REPLIES_PER_DAY"))
    used_auto = int_or_none(st.get("daily_reply_count"))
    used_quote = int_or_none(st.get("daily_quote_reply_count"))

    report["derived"] = {
        "reply_budget": {
            "auto_used": used_auto,
            "auto_limit": max_auto,
            "auto_remaining": (max_auto - used_auto) if max_auto is not None and used_auto is not None else None,
            "quote_used": used_quote,
            "quote_limit": max_quote,
            "quote_remaining": (max_quote - used_quote) if max_quote is not None and used_quote is not None else None,
            "has_any_budget_input": any(x is not None for x in (used_auto, max_auto, used_quote, max_quote)),
            "state_carried_forward": bool(st.get("_carried_forward")),
            "config_carried_forward": bool(configs.get("_carried_forward")),
            "config_carried_from_log_backscan": bool(configs.get("_carried_from_log_backscan")),
            "config_backscan_timestamp": configs.get("_log_backscan_timestamp"),
            "state_filled_from_previous": bool(st.get("_filled_from_previous")),
            "config_filled_from_previous": bool(configs.get("_filled_from_previous")),
            "config_filled_from_log_backscan": bool(configs.get("_filled_from_log_backscan")),
        },
        "reply_lane_priority": {
            "current_next_priority": st.get("next_reply_lane_priority"),
            "has_priority_state": st.get("next_reply_lane_priority") is not None,
            "state_carried_forward": bool(st.get("_carried_forward")),
            "state_filled_from_previous": bool(st.get("_filled_from_previous")),
            "config_carried_forward": bool(configs.get("_carried_forward")),
            "config_carried_from_log_backscan": bool(configs.get("_carried_from_log_backscan")),
            "config_backscan_timestamp": configs.get("_log_backscan_timestamp"),
            "config_filled_from_previous": bool(configs.get("_filled_from_previous")),
            "config_filled_from_log_backscan": bool(configs.get("_filled_from_log_backscan")),
            "normal_lane_due_checks": stats.get("normal_lane_due_checks", 0),
            "mention_function_entries": stats.get("mention_checks", 0),
            "mention_fetch_attempts": stats.get("mention_fetch_attempts", 0),
            "mention_checks_skipped_spacing": stats.get("mention_checks_skipped_spacing", 0),
            "mention_checks_skipped_cooldown": stats.get("cooldown_mentions", 0),
            "quote_lane_due_checks": stats.get("quote_lane_due_checks", 0),
            "flipped_to_quote": stats.get("priority_flipped_to_quote", 0),
            "flipped_to_normal": stats.get("priority_flipped_to_normal", 0),
            "forced_normal_before_quote": stats.get("priority_forced_normal_before_quote", 0),
            "normal_first_refusal_no_post": stats.get("priority_normal_first_refusal_no_post", 0),
            "quote_tweet_status_posted": stats.get("quote_tweet_status_posted", 0),
            "quote_tweet_status_checked": stats.get("quote_tweet_status_checked", 0),
            "quote_tweet_status_skipped_spacing": stats.get("quote_tweet_status_skipped_spacing", 0),
            "quote_tweet_status_skipped_cap": stats.get("quote_tweet_status_skipped_cap", 0),
            "quote_tweet_status_skipped_cooldown": stats.get("quote_tweet_status_skipped_cooldown", 0),
            "quote_tweet_checks_no_post": stats.get("quote_tweet_checks_no_post", 0),
        },
    }


def apply_saved_context(
    report: Dict[str, Any],
    state_file: Path,
    *,
    window_end: Optional[datetime] = None,
) -> None:
    """Fill missing latest_state/latest_config from the previous digest run.

    v4 merges field-by-field. That means a current state snapshot can be
    combined with a carried-forward config snapshot, so the reply budget section
    can still show e.g. "5 / 12" even in windows with no startup Config line.
    """
    old = read_resume_data(state_file)
    previous_state = old.get("last_known_latest_state") or {}
    if previous_state and not state_context_is_within_window(previous_state, window_end):
        previous_state = {}

    report["latest_state"] = merge_context(
        report.get("latest_state") or {},
        previous_state,
    )
    report["latest_config"] = merge_context(
        report.get("latest_config") or {},
        old.get("last_known_latest_config") or {},
    )
    generated_spacing = report.get("generated_image_spacing")
    if isinstance(generated_spacing, dict) and not generated_spacing.get("latest"):
        previous_spacing = old.get("last_known_generated_image_spacing")
        if isinstance(previous_spacing, dict) and previous_spacing:
            generated_spacing["latest"] = dict(previous_spacing)
            generated_spacing["latest"]["_carried_forward"] = True

    refresh_derived(report)



def _cfg_bool(cfg: Dict[str, Any], key: str) -> Optional[bool]:
    if key not in cfg:
        return None
    val = str(cfg.get(key)).strip().lower()
    if val in {"true", "1", "yes", "on"}:
        return True
    if val in {"false", "0", "no", "off"}:
        return False
    return None


def _seconds_to_minutes_text(value: Any) -> str:
    n = int_or_none(value)
    if n is None:
        return "?"
    if n % 60 == 0:
        return f"{n // 60} min"
    return f"{n} sec"


def _source_bits_for_state_config(st: Dict[str, Any], cfg: Dict[str, Any]) -> List[str]:
    bits: List[str] = []
    if st.get("_carried_forward"):
        bits.append("state carried forward")
    if st.get("_filled_from_previous"):
        bits.append("state partly filled")
    if cfg.get("_carried_forward"):
        bits.append("config carried forward")
    if cfg.get("_carried_from_log_backscan"):
        ts = cfg.get("_log_backscan_timestamp")
        bits.append(f"config backfilled from earlier log scan{f' at {ts}' if ts else ''}")
    if cfg.get("_filled_from_previous"):
        bits.append("config partly filled from previous digest state")
    if cfg.get("_filled_from_log_backscan"):
        ts = cfg.get("_log_backscan_timestamp")
        bits.append(f"config partly filled from earlier log scan{f' at {ts}' if ts else ''}")
    return bits

def render_markdown(report: Dict[str, Any]) -> str:
    s = report["summary"]
    out: List[str] = []
    out.append("# MrsMThatcher log digest")
    out.append("")
    out.append(f"Window: `{s.get('time_start')}` → `{s.get('time_end')}`")
    out.append(f"Project directory: `{report.get('project_dir') or 'unavailable'}`")
    if report.get("requested_since"):
        mode = "exclusive" if report.get("since_exclusive") else "inclusive"
        source = report.get("since_source") or "manual"
        out.append(f"Requested since: `{report.get('requested_since')}` ({mode}, source={source})")
    if report.get("resume_state_file"):
        out.append(f"Resume state file: `{report.get('resume_state_file')}`")
    out.append(f"Records parsed: `{s.get('record_count')}`")
    input_warning = report.get("input_warning")
    if input_warning:
        out.append(f"Input warning: **{input_warning}**")
    out.append("")

    input_files = report.get("input_files") or []
    if input_files:
        out.append("## Input files")
        out.append("```text")
        for item in input_files:
            out.append(str(item.get("path")))
            if not item.get("exists"):
                out.append("  missing")
                continue
            out.append(f"  size={item.get('size')}  mtime={item.get('mtime')}")
            out.append(
                f"  first_timestamp={item.get('first_timestamp')}  "
                f"last_timestamp={item.get('last_timestamp')}"
            )
            out.append(
                f"  total_records={item.get('total_records')}  "
                f"records_after_since={item.get('records_after_since')}  "
                f"records_in_window_before_dedupe={item.get('records_in_window')}"
            )
        out.append("```")
        out.append("")

    out.append("## Headline")
    out.append(s.get("headline") or "")
    out.append("")

    st = report.get("latest_state") or {}
    if st:
        out.append("## Latest state")
        state_source = st.get("_state_source")
        state_source_path = st.get("_state_source_path")
        if st.get("_carried_forward"):
            out.append(f"State timestamp: `{st.get('time')}` (carried forward from previous digest state)")
        elif st.get("_filled_from_previous"):
            out.append(f"State timestamp: `{st.get('time')}` (current snapshot with missing fields filled from previous digest state)")
        elif state_source == "bot_state.json":
            path_text = f" `{state_source_path}`" if state_source_path else ""
            out.append(f"State timestamp: `{st.get('time')}` (authoritative current state from{path_text})")
        elif st.get("_partial"):
            out.append(f"State timestamp: `{st.get('time')}` (partial/truncated log snapshot)")
        else:
            out.append(f"State timestamp: `{st.get('time')}`")
        out.append("")
        out.append("```text")
        out.append(f"daily_reply_count       = {st.get('daily_reply_count')}  date={st.get('daily_reply_date')}")
        out.append(f"daily_quote_reply_count = {st.get('daily_quote_reply_count')}  date={st.get('daily_quote_reply_date')}")
        if st.get("next_reply_lane_priority") is not None:
            out.append(f"next_reply_lane_priority = {st.get('next_reply_lane_priority')}")
        if st.get("skipped_hot_reply_count") is not None:
            out.append(f"skipped_hot_reply_count = {st.get('skipped_hot_reply_count')}")
        out.append(f"quote_spam_author_count = {st.get('quote_spam_author_count')}")
        api_cooldown_status = cooldown_state_text(st.get("api_cooldown_until_epoch"), st.get("time"))
        api_cooldown_suffix = f"  {api_cooldown_status}" if api_cooldown_status else ""
        api_cooldown_human = st.get("api_cooldown_until_human") or "none"
        out.append(
            f"x_read_api_cooldown_until = {st.get('api_cooldown_until_epoch')}  "
            f"{api_cooldown_human}{api_cooldown_suffix}"
        )
        if st.get("api_cooldown_reason"):
            out.append(f"x_read_api_cooldown_reason = {st.get('api_cooldown_reason')}")
        x_write_api_cooldown_status = cooldown_state_text(st.get("x_write_api_cooldown_until_epoch"), st.get("time"))
        x_write_api_cooldown_suffix = f"  {x_write_api_cooldown_status}" if x_write_api_cooldown_status else ""
        x_write_api_cooldown_human = st.get("x_write_api_cooldown_until_human") or "none"
        out.append(
            f"x_write_api_cooldown_until = {st.get('x_write_api_cooldown_until_epoch')}  "
            f"{x_write_api_cooldown_human}{x_write_api_cooldown_suffix}"
        )
        if st.get("x_write_api_cooldown_reason"):
            out.append(f"x_write_api_cooldown_reason = {st.get('x_write_api_cooldown_reason')}")
        xai_api_cooldown_status = cooldown_state_text(st.get("xai_api_cooldown_until_epoch"), st.get("time"))
        xai_api_cooldown_suffix = f"  {xai_api_cooldown_status}" if xai_api_cooldown_status else ""
        xai_api_cooldown_human = st.get("xai_api_cooldown_until_human") or "none"
        out.append(
            f"xai_api_cooldown_until  = {st.get('xai_api_cooldown_until_epoch')}  "
            f"{xai_api_cooldown_human}{xai_api_cooldown_suffix}"
        )
        if st.get("xai_api_cooldown_reason"):
            out.append(f"xai_api_cooldown_reason = {st.get('xai_api_cooldown_reason')}")
        quote_api_cooldown_status = cooldown_state_text(st.get("quote_api_cooldown_until_epoch"), st.get("time"))
        quote_api_cooldown_suffix = f"  {quote_api_cooldown_status}" if quote_api_cooldown_status else ""
        quote_api_cooldown_human = st.get("quote_api_cooldown_until_human") or "none"
        out.append(
            f"quote_api_cooldown_until = {st.get('quote_api_cooldown_until_epoch')}  "
            f"{quote_api_cooldown_human}{quote_api_cooldown_suffix}"
        )
        if st.get("quote_api_cooldown_reason"):
            out.append(f"quote_api_cooldown_reason = {st.get('quote_api_cooldown_reason')}")
        out.append(f"last_main_post_id       = {st.get('last_main_post_id')}")
        out.append(f"last_seen_mention_id    = {st.get('last_seen_mention_id')}")
        if st.get("last_quote_post_epoch") is not None:
            out.append(f"last_quote_post         = {st.get('last_quote_post_human')}  epoch={st.get('last_quote_post_epoch')}")
        out.append(f"next_quote_post         = {st.get('next_quote_post_human')}  epoch={st.get('next_quote_post_epoch')}")
        out.append(f"next_meme_post          = {st.get('next_meme_post_human')}  epoch={st.get('next_meme_post_epoch')}")
        if st.get("next_meme_schedule_mode") is not None:
            out.append(f"next_meme_mode          = {st.get('next_meme_schedule_mode')}  date={st.get('next_meme_schedule_date')}")
        if st.get("meme_anchor_quote_post_epoch"):
            out.append(f"meme_anchor_quote_post  = {st.get('meme_anchor_quote_post_human')}  epoch={st.get('meme_anchor_quote_post_epoch')}")
        if st.get("meme_schedule_version") is not None:
            out.append(f"meme_schedule_version   = {st.get('meme_schedule_version')}")
        out.append(f"posted_meme_count       = {st.get('posted_meme_count')}")
        out.append("```")
        if st.get("posted_meme_filenames_tail"):
            out.append("Recent posted meme filenames:")
            out.append("```text")
            for name in st["posted_meme_filenames_tail"]:
                out.append(str(name))
            out.append("```")
        out.append("")

    media_upload = report.get("media_upload") or {}
    media_incidents = media_upload.get("incidents") or []
    if media_incidents:
        out.append("## Media upload incidents")
        out.append("```text")
        out.append(f"handled_fallbacks     = {len(media_upload.get('handled_fallbacks') or [])}")
        out.append(f"unrecovered_failures  = {len(media_upload.get('unrecovered_failures') or [])}")
        out.append("```")
        out.append(md_table_row(["time", "status", "media", "v1.1 result", "post result", "summary"]))
        out.append(md_table_row(["---", "---", "---", "---", "---", "---"]))
        for item in media_incidents:
            out.append(md_table_row([
                item.get("time", ""),
                item.get("status", ""),
                item.get("media", ""),
                item.get("v1_result", ""),
                item.get("post_result", ""),
                item.get("summary", ""),
            ]))
        out.append("")
        out.append("Details:")
        out.append(md_table_row(["time", "v2 failure", "fallback log"]))
        out.append(md_table_row(["---", "---", "---"]))
        for item in media_incidents:
            out.append(md_table_row([
                item.get("time", ""),
                item.get("v2_failure", ""),
                item.get("fallback", ""),
            ]))
        out.append("")

    cfg = report.get("latest_config") or {}
    meme_keys = {
        "ENABLE_DAILY_MEME_POSTS", "MEME_TRIGGER_AFTER_HOUR",
        "MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS", "MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS",
        "MEME_FALLBACK_HOUR", "MEME_FALLBACK_MINUTE",
        "MEME_MIN_SECONDS_AFTER_QUOTE_POST", "MEME_SCHEDULE_VERSION",
    }
    if cfg or st:
        has_new_meme_config = any(k in cfg for k in meme_keys)
        has_meme_state = any(st.get(k) is not None for k in (
            "next_meme_post_epoch", "next_meme_schedule_mode",
            "meme_anchor_quote_post_epoch", "meme_schedule_version",
        ))
        if has_new_meme_config or has_meme_state:
            out.append("## Daily meme schedule")
            out.append("```text")
            source_bits = _source_bits_for_state_config(st, cfg)
            if source_bits:
                out.append(f"source                  = {', '.join(source_bits)}")
            enabled = _cfg_bool(cfg, "ENABLE_DAILY_MEME_POSTS")
            if enabled is not None:
                out.append(f"enabled                 = {enabled}")
            trigger = cfg.get("MEME_TRIGGER_AFTER_HOUR")
            min_delay = cfg.get("MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS")
            max_delay = cfg.get("MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS")
            fallback_hour = cfg.get("MEME_FALLBACK_HOUR")
            fallback_minute = cfg.get("MEME_FALLBACK_MINUTE")
            if trigger is not None:
                out.append(f"rule                    = first normal quote/image post after {trigger}:00 schedules the daily meme")
            if min_delay is not None or max_delay is not None:
                out.append(f"random_delay_after_rule = {_seconds_to_minutes_text(min_delay)} to {_seconds_to_minutes_text(max_delay)}")
            if fallback_hour is not None or fallback_minute is not None:
                hh = str(fallback_hour) if fallback_hour is not None else "?"
                mm_int = int_or_none(fallback_minute)
                mm = f"{mm_int:02d}" if mm_int is not None else "?"
                out.append(f"fallback_if_no_anchor   = {hh}:{mm}")
            if cfg.get("MEME_MIN_SECONDS_AFTER_QUOTE_POST") is not None:
                out.append(f"min_gap_after_quote     = {_seconds_to_minutes_text(cfg.get('MEME_MIN_SECONDS_AFTER_QUOTE_POST'))}")
            if cfg.get("MEME_SCHEDULE_VERSION") is not None:
                out.append(f"config_schedule_version = {cfg.get('MEME_SCHEDULE_VERSION')}")
            if st.get("next_meme_post_epoch") is not None:
                out.append(f"current_next_meme       = {st.get('next_meme_post_human')}  epoch={st.get('next_meme_post_epoch')}")
            if st.get("next_meme_schedule_mode") is not None:
                out.append(f"current_mode            = {st.get('next_meme_schedule_mode')}  date={st.get('next_meme_schedule_date')}")
            if st.get("meme_anchor_quote_post_epoch"):
                out.append(f"current_anchor          = {st.get('meme_anchor_quote_post_human')}  epoch={st.get('meme_anchor_quote_post_epoch')}")
            else:
                if st.get("next_meme_schedule_mode") and str(st.get("next_meme_schedule_mode")).startswith("fallback"):
                    out.append("current_anchor          = none yet; fallback remains until first qualifying post/image after midday")
            out.append("```")
            out.append("")

    derived = report.get("derived") or {}
    budget = derived.get("reply_budget") or {}
    if budget:
        out.append("## Reply budget")
        out.append("```text")
        au, al, ar = budget.get("auto_used"), budget.get("auto_limit"), budget.get("auto_remaining")
        qu, ql, qr = budget.get("quote_used"), budget.get("quote_limit"), budget.get("quote_remaining")
        source_bits = []
        if budget.get("state_carried_forward"):
            source_bits.append("state carried forward")
        if budget.get("config_carried_forward"):
            source_bits.append("config carried forward")
        if budget.get("config_carried_from_log_backscan"):
            ts = budget.get("config_backscan_timestamp")
            source_bits.append(f"config backfilled from earlier log scan{f' at {ts}' if ts else ''}")
        if budget.get("state_filled_from_previous"):
            source_bits.append("state partly filled")
        if budget.get("config_filled_from_previous"):
            source_bits.append("config partly filled from previous digest state")
        if budget.get("config_filled_from_log_backscan"):
            ts = budget.get("config_backscan_timestamp")
            source_bits.append(f"config partly filled from earlier log scan{f' at {ts}' if ts else ''}")
        if source_bits:
            out.append(f"source             = {', '.join(source_bits)}")
        if not budget.get("has_any_budget_input"):
            out.append("not available      = no state/config snapshot in this window or saved resume context")
        else:
            if au is not None or al is not None:
                out.append(f"auto replies used  = {au if au is not None else '?'} / {al if al is not None else '?'}  remaining={ar if ar is not None else '?'}")
            if qu is not None or ql is not None:
                out.append(f"quote replies used = {qu if qu is not None else '?'} / {ql if ql is not None else '?'}  remaining={qr if qr is not None else '?'}")
        out.append("```")
        out.append("")

    lane = derived.get("reply_lane_priority") or {}
    if lane:
        out.append("## Reply lane priority")
        out.append("```text")
        source_bits = []
        if lane.get("state_carried_forward"):
            source_bits.append("state carried forward")
        if lane.get("state_filled_from_previous"):
            source_bits.append("state partly filled")
        if lane.get("config_carried_forward"):
            source_bits.append("config carried forward")
        if lane.get("config_carried_from_log_backscan"):
            ts = lane.get("config_backscan_timestamp")
            source_bits.append(f"config backfilled from earlier log scan{f' at {ts}' if ts else ''}")
        if lane.get("config_filled_from_previous"):
            source_bits.append("config partly filled from previous digest state")
        if lane.get("config_filled_from_log_backscan"):
            ts = lane.get("config_backscan_timestamp")
            source_bits.append(f"config partly filled from earlier log scan{f' at {ts}' if ts else ''}")
        if source_bits:
            out.append(f"source                         = {', '.join(source_bits)}")
        priority = lane.get("current_next_priority")
        out.append(f"current_next_priority          = {priority if priority is not None else 'not available'}")
        out.append(f"normal_lane_due_checks         = {lane.get('normal_lane_due_checks')}")
        out.append(f"mention_function_entries       = {lane.get('mention_function_entries')}")
        out.append(f"mention_fetch_attempts         = {lane.get('mention_fetch_attempts')}")
        out.append(f"mention_checks_skipped_spacing = {lane.get('mention_checks_skipped_spacing')}")
        out.append(f"mention_checks_skipped_cooldown = {lane.get('mention_checks_skipped_cooldown')}")
        out.append(f"quote_lane_due_checks          = {lane.get('quote_lane_due_checks')}")
        out.append(f"priority_flipped_to_quote      = {lane.get('flipped_to_quote')}")
        out.append(f"priority_flipped_to_normal     = {lane.get('flipped_to_normal')}")
        out.append(f"forced_normal_before_quote     = {lane.get('forced_normal_before_quote')}")
        out.append(f"normal_first_refusal_no_post   = {lane.get('normal_first_refusal_no_post')}")
        out.append(f"quote_tweet_status_posted      = {lane.get('quote_tweet_status_posted')}")
        out.append(f"quote_tweet_status_checked     = {lane.get('quote_tweet_status_checked')}")
        out.append(f"quote_tweet_status_spacing     = {lane.get('quote_tweet_status_skipped_spacing')}")
        out.append(f"quote_tweet_status_cap         = {lane.get('quote_tweet_status_skipped_cap')}")
        out.append(f"quote_tweet_status_cooldown    = {lane.get('quote_tweet_status_skipped_cooldown')}")
        out.append(f"quote_tweet_checks_no_post     = {lane.get('quote_tweet_checks_no_post')}")
        out.append("```")
        out.append("")

    xai_usage = report.get("xai_usage") or {}
    xai_events = xai_usage.get("events") or []
    xai_parse_errors = xai_usage.get("parse_errors") or []
    if xai_events or xai_parse_errors:
        out.append("## xAI usage")
        if xai_events:
            out.append(md_table_row([
                "time",
                "lane",
                "context_id",
                "prompt",
                "cached",
                "reasoning",
                "completion",
                "total",
                "sources",
                "cost_ticks",
                "image",
            ]))
            out.append(md_table_row(["---"] * 11))
            for item in xai_events:
                out.append(md_table_row([
                    item.get("time", ""),
                    item.get("lane", ""),
                    item.get("context_id", ""),
                    item.get("prompt_tokens", 0),
                    item.get("cached_tokens", 0),
                    item.get("reasoning_tokens", 0),
                    item.get("completion_tokens", 0),
                    item.get("total_tokens", 0),
                    item.get("num_sources_used", 0),
                    item.get("cost_in_usd_ticks", 0),
                    item.get("image_tokens", 0),
                ]))
            out.append("")
            totals = xai_usage.get("totals") or {}
            out.append("Totals:")
            out.append("```text")
            out.append(f"successful_xai_calls = {totals.get('successful_xai_calls', 0)}")
            out.append(f"prompt_tokens        = {totals.get('prompt_tokens', 0)}")
            out.append(f"cached_tokens        = {totals.get('cached_tokens', 0)}")
            out.append(f"image_tokens         = {totals.get('image_tokens', 0)}")
            out.append(f"reasoning_tokens     = {totals.get('reasoning_tokens', 0)}")
            out.append(f"completion_tokens    = {totals.get('completion_tokens', 0)}")
            out.append(f"total_tokens         = {totals.get('total_tokens', 0)}")
            out.append(f"sources_used         = {totals.get('sources_used', 0)}")
            out.append(f"cost_in_usd_ticks    = {totals.get('cost_in_usd_ticks', 0)}")
            out.append("```")
            out.append("")
        if xai_parse_errors:
            out.append("Malformed xAI usage records:")
            out.append(md_table_row(["time", "where", "error", "message"]))
            out.append(md_table_row(["---", "---", "---", "---"]))
            for item in xai_parse_errors:
                out.append(md_table_row([
                    item.get("time", ""),
                    item.get("where", ""),
                    item.get("error", ""),
                    item.get("message", ""),
                ]))
            out.append("")

    generated_spacing = report.get("generated_image_spacing") or {}
    generated_spacing_latest = generated_spacing.get("latest") or {}
    generated_spacing_events = generated_spacing.get("events") or []
    pool_health = report.get("generated_image_pool_health") or {}
    if pool_health:
        out.append("## Generated image pool health")
        out.append("Current filesystem snapshot at digest generation time; these counts are not limited to the selected log window.")
        out.append("")
        out.append("```text")
        out.append(f"active_generated_images       = {pool_health.get('active_generated_images', 'unavailable')}")
        out.append(f"quarantined_generated_images  = {pool_health.get('quarantined_generated_images', 'unavailable')}")
        out.append(f"total_known_generated_images  = {pool_health.get('total_known_generated_images', 'unavailable')}")
        out.append(f"active_analysis_records       = {pool_health.get('active_analysis_records', 'unavailable')}")
        out.append(f"active_identity_records       = {pool_health.get('active_identity_records', 'unavailable')}")
        out.append(f"metadata_coverage             = {pool_health.get('metadata_coverage', 'unavailable')}")
        out.append(f"hash_validation               = {pool_health.get('hash_valid', 0)} / {pool_health.get('hash_total', 0)} valid ({pool_health.get('hash_validation', 'unavailable')})")
        out.append(f"generated_images_in_used_history = {pool_health.get('generated_images_in_used_history', 0)}")
        out.append(f"health                        = {pool_health.get('health', 'WARNING')}")
        out.append("```")
        out.append("Active policy counts:")
        out.append("```text")
        for policy in GENERATED_POLICIES:
            out.append(f"{policy:<18} = {(pool_health.get('active_policy_counts') or {}).get(policy, 0)}")
        out.append("```")
        out.append("Usage history:")
        out.append("```text")
        out.append(f"active_used_in_current_cycle       = {pool_health.get('active_previously_used', 0)}")
        out.append(f"active_unused_in_current_cycle     = {pool_health.get('active_never_used', 0)}")
        out.append(f"quarantined_used_in_current_cycle  = {pool_health.get('quarantined_previously_used', 0)}")
        out.append(f"quarantined_unused_in_current_cycle = {pool_health.get('quarantined_never_used', 0)}")
        out.append("```")
        rates = report.get("generated_image_post_rates") or {}
        if rates:
            out.append("Recent successful regular-post rate (bounded historical log scan):")
            out.append("```text")
            for label in ("trailing_7d", "trailing_30d"):
                window = (rates.get("windows") or {}).get(label) or {}
                share = window.get("generated_share_percent")
                out.append(f"{label}_coverage_days          = {float(window.get('coverage_days') or 0.0):.1f}")
                out.append(f"{label}_observed_logging_days  = {float(window.get('observed_logging_days') or 0.0):.1f}")
                out.append(f"{label}_coverage_quality       = {window.get('coverage_quality') or 'unavailable'}")
                out.append(f"{label}_largest_detected_gap   = {float(window.get('largest_detected_gap_seconds') or 0.0) / 3600.0:.1f} hours")
                out.append(f"{label}_regular_posts          = {window.get('regular_posts', 0)}")
                out.append(f"{label}_generated_posts        = {window.get('generated_posts', 0)}")
                out.append(f"{label}_generated_share        = {float(share):.1f}%" if share is not None else f"{label}_generated_share        = unavailable")
                regular_rate = window.get("regular_posts_per_day"); generated_rate = window.get("generated_posts_per_day")
                out.append(f"{label}_regular_posts_per_day  = {float(regular_rate):.2f}" if regular_rate is not None else f"{label}_regular_posts_per_day  = unavailable")
                out.append(f"{label}_generated_posts_per_day = {float(generated_rate):.2f}" if generated_rate is not None else f"{label}_generated_posts_per_day = unavailable")
            out.append(f"contaminated_seconds_excluded  = {rates.get('contaminated_seconds_excluded', 0)}")
            out.append("```")
            if any(((rates.get("windows") or {}).get(label) or {}).get("coverage_quality") == "gapped" for label in ("trailing_7d", "trailing_30d")):
                out.append("WARNING: material gaps were detected in available logs; posts/day and runway estimates are not treated as reliable.")
        runway = report.get("generated_image_pool_runway") or {}
        if runway:
            out.append("Estimated current-cycle runway (not an all-time posting claim):")
            out.append("```text")
            out.append(f"active_generated_unused_in_current_cycle = {runway.get('remaining_active_generated_in_current_cycle', 0)}")
            out.append(f"primary_basis                           = {runway.get('primary_basis') or 'unavailable'}")
            primary = (runway.get("observed") or {}).get(str(runway.get("primary_basis"))) or {}
            if primary.get("available"):
                out.append(f"estimated_regular_posts_to_cycle_exhaustion = {primary.get('regular_posts_to_cycle_exhaustion')}")
                out.append(f"estimated_days_to_cycle_exhaustion     = {float(primary.get('days_to_cycle_exhaustion') or 0.0):.1f}")
            else:
                out.append("estimated_regular_posts_to_cycle_exhaustion = unavailable")
                out.append(f"estimated_days_to_cycle_exhaustion     = unavailable ({primary.get('reason') or 'no reliable observed basis'})")
            schedule = runway.get("schedule") or {}
            if schedule.get("available"):
                out.append(f"schedule_model_generated_share_max     = {float(schedule.get('maximum_generated_share_percent') or 0.0):.1f}%")
                out.append(f"schedule_model_generated_posts_per_day = {float(schedule.get('generated_posts_per_day') or 0.0):.2f}")
                out.append(
                    "schedule_model_days_to_cycle_exhaustion = "
                    f"{float(schedule.get('days_to_cycle_exhaustion') or 0.0):.1f} "
                    "(maximum-throughput minimum; assumes generated selection whenever spacing permits)"
                )
            else:
                out.append(f"schedule_model_days_to_cycle_exhaustion = unavailable ({schedule.get('reason') or 'pool disabled'})")
            out.append("```")
        out.append("Curation trend (completed transaction image actions):")
        out.append("```text")
        for days in (7, 30):
            trend = pool_health.get(f"curation_{days}d") or {}
            out.append(f"quarantined_last_{days}d = {trend.get('quarantined', 0)}")
            out.append(f"restored_last_{days}d    = {trend.get('restored', 0)}")
            out.append(f"net_active_change_last_{days}d = {trend.get('net_active_change', 0):+d}")
        out.append("```")
        latest_quarantine = pool_health.get("latest_quarantine")
        if latest_quarantine:
            out.append("Latest completed quarantine:")
            out.append("```text")
            out.append(f"transaction_id = {latest_quarantine.get('transaction_id', '')}")
            out.append(f"timestamp      = {latest_quarantine.get('timestamp', '')}")
            out.append(f"image_count    = {latest_quarantine.get('image_count', 0)}")
            out.append("```")
        else:
            out.append("Latest completed quarantine: none")
            out.append("")
        out.append(f"completed_quarantine_transactions = {pool_health.get('completed_quarantine_transactions', 0)}")
        out.append(f"completed_restore_transactions    = {pool_health.get('completed_restore_transactions', 0)}")
        if pool_health.get("latest_restore"):
            restore = pool_health["latest_restore"]
            out.append(f"latest_restore = {restore.get('transaction_id')} at {restore.get('timestamp')} ({restore.get('image_count', 0)} images)")
        out.append("")
        warnings = pool_health.get("warnings") or []
        if warnings:
            out.append("Pool-health warnings:")
            out.append(md_table_row(["kind", "basename", "detail"]))
            out.append(md_table_row(["---", "---", "---"]))
            for item in warnings[:20]:
                out.append(md_table_row([item.get("kind", ""), item.get("basename", ""), item.get("detail", "")]))
            if len(warnings) > 20:
                out.append(f"{len(warnings) - 20} additional warning(s) omitted.")
            out.append("")

    utilisation = report.get("generated_image_utilisation") or {}
    if utilisation:
        out.append("## Generated image utilisation")
        out.append("Successful-post history is bounded by available structured production logs; observed-log usage and current-cycle history are separate measures.")
        out.append("Deprecated machine-readable usage aliases retain the bounded-log values for compatibility and are planned for removal only in a future major digest schema version.")
        coverage_start = utilisation.get("history_coverage_start") or "unavailable"
        coverage_end = utilisation.get("history_coverage_end") or "unavailable"
        out.append(f"Observed structured-log coverage: `{coverage_start}` to `{coverage_end}`.")
        out.append("")
        out.append("```text")
        out.append(f"active_generated_images                    = {utilisation.get('active_generated_images', 0)}")
        out.append(f"active_images_used_in_observed_logs        = {utilisation.get('active_images_used_in_observed_logs', utilisation.get('active_images_used_ever', 0))}")
        out.append(f"active_images_not_seen_in_observed_logs    = {utilisation.get('active_images_not_seen_in_observed_logs', utilisation.get('active_images_never_used', 0))}")
        percentage = utilisation.get("active_pool_observed_usage_percentage", utilisation.get("active_pool_ever_used_percentage"))
        out.append(f"active_pool_observed_usage_percentage      = {float(percentage):.1f}%" if percentage is not None else "active_pool_observed_usage_percentage      = unavailable")
        out.append(f"active_images_used_in_current_cycle        = {utilisation.get('active_images_used_in_current_cycle', 0)}")
        out.append(f"active_images_unused_in_current_cycle      = {utilisation.get('active_images_unused_in_current_cycle', 0)}")
        out.append(f"total_successful_generated_posts_observed  = {utilisation.get('total_successful_generated_posts_observed', 0)}")
        median_count = utilisation.get("median_successful_posts_per_used_image")
        out.append(f"median_successful_posts_per_used_image     = {float(median_count):.1f}" if median_count is not None else "median_successful_posts_per_used_image     = unavailable")
        out.append(f"maximum_successful_posts_for_one_image     = {utilisation.get('maximum_successful_posts_for_one_image', 0)}")
        top_share = utilisation.get("top_10_share_of_successful_generated_posts")
        out.append(f"top_10_share_of_successful_generated_posts = {float(top_share):.1f}%" if top_share is not None else "top_10_share_of_successful_generated_posts = unavailable")
        out.append("```")

        out.append("Most frequently used active generated images")
        out.append(md_table_row(["image", "successful_posts", "last_successful_post"]))
        out.append(md_table_row(["---", "---", "---"]))
        for item in utilisation.get("most_frequently_used") or []:
            out.append(md_table_row([item.get("image", ""), item.get("successful_posts", 0), item.get("last_successful_post") or "never"]))
        if not utilisation.get("most_frequently_used"): out.append(md_table_row(["none observed", "0", "never"]))
        out.append("")

        out.append("Active generated images never successfully posted in observed logs")
        out.append(md_table_row(["image", "origin_quote_hash"]))
        out.append(md_table_row(["---", "---"]))
        for item in utilisation.get("never_used") or []:
            out.append(md_table_row([item.get("image", ""), item.get("origin_quote_hash") or "unavailable"]))
        omitted = int(utilisation.get("never_used_total", 0) or 0) - len(utilisation.get("never_used") or [])
        if omitted > 0: out.append(f"{omitted} additional active image(s) omitted.")
        if not utilisation.get("never_used"): out.append(md_table_row(["none", "-"]))
        out.append("")

        out.append("Active generated images unused longest in observed logs")
        out.append(md_table_row(["image", "last_successful_post", "successful_posts"]))
        out.append(md_table_row(["---", "---", "---"]))
        for item in utilisation.get("unused_longest") or []:
            out.append(md_table_row([item.get("image", ""), item.get("last_successful_post") or "never", item.get("successful_posts", 0)]))
        if not utilisation.get("unused_longest"): out.append(md_table_row(["none", "never", "0"]))
        out.append("")

    if generated_spacing_latest or generated_spacing_events:
        out.append("## Generated image spacing")
        if generated_spacing_latest:
            out.append("```text")
            out.append(f"required_original_posts_between = {generated_spacing_latest.get('required', '')}")
            out.append(f"original_posts_since_generated  = {generated_spacing_latest.get('original_posts_since_generated', '')}")
            out.append(f"generated_pool_enabled          = {generated_spacing_latest.get('pool_enabled', '')}")
            out.append(f"generated_pool_allowed          = {generated_spacing_latest.get('allowed', '')}")
            out.append("```")
        out.append(md_table_row(["time", "kind", "pool_enabled", "allowed", "original_posts_since_generated", "required"]))
        out.append(md_table_row(["---"] * 6))
        for item in generated_spacing_events[-20:]:
            out.append(md_table_row([
                item.get("time", ""),
                item.get("kind", ""),
                item.get("pool_enabled", ""),
                item.get("allowed", ""),
                item.get("original_posts_since_generated", ""),
                item.get("required", ""),
            ]))
        out.append("")

    regular_image_usage = report.get("regular_image_usage") or {}
    regular_image_events = regular_image_usage.get("events") or []
    if regular_image_events:
        summary = regular_image_usage.get("summary") or {}
        out.append("## Regular image usage")
        out.append("```text")
        out.append(f"selections          = {summary.get('selections', 0)}")
        out.append(f"original_images     = {summary.get('original', 0)}")
        out.append(f"generated_images    = {summary.get('generated', 0)}")
        out.append(f"originating_quote   = {summary.get('origin_matches', 0)}")
        out.append(f"cross_quote         = {summary.get('cross_quote', 0)}")
        out.append(f"made_with_ai_true   = {summary.get('made_with_ai_true', 0)}")
        out.append(f"made_with_ai_false  = {summary.get('made_with_ai_false', 0)}")
        out.append(f"made_with_ai_unknown = {summary.get('made_with_ai_unknown', 0)}")
        out.append(f"generated_share     = {float(summary.get('generated_share', 0.0)):.1f}%")
        out.append(f"origin_match_share  = {float(summary.get('origin_match_share', 0.0)):.1f}%")
        out.append("```")
        out.append(md_table_row(["time", "source", "basename", "score", "origin_quote_match", "origin_quote_boost", "made_with_ai"]))
        out.append(md_table_row(["---"] * 7))
        for item in regular_image_events:
            out.append(md_table_row([
                item.get("time", ""),
                item.get("source", ""),
                item.get("basename", ""),
                item.get("score", ""),
                item.get("origin_quote_match", ""),
                item.get("origin_quote_boost", ""),
                item.get("made_with_ai", ""),
            ]))
        out.append("")

    shadow = report.get("original_editorial_shadow") or {}
    shadow_events = shadow.get("events") or []
    shadow_summary = shadow.get("summary") or {}
    if shadow_events:
        out.append("## Original editorial shadow scoring")
        out.append("This section is shadow-only. It reports hypothetical original-image choices and does not imply the shadow image was posted.")
        out.append("")
        out.append("```text")
        out.append(f"shadow_observations                  = {shadow_summary.get('observations', 0)}")
        out.append(f"production_original_winners          = {shadow_summary.get('production_original', 0)}")
        out.append(f"production_generated_winners         = {shadow_summary.get('production_generated', 0)}")
        out.append(f"comparable_original_observations     = {shadow_summary.get('comparable_original_observations', 0)}")
        out.append(f"original_winner_changes              = {shadow_summary.get('winner_changes', 0)} ({float(shadow_summary.get('winner_change_percent', 0.0)):.1f}%)")
        avg_rank = shadow_summary.get("average_production_winner_shadow_rank")
        out.append(f"average_production_winner_shadow_rank = {avg_rank if avg_rank is not None else 'n/a'}")
        out.append(f"production_winner_shadow_rank_1      = {shadow_summary.get('production_rank_1', 0)}")
        out.append(f"production_winner_shadow_rank_2_or_3 = {shadow_summary.get('production_rank_2_or_3', 0)}")
        out.append(f"average_abs_editorial_adjustment     = {float(shadow_summary.get('average_abs_editorial_adjustment', 0.0)):.2f}")
        out.append(f"max_abs_editorial_adjustment         = {float(shadow_summary.get('max_abs_editorial_adjustment', 0.0)):.2f}")
        out.append(f"cap_hit_count                        = {shadow_summary.get('cap_hit_count', 0)}")
        out.append("```")
        if shadow_summary.get("most_frequent_shadow_winners"):
            out.append("Most frequent shadow winners:")
            out.append(", ".join(f"{name} ({count})" for name, count in shadow_summary.get("most_frequent_shadow_winners", []) if name))
            out.append("")
        if shadow_summary.get("most_frequent_affinity_concepts"):
            out.append("Most frequent affinity concepts:")
            out.append(", ".join(f"{name} ({count})" for name, count in shadow_summary.get("most_frequent_affinity_concepts", []) if name))
            out.append("")
        if shadow_summary.get("most_frequent_active_dimensions"):
            out.append("Most frequent positive shadow-winner dimensions:")
            out.append(", ".join(f"{name} ({count})" for name, count in shadow_summary.get("most_frequent_active_dimensions", []) if name))
            out.append("")
        changed_shadow = [
            item
            for item in shadow_events
            if item.get("production_source") == "original" and item.get("winner_changed") is True
        ]
        if changed_shadow:
            out.append("Changed-winner observations:")
            out.append(md_table_row(["time", "line_no", "production", "shadow", "production rank", "adjustment", "reason"]))
            out.append(md_table_row(["---"] * 7))
            for item in changed_shadow[:20]:
                reason_bits = []
                if item.get("affinity_matches"):
                    reason_bits.append("affinity=" + ",".join(str(v) for v in item.get("affinity_matches", [])[:4]))
                if item.get("dimension_matches"):
                    reason_bits.append("dimensions=" + ",".join(str(v) for v in item.get("dimension_matches", [])[:4]))
                if item.get("penalties"):
                    reason_bits.append("penalties=" + ",".join(str(v) for v in item.get("penalties", [])[:3]))
                out.append(md_table_row([
                    item.get("time", ""),
                    item.get("line_no", ""),
                    f"{item.get('production_winner', '')} ({item.get('production_source', '')})",
                    item.get("shadow_original_winner", ""),
                    item.get("production_shadow_rank", ""),
                    item.get("shadow_winner_editorial_adjustment", ""),
                    "; ".join(reason_bits),
                ]))
            out.append("")
        else:
            out.append("No changed-winner observations in this window.")
            out.append("")

    identity_policy = report.get("generated_identity_policy") or {}
    identity_policy_events = identity_policy.get("events") or []
    identity_policy_summary = identity_policy.get("summary") or {}
    if identity_policy_events:
        out.append("## Generated identity policy")
        out.append("This policy is active in real production. New events compare policy-disabled and policy-enabled selection with the same candidates, scores and saved random state; the counterfactual baseline image was not posted.")
        out.append("")
        out.append("The winner-change percentage denominator is counterfactually validated selections where the policy affected a score or eligibility. Older events without that comparison are reported separately and are not attributed causally.")
        out.append("")
        out.append("```text")
        out.append(f"regular_selections_under_policy        = {identity_policy_summary.get('observations', 0)}")
        out.append(f"policy_relevant_selections             = {identity_policy_summary.get('policy_relevant_observations', 0)}")
        out.append(f"identity_policy_winner_changed         = {identity_policy_summary.get('identity_policy_winner_changes', identity_policy_summary.get('winner_changes', 0))} ({float(identity_policy_summary.get('winner_change_percent', 0.0)):.1f}%)")
        out.append(f"policy_effect_without_winner_change   = {identity_policy_summary.get('policy_effect_without_winner_change', 0)}")
        out.append(f"legacy_policy_causation_unverified    = {identity_policy_summary.get('legacy_policy_causation_unverified', 0)}")
        out.append(f"counterfactual_invariant_failures     = {identity_policy_summary.get('counterfactual_invariant_failures', 0)}")
        out.append(f"policy_neutral_baseline_differences    = {identity_policy_summary.get('policy_neutral_baseline_differences', 0)}")
        out.append(f"policy_neutral_equal_score_ties        = {identity_policy_summary.get('policy_neutral_equal_score_tie_resolutions', 0)}")
        out.append(f"origin_only_baseline_winners_prevented = {identity_policy_summary.get('baseline_origin_only_prevented', 0)}")
        out.append(f"small_penalty_baseline_winners_displaced = {identity_policy_summary.get('baseline_small_penalty_displaced', 0)}")
        out.append(f"strong_penalty_baseline_winners_displaced = {identity_policy_summary.get('baseline_strong_penalty_displaced', 0)}")
        out.append(f"cross_quote_candidates_excluded       = {identity_policy_summary.get('cross_quote_candidates_excluded', 0)}")
        out.append(f"cross_quote_candidates_penalised      = {identity_policy_summary.get('cross_quote_candidates_penalised', 0)}")
        out.append(f"recovery_observations                  = {identity_policy_summary.get('recovery_observations', 0)}")
        out.append(f"no_valid_candidate_events             = {identity_policy_summary.get('no_valid_candidate_events', 0)}")
        out.append("```")
        for label, key in (("Replacement source transitions", "replacement_source_transitions"), ("Most frequently excluded images", "most_frequent_excluded_images"), ("Most frequent replacement images", "most_frequent_replacement_images"), ("Selection phases", "selection_phases")):
            values = identity_policy_summary.get(key) or []
            if values:
                out.append(f"{label}: " + ", ".join(f"{name} ({count})" for name, count in values if name))
                out.append("")
        event_categories = identity_policy_summary.get("event_categories") or []
        changed_policy = [
            item for item, category in zip(identity_policy_events, event_categories)
            if category == "identity_policy_winner_change"
        ]
        if changed_policy:
            out.append("Counterfactual winners changed by the generated-identity policy:")
            out.append(md_table_row(["time", "line_no", "policy-disabled winner", "action", "policy-enabled winner", "baseline score", "policy score", "phase"]))
            out.append(md_table_row(["---"] * 8))
            for item in changed_policy[:20]:
                out.append(md_table_row([item.get("time", ""), item.get("line_no", ""), f"{item.get('baseline_winner', '')} ({item.get('baseline_winner_source', '')})", item.get("baseline_identity_action", ""), f"{item.get('production_winner', '')} ({item.get('production_winner_source', '')})", item.get("baseline_winner_score", ""), item.get("production_policy_score", ""), item.get("selection_phase", "")]))
            out.append("")
        neutral_differences = [
            (item, category) for item, category in zip(identity_policy_events, event_categories)
            if category.startswith("policy_neutral_")
        ]
        if neutral_differences:
            out.append("Policy-neutral baseline differences:")
            out.append("These differences did not result from a generated-identity penalty or exclusion.")
            out.append("")
            out.append(md_table_row(["time", "line_no", "deterministic baseline", "actual production winner", "reason", "baseline score", "production score", "phase"]))
            out.append(md_table_row(["---"] * 8))
            for item, category in neutral_differences[:20]:
                reason = "equal-score production tie resolution" if category.endswith("equal_score_tie_resolution") else "downstream production selection"
                out.append(md_table_row([item.get("time", ""), item.get("line_no", ""), f"{item.get('baseline_winner', '')} ({item.get('baseline_winner_source', '')})", f"{item.get('production_winner', '')} ({item.get('production_winner_source', '')})", reason, item.get("baseline_winner_score", ""), item.get("production_policy_score", ""), item.get("selection_phase", "")]))
            out.append("")
        legacy_unverified = [
            item for item, category in zip(identity_policy_events, event_categories)
            if category == "legacy_policy_causation_unverified"
        ]
        if legacy_unverified:
            out.append("Legacy winner differences with unverified policy causation:")
            out.append("These records predate shared-random-state counterfactual telemetry and are excluded from the causal winner-change count.")
            out.append("")
            out.append(md_table_row(["time", "line_no", "reported baseline", "production winner", "baseline action", "phase"]))
            out.append(md_table_row(["---"] * 6))
            for item in legacy_unverified[:20]:
                out.append(md_table_row([item.get("time", ""), item.get("line_no", ""), item.get("baseline_winner", ""), item.get("production_winner", ""), item.get("baseline_identity_action", ""), item.get("selection_phase", "")]))
            out.append("")

    identity_shadow = report.get("generated_identity_shadow") or {}
    identity_events = identity_shadow.get("events") or []
    identity_summary = identity_shadow.get("summary") or {}
    if identity_events:
        out.append("## Generated identity-policy shadow scoring")
        out.append("This section is shadow-only and hypothetical. It does not imply that the identity-policy shadow winner was posted.")
        out.append("")
        out.append("The winner-change percentage denominator is counterfactually validated observations where at least one cross-quote generated candidate was penalised or excluded. Older observations without saved-RNG replay are reported separately and are not attributed causally.")
        out.append("")
        out.append("```text")
        out.append(f"shadow_observations                         = {identity_summary.get('observations', 0)}")
        out.append(f"production_original_winners                 = {identity_summary.get('production_original', 0)}")
        out.append(f"production_generated_winners                = {identity_summary.get('production_generated', 0)}")
        out.append(f"production_generated_origin_quote_winners   = {identity_summary.get('production_generated_origin_quote', 0)}")
        out.append(f"production_generated_cross_quote_winners    = {identity_summary.get('production_generated_cross_quote', 0)}")
        out.append(f"policy_relevant_observations                = {identity_summary.get('policy_relevant_observations', 0)}")
        out.append(f"winner_changes                              = {identity_summary.get('winner_changes', 0)} ({float(identity_summary.get('winner_change_percent', 0.0)):.1f}%)")
        out.append(f"policy_effect_without_winner_change         = {identity_summary.get('policy_effect_without_winner_change', 0)}")
        out.append(f"legacy_policy_causation_unverified          = {identity_summary.get('legacy_policy_causation_unverified', 0)}")
        out.append(f"counterfactual_invariant_failures           = {identity_summary.get('counterfactual_invariant_failures', 0)}")
        out.append(f"production_winners_origin_only_excluded     = {identity_summary.get('production_winner_origin_only_excluded', 0)}")
        out.append(f"production_winners_small_penalty            = {identity_summary.get('production_winner_small_penalty', 0)}")
        out.append(f"production_winners_strong_penalty           = {identity_summary.get('production_winner_strong_penalty', 0)}")
        out.append(f"cross_quote_candidates_excluded             = {identity_summary.get('cross_quote_candidates_excluded', 0)}")
        out.append(f"cross_quote_candidates_penalised            = {identity_summary.get('cross_quote_candidates_penalised', 0)}")
        out.append("```")
        for label, key in (
            ("Most frequently excluded images", "most_frequent_excluded_images"),
            ("Most frequently penalised images", "most_frequent_penalised_images"),
            ("Most frequent production policies", "most_frequent_production_policies"),
            ("Most frequent shadow winners", "most_frequent_shadow_winners"),
            ("Selection phases", "selection_phases"),
        ):
            values = identity_summary.get(key) or []
            if values:
                out.append(f"{label}:")
                out.append(", ".join(f"{name} ({count})" for name, count in values if name))
                out.append("")
        identity_categories = identity_summary.get("event_categories") or []
        changed_identity = [
            item for item, category in zip(identity_events, identity_categories)
            if category == "identity_policy_winner_change"
        ]
        if changed_identity:
            out.append("Counterfactual winners changed by the generated-identity shadow policy:")
            out.append(md_table_row(["time", "line_no", "production", "action", "shadow", "production score", "shadow score", "phase"]))
            out.append(md_table_row(["---"] * 8))
            for item in changed_identity[:20]:
                out.append(md_table_row([
                    item.get("time", ""), item.get("line_no", ""),
                    f"{item.get('production_winner', '')} ({item.get('production_source', '')})",
                    item.get("production_identity_action", ""),
                    f"{item.get('shadow_winner', '')} ({item.get('shadow_winner_source', '')})",
                    item.get("production_score", ""), item.get("shadow_winner_score", ""), item.get("selection_phase", ""),
                ]))
            out.append("")
        legacy_identity = [
            item for item, category in zip(identity_events, identity_categories)
            if category == "legacy_policy_causation_unverified"
        ]
        if legacy_identity:
            out.append("Legacy shadow winner differences with unverified policy causation:")
            out.append("These observations predate saved-random-state counterfactual telemetry and are excluded from winner-change counts.")
            out.append("")
            out.append(md_table_row(["time", "line_no", "production", "reported shadow", "phase"]))
            out.append(md_table_row(["---"] * 5))
            for item in legacy_identity[:20]:
                out.append(md_table_row([
                    item.get("time", ""), item.get("line_no", ""), item.get("production_winner", ""),
                    item.get("shadow_winner", ""), item.get("selection_phase", ""),
                ]))
            out.append("")
        origin_only_production = [
            item for item in identity_events
            if item.get("production_identity_action") == "generated_cross_quote_origin_only_excluded"
        ]
        if origin_only_production:
            out.append("Production winners excluded by origin-quote-only shadow policy:")
            out.append(md_table_row(["time", "line_no", "production", "shadow replacement", "phase"]))
            out.append(md_table_row(["---"] * 5))
            for item in origin_only_production[:20]:
                out.append(md_table_row([
                    item.get("time", ""), item.get("line_no", ""), item.get("production_winner", ""),
                    f"{item.get('shadow_winner', '')} ({item.get('shadow_winner_source', '')})", item.get("selection_phase", ""),
                ]))
            out.append("")
    def compact_counts(values: Dict[str, Any]) -> str:
        visible = [(name, count) for name, count in values.items() if isinstance(count, int) and count > 0]
        return ", ".join(f"{name}={count}" for name, count in visible) or "none observed"

    context_quality = report.get("historical_context_quality") or {}
    out.append("## Historical context reply quality")
    status_counts = context_quality.get("status_counts") or {}
    out.append(
        f"Attempted: **{context_quality.get('attempted_count', 0)}**; "
        f"completed: **{status_counts.get('completed', 0)}**; "
        f"already completed: **{status_counts.get('already_completed', 0)}**; "
        f"failed: **{status_counts.get('failed', 0)}**; "
        f"skipped: **{status_counts.get('skipped', 0)}**; "
        f"dry run: **{status_counts.get('dry_run', 0)}**."
    )
    out.append(
        "Lengths (raw average / X-weighted average / weighted range): "
        f"**{round(context_quality['average_raw_characters'], 1) if context_quality.get('average_raw_characters') is not None else 'unavailable'} / "
        f"{round(context_quality['average_weighted_characters'], 1) if context_quality.get('average_weighted_characters') is not None else 'unavailable'} / "
        f"{context_quality.get('minimum_weighted_characters') if context_quality.get('minimum_weighted_characters') is not None else 'unavailable'}–"
        f"{context_quality.get('maximum_weighted_characters') if context_quality.get('maximum_weighted_characters') is not None else 'unavailable'}**."
    )
    out.append(
        f"Length metadata (raw observed/unavailable; weighted observed/unavailable): "
        f"**{context_quality.get('raw_length_observation_count', 0)}/{context_quality.get('raw_length_metadata_unavailable_count', 0)}; "
        f"{context_quality.get('weighted_length_observation_count', 0)}/{context_quality.get('weighted_length_metadata_unavailable_count', 0)}**."
    )
    out.append(
        f"Shortened: **{context_quality.get('shortened_count', 0)}** "
        f"(metadata unavailable: {context_quality.get('shortening_metadata_unavailable_count', 0)}); "
        f"meaning omitted: **{context_quality.get('meaning_omitted_count', 0)}** "
        f"(metadata unavailable: {context_quality.get('meaning_omitted_metadata_unavailable_count', 0)}); "
        f"source omitted: **{context_quality.get('source_omitted_count', 0)}** "
        f"(metadata unavailable: {context_quality.get('source_omitted_metadata_unavailable_count', 0)}); "
        f"verification omitted: **{context_quality.get('verification_omitted_count', 0)}** "
        f"(metadata unavailable: {context_quality.get('verification_omitted_metadata_unavailable_count', 0)})."
    )
    for label, key in (("Verification labels", "verification_counts"), ("Source classes", "source_class_counts"),
                       ("Historical confidence", "confidence_counts")):
        values = context_quality.get(key) or {}
        out.append(f"{label}: {compact_counts(values)}")
    if context_quality.get("skip_reason_counts"):
        out.append("Skip reasons:")
        out.append(md_table_row(["reason", "count"]))
        out.append(md_table_row(["---", "---"]))
        for reason, count in context_quality["skip_reason_counts"].items():
            out.append(md_table_row([reason, count]))
    out.append("")

    engagement = report.get("historical_context_engagement") or {}
    out.append("## Historical context engagement")
    if not engagement.get("available"):
        out.append(f"Unavailable: **{engagement.get('reason') or 'analytics database not initialised'}**.")
    else:
        def engagement_percent(value: Any) -> str:
            return "metric unavailable" if value is None else f"{float(value) * 100:.2f}%"

        out.append(
            f"Trailing window: **{engagement.get('window_days', 28)} days**; "
            f"tracked post pairs: **{engagement.get('tracked_post_pairs', 0)}**; "
            f"with context replies: **{engagement.get('posts_with_context_replies', 0)}**; "
            f"snapshot coverage: **{engagement_percent(engagement.get('latest_snapshot_coverage'))}**."
        )
        out.append(
            "Medians (context view ratio / main engagement / context engagement / "
            "context bookmark / source-link click): "
            f"**{engagement_percent(engagement.get('median_context_view_ratio'))} / "
            f"{engagement_percent(engagement.get('median_main_post_engagement_rate'))} / "
            f"{engagement_percent(engagement.get('median_context_engagement_rate'))} / "
            f"{engagement_percent(engagement.get('median_context_bookmark_rate'))} / "
            f"{engagement_percent(engagement.get('median_source_link_click_rate'))}**."
        )
        out.append(
            f"Unavailable impressions/click metrics: "
            f"**{engagement.get('unavailable_impressions_count', 0)} / "
            f"{engagement.get('unavailable_click_metrics_count', 0)}**."
        )
        warnings = engagement.get("sample_size_warnings") or []
        if warnings:
            out.append("Sample-size warnings: " + ", ".join(str(value) for value in warnings) + ".")
        out.append("All associations are observational; the digest does not attribute causation.")
    out.append("")

    veto_section = report.get("quote_image_semantic_veto_shadow") or {}
    veto_window = veto_section.get("summary") or {}
    veto_runtime = veto_section.get("runtime_summary") or {}
    out.append("## Quote/image semantic veto shadow")
    if not veto_window.get("available") and not veto_runtime.get("available"):
        out.append(f"Unavailable: **{veto_runtime.get('reason') or 'shadow mode disabled'}**.")
    else:
        if veto_window.get("available"):
            summary = veto_window
            out.append(
                f"Selection-time observations: **{summary.get('selection_time_observations', 0)}**; "
                f"confirmed successful posts: **{summary.get('confirmed_successful_posts', 0)}**. "
                "Unconfirmed observations are not counted as posted outcomes."
            )
            allowed = summary.get("allowed_production_winners", 0)
            vetoed = summary.get("vetoed_production_winners", 0)
            unknown = summary.get("unknown_unjudged", 0)
            generated = summary.get("generated_out_of_scope", 0)
            in_scope = summary.get("in_scope_historical_selections", 0)
            with_alternative = summary.get("vetoed_with_allowed_alternative", 0)
            without_alternative = summary.get("vetoed_without_allowed_alternative", 0)
            selection_error = summary.get("selection_error_candidate_available", 0)
            coverage_gap = summary.get("coverage_gap_no_safe_image", 0)
            no_global = summary.get("quotes_with_no_globally_allowed_candidate", 0)
            median_delta = summary.get("median_alternative_score_delta")
            version = summary.get("manifest_policy_version", "unavailable")
            manifest_hash = summary.get("manifest_sha256") or ""
            failures = summary.get("lookup_failures", 0)
        else:
            summary = veto_runtime
            out.append(f"Runtime observations retained: **{summary.get('events', 0)}**.")
            allowed = summary.get("allowed", 0)
            vetoed = summary.get("vetoed", 0)
            unknown = summary.get("unknown", 0)
            generated = summary.get("generated_out_of_scope", 0)
            in_scope = int(allowed or 0) + int(vetoed or 0) + int(unknown or 0)
            with_alternative = summary.get("vetoed_with_alternative", 0)
            without_alternative = summary.get("vetoed_without_alternative", 0)
            selection_error = summary.get("selection_error_candidate_available", with_alternative)
            coverage_gap = summary.get("coverage_gap_no_safe_image", 0)
            no_global = summary.get("quotes_with_no_globally_allowed_candidate", 0)
            median_delta = summary.get("alternative_score_delta_median")
            version = summary.get("manifest_policy_version", "unavailable")
            manifest_hash = summary.get("manifest_sha256") or ""
            failures = int(summary.get("manifest_unavailable", 0) or 0) + int(summary.get("manifest_stale", 0) or 0)
        out.append("Selections:")
        out.append(f"- Allowed: **{allowed}**")
        out.append(f"- Vetoed: **{vetoed}**")
        out.append(f"  - alternative available: **{selection_error}**")
        out.append(f"  - no safe image exists: **{coverage_gap}**")
        out.append(f"- Unknown: **{unknown}**")
        out.append(f"- Generated out of scope: **{generated}**")
        out.append(
            f"In-scope historical selections: **{in_scope}**; vetoed with/without an allowed "
            f"candidate in the current set: **{with_alternative} / {without_alternative}**; "
            f"median alternative score delta: "
            f"**{f'{float(median_delta):.2f}' if median_delta is not None else 'unavailable'}**."
        )
        out.append(
            f"Manifest: **{version}** (`{str(manifest_hash)[:16] or 'unavailable'}`); "
            f"lookup failures: **{failures}**."
        )
        reason_counts = summary.get("veto_reason_counts") or {}
        if reason_counts:
            out.append("Veto reasons:")
            for reason, count in sorted(reason_counts.items(), key=lambda item: (-item[1], item[0])):
                out.append(f"- {reason}: **{count}**")
        out.append("")
        out.append("Coverage gaps:")
        out.append(f"- quotations with no safe historical image: **{no_global}**")
        examples = veto_window.get("examples") or []
        if examples:
            out.append("")
            out.append("| Quote | Production image | Category | Reason | Alternative | Score difference | Posted |")
            out.append("|---|---|---|---|---|---:|---|")
            for item in examples[:5]:
                out.append(md_table_row([
                    item.get("quote_preview") or "",
                    item.get("production_image") or "",
                    item.get("veto_category") or "unclassified",
                    item.get("veto_reason") or "",
                    item.get("alternative") or "none",
                    item.get("score_delta") if item.get("score_delta") is not None else "unavailable",
                    "yes" if item.get("confirmed_post") else "no",
                ]))
    out.append("")

    hybrid_shadow = report.get("hybrid_retrieval_shadow") or {}
    out.append("## Hybrid retrieval shadow")
    if not hybrid_shadow.get("available"):
        out.append(f"Unavailable: **{hybrid_shadow.get('reason') or 'shadow mode disabled'}**.")
    else:
        overlap = hybrid_shadow.get("top_5_overlap_percent")
        out.append(
            f"Events: **{hybrid_shadow.get('events', 0)}**; completed: **{hybrid_shadow.get('completed', 0)}**; "
            f"failures: **{hybrid_shadow.get('failures', 0)}**; lexical/hybrid top-5 overlap: "
            f"**{f'{float(overlap):.1f}%' if overlap is not None else 'unavailable'}**."
        )
        out.append(
            f"Changed evidence sets: **{hybrid_shadow.get('hybrid_changed_evidence_set', 0)}**; "
            f"hybrid-only: **{hybrid_shadow.get('hybrid_only_evidence', 0)}**; "
            f"lexical-only: **{hybrid_shadow.get('lexical_only_evidence', 0)}**; "
            f"no-evidence disagreements: **{hybrid_shadow.get('no_evidence_disagreements', 0)}**."
        )
        out.append(
            "Latency p50/p95/max: "
            f"**{hybrid_shadow.get('latency_p50_ms', 'unavailable')} / "
            f"{hybrid_shadow.get('latency_p95_ms', 'unavailable')} / "
            f"{hybrid_shadow.get('latency_max_ms', 'unavailable')} ms**; "
            f"index/model: **{hybrid_shadow.get('index_version', 'unavailable')} / "
            f"{hybrid_shadow.get('model_revision', 'unavailable')}**."
        )
    out.append("")

    strategy = report.get("reply_strategy") or {}
    out.append("## Conversational reply strategy")
    out.append("Generated decisions: " + compact_counts(strategy.get("generated_mode_counts") or {}))
    out.append("Public outcomes: " + compact_counts(strategy.get("outcome_status_counts") or {}))
    out.append("Published/terminal modes: " + compact_counts(strategy.get("mode_counts") or {}))
    for lane, counts in (strategy.get("mode_counts_by_lane") or {}).items():
        if counts:
            out.append(f"{lane}: {compact_counts(counts)}")
    out.append(
        f"Grounded decisions generated: **{strategy.get('generated_grounded_count', 0)}**; "
        f"grounded replies posted: **{strategy.get('posted_grounded_count', 0)}**; "
        f"factual decisions generated: **{strategy.get('generated_factual_claim_count', 0)}**."
    )
    out.append(
        f"Generated retrieved packets average/max/none: **"
        f"{round(strategy['generated_average_retrieved_packet_count'], 2) if strategy.get('generated_average_retrieved_packet_count') is not None else 'unavailable'} / "
        f"{strategy.get('generated_maximum_retrieved_packet_count') if strategy.get('generated_maximum_retrieved_packet_count') is not None else 'unavailable'} / "
        f"{strategy.get('generated_no_retrieved_packets_count', 0)}**."
    )
    out.append("Generated evidence confidence: " + compact_counts(strategy.get("generated_confidence_counts") or {}))
    out.append("Generated humour tones: " + compact_counts(strategy.get("generated_humour_tone_counts") or {}))
    out.append(
        f"Published/terminal grounded: **{strategy.get('grounded_count', 0)}** "
        f"(metadata unavailable: {strategy.get('grounding_metadata_unavailable_count', 0)}); "
        f"humour-only ungrounded: **{strategy.get('ungrounded_humour_only_count', 0)}**; "
        f"factual claims: **{strategy.get('factual_claim_count', 0)}** "
        f"(metadata unavailable: {strategy.get('factual_claim_metadata_unavailable_count', 0)}); "
        f"factual grounding rejections: **{strategy.get('factual_rejected_insufficient_grounding_count', 0)}**."
    )
    out.append(
        f"Retrieved packets average/max/none: **{round(strategy['average_retrieved_packet_count'], 2) if strategy.get('average_retrieved_packet_count') is not None else 'unavailable'} / "
        f"{strategy.get('maximum_retrieved_packet_count') if strategy.get('maximum_retrieved_packet_count') is not None else 'unavailable'} / "
        f"{strategy.get('no_retrieved_packets_count', 0)}** "
        f"(metadata unavailable: {strategy.get('retrieved_packet_metadata_unavailable_count', 0)})."
    )
    out.append("Published/terminal evidence confidence: " + compact_counts(strategy.get("confidence_counts") or {}))
    out.append("Published/terminal humour tones: " + compact_counts(strategy.get("humour_tone_counts") or {}))
    out.append("No-reply categories: " + compact_counts(strategy.get("no_reply_category_counts") or {}))
    out.append("Repetition controls: " + compact_counts(strategy.get("repetition_control_counts") or {}))
    if strategy.get("rejection_reason_counts"):
        out.append("Editorial no-reply/rejections:")
        out.append(md_table_row(["reason", "count"]))
        out.append(md_table_row(["---", "---"]))
        for reason, count in strategy["rejection_reason_counts"].items():
            out.append(md_table_row([reason, count]))
    if strategy.get("routine_skip_reason_counts"):
        out.append("Routine scheduling skips (separate):")
        out.append(md_table_row(["reason", "count"]))
        out.append(md_table_row(["---", "---"]))
        for reason, count in strategy["routine_skip_reason_counts"].items():
            out.append(md_table_row([reason, count]))
    out.append("")

    stats = report["summary"].get("stats", {})
    routine = report["summary"].get("routine_skip_counts", {})
    out.append("## Counts")
    out.append("```json")
    out.append(json.dumps({"stats": stats, "routine_skip_counts": routine}, indent=2, ensure_ascii=False))
    out.append("```")
    out.append("")

    events = report.get("events") or []
    by_kind: Dict[str, List[Dict[str, Any]]] = {}
    for ev in events:
        by_kind.setdefault(ev["kind"], []).append(ev)

    def section(kind: str, title: str, cols: List[str]) -> None:
        rows = by_kind.get(kind) or []
        if not rows:
            return
        out.append(f"## {title}")
        out.append(md_table_row(cols))
        out.append(md_table_row(["---"] * len(cols)))
        for ev in rows:
            out.append(md_table_row([ev.get(c, "") for c in cols]))
        out.append("")

    section("quote_image_posted", "Quote/image posts", ["time", "post_id", "line_no", "quote_hash", "image_basename", "image_no", "image_score", "made_with_ai", "text"])
    section("daily_meme_posted", "Daily meme posts", ["time", "post_id", "file", "summary"])
    section("quote_selected", "Regular quote selections", ["time", "line_no", "quote_hash", "weight", "seasonal_boost"])
    section("matched_image_selected", "Matched image selections", ["time", "image", "image_no", "score", "components"])
    section("regular_image_selected", "Regular image selection metadata", ["time", "source", "basename", "score", "origin_quote_hash", "origin_quote_match", "origin_quote_boost"])
    section("image_cycle_status", "Image cycle status", ["time", "used_count", "currently_eligible", "remaining_count", "seasonally_excluded", "stale_excluded", "cycle_reset"])
    section("quote_cycle_reset", "Quote cycle resets", ["time", "reason", "affected", "full_selectable", "full_hard_excluded"])
    section("mention_reply_posted", "Mention replies", ["time", "mention_id", "author_id", "incoming_text", "reply", "reply_post_id"])
    section("hot_post_reply_posted", "Hot-post replies", ["time", "hot_post_reply_id", "author_id", "incoming_text", "reply", "reply_post_id"])
    section("quote_tweet_reply_posted", "Quote-tweet replies", ["time", "quote_tweet_id", "author_id", "original_post_id", "incoming_text", "reply", "reply_post_id"])
    section(
        "historical_context_reply",
        "Historical context replies",
        ["time", "status", "parent_post_id", "quote_id", "weighted_character_count", "verification_label", "source_class", "historical_confidence", "shortening_applied", "reason"]
        + (["reply_preview"] if report.get("verbose_replies") else []),
    )
    section(
        "reply_strategy_decision",
        "Reply strategy decisions",
        ["time", "lane", "mode", "humour_tone", "evidence_confidence", "retrieved_count", "factual_claim", "grounded", "no_reply_reason"],
    )
    section(
        "reply_strategy_outcome",
        "Reply strategy outcomes",
        ["time", "status", "lane", "target_id", "reply_post_id", "mode", "humour_tone", "evidence_confidence", "retrieved_count", "factual_claim", "grounded", "failure_reason"],
    )
    section(
        "reply_target_terminal",
        "Terminal reply targets",
        ["time", "lane", "target_id", "outcome", "reason"],
    )
    section("hot_post_search_result", "Hot-post recent-search results", ["time", "original_post_id", "candidates"])
    section("mention_grok_skip", "Mention Grok skips", ["time", "mention_id", "author_id", "incoming_text"])
    section("hot_post_reply_grok_skip", "Hot-post Grok skips", ["time", "hot_post_reply_id", "author_id", "incoming_text"])
    section("quote_tweet_grok_skip", "Quote-tweet Grok skips", ["time", "quote_tweet_id", "author_id", "original_post_id", "incoming_text"])
    section("mention_skipped", "Mention direct skips", ["time", "mention_id", "author_id", "incoming_text", "reason"])
    section("hot_post_reply_skipped", "Hot-post direct skips", ["time", "hot_post_reply_id", "author_id", "incoming_text", "reason"])
    section("quote_tweet_skipped", "Quote-tweet direct skips", ["time", "quote_tweet_id", "reason"])
    section("api_cooldown_entered", "API cooldowns entered", ["time", "reason", "until"])
    section("used_history_migrated", "Used-history migrations", ["time", "legacy_file", "json_file"])
    section("used_history_normalized", "Used-history normalizations", ["time", "json_file"])

    recovery = report.get("main_post_recovery") or {}
    receipt_events = recovery.get("receipt_events") or []
    confirmed_post_recovery = recovery.get("confirmed_post_recovery") or []
    if receipt_events or confirmed_post_recovery:
        out.append("## Main-post recovery")
        if receipt_events:
            out.append("Receipt lifecycle:")
            out.append(md_table_row(["time", "level", "lane", "kind", "post_id", "quote_hash", "image/file", "message"]))
            out.append(md_table_row(["---", "---", "---", "---", "---", "---", "---", "---"]))
            for item in receipt_events:
                out.append(md_table_row([
                    item.get("time", ""),
                    item.get("level", ""),
                    item.get("lane", ""),
                    item.get("kind", ""),
                    item.get("post_id", ""),
                    item.get("quote_hash", ""),
                    item.get("image", item.get("file", "")),
                    item.get("message", ""),
                ]))
            out.append("")
        if confirmed_post_recovery:
            out.append("Confirmed remote posts with local recovery/persistence trouble:")
            out.append(md_table_row(["time", "level", "where", "message"]))
            out.append(md_table_row(["---", "---", "---", "---"]))
            for item in confirmed_post_recovery:
                out.append(md_table_row([
                    item.get("time", ""),
                    item.get("level", ""),
                    item.get("where", ""),
                    item.get("message", ""),
                ]))
            out.append("")

    reply_recovery = report.get("confirmed_reply_recovery") or {}
    reply_receipt_events = reply_recovery.get("receipt_events") or []
    reply_recovery_warnings = reply_recovery.get("warnings") or []
    if reply_receipt_events or reply_recovery_warnings:
        out.append("## Confirmed-reply recovery")
        if reply_receipt_events:
            out.append("Receipt lifecycle:")
            out.append(md_table_row(["time", "level", "lane", "kind", "target_id", "reply_post_id", "message"]))
            out.append(md_table_row(["---", "---", "---", "---", "---", "---", "---"]))
            for item in reply_receipt_events:
                out.append(md_table_row([
                    item.get("time", ""),
                    item.get("level", ""),
                    item.get("lane", ""),
                    item.get("kind", ""),
                    item.get("target_id", ""),
                    item.get("reply_post_id", ""),
                    item.get("message", ""),
                ]))
            out.append("")
        if reply_recovery_warnings:
            out.append("Confirmed replies with local recovery/persistence trouble:")
            out.append(md_table_row(["time", "level", "where", "message"]))
            out.append(md_table_row(["---", "---", "---", "---"]))
            for item in reply_recovery_warnings:
                out.append(md_table_row([
                    item.get("time", ""),
                    item.get("level", ""),
                    item.get("where", ""),
                    item.get("message", ""),
                ]))
            out.append("")

    reply_media_context = report.get("reply_media_context") or []
    if reply_media_context:
        out.append("## Reply media context")
        out.append(md_table_row(["time", "level", "lane", "target_id", "photos", "mode", "status", "http_status"]))
        out.append(md_table_row(["---", "---", "---", "---", "---", "---", "---", "---"]))
        for item in reply_media_context:
            out.append(md_table_row([
                item.get("time", ""),
                item.get("level", ""),
                item.get("lane", ""),
                item.get("target_id", ""),
                item.get("photos", ""),
                item.get("mode", ""),
                item.get("status", ""),
                item.get("http_status", ""),
            ]))
        out.append("")

    asset_health = report.get("asset_health") or []
    if asset_health:
        out.append("## Asset metadata health")
        out.append(md_table_row(["time", "level", "kind", "message"]))
        out.append(md_table_row(["---", "---", "---", "---"]))
        for item in asset_health:
            out.append(md_table_row([
                item.get("time", ""),
                item.get("level", ""),
                item.get("kind", ""),
                item.get("message", ""),
            ]))
        out.append("")

    api_health = report.get("api_health") or {}
    api_errors = api_health.get("errors") or []
    handled_restrictions = api_health.get("handled_restrictions") or []
    cooldown_active = api_health.get("cooldown_active") or []
    post_cooldown_errors = api_health.get("post_cooldown_errors") or []
    if api_errors or handled_restrictions or cooldown_active:
        out.append("## API health")
        out.append(
            f"Unique incidents: **{api_health.get('unique_incident_count', 0)}**; "
            f"posting attempts: **{api_health.get('posting_attempt_count', 0)}**; "
            f"Target-eligibility 403 responses: **{api_health.get('target_eligibility_403_count', 0)}**; "
            f"transient transport failures: **{api_health.get('transient_failure_count', 0)}**; "
            f"rate-limit failures: **{api_health.get('rate_limit_failure_count', 0)}**."
        )
        out.append("")
        if api_errors:
            out.append(md_table_row(["time", "service", "endpoint", "status", "window", "remaining", "message"]))
            out.append(md_table_row(["---", "---", "---", "---", "---", "---", "---"]))
            for item in api_errors:
                out.append(md_table_row([
                    item.get("time", ""),
                    item.get("service", ""),
                    item.get("endpoint", ""),
                    item.get("status", ""),
                    item.get("errors_in_window", ""),
                    item.get("remaining", ""),
                    item.get("message", ""),
                ]))
            out.append("")
        if handled_restrictions:
            out.append("Handled API restrictions:")
            out.append(md_table_row(["time", "service", "endpoint", "status", "lane", "target", "message"]))
            out.append(md_table_row(["---", "---", "---", "---", "---", "---", "---"]))
            for item in handled_restrictions:
                out.append(md_table_row([
                    item.get("time", ""),
                    item.get("service", ""),
                    item.get("endpoint", ""),
                    item.get("status", ""),
                    item.get("lane", ""),
                    item.get("target_id", ""),
                    item.get("message", ""),
                ]))
            out.append("")
        if cooldown_active:
            out.append("Cooldown-active checks:")
            out.append(md_table_row(["time", "until", "reason"]))
            out.append(md_table_row(["---", "---", "---"]))
            for item in cooldown_active:
                out.append(md_table_row([item.get("time", ""), item.get("until", ""), item.get("reason", "")]))
            out.append("")
        if post_cooldown_errors:
            out.append("Post-cooldown errors:")
            out.append(md_table_row(["time", "service", "endpoint", "status", "window"]))
            out.append(md_table_row(["---", "---", "---", "---", "---"]))
            for item in post_cooldown_errors:
                out.append(md_table_row([
                    item.get("time", ""),
                    item.get("service", ""),
                    item.get("endpoint", ""),
                    item.get("status", ""),
                    item.get("errors_in_window", ""),
                ]))
            out.append("")
        if api_health.get("has_5xx_failures") and api_health.get("not_rate_limited"):
            out.append("503/5xx summary: likely upstream/API-side failure, not quota exhaustion; remaining quota was non-zero on recorded error headers.")
            out.append("")
        if handled_restrictions:
            legacy_cooldowns = int(api_health.get("legacy_cooldown_from_target_restriction_count", 0) or 0)
            if legacy_cooldowns:
                out.append(
                    "403 restriction summary: deterministic target restrictions were identified; "
                    f"**{legacy_cooldowns} legacy cooldown activation(s)** in this historical window "
                    "were caused by the pre-fix classification."
                )
            else:
                out.append("403 restriction summary: target conversation controls disallowed the reply; handled locally without quota/cooldown impact.")
            out.append("")

    self_test_errors = report.get("self_test_errors") or []
    if self_test_errors:
        out.append("## Self-test failures")
        out.append(md_table_row(["time", "level", "where", "message"]))
        out.append(md_table_row(["---", "---", "---", "---"]))
        for e in self_test_errors:
            out.append(md_table_row([e.get("time"), e.get("level"), e.get("where"), e.get("message")]))
        out.append("")

    errs = report.get("errors_and_warnings") or []
    out.append("## Errors / warnings")
    if not errs:
        out.append("None found in selected window.")
    else:
        out.append(md_table_row(["time", "level", "where", "message"]))
        out.append(md_table_row(["---", "---", "---", "---"]))
        for e in errs:
            out.append(md_table_row([e.get("time"), e.get("level"), e.get("where"), e.get("message")]))
    out.append("")

    if report.get("lifecycle"):
        out.append("## Lifecycle")
        out.append("```text")
        for item in report["lifecycle"]:
            out.append(f"{item['time']} {item['level']} {item['message']}")
        out.append("```")
        out.append("")

    cfg = report.get("latest_config") or {}
    if cfg:
        out.append("## Latest config seen")
        if cfg.get("_carried_forward"):
            out.append("Config source: carried forward from previous digest state.")
        elif cfg.get("_carried_from_log_backscan"):
            ts = cfg.get("_log_backscan_timestamp")
            out.append(f"Config source: backfilled from earlier log scan{f' at {ts}' if ts else ''}.")
        elif cfg.get("_filled_from_previous") and cfg.get("_filled_from_log_backscan"):
            ts = cfg.get("_log_backscan_timestamp")
            out.append(f"Config source: current window plus missing values from previous digest state and earlier log scan{f' at {ts}' if ts else ''}.")
        elif cfg.get("_filled_from_log_backscan"):
            ts = cfg.get("_log_backscan_timestamp")
            out.append(f"Config source: current window plus missing values from earlier log scan{f' at {ts}' if ts else ''}.")
        elif cfg.get("_filled_from_previous"):
            out.append("Config source: current window plus missing values from previous digest state.")
        keep = [
            "MAX_AUTO_REPLIES_PER_DAY", "MAX_QUOTE_REPLIES_PER_DAY", "MIN_SECONDS_BETWEEN_REPLIES",
            "REPLY_CHECK_EVERY_SECONDS", "MAX_MENTIONS_PER_CHECK", "MENTIONS_MAX_PAGES_PER_CHECK",
            "QUOTE_CHECK_EVERY_SECONDS", "QUOTE_LOOKUP_API_MAX_RESULTS", "QUOTE_LOOKUP_MAX_PAGES_PER_POST",
            "QUOTE_CHECK_SPACING_RETRY_SECONDS", "ENABLE_HOT_POST_REPLY_CHECKS",
            "MAX_HOT_POST_REPLIES_PER_CHECK", "HOT_POST_REPLY_SEARCH_API_MAX_RESULTS",
            "HOT_POST_REPLY_SEARCH_MAX_PAGES_PER_CHECK",
            "ENABLE_DAILY_MEME_POSTS", "MEME_TRIGGER_AFTER_HOUR",
            "MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS", "MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS",
            "MEME_FALLBACK_HOUR", "MEME_FALLBACK_MINUTE",
            "MEME_MIN_SECONDS_AFTER_QUOTE_POST", "MEME_SCHEDULE_VERSION",
            "MAX_QUOTE_IMAGE_PAIR_ATTEMPTS",
            "QUOTE_ANALYSIS_FILE", "IMAGE_ANALYSIS_FILE", "QUOTE_ANALYSIS_OVERRIDES_FILE",
            "IMAGE_STRONG_MISMATCH_PENALTY",
            "POST_SLEEP_MIN", "POST_SLEEP_MAX",
        ]
        out.append("```text")
        for k in keep:
            if k in cfg:
                out.append(f"{k}={cfg[k]}")
        out.append("```")
        out.append("")

    return "\n".join(out)


def deliver_report(rendered: str, output_path: Optional[Path] = None) -> None:
    """Deliver a complete report before the caller advances resume state."""
    if output_path is None:
        sys.stdout.write(rendered)
        sys.stdout.flush()
        return
    output_path = output_path.expanduser().resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_suffix(output_path.suffix + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, output_path)
    except Exception:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Summarise MrsMThatcher bot logs into a compact digest.")
    ap.add_argument(
        "logs",
        nargs="*",
        type=Path,
        help="Optional explicit log files. If omitted, logs are auto-discovered in the current directory.",
    )
    ap.add_argument("--since", help="Only include records at/after this local timestamp, e.g. '2026-06-25 08:00'. Overrides saved resume time.")
    ap.add_argument("--until", help="Only include records at/before this local timestamp.")
    ap.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of Markdown.")
    ap.add_argument("--output", type=Path, help="Atomically write the report to this file instead of stdout.")
    ap.add_argument("--markdown-output", type=Path, help="Also atomically write Markdown to this file.")
    ap.add_argument("--json-output", type=Path, help="Also atomically write structured JSON to this file.")
    ap.add_argument("--verbose-replies", action="store_true", help="Include truncated context-reply previews in Markdown event detail.")
    ap.add_argument("--max-text", type=int, default=280, help="Maximum text length per field in report. Default: 280.")
    ap.add_argument("--glob", default="mrsMThatcher*.log*", help="Log glob to use when no explicit log files are supplied. Default: mrsMThatcher*.log*")
    ap.add_argument("--project-dir", type=Path, default=Path(__file__).resolve().parent, help="Project directory for config, metadata, history and auto-discovered logs.")
    ap.add_argument("--state-file", type=Path, default=Path(".mrs_log_digest_state.json"), help="Resume-state file, relative to --project-dir unless absolute.")
    ap.add_argument("--no-state", action="store_true", help="Do not read or update the resume-state file.")
    ap.add_argument("--reset-state", action="store_true", help="Ignore any existing resume-state file for this run; save the new end timestamp afterwards.")
    ap.add_argument("--no-update-state", action="store_true", help="Read resume state, but do not write the new end timestamp.")
    args = ap.parse_args(argv)

    output_paths = [
        path.expanduser().resolve()
        for path in (args.output, args.markdown_output, args.json_output)
        if path is not None
    ]
    if len(output_paths) != len(set(output_paths)):
        ap.error("output paths must be distinct")

    project_dir = args.project_dir.expanduser().resolve()
    state_file = args.state_file.expanduser()
    if not state_file.is_absolute():
        state_file = project_dir / state_file
    lock_paths: List[Path] = []
    if not args.no_state:
        lock_paths.append(state_file.with_suffix(state_file.suffix + ".lock"))
    else:
        for output in output_paths:
            lock_paths.append(output.with_suffix(output.suffix + ".lock"))

    if not lock_paths:
        return run_digest(args, project_dir=project_dir, state_file=state_file)
    with ExitStack() as stack:
        for lock_path in sorted(set(lock_paths), key=str):
            stack.enter_context(digest_execution_lock(lock_path))
        return run_digest(args, project_dir=project_dir, state_file=state_file)


def run_digest(args: argparse.Namespace, *, project_dir: Path, state_file: Path) -> int:
    if args.logs:
        logs = resolve_explicit_logs(args.logs, project_dir)
    else:
        logs = discover_logs(project_dir, args.glob)

    if not logs:
        raise SystemExit(
            f"No log files found. Run this in the log directory or pass files explicitly. "
            f"Auto-discovery pattern was: {args.glob!r}"
        )

    since_source = None
    since_exclusive = False
    resume_boundary_fingerprints: set[str] = set()
    resume_data: Dict[str, Any] = {}

    if args.since:
        since = parse_dt(args.since)
        since_source = "manual --since"
        since_exclusive = False
    elif not args.no_state and not args.reset_state:
        resume_data = read_resume_data(state_file)
        since = None
        if resume_data:
            try:
                since = parse_dt(resume_data.get("last_log_entry_time"))
            except Exception as e:
                print(
                    f"WARNING: ignoring invalid resume timestamp in {state_file}: "
                    f"{resume_data.get('last_log_entry_time')!r} ({e})",
                    file=sys.stderr,
                )
                since = None
            resume_boundary_fingerprints = {
                str(value)
                for value in resume_data.get("last_log_entry_fingerprints", [])
                if value
            }
        if since:
            since_source = "saved resume state"
            since_exclusive = not bool(resume_boundary_fingerprints)
    else:
        since = None

    until = parse_dt(args.until)
    records = read_records(logs, since, until, since_exclusive=since_exclusive)
    if since is not None and resume_boundary_fingerprints:
        records = [
            record
            for record in records
            if not (record.ts == since and record_fingerprint(record) in resume_boundary_fingerprints)
        ]
    input_files = summarize_input_files(logs, since, until, since_exclusive=since_exclusive)
    initial_active_xai_context = None
    initial_pending_mention = None
    initial_pending_qt = None
    if since_source == "saved resume state":
        if isinstance(resume_data.get("last_active_xai_context"), dict):
            initial_active_xai_context = resume_data.get("last_active_xai_context")
        if isinstance(resume_data.get("last_pending_mention"), dict):
            initial_pending_mention = dict(resume_data.get("last_pending_mention") or {})
            initial_pending_mention["considered_seq"] = -1
        if isinstance(resume_data.get("last_pending_qt"), dict):
            initial_pending_qt = dict(resume_data.get("last_pending_qt") or {})
            initial_pending_qt["considered_seq"] = -1
    report = analyse(
        records,
        max_text=args.max_text,
        initial_active_xai_context=initial_active_xai_context,
        initial_pending_mention=initial_pending_mention,
        initial_pending_qt=initial_pending_qt,
    )
    report_window_end = until or (records[-1].ts if records else None)

    report["log_files"] = [str(p) for p in logs]
    report["input_files"] = input_files
    report["input_warning"] = None
    if not records and any(int(item.get("records_in_window") or 0) > 0 for item in input_files):
        report["input_warning"] = (
            "selected log sources contain timestamped records inside the requested window, "
            "but 0 records survived filtering"
        )
    report["requested_since"] = dt_text(since) if since else None
    report["since_source"] = since_source
    report["since_exclusive"] = since_exclusive
    report["resume_boundary_fingerprint_count"] = len(resume_boundary_fingerprints)
    report["project_dir"] = str(project_dir)
    report["resume_state_file"] = None if args.no_state else str(state_file)
    report["state_updated"] = False
    report["generated_image_pool_health"] = generated_pool_health_snapshot(project_dir)
    try:
        from mrs_engagement_analytics import read_digest_summary

        report["historical_context_engagement"] = read_digest_summary(project_dir, window_days=28)
    except Exception as exc:
        # Analytics is optional. A missing dependency or malformed runtime database
        # must not prevent an otherwise valid production digest.
        report["historical_context_engagement"] = {
            "available": False,
            "reason": f"analytics summary unavailable: {type(exc).__name__}",
            "tracked_post_pairs": 0,
        }
    report["hybrid_retrieval_shadow"] = hybrid_retrieval_shadow_snapshot(project_dir)
    veto_section = report.setdefault("quote_image_semantic_veto_shadow", {"events": [], "summary": {}})
    veto_section["runtime_summary"] = quote_image_semantic_veto_shadow_snapshot(project_dir)

    authoritative_state, authoritative_state_path, authoritative_state_ts = load_authoritative_state_for_logs(logs)
    if (
        authoritative_state is not None
        and (report_window_end is None or authoritative_state_ts is None or authoritative_state_ts <= report_window_end)
    ):
        report["latest_state"] = summarize_latest_state(
            authoritative_state,
            authoritative_state_ts,
            source="bot_state.json",
            source_path=authoritative_state_path,
        )

    # v5: if this incremental window has no startup Config lines, scan earlier
    # records in the same log files for the most recent Config values before
    # the window. This avoids "5 / ?" budget output after quiet windows, even
    # when the digest resume state has not yet stored config context.
    cutoff_for_backscan = records[0].ts if records else since
    if cutoff_for_backscan is not None:
        backscan_config, backscan_ts = find_latest_config_before(logs, cutoff_for_backscan)
        if backscan_config:
            report["latest_config"] = merge_context_from_log_backscan(
                report.get("latest_config") or {},
                backscan_config,
                backscan_ts=backscan_ts,
            )
            report["config_backscan_timestamp"] = dt_text(backscan_ts) if backscan_ts else None

    if not args.no_state and not args.reset_state:
        apply_saved_context(report, state_file, window_end=report_window_end)
    else:
        refresh_derived(report)

    if not records:
        report["saved_last_log_entry_time"] = dt_text(since) if since else None

    runway_config = load_runway_config(project_dir, dict(report.get("latest_config") or {}))
    report["generated_image_post_rates"] = generated_post_rate_history(logs)
    report["generated_image_pool_runway"] = generated_pool_runway(report.get("generated_image_pool_health") or {}, report["generated_image_post_rates"], runway_config)
    report["generated_image_utilisation"] = generated_image_utilisation(report.get("generated_image_pool_health") or {}, report["generated_image_post_rates"])
    report["verbose_replies"] = bool(args.verbose_replies)

    if args.json:
        rendered = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
    else:
        rendered = render_markdown(report) + "\n"
        if not records:
            rendered += "\n<!-- no matching records; resume state not advanced -->\n"

    deliver_report(rendered, args.output)
    if args.markdown_output is not None:
        deliver_report(render_markdown(report) + "\n", args.markdown_output)
    if args.json_output is not None:
        deliver_report(json.dumps(report, indent=2, ensure_ascii=False) + "\n", args.json_output)

    if records and not args.no_state and not args.no_update_state:
        last_ts = records[-1].ts
        save_resume_time(
            state_file,
            last_ts,
            records,
            report,
            logs,
            preserve_existing_context=not args.reset_state,
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
