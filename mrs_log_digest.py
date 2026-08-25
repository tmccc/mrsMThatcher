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

Enhanced v10: keeps the v9 retention and configured-manifest checks, adds a
strict read-only snapshot of the active remote-write protocol, correlates X
errors with their exact request endpoints, understands the receipt/media/
transport lifecycle, and resolves historical ambiguity incidents only from
durable reconciliation evidence.
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
import stat
import statistics
import sys
import tempfile
from collections import Counter
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

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
RESUME_FINGERPRINT_TAIL_LIMIT = 128
USD_TICKS_PER_DOLLAR = 10_000_000_000
USD_DISPLAY_QUANTUM = Decimal("0.00000001")
OPENAI_COST_CACHE_SCHEMA_VERSION = 1
OPENAI_COST_CACHE_SOURCE = "openai_organization_costs"
OPENAI_COST_CACHE_PATH = (
    Path.home() / ".local/state/mrsMThatcher/openai-costs/daily_costs.json"
)
OPENAI_COST_CACHE_MAX_BYTES = 16 * 1024 * 1024
OPENAI_COST_CACHE_STALE_AFTER_SECONDS = 2 * 60 * 60
OPENAI_COST_SAMPLE_BOUNDARY_MAX_GAP_SECONDS = 2 * 60 * 60
SEMANTIC_VETO_NAMED_COVERAGE_QUOTE_ID = (
    "0a67f403a7ac02347e43791d2daf3057aabdcfd64b62edbe1b3484a3a4b66729"
)
REMOTE_WRITE_MARKER_BASENAMES = (
    "ambiguous_post_outcome.json",
    "ambiguous_post_outcome.restart_barrier.json",
)
REMOTE_WRITE_SOURCE_RECEIPT_BASENAMES = (
    "regular_post_receipt.json",
    "meme_post_receipt.json",
    "confirmed_reply_receipt.json",
    "historical_context_reply_receipt.json",
)
REMOTE_MEDIA_RECEIPT_BASENAME = "remote_media_upload_receipt.json"
REMOTE_MEDIA_FENCE_BASENAME = "remote_media_upload_receipt.json.fence.json"
REMOTE_TRANSPORT_JOURNAL_BASENAME = "remote_write_transport_journal.json"
REMOTE_TRANSPORT_FENCE_BASENAME = "remote_write_transport_fence.json"
REMOTE_WRITE_ARCHIVE_BASENAME = "remote_write_safety_marker_archive"
REMOTE_WRITE_SNAPSHOT_MAX_BYTES = 256 * 1024
REMOTE_WRITE_CONTROL_BOOLEAN_KEYS = frozenset(
    {
        "disable_all",
        "pause_all",
        "disable_replies",
        "pause_replies",
        "disable_normal_replies",
        "pause_normal_replies",
        "disable_quote_replies",
        "pause_quote_replies",
        "disable_hot_post_replies",
        "pause_hot_post_replies",
        "disable_quote_posts",
        "pause_quote_posts",
        "disable_meme_posts",
        "pause_meme_posts",
    }
)
REMOTE_WRITE_CONTROL_TIME_KEYS = frozenset(
    f"{key}_until" for key in REMOTE_WRITE_CONTROL_BOOLEAN_KEYS
)
REMOTE_WRITE_CONTROL_ALLOWED_KEYS = (
    REMOTE_WRITE_CONTROL_BOOLEAN_KEYS
    | REMOTE_WRITE_CONTROL_TIME_KEYS
    | {"generation"}
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


def file_sha256(path: Path) -> str:
    """Return the file SHA-256."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _stable_file_identity(metadata: os.stat_result) -> Tuple[int, ...]:
    """Return fields that bind one read-only filesystem observation."""

    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_mode),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
        int(metadata.st_ctime_ns),
    )


def read_stable_regular_bytes(path: Path, *, maximum: int) -> bytes:
    """Read one bounded regular file twice-bound to its no-follow pathname."""

    path = Path(path)
    before_path = os.lstat(path)
    if not stat.S_ISREG(before_path.st_mode):
        raise ValueError(f"not a regular file: {path.name}")
    if before_path.st_size > maximum:
        raise ValueError(f"file exceeds {maximum} bytes: {path.name}")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise RuntimeError("O_NOFOLLOW is unavailable")
    descriptor = os.open(
        path,
        os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        before_fd = os.fstat(descriptor)
        if _stable_file_identity(before_fd) != _stable_file_identity(before_path):
            raise RuntimeError(f"path changed before open: {path.name}")
        chunks: List[bytes] = []
        observed = 0
        while observed <= maximum:
            chunk = os.read(descriptor, min(8192, maximum + 1 - observed))
            if not chunk:
                break
            chunks.append(chunk)
            observed += len(chunk)
        data = b"".join(chunks)
        after_fd = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    after_path = os.lstat(path)
    if (
        len(data) > maximum
        or len(data) != before_fd.st_size
        or _stable_file_identity(before_fd) != _stable_file_identity(after_fd)
        or _stable_file_identity(after_fd) != _stable_file_identity(after_path)
    ):
        raise RuntimeError(f"file changed while read: {path.name}")
    return data


def _strict_json_object(data: bytes, *, label: str) -> Dict[str, Any]:
    """Parse one duplicate-free, finite JSON object."""

    def reject_constant(value: str) -> None:
        raise ValueError(f"{label} contains non-finite number {value}")

    def pairs(items: List[Tuple[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"{label} contains duplicate key {key!r}")
            result[key] = value
        return result

    value = json.loads(
        data.decode("utf-8"),
        object_pairs_hook=pairs,
        parse_float=Decimal,
        parse_constant=reject_constant,
    )
    if not isinstance(value, dict):
        raise ValueError(f"{label} root is not an object")
    return value


def _control_boolean(value: Any) -> bool:
    """Return one already-validated runtime-control boolean."""

    if isinstance(value, bool):
        return value
    return isinstance(value, str) and value.strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _control_epoch(value: Any) -> int:
    """Parse the documented runtime-control epoch/date representations."""

    if isinstance(value, bool) or value is None:
        raise ValueError("control time must not be boolean or null")
    if type(value) is int:
        epoch = value
    elif type(value) is Decimal:
        if not value.is_finite() or value != value.to_integral_value():
            raise ValueError("control time exact number must be finite and integral")
        epoch = int(value)
    elif type(value) is float and math.isfinite(value) and value.is_integer():
        epoch = int(value)
    elif type(value) is str and value.strip() and not value.strip().isdigit():
        text = value.strip()
        parsed: Optional[datetime] = None
        for fmt in (
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%dT%H:%M:%S",
            "%Y-%m-%dT%H:%M",
        ):
            try:
                parsed = datetime.strptime(text, fmt)
                break
            except ValueError:
                pass
        if parsed is None:
            parsed = datetime.fromisoformat(text)
        epoch = int(parsed.timestamp())
    else:
        raise ValueError("control time has an unsupported representation")
    if epoch < 0 or epoch > 4_102_444_800:
        raise ValueError("control time is outside the supported range")
    return epoch


def runtime_control_snapshot(project_dir: Path) -> Dict[str, Any]:
    """Return a strict, read-only view of current operator pause controls."""

    path = Path(project_dir) / "mrsMThatcher.control.json"
    try:
        data = read_stable_regular_bytes(
            path,
            maximum=64 * 1024,
        )
    except FileNotFoundError:
        return {
            "present": False,
            "valid": True,
            "generation": None,
            "active_keys": [],
            "global_pause_active": False,
        }
    except Exception as exc:
        return {
            "present": True,
            "valid": False,
            "generation": None,
            "active_keys": ["fail_closed_invalid_control"],
            "global_pause_active": True,
            "reason": f"{type(exc).__name__}: {exc}",
        }
    try:
        value = _strict_json_object(data, label="runtime control")
        unsupported = sorted(set(value) - REMOTE_WRITE_CONTROL_ALLOWED_KEYS)
        if unsupported:
            raise ValueError(
                "unsupported control key(s): " + ", ".join(unsupported)
            )
        generation = value.get("generation")
        if generation is not None and (
            type(generation) is not int or generation < 0
        ):
            raise ValueError("generation must be a non-negative integer")
        now_epoch = int(datetime.now().timestamp())
        active: List[str] = []
        for key in sorted(REMOTE_WRITE_CONTROL_BOOLEAN_KEYS):
            if key not in value:
                continue
            raw = value[key]
            if not isinstance(raw, bool) and not (
                isinstance(raw, str)
                and raw.strip().lower()
                in {"1", "true", "yes", "on", "0", "false", "no", "off"}
            ):
                raise ValueError(f"{key} must be a boolean")
            if _control_boolean(raw):
                active.append(key)
        for key in sorted(REMOTE_WRITE_CONTROL_TIME_KEYS):
            if key in value and _control_epoch(value[key]) > now_epoch:
                active.append(key)
        return {
            "present": True,
            "valid": True,
            "generation": generation,
            "active_keys": active,
            "global_pause_active": bool(
                {"disable_all", "pause_all"} & set(active)
                or {
                    "disable_all_until",
                    "pause_all_until",
                }
                & set(active)
            ),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
    except Exception as exc:
        return {
            "present": True,
            "valid": False,
            "generation": None,
            "active_keys": ["fail_closed_invalid_control"],
            "global_pause_active": True,
            "sha256": hashlib.sha256(data).hexdigest(),
            "reason": f"{type(exc).__name__}: {exc}",
        }


def _safe_relative_project_path(project_dir: Path, value: Any) -> Optional[Path]:
    """Resolve one lexical direct-project relative path without escaping."""

    if not isinstance(value, str) or not value:
        return None
    relative = Path(value)
    if relative.is_absolute() or ".." in relative.parts:
        return None
    return Path(project_dir) / relative


def _read_readonly_archive_bytes(project_dir: Path, value: Any) -> bytes:
    """Read one direct project-relative, mode-0400 archive file."""

    path = _safe_relative_project_path(project_dir, value)
    if path is None:
        raise ValueError("archive path is not a safe project-relative path")
    if path.parent != Path(project_dir) / REMOTE_WRITE_ARCHIVE_BASENAME:
        raise ValueError("archive path is not a direct reconciliation-archive child")
    metadata = os.lstat(path)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or stat.S_IMODE(metadata.st_mode) != 0o400
    ):
        raise ValueError("archive is not a mode-0400 regular file")
    return read_stable_regular_bytes(
        path,
        maximum=REMOTE_WRITE_SNAPSHOT_MAX_BYTES,
    )


def reconciliation_archive_snapshot(project_dir: Path) -> Dict[str, Any]:
    """Validate durable marker/media audit receipts without changing them."""

    project_dir = Path(project_dir)
    archive = project_dir / REMOTE_WRITE_ARCHIVE_BASENAME
    try:
        metadata = os.lstat(archive)
    except FileNotFoundError:
        return {
            "present": False,
            "valid_marker_reconciliation_count": 0,
            "valid_media_reconciliation_count": 0,
            "marker_reconciliations": [],
            "media_reconciliations": [],
        }
    if not stat.S_ISDIR(metadata.st_mode):
        return {
            "present": True,
            "valid": False,
            "reason": "archive path is not a directory",
            "valid_marker_reconciliation_count": 0,
            "valid_media_reconciliation_count": 0,
            "marker_reconciliations": [],
            "media_reconciliations": [],
        }
    valid_marker: List[Dict[str, Any]] = []
    valid_media: List[Dict[str, Any]] = []
    invalid_audits: List[str] = []
    try:
        candidates = sorted(archive.glob("*.reconciliation.json"))
    except OSError as exc:
        return {
            "present": True,
            "valid": False,
            "reason": f"archive listing failed: {type(exc).__name__}",
            "valid_marker_reconciliation_count": 0,
            "valid_media_reconciliation_count": 0,
            "marker_reconciliations": [],
            "media_reconciliations": [],
        }
    for path in candidates:
        try:
            audit_metadata = os.lstat(path)
            if (
                not stat.S_ISREG(audit_metadata.st_mode)
                or stat.S_IMODE(audit_metadata.st_mode) != 0o400
            ):
                raise ValueError("audit is not a read-only regular file")
            data = read_stable_regular_bytes(
                path,
                maximum=REMOTE_WRITE_SNAPSHOT_MAX_BYTES,
            )
            value = _strict_json_object(data, label=path.name)
            operation = value.get("operation")
            if operation == "offline_remote_write_safety_marker_archive":
                expected_hash = value.get("marker_sha256")
                archived_at_epoch = value.get("archived_at_epoch")
                reconciliation_reference = value.get("reconciliation_reference")
                if (
                    value.get("schema_version") != 3
                    or type(archived_at_epoch) is not int
                    or archived_at_epoch < 0
                    or not isinstance(reconciliation_reference, str)
                    or not reconciliation_reference.strip()
                    or len(reconciliation_reference) > 512
                    or not isinstance(expected_hash, str)
                    or re.fullmatch(r"[0-9a-f]{64}", expected_hash) is None
                    or value.get("archive_and_receipt_durable_before_source_removal")
                    is not True
                    or value.get("restart_barrier_retired_last") is not True
                    or value.get("successful_return_requires_source_absent") is not True
                    or value.get(
                        "successful_return_requires_all_active_barriers_absent"
                    )
                    is not True
                    or hashlib.sha256(
                        _read_readonly_archive_bytes(
                            project_dir,
                            value.get("archive_path"),
                        )
                    ).hexdigest()
                    != expected_hash
                ):
                    raise ValueError("marker audit/archive binding is invalid")
                valid_marker.append(
                    {
                        "audit_path": str(path.relative_to(project_dir)),
                        "audit_sha256": hashlib.sha256(data).hexdigest(),
                        "archived_at_epoch": archived_at_epoch,
                        "marker_sha256": expected_hash,
                        "reconciliation_reference": reconciliation_reference,
                    }
                )
            elif operation == "offline_unattached_media_upload_archive":
                receipt_hash = value.get("receipt_sha256")
                fence_hash = value.get("fence_sha256")
                marker_hash = value.get("marker_sha256")
                transaction_id = value.get("media_transaction_id")
                archived_at_epoch = value.get("archived_at_epoch")
                image_basename = value.get("image_basename")
                if (
                    value.get("schema_version") != 1
                    or type(archived_at_epoch) is not int
                    or archived_at_epoch < 0
                    or not isinstance(transaction_id, str)
                    or re.fullmatch(r"[0-9a-f]{64}", transaction_id) is None
                    or not isinstance(receipt_hash, str)
                    or not isinstance(fence_hash, str)
                    or re.fullmatch(r"[0-9a-f]{64}", receipt_hash) is None
                    or re.fullmatch(r"[0-9a-f]{64}", fence_hash) is None
                    or not isinstance(marker_hash, str)
                    or re.fullmatch(r"[0-9a-f]{64}", marker_hash) is None
                    or not isinstance(image_basename, str)
                    or not image_basename
                    or Path(image_basename).name != image_basename
                    or value.get("accepted_media_disposition")
                    != "unattached_and_abandoned"
                    or value.get("no_tweet_create_authority_present") is not True
                    or value.get("operator_confirmed_no_tweet_create_attempted")
                    is not True
                    or value.get("operator_confirmed_unattached_media_abandoned")
                    is not True
                    or value.get("remote_media_id_absent") is not True
                    or value.get(
                        "media_archives_and_audit_durable_before_active_removal"
                    )
                    is not True
                    or value.get("media_receipt_retired_before_fence") is not True
                    or value.get("active_marker_preserved_after_media_reconciliation")
                    is not True
                    or value.get("successful_return_requires_active_media_pair_absent")
                    is not True
                    or hashlib.sha256(
                        _read_readonly_archive_bytes(
                            project_dir,
                            value.get("receipt_archive_path"),
                        )
                    ).hexdigest()
                    != receipt_hash
                    or hashlib.sha256(
                        _read_readonly_archive_bytes(
                            project_dir,
                            value.get("fence_archive_path"),
                        )
                    ).hexdigest()
                    != fence_hash
                ):
                    raise ValueError("media audit/archive binding is invalid")
                valid_media.append(
                    {
                        "audit_path": str(path.relative_to(project_dir)),
                        "audit_sha256": hashlib.sha256(data).hexdigest(),
                        "archived_at_epoch": archived_at_epoch,
                        "transaction_id": transaction_id,
                        "marker_sha256": marker_hash,
                        "image_basename": image_basename,
                        "accepted_media_disposition": value.get(
                            "accepted_media_disposition"
                        ),
                    }
                )
            else:
                continue
        except Exception as exc:
            invalid_audits.append(f"{path.name}: {type(exc).__name__}: {exc}")
    newest = lambda rows: max(
        rows,
        key=lambda item: (
            int(item.get("archived_at_epoch") or -1),
            str(item.get("audit_path") or ""),
        ),
        default=None,
    )
    return {
        "present": True,
        "valid": not invalid_audits,
        "valid_marker_reconciliation_count": len(valid_marker),
        "valid_media_reconciliation_count": len(valid_media),
        "marker_reconciliations": sorted(
            valid_marker,
            key=lambda item: (
                int(item.get("archived_at_epoch") or -1),
                str(item.get("audit_path") or ""),
            ),
        ),
        "media_reconciliations": sorted(
            valid_media,
            key=lambda item: (
                int(item.get("archived_at_epoch") or -1),
                str(item.get("audit_path") or ""),
            ),
        ),
        "latest_marker_reconciliation": newest(valid_marker),
        "latest_media_reconciliation": newest(valid_media),
        "invalid_audits": invalid_audits,
    }


def remote_write_safety_snapshot(project_dir: Path) -> Dict[str, Any]:
    """Inspect every current v2 remote-write barrier without mutating state."""

    project_dir = Path(project_dir)
    control = runtime_control_snapshot(project_dir)
    archive = reconciliation_archive_snapshot(project_dir)
    configured_names = [
        ".mrs_remote_write_safety_protocol_v2",
        ".mrs_remote_write_safety_protocol_v2.activation_audit.json",
        *REMOTE_WRITE_MARKER_BASENAMES,
        *REMOTE_WRITE_SOURCE_RECEIPT_BASENAMES,
        REMOTE_MEDIA_RECEIPT_BASENAME,
        REMOTE_MEDIA_FENCE_BASENAME,
        REMOTE_TRANSPORT_JOURNAL_BASENAME,
        REMOTE_TRANSPORT_FENCE_BASENAME,
        *(f".{name}.retirement.ledger.json" for name in REMOTE_WRITE_SOURCE_RECEIPT_BASENAMES),
    ]
    configured = control.get("present") is True or archive.get("present") is True
    for name in configured_names:
        try:
            os.lstat(project_dir / name)
        except FileNotFoundError:
            continue
        except OSError:
            configured = True
            break
        else:
            configured = True
            break
    if not configured:
        return {
            "configured": False,
            "available": False,
            "status": "not_configured",
            "blocking": False,
            "control": control,
            "reconciliation_archive": archive,
        }

    active_entries: List[Dict[str, Any]] = []

    def observe_name(name: str, kind: str) -> None:
        path = project_dir / name
        try:
            metadata = os.lstat(path)
        except FileNotFoundError:
            return
        except OSError as exc:
            active_entries.append(
                {
                    "name": name,
                    "kind": kind,
                    "safe_regular": False,
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            )
            return
        active_entries.append(
            {
                "name": name,
                "kind": kind,
                "safe_regular": stat.S_ISREG(metadata.st_mode),
                "mode": oct(stat.S_IMODE(metadata.st_mode)),
                "size": int(metadata.st_size),
            }
        )

    for name in REMOTE_WRITE_MARKER_BASENAMES:
        observe_name(name, "ambiguity_marker")
    for name in REMOTE_WRITE_SOURCE_RECEIPT_BASENAMES:
        observe_name(name, "source_receipt")

    protocol: Dict[str, Any]
    try:
        from remote_write_safety_protocol import (
            ACTIVATION_BASENAME,
            inspect_protocol_activation,
        )

        activation = inspect_protocol_activation(project_dir / ACTIVATION_BASENAME)
        protocol = {
            "valid": True,
            "status": "active_v2",
            "activation_kind": activation.activation_kind,
            "sha256": activation.sha256,
            "audit_sha256": activation.audit_sha256,
        }
    except Exception as exc:
        protocol = {
            "valid": False,
            "status": "invalid_or_missing",
            "reason": f"{type(exc).__name__}: {exc}",
        }

    ledger_rows: List[Dict[str, Any]] = []
    retirement_auxiliaries: List[str] = []
    try:
        from exact_receipt_retirement import (
            inspect_retirement_ledger,
            retirement_auxiliary_paths,
        )

        for name in REMOTE_WRITE_SOURCE_RECEIPT_BASENAMES:
            source = project_dir / name
            inspection = inspect_retirement_ledger(source)
            ledger_rows.append(
                {
                    "source": name,
                    "valid": inspection.valid,
                    "blocking": inspection.blocking,
                    "state": inspection.state,
                    "sequence": inspection.sequence,
                    "record_sha256": inspection.record_sha256,
                    "detail": inspection.detail,
                }
            )
            for path in retirement_auxiliary_paths(source):
                try:
                    os.lstat(path)
                except FileNotFoundError:
                    continue
                retirement_auxiliaries.append(path.name)
    except Exception as exc:
        ledger_rows.append(
            {
                "source": "inspection",
                "valid": False,
                "blocking": True,
                "state": "unavailable",
                "sequence": -1,
                "record_sha256": "",
                "detail": f"{type(exc).__name__}: {exc}",
            }
        )
    for name in retirement_auxiliaries:
        observe_name(name, "receipt_retirement_auxiliary")

    try:
        from remote_write_transport_journal import inspect_transport_state

        transport_state = inspect_transport_state(
            project_dir / REMOTE_TRANSPORT_JOURNAL_BASENAME
        )
        transport = {
            "classification": transport_state.classification,
            "blocking": transport_state.blocking,
            "staging_names": list(transport_state.staging_names),
            "retirement_guard_names": list(
                transport_state.retirement_guard_names
            ),
            "errors": list(transport_state.errors),
        }
    except Exception as exc:
        transport = {
            "classification": "inspection_unavailable",
            "blocking": True,
            "staging_names": [],
            "retirement_guard_names": [],
            "errors": [f"{type(exc).__name__}: {exc}"],
        }

    media: Dict[str, Any]
    try:
        from remote_media_upload_receipt import (
            inspect_media_upload_receipt,
            media_upload_receipt_is_blocking,
        )

        media_path = project_dir / REMOTE_MEDIA_RECEIPT_BASENAME
        media_receipt = inspect_media_upload_receipt(media_path)
        media_blocking = media_upload_receipt_is_blocking(media_path)
        media = {
            "classification": (
                str(media_receipt.document.get("lifecycle_state") or "present")
                if media_receipt is not None
                else "blocking_companion_or_transition"
                if media_blocking
                else "clear"
            ),
            "blocking": media_blocking,
            "transaction_id": (
                media_receipt.document.get("transaction_id")
                if media_receipt is not None
                else None
            ),
            "lane": (
                media_receipt.document.get("lane")
                if media_receipt is not None
                else None
            ),
            "remote_media_id_present": bool(
                media_receipt is not None
                and media_receipt.document.get("remote_media_id")
            ),
        }
    except Exception as exc:
        media = {
            "classification": "invalid",
            "blocking": True,
            "transaction_id": None,
            "lane": None,
            "remote_media_id_present": False,
            "reason": f"{type(exc).__name__}: {exc}",
        }

    ledger_blocking = any(
        row.get("valid") is not True or row.get("blocking") is True
        for row in ledger_rows
    )
    blocking = bool(
        active_entries
        or protocol.get("valid") is not True
        or ledger_blocking
        or transport.get("blocking") is True
        or media.get("blocking") is True
    )
    global_pause = control.get("global_pause_active") is True
    control_invalid = control.get("valid") is not True
    status = (
        "blocked"
        if blocking
        else "paused_fail_closed_control"
        if control_invalid
        else "operator_paused"
        if global_pause
        else "ready"
    )
    active_marker_names = [
        item["name"]
        for item in active_entries
        if item.get("kind") == "ambiguity_marker"
    ]
    marker_reconciliation = archive.get("latest_marker_reconciliation") or {}
    media_reconciliation = archive.get("latest_media_reconciliation") or {}
    archive_valid = archive.get("valid") is True
    media_reconciliation_proven = bool(
        archive_valid
        and media.get("blocking") is False
        and media_reconciliation
        and (
            not marker_reconciliation
            or media_reconciliation.get("marker_sha256")
            == marker_reconciliation.get("marker_sha256")
        )
    )
    reconciliation_reference = marker_reconciliation.get(
        "reconciliation_reference"
    )
    media_reference_matches = bool(
        not isinstance(reconciliation_reference, str)
        or not reconciliation_reference.startswith(
            REMOTE_WRITE_ARCHIVE_BASENAME + "/unattached_media_upload."
        )
        or reconciliation_reference == media_reconciliation.get("audit_path")
    )
    return {
        "configured": True,
        "available": True,
        "observed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "status": status,
        "blocking": blocking,
        "ready_for_remote_writes": not blocking and not global_pause and not control_invalid,
        "active_entries": active_entries,
        "active_marker_names": active_marker_names,
        "protocol": protocol,
        "retirement_ledgers": ledger_rows,
        "transport": transport,
        "media": media,
        "control": control,
        "reconciliation_archive": archive,
        "reconciliation_proven": bool(
            archive_valid
            and not active_marker_names
            and marker_reconciliation
            and media_reference_matches
        ),
        "media_reconciliation_proven": media_reconciliation_proven,
    }


def configured_quote_image_semantic_veto_snapshot(project_dir: Path) -> Dict[str, Any]:
    """Validate the configured semantic-veto manifest without changing runtime state."""
    config_path = project_dir / "mrsMThatcher.local.json"
    try:
        local_config = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(local_config, dict):
            raise ValueError("local config root is not an object")
    except FileNotFoundError:
        return {"present": False}
    except Exception as exc:
        return {
            "present": True,
            "available": False,
            "status": "config_unavailable",
            "reason": f"local config unavailable: {type(exc).__name__}",
        }

    config = local_config.get("quote_image_semantic_veto")
    if not isinstance(config, dict):
        return {"present": False}
    result: Dict[str, Any] = {
        "present": True,
        "available": False,
        "enabled": config.get("enabled") is True,
        "mode": str(config.get("mode") or "unavailable"),
        "status": "disabled",
        "reason": "",
    }
    if config.get("enabled") is not True:
        result["reason"] = "semantic-veto shadow is disabled in local config"
        return result

    configured_path = Path(str(config.get("manifest_path") or ""))
    if not configured_path.is_absolute():
        configured_path = project_dir / configured_path
    try:
        from semantic_alignment.quote_image_semantic_veto import (
            manifest_source_hash_mismatches,
            validate_compiled_manifest,
            validate_shadow_config,
        )

        config_errors = validate_shadow_config(config)
        if config_errors:
            raise ValueError("; ".join(config_errors))
        manifest = json.loads(configured_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict):
            raise ValueError("manifest root is not an object")
        audit = validate_compiled_manifest(manifest)
        stale = manifest_source_hash_mismatches(project_dir, manifest)
        manifest_hash = file_sha256(configured_path)
        quote_count = audit.get("quote_count", manifest.get("quote_count"))
        image_count = audit.get("image_count", manifest.get("image_count"))
        total_authorised = manifest.get("total_authorised_pair_count")
        if type(total_authorised) is not int and type(quote_count) is int and type(image_count) is int:
            total_authorised = quote_count * image_count
        coverage = manifest.get("quote_pair_coverage")
        quote_text = manifest.get("quote_text")
        fully_unadjudicated: List[Dict[str, Any]] = []
        named_coverage: Optional[Dict[str, Any]] = None
        if isinstance(coverage, dict):
            for quote_id, row in sorted(coverage.items()):
                if not isinstance(row, dict):
                    continue
                authorised = row.get("authorised_image_count")
                not_adjudicated = row.get("not_adjudicated_count")
                if (
                    type(authorised) is int
                    and authorised > 0
                    and not_adjudicated == authorised
                ):
                    fully_unadjudicated.append(
                        {
                            "quote_id": str(quote_id),
                            "quote_preview": short(
                                quote_text.get(quote_id, "")
                                if isinstance(quote_text, dict)
                                else "",
                                120,
                            ),
                            "authorised_image_count": authorised,
                            "resolved_pair_count": int(row.get("resolved_pair_count", 0) or 0),
                            "adjudicated_unknown_count": int(
                                row.get("adjudicated_unknown_count", 0) or 0
                            ),
                            "not_adjudicated_count": not_adjudicated,
                        }
                    )
                if str(quote_id) == SEMANTIC_VETO_NAMED_COVERAGE_QUOTE_ID:
                    named_coverage = {
                        "quote_id": str(quote_id),
                        "authorised_image_count": int(authorised or 0),
                        "allow_count": int(row.get("allow_count", 0) or 0),
                        "veto_count": int(row.get("veto_count", 0) or 0),
                        "adjudicated_unknown_count": int(
                            row.get("adjudicated_unknown_count", 0) or 0
                        ),
                        "not_adjudicated_count": int(not_adjudicated or 0),
                        "complete": row.get("complete_pair_coverage") is True
                        and int(not_adjudicated or 0) == 0,
                    }
        allow_count = manifest.get("allow_count")
        veto_count = manifest.get("veto_count")
        unknown_count = manifest.get("adjudicated_unknown_pair_count")
        not_adjudicated_count = manifest.get("not_adjudicated_pair_count")
        if not all(type(value) is int for value in (
            allow_count, veto_count, unknown_count, not_adjudicated_count
        )):
            raise ValueError("manifest does not expose separate allow/veto/unknown/not-adjudicated counts")
        if type(total_authorised) is int and (
            allow_count + veto_count + unknown_count + not_adjudicated_count
            != total_authorised
        ):
            raise ValueError("manifest pair-state counts do not equal authorised pair universe")
        result.update(
            {
                "manifest_path": str(configured_path),
                "manifest_policy_version": str(manifest.get("policy_version") or "unavailable"),
                "manifest_sha256": manifest_hash,
                "quote_count": quote_count,
                "image_count": image_count,
                "pair_count": audit.get("pair_count", manifest.get("pair_count")),
                "total_authorised_pair_count": total_authorised,
                "resolved_pair_count": manifest.get(
                    "resolved_pair_count",
                    audit.get("pair_count", manifest.get("pair_count")),
                ),
                "allow_pair_count": allow_count,
                "veto_pair_count": veto_count,
                "adjudicated_unknown_pair_count": manifest.get(
                    "adjudicated_unknown_pair_count",
                    audit.get("adjudicated_unknown_pair_count", 0),
                ),
                "not_adjudicated_pair_count": manifest.get(
                    "not_adjudicated_pair_count",
                    audit.get("not_adjudicated_pair_count", 0),
                ),
                "fully_unadjudicated_quote_count": len(fully_unadjudicated),
                "fully_unadjudicated_quotes": fully_unadjudicated,
                "named_quote_coverage": named_coverage,
            }
        )
        if stale:
            result.update(
                {
                    "status": "manifest_stale",
                    "reason": f"source hash mismatch: {Path(stale[0]).name}",
                }
            )
            return result
        result.update({"available": True, "status": "loaded"})
        return result
    except FileNotFoundError:
        result.update(
            {
                "status": "manifest_unavailable",
                "reason": f"configured manifest is missing: {configured_path.name}",
            }
        )
    except Exception as exc:
        result.update(
            {
                "status": "manifest_invalid",
                "reason": f"configured manifest invalid: {type(exc).__name__}: {exc}",
            }
        )
    return result


def _add_configured_veto_health(
    runtime: Dict[str, Any],
    configured: Dict[str, Any],
) -> Dict[str, Any]:
    """Attach current configured-manifest health to a retained runtime snapshot."""
    if not configured.get("present"):
        return runtime
    result = dict(runtime)
    result.update(
        {
            "configured_manifest_present": True,
            "configured_manifest_available": configured.get("available") is True,
            "configured_manifest_enabled": configured.get("enabled"),
            "configured_manifest_mode": configured.get("mode"),
            "configured_manifest_status": configured.get("status"),
            "configured_manifest_reason": configured.get("reason") or "",
            "configured_manifest_path": configured.get("manifest_path"),
            "configured_manifest_policy_version": configured.get("manifest_policy_version"),
            "configured_manifest_sha256": configured.get("manifest_sha256"),
            "configured_manifest_quote_count": configured.get("quote_count"),
            "configured_manifest_image_count": configured.get("image_count"),
            "configured_manifest_pair_count": configured.get("pair_count"),
            "configured_manifest_total_authorised_pair_count": configured.get(
                "total_authorised_pair_count"
            ),
            "configured_manifest_resolved_pair_count": configured.get(
                "resolved_pair_count"
            ),
            "configured_manifest_allow_pair_count": configured.get(
                "allow_pair_count"
            ),
            "configured_manifest_veto_pair_count": configured.get(
                "veto_pair_count"
            ),
            "configured_manifest_adjudicated_unknown_pair_count": configured.get(
                "adjudicated_unknown_pair_count"
            ),
            "configured_manifest_not_adjudicated_pair_count": configured.get(
                "not_adjudicated_pair_count"
            ),
            "configured_manifest_fully_unadjudicated_quote_count": configured.get(
                "fully_unadjudicated_quote_count"
            ),
            "configured_manifest_fully_unadjudicated_quotes": configured.get(
                "fully_unadjudicated_quotes"
            )
            or [],
            "configured_manifest_named_quote_coverage": configured.get(
                "named_quote_coverage"
            ),
        }
    )
    if runtime.get("available") and configured.get("available"):
        result["runtime_status_matches_configured_manifest"] = bool(
            runtime.get("manifest_policy_version")
            == configured.get("manifest_policy_version")
            and runtime.get("manifest_sha256") == configured.get("manifest_sha256")
        )
    else:
        result["runtime_status_matches_configured_manifest"] = None
    return result


def quote_image_semantic_veto_shadow_snapshot(project_dir: Path) -> Dict[str, Any]:
    """Read the local material-veto shadow status without any provider access."""
    configured = configured_quote_image_semantic_veto_snapshot(project_dir)
    path = project_dir / "quote_image_semantic_veto_runtime" / "shadow_status.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, dict):
            raise ValueError("status root is not an object")
        if "mixed_manifest_versions" not in value:
            history_path = path.with_name("shadow_history.jsonl")
            history_rows: List[Dict[str, Any]] = []
            if history_path.is_file():
                for line in history_path.read_text(encoding="utf-8", errors="replace").splitlines()[-10_000:]:
                    try:
                        row = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if isinstance(row, dict):
                        history_rows.append(row)
            if history_rows:
                reconstructed = quote_image_semantic_veto_summary(history_rows)
                value = {
                    **value,
                    "events": reconstructed.get("selection_time_observations", 0),
                    "allowed": reconstructed.get("allowed_production_winners", 0),
                    "vetoed": reconstructed.get("vetoed_production_winners", 0),
                    "unknown": reconstructed.get("unknown_unjudged", 0),
                    "generated_out_of_scope": reconstructed.get("generated_out_of_scope", 0),
                    "vetoed_with_alternative": reconstructed.get("vetoed_with_allowed_alternative", 0),
                    "vetoed_without_alternative": reconstructed.get("vetoed_without_allowed_alternative", 0),
                    "selection_error_candidate_available": reconstructed.get(
                        "selection_error_candidate_available", 0
                    ),
                    "coverage_gap_no_safe_image": reconstructed.get("coverage_gap_no_safe_image", 0),
                    "quotes_with_no_globally_allowed_candidate": reconstructed.get(
                        "quotes_with_no_globally_allowed_candidate", 0
                    ),
                    "veto_reason_counts": reconstructed.get("veto_reason_counts", {}),
                    "alternative_score_delta_median": reconstructed.get("median_alternative_score_delta"),
                    "manifest_policy_version": reconstructed.get("manifest_policy_version", "unavailable"),
                    "manifest_sha256": reconstructed.get("manifest_sha256", ""),
                    "production_selection_change_failures": reconstructed.get(
                        "production_selection_change_failures", 0
                    ),
                    "history_events_all_manifests": reconstructed.get("window_event_count_all_manifests", 0),
                    "events_excluded_from_current_manifest_summary": reconstructed.get(
                        "events_excluded_from_current_manifest_summary", 0
                    ),
                    "mixed_manifest_versions": reconstructed.get("mixed_manifest_versions", False),
                    "manifest_strata": reconstructed.get("manifest_strata", []),
                }
        counts = {
            key: int(value.get(key, 0) or 0)
            for key in (
                "events", "allowed", "vetoed", "unknown", "generated_out_of_scope",
                "manifest_unavailable", "manifest_stale", "vetoed_with_alternative",
                "vetoed_without_alternative", "coverage_gap_no_safe_image",
                "quotes_with_no_globally_allowed_candidate",
                "production_selection_change_failures",
            )
        }
        measurements = {
            key: None if value.get(key) is None else float(value[key])
            for key in (
                "alternative_score_delta_median", "lookup_latency_p50_ms",
                "lookup_latency_p95_ms", "lookup_latency_max_ms",
            )
        }
        selection_error = int(
            value.get("selection_error_candidate_available", value.get("vetoed_with_alternative", 0)) or 0
        )
        history_events_all_manifests = int(
            value.get("history_events_all_manifests", value.get("events", 0)) or 0
        )
        events_excluded = int(value.get("events_excluded_from_current_manifest_summary", 0) or 0)
        mixed_manifest_versions = value.get("mixed_manifest_versions", False)
        if type(mixed_manifest_versions) is not bool:
            raise ValueError("mixed_manifest_versions must be boolean")
        manifest_strata = value.get("manifest_strata", [])
        if not isinstance(manifest_strata, list) or not all(isinstance(item, dict) for item in manifest_strata):
            raise ValueError("manifest_strata must be a list of objects")
    except FileNotFoundError:
        return _add_configured_veto_health(
            {"available": False, "reason": "shadow mode disabled or no events observed"},
            configured,
        )
    except Exception as exc:
        return _add_configured_veto_health(
            {"available": False, "reason": f"shadow status unavailable: {type(exc).__name__}"},
            configured,
        )
    return _add_configured_veto_health({
        "available": True,
        **counts,
        **measurements,
        "selection_error_candidate_available": selection_error,
        "history_events_all_manifests": history_events_all_manifests,
        "events_excluded_from_current_manifest_summary": events_excluded,
        "mixed_manifest_versions": mixed_manifest_versions,
        "manifest_strata": manifest_strata,
        "veto_reason_counts": value.get("veto_reason_counts") if isinstance(value.get("veto_reason_counts"), dict) else {},
        "manifest_policy_version": str(value.get("manifest_policy_version") or "unavailable"),
        "manifest_sha256": str(value.get("manifest_sha256") or ""),
        "updated_at": value.get("updated_at"),
    }, configured)


def semantic_veto_load_lifecycle(records: List["Record"]) -> Dict[str, Any]:
    """Correlate semantic-veto startup warnings with later successful loads."""
    stale: List[Dict[str, Any]] = []
    loads: List[Dict[str, Any]] = []
    for record in records:
        message = record.msg
        if "Quote/image semantic-veto shadow unavailable" in message:
            stale.append({
                "time": record.ts.strftime("%Y-%m-%d %H:%M:%S"),
                "timestamp": record.ts,
                "message": message,
            })
        elif "Quote/image semantic-veto shadow manifest loaded" in message:
            policy_match = re.search(r"\bpolicy=(\S+)", message)
            hash_match = re.search(r"\bsha256=([0-9a-f]{64})", message)
            loads.append({
                "time": record.ts.strftime("%Y-%m-%d %H:%M:%S"),
                "timestamp": record.ts,
                "policy": policy_match.group(1) if policy_match else "unavailable",
                "manifest_sha256": hash_match.group(1) if hash_match else "",
            })
    resolved = []
    unresolved = []
    for warning in stale:
        later = next(
            (load for load in loads if load["timestamp"] > warning["timestamp"]),
            None,
        )
        public = {key: value for key, value in warning.items() if key != "timestamp"}
        if later:
            public["resolved_at"] = later["time"]
            public["resolved_by_manifest_sha256"] = later["manifest_sha256"]
            resolved.append(public)
        else:
            unresolved.append(public)
    return {
        "resolved_warning_count": len(resolved),
        "unresolved_warning_count": len(unresolved),
        "resolved_warnings": resolved,
        "unresolved_warnings": unresolved,
        "successful_loads": [
            {key: value for key, value in load.items() if key != "timestamp"}
            for load in loads
        ],
    }


def historical_context_corpus_snapshot(project_dir: Path) -> Dict[str, Any]:
    """Read current corpus, unresolved, eligibility, gate, and audit counts."""
    paths = {
        "research_packets": (
            project_dir
            / "semantic_alignment_research"
            / "quote_research_full_001"
            / "research_packets.json"
        ),
        "unresolved_cases": (
            project_dir
            / "semantic_alignment_research"
            / "quote_research_full_001"
            / "final_unresolved"
            / "unresolved_cases.json"
        ),
        "runtime_eligible_manifest": (
            project_dir
            / "semantic_alignment_research"
            / "quote_attribution_cleanup_001"
            / "deployment_candidate"
            / "runtime_eligible_quote_manifest.json"
        ),
        "source_role_audit": (
            project_dir
            / "semantic_alignment_research"
            / "quote_research_full_001"
            / "historical_context_source_role_audit.json"
        ),
        "semantic_gate_audit": project_dir / "historical_context_reply_semantic_gate_audit.json",
        "semantic_review_ledger": (
            project_dir / "historical_context_published_reply_semantic_review.json"
        ),
    }
    loaded: Dict[str, Dict[str, Any]] = {}
    hashes: Dict[str, str] = {}
    missing: List[str] = []
    malformed: List[str] = []
    for label, path in paths.items():
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("root is not an object")
            loaded[label] = value
            hashes[label] = file_sha256(path)
        except FileNotFoundError:
            missing.append(label)
        except Exception as exc:
            malformed.append(f"{label}:{type(exc).__name__}")

    packets = loaded.get("research_packets", {}).get("items")
    unresolved = loaded.get("unresolved_cases", {})
    eligible = loaded.get("runtime_eligible_manifest", {})
    role_audit = loaded.get("source_role_audit", {})
    gate_audit = loaded.get("semantic_gate_audit", {})
    gate = gate_audit.get("gate") if isinstance(gate_audit.get("gate"), dict) else {}
    coverage = (
        gate_audit.get("coverage")
        if isinstance(gate_audit.get("coverage"), dict)
        else {}
    )
    decision_counts = (
        gate_audit.get("decision_counts")
        if isinstance(gate_audit.get("decision_counts"), dict)
        else {}
    )
    packet_count = len(packets) if isinstance(packets, (dict, list)) else None
    unresolved_count = unresolved.get("case_count")
    if type(unresolved_count) is not int:
        cases = unresolved.get("cases")
        unresolved_count = len(cases) if isinstance(cases, list) else None
    ordinary_count = eligible.get("runtime_eligible_quote_count")
    if type(ordinary_count) is not int:
        ordinary_ids = eligible.get("runtime_eligible_quote_ids")
        ordinary_count = len(ordinary_ids) if isinstance(ordinary_ids, list) else None
    blocked_count = gate.get("blocked_quote_count")
    if type(blocked_count) is not int:
        blocked_count = decision_counts.get("blocked_open_semantic_review")

    required_counts = (packet_count, unresolved_count, ordinary_count, blocked_count)
    return {
        "available": not missing and not malformed and all(type(value) is int for value in required_counts),
        "reason": (
            "; ".join(
                ([f"missing: {', '.join(missing)}"] if missing else [])
                + ([f"malformed: {', '.join(malformed)}"] if malformed else [])
            )
        ),
        "completed_packet_count": packet_count,
        "unresolved_quote_count": unresolved_count,
        "ordinary_post_cycle_count": ordinary_count,
        "attribution_eligible_count": coverage.get(
            "attribution_eligible_count",
            role_audit.get("attribution_eligible_quote_count"),
        ),
        "completed_attribution_ineligible_count": coverage.get(
            "completed_attribution_ineligible_count"
        ),
        "historical_context_blocked_count": blocked_count,
        "historical_context_allowed_count": decision_counts.get("eligible_allow"),
        "source_role_policy_version": role_audit.get("policy_version"),
        "semantic_gate_policy_version": gate_audit.get("policy_version"),
        "semantic_review_ledger_sha256": gate.get("semantic_review_ledger_sha256"),
        "semantic_gate_projection_sha256": gate.get("blocked_projection_sha256"),
        "file_sha256": dict(sorted(hashes.items())),
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
                transaction = json.loads(manifest_path.read_text(encoding="utf-8"))
                if not isinstance(transaction, dict): raise ValueError("manifest root is not an object")
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
                timestamp = datetime.fromisoformat(str(transaction.get("created_at") or "").replace("Z", "+00:00"))
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
    """Parse datetime."""
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
    raise ValueError(f"Could not parse datetime: {value!r}. Use e.g. '2026-06-25 08:00'.")


def dt_text(value: datetime) -> str:
    """Return the datetime text."""
    return value.strftime("%Y-%m-%d %H:%M:%S")


def format_rank(value: Any, *, mean: bool = False) -> str:
    """Format an ordinal rank without exposing floating-point noise."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return "n/a"
    number = float(value)
    if not math.isfinite(number):
        return "n/a"
    if mean:
        return f"{number:.2f}"
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def format_display_number(value: Any, *, decimal_places: int = 2) -> str:
    """Format a numeric display value without changing its stored representation."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    raw = str(value)
    try:
        number = float(raw)
    except (TypeError, ValueError):
        return raw
    if not math.isfinite(number):
        return raw
    rendered = f"{number:.{decimal_places}f}".rstrip("0").rstrip(".")
    return "0" if rendered in {"", "-0"} else rendered


def most_common_with_cutoff_ties(
    counts: Counter,
    *,
    limit: int = 8,
) -> List[Tuple[str, int]]:
    """Return a deterministic top list without dropping ties at the cut-off."""
    ordered = sorted(
        (
            (str(name), int(count))
            for name, count in counts.items()
            if str(name) and int(count) > 0
        ),
        key=lambda item: (-item[1], item[0]),
    )
    if len(ordered) <= limit:
        return ordered
    cutoff = ordered[limit - 1][1]
    return [item for item in ordered if item[1] >= cutoff]


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
    merge_existing_boundary_occurrences: bool = False,
    cursor_fingerprint_tail: Optional[List[str]] = None,
) -> None:
    """Save resume time."""
    old = read_resume_data(state_file) if preserve_existing_context else {}

    latest_state = dict(report.get("latest_state") or {})
    latest_config = dict(report.get("latest_config") or {})
    runtime_state_status = str(
        (report.get("runtime_state_status") or {}).get("status") or ""
    )
    runtime_config_status = str(
        (report.get("runtime_config_status") or {}).get("status") or ""
    )
    if (
        preserve_existing_context
        and runtime_state_status
        and runtime_state_status != "available"
    ):
        retained_state = report.get("historical_retained_state") or old.get(
            "last_known_latest_state"
        )
        if isinstance(retained_state, dict):
            latest_state = dict(retained_state)
    if (
        preserve_existing_context
        and runtime_config_status
        and runtime_config_status != "available"
    ):
        retained_config = report.get("historical_retained_config") or old.get(
            "last_known_latest_config"
        )
        if isinstance(retained_config, dict):
            latest_config = dict(retained_config)

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
    boundary_fingerprint_counts = Counter(
        record_fingerprint(record)
        for record in records
        if record.ts == last_ts
    )
    try:
        old_last_ts = parse_dt(old.get("last_log_entry_time"))
    except Exception:
        old_last_ts = None
    if merge_existing_boundary_occurrences and old_last_ts == last_ts:
        boundary_fingerprint_counts.update(resume_boundary_fingerprint_counts(old))
    if cursor_fingerprint_tail is None:
        cursor_fingerprint_tail = [
            *resume_fingerprint_tail(old),
            *(record_fingerprint(record) for record in records),
        ]
    cursor_fingerprint_tail = cursor_fingerprint_tail[-RESUME_FINGERPRINT_TAIL_LIMIT:]

    data = {
        "resume_cursor_schema_version": 1,
        "last_log_entry_time": dt_text(last_ts),
        "last_log_entry_fingerprints": sorted(boundary_fingerprint_counts),
        "last_log_entry_fingerprint_counts": dict(sorted(boundary_fingerprint_counts.items())),
        "last_log_entry_fingerprint_tail": cursor_fingerprint_tail,
        "last_run_record_count": report.get("summary", {}).get("record_count"),
        "last_run_time_start": report.get("summary", {}).get("time_start"),
        "last_run_time_end": report.get("summary", {}).get("time_end"),
        "last_run_logs": [str(p) for p in logs],
        "last_known_latest_state": latest_state_clean,
        "last_known_latest_config": latest_config_clean,
        "last_known_generated_image_spacing": latest_generated_image_spacing,
        "last_active_xai_context": report.get("resume_context", {}).get("active_xai_context"),
        "last_active_xai_call_attempt": report.get("resume_context", {}).get(
            "active_xai_call_attempt"
        ),
        "last_pending_mention": report.get("resume_context", {}).get("pending_mention"),
        "last_pending_qt": report.get("resume_context", {}).get("pending_qt"),
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    tmp = state_file.with_suffix(state_file.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(state_file)


def discover_logs(directory: Path, pattern: str) -> List[Path]:
    """Resolve eligible production log files without including test fixtures."""
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
    """Return the epoch to human."""
    try:
        n = int(value)
    except Exception:
        return None
    if n <= 0:
        return None
    return datetime.fromtimestamp(n).strftime("%Y-%m-%d %H:%M:%S")


def int_or_none(value: Any) -> Optional[int]:
    """Return the int or none."""
    try:
        if value is None:
            return None
        return int(str(value).strip())
    except Exception:
        return None


def cooldown_state_text(until_epoch: Any, generation_epoch: Any) -> str:
    """Return cooldown state at digest-generation time."""
    until = int_or_none(until_epoch)
    generated = int_or_none(generation_epoch)
    if generated is None:
        return ""
    if until is None or until < 0:
        return "unavailable"
    if until == 0:
        return "cleared"
    return "active" if generated < until else "expired"


@dataclass(frozen=True)
class Record:
    """Represent record data."""
    ts: datetime
    level: str
    src: str
    line: int
    msg: str
    path: str
    ordinal: int


def record_fingerprint(record: Record) -> str:
    """Record fingerprint."""
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


def resume_fingerprint_tail(data: Dict[str, Any]) -> List[str]:
    """Return the resume fingerprint tail."""
    raw = data.get("last_log_entry_fingerprint_tail")
    if not isinstance(raw, list):
        return []
    return [
        value
        for value in raw[-RESUME_FINGERPRINT_TAIL_LIMIT:]
        if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value)
    ]


def locate_resume_fingerprint_tail(records: List[Record], tail: List[str]) -> Optional[Tuple[int, int]]:
    """Locate the saved append-order tail, tolerating bounded rotation loss."""
    if not records or not tail:
        return None
    fingerprints = [record_fingerprint(record) for record in records]
    minimum = min(8, len(tail))
    for length in range(len(tail), minimum - 1, -1):
        needle = tail[-length:]
        limit = len(fingerprints) - length + 1
        for start in range(max(0, limit)):
            if fingerprints[start:start + length] == needle:
                return start + length, length
    return None


def resume_boundary_fingerprint_counts(data: Dict[str, Any]) -> Counter[str]:
    """Return the resume boundary fingerprint counts."""
    raw_counts = data.get("last_log_entry_fingerprint_counts")
    counts: Counter[str] = Counter()
    if isinstance(raw_counts, dict):
        for fingerprint, raw_count in raw_counts.items():
            if not fingerprint or isinstance(raw_count, bool):
                continue
            try:
                count = int(raw_count)
            except (TypeError, ValueError, OverflowError):
                continue
            if count > 0:
                counts[str(fingerprint)] = count
        return counts
    for fingerprint in data.get("last_log_entry_fingerprints", []):
        if fingerprint:
            counts[str(fingerprint)] += 1
    return counts


def filter_resume_boundary_records(
    records: List[Record],
    boundary: datetime,
    processed_counts: Counter[str],
) -> List[Record]:
    """Filter resume boundary records."""
    remaining = Counter(processed_counts)
    filtered: List[Record] = []
    for record in records:
        fingerprint = record_fingerprint(record)
        if record.ts == boundary and remaining[fingerprint] > 0:
            remaining[fingerprint] -= 1
            continue
        filtered.append(record)
    return filtered


def iter_records(path: Path) -> Iterable[Record]:
    """Yield iter records values."""
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
    physical_order: bool = False,
) -> List[Record]:
    """Read and deduplicate structured and legacy log records."""
    occurrences: Dict[tuple[Any, ...], Dict[str, List[Record]]] = {}
    path_priority = {str(path): index for index, path in enumerate(paths)}
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
            # Preserve repeated occurrences within a source. For overlapping
            # rotations, retain the greatest occurrence count seen in any one
            # source instead of collapsing the record globally.
            key = (r.ts, r.level, r.src, r.line, r.msg)
            occurrences.setdefault(key, {}).setdefault(str(path), []).append(r)
    out: List[Record] = []
    for by_path in occurrences.values():
        _selected_path, selected_records = min(
            by_path.items(),
            key=lambda item: (-len(item[1]), path_priority.get(item[0], len(paths))),
        )
        out.extend(selected_records)
    if physical_order:
        canonical = []
        for path in paths:
            match = re.fullmatch(r"(?P<base>.+\.log)(?:\.(?P<rotation>\d+))?", path.name)
            canonical.append((path, match))
        same_rotation_family = bool(canonical) and all(match for _path, match in canonical)
        if same_rotation_family:
            families = {(path.parent.resolve(), match.group("base")) for path, match in canonical if match}
            same_rotation_family = len(families) == 1
        if same_rotation_family:
            ordered_paths = sorted(
                (path for path, _match in canonical),
                key=lambda path: (
                    1 if re.fullmatch(r".+\.log", path.name) else 0,
                    -int(path.name.rsplit(".", 1)[1]) if path.name.rsplit(".", 1)[1].isdigit() else 0,
                ),
            )
        else:
            def physical_path_key(path: Path) -> Tuple[int, str]:
                try:
                    return path.stat().st_mtime_ns, str(path)
                except OSError:
                    return 0, str(path)

            ordered_paths = sorted(paths, key=physical_path_key)
        physical_priority = {str(path): index for index, path in enumerate(ordered_paths)}
        out.sort(key=lambda r: (physical_priority.get(r.path, len(paths)), r.ordinal, r.ts))
    else:
        out.sort(key=lambda r: (r.ts, r.path, r.ordinal))
    return out


def filter_records_by_time(
    records: List[Record],
    since: Optional[datetime],
    *,
    since_exclusive: bool,
) -> List[Record]:
    """Filter records by time."""
    if since is None:
        return list(records)
    if since_exclusive:
        return [record for record in records if record.ts > since]
    return [record for record in records if record.ts >= since]


def summarize_input_files(
    paths: List[Path],
    since: Optional[datetime],
    until: Optional[datetime],
    *,
    since_exclusive: bool = False,
) -> List[Dict[str, Any]]:
    """Summarise input files."""
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


def input_retention_coverage(
    input_files: List[Dict[str, Any]],
    since: Optional[datetime],
) -> Dict[str, Any]:
    """Describe whether retained records cover the requested lower boundary."""
    timestamps: List[datetime] = []
    for item in input_files:
        first = item.get("first_timestamp")
        if not first:
            continue
        try:
            parsed = parse_dt(str(first))
        except ValueError:
            continue
        if parsed is not None:
            timestamps.append(parsed)
    earliest = min(timestamps) if timestamps else None
    result: Dict[str, Any] = {
        "requested_since": dt_text(since) if since else None,
        "earliest_retained_timestamp": dt_text(earliest) if earliest else None,
        "requested_start_covered": None,
        "retention_gap_seconds": None,
        "warning": "",
    }
    if since is None or earliest is None:
        return result
    if earliest <= since:
        result["requested_start_covered"] = True
        return result
    gap = int((earliest - since).total_seconds())
    result.update(
        {
            "requested_start_covered": False,
            "retention_gap_seconds": gap,
            "warning": (
                f"requested window starts at {dt_text(since)}, but the earliest "
                f"retained timestamp is {dt_text(earliest)}; coverage of the "
                "preceding interval cannot be verified from retained logs"
            ),
        }
    )
    return result


def combine_input_warnings(*warnings: Optional[str]) -> Optional[str]:
    """Combine distinct non-empty input warnings deterministically."""
    values = list(dict.fromkeys(str(value) for value in warnings if value))
    return "; ".join(values) if values else None


def lit(value: str) -> str:
    """Parse a Python repr string when possible, otherwise return raw."""
    value = value.strip()
    try:
        return ast.literal_eval(value)
    except Exception:
        return value.strip("'\"")


def short(value: Any, n: int) -> str:
    """Return the short."""
    if value is None:
        return ""
    s = str(value).replace("\n", "\\n")
    if len(s) <= n:
        return s
    return s[: max(0, n - 1)] + "…"


UNKNOWN_MISSING_STATE_FIELD = "unknown (not present in latest snapshot)"
UNKNOWN_INVALID_STATE_FIELD = "unknown (invalid in latest snapshot)"


def state_list_count(state: Dict[str, Any], key: str) -> Any:
    """Return the state list count."""
    if key not in state:
        return UNKNOWN_MISSING_STATE_FIELD
    value = state.get(key)
    if isinstance(value, list):
        return len(value)
    return UNKNOWN_INVALID_STATE_FIELD


def state_list_tail(state: Dict[str, Any], key: str, count: int) -> Optional[List[Any]]:
    """Return the state list tail."""
    if key not in state:
        return None
    value = state.get(key)
    if isinstance(value, list):
        return value[-count:]
    return None


def state_list_head(state: Dict[str, Any], key: str, count: int) -> Optional[List[Any]]:
    """Return the state list head."""
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
    """Summarise latest state."""
    observed_epoch = int(datetime.now().timestamp())
    backlog = latest_state.get("mention_backlog")
    if not isinstance(backlog, dict):
        backlog = {}
    quarantine_records = latest_state.get("author_evaluation_quarantines")
    if not isinstance(quarantine_records, dict):
        quarantine_records = {}
    active_quarantine_author_ids = sorted(
        str(author_id)
        for author_id, record in quarantine_records.items()
        if isinstance(record, dict)
        and type(record.get("quarantine_until_epoch")) is int
        and record["quarantine_until_epoch"] > observed_epoch
    )
    backlog_started_epoch = backlog.get("started_epoch")
    summary = {
        "time": latest_state_ts.strftime("%Y-%m-%d %H:%M:%S") if latest_state_ts else None,
        "_state_source": source,
        "daily_reply_date": latest_state.get("daily_reply_date"),
        "daily_reply_count": latest_state.get("daily_reply_count"),
        "daily_quote_reply_date": latest_state.get("daily_quote_reply_date"),
        "daily_quote_reply_count": latest_state.get("daily_quote_reply_count"),
        "last_seen_mention_id": latest_state.get("last_seen_mention_id"),
        "mention_backlog_active": bool(backlog),
        "mention_backlog_age_seconds": (
            max(0, observed_epoch - backlog_started_epoch)
            if type(backlog_started_epoch) is int and backlog_started_epoch > 0
            else None
        ),
        "mention_backlog_pages_completed": backlog.get("pages_completed", 0),
        "mention_backlog_highest_mention_id": backlog.get("highest_mention_id"),
        "mention_backlog_continuation_token_present": bool(backlog.get("next_token")),
        "mention_pending_candidate_count": (
            len(latest_state.get("mention_pending_candidates", {}))
            if isinstance(latest_state.get("mention_pending_candidates"), dict)
            else UNKNOWN_INVALID_STATE_FIELD
        ),
        "active_author_evaluation_quarantine_count": len(active_quarantine_author_ids),
        "active_author_evaluation_quarantine_author_ids": active_quarantine_author_ids,
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
    """Load authoritative state for logs."""
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


def load_current_runtime_state(
    project_dir: Path,
) -> Tuple[Optional[Dict[str, Any]], Path, Optional[datetime], str]:
    """Read and minimally validate the production runtime state at generation time."""
    path = project_dir / "bot_state.json"
    if not path.exists():
        return None, path, None, "absent"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("state root is not a JSON object")
        if not any(
            key in data
            for key in (
                "daily_reply_count",
                "last_main_post_id",
                "last_seen_mention_id",
                "next_reply_lane_priority",
            )
        ):
            raise ValueError("state has no recognised runtime fields")
        for key in ("daily_reply_count", "daily_quote_reply_count"):
            if key in data and (
                type(data[key]) is not int or data[key] < 0
            ):
                raise ValueError(f"{key} is not a non-negative integer")
        mtime = datetime.fromtimestamp(path.stat().st_mtime)
        return data, path, mtime, "available"
    except Exception as exc:
        return None, path, None, f"malformed: {type(exc).__name__}: {exc}"


CURRENT_CONFIG_REPORT_KEYS = {
    "MAX_AUTO_REPLIES_PER_DAY",
    "MAX_REPLIES_PER_AUTHOR_PER_DAY",
    "MAX_QUOTE_REPLIES_PER_DAY",
    "MIN_SECONDS_BETWEEN_REPLIES",
    "REPLY_CHECK_EVERY_SECONDS",
    "MAX_MENTIONS_PER_CHECK",
    "MENTIONS_MAX_PAGES_PER_CHECK",
    "AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD",
    "AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS",
    "AUTHOR_NO_REPLY_QUARANTINE_SECONDS",
    "QUOTE_CHECK_EVERY_SECONDS",
    "QUOTE_LOOKUP_API_MAX_RESULTS",
    "QUOTE_LOOKUP_MAX_PAGES_PER_POST",
    "QUOTE_CHECK_SPACING_RETRY_SECONDS",
    "ENABLE_HOT_POST_REPLY_CHECKS",
    "MAX_HOT_POST_REPLIES_PER_CHECK",
    "HOT_POST_REPLY_SEARCH_API_MAX_RESULTS",
    "HOT_POST_REPLY_SEARCH_MAX_PAGES_PER_CHECK",
    "ENABLE_DAILY_MEME_POSTS",
    "MEME_TRIGGER_AFTER_HOUR",
    "MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS",
    "MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS",
    "MEME_FALLBACK_HOUR",
    "MEME_FALLBACK_MINUTE",
    "MEME_MIN_SECONDS_AFTER_QUOTE_POST",
    "MEME_SCHEDULE_VERSION",
    "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN",
    "POST_SLEEP_MIN",
    "POST_SLEEP_MAX",
}


def load_current_runtime_config(
    project_dir: Path,
) -> Tuple[Optional[Dict[str, Any]], Path, Optional[datetime], str]:
    """Read allow-listed values from the on-disk local override file."""
    path = project_dir / "mrsMThatcher.local.json"
    if not path.exists():
        return None, path, None, "absent"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("config root is not a JSON object")
        for key in (
            "MAX_AUTO_REPLIES_PER_DAY",
            "MAX_REPLIES_PER_AUTHOR_PER_DAY",
            "MAX_QUOTE_REPLIES_PER_DAY",
        ):
            if key in data and (type(data[key]) is not int or data[key] <= 0):
                raise ValueError(f"{key} is not a positive integer")
        config = {
            key: value
            for key, value in data.items()
            if key in CURRENT_CONFIG_REPORT_KEYS
        }
        config["_config_source"] = "mrsMThatcher.local.json"
        config["_config_source_path"] = str(path)
        config["_config_source_time"] = dt_text(
            datetime.fromtimestamp(path.stat().st_mtime)
        )
        return config, path, datetime.fromtimestamp(path.stat().st_mtime), "available"
    except Exception as exc:
        return None, path, None, f"malformed: {type(exc).__name__}: {exc}"


def state_context_is_within_window(state: Dict[str, Any], window_end: Optional[datetime]) -> bool:
    """Return whether state context is within window."""
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
    "_config_source",
    "_config_source_path",
    "_config_source_time",
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


def shadow_lifecycle_snapshot(project_dir: Path) -> Dict[str, Any]:
    """Load the compact local lifecycle register without contacting a provider."""
    path = project_dir / "shadow_feature_lifecycle.json"
    try:
        from shadow_lifecycle import lifecycle_decision_schedule, load_lifecycle_register

        value = load_lifecycle_register(path)
        schedule = lifecycle_decision_schedule(value)
    except Exception as exc:
        return {
            "available": False,
            "reason": f"lifecycle register unavailable: {type(exc).__name__}: {exc}",
            "features": [],
        }
    return {
        "available": True,
        "schema_version": value["schema_version"],
        "features": value["features"],
        "decision_schedule": schedule,
        "overdue_decisions": [
            row for row in schedule if row["decision_overdue"]
        ],
    }


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
    """Return the try parse response ID text."""
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
    """Return the try parse JSON object from msg."""
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
    """Return the seconds between."""
    return abs((a - b).total_seconds())


def is_media_v2_request_failure(record: Record) -> bool:
    """Return whether is media v2 request failure."""
    return (
        record.level in {"ERROR", "CRITICAL"}
        and record.src == "x_request"
        and "X request failed before receiving response" in record.msg
    )


def is_reply_target_eligibility_restriction(message: str) -> bool:
    """Return whether is reply target eligibility restriction."""
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


def is_deleted_or_inaccessible_tweet_403(message: str) -> bool:
    """Return whether a 403 says the target tweet was deleted or inaccessible."""
    text = str(message or "").lower()
    return "403" in text and any(
        marker in text
        for marker in (
            "tweet that is deleted or not visible to you",
            "post that is deleted or not visible to you",
            "tweet is deleted or not visible",
            "post is deleted or not visible",
            "tweet is unavailable",
            "post is unavailable",
        )
    )


def classify_x_request_endpoint(method: str, url: str) -> str:
    """Map one logged X request to its exact operational endpoint class."""

    method = str(method or "").upper()
    try:
        path = urlsplit(str(url or "")).path
    except ValueError:
        path = ""
    if path == "/2/media/upload":
        return "media/upload"
    if path == "/2/tweets" and method == "POST":
        return "tweet/create"
    if re.fullmatch(r"/2/users/[^/]+/mentions", path):
        return "mentions"
    if path == "/2/tweets/search/recent":
        return "recent/search"
    if re.fullmatch(r"/2/tweets/[^/]+/quote_tweets", path):
        return "quote_tweets"
    if re.fullmatch(r"/2/tweets/[^/]+", path):
        return "tweet/lookup"
    if path:
        return path.lstrip("/") or "root"
    return "unknown"


def parse_x_request_start(message: str) -> Optional[Dict[str, str]]:
    """Parse the request identity logged immediately before X transport."""

    match = re.fullmatch(r"X(?: bearer)? request: ([A-Z]+) (\S+)", str(message))
    if not match:
        return None
    method, url = match.groups()
    return {
        "method": method,
        "url": url,
        "endpoint": classify_x_request_endpoint(method, url),
    }


def parse_remote_write_transaction_event(record: Record) -> Optional[Dict[str, Any]]:
    """Parse current receipt/media/transport lifecycle logs into one vocabulary."""

    message = str(record.msg or "")
    base: Dict[str, Any] = {
        "time": record.ts.strftime("%Y-%m-%d %H:%M:%S"),
        "level": record.level,
        "where": f"{record.src}:{record.line}",
        "message": short(message, 500),
    }
    match = re.search(r"Uploading receipt-bound media via X API v2: (.+)$", message)
    if match:
        return {
            **base,
            "kind": "media_upload",
            "phase": "request_started",
            "image": Path(match.group(1).strip()).name,
        }
    match = re.search(
        r"X media upload outcome is ambiguous; .* image=([^\s]+)",
        message,
    )
    if match:
        return {
            **base,
            "kind": "media_upload",
            "phase": "ambiguous",
            "image": Path(match.group(1)).name,
        }
    match = re.search(
        r"Creating X post with durable transport journal\. lane=([^\s]+) "
        r"transaction_id=([0-9a-f]{64}) reply_to_id=([^\s]*) "
        r"media_count=(\d+) made_with_ai=(\S+)",
        message,
    )
    if match:
        return {
            **base,
            "kind": "tweet_transport",
            "phase": "request_started",
            "lane": match.group(1),
            "transaction_id": match.group(2),
            "reply_to_id": match.group(3),
            "media_count": int(match.group(4)),
            "made_with_ai": match.group(5),
        }
    patterns = (
        (
            r"Wrote main-post sending receipt lane=([^\s]+) attempt_id=([^\s]+) path=(.+)$",
            "main_post_receipt",
            "sending_published",
        ),
        (
            r"Promoted main-post receipt to attempting lane=([^\s]+) attempt_id=([^\s]+) path=(.+)$",
            "main_post_receipt",
            "attempting",
        ),
        (
            r"Handed confirmed media upload to durable main-post attempt lane=([^\s]+) attempt_id=([^\s]+) media_id=([^\s]+)$",
            "media_upload",
            "confirmed_handoff",
        ),
        (
            r"Promoted main-post attempt to confirmed pending-schedule receipt lane=([^\s]+) attempt_id=([^\s]+) post_id=([^\s]+) path=(.+)$",
            "main_post_receipt",
            "confirmed_pending_schedule",
        ),
    )
    for expression, kind, phase in patterns:
        match = re.search(expression, message)
        if not match:
            continue
        result = {
            **base,
            "kind": kind,
            "phase": phase,
            "lane": match.group(1),
            "attempt_id": match.group(2),
        }
        if phase == "confirmed_handoff":
            result["media_id"] = match.group(3)
        elif phase == "confirmed_pending_schedule":
            result["post_id"] = match.group(3)
            result["path"] = match.group(4)
        else:
            result["path"] = match.group(3)
        return result
    match = re.search(
        r"Removed main-post sending receipt disposition=([^\s]+) "
        r"lane=([^\s]+) attempt_id=([^\s]+) path=(.+)$",
        message,
    )
    if match:
        return {
            **base,
            "kind": "main_post_receipt",
            "phase": "sending_retired",
            "disposition": match.group(1),
            "lane": match.group(2),
            "attempt_id": match.group(3),
            "path": match.group(4),
        }
    match = re.search(
        r"Finalised (?:confirmed )?pending-schedule receipt "
        r"lane=([^\s]+) post_id=([^\s]+) path=(.+)$",
        message,
    )
    if match:
        return {
            **base,
            "kind": "main_post_receipt",
            "phase": "schedule_finalised",
            "lane": match.group(1),
            "post_id": match.group(2),
            "path": match.group(3),
        }
    match = re.search(
        r"Resumed interrupted exact source-receipt retirement path=([^\s]+) "
        r"phase=([^\s]+)",
        message,
    )
    if match:
        return {
            **base,
            "kind": "source_receipt_retirement",
            "phase": match.group(2),
            "path": match.group(1),
        }
    match = re.search(
        r"Resumed interrupted confirmed-media fence retirement lane=([^\s]+) "
        r"media_transaction_id=([^\s]+) media_id=([^;\s]+)",
        message,
    )
    if match:
        return {
            **base,
            "kind": "media_retirement",
            "phase": "resumed",
            "lane": match.group(1),
            "transaction_id": match.group(2),
            "media_id": match.group(3),
        }
    if "Recovered crash-left permanent retirement-ledger exchanges" in message:
        return {
            **base,
            "kind": "retirement_ledger",
            "phase": "exchange_recovered",
        }
    return None


def summarise_main_post_receipt_lifecycle(
    receipt_events: List[Dict[str, Any]],
) -> Dict[str, Any]:
    """Associate current and retained main-post receipt lifecycle events."""

    pending: Dict[str, List[Dict[str, Any]]] = {
        "quote_image": [],
        "daily_meme": [],
    }
    unresolved: List[Dict[str, Any]] = []
    completed_by_lane: Counter = Counter()
    boundary_by_lane: Counter = Counter()

    def normal_lane(item: Dict[str, Any]) -> str:
        lane = str(item.get("lane") or "")
        kind = str(item.get("kind") or "")
        if lane in pending:
            return lane
        if kind.startswith("regular_"):
            return "quote_image"
        if kind.startswith("meme_"):
            return "daily_meme"
        return lane

    def matching_index(lane: str, item: Dict[str, Any]) -> int | None:
        candidates = pending.get(lane, [])
        attempt_id = str(item.get("attempt_id") or "")
        path = str(item.get("path") or "")
        for index, candidate in enumerate(candidates):
            if attempt_id and candidate.get("attempt_id") == attempt_id:
                return index
            if path and candidate.get("path") == path:
                return index
        return 0 if candidates else None

    def observe_pending(
        lane: str,
        item: Dict[str, Any],
        *,
        opening_write_observed: bool,
    ) -> None:
        if lane not in pending:
            unresolved.append(item)
            return
        index = matching_index(lane, item)
        if index is None:
            pending[lane].append(
                {**item, "opening_write_observed": opening_write_observed}
            )
            return
        existing = pending[lane][index]
        pending[lane][index] = {
            **existing,
            **item,
            "opening_write_observed": bool(
                existing.get("opening_write_observed")
                or opening_write_observed
            ),
        }

    def terminal_removal(lane: str) -> None:
        candidates = pending.get(lane, [])
        if candidates:
            lifecycle = candidates.pop(0)
            if lifecycle.get("opening_write_observed"):
                completed_by_lane[lane] += 1
            else:
                boundary_by_lane[lane] += 1
        elif lane in pending:
            boundary_by_lane[lane] += 1

    for item in receipt_events:
        kind = str(item.get("kind") or "")
        phase = str(item.get("phase") or "")
        lane = normal_lane(item)
        if kind in {"regular_written", "meme_written"}:
            observe_pending(lane, item, opening_write_observed=True)
        elif kind == "main_post_receipt":
            if phase == "sending_published":
                observe_pending(lane, item, opening_write_observed=True)
            elif phase in {
                "attempting",
                "confirmed_pending_schedule",
                "schedule_finalised",
            }:
                observe_pending(lane, item, opening_write_observed=False)
            elif phase == "sending_retired":
                index = matching_index(lane, item)
                if index is not None and lane in pending:
                    pending[lane].pop(index)
            else:
                unresolved.append(item)
        elif kind in {"regular_reconciled", "meme_reconciled"}:
            observe_pending(lane, item, opening_write_observed=False)
        elif kind in {"regular_removed", "meme_removed"}:
            terminal_removal(lane)
        elif kind in {
            "regular_replay_suppressed_second_post",
            "meme_replay_suppressed_second_post",
        }:
            continue
        else:
            unresolved.append(item)

    for lane in ("quote_image", "daily_meme"):
        unresolved.extend(pending[lane])
    return {
        "completed_count": sum(completed_by_lane.values()),
        "regular_completed_count": completed_by_lane["quote_image"],
        "meme_completed_count": completed_by_lane["daily_meme"],
        "boundary_removal_count": sum(boundary_by_lane.values()),
        "regular_boundary_removal_count": boundary_by_lane["quote_image"],
        "meme_boundary_removal_count": boundary_by_lane["daily_meme"],
        "unresolved": unresolved,
    }


def plural_count(count: Any, singular: str, plural: Optional[str] = None) -> str:
    """Format an integer with a correctly pluralised noun phrase."""
    try:
        number = int(count)
    except (TypeError, ValueError):
        number = 0
    noun = singular if number == 1 else (plural or f"{singular}s")
    return f"{number} {noun}"


def _incident_exception_line(message: str) -> str:
    """Return the final exception/result line from a traceback-like message."""
    lines = [line.strip() for line in str(message or "").splitlines() if line.strip()]
    for line in reversed(lines):
        if line.startswith(("Traceback (most recent call last)", "File ")):
            continue
        return line
    return ""


def _normalise_incident_text(value: str) -> str:
    """Remove volatile identifiers while retaining a deterministic root signature."""
    text = str(value or "").lower()
    text = re.sub(r"/[^\s:]+", "<path>", text)
    text = re.sub(r"\b[0-9a-f]{64}\b", "<sha256>", text)
    text = re.sub(r"\b\d{12,}\b", "<id>", text)
    text = re.sub(r"\b\d+\b", "<n>", text)
    return re.sub(r"\s+", " ", text).strip()


def classify_operational_error(message: str) -> str:
    """Classify a traceback/error by its root operational concern."""
    text = str(message or "")
    lowered = text.lower()
    exception_line = _incident_exception_line(text).lower()
    if "clarification reply lacks direct_factual_answer mode" in lowered:
        return "clarification_mode_local_rejection"
    if (
        any(marker in lowered for marker in ("readtimeout", "read timed out"))
        and any(marker in lowered for marker in ("xai", "grok", "api.x.ai"))
    ):
        return "xai_provider_timeout"
    if (
        re.search(r"\bx(?: bearer)? api error 429\b", lowered)
        or "entering api cooldown after 429" in lowered
    ):
        return "x_api_rate_limit"
    if (
        "paginationcursorprotocolerror" in lowered
        and "quote tweets" in lowered
        and "repeated pagination token" in lowered
    ):
        return "quote_pagination_protocol_anomaly"
    if is_deleted_or_inaccessible_tweet_403(text):
        return "deleted_or_inaccessible_tweet"
    if any(
        marker in lowered
        for marker in (
            "ambiguous remote x post outcome",
            "ambiguousremotepostoutcome",
            "media upload outcome is ambiguous",
            "remote outcome is ambiguous",
            "durable remote-write safety barrier",
            "unresolved transaction receipt, marker, or process latch",
        )
    ):
        return "remote_write_ambiguity_barrier"
    if any(
        marker in lowered
        for marker in (
            "remote-write protocol is not activated",
            "protocol activation sentinel",
            "restart-persistent remote-write protocol",
        )
    ):
        return "remote_write_protocol_barrier"
    if any(
        marker in lowered
        for marker in (
            "remote-write receipt cannot be inspected",
            "transport journal",
            "transport fence",
            "source-receipt retirement",
            "confirmed-media retirement",
            "pending-schedule receipt",
            "sending receipt as a global manual-reconciliation barrier",
            "locally confirmed remote transaction could not be reconciled",
        )
    ):
        return "remote_write_transaction_barrier"
    if any(
        marker in lowered
        for marker in (
            "another mrsmthatcher instance owns",
            "instance lock cannot be opened safely",
        )
    ):
        return "instance_lock_conflict"
    if re.search(r"\bx(?: bearer)? api error 5\d\d\b", lowered):
        return "x_api_transient_failure"
    if "source-role audit policy is incompatible" in lowered:
        return "historical_context_source_role_incompatibility"
    if "bot crashed with unhandled exception" in lowered:
        return "process_crash"
    if (
        "unresolvedregularpostreceipt" in lowered
        or "unresolved regular-post receipt" in lowered
    ):
        return "legacy_regular_receipt_barrier"
    if "historical context reply failed independently" in lowered:
        return "historical_context_reply_failure"
    if "daily meme posting failed" in lowered:
        return "daily_meme_failure"
    if "quote/image posting failed" in lowered:
        return "quote_image_posting_failure"
    if (
        "unresolved conversational reply sending receipt" in lowered
        or "unresolved confirmed reply receipt reconciliation" in lowered
    ):
        return "conversational_reply_receipt_barrier"
    if "failed to post generated reply" in lowered:
        return "conversational_reply_posting_failure"
    if exception_line:
        return _normalise_incident_text(exception_line).split(":", 1)[0] or "operational_error"
    return "operational_error"


def _event_time(value: Dict[str, Any]) -> Optional[datetime]:
    try:
        return parse_dt(str(value.get("time") or ""))
    except ValueError:
        return None


def summarise_operational_error_health(
    errors: List[Dict[str, Any]],
    events: List[Dict[str, Any]],
    receipt_events: List[Dict[str, Any]],
    lifecycle: Iterable[Dict[str, Any]] = (),
    current_remote_write_safety: Optional[Dict[str, Any]] = None,
    generation_time: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Group traceback cascades and distinguish recovered from current incidents."""
    generated_at = generation_time or datetime.now()
    serious = [item for item in errors if item.get("level") in {"ERROR", "CRITICAL"}]
    operational = [
        item
        for item in serious
        if classify_operational_error(
            str(item.get("_raw_message") or item.get("message") or "")
        )
        != "clarification_mode_local_rejection"
    ]
    pipeline_failures_by_identity: Dict[
        Tuple[str, str], List[Dict[str, Any]]
    ] = {}
    for event in events:
        if event.get("kind") != "reply_strategy_failure":
            continue
        lane = _normalise_lane(event.get("lane"))
        target_id = str(event.get("target_id") or "")
        if lane == "unavailable" or not target_id:
            continue
        pipeline_failures_by_identity.setdefault((lane, target_id), []).append(
            event
        )

    raw_pipeline_evidence: Dict[
        int, Tuple[Tuple[str, str], str]
    ] = {}
    for item in operational:
        raw = str(item.get("_raw_message") or item.get("message") or "")
        item_time = _event_time(item)
        if item_time is None:
            continue
        pipeline_ended_match = re.fullmatch(
            r"AI-first reply pipeline ended\s+"
            r"status=(?P<status>\S+)\s+lane=(?P<lane>\S+)\s+"
            r"target_id=(?P<target_id>[A-Za-z0-9_-]+)\s+"
            r"reason=(?P<reason>\S+)\s+calls=\d+\s+revisions=\d+",
            raw.strip(),
        )
        if pipeline_ended_match is not None:
            lane = _normalise_lane(pipeline_ended_match.group("lane"))
            target_id = pipeline_ended_match.group("target_id")
            reason = pipeline_ended_match.group("reason")
            matching_failures: List[Tuple[str, str]] = []
            if (
                pipeline_ended_match.group("status") == "operational_failure"
                and lane in {"mention", "hot-post", "quote-tweet"}
                and target_id
                and reason
            ):
                identity = (lane, target_id)
                for failure in pipeline_failures_by_identity.get(identity, []):
                    failure_time = _event_time(failure)
                    if (
                        failure_time is not None
                        and str(failure.get("reason") or "") == reason
                        and abs((item_time - failure_time).total_seconds()) <= 5
                    ):
                        matching_failures.append(identity)
            if len(matching_failures) == 1:
                raw_pipeline_evidence[id(item)] = (
                    matching_failures[0],
                    "pipeline_error",
                )
            continue
        lowered = raw.lower()
        if "failed to ask grok for reply" not in lowered or "apierror" not in lowered:
            continue
        where = str(item.get("where") or "").lower()
        lane_hint: Optional[str] = None
        lane_match = re.search(r"\blane[=:]\s*([a-z_-]+)", raw, re.IGNORECASE)
        if lane_match:
            parsed_lane = _normalise_lane(lane_match.group(1))
            if parsed_lane != "unavailable":
                lane_hint = parsed_lane
        target_match = re.search(
            r"\btarget_id[=:]\s*([A-Za-z0-9_-]+)", raw, re.IGNORECASE
        )
        target_hint = target_match.group(1) if target_match else None
        if lane_hint is None and target_hint is None:
            if "quote_tweet" in where or "quote-tweet" in where:
                lane_hint = "quote-tweet"
            elif "hot_post" in where or "hot-post" in where:
                lane_hint = "hot-post"
            elif "maybe_reply_to_mentions" not in where and "mention" in where:
                lane_hint = "mention"
        candidates: List[Tuple[float, Tuple[str, str]]] = []
        for identity, failures_for_target in pipeline_failures_by_identity.items():
            lane, target_id = identity
            if lane_hint is not None and lane != lane_hint:
                continue
            if target_hint is not None and target_id != target_hint:
                continue
            deltas = [
                (item_time - failure_time).total_seconds()
                for failure in failures_for_target
                if (failure_time := _event_time(failure)) is not None
            ]
            causal_deltas = [delta for delta in deltas if 0 <= delta <= 5]
            if causal_deltas:
                candidates.append((min(causal_deltas), identity))
        if candidates:
            nearest_delta = min(delta for delta, _identity in candidates)
            nearest_identities = sorted(
                {
                    identity
                    for delta, identity in candidates
                    if delta == nearest_delta
                }
            )
            if len(nearest_identities) == 1:
                raw_pipeline_evidence[id(item)] = (
                    nearest_identities[0],
                    "outer_wrapper",
                )

    groups: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    stable_root_categories = {
        "historical_context_source_role_incompatibility",
        "historical_context_reply_failure",
        "legacy_regular_receipt_barrier",
        "conversational_reply_receipt_barrier",
        "process_crash",
        "remote_write_ambiguity_barrier",
        "remote_write_protocol_barrier",
        "remote_write_transaction_barrier",
        "instance_lock_conflict",
        "x_api_transient_failure",
        "x_api_rate_limit",
        "quote_pagination_protocol_anomaly",
        "xai_provider_timeout",
    }
    ambiguity_times = [
        _event_time(item)
        for item in serious
        if classify_operational_error(
            str(item.get("_raw_message") or item.get("message") or "")
        )
        == "remote_write_ambiguity_barrier"
    ]
    ambiguity_times = [item for item in ambiguity_times if item is not None]
    ambiguous_reply_outcomes: List[Tuple[datetime, str, str]] = []
    for event in events:
        if (
            event.get("kind") != "reply_strategy_outcome"
            or event.get("status") != "posting_failed_retryable"
            or event.get("failure_reason") != "ambiguous_remote_outcome"
        ):
            continue
        outcome_time = _event_time(event)
        lane = _normalise_lane(event.get("lane"))
        target_id = str(event.get("target_id") or "")
        if outcome_time is None or lane == "unavailable" or not target_id:
            continue
        if not any(
            seconds_between(outcome_time, root_time) <= 5
            for root_time in ambiguity_times
        ):
            continue
        ambiguous_reply_outcomes.append((outcome_time, lane, target_id))

    def is_subordinate_remote_write_symptom(
        *,
        category: str,
        raw: str,
        item_time: Optional[datetime],
    ) -> bool:
        """Bind exact receipt/lane symptoms to a logged reply ambiguity root."""

        if item_time is None:
            return False
        if category == "conversational_reply_receipt_barrier":
            identity = re.search(
                r"\blane=([^\s]+) target_id=([^\s]+)",
                raw,
            )
            if identity is None:
                return False
            lane = _normalise_lane(identity.group(1))
            target_id = identity.group(2)
            return any(
                outcome_lane == lane
                and outcome_target == target_id
                and 0
                <= (outcome_time - item_time).total_seconds()
                <= 300
                for outcome_time, outcome_lane, outcome_target
                in ambiguous_reply_outcomes
            )
        if category == "remote_write_transaction_barrier":
            return any(
                seconds_between(item_time, root_time) <= 5
                for root_time in ambiguity_times
            )
        first_line = raw.splitlines()[0].strip() if raw else ""
        exact_lane_barrier = bool(
            re.fullmatch(
                r"(?:Normal reply|Quote-tweet) lane (?:stopped by the global "
                r"remote-write safety barrier|created an ambiguous-post barrier; "
                r"skipping all later lanes)",
                first_line,
            )
            or re.fullmatch(
                r"Test-cycle (?:normal|quote_tweet) reply lane stopped by the "
                r"global remote-write safety barrier",
                first_line,
            )
            or first_line
            == "Test cycle stopped after an ambiguous remote post; no later lane will run"
            or re.fullmatch(
                r"(?:Mention|Hot-post|Quote-tweet) reply stopped after an "
                r"ambiguous remote outcome; the global remote-write safety "
                r"barrier remains active",
                first_line,
            )
        )
        if not exact_lane_barrier:
            return False
        return any(
            0 <= (item_time - outcome_time).total_seconds() <= 5
            for outcome_time, _lane, _target_id in ambiguous_reply_outcomes
        )

    for item in operational:
        raw = str(item.get("_raw_message") or item.get("message") or "")
        pipeline_evidence = raw_pipeline_evidence.get(id(item))
        evidence_identity = pipeline_evidence[0] if pipeline_evidence else None
        category = (
            "reply_strategy_pipeline_failure"
            if evidence_identity is not None
            else classify_operational_error(raw)
        )
        item_time = _event_time(item)
        if (
            category == "x_api_transient_failure"
            and item_time is not None
            and any(seconds_between(item_time, other) <= 10 for other in ambiguity_times)
        ):
            category = "remote_write_ambiguity_barrier"
        elif is_subordinate_remote_write_symptom(
            category=category,
            raw=raw,
            item_time=item_time,
        ):
            item["_remote_write_subordinate_category"] = category
            if category == "conversational_reply_receipt_barrier":
                identity = re.search(
                    r"\blane=([^\s]+) target_id=([^\s]+)",
                    raw,
                )
                if identity is not None:
                    item["_remote_write_subordinate_reply_identity"] = {
                        "lane": _normalise_lane(identity.group(1)),
                        "target_id": identity.group(2),
                        "source_time": str(item.get("time") or ""),
                    }
            category = "remote_write_ambiguity_barrier"
        root = (_incident_exception_line(raw) or raw.splitlines()[0]) if raw else category
        if category == "xai_provider_timeout" and item_time is not None:
            signature = ""
            for (candidate_category, candidate_signature), rows in reversed(
                list(groups.items())
            ):
                previous_time = _event_time(rows[-1])
                if (
                    candidate_category == category
                    and previous_time is not None
                    and seconds_between(item_time, previous_time) <= 5
                ):
                    signature = candidate_signature
                    break
            if not signature:
                signature = f"{category}:{dt_text(item_time)}"
        elif evidence_identity is not None:
            signature = f"{evidence_identity[0]}:{evidence_identity[1]}"
        else:
            signature = (
                category
                if category in stable_root_categories
                else _normalise_incident_text(root)
            )
        groups.setdefault((category, signature), []).append(item)

    pipeline_identity_by_group: Dict[Tuple[str, str], Tuple[str, str]] = {}
    for identity in pipeline_failures_by_identity:
        group_key = (
            "reply_strategy_pipeline_failure",
            f"{identity[0]}:{identity[1]}",
        )
        groups.setdefault(group_key, [])
        pipeline_identity_by_group[group_key] = identity

    event_times: Dict[str, List[datetime]] = {}
    for event in events:
        ts = _event_time(event)
        if ts is not None:
            event_times.setdefault(str(event.get("kind") or ""), []).append(ts)
    receipt_removed_times: List[datetime] = []
    for item in receipt_events:
        if item.get("kind") not in {"regular_removed", "regular_reconciled"}:
            continue
        ts = _event_time(item)
        if ts is not None:
            receipt_removed_times.append(ts)
    successful_restart_times: List[datetime] = []
    for item in lifecycle:
        if "Bot started successfully" not in str(item.get("message") or ""):
            continue
        ts = _event_time(item)
        if ts is not None:
            successful_restart_times.append(ts)

    def pipeline_recovered_after(
        identity: Tuple[str, str], last_time: datetime
    ) -> Tuple[bool, str, Optional[datetime]]:
        lane, target_id = identity
        candidates: List[Tuple[datetime, str]] = []
        for event in events:
            ts = _event_time(event)
            if ts is None or ts <= last_time:
                continue
            if (
                _normalise_lane(event.get("lane")) != lane
                or str(event.get("target_id") or "") != target_id
            ):
                continue
            kind = event.get("kind")
            if kind == "reply_strategy_decision":
                if _is_terminal_pipeline_failure(
                    event.get("reason") or event.get("no_reply_reason"),
                    event.get("status"),
                ):
                    continue
                terminal_local_outcome = _terminal_local_rejection_outcome(
                    event.get("reason")
                ) or _terminal_local_rejection_outcome(
                    event.get("no_reply_reason")
                )
                if terminal_local_outcome is not None:
                    candidates.append(
                        (
                            ts,
                            "later terminal local decision observed for "
                            f"{lane} target {target_id}",
                        )
                    )
                elif event.get("mode") == "no_reply":
                    candidates.append(
                        (
                            ts,
                            "later terminal no-reply decision observed for "
                            f"{lane} target {target_id}",
                        )
                    )
            elif kind == "reply_strategy_outcome" and str(
                event.get("status") or "confirmed"
            ) in {"confirmed", "posted"}:
                candidates.append(
                    (
                        ts,
                        "later confirmed reply outcome observed for "
                        f"{lane} target {target_id}",
                    )
                )
            elif (
                kind == "reply_strategy_local_rejection"
                and _terminal_local_rejection_outcome(event.get("reason"))
                is not None
            ):
                candidates.append(
                    (
                        ts,
                        "later terminal local rejection observed for "
                        f"{lane} target {target_id}",
                    )
                )
        if not candidates:
            return False, "", None
        recovery_time, reason = min(candidates, key=lambda item: (item[0], item[1]))
        return True, reason, recovery_time

    def recovered_after(category: str, last_time: datetime) -> Tuple[bool, str, Optional[datetime]]:
        candidates: List[Tuple[datetime, str]] = []
        recovery_kinds: Tuple[str, ...] = ()
        if category in {
            "historical_context_source_role_incompatibility",
            "historical_context_reply_failure",
        }:
            for event in events:
                ts = _event_time(event)
                if (
                    ts is not None
                    and ts > last_time
                    and event.get("kind") == "historical_context_reply"
                    and event.get("status") in {"completed", "already_completed"}
                ):
                    candidates.append((ts, "later historical-context reply completed"))
            for event in events:
                ts = _event_time(event)
                if (
                    ts is not None
                    and ts > last_time
                    and event.get("kind") == "historical_context_semantic_gate"
                    and event.get("status") == "loaded"
                ):
                    candidates.append(
                        (ts, "later historical-context semantic gate loaded successfully")
                    )
        elif category == "legacy_regular_receipt_barrier":
            candidates.extend(
                (ts, "regular receipt reconciled or retired")
                for ts in receipt_removed_times
                if ts > last_time
            )
            recovery_kinds = ("daily_meme_posted", "quote_image_posted")
        elif category == "daily_meme_failure":
            recovery_kinds = ("daily_meme_posted",)
        elif category == "quote_image_posting_failure":
            recovery_kinds = ("quote_image_posted",)
        elif category == "conversational_reply_posting_failure":
            recovery_kinds = (
                "mention_reply_posted",
                "hot_post_reply_posted",
                "quote_tweet_reply_posted",
            )
        elif category == "quote_pagination_protocol_anomaly":
            recovery_kinds = (
                "quote_lane_activity_succeeded",
                "quote_pagination_repeated_token",
            )
        elif category == "process_crash":
            candidates.extend(
                (ts, "later successful bot startup observed")
                for ts in successful_restart_times
                if ts > last_time
            )
        elif category == "instance_lock_conflict":
            candidates.extend(
                (ts, "later successful single-instance bot startup observed")
                for ts in successful_restart_times
                if ts > last_time
            )
        elif category in {
            "remote_write_ambiguity_barrier",
            "remote_write_protocol_barrier",
        }:
            safety = current_remote_write_safety or {}
            if safety.get("configured") is True and safety.get("available") is True:
                protocol_valid = (
                    (safety.get("protocol") or {}).get("valid") is True
                )
                active_entries = safety.get("active_entries")
                active_marker_names = safety.get("active_marker_names")
                transport = safety.get("transport") or {}
                current_clear = safety.get("blocking") is False
                authoritative_barrier_namespace_clear = bool(
                    safety.get("blocking") is False
                    and isinstance(active_entries, list)
                    and not active_entries
                    and isinstance(active_marker_names, list)
                    and not active_marker_names
                    and transport.get("blocking") is False
                    and transport.get("classification") == "clear"
                )
                if category == "remote_write_ambiguity_barrier":
                    proved = safety.get("reconciliation_proven") is True
                    archive = safety.get("reconciliation_archive") or {}
                    marker_audits = archive.get("marker_reconciliations") or []
                    if not marker_audits:
                        latest = archive.get("latest_marker_reconciliation")
                        marker_audits = [latest] if latest else []
                    following_audits = [
                        item
                        for item in marker_audits
                        if type(item.get("archived_at_epoch")) is int
                        and item["archived_at_epoch"]
                        >= int(last_time.timestamp())
                    ]
                    if (
                        authoritative_barrier_namespace_clear
                        and protocol_valid
                        and proved
                        and archive.get("valid") is True
                        and following_audits
                    ):
                        resolution_audit = min(
                            following_audits,
                            key=lambda item: (
                                item["archived_at_epoch"],
                                str(item.get("audit_path") or ""),
                            ),
                        )
                        resolved_at = datetime.fromtimestamp(
                            resolution_audit["archived_at_epoch"]
                        )
                        return (
                            True,
                            "durable offline reconciliation audit is valid and the current barrier namespace is clear",
                            resolved_at,
                        )
                elif current_clear and protocol_valid:
                    return (
                        True,
                        "current protocol snapshot is valid with no active transaction barrier",
                        datetime.now(),
                    )
        for kind in recovery_kinds:
            candidates.extend(
                (ts, f"later {kind.replace('_', ' ')} observed")
                for ts in event_times.get(kind, [])
                if ts > last_time
            )
        if not candidates:
            return False, "", None
        recovery_time, reason = min(candidates, key=lambda item: (item[0], item[1]))
        return True, reason, recovery_time

    incidents: List[Dict[str, Any]] = []
    for (category, signature), rows in groups.items():
        ordered = sorted(
            rows,
            key=lambda item: (str(item.get("time") or ""), str(item.get("where") or "")),
        )
        group_key = (category, signature)
        pipeline_identity = pipeline_identity_by_group.get(group_key)
        pipeline_failure_events = (
            pipeline_failures_by_identity.get(pipeline_identity, [])
            if pipeline_identity is not None
            else []
        )
        evidence_times = [
            ts
            for item in [*ordered, *pipeline_failure_events]
            if (ts := _event_time(item)) is not None
        ]
        first_time = min(evidence_times) if evidence_times else datetime.min
        last_time = max(evidence_times) if evidence_times else first_time
        transient_observation = category in {
            "x_api_transient_failure",
            "xai_provider_timeout",
        }
        if pipeline_identity is not None:
            resolved, resolution_reason, resolution_time = pipeline_recovered_after(
                pipeline_identity, last_time
            )
            status = "historical_resolved" if resolved else "current_unresolved"
        elif category == "x_api_rate_limit":
            cooldown_deadlines: List[datetime] = []
            for item in ordered:
                raw = str(
                    item.get("_raw_message") or item.get("message") or ""
                )
                match = re.search(
                    r"Entering API cooldown after 429 until "
                    r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})",
                    raw,
                )
                if match:
                    try:
                        cooldown_deadlines.append(parse_dt(match.group(1)))
                    except ValueError:
                        pass
            cooldown_deadline = max(cooldown_deadlines, default=None)
            later_x_successes = [
                ts
                for ts in event_times.get("x_activity_succeeded", [])
                if cooldown_deadline is not None
                and ts > last_time
                and ts > cooldown_deadline
            ]
            resolved = bool(
                cooldown_deadline is not None
                and cooldown_deadline < generated_at
                and later_x_successes
            )
            resolution_time = min(later_x_successes) if resolved else None
            resolution_reason = (
                "cooldown deadline passed and later successful X activity was observed"
                if resolved
                else ""
            )
            status = "historical_resolved" if resolved else "current_unresolved"
        elif transient_observation:
            resolved, resolution_reason, resolution_time = False, "", None
            status = "transient_observation_recovery_unverified"
        else:
            resolved, resolution_reason, resolution_time = recovered_after(category, last_time)
            status = "historical_resolved" if resolved else "current_unresolved"
        if pipeline_identity is not None:
            reasons = Counter(
                str(event.get("reason") or "unknown_pipeline_failure")
                for event in pipeline_failure_events
            )
            reason_text = ", ".join(
                f"{reason}={count}" for reason, count in sorted(reasons.items())
            )
            representative = (
                f"AI reply pipeline failure for {pipeline_identity[0]} target "
                f"{pipeline_identity[1]}: {reason_text}"
            )
        else:
            representative = str(
                ordered[0].get("_raw_message")
                or ordered[0].get("message")
                or ""
            ).splitlines()[0]
        incident = {
                "category": category,
                "signature": signature,
                "status": status,
                "first_seen": dt_text(first_time),
                "last_seen": dt_text(last_time),
                "record_count": len(ordered),
                "traceback_count": sum(
                    "Traceback" in str(item.get("_raw_message") or item.get("message") or "")
                    for item in ordered
                ),
                "affected_locations": sorted(
                    {
                        str(item.get("where") or "")
                        for item in ordered
                        if item.get("where")
                    }
                ),
                "summary": short(representative, 300),
                "resolution_reason": resolution_reason,
                "resolution_time": dt_text(resolution_time) if resolution_time else None,
            }
        subordinate_symptoms = Counter(
            str(item.get("_remote_write_subordinate_category") or "")
            for item in ordered
            if item.get("_remote_write_subordinate_category")
        )
        subordinate_reply_identities = sorted(
            {
                (
                    str(identity.get("lane") or ""),
                    str(identity.get("target_id") or ""),
                )
                for item in ordered
                if isinstance(
                    identity := item.get(
                        "_remote_write_subordinate_reply_identity"
                    ),
                    dict,
                )
                and identity.get("lane")
                and identity.get("target_id")
            }
        )
        subordinate_reply_events = [
            {
                "lane": str(identity.get("lane") or ""),
                "target_id": str(identity.get("target_id") or ""),
                "source_time": str(identity.get("source_time") or ""),
            }
            for item in ordered
            if isinstance(
                identity := item.get(
                    "_remote_write_subordinate_reply_identity"
                ),
                dict,
            )
            and identity.get("lane")
            and identity.get("target_id")
            and identity.get("source_time")
        ]
        if subordinate_symptoms:
            incident["correlated_subordinate_symptom_counts"] = dict(
                sorted(subordinate_symptoms.items())
            )
        if subordinate_reply_identities:
            incident["correlated_reply_receipt_identities"] = [
                {"lane": lane, "target_id": target_id}
                for lane, target_id in subordinate_reply_identities
            ]
        if subordinate_reply_events:
            incident["correlated_reply_receipt_events"] = (
                subordinate_reply_events
            )
        if pipeline_identity is not None:
            incident.update(
                {
                    "lane": pipeline_identity[0],
                    "target_id": pipeline_identity[1],
                    "pipeline_failure_event_count": len(pipeline_failure_events),
                    "wrapper_record_count": sum(
                        raw_pipeline_evidence.get(id(item), (pipeline_identity, ""))[1]
                        == "outer_wrapper"
                        for item in ordered
                    ),
                    "pipeline_failure_reason_counts": dict(reasons.most_common()),
                }
            )
        incidents.append(incident)
    incidents.sort(key=lambda item: (item["first_seen"], item["category"], item["signature"]))
    current = [item for item in incidents if item["status"] == "current_unresolved"]
    resolved = [item for item in incidents if item["status"] == "historical_resolved"]
    transient_provider_observations = [
        item
        for item in incidents
        if item["status"] == "transient_observation_recovery_unverified"
    ]
    transient_provider_timeouts = [
        item
        for item in transient_provider_observations
        if item["category"] == "xai_provider_timeout"
    ]
    return {
        "current_independent_incident_count": len(current),
        "historical_resolved_incident_count": len(resolved),
        "raw_serious_error_record_count": len(serious),
        "raw_traceback_count": sum(item.get("traceback_count", 0) for item in incidents),
        "transient_provider_timeout_count": len(transient_provider_timeouts),
        "transient_provider_timeout_record_count": sum(
            item.get("record_count", 0) for item in transient_provider_timeouts
        ),
        "transient_provider_observation_count": len(transient_provider_observations),
        "transient_provider_observation_record_count": sum(
            item.get("record_count", 0) for item in transient_provider_observations
        ),
        "current_incidents": current,
        "historical_resolved_incidents": resolved,
        "transient_provider_observations": transient_provider_observations,
    }


def is_media_fallback_warning(record: Record) -> bool:
    """Return whether is media fallback warning."""
    return (
        record.level in {"ERROR", "CRITICAL", "WARNING"}
        and "v2 media upload failed; trying v1.1 fallback" in record.msg
    )


def is_media_v1_success(record: Record) -> bool:
    """Return whether is media v1 success."""
    return "Uploaded media via v1.1." in record.msg


def is_media_v1_failure(record: Record) -> bool:
    """Return whether is media v1 failure."""
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
    """Return whether is main post success."""
    return (
        "Quote/image posted successfully." in record.msg
        or "Daily meme posted successfully." in record.msg
        or ('EVENT {"event":"main_post_posted"' in record.msg)
    )


def find_recent_media_path(records: List[Record], index: int) -> Optional[str]:
    """Find recent media path."""
    for earlier in reversed(records[max(0, index - 20):index + 1]):
        m = re.search(r"Uploading media via X API v2: (.+)$", earlier.msg)
        if m:
            return m.group(1).strip()
        m = re.search(r"Detected MIME type for (.+?):", earlier.msg)
        if m:
            return m.group(1).strip()
    return None


def correlate_media_upload_incidents(records: List[Record], max_text: int) -> Tuple[List[Dict[str, Any]], set[str]]:
    """Return the correlate media upload incidents."""
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
            if seconds_between(candidate.ts, record.ts) <= 180
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


def _parse_openai_cost_decimal(value: Any, *, label: str) -> Decimal:
    """Parse one canonical monetary string from the private OpenAI cache."""

    if type(value) is not str or not re.fullmatch(
        r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]*[1-9])?", value
    ):
        raise ValueError(f"{label} is not a canonical decimal string")
    parsed = Decimal(value)
    if not parsed.is_finite() or (parsed == 0 and value != "0"):
        raise ValueError(f"{label} is not a canonical finite decimal string")
    return parsed


def _openai_decimal_text(value: Decimal) -> str:
    """Render one finite Decimal without an exponent or redundant zeroes."""

    if not value.is_finite():
        raise ValueError("OpenAI cost is not finite")
    if value == 0:
        return "0"
    rendered = format(value, "f")
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered


def format_openai_usd(value: Any) -> str:
    """Render one cache decimal as provider-published US dollars."""

    amount = (
        value
        if isinstance(value, Decimal)
        else _parse_openai_cost_decimal(value, label="OpenAI cost")
    )
    return f"US${_openai_decimal_text(amount)}"


def _parse_openai_utc(value: Any, *, label: str) -> datetime:
    """Parse the cache's canonical whole-second UTC timestamp."""

    if type(value) is not str or not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value
    ):
        raise ValueError(f"{label} is not a canonical UTC timestamp")
    return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
        tzinfo=timezone.utc
    )


def _validate_openai_money_map(value: Any, *, label: str) -> Decimal:
    """Validate one monetary breakdown and return its exact sum."""

    if not isinstance(value, dict):
        raise ValueError(f"{label} is not an object")
    total = Decimal(0)
    for key, amount in value.items():
        if type(key) is not str or not key:
            raise ValueError(f"{label} contains an invalid key")
        total += _parse_openai_cost_decimal(amount, label=f"{label}.{key}")
    return total


def load_openai_cost_cache(
    cache_path: Optional[Path] = None,
    *,
    now_utc: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Read and strictly validate the private cache without network access."""

    path = Path(cache_path or OPENAI_COST_CACHE_PATH).expanduser()
    observed_now = now_utc or datetime.now(timezone.utc)
    if observed_now.tzinfo is None:
        observed_now = observed_now.astimezone(timezone.utc)
    else:
        observed_now = observed_now.astimezone(timezone.utc)

    def unavailable(reason: str) -> Dict[str, Any]:
        return {
            "available": False,
            "path": str(path),
            "reason": reason,
        }

    try:
        raw = read_stable_regular_bytes(path, maximum=OPENAI_COST_CACHE_MAX_BYTES)
    except FileNotFoundError:
        return unavailable("cache file is missing")
    except (OSError, RuntimeError, ValueError) as exc:
        return unavailable(f"cache file is unavailable: {exc}")
    try:
        cache = _strict_json_object(raw, label="OpenAI cost cache")
        if cache.get("schema_version") != OPENAI_COST_CACHE_SCHEMA_VERSION:
            raise ValueError("unsupported cache schema")
        if cache.get("source") != OPENAI_COST_CACHE_SOURCE:
            raise ValueError("unexpected cache source")
        if cache.get("currency") != "usd":
            raise ValueError("unsupported or mixed cache currency")
        updated_at = _parse_openai_utc(
            cache.get("updated_at_utc"), label="updated_at_utc"
        )
        if updated_at > observed_now + timedelta(minutes=5):
            raise ValueError("cache update time is in the future")
        age_seconds = max(0, int((observed_now - updated_at).total_seconds()))
        if age_seconds > OPENAI_COST_CACHE_STALE_AFTER_SECONDS:
            return unavailable(
                f"cache is stale ({_human_snapshot_age(age_seconds)} old)"
            )

        scope = cache.get("scope")
        if not isinstance(scope, dict) or scope.get("kind") not in {
            "project",
            "organization",
        }:
            raise ValueError("cache scope is invalid")
        if type(scope.get("description")) is not str or not scope["description"]:
            raise ValueError("cache scope description is invalid")
        if scope["kind"] == "project":
            if type(scope.get("project_id")) is not str or not scope["project_id"]:
                raise ValueError("project-scoped cache has no project ID")
        elif "project_id" in scope:
            raise ValueError("organization-scoped cache contains a project ID")

        days = cache.get("days")
        if not isinstance(days, dict) or not days or len(days) > 400:
            raise ValueError("cache days are missing or exceed the retention bound")
        for day_key, day_value in days.items():
            if type(day_key) is not str or not isinstance(day_value, dict):
                raise ValueError("cache contains an invalid day")
            day_date = datetime.strptime(day_key, "%Y-%m-%d").date()
            expected_start = int(
                datetime.combine(day_date, time.min, tzinfo=timezone.utc).timestamp()
            )
            if (
                type(day_value.get("start_time")) is not int
                or type(day_value.get("end_time")) is not int
                or day_value["start_time"] != expected_start
                or day_value["end_time"] != expected_start + 86_400
            ):
                raise ValueError(f"cache day {day_key} has invalid UTC boundaries")
            primary = _parse_openai_cost_decimal(
                day_value.get("primary_total"), label=f"days.{day_key}.primary_total"
            )
            organization = _parse_openai_cost_decimal(
                day_value.get("organization_total"),
                label=f"days.{day_key}.organization_total",
            )
            _validate_openai_money_map(
                day_value.get("projects"), label=f"days.{day_key}.projects"
            )
            line_total = _validate_openai_money_map(
                day_value.get("line_items"), label=f"days.{day_key}.line_items"
            )
            organization_line_items = day_value.get("organization_line_items")
            organization_line_total = (
                _validate_openai_money_map(
                    organization_line_items,
                    label=f"days.{day_key}.organization_line_items",
                )
                if organization_line_items is not None
                else None
            )
            if line_total != primary or (
                organization_line_total is not None
                and organization_line_total != organization
            ):
                raise ValueError(f"cache day {day_key} breakdown totals disagree")
            if scope["kind"] == "organization" and primary != organization:
                raise ValueError(f"cache day {day_key} organization primary total disagrees")
            if scope["kind"] == "project":
                expected_primary = _parse_openai_cost_decimal(
                    day_value["projects"].get(scope["project_id"], "0"),
                    label=f"days.{day_key}.selected_project",
                )
                if primary != expected_primary:
                    raise ValueError(f"cache day {day_key} project primary total disagrees")

            samples = day_value.get("samples")
            if not isinstance(samples, list) or not samples or len(samples) > 96:
                raise ValueError(f"cache day {day_key} has invalid samples")
            previous_sample_time: Optional[datetime] = None
            for sample in samples:
                if not isinstance(sample, dict):
                    raise ValueError(f"cache day {day_key} contains an invalid sample")
                sample_time = _parse_openai_utc(
                    sample.get("fetched_at_utc"), label="sample fetched_at_utc"
                )
                _parse_openai_cost_decimal(
                    sample.get("primary_total"), label="sample primary_total"
                )
                if sample_time > updated_at:
                    raise ValueError(f"cache day {day_key} sample is newer than the cache")
                if previous_sample_time is not None and sample_time < previous_sample_time:
                    raise ValueError(f"cache day {day_key} samples are out of order")
                previous_sample_time = sample_time
            if samples[-1].get("primary_total") != day_value.get("primary_total"):
                raise ValueError(f"cache day {day_key} latest sample disagrees")

        current_date = observed_now.date().isoformat()
        if current_date not in days:
            raise ValueError("cache is missing the current UTC date")
    except (KeyError, TypeError, ValueError, ArithmeticError) as exc:
        return unavailable(f"cache validation failed: {exc}")

    return {
        "available": True,
        "path": str(path),
        "currency": "usd",
        "updated_at_utc": cache["updated_at_utc"],
        "updated_at_display": updated_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "age_seconds": age_seconds,
        "age": _human_snapshot_age(age_seconds),
        "scope": dict(scope),
        "days": days,
        "current_utc_date": current_date,
    }


def local_digest_time_to_utc(value: datetime) -> datetime:
    """Convert the digest's existing host-local naive time to aware UTC."""

    if value.tzinfo is None:
        return value.astimezone(timezone.utc)
    return value.astimezone(timezone.utc)


def _openai_window_text(start: datetime, end: datetime) -> str:
    """Render one requested UTC cost window compactly."""

    if start.date() == end.date():
        return f"{start:%H:%M}–{end:%H:%M} UTC"
    return f"{start:%Y-%m-%d %H:%M}–{end:%Y-%m-%d %H:%M} UTC"


def estimate_openai_cost_window(
    cache: Dict[str, Any],
    *,
    window_start_utc: datetime,
    window_end_utc: datetime,
    now_utc: datetime,
) -> Dict[str, Any]:
    """Estimate a UTC window from cumulative samples without interpolation."""

    start = window_start_utc.astimezone(timezone.utc)
    end = window_end_utc.astimezone(timezone.utc)
    result: Dict[str, Any] = {
        "status": "unknown",
        "method": "cumulative provider-published cost delta",
        "requested_start_utc": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "requested_end_utc": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "requested_window": _openai_window_text(start, end),
        "segments": [],
        "reason": None,
    }
    if end <= start:
        result["reason"] = "selected log window has no positive duration"
        return result

    cursor = start
    total = Decimal(0)
    valid_count = 0
    while cursor < end:
        next_midnight = datetime.combine(
            cursor.date() + timedelta(days=1), time.min, tzinfo=timezone.utc
        )
        segment_end = min(end, next_midnight)
        day_key = cursor.date().isoformat()
        segment: Dict[str, Any] = {
            "utc_date": day_key,
            "requested_start_utc": cursor.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "requested_end_utc": segment_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": "unavailable",
        }
        day = cache.get("days", {}).get(day_key)
        if not isinstance(day, dict):
            segment["reason"] = "required UTC date is missing from the cache"
            result["segments"].append(segment)
            cursor = segment_end
            continue

        day_start = datetime.combine(cursor.date(), time.min, tzinfo=timezone.utc)
        is_complete_closed_day = (
            cursor == day_start
            and segment_end == next_midnight
            and cursor.date() < now_utc.astimezone(timezone.utc).date()
        )
        if is_complete_closed_day:
            amount = _parse_openai_cost_decimal(
                day["primary_total"], label=f"days.{day_key}.primary_total"
            )
            segment.update(
                {
                    "status": "complete",
                    "method": "latest provider-published daily total",
                    "amount": _openai_decimal_text(amount),
                    "sample_start_utc": cursor.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "sample_end_utc": segment_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
                }
            )
            total += amount
            valid_count += 1
            result["segments"].append(segment)
            cursor = segment_end
            continue

        samples: list[tuple[datetime, Decimal]] = [
            (
                _parse_openai_utc(sample["fetched_at_utc"], label="sample time"),
                _parse_openai_cost_decimal(
                    sample["primary_total"], label="sample primary_total"
                ),
            )
            for sample in day.get("samples", [])
        ]
        before = [item for item in samples if item[0] <= cursor]
        after = [item for item in samples if item[0] >= segment_end]
        if not before:
            segment["reason"] = "no cumulative sample at or before segment start"
        else:
            start_sample = max(before, key=lambda item: item[0])
            start_gap = (cursor - start_sample[0]).total_seconds()
            if start_gap > OPENAI_COST_SAMPLE_BOUNDARY_MAX_GAP_SECONDS:
                segment["reason"] = "start boundary sample is too old"
            elif after:
                end_sample = min(after, key=lambda item: item[0])
                end_gap = (end_sample[0] - segment_end).total_seconds()
                if end_gap > OPENAI_COST_SAMPLE_BOUNDARY_MAX_GAP_SECONDS:
                    segment["reason"] = "end boundary sample is too late"
                elif end_sample[1] < start_sample[1]:
                    segment["reason"] = (
                        "provider-published cumulative total changed non-monotonically"
                    )
                else:
                    amount = end_sample[1] - start_sample[1]
                    segment.update(
                        {
                            "status": "complete",
                            "method": result["method"],
                            "amount": _openai_decimal_text(amount),
                            "sample_start_utc": start_sample[0].strftime(
                                "%Y-%m-%dT%H:%M:%SZ"
                            ),
                            "sample_end_utc": end_sample[0].strftime(
                                "%Y-%m-%dT%H:%M:%SZ"
                            ),
                        }
                    )
                    total += amount
                    valid_count += 1
            elif segment_end == end:
                latest_sample = max(
                    (item for item in samples if item[0] <= segment_end),
                    key=lambda item: item[0],
                )
                trailing_gap = (segment_end - latest_sample[0]).total_seconds()
                if (
                    latest_sample[0] <= start_sample[0]
                    or latest_sample[0] <= cursor
                ):
                    segment["reason"] = (
                        "no later cumulative sample before segment end"
                    )
                elif trailing_gap > OPENAI_COST_SAMPLE_BOUNDARY_MAX_GAP_SECONDS:
                    segment["reason"] = (
                        "latest cumulative sample is too old for trailing coverage"
                    )
                elif latest_sample[1] < start_sample[1]:
                    segment["reason"] = (
                        "provider-published cumulative total changed non-monotonically"
                    )
                else:
                    amount = latest_sample[1] - start_sample[1]
                    partial_reason = (
                        "requested segment ends after the latest cumulative sample"
                    )
                    segment.update(
                        {
                            "status": "partial",
                            "method": result["method"],
                            "amount": _openai_decimal_text(amount),
                            "sample_start_utc": start_sample[0].strftime(
                                "%Y-%m-%dT%H:%M:%SZ"
                            ),
                            "sample_end_utc": latest_sample[0].strftime(
                                "%Y-%m-%dT%H:%M:%SZ"
                            ),
                            "trailing_uncovered_seconds": int(trailing_gap),
                            "partial_coverage_reason": partial_reason,
                            "reason": partial_reason,
                        }
                    )
                    total += amount
                    valid_count += 1
            else:
                segment["reason"] = "no cumulative sample at or after segment end"
        result["segments"].append(segment)
        cursor = segment_end

    if all(item.get("status") == "complete" for item in result["segments"]):
        result["status"] = "complete"
        result["amount"] = _openai_decimal_text(total)
    elif valid_count:
        result["status"] = "partial"
        result["amount"] = _openai_decimal_text(total)
        result["reason"] = "one or more UTC-date segments lack boundary coverage"
    else:
        result["reason"] = "no UTC-date segment has suitable boundary coverage"
    return result


def _xai_provider_reported_component(provider_usage: Dict[str, Any]) -> Dict[str, Any]:
    """Return the existing xAI provider-reported component without reconstruction."""

    events = provider_usage.get("events") or []
    values = [
        value
        for item in events
        if item.get("provider", "xAI") == "xAI"
        and (value := optional_int_usage_value(item.get("cost_in_usd_ticks")))
        is not None
    ]
    successful = sum(item.get("provider", "xAI") == "xAI" for item in events)
    if not values:
        return {
            "available": False,
            "successful_calls": successful,
            "costed_calls": 0,
        }
    ticks = sum(values)
    return {
        "available": True,
        "ticks": ticks,
        "amount": _openai_decimal_text(
            Decimal(ticks) / Decimal(USD_TICKS_PER_DOLLAR)
        ),
        "successful_calls": successful,
        "costed_calls": len(values),
        "coverage_complete": len(values) == successful,
    }


def openai_published_cost_report(
    *,
    cache_path: Optional[Path],
    window_start_local: Optional[datetime],
    window_end_local: Optional[datetime],
    generation_time_local: datetime,
    provider_usage: Dict[str, Any],
) -> Dict[str, Any]:
    """Build the digest's offline cache, current-day, and window-cost view."""

    now_utc = local_digest_time_to_utc(generation_time_local)
    cache = load_openai_cost_cache(cache_path, now_utc=now_utc)
    report: Dict[str, Any] = {
        key: value for key, value in cache.items() if key != "days"
    }
    report["current_day"] = {"available": False}
    report["selected_window"] = {
        "status": "unknown",
        "reason": "OpenAI published-cost cache is unavailable",
    }
    if not cache.get("available"):
        return report

    current = cache["days"][cache["current_utc_date"]]
    report["current_day"] = {
        "available": True,
        "utc_date": cache["current_utc_date"],
        "primary_total": current["primary_total"],
        "provisional": True,
        "status": "provisional",
    }
    if window_start_local is not None and window_end_local is not None:
        report["selected_window"] = estimate_openai_cost_window(
            cache,
            window_start_utc=local_digest_time_to_utc(window_start_local),
            window_end_utc=local_digest_time_to_utc(window_end_local),
            now_utc=now_utc,
        )
    else:
        report["selected_window"] = {
            "status": "unknown",
            "reason": "selected log window boundaries are unavailable",
        }

    xai_component = _xai_provider_reported_component(provider_usage)
    report["xai_component"] = xai_component
    if cache["scope"]["kind"] == "project":
        selected = report["selected_window"]
        if selected.get("status") == "complete" and xai_component.get("available"):
            combined = _parse_openai_cost_decimal(
                selected["amount"], label="selected OpenAI cost"
            ) + _parse_openai_cost_decimal(
                xai_component["amount"], label="selected xAI cost"
            )
            report["combined_selected_window"] = {
                "available": True,
                "amount": _openai_decimal_text(combined),
            }
        else:
            report["combined_selected_window"] = {
                "available": False,
                "reason": "one or both provider components are unavailable or partial",
            }
    return report


def xai_usage_stage_from_msg(msg: str) -> str:
    """Return the provider pipeline stage recorded on a usage line."""
    tested = re.match(
        r"^Tested reply stage=([^\s]+)\s+provider=(?:xAI|OpenAI)\s+usage=",
        msg,
    )
    if tested:
        return tested.group(1)
    match = re.match(r"^xAI reply stage=([^\s]+)\s+usage=", msg)
    if match:
        return match.group(1)
    if "xAI usage=" in msg:
        return "legacy_or_unavailable"
    return "unavailable"


def provider_usage_provider_from_msg(msg: str) -> str:
    """Return the provider named by a legacy or tested-pipeline usage line."""
    tested = re.match(
        r"^Tested reply stage=[^\s]+\s+provider=(xAI|OpenAI)\s+usage=",
        msg,
    )
    if tested:
        return tested.group(1)
    if msg.startswith("xAI reply stage=") or "xAI usage=" in msg:
        return "xAI"
    return "unavailable"


def parse_xai_call_start(msg: str) -> Optional[Dict[str, str]]:
    """Parse a structured provider call-start line."""
    tested = re.match(
        r"^Calling tested reply pipeline stage=([^\s]+)\s+"
        r"provider=(xAI|OpenAI)\s+model=([^\s]+)\s+"
        r"reasoning_effort=([^\s]+)",
        msg,
    )
    if tested:
        return {
            "stage": tested.group(1),
            "provider": tested.group(2),
            "model": tested.group(3),
            "reasoning_effort": tested.group(4),
        }
    match = re.match(
        r"^Calling AI-first reply stage=([^\s]+)\s+model=([^\s]+)",
        msg,
    )
    if not match:
        return None
    return {"stage": match.group(1), "model": match.group(2)}


def parse_xai_usage_from_msg(msg: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Parse legacy and AI-first xAI usage messages."""
    tested = re.match(
        r"^Tested reply stage=[^\s]+\s+provider=(?:xAI|OpenAI)\s+usage=(.+)$",
        msg,
    )
    marker = "xAI usage="
    if tested:
        raw = tested.group(1).strip()
    elif marker in msg:
        raw = msg.split(marker, 1)[1].strip()
    elif msg.startswith("xAI reply stage=") and " usage=" in msg:
        raw = msg.split(" usage=", 1)[1].strip()
    else:
        return None, None
    try:
        parsed = ast.literal_eval(raw)
    except Exception as exc:
        return None, f"could not parse xAI usage dictionary: {exc}"
    if not isinstance(parsed, dict):
        return None, f"xAI usage payload was {type(parsed).__name__}, not dict"
    return parsed, None


def xai_usage_context_from_pending(pending_mention: Dict[str, Any], pending_qt: Dict[str, Any]) -> Dict[str, Any]:
    """Return the xAI usage context from pending."""
    mention_seq = pending_mention.get("considered_seq", -1) if pending_mention else -1
    quote_seq = pending_qt.get("considered_seq", -1) if pending_qt else -1
    if pending_qt and quote_seq >= mention_seq:
        return {
            "lane": "quote-tweet",
            "context_id": pending_qt.get("quote_tweet_id", ""),
            "author_id": pending_qt.get("author_id", ""),
        }
    if pending_mention:
        source = str(pending_mention.get("source") or "mention")
        lane = "hot-post" if source == "hot_post_reply" else "mention"
        return {
            "lane": lane,
            "context_id": pending_mention.get("mention_id") or pending_mention.get("hot_post_reply_id") or "",
            "author_id": pending_mention.get("author_id", ""),
        }
    return {"lane": "unknown", "context_id": "", "author_id": ""}


def unknown_xai_usage_context() -> Dict[str, Any]:
    """Return the unknown xAI usage context."""
    return {"lane": "unknown", "context_id": "", "author_id": ""}


def normalise_active_xai_call_attempt(value: Any) -> Optional[Dict[str, Any]]:
    """Return safe resumable metadata for one provider call still awaiting usage."""
    if not isinstance(value, dict) or value.get("usage_observed") is True:
        return None
    stage = str(value.get("stage") or "").strip()
    model = str(value.get("model") or "").strip()
    if not stage or not model:
        return None
    result = {
        "time": str(value.get("time") or ""),
        "lane": normalise_reply_lane(value.get("lane")),
        "context_id": str(value.get("context_id") or ""),
        "author_id": str(value.get("author_id") or ""),
        "stage": stage,
        "model": model,
        "usage_observed": False,
    }
    if value.get("provider") in {"xAI", "OpenAI"}:
        result["provider"] = str(value["provider"])
    if value.get("reasoning_effort"):
        result["reasoning_effort"] = str(value["reasoning_effort"])
    return result


def _cache_input_metric(
    usage: Dict[str, Any],
    prompt_details: Dict[str, Any],
    input_details: Dict[str, Any],
    field_names: Tuple[str, ...],
) -> Optional[int]:
    """Return one explicitly reported cache metric without inventing zero."""
    for container in (usage, prompt_details, input_details):
        for field in field_names:
            if field in container:
                return optional_int_usage_value(container.get(field))
    return None


def summarize_xai_usage_event(
    record: Record,
    usage: Dict[str, Any],
    context: Dict[str, Any],
    *,
    model: str = "",
    provider: str = "xAI",
    call_start_matched: bool = False,
) -> Dict[str, Any]:
    """Summarise one legacy or tested-pipeline provider usage event."""
    prompt_details = usage.get("prompt_tokens_details")
    if not isinstance(prompt_details, dict):
        prompt_details = {}
    completion_details = usage.get("completion_tokens_details")
    if not isinstance(completion_details, dict):
        completion_details = {}
    input_details = usage.get("input_tokens_details")
    if not isinstance(input_details, dict):
        input_details = {}
    cache_read_input = _cache_input_metric(
        usage,
        prompt_details,
        input_details,
        ("cache_read_input_tokens", "cached_tokens"),
    )
    cache_creation_input = _cache_input_metric(
        usage,
        prompt_details,
        input_details,
        ("cache_creation_input_tokens", "cache_creation_tokens"),
    )
    cache_write_input = _cache_input_metric(
        usage,
        prompt_details,
        input_details,
        ("cache_write_input_tokens", "cache_write_tokens"),
    )
    return {
        "time": record.ts.strftime("%Y-%m-%d %H:%M:%S"),
        "lane": context.get("lane", "unknown"),
        "context_id": context.get("context_id", ""),
        "author_id": context.get("author_id", ""),
        "stage": xai_usage_stage_from_msg(record.msg),
        "provider": provider if provider in {"xAI", "OpenAI"} else "unavailable",
        "model": model,
        "call_start_matched": bool(call_start_matched),
        "prompt_tokens": int_usage_value(usage.get("prompt_tokens")),
        # Retain cached_tokens for JSON compatibility. Provider cached-token
        # usage is an input-cache read, not evidence of cache creation/writes.
        "cached_tokens": int_usage_value(cache_read_input),
        "cache_read_input_tokens": int_usage_value(cache_read_input),
        "cache_creation_input_tokens": cache_creation_input,
        "cache_write_input_tokens": cache_write_input,
        "image_tokens": int_usage_value(prompt_details.get("image_tokens")),
        "reasoning_tokens": int_usage_value(completion_details.get("reasoning_tokens")),
        "completion_tokens": int_usage_value(usage.get("completion_tokens")),
        "total_tokens": int_usage_value(usage.get("total_tokens")),
        "num_sources_used": int_usage_value(usage.get("num_sources_used")),
        "cost_in_usd_ticks": optional_int_usage_value(
            usage.get("cost_in_usd_ticks")
        ),
    }


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


def normalise_reply_lane(value: Any) -> str:
    """Return a stable conversational-reply lane label."""
    lane = str(value or "unknown").strip().lower().replace("_", "-")
    if lane == "hot-post-reply":
        return "hot-post"
    return lane or "unknown"


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
        terminal_local_outcome = _terminal_local_rejection_outcome(
            (decision or {}).get("no_reply_reason")
        ) or _terminal_local_rejection_outcome(
            (local_rejection or {}).get("reason")
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


def original_editorial_shadow_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return the original editorial shadow summary."""
    total = len(events)
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


def _count_optional(events: List[Dict[str, Any]], field: str, values: tuple[str, ...]) -> Dict[str, int]:
    counts = Counter({value: 0 for value in values})
    for event in events:
        value = event.get(field)
        key = str(value) if value not in (None, "") else "unavailable"
        counts[key if not values or key in values else "unavailable"] += 1
    return dict(sorted(counts.items()))


_HISTORICAL_CONTEXT_VERIFICATION_LABELS = (
    # Formatter v4 public labels.
    "Exact wording verified",
    "Historically verified variant",
    "Verified excerpt",
    "Attributed, but exact wording not independently verified",
    "Exact wording not independently verified",
    "Research incomplete",
    # Audited internal-rendering label retained by formatter v4.
    "Normalised wording verified",
    # Source-role audit labels emitted by formatter v3 internal metadata.
    "Paraphrase; exact wording not verified",
    "Composite wording assembled from related material",
    "Historically misattributed; not Thatcher wording",
    "Reported wording; no primary Thatcher transcript located",
    "Secondary recollection; no primary Thatcher transcript located",
    "Wording partially supported by a retained source citation; exact wording not independently verified",
    "Historical variant not independently verified by the retained evidence",
    "Exact wording not independently verified by the retained evidence",
    # Frozen labels retained for older structured logs.
    "Exact wording",
    "Normalised wording",
    "Historical paraphrase",
    "Composite wording",
    "Commonly misattributed wording",
    "Exact wording not verified",
    "unavailable",
)
_HISTORICAL_CONTEXT_FORMATTER_VERSIONS = (
    "historical_context_reply_schema_v1",
    "historical_context_reply_schema_v2",
    "historical_context_reply_schema_v3",
    "historical_context_reply_schema_v4",
    "historical_context_reply_schema_v5",
    "unavailable",
)
_HISTORICAL_CONTEXT_SOURCE_ROLE_AUDIT_VERSIONS = (
    "historical-context-source-roles-v2-recovered-citations",
    "historical-context-source-roles-v3",
    "historical-context-source-roles-v4",
    "historical-context-source-roles-v5-independent-review-and-exclusive-counts",
    "historical-context-source-roles-v6-curated-evidence",
    "historical-context-source-roles-v7-curated-source-adjudications",
    "historical-context-source-roles-v8-claim-specific-public-context",
    "historical-context-source-roles-v9-archive-provenance",
    "unavailable",
)
_HISTORICAL_CONTEXT_CONFIDENCE_DIMENSIONS = (
    "attribution",
    "wording",
    "source_event",
    "date",
    "historical_context",
    "interpretation",
)
_HISTORICAL_CONTEXT_CONFIDENCE_VALUES = ("high", "medium", "low", "unknown", "unavailable")


def _historical_context_verification_counts(
    events: List[Dict[str, Any]],
) -> Dict[str, int]:
    """Count controlled labels, grouping source-specific recollection wording."""
    counts = Counter({value: 0 for value in _HISTORICAL_CONTEXT_VERIFICATION_LABELS})
    for event in events:
        value = event.get("verification_label")
        if (
            isinstance(value, str)
            and value.startswith("Reported in ")
            and value.endswith("; no primary Thatcher transcript located")
        ):
            key = "Secondary recollection; no primary Thatcher transcript located"
        elif isinstance(value, str) and value in counts:
            key = value
        else:
            key = "unavailable"
        counts[key] += 1
    return dict(sorted(counts.items()))


def _historical_context_confidence_dimension_counts(
    events: List[Dict[str, Any]],
) -> Dict[str, Dict[str, int]]:
    """Count validated v3/v4 confidence dimensions without flattening them."""
    result: Dict[str, Dict[str, int]] = {}
    for field in _HISTORICAL_CONTEXT_CONFIDENCE_DIMENSIONS:
        counts = Counter({value: 0 for value in _HISTORICAL_CONTEXT_CONFIDENCE_VALUES})
        for event in events:
            dimensions = event.get("confidence_dimensions")
            value = dimensions.get(field) if isinstance(dimensions, dict) else None
            key = value if isinstance(value, str) and value in counts else "unavailable"
            counts[key] += 1
        result[field] = dict(sorted(counts.items()))
    return result


def historical_context_quality_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return the historical context quality summary."""
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
    rendering_context_counts = Counter({
        "concrete_event_or_date_context_included": 0,
        "date_only_qualified_context_included": 0,
        "context_omitted_no_useful_event_or_date": 0,
        "old_generic_fallback_used": 0,
        "rendering_metadata_unavailable": 0,
    })
    generic_fallback = (
        "Context — The surviving attribution does not establish an occasion, "
        "date or immediate historical issue."
    )
    for event in rendered_items:
        preview = str(event.get("reply_preview") or "")
        variant = str(event.get("template_variant") or "")
        if generic_fallback in preview:
            rendering_context_counts["old_generic_fallback_used"] += 1
        elif variant == "compact_generic_context_omitted" or (
            preview and not preview.startswith("Context —")
        ):
            rendering_context_counts["context_omitted_no_useful_event_or_date"] += 1
        elif preview.startswith("Context — The surviving record dates this wording to "):
            rendering_context_counts["date_only_qualified_context_included"] += 1
        elif preview.startswith("Context —"):
            rendering_context_counts["concrete_event_or_date_context_included"] += 1
        else:
            rendering_context_counts["rendering_metadata_unavailable"] += 1
    return {
        "attempted_count": attempted,
        "status_counts": dict(sorted(statuses.items())),
        "skip_reason_counts": dict(skip_reasons.most_common()),
        "verification_counts": _historical_context_verification_counts(rendered_items),
        "source_class_counts": _count_optional(rendered_items, "source_class", (
            "Margaret Thatcher Foundation", "Hansard", "original speech transcript",
            "Thatcher-authored publication", "contemporary interview", "official Conservative publication",
            "other authoritative source", "canonical locator only", "no public URL", "unavailable",
        )),
        "confidence_counts": _count_optional(rendered_items, "historical_confidence", ("high", "medium", "low", "unavailable")),
        "rendering_context_counts": dict(rendering_context_counts),
        "formatter_version_counts": _count_optional(
            rendered_items,
            "formatter_version",
            _HISTORICAL_CONTEXT_FORMATTER_VERSIONS,
        ),
        "rendering_mode_counts": _count_optional(
            rendered_items,
            "rendering_mode",
            ("public", "internal", "unavailable"),
        ),
        "source_role_audit_version_counts": _count_optional(
            rendered_items,
            "source_role_audit_version",
            _HISTORICAL_CONTEXT_SOURCE_ROLE_AUDIT_VERSIONS,
        ),
        "confidence_dimension_counts": _historical_context_confidence_dimension_counts(rendered_items),
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


def _terminal_local_rejection_outcome(reason: Any) -> Optional[str]:
    """Return the terminal local outcome represented by a pipeline reason."""
    normalised = str(reason or "").strip().lower()
    return {
        "exact_duplicate_reply": "terminal_repetition_rejection",
        "near_duplicate_reply": "terminal_repetition_rejection",
        "clarification_not_direct_factual_answer": (
            "terminal_clarification_mode_rejection"
        ),
    }.get(normalised)


def _is_terminal_pipeline_failure(reason: Any, status: Any = None) -> bool:
    """Identify old pipeline terminal failures logged as decision records."""
    normalised = str(reason or "").strip().lower()
    return (
        str(status or "").strip().lower() == "operational_failure"
        or normalised
        in {
            "claim_auditor_detected_unresolved_factual_claim",
            "revision_limit_reached",
        }
    )


def _no_reply_category(value: Any) -> str:
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


def reconcile_reply_pipeline_effective_outcomes(
    events: List[Dict[str, Any]],
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
            reason = str(event.get("no_reply_reason") or "model-selected no_reply")
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
        "deliberately_declined_count": sum(
            event.get("mode") == "no_reply"
            and not _is_terminal_pipeline_failure(
                event.get("reason") or event.get("no_reply_reason"),
                event.get("status"),
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


def quote_image_semantic_veto_category(event: Dict[str, Any]) -> Optional[str]:
    """Return a category for new events and infer one from compatible legacy fields."""
    if event.get("shadow_status") != "veto":
        return None
    category = event.get("veto_category")
    if category == "selection_error_candidate_available":
        return str(category)
    if (
        category == "coverage_gap_no_safe_image"
        and event.get("quote_pair_fully_resolved") is True
    ):
        return str(category)
    if event.get("alternative_available") is True:
        return "selection_error_candidate_available"
    if (
        event.get("quote_has_no_allowed_candidate_globally") is True
        and event.get("quote_pair_fully_resolved") is True
    ):
        return "coverage_gap_no_safe_image"
    return None


def _quote_image_semantic_veto_manifest_key(event: Dict[str, Any]) -> tuple[str, str]:
    return (
        str(event.get("manifest_policy_version") or "unavailable"),
        str(event.get("manifest_sha256") or ""),
    )


def _quote_image_semantic_veto_summary_subset(events: List[Dict[str, Any]]) -> Dict[str, Any]:
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
        if event.get("quote_has_no_allowed_candidate_globally") is True
        and event.get("quote_pair_fully_resolved") is True
        and event.get("quote_id")
    }
    incomplete_quote_ids = {
        str(event.get("quote_id"))
        for event in events
        if event.get("quote_has_incomplete_pair_coverage") is True
        and event.get("quote_id")
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
        "quotes_with_incomplete_pair_coverage": len(incomplete_quote_ids),
        "adjudicated_unknown_selections": sum(
            event.get("selected_pair_adjudication_status") == "adjudicated_unknown"
            for event in events
        ),
        "not_adjudicated_selections": sum(
            event.get("selected_pair_adjudication_status") == "not_adjudicated_missing"
            for event in events
        ),
        "median_alternative_score_delta": statistics.median(deltas) if deltas else None,
        "manifest_policy_version": next((event.get("manifest_policy_version") for event in reversed(events) if event.get("manifest_policy_version")), "unavailable"),
        "manifest_sha256": next((event.get("manifest_sha256") for event in reversed(events) if event.get("manifest_sha256")), ""),
        "lookup_failures": statuses["manifest_unavailable"] + statuses["manifest_stale"],
        "production_selection_change_failures": sum(event.get("production_selection_changed") is not False for event in events),
        "veto_reason_counts": dict(reasons.most_common()),
        "examples": examples,
    }


def quote_image_semantic_veto_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Return the quote image semantic veto summary."""
    grouped: Dict[tuple[str, str], List[Dict[str, Any]]] = {}
    for event in events:
        grouped.setdefault(_quote_image_semantic_veto_manifest_key(event), []).append(event)
    current_key = (
        _quote_image_semantic_veto_manifest_key(events[-1])
        if events else
        ("unavailable", "")
    )
    current_events = grouped.get(current_key, [])
    summary = _quote_image_semantic_veto_summary_subset(current_events)
    summary["manifest_policy_version"] = current_key[0]
    summary["manifest_sha256"] = current_key[1]
    summary.update({
        "window_event_count_all_manifests": len(events),
        "events_excluded_from_current_manifest_summary": len(events) - len(current_events),
        "mixed_manifest_versions": len(grouped) > 1,
        "manifest_strata": [
            {
                "manifest_policy_version": key[0],
                "manifest_sha256": key[1],
                "selection_time_observations": len(rows),
                "status_counts": dict(Counter(
                    str(row.get("shadow_status") or "unknown") for row in rows
                )),
            }
            for key, rows in grouped.items()
        ],
    })
    return summary


def analyse(
    records: List[Record],
    max_text: int = 280,
    *,
    initial_active_xai_context: Optional[Dict[str, Any]] = None,
    initial_active_xai_call_attempt: Optional[Dict[str, Any]] = None,
    initial_pending_mention: Optional[Dict[str, Any]] = None,
    initial_pending_qt: Optional[Dict[str, Any]] = None,
    current_remote_write_safety: Optional[Dict[str, Any]] = None,
    generation_time: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Aggregate parsed production records into digest metrics."""
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
    remote_write_transactions: List[Dict[str, Any]] = []
    x_requests: List[Dict[str, Any]] = []
    latest_x_request_by_source: Dict[str, Dict[str, Any]] = {}
    xai_usage_events: List[Dict[str, Any]] = []
    restored_xai_call_attempt = normalise_active_xai_call_attempt(
        initial_active_xai_call_attempt
    )
    xai_call_attempts: List[Dict[str, Any]] = (
        [restored_xai_call_attempt] if restored_xai_call_attempt else []
    )
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
    active_xai_call_attempt_index: Optional[int] = (
        0 if restored_xai_call_attempt else None
    )
    last_created_post: Dict[str, Any] = {}
    pending_semantic_veto_event: Optional[Dict[str, Any]] = None
    pending_semantic_veto_ts: Optional[datetime] = None

    def semantic_veto_matches_post(
        shadow_event: Dict[str, Any],
        shadow_ts: datetime,
        posted_event: Dict[str, Any],
        posted_ts: datetime,
    ) -> bool:
        elapsed = (posted_ts - shadow_ts).total_seconds()
        if elapsed < 0 or elapsed > 30 * 60:
            return False
        comparisons: List[bool] = []
        for shadow_field, posted_field in (
            ("quote_hash", "quote_hash"),
            ("selected_image_hash", "image_hash"),
            ("selected_image_basename", "image_basename"),
        ):
            shadow_value = str(shadow_event.get(shadow_field) or "")
            posted_value = str(posted_event.get(posted_field) or "")
            if shadow_value and posted_value:
                comparisons.append(shadow_value == posted_value)
        return bool(comparisons) and all(comparisons)

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

    local_rejections_by_identity: Dict[Tuple[str, str], Dict[str, Any]] = {}

    def add_or_merge_local_rejection(
        ts: datetime,
        *,
        lane: Any,
        target_id: Any,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Keep one enriched effective local-rejection record per target."""
        target = str(target_id or "")
        normalised_lane = _normalise_lane(lane)
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
                lane=lane or "unavailable",
                target_id=target,
                **kwargs,
            )
            local_rejections_by_identity[key] = existing
            return existing
        if _normalise_lane(existing.get("lane")) == "unavailable" and normalised_lane != "unavailable":
            existing["lane"] = lane
            local_rejections_by_identity.pop(("unavailable", target), None)
            local_rejections_by_identity[key] = existing
        for field, value in kwargs.items():
            existing_value = existing.get(field)
            if (
                value is not None
                and value != ""
                and (existing_value is None or existing_value == "")
            ):
                existing[field] = short(value, max_text) if isinstance(value, str) else value
        return existing

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

    def conversational_evidence_fields(
        event_obj: Dict[str, Any],
        *,
        evidence_ids: Any,
        factual_claim_count: Any,
    ) -> Dict[str, Any]:
        factual_claim = (
            factual_claim_count > 0
            if type(factual_claim_count) is int
            else None
        )
        confidence = event_obj.get("evidence_confidence")
        if not isinstance(confidence, str) or not confidence:
            confidence = "none" if factual_claim is False else "unavailable"
        supplied_ids = event_obj.get("trusted_fact_ids_supplied")
        has_explicit_supply = isinstance(supplied_ids, list)
        if not has_explicit_supply:
            supplied_ids = list(evidence_ids) if isinstance(evidence_ids, list) else None
        supplied_count = event_obj.get("trusted_facts_supplied_count")
        if type(supplied_count) is not int or supplied_count < 0:
            supplied_count = event_obj.get("retrieved_count")
        if type(supplied_count) is not int or supplied_count < 0:
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
                used_count = len(evidence_ids) if isinstance(evidence_ids, list) else None
        elif not (type(used_count) is int and used_count >= 0):
            used_count = "unknown"
        used_ids = event_obj.get("used_fact_ids") if has_explicit_use else evidence_ids
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

    for record_index, r in enumerate(records):
        msg = r.msg
        production_record = not is_selftest_log_path(r.path)

        request_start = (
            parse_x_request_start(msg)
            if production_record and r.src in {"x_request", "x_bearer_request"}
            else None
        )
        if request_start is not None:
            request_event: Dict[str, Any] = {
                "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                "source": r.src,
                **request_start,
            }
            x_requests.append(request_event)
            latest_x_request_by_source[r.src] = request_event
            stats[f"x_request_endpoint_{request_start['endpoint'].replace('/', '_')}"] += 1

        transaction_event = (
            parse_remote_write_transaction_event(r) if production_record else None
        )
        if transaction_event is not None:
            remote_write_transactions.append(transaction_event)
            stats[
                "remote_write_transaction_"
                + str(transaction_event.get("phase") or "observed")
            ] += 1
            if transaction_event.get("kind") == "main_post_receipt":
                add_receipt_event(
                    "main_post_receipt",
                    r,
                    **{
                        key: value
                        for key, value in transaction_event.items()
                        if key
                        in {
                            "phase",
                            "lane",
                            "attempt_id",
                            "post_id",
                            "path",
                            "disposition",
                        }
                    },
                )

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
            or is_deleted_or_inaccessible_tweet_403(msg)
            or "reply not allowed" in msg.lower()
            or "marking quote tweet as skipped without consuming reply quota" in msg.lower()
            or "not allowed to reply" in msg.lower()
            or "author has restricted who can reply" in msg.lower()
        )
        is_receipt_routine = (
            "Wrote confirmed regular-post receipt pending local reconciliation" in msg
            or "Wrote confirmed meme-post receipt pending local reconciliation" in msg
            or "Wrote confirmed reply receipt pending local reconciliation" in msg
            or "Wrote conversational reply sending receipt" in msg
            or "Promoted conversational reply receipt to confirmed" in msg
            or "Removed conversational reply sending receipt after definite non-success" in msg
            or "Removed conversational reply sending receipt after confirmed identity" in msg
            or "Removed reconciled regular-post receipt" in msg
            or "Removed reconciled meme-post receipt" in msg
            or "Removed reconciled confirmed-reply receipt" in msg
            or "Reconciling confirmed regular quote/image post receipt" in msg
            or "Reconciling confirmed meme post receipt" in msg
            or "Reconciling confirmed reply receipt" in msg
            or "Reconciled confirmed reply receipt before checking" in msg
            or "Reconciled regular quote/image receipt; not creating a second regular post" in msg
            or "Reconciled meme post receipt; not creating a second meme post" in msg
            or "Wrote main-post sending receipt" in msg
            or "Handed confirmed media upload to durable main-post attempt" in msg
            or "Promoted main-post receipt to attempting" in msg
            or "Removed main-post sending receipt" in msg
            or "Promoted main-post attempt to confirmed pending-schedule receipt" in msg
            or "Re-established confirmed pending-schedule receipt durability" in msg
            or "Finalised confirmed pending-schedule receipt" in msg
            or "Wrote confirmed regular pending-schedule receipt" in msg
            or "Finalised regular-post pending schedule" in msg
            or "Promoted regular-post sending receipt to confirmed" in msg
            or "Wrote confirmed meme pending-schedule receipt" in msg
            or "Finalised meme-post pending schedule" in msg
            or "Promoted meme-post sending receipt to confirmed" in msg
            or "Removed conversational reply sending receipt disposition=" in msg
            or "Resumed interrupted exact source-receipt retirement" in msg
            or "Resumed interrupted confirmed-media fence retirement" in msg
            or "Recovered crash-left permanent retirement-ledger exchanges" in msg
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
            or "reply required its durable state fallback" in msg
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
        clarification_mode_refusal = re.search(
            r"Clarification reply lacks direct_factual_answer mode; refusing target_id=(\d+)",
            msg,
        )
        if clarification_mode_refusal is not None:
            add_or_merge_local_rejection(
                r.ts,
                lane=pending_mention.get("source") or "unavailable",
                target_id=clarification_mode_refusal.group(1),
                reason="clarification_not_direct_factual_answer",
                original_local_rejection_reason=(
                    "clarification_not_direct_factual_answer"
                ),
                pipeline_stage_status="approved",
                effective_status="local_rejection",
                effective_reason="clarification_not_direct_factual_answer",
                direct_answer_repair_attempted=False,
                direct_answer_repair_outcome="not_available_legacy_telemetry",
                incoming_contribution=pending_mention.get("incoming_text", ""),
                proposed_draft=None,
                repaired_draft=None,
            )

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
                "_raw_message": msg,
                "_fingerprint": record_fingerprint(r),
            })

        if r.src == "ask_grok_for_reply" and msg.startswith("Asking Grok for reply."):
            active_xai_context = xai_usage_context_from_pending(pending_mention, pending_qt)
        call_start = (
            parse_xai_call_start(msg)
            if r.src in {
                "xai_structured_reply_call",
                "tested_pipeline_structured_call",
            }
            else None
        )
        if call_start is not None:
            active_xai_context = xai_usage_context_from_pending(pending_mention, pending_qt)
            context = active_xai_context or unknown_xai_usage_context()
            attempt_row = {
                "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                "lane": context.get("lane", "unknown"),
                "context_id": context.get("context_id", ""),
                "author_id": context.get("author_id", ""),
                "stage": call_start["stage"],
                "model": call_start["model"],
                "usage_observed": False,
            }
            if call_start.get("provider") in {"xAI", "OpenAI"}:
                attempt_row["provider"] = call_start["provider"]
            if call_start.get("reasoning_effort"):
                attempt_row["reasoning_effort"] = call_start["reasoning_effort"]
            xai_call_attempts.append(attempt_row)
            active_xai_call_attempt_index = len(xai_call_attempts) - 1

        usage, usage_error = parse_xai_usage_from_msg(msg)
        if usage is not None:
            model = ""
            call_start_matched = False
            usage_stage = xai_usage_stage_from_msg(msg)
            usage_provider = provider_usage_provider_from_msg(msg)
            if active_xai_call_attempt_index is not None:
                attempt = xai_call_attempts[active_xai_call_attempt_index]
                if (
                    attempt.get("stage") == usage_stage
                    and str(attempt.get("provider") or "xAI") == usage_provider
                    and normalise_reply_lane(attempt.get("lane"))
                    == normalise_reply_lane(
                        (active_xai_context or {}).get("lane")
                    )
                    and str(attempt.get("context_id") or "")
                    == str(
                        (active_xai_context or {}).get("context_id") or ""
                    )
                ):
                    attempt["usage_observed"] = True
                    attempt["usage_time"] = r.ts.strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                    model = str(attempt.get("model") or "")
                    call_start_matched = True
                    active_xai_call_attempt_index = None
            xai_usage_events.append(
                summarize_xai_usage_event(
                    r,
                    usage,
                    active_xai_context or unknown_xai_usage_context(),
                    model=model,
                    provider=usage_provider,
                    call_start_matched=call_start_matched,
                )
            )
            stats["provider_usage_successes"] += 1
            if usage_provider == "xAI":
                stats["xai_usage_successes"] += 1
            elif usage_provider == "OpenAI":
                stats["openai_usage_successes"] += 1
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
                        "image_hash": event_obj.get("image_hash"),
                        "image_score": event_obj.get("image_score"),
                        "quote_hash": event_obj.get("quote_hash"),
                    })
                    if (
                        pending_semantic_veto_event is not None
                        and pending_semantic_veto_ts is not None
                        and semantic_veto_matches_post(
                            pending_semantic_veto_event,
                            pending_semantic_veto_ts,
                            event_obj,
                            r.ts,
                        )
                    ):
                        pending_semantic_veto_event["confirmed_post"] = True
                        pending_semantic_veto_event["post_id"] = event_obj.get("post_id") or ""
                    pending_semantic_veto_event = None
                    pending_semantic_veto_ts = None
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
                    quote_has_incomplete_pair_coverage=event_obj.get("quote_has_incomplete_pair_coverage"),
                    quote_pair_fully_resolved=event_obj.get("quote_pair_fully_resolved"),
                    selected_pair_adjudication_status=event_obj.get("selected_pair_adjudication_status"),
                    quote_pair_adjudicated_unknown_count=event_obj.get("quote_pair_adjudicated_unknown_count"),
                    quote_pair_not_adjudicated_count=event_obj.get("quote_pair_not_adjudicated_count"),
                    manifest_policy_version=event_obj.get("manifest_policy_version") or "unavailable",
                    manifest_sha256=event_obj.get("manifest_sha256") or "",
                    lookup_latency_ms=event_obj.get("lookup_latency_ms"),
                    production_selection_changed=event_obj.get("production_selection_changed"),
                    confirmed_post=False,
                )
                quote_image_semantic_veto_events.append(pending_semantic_veto_event)
                pending_semantic_veto_ts = r.ts
            elif event_obj and event_obj.get("event") == "historical_context_semantic_gate":
                add_event(
                    "historical_context_semantic_gate",
                    r.ts,
                    status=event_obj.get("status") or "unavailable",
                    policy_version=event_obj.get("policy_version") or "unavailable",
                    ledger_sha256=event_obj.get("ledger_sha256") or "",
                    projection_sha256=event_obj.get("projection_sha256") or "",
                    blocked_quote_count=event_obj.get("blocked_quote_count"),
                    reason=event_obj.get("reason") or "",
                )
            elif event_obj and event_obj.get("event") == "historical_context_runtime":
                status = str(event_obj.get("status") or "unavailable")
                add_event(
                    "historical_context_runtime",
                    r.ts,
                    status=status,
                    reason=event_obj.get("reason") or "",
                    regular_post_eligibility_unchanged=event_obj.get(
                        "regular_post_eligibility_unchanged"
                    ),
                )
                stats[f"historical_context_runtime_status_{status}"] += 1
            elif event_obj and event_obj.get("event") == "reply_evidence_unavailable":
                lane = str(event_obj.get("lane") or "unavailable")
                add_event(
                    "reply_evidence_unavailable",
                    r.ts,
                    lane=lane,
                    target_id=event_obj.get("target_id") or "",
                )
                stats[f"reply_evidence_unavailable_lane_{lane}"] += 1
            elif event_obj and event_obj.get("event") == "runtime_control_pause":
                lanes = event_obj.get("lanes")
                add_event(
                    "runtime_control_pause",
                    r.ts,
                    key=event_obj.get("key") or "unavailable",
                    lanes=", ".join(str(item) for item in lanes)
                    if isinstance(lanes, list)
                    else "",
                    until_epoch=event_obj.get("until_epoch"),
                )
                stats["runtime_control_pause"] += 1
            elif event_obj and event_obj.get("event") == "clarification_reply_cap_override":
                add_event(
                    "clarification_reply_cap_override",
                    r.ts,
                    target_id=event_obj.get("target_id") or "",
                    thread_id=event_obj.get("thread_id") or "",
                    author_id=event_obj.get("author_id") or "",
                    bypassed_cap=event_obj.get("bypassed_cap") or "",
                )
                stats["clarification_reply_cap_override"] += 1
            elif event_obj and event_obj.get("event") == "clarification_reply_used":
                add_event(
                    "clarification_reply_used",
                    r.ts,
                    target_id=event_obj.get("target_id") or "",
                    thread_id=event_obj.get("thread_id") or "",
                    author_id=event_obj.get("author_id") or "",
                    reply_post_id=event_obj.get("reply_post_id") or "",
                    trigger=event_obj.get("trigger") or "",
                )
                stats["clarification_reply_used"] += 1
            elif event_obj and event_obj.get("event") == "repair_reply_completed":
                add_event(
                    "repair_reply_completed",
                    r.ts,
                    target_id=event_obj.get("target_id") or "",
                    thread_id=event_obj.get("thread_id") or "",
                    author_id=event_obj.get("author_id") or "",
                    reply_post_id=event_obj.get("reply_post_id") or "",
                )
                stats["repair_reply_completed"] += 1
            elif event_obj and event_obj.get("event") in {
                "mention_backlog_started",
                "mention_backlog_progress",
                "mention_backlog_completed",
                "mention_backlog_reset",
            }:
                kind = str(event_obj["event"])
                add_event(
                    kind,
                    r.ts,
                    since_id=event_obj.get("since_id"),
                    pages_completed=event_obj.get("pages_completed"),
                    highest_mention_id=event_obj.get("highest_mention_id"),
                    continuation_token_present=event_obj.get(
                        "continuation_token_present"
                    ),
                    backlog_age_seconds=event_obj.get("backlog_age_seconds"),
                    reason=event_obj.get("reason") or "",
                )
                stats[kind] += 1
            elif event_obj and event_obj.get("event") in {
                "author_evaluation_quarantine_started",
                "author_evaluation_quarantine_skip",
                "author_evaluation_quarantine_expired",
            }:
                kind = str(event_obj["event"])
                add_event(
                    kind,
                    r.ts,
                    author_id=event_obj.get("author_id") or "",
                    target_id=event_obj.get("target_id") or "",
                    strike_count=event_obj.get("strike_count"),
                    quarantine_until_epoch=event_obj.get(
                        "quarantine_until_epoch"
                    ),
                    pipeline_evaluations_skipped=event_obj.get(
                        "pipeline_evaluations_skipped"
                    ),
                )
                stats[kind] += 1
            elif event_obj and event_obj.get("event") == "historical_context_reply":
                status = str(event_obj.get("status") or "unknown")
                confidence_dimensions = event_obj.get("confidence_dimensions")
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
                    formatter_version=event_obj.get("formatter_version") or "unavailable",
                    rendering_mode=event_obj.get("rendering_mode") or "unavailable",
                    confidence_dimensions=(
                        confidence_dimensions if isinstance(confidence_dimensions, dict) else None
                    ),
                    source_role_audit_version=(
                        event_obj.get("source_role_audit_version") or "unavailable"
                    ),
                    template_variant=event_obj.get("template_variant") or "",
                    shortening_applied=event_obj.get("shortening_applied"),
                    meaning_omitted=event_obj.get("meaning_omitted"),
                    source_omitted=event_obj.get("source_omitted"),
                    verification_omitted=event_obj.get("verification_omitted"),
                    reason=event_obj.get("reason") or "",
                    semantic_review_disposition=(
                        event_obj.get("semantic_review_disposition") or "unavailable"
                    ),
                    semantic_review_ledger_sha256=(
                        event_obj.get("semantic_review_ledger_sha256") or "unavailable"
                    ),
                    semantic_review_projection_sha256=(
                        event_obj.get("semantic_review_projection_sha256")
                        or "unavailable"
                    ),
                    reply_preview=event_obj.get("reply_preview") or "",
                )
                stats[f"historical_context_reply_status_{status}"] += 1
            elif event_obj and event_obj.get("event") == "posting_transaction_state":
                context_state = str(
                    event_obj.get("context_reply_state") or "unavailable"
                )
                add_event(
                    "posting_transaction_state",
                    r.ts,
                    parent_post_id=event_obj.get("parent_post_id") or "",
                    main_post_state=event_obj.get("main_post_state") or "unavailable",
                    context_reply_state=context_state,
                    context_state_persisted=event_obj.get(
                        "context_state_persisted"
                    ),
                    reason=event_obj.get("reason") or "",
                )
                stats[f"context_transaction_state_{context_state}"] += 1
            elif event_obj and event_obj.get("event") == "historical_context_obligation":
                context_state = str(
                    event_obj.get("context_reply_state") or "unavailable"
                )
                status = str(event_obj.get("status") or "unknown")
                add_event(
                    "historical_context_obligation",
                    r.ts,
                    status=status,
                    parent_post_id=event_obj.get("parent_post_id") or "",
                    context_reply_state=context_state,
                    attempt_number=event_obj.get("attempt_number"),
                    remote_work_repeated=event_obj.get("remote_work_repeated"),
                    error_type=event_obj.get("error_type") or "",
                    reason=event_obj.get("reason") or "",
                )
                stats[f"context_obligation_state_{context_state}"] += 1
                stats[f"context_obligation_status_{status}"] += 1
            elif event_obj and event_obj.get("event") == "historical_context_outbox":
                status = str(event_obj.get("status") or "unknown")
                add_event(
                    "historical_context_outbox",
                    r.ts,
                    status=status,
                    parent_post_id=event_obj.get("parent_post_id") or "",
                    error_type=event_obj.get("error_type") or "",
                    reason=event_obj.get("reason") or "",
                    main_post_success_preserved=event_obj.get(
                        "main_post_success_preserved"
                    ),
                    unrelated_lanes_available=event_obj.get(
                        "unrelated_lanes_available"
                    ),
                )
                stats[f"historical_context_outbox_status_{status}"] += 1
            elif event_obj and event_obj.get("event") == "daily_meme_failure":
                stage = str(event_obj.get("stage") or "unavailable")
                add_event(
                    "daily_meme_failure",
                    r.ts,
                    status=event_obj.get("status") or "failed",
                    stage=stage,
                    post_id=event_obj.get("post_id") or "",
                    error_type=event_obj.get("error_type") or "",
                    reason=event_obj.get("reason") or "",
                )
                stats[f"daily_meme_failure_stage_{stage}"] += 1
            elif event_obj and event_obj.get("event") == "reply_strategy_decision":
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
                        len(retrieved_ids)
                        if isinstance(retrieved_ids, list)
                        else None
                    )
                evidence_fields["grounded"] = event_obj.get("grounded")
                decision_event = add_event(
                    "reply_strategy_decision",
                    r.ts,
                    lane=event_obj.get("lane") or "unavailable",
                    target_id=event_obj.get("target_id") or "",
                    mode=event_obj.get("mode"),
                    humour_tone=event_obj.get("humour_tone"),
                    tone=event_obj.get("humour_tone"),
                    **evidence_fields,
                    no_reply_reason=event_obj.get("no_reply_reason"),
                )
            elif event_obj and event_obj.get("event") == "reply_strategy_outcome":
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
                        len(retrieved_ids)
                        if isinstance(retrieved_ids, list)
                        else None
                    )
                evidence_fields["grounded"] = event_obj.get("grounded")
                add_event(
                    "reply_strategy_outcome", r.ts,
                    status=event_obj.get("status") or "confirmed",
                    lane=event_obj.get("lane") or "unavailable",
                    target_id=event_obj.get("target_id") or "",
                    reply_post_id=event_obj.get("reply_post_id") or "",
                    mode=event_obj.get("mode"),
                    final_reply_kind=event_obj.get("final_reply_kind"),
                    humour_tone=event_obj.get("humour_tone"),
                    tone=event_obj.get("humour_tone"),
                    **evidence_fields,
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
            elif event_obj and event_obj.get("event") == "ai_reply_pipeline_decision":
                evidence_ids = event_obj.get("evidence_ids")
                factual_claim_count = event_obj.get("factual_claim_count")
                evidence_fields = conversational_evidence_fields(
                    event_obj,
                    evidence_ids=evidence_ids,
                    factual_claim_count=factual_claim_count,
                )
                decision_status = str(event_obj.get("status") or "")
                final_reply_kind = event_obj.get("final_reply_kind")
                effective_mode = (
                    event_obj.get("mode")
                    or ("no_reply" if decision_status == "no_reply" else None)
                )
                add_event(
                    "reply_strategy_decision",
                    r.ts,
                    lane=event_obj.get("lane") or "unavailable",
                    target_id=event_obj.get("target_id") or "",
                    strategy_version=event_obj.get("strategy_version") or "unavailable",
                    status=decision_status or "unavailable",
                    mode=effective_mode,
                    proposer_mode=event_obj.get("proposer_mode") or event_obj.get("mode"),
                    final_reply_kind=final_reply_kind,
                    reply_requirement=event_obj.get("reply_requirement"),
                    route_source=event_obj.get("route_source"),
                    claim_risk_categories=(
                        [str(value) for value in event_obj.get("claim_risk_categories") if str(value)]
                        if isinstance(event_obj.get("claim_risk_categories"), list)
                        else []
                    ),
                    humour_tone=event_obj.get("tone"),
                    tone=event_obj.get("tone"),
                    **evidence_fields,
                    no_reply_reason=event_obj.get("reason"),
                    reason=event_obj.get("reason"),
                    reviewer_verdict=event_obj.get("reviewer_verdict"),
                    model_call_count=event_obj.get("model_call_count"),
                    revision_count=event_obj.get("revision_count"),
                    author_quarantine_evidence=event_obj.get(
                        "author_quarantine_evidence"
                    ),
                    pipeline_stage_status=(
                        event_obj.get("pipeline_stage_status")
                        or decision_status
                        or "unavailable"
                    ),
                    effective_status=event_obj.get("effective_status"),
                    effective_reason=event_obj.get("effective_reason"),
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
                if event_obj.get("effective_status") == "local_rejection":
                    add_or_merge_local_rejection(
                        r.ts,
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
            elif event_obj and event_obj.get("event") == "ai_reply_pipeline_stage_summary":
                raw_provider_counts = event_obj.get("provider_call_counts")
                provider_call_counts = {
                    provider: count
                    for provider in ("xAI", "OpenAI")
                    if isinstance(raw_provider_counts, dict)
                    and type(count := raw_provider_counts.get(provider)) is int
                    and count >= 0
                }
                schema_invalid_stages = event_obj.get("schema_invalid_stages")
                allegation_categories = event_obj.get(
                    "allegation_conspiracy_categories"
                )
                claim_risk_categories = event_obj.get("claim_risk_categories")
                raw_claim_outcomes = event_obj.get("claim_audit_outcomes")
                claim_audit_outcomes = [
                    {
                        "stage": str(item.get("stage") or ""),
                        "outcome": str(item.get("outcome") or ""),
                    }
                    for item in (
                        raw_claim_outcomes
                        if isinstance(raw_claim_outcomes, list)
                        else []
                    )
                    if isinstance(item, dict)
                    and item.get("stage")
                    and item.get("outcome")
                ]
                majority_review = normalise_majority_review_telemetry(
                    event_obj
                )
                add_event(
                    "reply_pipeline_stage_summary",
                    r.ts,
                    lane=event_obj.get("lane") or "unavailable",
                    target_id=event_obj.get("target_id") or "",
                    strategy_version=event_obj.get("strategy_version") or "unavailable",
                    status=event_obj.get("status") or "unavailable",
                    pipeline_stage_status=(
                        event_obj.get("pipeline_stage_status")
                        or event_obj.get("status")
                        or "unavailable"
                    ),
                    pipeline_stage_reason=(
                        event_obj.get("terminal_reason") or ""
                    ),
                    terminal_reason=event_obj.get("terminal_reason") or "",
                    effective_status=event_obj.get("effective_status"),
                    effective_reason=event_obj.get("effective_reason"),
                    original_local_rejection_reason=event_obj.get(
                        "original_local_rejection_reason"
                    ),
                    direct_answer_repair_attempted=event_obj.get(
                        "direct_answer_repair_attempted"
                    ),
                    direct_answer_repair_outcome=event_obj.get(
                        "direct_answer_repair_outcome"
                    ),
                    model_call_count=event_obj.get("model_call_count"),
                    revision_count=event_obj.get("revision_count"),
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
                    reply_requirement=event_obj.get("reply_requirement"),
                    route_source=event_obj.get("route_source"),
                    trusted_facts_supplied_count=event_obj.get(
                        "trusted_facts_supplied_count"
                    ),
                    trusted_fact_ids_supplied=(
                        [str(value) for value in event_obj.get("trusted_fact_ids_supplied") if str(value)]
                        if isinstance(event_obj.get("trusted_fact_ids_supplied"), list)
                        else []
                    ),
                    schema_invalid_stages=(
                        [str(value) for value in schema_invalid_stages if str(value)]
                        if isinstance(schema_invalid_stages, list)
                        else []
                    ),
                    deterministic_suppressed=event_obj.get("deterministic_suppressed"),
                    deterministic_reason=event_obj.get("deterministic_reason"),
                    xai_gate_decision=event_obj.get("xai_gate_decision"),
                    reply_necessity_outcome=event_obj.get("reply_necessity_outcome"),
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
                    group_hostility_candidate=event_obj.get("group_hostility_candidate"),
                    group_hostility_outcome=event_obj.get("group_hostility_outcome"),
                    allegation_conspiracy_candidate=event_obj.get("allegation_conspiracy_candidate"),
                    allegation_conspiracy_categories=(
                        [str(value) for value in allegation_categories if str(value)]
                        if isinstance(allegation_categories, list)
                        else []
                    ),
                    allegation_conspiracy_outcome=event_obj.get("allegation_conspiracy_outcome"),
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
                    attribution_route=event_obj.get("attribution_route"),
                    attribution_reply_requirement=event_obj.get("attribution_reply_requirement"),
                    authentication_outcome=event_obj.get("authentication_outcome"),
                    claim_risk_categories=(
                        [str(value) for value in claim_risk_categories if str(value)]
                        if isinstance(claim_risk_categories, list)
                        else []
                    ),
                    claim_audit_outcomes=claim_audit_outcomes,
                    claim_cleanup_called=event_obj.get("claim_cleanup_called"),
                    exact_duplicate_detected=event_obj.get("exact_duplicate_detected"),
                    near_duplicate_count=(
                        event_obj.get("near_duplicate_count")
                        if type(event_obj.get("near_duplicate_count")) is int
                        and event_obj.get("near_duplicate_count") >= 0
                        else None
                    ),
                    duplicate_repair_called=event_obj.get("duplicate_repair_called"),
                    duplicate_repair_outcome=event_obj.get("duplicate_repair_outcome"),
                    final_validation=event_obj.get("final_validation"),
                )
            elif event_obj and event_obj.get("event") == "ai_reply_pipeline_effective_outcome":
                if event_obj.get("effective_status") == "local_rejection":
                    add_or_merge_local_rejection(
                        r.ts,
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
            elif event_obj and event_obj.get("event") == "ai_reply_pipeline_failure":
                add_event(
                    "reply_strategy_failure",
                    r.ts,
                    status=event_obj.get("status") or "operational_failure",
                    lane=event_obj.get("lane") or "unavailable",
                    target_id=event_obj.get("target_id") or "",
                    strategy_version=event_obj.get("strategy_version") or "unavailable",
                    reason=event_obj.get("reason") or "unknown_pipeline_failure",
                    model_call_count=event_obj.get("model_call_count"),
                    revision_count=event_obj.get("revision_count"),
                    author_quarantine_evidence=event_obj.get(
                        "author_quarantine_evidence"
                    ),
                )
            elif event_obj and event_obj.get("event") == "ai_reply_pipeline_outcome":
                evidence_ids = event_obj.get("evidence_ids")
                factual_claim_count = event_obj.get("factual_claim_count")
                evidence_fields = conversational_evidence_fields(
                    event_obj,
                    evidence_ids=evidence_ids,
                    factual_claim_count=factual_claim_count,
                )
                add_event(
                    "reply_strategy_outcome",
                    r.ts,
                    status=event_obj.get("status") or "confirmed",
                    lane=event_obj.get("lane") or "unavailable",
                    target_id=event_obj.get("target_id") or "",
                    reply_post_id=event_obj.get("reply_post_id") or "",
                    strategy_version=event_obj.get("strategy_version") or "unavailable",
                    mode=event_obj.get("mode"),
                    final_reply_kind=event_obj.get("final_reply_kind"),
                    reply_requirement=event_obj.get("reply_requirement"),
                    route_source=event_obj.get("route_source"),
                    claim_risk_categories=(
                        [str(value) for value in event_obj.get("claim_risk_categories") if str(value)]
                        if isinstance(event_obj.get("claim_risk_categories"), list)
                        else []
                    ),
                    humour_tone=event_obj.get("tone"),
                    tone=event_obj.get("tone"),
                    **evidence_fields,
                    reviewer_verdict=event_obj.get("reviewer_verdict"),
                    model_call_count=event_obj.get("model_call_count"),
                    revision_count=event_obj.get("revision_count"),
                    failure_reason=event_obj.get("failure_reason") or "",
                )
            elif event_obj and event_obj.get("event") == "quote_pagination_repeated_token":
                add_event(
                    "quote_pagination_repeated_token",
                    r.ts,
                    post_id=event_obj.get("post_id") or "",
                    token_fingerprint=event_obj.get("token_fingerprint") or "",
                    pages_completed=event_obj.get("pages_completed"),
                    results_retained=event_obj.get("results_retained"),
                )
                stats["quote_pagination_repeated_token"] += 1
            elif event_obj and event_obj.get("event") == "candidate_skipped":
                add_event(
                    "candidate_skipped", r.ts,
                    lane=event_obj.get("lane") or "unavailable",
                    target_id=event_obj.get("id") or "",
                    reason=event_obj.get("reason") or "other",
                )
            continue

        quote_success = re.match(
            r"Fetched \d+ quote tweet\(s\) for post_id=(\d+)$",
            msg,
        )
        if quote_success:
            add_event(
                "quote_lane_activity_succeeded",
                r.ts,
                post_id=quote_success.group(1),
            )
            add_event("x_activity_succeeded", r.ts, activity="quote_lookup")
            continue
        if re.match(r"Fetched \d+ mentions$", msg):
            add_event("x_activity_succeeded", r.ts, activity="mention_lookup")
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
        m = re.search(
            r"Wrote conversational reply sending receipt"
            r" source=([^\s]+) target_id=([^\s]+)",
            msg,
        )
        if m:
            add_confirmed_reply_receipt_event(
                "sending",
                r,
                lane=m.group(1),
                target_id=m.group(2),
            )
            continue
        m = re.search(
            r"Promoted conversational reply receipt to confirmed"
            r" source=([^\s]+) target_id=([^\s]+) reply_post_id=([^\s]+)",
            msg,
        )
        if m:
            add_confirmed_reply_receipt_event(
                "promoted",
                r,
                lane=m.group(1),
                target_id=m.group(2),
                reply_post_id=m.group(3),
            )
            continue
        m = re.search(
            r"Removed conversational reply sending receipt after definite "
            r"non-success source=([^\s]+) target_id=([^\s]+)",
            msg,
        )
        if m:
            add_confirmed_reply_receipt_event(
                "sending_removed",
                r,
                lane=m.group(1),
                target_id=m.group(2),
                disposition="definite_non_success",
            )
            continue
        m = re.search(
            r"Removed conversational reply sending receipt after confirmed identity "
            r"was preserved in canonical state source=([^\s]+) target_id=([^\s]+)",
            msg,
        )
        if m:
            add_confirmed_reply_receipt_event(
                "confirmed_state_fallback_removed",
                r,
                lane=m.group(1),
                target_id=m.group(2),
                disposition="confirmed_state_fallback",
            )
            continue
        m = re.search(r"Removed reconciled regular-post receipt:\s*(.+)$", msg)
        if m:
            add_receipt_event(
                "regular_removed",
                r,
                lane="quote_image",
                path=m.group(1).strip(),
            )
            continue
        m = re.search(r"Removed reconciled meme-post receipt:\s*(.+)$", msg)
        if m:
            add_receipt_event(
                "meme_removed",
                r,
                lane="daily_meme",
                path=m.group(1).strip(),
            )
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
            request_context = latest_x_request_by_source.get(r.src)
            if request_context is not None:
                try:
                    request_time = parse_dt(str(request_context.get("time") or ""))
                except ValueError:
                    request_time = None
                if request_time is None or seconds_between(request_time, r.ts) > 300:
                    request_context = None
            endpoint = (
                str(request_context.get("endpoint") or "unknown")
                if request_context is not None
                else "quote_tweets"
                if service == "X bearer"
                else "unknown_oauth"
            )
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
                "request_method": (
                    request_context.get("method") if request_context else ""
                ),
                "request_url": (
                    request_context.get("url") if request_context else ""
                ),
            }
            if request_context is not None:
                request_context["status"] = status_code
                request_context["failed"] = True
            if status_code == "403" and is_deleted_or_inaccessible_tweet_403(msg):
                api_error["restriction_kind"] = "deleted_or_inaccessible_tweet"
                endpoint = "post/reply"
                api_error["endpoint"] = endpoint
                handled_api_restrictions.append(api_error)
                continue
            if status_code == "403" and is_handled_reply_restriction:
                api_error["restriction_kind"] = "reply_target_eligibility"
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
    for event in remote_write_transactions:
        if event.get("kind") != "media_upload" or event.get("phase") != "ambiguous":
            continue
        incident_time = parse_dt(str(event.get("time") or ""))
        later_tweet_create = any(
            request.get("endpoint") == "tweet/create"
            and (
                incident_time is None
                or (
                    (parse_dt(str(request.get("time") or "")) or incident_time)
                    >= incident_time
                )
            )
            for request in x_requests
        )
        reconciliation_archive = (
            (current_remote_write_safety or {}).get("reconciliation_archive")
            or {}
        )
        media_reconciliations = (
            reconciliation_archive.get("media_reconciliations") or []
        )
        if not media_reconciliations:
            latest_media_reconciliation = reconciliation_archive.get(
                "latest_media_reconciliation"
            )
            media_reconciliations = (
                [latest_media_reconciliation]
                if latest_media_reconciliation
                else []
            )
        matching_media_reconciliations = [
            item
            for item in media_reconciliations
            if type(item.get("archived_at_epoch")) is int
            and incident_time is not None
            and item["archived_at_epoch"] >= int(incident_time.timestamp())
            and item.get("image_basename") == event.get("image")
        ]
        reconciled = bool(
            (current_remote_write_safety or {}).get(
                "media_reconciliation_proven"
            )
            and (current_remote_write_safety or {}).get("blocking") is False
            and matching_media_reconciliations
        )
        media_upload_incidents.append(
            {
                "time": event.get("time"),
                "status": "reconciled" if reconciled else "blocked",
                "media": event.get("image") or "",
                "v2_failure": event.get("message") or "",
                "fallback": "legacy fallback prohibited by receipt-bound v2 protocol",
                "v1_result": "not applicable",
                "post_result": (
                    "tweet-create request observed"
                    if later_tweet_create
                    else "no tweet-create request observed"
                ),
                "summary": (
                    "ambiguous receipt-bound media upload was durably reconciled offline"
                    if reconciled
                    else "ambiguous receipt-bound media upload remains blocked"
                ),
                "protocol": "receipt_bound_v2",
            }
        )
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

    pending_sending_lifecycle: Dict[
        Tuple[str, str], List[Dict[str, Any]]
    ] = {}
    pending_reconciliations: List[
        Tuple[Tuple[str, str, str], Dict[str, Any]]
    ] = []

    def clear_latest_reconciliation(
        *,
        identity: Tuple[str, str, str] | None = None,
        lane: str | None = None,
    ) -> Tuple[str, str, str] | None:
        for index in range(len(pending_reconciliations) - 1, -1, -1):
            candidate_identity, _item = pending_reconciliations[index]
            if identity is not None and candidate_identity != identity:
                continue
            if lane is not None and candidate_identity[0] != lane:
                continue
            pending_reconciliations.pop(index)
            return candidate_identity
        return None

    for item in confirmed_reply_receipts:
        sending_identity = (
            str(item.get("lane") or ""),
            str(item.get("target_id") or ""),
        )
        identity = (
            *sending_identity,
            str(item.get("reply_post_id") or ""),
        )
        kind = str(item.get("kind") or "")
        if kind == "sending":
            pending_sending_lifecycle.setdefault(sending_identity, []).append(item)
        elif kind in {
            "promoted",
            "sending_removed",
            "confirmed_state_fallback_removed",
        }:
            pending_for_identity = pending_sending_lifecycle.get(
                sending_identity, []
            )
            if pending_for_identity:
                pending_for_identity.pop()
        if kind == "reconciled":
            pending_reconciliations.append((identity, item))
        elif kind == "removed":
            clear_latest_reconciliation(identity=identity)
        elif kind in {
            "replay_suppressed_mention_check",
            "replay_suppressed_quote_tweet_check",
        }:
            clear_latest_reconciliation(lane=sending_identity[0])
    for identity, pending_events in sorted(pending_sending_lifecycle.items()):
        for source in pending_events:
            raw_message = (
                "Unresolved conversational reply sending receipt remains at the end "
                f"of the observed window lane={identity[0]} target_id={identity[1]}"
            )
            errors.append(
                {
                    "time": str(source.get("time") or ""),
                    "level": "CRITICAL",
                    "where": "confirmed_reply_receipt_lifecycle",
                    "message": raw_message,
                    "_raw_message": raw_message,
                }
            )
    for identity, source in pending_reconciliations:
        raw_message = (
            "Unresolved confirmed reply receipt reconciliation remains at the "
            "end of the observed window "
            f"lane={identity[0]} target_id={identity[1]} "
            f"reply_post_id={identity[2]}"
        )
        errors.append(
            {
                "time": str(source.get("time") or ""),
                "level": "CRITICAL",
                "where": "confirmed_reply_receipt_lifecycle",
                "message": raw_message,
                "_raw_message": raw_message,
            }
        )

    error_health = summarise_operational_error_health(
        errors,
        events,
        receipt_events,
        lifecycle,
        current_remote_write_safety=current_remote_write_safety,
        generation_time=generation_time,
    )
    durably_reconciled_reply_receipts: List[Dict[str, Any]] = []
    for incident in error_health.get("historical_resolved_incidents") or []:
        if incident.get("category") != "remote_write_ambiguity_barrier":
            continue
        resolution_time = str(incident.get("resolution_time") or "")
        try:
            resolved_at = parse_dt(resolution_time)
        except ValueError:
            continue
        for receipt_event in incident.get("correlated_reply_receipt_events") or []:
            if not isinstance(receipt_event, dict):
                continue
            source_time = str(receipt_event.get("source_time") or "")
            try:
                source_at = parse_dt(source_time)
            except ValueError:
                continue
            if source_at > resolved_at:
                continue
            durably_reconciled_reply_receipts.append(
                {
                    "lane": str(receipt_event.get("lane") or ""),
                    "target_id": str(receipt_event.get("target_id") or ""),
                    "source_time": source_time,
                    "resolution_time": resolution_time,
                    "resolution_reason": incident.get("resolution_reason"),
                }
            )

    # Build a short automatic headline around current health, not raw traceback volume.
    headline = []
    headline.append(plural_count(stats.get("quote_image_posted", 0), "quote/image post"))
    headline.append(plural_count(stats.get("daily_meme_posted", 0), "daily meme"))
    headline.append(plural_count(stats.get("mention_reply_posted", 0), "mention reply", "mention replies"))
    headline.append(plural_count(stats.get("hot_post_reply_posted", 0), "hot-post reply", "hot-post replies"))
    headline.append(plural_count(stats.get("quote_tweet_reply_posted", 0), "quote-tweet reply", "quote-tweet replies"))
    headline.append(
        plural_count(
            stats.get("historical_context_reply_status_completed", 0),
            "historical-context reply",
            "historical-context replies",
        )
        + " completed"
    )
    headline.append(
        plural_count(
            stats.get("mention_grok_skip", 0)
            + stats.get("hot_post_reply_grok_skip", 0)
            + stats.get("quote_tweet_grok_skip", 0),
            "Grok skip",
        )
    )
    current_incidents = int(error_health["current_independent_incident_count"])
    resolved_incidents = int(error_health["historical_resolved_incident_count"])
    transient_provider_timeouts = int(
        error_health.get("transient_provider_timeout_count", 0)
    )
    if current_incidents:
        headline.append(
            "current health: "
            + plural_count(
                current_incidents,
                "unresolved operational incident",
            )
        )
    else:
        headline.append("current health: no unresolved operational incidents")
    if transient_provider_timeouts:
        headline.append(
            f"{plural_count(transient_provider_timeouts, 'transient provider timeout')} "
            "observed (provider recovery unverified)"
        )
    non_transient_resolved_incidents = resolved_incidents
    if non_transient_resolved_incidents:
        headline.append(
            plural_count(
                non_transient_resolved_incidents,
                "historical/resolved incident",
            )
            + " in window"
        )
    safety = current_remote_write_safety or {}
    if safety.get("configured") is True and safety.get("available") is True:
        safety_status = str(safety.get("status") or "unavailable")
        if safety.get("blocking") is True:
            headline.append("remote-write safety: BLOCKED")
        elif safety_status == "paused_fail_closed_control":
            headline.append("remote writes fail-closed by invalid control")
        elif safety_status == "operator_paused":
            headline.append("remote writes operator-paused")
        else:
            headline.append("remote-write safety ready")
    if handled_api_restrictions:
        deleted_incidents = {
            (
                str(item.get("service") or ""),
                str(item.get("target_id") or item.get("message") or ""),
            )
            for item in handled_api_restrictions
            if item.get("restriction_kind") == "deleted_or_inaccessible_tweet"
        }
        other_handled_incidents = {
            (
                str(item.get("service") or ""),
                str(item.get("status") or ""),
                str(item.get("target_id") or item.get("message") or ""),
            )
            for item in handled_api_restrictions
            if item.get("restriction_kind") != "deleted_or_inaccessible_tweet"
        }
        if deleted_incidents:
            headline.append(
                plural_count(
                    len(deleted_incidents),
                    "deleted/inaccessible-target 403",
                    "deleted/inaccessible-target 403s",
                )
                + " handled"
            )
        if other_handled_incidents:
            headline.append(
                plural_count(
                    len(other_handled_incidents),
                    "handled API restriction incident",
                )
            )
    handled_media_fallbacks = [
        item
        for item in media_upload_incidents
        if item.get("status") == "handled"
    ]
    reconciled_media_uploads = [
        item
        for item in media_upload_incidents
        if item.get("status") == "reconciled"
    ]
    unrecovered_media = [
        item
        for item in media_upload_incidents
        if item.get("status") not in {"handled", "reconciled"}
    ]
    if handled_media_fallbacks:
        headline.append(plural_count(len(handled_media_fallbacks), "handled media-upload fallback"))
    if reconciled_media_uploads:
        headline.append(
            plural_count(
                len(reconciled_media_uploads),
                "durably reconciled media-upload ambiguity",
                "durably reconciled media-upload ambiguities",
            )
        )
    if unrecovered_media:
        headline.append(plural_count(len(unrecovered_media), "unrecovered media-upload failure"))
    if self_test_errors:
        selftest_fail_checks = sum(1 for e in self_test_errors if str(e.get("message", "")).startswith("SELFTEST FAIL:"))
        headline.append(f"self-test failures: {selftest_fail_checks} check(s)")
    if confirmed_post_recovery:
        headline.append(
            plural_count(
                len(confirmed_post_recovery),
                "confirmed-post recovery record",
            )
            + " in window"
        )
    if confirmed_reply_recovery:
        headline.append(
            plural_count(
                len(confirmed_reply_recovery),
                "confirmed-reply recovery record",
            )
            + " in window"
        )
    blocking_receipts = [
        item for item in receipt_events
        if item.get("kind") in {"invalid_or_unresolved_blocked", "simultaneous_receipts_blocked"}
    ]
    if blocking_receipts:
        headline.append(
            plural_count(len(blocking_receipts), "receipt-block record") + " in window"
        )
    if asset_health:
        headline.append(
            plural_count(len(asset_health), "asset-metadata warning") + " in window"
        )
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
    max_per_author = int_or_none(
        configs.get("MAX_REPLIES_PER_AUTHOR_PER_DAY")
    )
    max_quote = int_or_none(configs.get("MAX_QUOTE_REPLIES_PER_DAY"))
    used_auto = int_or_none(latest_state_summary.get("daily_reply_count"))
    used_quote = int_or_none(latest_state_summary.get("daily_quote_reply_count"))
    derived = {
        "reply_budget": {
            "auto_used": used_auto,
            "auto_limit": max_auto,
            "per_author_limit": max_per_author,
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
        and item.get("restriction_kind") == "reply_target_eligibility"
        for item in all_api_failures
    )
    deleted_or_inaccessible_tweet_403_count = sum(
        str(item.get("status")) == "403"
        and item.get("restriction_kind") == "deleted_or_inaccessible_tweet"
        for item in all_api_failures
    )
    posting_attempt_count = sum(
        item.get("endpoint") in {"post/reply", "tweet/create"}
        for item in all_api_failures
    )
    tweet_create_request_count = sum(
        item.get("endpoint") == "tweet/create" for item in x_requests
    )
    media_upload_request_count = sum(
        item.get("endpoint") == "media/upload" for item in x_requests
    )
    transient_failure_count = sum(
        str(item.get("status") or "") in {"408", "425"}
        or str(item.get("status") or "").startswith("5")
        for item in all_api_failures
    ) + transient_provider_timeouts
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

    reconcile_reply_pipeline_effective_outcomes(events)
    context_quality = historical_context_quality_summary(events)
    strategy_quality = reply_strategy_summary(events)
    pipeline_stage_quality = reply_pipeline_stage_summary(events)
    stats["tested_pipeline_decisions_all_versions"] = int(
        pipeline_stage_quality.get("tested_pipeline_decision_count", 0) or 0
    )
    stats["tested_pipeline_stage_summary_events_all_versions"] = int(
        pipeline_stage_quality.get("all_stage_summary_event_count", 0) or 0
    )
    stats["tested_pipeline_complete_stage_telemetry"] = int(
        pipeline_stage_quality.get("complete_stage_telemetry_count", 0) or 0
    )
    stats["tested_pipeline_partial_or_legacy_telemetry"] = int(
        pipeline_stage_quality.get("partial_or_legacy_telemetry_count", 0) or 0
    )
    provider_usage = {
        "events": xai_usage_events,
        "totals": xai_usage_totals(xai_usage_events),
        "call_attempts": xai_call_attempts,
        "cost_summary": xai_reply_cost_summary(
            xai_usage_events,
            events,
            xai_call_attempts,
        ),
        "parse_errors": xai_usage_parse_errors,
    }
    tested_decisions = int(
        pipeline_stage_quality.get("tested_pipeline_decision_count", 0) or 0
    )
    cost_candidates = provider_usage["cost_summary"].get("candidates") or []
    deterministic_stage_by_target: Dict[
        Tuple[str, str], Dict[str, Any]
    ] = {}
    for event in events:
        if event.get("kind") != "reply_pipeline_stage_summary":
            continue
        key = (
            _normalise_lane(event.get("lane")),
            str(event.get("target_id") or ""),
        )
        if not key[1]:
            continue
        model_call_count = optional_int_usage_value(
            event.get("model_call_count")
        )
        if model_call_count == 0 and (
            event.get("route_source") == "deterministic_suppression"
            or event.get("deterministic_suppressed") is True
        ):
            deterministic_stage_by_target[key] = event

    decline_event_fields = {
        "mention_grok_skip": ("mention", "mention_id"),
        "hot_post_reply_grok_skip": ("hot-post", "hot_post_reply_id"),
        "quote_tweet_grok_skip": ("quote-tweet", "quote_tweet_id"),
    }
    decline_event_by_target: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for event in events:
        lane_and_field = decline_event_fields.get(str(event.get("kind") or ""))
        if lane_and_field is None:
            continue
        lane, target_field = lane_and_field
        target_id = str(event.get(target_field) or "")
        if target_id:
            decline_event_by_target[(lane, target_id)] = event

    deterministic_suppressions: List[Dict[str, Any]] = []
    for key, decision in strategy_decisions_by_target.items():
        stage = deterministic_stage_by_target.get(key)
        model_call_count = optional_int_usage_value(
            decision.get("model_call_count")
        )
        if model_call_count is None and stage is not None:
            model_call_count = optional_int_usage_value(
                stage.get("model_call_count")
            )
        route_source = (
            decision.get("route_source")
            or (stage or {}).get("route_source")
        )
        explicitly_deterministic = (
            route_source == "deterministic_suppression"
            or (stage or {}).get("deterministic_suppressed") is True
        )
        if not explicitly_deterministic or model_call_count != 0:
            continue
        decline_event = decline_event_by_target.get(key) or {}
        deterministic_suppressions.append({
            "time": decline_event.get("time") or decision.get("time") or "",
            "lane": key[0],
            "target_id": key[1],
            "author_id": decline_event.get("author_id") or "",
            "original_post_id": decline_event.get("original_post_id") or "",
            "incoming_text": (
                decline_event.get("incoming_text")
                or decision.get("incoming_contribution")
                or ""
            ),
            "route_source": route_source,
            "model_call_count": model_call_count,
            "reason": (
                decision.get("reason")
                or decision.get("no_reply_reason")
                or (stage or {}).get("deterministic_reason")
                or (stage or {}).get("pipeline_stage_reason")
                or ""
            ),
        })
    deterministic_suppressions.sort(
        key=lambda row: (
            str(row.get("time") or ""),
            str(row.get("lane") or ""),
            str(row.get("target_id") or ""),
        )
    )

    review_classification_available = bool(
        cost_candidates or deterministic_suppressions
    )
    if review_classification_available:
        ai_reviewed_decline_outcomes = {
            "deliberately_declined",
            "terminal_repetition_rejection",
            "terminal_clarification_mode_rejection",
        }
        ai_reviewed_decline_count = sum(
            str(candidate.get("outcome") or "")
            in ai_reviewed_decline_outcomes
            for candidate in cost_candidates
        )
        deliberately_declined_count = sum(
            candidate.get("outcome") == "deliberately_declined"
            for candidate in cost_candidates
        )
        strategy_quality.update({
            "conversational_candidate_count": int(
                provider_usage["cost_summary"].get("candidate_count", 0) or 0
            ),
            "deliberately_declined_count": deliberately_declined_count,
            "ai_reviewed_decline_count": ai_reviewed_decline_count,
            "deterministic_suppression_count": len(
                deterministic_suppressions
            ),
            "terminal_no_reply_decision_count": (
                ai_reviewed_decline_count + len(deterministic_suppressions)
            ),
            "review_classification_available": True,
            "deterministic_suppressions": deterministic_suppressions,
        })
    headline = [
        item for item in headline
        if not item.endswith("Grok skip") and not item.endswith("Grok skips")
    ]
    health_index = next(
        (index for index, item in enumerate(headline) if item.startswith("current health:")),
        len(headline),
    )
    if tested_decisions:
        latest_version = str(
            pipeline_stage_quality.get("latest_strategy_version") or "unavailable"
        )
        latest_subtotal = int(
            pipeline_stage_quality.get(
                "latest_strategy_version_decision_count", 0
            )
            or 0
        )
        headline.insert(
            health_index,
            f"{tested_decisions} tested-pipeline decisions across all versions; "
            f"current/latest {latest_version}: {latest_subtotal}",
        )
        health_index += 1
    candidates = int(strategy_quality.get("conversational_candidate_count", 0) or 0)
    posted_replies = int(strategy_quality.get("confirmed_outcome_count", 0) or 0)
    declined = int(strategy_quality.get("deliberately_declined_count", 0) or 0)
    repetition_rejections = int(
        strategy_quality.get("terminal_repetition_rejection_count", 0) or 0
    )
    clarification_rejections = int(
        strategy_quality.get(
            "terminal_clarification_mode_rejection_count", 0
        )
        or 0
    )
    deterministic_suppression_count = int(
        strategy_quality.get("deterministic_suppression_count", 0) or 0
    )
    if candidates or deterministic_suppression_count:
        candidate_summary = (
            f"{plural_count(candidates, 'conversational candidate')} AI-reviewed; "
            f"{plural_count(posted_replies, 'reply', 'replies')} posted; "
            f"{plural_count(repetition_rejections, 'terminal repetition rejection')}; "
            f"{plural_count(clarification_rejections, 'terminal clarification-mode rejection')}"
        )
        if strategy_quality.get("review_classification_available"):
            candidate_summary += (
                f"; {plural_count(strategy_quality.get('ai_reviewed_decline_count', 0), 'AI-reviewed decline')} total "
                f"({declined} deliberately declined); "
                f"{plural_count(deterministic_suppression_count, 'deterministic suppression')}; "
                f"{plural_count(strategy_quality.get('terminal_no_reply_decision_count', 0), 'terminal no-reply decision')}"
            )
        else:
            candidate_summary += f"; {declined} deliberately declined"
        headline.insert(
            health_index,
            candidate_summary,
        )
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

    cooldown_claims = {
        "API cooldown occurred",
        "no API cooldown",
    }
    cooldown_claim_prefixes = (
        "X read API cooldown ",
        "X write API cooldown ",
        "xAI cooldown ",
        "quote API cooldown ",
        "current API cooldown state ",
    )
    headline_without_current_cooldown = [
        item
        for item in headline
        if item not in cooldown_claims
        and not item.startswith(cooldown_claim_prefixes)
    ]

    mention_control_kinds = {
        "mention_backlog_started",
        "mention_backlog_progress",
        "mention_backlog_completed",
        "mention_backlog_reset",
        "author_evaluation_quarantine_started",
        "author_evaluation_quarantine_skip",
        "author_evaluation_quarantine_expired",
    }
    mention_control_events = [
        item for item in events if item.get("kind") in mention_control_kinds
    ]
    mention_control_counts = Counter(
        str(item.get("kind")) for item in mention_control_events
    )
    pipeline_evaluations_skipped = sum(
        int(item["pipeline_evaluations_skipped"])
        for item in mention_control_events
        if item.get("kind") == "author_evaluation_quarantine_skip"
        and type(item.get("pipeline_evaluations_skipped")) is int
        and item["pipeline_evaluations_skipped"] >= 0
    )

    return {
        "summary": {
            "record_count": len(records),
            "time_start": records[0].ts.strftime("%Y-%m-%d %H:%M:%S") if records else None,
            "time_end": records[-1].ts.strftime("%Y-%m-%d %H:%M:%S") if records else None,
            "headline": "; ".join(headline),
            "_headline_without_current_cooldown": (
                headline_without_current_cooldown
            ),
            "stats": dict(stats),
            "routine_skip_counts": dict(routine_skip_counts),
        },
        "latest_config": configs,
        "latest_state": latest_state_summary,
        "derived": derived,
        "mention_backlog_and_quarantine": {
            "events": mention_control_events,
            "event_counts": dict(sorted(mention_control_counts.items())),
            "pipeline_evaluations_skipped": pipeline_evaluations_skipped,
        },
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
            "tweet_create_request_count": tweet_create_request_count,
            "media_upload_request_count": media_upload_request_count,
            "x_requests": x_requests,
            "target_eligibility_403_count": target_eligibility_403_count,
            "deleted_or_inaccessible_tweet_403_count": (
                deleted_or_inaccessible_tweet_403_count
            ),
            "transient_failure_count": transient_failure_count,
            "rate_limit_failure_count": rate_limit_failure_count,
            "legacy_cooldown_from_target_restriction_count": legacy_cooldown_from_target_restriction_count,
        },
        "main_post_recovery": {
            "receipt_events": receipt_events,
            "confirmed_post_recovery": confirmed_post_recovery,
        },
        "remote_write_transactions": remote_write_transactions,
        "confirmed_reply_recovery": {
            "receipt_events": confirmed_reply_receipts,
            "warnings": confirmed_reply_recovery,
            "durably_reconciled_ambiguity_receipts": (
                durably_reconciled_reply_receipts
            ),
        },
        "historical_context_replies": {
            "events": [item for item in events if item.get("kind") == "historical_context_reply"],
            "status_counts": {
                key.removeprefix("historical_context_reply_status_"): value
                for key, value in sorted(stats.items())
                if key.startswith("historical_context_reply_status_")
            },
        },
        "production_consistency": {
            "events": [
                item
                for item in events
                if item.get("kind")
                in {
                    "historical_context_runtime",
                    "reply_evidence_unavailable",
                    "runtime_control_pause",
                    "clarification_reply_cap_override",
                    "clarification_reply_used",
                    "repair_reply_completed",
                    "posting_transaction_state",
                    "historical_context_obligation",
                    "historical_context_outbox",
                    "daily_meme_failure",
                }
            ],
            "context_transaction_state_counts": {
                key.removeprefix("context_transaction_state_"): value
                for key, value in sorted(stats.items())
                if key.startswith("context_transaction_state_")
            },
            "context_obligation_state_counts": {
                key.removeprefix("context_obligation_state_"): value
                for key, value in sorted(stats.items())
                if key.startswith("context_obligation_state_")
            },
            "daily_meme_failure_stage_counts": {
                key.removeprefix("daily_meme_failure_stage_"): value
                for key, value in sorted(stats.items())
                if key.startswith("daily_meme_failure_stage_")
            },
            "historical_context_runtime_status_counts": {
                key.removeprefix("historical_context_runtime_status_"): value
                for key, value in sorted(stats.items())
                if key.startswith("historical_context_runtime_status_")
            },
            "reply_evidence_unavailable_lane_counts": {
                key.removeprefix("reply_evidence_unavailable_lane_"): value
                for key, value in sorted(stats.items())
                if key.startswith("reply_evidence_unavailable_lane_")
            },
        },
        "historical_context_quality": context_quality,
        "reply_strategy": strategy_quality,
        "reply_pipeline_stages": pipeline_stage_quality,
        "semantic_veto_load_lifecycle": semantic_veto_load_lifecycle(records),
        "reply_media_context": reply_media_context,
        "asset_health": asset_health,
        "media_upload": {
            "incidents": media_upload_incidents,
            "handled_fallbacks": handled_media_fallbacks,
            "reconciled_incidents": reconciled_media_uploads,
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
        "provider_usage": provider_usage,
        # Retained as a compatibility alias for existing JSON consumers.
        "xai_usage": provider_usage,
        "resume_context": {
            "active_xai_context": active_xai_context,
            "active_xai_call_attempt": (
                dict(xai_call_attempts[active_xai_call_attempt_index])
                if active_xai_call_attempt_index is not None
                and xai_call_attempts[active_xai_call_attempt_index].get(
                    "usage_observed"
                )
                is not True
                else None
            ),
            "pending_mention": pending_mention if active_xai_context else None,
            "pending_qt": pending_qt if active_xai_context else None,
        },
        "lifecycle": lifecycle[-12:],
        "events": events,
        "self_test_errors": self_test_errors[-40:],
        "error_health": error_health,
        "errors_and_warnings": [
            {
                key: value
                for key, value in item.items()
                if not key.startswith("_")
            }
            for item in errors[-40:]
        ],
    }


def md_table_row(cols: List[Any]) -> str:
    """Return the Markdown table row."""
    def esc(x: Any) -> str:
        s = short(x, 240).replace("|", "\\|")
        return s
    return "| " + " | ".join(esc(c) for c in cols) + " |"


CURRENT_COOLDOWN_FIELDS = (
    ("api_cooldown_until_epoch", "X read API"),
    ("x_write_api_cooldown_until_epoch", "X write API"),
    ("xai_api_cooldown_until_epoch", "xAI"),
    ("quote_api_cooldown_until_epoch", "quote API"),
)


def refresh_current_health_headline(report: Dict[str, Any]) -> None:
    """Rebuild current-health and cooldown claims after runtime overlay."""
    if "runtime_state_status" not in report:
        return
    summary = report.get("summary") or {}
    base = summary.get("_headline_without_current_cooldown")
    if not isinstance(base, list):
        return
    runtime_status = str(
        (report.get("runtime_state_status") or {}).get("status") or ""
    )
    generated = int_or_none(report.get("generation_epoch"))
    state = report.get("latest_state") or {}
    current_incidents = int(
        (report.get("error_health") or {}).get(
            "current_independent_incident_count", 0
        )
        or 0
    )
    health_claim = (
        "current health: "
        + plural_count(current_incidents, "unresolved operational incident")
        if current_incidents
        else "current health: no unresolved operational incidents"
    )
    rebuilt_base = [
        health_claim if str(item).startswith("current health:") else item
        for item in base
    ]
    claims: List[str] = []
    statuses: Dict[str, str] = {}
    if runtime_status != "available" or generated is None:
        claims.append("current API cooldown state unavailable")
    else:
        for field, label in CURRENT_COOLDOWN_FIELDS:
            status = cooldown_state_text(state.get(field), generated)
            statuses[field] = status
            if status == "active":
                claims.append(f"{label} cooldown active now")
            elif status == "expired":
                claims.append(f"{label} cooldown occurred, now expired")
            elif status == "unavailable":
                claims.append(f"{label} cooldown state unavailable")
        if statuses and all(value == "cleared" for value in statuses.values()):
            claims.append("no API cooldown")
    report["current_cooldown_status"] = statuses
    summary["headline"] = "; ".join([*rebuilt_base, *claims])


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
    max_per_author = int_or_none(
        configs.get("MAX_REPLIES_PER_AUTHOR_PER_DAY")
    )
    max_quote = int_or_none(configs.get("MAX_QUOTE_REPLIES_PER_DAY"))
    used_auto = int_or_none(st.get("daily_reply_count"))
    used_quote = int_or_none(st.get("daily_quote_reply_count"))

    report["derived"] = {
        "reply_budget": {
            "auto_used": used_auto,
            "auto_limit": max_auto,
            "per_author_limit": max_per_author,
            "auto_remaining": (max_auto - used_auto) if max_auto is not None and used_auto is not None else None,
            "quote_used": used_quote,
            "quote_limit": max_quote,
            "quote_remaining": (max_quote - used_quote) if max_quote is not None and used_quote is not None else None,
            "has_any_budget_input": any(
                x is not None
                for x in (
                    used_auto,
                    max_auto,
                    max_per_author,
                    used_quote,
                    max_quote,
                )
            ),
            "state_carried_forward": bool(st.get("_carried_forward")),
            "config_carried_forward": bool(configs.get("_carried_forward")),
            "config_carried_from_log_backscan": bool(configs.get("_carried_from_log_backscan")),
            "config_backscan_timestamp": configs.get("_log_backscan_timestamp"),
            "state_filled_from_previous": bool(st.get("_filled_from_previous")),
            "config_filled_from_previous": bool(configs.get("_filled_from_previous")),
            "config_filled_from_log_backscan": bool(configs.get("_filled_from_log_backscan")),
            "config_is_on_disk_override": (
                configs.get("_config_source") == "mrsMThatcher.local.json"
            ),
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
    refresh_current_health_headline(report)


def apply_saved_context(
    report: Dict[str, Any],
    state_file: Path,
    *,
    window_end: Optional[datetime] = None,
) -> None:
    """Load digest-cursor history without presenting it as current bot state."""
    old = read_resume_data(state_file)
    report["digest_resume_context"] = {
        "available": bool(old),
        "last_log_entry_time": old.get("last_log_entry_time"),
        "updated_at": old.get("updated_at"),
    }
    previous_state = old.get("last_known_latest_state")
    if isinstance(previous_state, dict) and previous_state:
        report["historical_retained_state"] = strip_internal_context_markers(
            previous_state
        )
    previous_config = old.get("last_known_latest_config")
    if isinstance(previous_config, dict) and previous_config:
        report["historical_retained_config"] = strip_internal_context_markers(
            previous_config
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


def _human_snapshot_age(seconds: float) -> str:
    """Return a deterministic, whole-second age for state presentation."""
    remaining = max(0, int(seconds))
    parts: List[str] = []
    for unit_seconds, singular in (
        (24 * 60 * 60, "day"),
        (60 * 60, "hour"),
        (60, "minute"),
        (1, "second"),
    ):
        value, remaining = divmod(remaining, unit_seconds)
        if value:
            parts.append(plural_count(value, singular))
    return " ".join(parts) if parts else "0 seconds"


def _carried_state_presentation(
    st: Dict[str, Any], summary: Dict[str, Any]
) -> Dict[str, Any]:
    """Describe carried state freshness without changing persisted context."""
    carried = bool(st.get("_carried_forward"))
    if not carried:
        return {"stale": False, "snapshot_only": False, "age": None}
    try:
        state_time = parse_dt(st.get("time"))
    except (TypeError, ValueError):
        state_time = None
    try:
        window_start = parse_dt(summary.get("time_start"))
    except (TypeError, ValueError):
        window_start = None
    try:
        window_end = parse_dt(summary.get("time_end"))
    except (TypeError, ValueError):
        window_end = None
    stale = bool(
        state_time is not None
        and window_start is not None
        and state_time < window_start
    )
    age = (
        _human_snapshot_age((window_end - state_time).total_seconds())
        if state_time is not None and window_end is not None
        else None
    )
    return {
        "stale": stale,
        "snapshot_only": stale or state_time is None,
        "age": age,
    }

def render_markdown(report: Dict[str, Any]) -> str:
    """Render digest metrics as deterministic Markdown."""
    s = report["summary"]
    out: List[str] = []
    out.append("# MrsMThatcher log digest")
    out.append("")
    out.append(
        f"Observed event window: `{s.get('time_start')}` → `{s.get('time_end')}`"
    )
    out.append(f"Project directory: `{report.get('project_dir') or 'unavailable'}`")
    if report.get("requested_since"):
        mode = "exclusive" if report.get("since_exclusive") else "inclusive"
        source = report.get("since_source") or "manual"
        out.append(f"Requested since: `{report.get('requested_since')}` ({mode}, source={source})")
    if report.get("resume_cursor_mode") == "fingerprint_tail":
        out.append(
            "Resume cursor: `physical append order` "
            f"({report.get('resume_tail_match_length', 0)} fingerprint(s) matched)"
        )
    if report.get("local_clock_rollback_count"):
        out.append(
            f"Input warning: **detected {report.get('local_clock_rollback_count')} local clock rollback(s); "
            "records are shown and resumed in physical append order**"
        )
    if report.get("resume_state_file"):
        out.append(f"Resume state file: `{report.get('resume_state_file')}`")
    out.append(f"Records parsed: `{s.get('record_count')}`")
    input_warning = report.get("input_warning")
    if input_warning:
        out.append(f"Input warning: **{input_warning}**")
    retention = report.get("input_retention_coverage") or {}
    if retention.get("requested_since"):
        coverage = retention.get("requested_start_covered")
        coverage_text = (
            "yes" if coverage is True else "no" if coverage is False else "unknown"
        )
        out.append(
            "Retained-log coverage of requested start: "
            f"**{coverage_text}**; earliest retained timestamp: "
            f"`{retention.get('earliest_retained_timestamp') or 'unavailable'}`."
        )
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
    state_presentation = _carried_state_presentation(st, s)
    stale_state_snapshot = state_presentation["stale"] is True
    state_snapshot_only = state_presentation["snapshot_only"] is True
    state_label_prefix = "snapshot_" if state_snapshot_only else ""
    if st:
        if stale_state_snapshot:
            out.append("## Latest state (stale carried-forward snapshot)")
        elif state_snapshot_only:
            out.append("## Latest state (carried-forward snapshot; age unavailable)")
        else:
            out.append("## Latest state")
        state_source = st.get("_state_source")
        state_source_path = st.get("_state_source_path")
        if stale_state_snapshot:
            out.append(
                f"State timestamp: `{st.get('time')}` (carried forward from previous "
                f"digest state; stale snapshot age at window end: "
                f"{state_presentation.get('age') or 'unavailable'})"
            )
            out.append(
                "Historical snapshot values only; the counters and schedules below "
                "are not current."
            )
        elif state_snapshot_only:
            out.append(
                "State timestamp: `unavailable` (carried forward from previous digest "
                "state; age and staleness unavailable)"
            )
            out.append(
                "Snapshot values only; without a state timestamp their currentness "
                "cannot be established."
            )
        elif st.get("_carried_forward"):
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
        out.append(f"{state_label_prefix}daily_reply_count       = {st.get('daily_reply_count')}  date={st.get('daily_reply_date')}")
        out.append(f"{state_label_prefix}daily_quote_reply_count = {st.get('daily_quote_reply_count')}  date={st.get('daily_quote_reply_date')}")
        if st.get("next_reply_lane_priority") is not None:
            out.append(f"{state_label_prefix}next_reply_lane_priority = {st.get('next_reply_lane_priority')}")
        if st.get("skipped_hot_reply_count") is not None:
            out.append(f"{state_label_prefix}skipped_hot_reply_count = {st.get('skipped_hot_reply_count')}")
        out.append(f"{state_label_prefix}quote_spam_author_count = {st.get('quote_spam_author_count')}")
        generation_epoch = report.get("generation_epoch")
        api_cooldown_status = cooldown_state_text(st.get("api_cooldown_until_epoch"), generation_epoch)
        api_cooldown_suffix = f"  {api_cooldown_status}" if api_cooldown_status else ""
        api_cooldown_human = st.get("api_cooldown_until_human") or "none"
        out.append(
            f"{state_label_prefix}x_read_api_cooldown_until = {st.get('api_cooldown_until_epoch')}  "
            f"{api_cooldown_human}{api_cooldown_suffix}"
        )
        if st.get("api_cooldown_reason"):
            out.append(f"{state_label_prefix}x_read_api_cooldown_reason = {st.get('api_cooldown_reason')}")
        x_write_api_cooldown_status = cooldown_state_text(st.get("x_write_api_cooldown_until_epoch"), generation_epoch)
        x_write_api_cooldown_suffix = f"  {x_write_api_cooldown_status}" if x_write_api_cooldown_status else ""
        x_write_api_cooldown_human = st.get("x_write_api_cooldown_until_human") or "none"
        out.append(
            f"{state_label_prefix}x_write_api_cooldown_until = {st.get('x_write_api_cooldown_until_epoch')}  "
            f"{x_write_api_cooldown_human}{x_write_api_cooldown_suffix}"
        )
        if st.get("x_write_api_cooldown_reason"):
            out.append(f"{state_label_prefix}x_write_api_cooldown_reason = {st.get('x_write_api_cooldown_reason')}")
        xai_api_cooldown_status = cooldown_state_text(st.get("xai_api_cooldown_until_epoch"), generation_epoch)
        xai_api_cooldown_suffix = f"  {xai_api_cooldown_status}" if xai_api_cooldown_status else ""
        xai_api_cooldown_human = st.get("xai_api_cooldown_until_human") or "none"
        out.append(
            f"{state_label_prefix}xai_api_cooldown_until  = {st.get('xai_api_cooldown_until_epoch')}  "
            f"{xai_api_cooldown_human}{xai_api_cooldown_suffix}"
        )
        if st.get("xai_api_cooldown_reason"):
            out.append(f"{state_label_prefix}xai_api_cooldown_reason = {st.get('xai_api_cooldown_reason')}")
        quote_api_cooldown_status = cooldown_state_text(st.get("quote_api_cooldown_until_epoch"), generation_epoch)
        quote_api_cooldown_suffix = f"  {quote_api_cooldown_status}" if quote_api_cooldown_status else ""
        quote_api_cooldown_human = st.get("quote_api_cooldown_until_human") or "none"
        out.append(
            f"{state_label_prefix}quote_api_cooldown_until = {st.get('quote_api_cooldown_until_epoch')}  "
            f"{quote_api_cooldown_human}{quote_api_cooldown_suffix}"
        )
        if st.get("quote_api_cooldown_reason"):
            out.append(f"{state_label_prefix}quote_api_cooldown_reason = {st.get('quote_api_cooldown_reason')}")
        out.append(f"{state_label_prefix}last_main_post_id       = {st.get('last_main_post_id')}")
        out.append(f"{state_label_prefix}last_seen_mention_id    = {st.get('last_seen_mention_id')}")
        out.append(
            f"{state_label_prefix}mention_backlog_active   = {str(bool(st.get('mention_backlog_active'))).lower()}"
        )
        if st.get("mention_backlog_active"):
            backlog_age = st.get("mention_backlog_age_seconds")
            out.append(
                f"{state_label_prefix}mention_backlog_age      = "
                + (
                    _human_snapshot_age(float(backlog_age))
                    if type(backlog_age) in {int, float}
                    else "unavailable"
                )
            )
            out.append(
                f"{state_label_prefix}mention_backlog_pages    = {st.get('mention_backlog_pages_completed')}"
            )
            out.append(
                f"{state_label_prefix}mention_backlog_highest  = {st.get('mention_backlog_highest_mention_id')}"
            )
            out.append(
                f"{state_label_prefix}mention_backlog_token    = {str(bool(st.get('mention_backlog_continuation_token_present'))).lower()}"
            )
        out.append(
            f"{state_label_prefix}mention_pending_candidates = {st.get('mention_pending_candidate_count')}"
        )
        out.append(
            f"{state_label_prefix}active_author_evaluation_quarantines = {st.get('active_author_evaluation_quarantine_count')}"
        )
        if st.get("last_quote_post_epoch") is not None:
            out.append(f"{state_label_prefix}last_quote_post         = {st.get('last_quote_post_human')}  epoch={st.get('last_quote_post_epoch')}")
        out.append(f"{state_label_prefix}next_quote_post         = {st.get('next_quote_post_human')}  epoch={st.get('next_quote_post_epoch')}")
        out.append(f"{state_label_prefix}next_meme_post          = {st.get('next_meme_post_human')}  epoch={st.get('next_meme_post_epoch')}")
        if st.get("next_meme_schedule_mode") is not None:
            out.append(f"{state_label_prefix}next_meme_mode          = {st.get('next_meme_schedule_mode')}  date={st.get('next_meme_schedule_date')}")
        if st.get("meme_anchor_quote_post_epoch"):
            out.append(f"{state_label_prefix}meme_anchor_quote_post  = {st.get('meme_anchor_quote_post_human')}  epoch={st.get('meme_anchor_quote_post_epoch')}")
        if st.get("meme_schedule_version") is not None:
            out.append(f"{state_label_prefix}meme_schedule_version   = {st.get('meme_schedule_version')}")
        out.append(f"{state_label_prefix}posted_meme_count       = {st.get('posted_meme_count')}")
        out.append("```")
        if st.get("posted_meme_filenames_tail"):
            out.append(
                "Snapshot recent posted meme filenames:"
                if state_snapshot_only
                else "Recent posted meme filenames:"
            )
            out.append("```text")
            for name in st["posted_meme_filenames_tail"]:
                out.append(str(name))
            out.append("```")
        out.append("")

    if not st:
        runtime_state_status = report.get("runtime_state_status") or {}
        out.append("## Latest state")
        out.append(
            "Current bot runtime state: **unavailable** "
            f"(`{runtime_state_status.get('status') or 'not read'}`; "
            f"source `{runtime_state_status.get('path') or 'unavailable'}`)."
        )
        out.append(
            "No digest resume snapshot or historical log snapshot is used as current state."
        )
        out.append("")

    mention_control = report.get("mention_backlog_and_quarantine") or {}
    mention_control_counts = mention_control.get("event_counts") or {}
    out.append("## Mention backlog and author evaluation quarantine")
    if st:
        author_ids = st.get("active_author_evaluation_quarantine_author_ids") or []
        out.append(
            "Active mention backlog: "
            + ("yes" if st.get("mention_backlog_active") else "no")
            + "; active author evaluation quarantines: "
            + str(st.get("active_author_evaluation_quarantine_count", 0))
            + "."
        )
        out.append(
            "Active quarantined author IDs: "
            + (", ".join(str(value) for value in author_ids) if author_ids else "none")
            + "."
        )
    out.append(
        "Observed events: starts={starts}, progress={progress}, completions={completions}, "
        "resets={resets}, quarantine_starts={quarantine_starts}, quarantine_skips={quarantine_skips}.".format(
            starts=mention_control_counts.get("mention_backlog_started", 0),
            progress=mention_control_counts.get("mention_backlog_progress", 0),
            completions=mention_control_counts.get("mention_backlog_completed", 0),
            resets=mention_control_counts.get("mention_backlog_reset", 0),
            quarantine_starts=mention_control_counts.get(
                "author_evaluation_quarantine_started", 0
            ),
            quarantine_skips=mention_control_counts.get(
                "author_evaluation_quarantine_skip", 0
            ),
        )
    )
    out.append(
        "Pipeline evaluations skipped by active author quarantine "
        "(explicit event counts only): "
        + str(mention_control.get("pipeline_evaluations_skipped", 0))
        + "."
    )
    out.append("")

    retained_state = report.get("historical_retained_state")
    retained_config = report.get("historical_retained_config")
    if retained_state or retained_config:
        out.append("## Historical retained diagnostic snapshots")
        out.append(
            "These snapshots come from prior digest context and are preserved "
            "for historical diagnosis only; they are not current runtime state "
            "or effective live configuration."
        )
        if isinstance(retained_state, dict) and retained_state:
            out.append(
                "Retained state snapshot timestamp: "
                f"`{retained_state.get('time') or 'unavailable'}`."
            )
            out.append("Historical state snapshot (diagnostic only):")
            out.append("```json")
            out.extend(
                json.dumps(
                    retained_state,
                    indent=2,
                    sort_keys=True,
                    ensure_ascii=False,
                ).splitlines()
            )
            out.append("```")
        if isinstance(retained_config, dict) and retained_config:
            out.append("Historical configuration snapshot (not effective live configuration):")
            out.append("```json")
            out.extend(
                json.dumps(
                    retained_config,
                    indent=2,
                    sort_keys=True,
                    ensure_ascii=False,
                ).splitlines()
            )
            out.append("```")
        out.append("")

    safety = report.get("remote_write_safety") or {}
    if safety:
        out.append("## Remote-write safety")
        if safety.get("configured") is not True:
            out.append(
                "No activated remote-write protocol state was found in the current "
                "project directory; this section is informational for non-production checkouts."
            )
        elif safety.get("available") is not True:
            out.append(
                "Current safety state is **unavailable**: "
                + str(safety.get("reason") or "inspection failed")
            )
        else:
            status = str(safety.get("status") or "unavailable")
            out.append(
                "Current read-only filesystem snapshot: "
                f"**{status.replace('_', ' ')}**"
                + (
                    "; active safety barriers are present."
                    if safety.get("blocking") is True
                    else "; no active transaction safety barrier is present."
                )
            )
            protocol = safety.get("protocol") or {}
            control = safety.get("control") or {}
            transport = safety.get("transport") or {}
            media = safety.get("media") or {}
            ledgers = safety.get("retirement_ledgers") or []
            healthy_ledgers = sum(
                row.get("valid") is True and row.get("blocking") is not True
                for row in ledgers
            )
            out.append("```text")
            out.append(
                "protocol_activation      = "
                + ("valid v2" if protocol.get("valid") is True else "INVALID/MISSING")
            )
            out.append(
                f"transport_journal        = {transport.get('classification', 'unavailable')}"
            )
            out.append(
                f"media_upload_receipt     = {media.get('classification', 'unavailable')}"
            )
            out.append(
                f"retirement_ledgers       = {healthy_ledgers} / {len(ledgers)} valid and nonblocking"
            )
            out.append(
                f"active_barrier_entries   = {len(safety.get('active_entries') or [])}"
            )
            out.append(
                f"control_generation       = {control.get('generation')}"
            )
            out.append(
                "control_active_keys      = "
                + (", ".join(control.get("active_keys") or []) or "none")
            )
            out.append(
                "ready_for_remote_writes  = "
                + str(safety.get("ready_for_remote_writes") is True).lower()
            )
            out.append("```")
            active_entries = safety.get("active_entries") or []
            if active_entries:
                out.append("Active blockers:")
                out.append(md_table_row(["name", "kind", "safe regular", "mode", "size"]))
                out.append(md_table_row(["---", "---", "---", "---", "---"]))
                for item in active_entries:
                    out.append(
                        md_table_row(
                            [
                                item.get("name", ""),
                                item.get("kind", ""),
                                item.get("safe_regular", ""),
                                item.get("mode", ""),
                                item.get("size", ""),
                            ]
                        )
                    )
            archive = safety.get("reconciliation_archive") or {}
            if archive.get("present") is True and archive.get("valid") is not True:
                out.append(
                    "Warning: the reconciliation archive contains invalid audit "
                    "evidence; it cannot resolve a historical ambiguity."
                )
                for reason in archive.get("invalid_audits") or []:
                    out.append("- Invalid audit: `" + str(reason) + "`")
            marker_audit = archive.get("latest_marker_reconciliation") or {}
            media_audit = archive.get("latest_media_reconciliation") or {}
            if marker_audit or media_audit:
                out.append(
                    "Durable reconciliation evidence: marker audits "
                    f"**{archive.get('valid_marker_reconciliation_count', 0)}**; "
                    "unattached-media audits "
                    f"**{archive.get('valid_media_reconciliation_count', 0)}**."
                )
                if marker_audit:
                    out.append(
                        "- Latest marker audit: `"
                        + str(marker_audit.get("audit_path") or "")
                        + "`"
                    )
                if media_audit:
                    out.append(
                        "- Latest media audit: `"
                        + str(media_audit.get("audit_path") or "")
                        + "`"
                    )
        out.append("")

    remote_transactions = report.get("remote_write_transactions") or []
    if remote_transactions:
        out.append("## Remote-write transaction lifecycle")
        out.append(
            md_table_row(
                ["time", "kind", "phase", "lane", "transaction/attempt", "detail"]
            )
        )
        out.append(md_table_row(["---", "---", "---", "---", "---", "---"]))
        for item in remote_transactions:
            out.append(
                md_table_row(
                    [
                        item.get("time", ""),
                        item.get("kind", ""),
                        item.get("phase", ""),
                        item.get("lane", ""),
                        item.get("transaction_id") or item.get("attempt_id") or "",
                        item.get("image")
                        or item.get("post_id")
                        or item.get("disposition")
                        or item.get("path")
                        or "",
                    ]
                )
            )
        out.append("")

    media_upload = report.get("media_upload") or {}
    media_incidents = media_upload.get("incidents") or []
    if media_incidents:
        out.append("## Media upload incidents")
        out.append("```text")
        out.append(f"handled_fallbacks     = {len(media_upload.get('handled_fallbacks') or [])}")
        out.append(f"reconciled_ambiguities = {len(media_upload.get('reconciled_incidents') or [])}")
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
            if stale_state_snapshot:
                source_bits.append(
                    "state is a stale snapshot"
                    + (
                        f" ({state_presentation.get('age')} old at window end)"
                        if state_presentation.get("age")
                        else ""
                    )
                )
            elif state_snapshot_only:
                source_bits.append("state snapshot age unavailable")
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
                state_meme_label = (
                    "snapshot_next_meme"
                    if state_snapshot_only
                    else "current_next_meme"
                )
                out.append(f"{state_meme_label:<24} = {st.get('next_meme_post_human')}  epoch={st.get('next_meme_post_epoch')}")
            if st.get("next_meme_schedule_mode") is not None:
                state_mode_label = (
                    "snapshot_mode" if state_snapshot_only else "current_mode"
                )
                out.append(f"{state_mode_label:<24} = {st.get('next_meme_schedule_mode')}  date={st.get('next_meme_schedule_date')}")
            if st.get("meme_anchor_quote_post_epoch"):
                state_anchor_label = (
                    "snapshot_anchor" if state_snapshot_only else "current_anchor"
                )
                out.append(f"{state_anchor_label:<24} = {st.get('meme_anchor_quote_post_human')}  epoch={st.get('meme_anchor_quote_post_epoch')}")
            else:
                if st.get("next_meme_schedule_mode") and str(st.get("next_meme_schedule_mode")).startswith("fallback"):
                    if state_snapshot_only:
                        out.append(
                            "snapshot_anchor          = none recorded in snapshot; "
                            "snapshot fallback mode retained for diagnosis"
                        )
                    else:
                        out.append("current_anchor          = none yet; fallback remains until first qualifying post/image after midday")
            out.append("```")
            out.append("")

    derived = report.get("derived") or {}
    budget = derived.get("reply_budget") or {}
    if budget:
        out.append("## Reply budget")
        out.append("```text")
        au, al, ar = budget.get("auto_used"), budget.get("auto_limit"), budget.get("auto_remaining")
        per_author_limit = budget.get("per_author_limit")
        qu, ql, qr = budget.get("quote_used"), budget.get("quote_limit"), budget.get("quote_remaining")
        source_bits = []
        if budget.get("state_carried_forward"):
            source_bits.append("state carried forward")
            if stale_state_snapshot:
                source_bits.append("state counters are stale snapshot values")
            elif state_snapshot_only:
                source_bits.append("state counter age unavailable")
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
        if budget.get("config_is_on_disk_override"):
            source_bits.append(
                "limits from on-disk local overrides; live effectiveness unverified"
            )
        if source_bits:
            out.append(f"source             = {', '.join(source_bits)}")
        if not budget.get("has_any_budget_input"):
            out.append("not available      = no state/config snapshot in this window or saved resume context")
        elif state_snapshot_only:
            if au is not None or al is not None:
                out.append(
                    f"snapshot auto replies used  = {au if au is not None else '?'} / "
                    f"{al if al is not None else '?'}  snapshot_remaining="
                    f"{ar if ar is not None else '?'}"
                )
            if qu is not None or ql is not None:
                out.append(
                    f"snapshot quote replies used = {qu if qu is not None else '?'} / "
                    f"{ql if ql is not None else '?'}  snapshot_remaining="
                    f"{qr if qr is not None else '?'}"
                )
        else:
            if au is not None or al is not None:
                out.append(f"auto replies used  = {au if au is not None else '?'} / {al if al is not None else '?'}  remaining={ar if ar is not None else '?'}")
            if per_author_limit is not None:
                out.append(f"per-author reply cap = {per_author_limit}")
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
            if stale_state_snapshot:
                source_bits.append("state priority is a stale snapshot value")
            elif state_snapshot_only:
                source_bits.append("state priority age unavailable")
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
        priority_label = (
            "snapshot_next_priority" if state_snapshot_only else "current_next_priority"
        )
        out.append(f"{priority_label:<31} = {priority if priority is not None else 'not available'}")
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

    openai_cost = report.get("openai_published_cost") or {
        "available": False,
        "reason": "cache was not inspected",
    }
    out.append("## OpenAI published-cost cache")
    if not openai_cost.get("available"):
        out.append("OpenAI published cost: unknown")
        out.append(
            f"Cache status: **unavailable** ({openai_cost.get('reason') or 'unknown reason'})."
        )
    else:
        openai_scope = openai_cost.get("scope") or {}
        if openai_scope.get("kind") == "project":
            scope_text = f"project {openai_scope.get('project_id', 'unavailable')}"
        else:
            scope_text = "organisation-wide (not bot-exclusive)"
        out.append(f"Cache scope: **{scope_text}**.")
        out.append(
            f"Cache updated: **{openai_cost.get('updated_at_display')}** "
            f"(age: {openai_cost.get('age')})."
        )
        current_day = openai_cost.get("current_day") or {}
        if current_day.get("available"):
            out.append(
                "Current UTC-day provider-published total: "
                f"**{format_openai_usd(current_day.get('primary_total'))}** "
                f"for `{current_day.get('utc_date')}`."
            )
            out.append("Status: **provisional**.")
        else:
            out.append("Current UTC-day provider-published total: **unknown**.")

        selected = openai_cost.get("selected_window") or {}
        selected_status = selected.get("status")
        organization_prefix = (
            "OpenAI organisation-wide "
            if openai_scope.get("kind") == "organization"
            else "OpenAI "
        )
        if selected_status == "complete":
            out.append(
                f"{organization_prefix}selected-window estimate: "
                f"**{format_openai_usd(selected.get('amount'))}**."
            )
        elif selected_status == "partial":
            out.append(
                f"{organization_prefix}selected-window estimate (partial coverage): "
                f"**{format_openai_usd(selected.get('amount'))}**."
            )
        else:
            out.append(f"{organization_prefix}selected-window estimate: **unknown**.")
        if selected.get("method"):
            out.append(f"Method: {selected.get('method')}.")
        if selected.get("requested_window"):
            out.append(f"Requested window: **{selected.get('requested_window')}**.")
        valid_segments = [
            item
            for item in selected.get("segments", [])
            if item.get("status") in {"complete", "partial"}
        ]
        if valid_segments:
            coverage_parts = []
            requested_coverage_parts = []
            outside_requested = False
            for item in valid_segments:
                sample_start = _parse_openai_utc(
                    item.get("sample_start_utc"), label="sample coverage start"
                )
                sample_end = _parse_openai_utc(
                    item.get("sample_end_utc"), label="sample coverage end"
                )
                requested_start = _parse_openai_utc(
                    item.get("requested_start_utc"), label="requested segment start"
                )
                requested_end = _parse_openai_utc(
                    item.get("requested_end_utc"), label="requested segment end"
                )
                outside_requested = outside_requested or (
                    sample_start < requested_start or sample_end > requested_end
                )
                coverage_parts.append(
                    f"{item.get('utc_date')}: {_openai_window_text(sample_start, sample_end)}"
                )
                represented_start = max(sample_start, requested_start)
                represented_end = min(sample_end, requested_end)
                if represented_end > represented_start:
                    requested_coverage_parts.append(
                        f"{item.get('utc_date')}: "
                        f"{_openai_window_text(represented_start, represented_end)}"
                    )
            out.append("Sample coverage: **" + "; ".join(coverage_parts) + "**.")
            if selected_status == "partial" and requested_coverage_parts:
                out.append(
                    "Requested coverage represented: approximately **"
                    + "; ".join(requested_coverage_parts)
                    + "**."
                )
            trailing_segments = [
                item
                for item in valid_segments
                if item.get("status") == "partial"
                and item.get("trailing_uncovered_seconds") is not None
            ]
            if len(trailing_segments) == 1:
                out.append(
                    "Trailing period unavailable: **"
                    + _human_snapshot_age(
                        float(trailing_segments[0]["trailing_uncovered_seconds"])
                    )
                    + "**."
                )
            if outside_requested:
                out.append(
                    "The estimate can include a small amount immediately outside the "
                    "requested log window because samples are collected at intervals."
                )
        unavailable_segments = [
            item
            for item in selected.get("segments", [])
            if item.get("status") == "unavailable"
        ]
        if unavailable_segments:
            out.append(
                "Unavailable UTC segment(s): "
                + "; ".join(
                    f"{item.get('utc_date')}: {item.get('reason') or 'unknown reason'}"
                    for item in unavailable_segments
                )
                + "."
            )
        elif selected.get("reason"):
            out.append(f"Estimate status: {selected.get('reason')}.")

        if openai_scope.get("kind") == "project":
            combined = openai_cost.get("combined_selected_window") or {}
            xai_component = openai_cost.get("xai_component") or {}
            if combined.get("available"):
                out.append(
                    "Combined selected-window estimate: "
                    f"**{format_openai_usd(combined.get('amount'))}**."
                )
            else:
                out.append("Combined selected-window estimate: **unknown**.")
            if xai_component.get("available"):
                xai_qualifier = (
                    "provider-reported"
                    if xai_component.get("coverage_complete")
                    else "provider-reported known lower bound"
                )
                out.append(
                    f"xAI component ({xai_qualifier}): "
                    f"**{format_openai_usd(xai_component.get('amount'))}**."
                )
            else:
                out.append("xAI component (provider-reported): **unknown**.")
            if selected_status == "complete":
                out.append(
                    "OpenAI component (published-cost delta estimate): "
                    f"**{format_openai_usd(selected.get('amount'))}**."
                )
            else:
                out.append(
                    "OpenAI component (published-cost delta estimate): **unknown or partial**."
                )
        else:
            out.append(
                "The OpenAI figure is organization-wide and is therefore not combined "
                "with the bot's xAI component."
            )
    out.append(
        "Individual OpenAI calls and pipeline stages remain cost **unknown**; the "
        "daily estimate is not allocated across calls or stages."
    )
    out.append("")

    xai_usage = report.get("provider_usage") or report.get("xai_usage") or {}
    xai_events = xai_usage.get("events") or []
    xai_call_attempts = xai_usage.get("call_attempts") or []
    xai_parse_errors = xai_usage.get("parse_errors") or []
    if xai_events or xai_call_attempts or xai_parse_errors:
        out.append("## xAI usage, OpenAI usage, and conversational reply cost")
        if xai_events or xai_call_attempts:
            totals = xai_usage.get("totals") or {}
            cost_summary = xai_usage.get("cost_summary") or {}
            known_ticks = int(
                cost_summary.get(
                    "known_cost_in_usd_ticks",
                    totals.get("cost_in_usd_ticks", 0),
                )
                or 0
            )
            coverage_complete = cost_summary.get("coverage_complete") is True
            successful_provider_calls = int(
                totals.get(
                    "successful_provider_calls",
                    totals.get("successful_xai_calls", 0),
                )
                or 0
            )
            costed_calls = int(totals.get("costed_call_count", 0) or 0)
            known_cost_text = (
                format_usd_ticks(known_ticks) if costed_calls else "unknown"
            )
            out.append(
                f"**Provider-reported known cost lower bound: {known_cost_text} "
                f"({known_ticks:,} reported ticks) across "
                f"{successful_provider_calls} successful logged calls.**"
            )
            out.append(
                f"Cost-record coverage: **{totals.get('costed_call_count', 0)} / "
                f"{successful_provider_calls} successful calls**; "
                f"AI-reviewed candidates observed: "
                f"**{cost_summary.get('candidate_count', 0)}**; "
                f"published conversational replies: "
                f"**{cost_summary.get('published_candidate_count', 0)}**."
            )
            provider_rows = cost_summary.get("providers") or []
            if provider_rows:
                out.append(
                    "Successful calls by provider: "
                    + ", ".join(
                        f"{item.get('provider', 'unavailable')}="
                        f"{item.get('successful_usage_records', 0)}"
                        for item in provider_rows
                    )
                    + "."
                )
            known_call_average = cost_summary.get("known_cost_per_costed_call")
            if known_call_average:
                out.append(
                    "Mean reported cost across cost-reported successful calls "
                    "(unknown-cost calls excluded): "
                    f"**{format_usd_ticks(int(known_call_average['ticks']), divisor=int(known_call_average['divisor']))}**."
                )
            if coverage_complete:
                per_candidate = cost_summary.get("per_reviewed_candidate")
                effective = cost_summary.get("effective_per_published_reply")
                if per_candidate:
                    out.append(
                        "Mean provider cost per AI-reviewed candidate: "
                        f"**{format_usd_ticks(int(per_candidate['ticks']), divisor=int(per_candidate['divisor']))}**."
                    )
                if effective:
                    out.append(
                        "Effective provider cost per published conversational reply "
                        "(including deliberately declined candidates): "
                        f"**{format_usd_ticks(int(effective['ticks']), divisor=int(effective['divisor']))}**."
                    )
            else:
                reasons = cost_summary.get("coverage_reasons") or [
                    "coverage could not be proved complete"
                ]
                out.append(
                    "Exact per-candidate and effective-per-published-reply averages "
                    "are unavailable: "
                    + "; ".join(str(reason) for reason in reasons)
                    + "."
                )
            out.append(
                "These figures cover successful provider responses present in the "
                "selected logs; they are not invoice reconciliation and can omit "
                "failed or ambiguous requests. Cached tokens are a subset of prompt "
                "tokens. Deterministic historical-context replies do not make a "
                "runtime conversational-AI call."
            )
            out.append("")

            outcome_rows = cost_summary.get("outcomes") or []
            if outcome_rows:
                out.append("Cost by candidate outcome:")
                out.append(
                    md_table_row(
                        [
                            "outcome",
                            "candidates",
                            "successful calls",
                            "total tokens",
                            "known cost",
                        ]
                    )
                )
                out.append(md_table_row(["---"] * 5))
                for item in outcome_rows:
                    out.append(
                        md_table_row(
                            [
                                str(item.get("outcome", "")).replace("_", " "),
                                item.get("candidate_count", 0),
                                item.get("observed_successful_calls", 0),
                                item.get("total_tokens", 0),
                                format_reported_cost(item),
                            ]
                        )
                    )
                out.append("")

            stage_rows = cost_summary.get("stages") or []
            if stage_rows:
                out.append("Cost by provider pipeline stage:")
                out.append(
                    md_table_row(
                        [
                            "provider",
                            "stage",
                            "calls started",
                            "successful usage",
                            "starts without usage",
                            "total tokens",
                            "known cost",
                        ]
                    )
                )
                out.append(md_table_row(["---"] * 7))
                for item in stage_rows:
                    out.append(
                        md_table_row(
                            [
                                item.get("provider", ""),
                                item.get("stage", ""),
                                item.get("started_calls", 0),
                                item.get("successful_usage_records", 0),
                                item.get("call_starts_without_usage", 0),
                                item.get("total_tokens", 0),
                                format_reported_cost(item),
                            ]
                        )
                    )
                out.append("")

            candidate_rows = cost_summary.get("candidates") or []
            if candidate_rows:
                out.append("Per-candidate accounting:")
                out.append(
                    md_table_row(
                        [
                            "lane",
                            "context_id",
                            "outcome",
                            "usage/reported calls",
                            "coverage",
                            "stages",
                            "tokens",
                            "known cost",
                        ]
                    )
                )
                out.append(md_table_row(["---"] * 8))
                for item in candidate_rows:
                    stage_text = ", ".join(
                        f"{stage}×{count}"
                        for stage, count in (
                            item.get("stages") or {}
                        ).items()
                    )
                    reported = item.get("reported_model_call_count")
                    out.append(
                        md_table_row(
                            [
                                item.get("lane", ""),
                                item.get("context_id", ""),
                                str(item.get("outcome", "")).replace("_", " "),
                                f"{item.get('observed_successful_calls', 0)}/"
                                f"{reported if reported is not None else 'unavailable'}",
                                item.get("call_coverage", ""),
                                stage_text,
                                item.get("total_tokens", 0),
                                format_reported_cost(item),
                            ]
                        )
                    )
                out.append("")

            out.append("Successful provider responses (detail):")
            out.append(md_table_row([
                "time",
                "lane",
                "context_id",
                "prompt input",
                "cache-read input",
                "reasoning",
                "completion",
                "total",
                "sources",
                "cost_ticks",
                "image",
                "provider",
                "stage",
                "model",
                "cost_usd",
            ]))
            out.append(md_table_row(["---"] * 15))
            for item in xai_events:
                item_cost = optional_int_usage_value(
                    item.get("cost_in_usd_ticks")
                )
                out.append(md_table_row([
                    item.get("time", ""),
                    item.get("lane", ""),
                    item.get("context_id", ""),
                    item.get("prompt_tokens", 0),
                    item.get(
                        "cache_read_input_tokens",
                        item.get("cached_tokens", 0),
                    ),
                    item.get("reasoning_tokens", 0),
                    item.get("completion_tokens", 0),
                    item.get("total_tokens", 0),
                    item.get("num_sources_used", 0),
                    item_cost if item_cost is not None else "unknown",
                    item.get("image_tokens", 0),
                    item.get("provider", "unavailable"),
                    item.get("stage", "unavailable"),
                    item.get("model", ""),
                    (
                        format_usd_ticks(item_cost)
                        if item_cost is not None
                        else "unknown"
                    ),
                ]))
            out.append("")
            out.append("Totals:")
            out.append("```text")
            out.append(f"successful_provider_calls = {successful_provider_calls}")
            out.append(f"successful_xai_calls = {totals.get('successful_xai_calls', 0)}")
            out.append(f"successful_openai_calls = {totals.get('successful_openai_calls', 0)}")
            out.append(f"prompt_tokens        = {totals.get('prompt_tokens', 0)}")
            out.append(
                "cache_read_input_tokens = "
                f"{totals.get('cache_read_input_tokens', totals.get('cached_tokens', 0))}"
            )
            out.append(
                "cached_tokens        = "
                f"{totals.get('cached_tokens', 0)} "
                "(compatibility alias for cache-read input; not cache-write usage)"
            )
            cache_metric_coverage = totals.get("cache_metric_coverage")
            if not isinstance(cache_metric_coverage, dict):
                cache_metric_coverage = {}
            for metric_name in (
                "cache_creation_input_tokens",
                "cache_write_input_tokens",
            ):
                metric_coverage = cache_metric_coverage.get(metric_name)
                if not isinstance(metric_coverage, dict):
                    metric_coverage = _cache_metric_coverage(
                        xai_events,
                        metric_name,
                    )
                out.append(
                    _format_cache_metric_coverage_line(
                        metric_name,
                        metric_coverage,
                    )
                )
                provider_coverage = metric_coverage.get("by_provider") or {}
                for provider in ("OpenAI", "xAI"):
                    scope_coverage = provider_coverage.get(provider) or {}
                    if int(
                        scope_coverage.get("successful_call_count", 0) or 0
                    ):
                        out.append(
                            _format_cache_metric_coverage_line(
                                metric_name,
                                scope_coverage,
                                provider=provider,
                            )
                        )
            out.append(f"image_tokens         = {totals.get('image_tokens', 0)}")
            out.append(f"reasoning_tokens     = {totals.get('reasoning_tokens', 0)}")
            out.append(f"completion_tokens    = {totals.get('completion_tokens', 0)}")
            out.append(f"total_tokens         = {totals.get('total_tokens', 0)}")
            out.append(f"sources_used         = {totals.get('sources_used', 0)}")
            out.append(
                "known_cost_ticks_lower_bound = "
                f"{totals.get('cost_in_usd_ticks', 0) if costed_calls else 'unknown'}"
            )
            out.append(f"known_cost_usd_lower_bound = {known_cost_text}")
            out.append(f"costed_calls         = {totals.get('costed_call_count', 0)}")
            out.append(f"uncosted_calls       = {totals.get('uncosted_successful_call_count', 0)}")
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
    generated_pool_enabled = generated_spacing_latest.get("pool_enabled")
    if isinstance(generated_pool_enabled, str):
        generated_pool_enabled = generated_pool_enabled.strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    generated_pool_allowed = generated_spacing_latest.get("allowed")
    if isinstance(generated_pool_allowed, str):
        generated_pool_allowed = generated_pool_allowed.strip().lower() in {
            "1",
            "true",
            "yes",
            "on",
        }
    pool_health = report.get("generated_image_pool_health") or {}
    if pool_health:
        out.append("## Generated image pool health")
        out.append("Current filesystem snapshot at digest generation time; these counts are not limited to the selected log window.")
        if generated_pool_enabled is False:
            disabled_explanation = (
                "**The generated-image pool is intentionally disabled.** "
                "Pool inventory and historical usage are reported for observability only."
            )
            if generated_pool_allowed is True:
                disabled_explanation += (
                    " `allowed=true` means a spacing rule would permit selection, "
                    "not that the pool is enabled."
                )
            else:
                disabled_explanation += (
                    " The `allowed` flag reports spacing eligibility separately "
                    "from the enablement setting."
                )
            out.append(disabled_explanation)
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
        out.append(
            "Current-cycle history records whether an image is marked used in the live image cycle. "
            "Bounded structured-log observations count successful post records retained in the scanned logs. "
            "The two measures answer different questions and are not interchangeable."
        )
        out.append(
            "The active-image inventory is a current filesystem snapshot. The structured-log "
            "coverage shown below is a secondary bounded scan and can extend slightly beyond "
            "the selected digest event window."
        )
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

        filename_sample_limit = 5
        never_used_rows = list(utilisation.get("never_used") or [])
        unused_longest_rows = list(utilisation.get("unused_longest") or [])
        never_used_total = int(utilisation.get("never_used_total", 0) or 0)
        never_used_sample = never_used_rows[:filename_sample_limit]
        unused_longest_sample = unused_longest_rows[:filename_sample_limit]
        unused_longest_total = int(
            utilisation.get(
                "unused_longest_total",
                utilisation.get("active_generated_images", len(unused_longest_rows)),
            )
            or 0
        )

        same_unused_population = (
            never_used_total == unused_longest_total
            and [item.get("image") for item in never_used_rows]
            == [item.get("image") for item in unused_longest_rows]
            and all(not item.get("last_successful_post") for item in unused_longest_rows)
        )
        out.append(
            "Active generated images never successfully posted in observed logs"
            + (
                " (the same population is therefore also unused longest)"
                if same_unused_population else ""
            )
            + f" (count: **{never_used_total}**; sample: **{len(never_used_sample)}**)"
        )
        out.append(md_table_row(["image", "origin_quote_hash"]))
        out.append(md_table_row(["---", "---"]))
        for item in never_used_sample:
            out.append(md_table_row([item.get("image", ""), item.get("origin_quote_hash") or "unavailable"]))
        omitted = max(0, never_used_total - len(never_used_sample))
        if omitted > 0:
            out.append(
                f"{plural_count(omitted, 'additional active image')} omitted from the readable summary."
            )
        if not never_used_sample:
            out.append(md_table_row(["none", "-"]))
        out.append("")

        if not same_unused_population:
            out.append(
                "Active generated images unused longest in observed logs "
                f"(count: **{unused_longest_total}**; sample: **{len(unused_longest_sample)}**)"
            )
            out.append(md_table_row(["image", "last_successful_post", "successful_posts"]))
            out.append(md_table_row(["---", "---", "---"]))
            for item in unused_longest_sample:
                out.append(md_table_row([item.get("image", ""), item.get("last_successful_post") or "never", item.get("successful_posts", 0)]))
            if unused_longest_total > len(unused_longest_sample):
                out.append(
                    f"{plural_count(unused_longest_total - len(unused_longest_sample), 'additional row')} "
                    "omitted from the readable summary."
                )
            if not unused_longest_sample:
                out.append(md_table_row(["none", "never", "0"]))
            out.append("")

        if report.get("detailed_appendix") and (never_used_rows or unused_longest_rows):
            out.append("### Detailed generated-image filename appendix")
            out.append(
                "This optional appendix contains every filename retained in the digest's "
                "already-bounded utilisation result."
            )
            if never_used_rows:
                out.append("Never observed:")
                for item in never_used_rows:
                    out.append(f"- `{item.get('image', '')}`")
            if unused_longest_rows and not same_unused_population:
                out.append("Unused longest:")
                for item in unused_longest_rows:
                    out.append(
                        f"- `{item.get('image', '')}` — "
                        f"{item.get('last_successful_post') or 'never'}"
                    )
            out.append("")

    if generated_spacing_latest or generated_spacing_events:
        out.append("## Generated image spacing")
        if generated_spacing_latest:
            if generated_pool_enabled is False:
                out.append(
                    "The pool is intentionally disabled; spacing eligibility is informational "
                    "and does not activate generated-image selection."
                )
            out.append("```text")
            out.append(f"required_original_posts_between = {generated_spacing_latest.get('required', '')}")
            out.append(f"original_posts_since_generated  = {generated_spacing_latest.get('original_posts_since_generated', '')}")
            out.append(f"generated_pool_enabled          = {generated_spacing_latest.get('pool_enabled', '')}")
            out.append(f"generated_pool_allowed          = {generated_spacing_latest.get('allowed', '')}")
            out.append("```")
        spacing_state_fields = (
            "pool_enabled", "allowed", "original_posts_since_generated", "required"
        )
        invariant_spacing = bool(generated_spacing_events) and len({
            tuple(str(item.get(field, "")) for field in spacing_state_fields)
            for item in generated_spacing_events
        }) == 1
        if invariant_spacing:
            out.append(
                f"All **{len(generated_spacing_events)}** spacing observations had the same "
                f"state; first `{generated_spacing_events[0].get('time', '')}`, "
                f"last `{generated_spacing_events[-1].get('time', '')}`."
            )
        else:
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
                format_display_number(item.get("score", "")),
                item.get("origin_quote_match", ""),
                format_display_number(item.get("origin_quote_boost", "")),
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
        out.append(
            "mean_production_winner_shadow_rank    = "
            f"{format_rank(avg_rank, mean=True)}"
        )
        out.append(
            "median_production_winner_shadow_rank  = "
            f"{format_rank(shadow_summary.get('median_production_winner_shadow_rank'))}"
        )
        out.append(
            "worst_production_winner_shadow_rank   = "
            f"{format_rank(shadow_summary.get('worst_production_winner_shadow_rank'))}"
        )
        out.append(f"production_winner_shadow_rank_1      = {shadow_summary.get('production_rank_1', 0)}")
        out.append(f"production_winner_shadow_rank_2_or_3 = {shadow_summary.get('production_rank_2_or_3', 0)}")
        out.append(f"production_winner_shadow_rank_10_plus = {shadow_summary.get('production_rank_10_or_worse', 0)}")
        out.append(f"average_abs_editorial_adjustment     = {float(shadow_summary.get('average_abs_editorial_adjustment', 0.0)):.2f}")
        out.append(f"max_abs_editorial_adjustment         = {float(shadow_summary.get('max_abs_editorial_adjustment', 0.0)):.2f}")
        out.append(f"cap_hit_count                        = {shadow_summary.get('cap_hit_count', 0)}")
        out.append("```")
        severe = shadow_summary.get("severe_disagreements") or []
        if severe:
            out.append("Severe disagreements (production winner ranked 10 or worse):")
            out.append(md_table_row(["time", "production", "shadow", "production rank"]))
            out.append(md_table_row(["---"] * 4))
            for item in severe:
                out.append(md_table_row([
                    item.get("time", ""),
                    item.get("production_winner", ""),
                    item.get("shadow_original_winner", ""),
                    item.get("production_shadow_rank", ""),
                ]))
            out.append("")
        else:
            out.append("Severe disagreements (production winner ranked 10 or worse): **0**.")
            out.append("")
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
    error_health = report.get("error_health") or {}
    context_categories = {
        "historical_context_source_role_incompatibility",
        "historical_context_reply_failure",
        "legacy_regular_receipt_barrier",
    }
    current_context_incidents = [
        item
        for item in error_health.get("current_incidents") or []
        if item.get("category") in context_categories
    ]
    resolved_context_incidents = [
        item
        for item in error_health.get("historical_resolved_incidents") or []
        if item.get("category") in context_categories
    ]
    out.append(
        "Operational status: "
        f"**{plural_count(len(current_context_incidents), 'current independent incident')}**; "
        f"**{plural_count(len(resolved_context_incidents), 'resolved legacy receipt/source-role incident')}** "
        "in the selected log window."
    )
    if resolved_context_incidents:
        out.append(
            "Resolved legacy incidents remain visible as history; they are not counted as "
            "current historical-context failures."
        )
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
    for label, key in (
        ("Verification labels", "verification_counts"),
        ("Source classes", "source_class_counts"),
        ("Overall reply confidence", "confidence_counts"),
        ("Formatter versions", "formatter_version_counts"),
        ("Rendering modes", "rendering_mode_counts"),
        ("Source-role audit versions", "source_role_audit_version_counts"),
    ):
        values = context_quality.get(key) or {}
        out.append(f"{label}: {compact_counts(values)}")
    rendering_counts = context_quality.get("rendering_context_counts") or {}
    out.append(
        "Context rendering: "
        f"concrete event/date included={rendering_counts.get('concrete_event_or_date_context_included', 0)}, "
        f"date-only qualified included={rendering_counts.get('date_only_qualified_context_included', 0)}, "
        f"omitted because no useful event/date was admitted="
        f"{rendering_counts.get('context_omitted_no_useful_event_or_date', 0)}, "
        f"old generic fallback sentence used={rendering_counts.get('old_generic_fallback_used', 0)}"
        + (
            f", metadata unavailable={rendering_counts.get('rendering_metadata_unavailable', 0)}"
            if rendering_counts.get("rendering_metadata_unavailable", 0)
            else ""
        )
        + "."
    )
    for field, values in (context_quality.get("confidence_dimension_counts") or {}).items():
        out.append(
            f"Confidence {field.replace('_', ' ')}: {compact_counts(values)}"
        )
    if context_quality.get("skip_reason_counts"):
        out.append("Skip reasons:")
        out.append(md_table_row(["reason", "count"]))
        out.append(md_table_row(["---", "---"]))
        for reason, count in context_quality["skip_reason_counts"].items():
            out.append(md_table_row([reason, count]))
    out.append("")

    corpus = report.get("historical_context_corpus_snapshot") or {}
    out.append("## Current historical-context corpus")
    if not corpus.get("available"):
        out.append(
            f"Snapshot incomplete: **{corpus.get('reason') or 'authoritative files unavailable'}**."
        )
    out.append(
        "Completed packets / attribution eligible / attribution ineligible: "
        f"**{corpus.get('completed_packet_count', 'unavailable')} / "
        f"{corpus.get('attribution_eligible_count', 'unavailable')} / "
        f"{corpus.get('completed_attribution_ineligible_count', 'unavailable')}**."
    )
    out.append(
        "Ordinary-post cycle / unresolved / historical-context blocked / allowed: "
        f"**{corpus.get('ordinary_post_cycle_count', 'unavailable')} / "
        f"{corpus.get('unresolved_quote_count', 'unavailable')} / "
        f"{corpus.get('historical_context_blocked_count', 'unavailable')} / "
        f"{corpus.get('historical_context_allowed_count', 'unavailable')}**."
    )
    out.append(
        f"Source-role policy: **{corpus.get('source_role_policy_version') or 'unavailable'}**; "
        f"semantic-gate policy: **{corpus.get('semantic_gate_policy_version') or 'unavailable'}**."
    )
    out.append(
        "Current ledger / gate projection: "
        f"`{str(corpus.get('semantic_review_ledger_sha256') or '')[:16] or 'unavailable'}` / "
        f"`{str(corpus.get('semantic_gate_projection_sha256') or '')[:16] or 'unavailable'}`."
    )
    if corpus.get("file_sha256"):
        out.append("Authoritative snapshot hashes:")
        for label, value in sorted(corpus["file_sha256"].items()):
            out.append(f"- {label}: `{str(value)[:16]}`")
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
            "Latest analytics snapshots with unavailable impressions: "
            f"**{engagement.get('unavailable_impressions_count', 0)}**; "
            "latest analytics snapshots with unavailable URL-link clicks: "
            f"**{engagement.get('unavailable_click_metrics_count', 0)}**."
        )
        warnings = engagement.get("sample_size_warnings") or []
        if warnings:
            out.append("Sample-size warnings: " + ", ".join(str(value) for value in warnings) + ".")
        out.append("All associations are observational; the digest does not attribute causation.")
    out.append("")

    veto_section = report.get("quote_image_semantic_veto_shadow") or {}
    veto_window = veto_section.get("summary") or {}
    veto_runtime = veto_section.get("runtime_summary") or {}
    veto_load_lifecycle = report.get("semantic_veto_load_lifecycle") or {}
    out.append("## Quote/image semantic veto shadow")
    if veto_load_lifecycle.get("resolved_warning_count"):
        latest_resolution = (veto_load_lifecycle.get("resolved_warnings") or [])[-1]
        out.append(
            f"Startup lifecycle: **{veto_load_lifecycle.get('resolved_warning_count')} stale/unavailable "
            "manifest warning(s) resolved by a later successful load**"
            f" (latest resolution `{latest_resolution.get('resolved_at')}`)."
        )
    if veto_load_lifecycle.get("unresolved_warning_count"):
        out.append(
            f"Startup lifecycle: **{veto_load_lifecycle.get('unresolved_warning_count')} "
            "unresolved manifest load warning(s)**."
        )
    configured_available = veto_runtime.get("configured_manifest_available") is True
    if veto_runtime.get("configured_manifest_present"):
        configured_mode = veto_runtime.get("configured_manifest_mode") or "unavailable"
        enforcement = (
            "disabled; observations are advisory and production selection is unchanged"
            if configured_mode == "shadow"
            else "disabled"
            if configured_mode == "disabled"
            else "unavailable"
        )
        out.append(
            f"Mode: **{configured_mode}**; active enforcement: **{enforcement}**."
        )
        out.append(
            "Loaded configured manifest: "
            f"**{veto_runtime.get('configured_manifest_status') or 'unavailable'}**; "
            f"policy **{veto_runtime.get('configured_manifest_policy_version') or 'unavailable'}**; "
            f"hash `{str(veto_runtime.get('configured_manifest_sha256') or '')[:16] or 'unavailable'}`."
        )
        configured_quote_count = veto_runtime.get("configured_manifest_quote_count")
        configured_image_count = veto_runtime.get("configured_manifest_image_count")
        out.append(
            "Authorised pair universe: "
            f"**{configured_quote_count if configured_quote_count is not None else 'unavailable'} "
            f"quotations × {configured_image_count if configured_image_count is not None else 'unavailable'} images "
            f"= {veto_runtime.get('configured_manifest_total_authorised_pair_count', 'unavailable')} pairs**."
        )
        out.append(
            "Pair adjudication state: "
            f"**allow {veto_runtime.get('configured_manifest_allow_pair_count', 'unavailable')}; "
            f"veto {veto_runtime.get('configured_manifest_veto_pair_count', 'unavailable')}; "
            f"{veto_runtime.get('configured_manifest_adjudicated_unknown_pair_count', 'unavailable')} "
            "adjudicated unknown; "
            f"{veto_runtime.get('configured_manifest_not_adjudicated_pair_count', 'unavailable')} "
            "authorised but not yet adjudicated**."
        )
        if veto_runtime.get("configured_manifest_reason"):
            out.append(
                f"Configured manifest warning: **{veto_runtime.get('configured_manifest_reason')}**."
            )
        configured_hash = str(veto_runtime.get("configured_manifest_sha256") or "")
        observed_hashes = {
            str(item.get("manifest_sha256") or "")
            for item in (veto_window.get("manifest_strata") or [])
        }
        if str(veto_window.get("manifest_sha256") or ""):
            observed_hashes.add(str(veto_window.get("manifest_sha256")))
        configured_observed = bool(configured_hash and configured_hash in observed_hashes)
        out.append(
            "Retained runtime-status manifest: "
            f"policy **{veto_runtime.get('manifest_policy_version') or 'unavailable'}**; "
            f"hash `{str(veto_runtime.get('manifest_sha256') or '')[:16] or 'unavailable'}`."
        )
        if configured_observed:
            out.append(
                "Selection observation state: **configured manifest observed active; "
                "any earlier retained-manifest mismatch is resolved**."
            )
        elif veto_runtime.get("runtime_status_matches_configured_manifest") is False:
            out.append(
                "Selection observation state: **awaiting first selection observation under "
                "configured manifest**. The retained runtime-status manifest is older; this "
                "is a lifecycle state, not an unresolved production fault."
            )
    if (
        not veto_window.get("available")
        and not veto_runtime.get("available")
        and not configured_available
    ):
        out.append(f"Unavailable: **{veto_runtime.get('reason') or 'shadow mode disabled'}**.")
    else:
        if veto_window.get("available"):
            summary = veto_window
            out.append(
                f"Selection-time observations: **{summary.get('selection_time_observations', 0)}**; "
                f"confirmed successful posts: **{summary.get('confirmed_successful_posts', 0)}**. "
                "Unconfirmed observations are not counted as posted outcomes."
            )
            if summary.get("mixed_manifest_versions"):
                out.append(
                    f"The window contains **{summary.get('window_event_count_all_manifests', 0)}** observations "
                    f"across **{len(summary.get('manifest_strata') or [])}** manifest versions; headline figures "
                    f"use the latest manifest only and exclude "
                    f"**{summary.get('events_excluded_from_current_manifest_summary', 0)}** older-manifest observations."
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
            incomplete_global = summary.get("quotes_with_incomplete_pair_coverage", 0)
            adjudicated_unknown = summary.get("adjudicated_unknown_selections", 0)
            not_adjudicated = summary.get("not_adjudicated_selections", 0)
            median_delta = summary.get("median_alternative_score_delta")
            version = summary.get("manifest_policy_version") or (
                veto_runtime.get("configured_manifest_policy_version") or "unavailable"
            )
            manifest_hash = summary.get("manifest_sha256") or (
                veto_runtime.get("configured_manifest_sha256") or ""
            )
            failures = summary.get("lookup_failures", 0)
        else:
            summary = veto_runtime
            out.append(f"Runtime observations retained: **{summary.get('events', 0)}**.")
            if summary.get("mixed_manifest_versions"):
                out.append(
                    f"Runtime history contains **{summary.get('history_events_all_manifests', 0)}** observations; "
                    f"the displayed counts exclude **{summary.get('events_excluded_from_current_manifest_summary', 0)}** "
                    "observations from other manifests."
                )
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
            incomplete_global = summary.get("quotes_with_incomplete_pair_coverage", 0)
            adjudicated_unknown = summary.get("adjudicated_unknown_selections", 0)
            not_adjudicated = summary.get("not_adjudicated_selections", 0)
            median_delta = summary.get("alternative_score_delta_median")
            version = summary.get("manifest_policy_version") or (
                summary.get("configured_manifest_policy_version") or "unavailable"
            )
            manifest_hash = summary.get("manifest_sha256") or (
                summary.get("configured_manifest_sha256") or ""
            )
            failures = int(summary.get("manifest_unavailable", 0) or 0) + int(summary.get("manifest_stale", 0) or 0)
        strata = summary.get("manifest_strata") or []
        if strata:
            out.append("Manifest-version strata:")
            out.append(md_table_row(["policy", "manifest hash", "observations", "status counts"]))
            out.append(md_table_row(["---", "---", "---", "---"]))
            for item in strata:
                observations = item.get(
                    "selection_time_observations",
                    item.get("events", 0),
                )
                out.append(
                    md_table_row(
                        [
                            item.get("manifest_policy_version") or "unavailable",
                            str(item.get("manifest_sha256") or "")[:16] or "unavailable",
                            observations,
                            compact_counts(item.get("status_counts") or {}),
                        ]
                    )
                )
            out.append("")
        out.append("Selections:")
        out.append(f"- Allowed: **{allowed}**")
        out.append(f"- Vetoed: **{vetoed}**")
        out.append(f"  - alternative available: **{selection_error}**")
        out.append(f"  - no safe image exists: **{coverage_gap}**")
        out.append(f"- Unknown: **{unknown}**")
        out.append(
            f"  - adjudicated unknown: **{adjudicated_unknown}**; "
            f"not adjudicated/missing: **{not_adjudicated}**"
        )
        out.append(f"- Generated out of scope: **{generated}**")
        out.append(
            f"In-scope historical selections: **{in_scope}**; vetoed with/without an allowed "
            f"candidate in the current set: **{with_alternative} / {without_alternative}**; "
            f"median alternative score delta: "
            f"**{f'{float(median_delta):.2f}' if median_delta is not None else 'unavailable'}**."
        )
        out.append(
            f"Observation stratum manifest: **{version}** "
            f"(`{str(manifest_hash)[:16] or 'unavailable'}`); "
            f"lookup failures: **{failures}**."
        )
        reason_counts = summary.get("veto_reason_counts") or {}
        if reason_counts:
            out.append("Veto reasons:")
            for reason, count in sorted(reason_counts.items(), key=lambda item: (-item[1], item[0])):
                out.append(f"- {reason}: **{count}**")
        out.append("")
        out.append("Coverage state:")
        out.append(f"- quotations with no safe historical image: **{no_global}**")
        out.append(
            "- selected quotations whose full image matrices remain unadjudicated: "
            f"**{incomplete_global}**"
        )
        fully_unadjudicated = (
            veto_runtime.get("configured_manifest_fully_unadjudicated_quotes") or []
        )
        if fully_unadjudicated:
            out.append(
                "- fully unadjudicated quotation matrices in the configured manifest "
                f"(coverage state, not a runtime error): **{len(fully_unadjudicated)}**"
            )
            for item in fully_unadjudicated[:3]:
                preview = item.get("quote_preview") or "text unavailable"
                out.append(
                    f"  - `{item.get('quote_id')}` — {preview}; "
                    f"resolved **{item.get('resolved_pair_count', 0)}**, "
                    f"adjudicated unknown **{item.get('adjudicated_unknown_count', 0)}**, "
                    f"not adjudicated **{item.get('not_adjudicated_count', 0)} / "
                    f"{item.get('authorised_image_count', 0)}**"
                )
        named = veto_runtime.get("configured_manifest_named_quote_coverage")
        if isinstance(named, dict):
            out.append(
                f"- named quotation `{named.get('quote_id')}`: "
                f"allow **{named.get('allow_count', 0)}**; "
                f"veto **{named.get('veto_count', 0)}**; "
                f"adjudicated unknown **{named.get('adjudicated_unknown_count', 0)}**; "
                f"not adjudicated **{named.get('not_adjudicated_count', 0)}**; "
                f"row complete: **{'yes' if named.get('complete') else 'no'}**"
            )
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

    lifecycle = report.get("shadow_feature_lifecycle") or {}
    out.append("## Shadow feature lifecycle")
    if not lifecycle.get("available"):
        out.append(f"Unavailable: **{lifecycle.get('reason') or 'invalid lifecycle register'}**.")
    else:
        out.append(
            "; ".join(
                f"**{feature.get('feature_name')}**=`{feature.get('current_state')}`"
                for feature in lifecycle.get("features", [])
            )
            + "."
        )
        overdue = lifecycle.get("overdue_decisions") or []
        if overdue:
            out.append(
                "Overdue lifecycle decisions: **"
                + ", ".join(
                    f"{row.get('feature_name')} ({row.get('next_decision_date')})"
                    for row in overdue
                )
                + "**."
            )
    out.append("")

    strategy = report.get("reply_strategy") or {}
    out.append("## Conversational reply strategy")
    strategy_summary = (
        f"**{plural_count(strategy.get('conversational_candidate_count', 0), 'conversational candidate')} "
        f"AI-reviewed; {plural_count(strategy.get('confirmed_outcome_count', 0), 'reply', 'replies')} posted; "
        f"{plural_count(strategy.get('terminal_repetition_rejection_count', 0), 'terminal repetition rejection')}; "
        f"{plural_count(strategy.get('terminal_clarification_mode_rejection_count', 0), 'terminal clarification-mode rejection')}"
    )
    if strategy.get("review_classification_available"):
        strategy_summary += (
            f"; {plural_count(strategy.get('ai_reviewed_decline_count', 0), 'AI-reviewed decline')} total "
            f"({strategy.get('deliberately_declined_count', 0)} deliberately declined); "
            f"{plural_count(strategy.get('deterministic_suppression_count', 0), 'deterministic suppression')}; "
            f"{plural_count(strategy.get('terminal_no_reply_decision_count', 0), 'terminal no-reply decision')}.**"
        )
    else:
        strategy_summary += (
            f"; {strategy.get('deliberately_declined_count', 0)} "
            "deliberately declined.**"
        )
    out.append(strategy_summary)
    pipeline_stages = report.get("reply_pipeline_stages") or {}
    if pipeline_stages.get("tested_pipeline_decision_count"):
        out.append(
            "All tested-pipeline decisions: "
            f"**{pipeline_stages.get('tested_pipeline_decision_count', 0)}**; "
            "complete stage telemetry: "
            f"**{pipeline_stages.get('complete_stage_telemetry_count', 0)}**; "
            "partial/legacy telemetry: "
            f"**{pipeline_stages.get('partial_or_legacy_telemetry_count', 0)}**."
        )
    out.append("Generated decisions: " + compact_counts(strategy.get("generated_mode_counts") or {}))
    out.append(
        "Generated final reply kinds: "
        + compact_counts(strategy.get("generated_final_reply_kind_counts") or {})
    )
    out.append("Public outcomes: " + compact_counts(strategy.get("outcome_status_counts") or {}))
    out.append("Published/terminal modes: " + compact_counts(strategy.get("mode_counts") or {}))
    out.append(
        "Published/terminal final reply kinds: "
        + compact_counts(strategy.get("final_reply_kind_counts") or {})
    )
    for lane, counts in (strategy.get("mode_counts_by_lane") or {}).items():
        if counts:
            out.append(f"{lane}: {compact_counts(counts)}")
    out.append(
        f"Grounded decisions generated: **{strategy.get('generated_grounded_count', 0)}**; "
        f"grounded replies posted: **{strategy.get('posted_grounded_count', 0)}**; "
        f"factual decisions generated: **{strategy.get('generated_factual_claim_count', 0)}**."
    )
    out.append(
        f"Trusted facts supplied to generated decisions average/max/none: **"
        f"{round(strategy['generated_average_retrieved_packet_count'], 2) if strategy.get('generated_average_retrieved_packet_count') is not None else 'unavailable'} / "
        f"{strategy.get('generated_maximum_retrieved_packet_count') if strategy.get('generated_maximum_retrieved_packet_count') is not None else 'unavailable'} / "
        f"{strategy.get('generated_no_retrieved_packets_count', 0)}**."
    )
    out.append(
        f"Facts actually referenced/used by generated decisions, when known, average/max/none: **"
        f"{round(strategy['generated_average_evidence_reference_count'], 2) if strategy.get('generated_average_evidence_reference_count') is not None else 'unavailable'} / "
        f"{strategy.get('generated_maximum_evidence_reference_count') if strategy.get('generated_maximum_evidence_reference_count') is not None else 'unavailable'} / "
        f"{strategy.get('generated_no_evidence_references_count', 0)}**."
    )
    out.append("Generated evidence confidence: " + compact_counts(strategy.get("generated_confidence_counts") or {}))
    out.append("Generated tones: " + compact_counts(strategy.get("generated_humour_tone_counts") or {}))
    out.append(
        f"Published/terminal grounded replies: **{strategy.get('grounded_count', 0)}** "
        f"(metadata unavailable: {strategy.get('grounding_metadata_unavailable_count', 0)}); "
        f"claim-free opinion/principle replies: **{strategy.get('claim_free_opinion_or_principle_count', 0)}**; "
        f"humour replies: **{strategy.get('humour_reply_count', 0)}**; "
        f"factual claims: **{strategy.get('factual_claim_count', 0)}** "
        f"(metadata unavailable: {strategy.get('factual_claim_metadata_unavailable_count', 0)}); "
        f"factual grounding rejections: **{strategy.get('factual_rejected_insufficient_grounding_count', 0)}**."
    )
    out.append(
        f"Trusted facts supplied to published/terminal decisions average/max/none: **{round(strategy['average_retrieved_packet_count'], 2) if strategy.get('average_retrieved_packet_count') is not None else 'unavailable'} / "
        f"{strategy.get('maximum_retrieved_packet_count') if strategy.get('maximum_retrieved_packet_count') is not None else 'unavailable'} / "
        f"{strategy.get('no_retrieved_packets_count', 0)}** "
        f"(metadata unavailable: {strategy.get('retrieved_packet_metadata_unavailable_count', 0)})."
    )
    out.append(
        f"Facts actually referenced/used by published/terminal decisions, when known, average/max/none: **"
        f"{round(strategy['average_evidence_reference_count'], 2) if strategy.get('average_evidence_reference_count') is not None else 'unavailable'} / "
        f"{strategy.get('maximum_evidence_reference_count') if strategy.get('maximum_evidence_reference_count') is not None else 'unavailable'} / "
        f"{strategy.get('no_evidence_references_count', 0)}** "
        f"(metadata unavailable: {strategy.get('evidence_reference_metadata_unavailable_count', 0)})."
    )
    out.append("Published/terminal evidence confidence: " + compact_counts(strategy.get("confidence_counts") or {}))
    out.append("Published/terminal tones: " + compact_counts(strategy.get("humour_tone_counts") or {}))
    out.append("No-reply categories: " + compact_counts(strategy.get("no_reply_category_counts") or {}))
    out.append("Repetition controls: " + compact_counts(strategy.get("repetition_control_counts") or {}))
    if strategy.get("rejection_reason_counts"):
        out.append("Editorial no-reply/rejections:")
        out.append(md_table_row(["reason", "count"]))
        out.append(md_table_row(["---", "---"]))
        for reason, count in strategy["rejection_reason_counts"].items():
            out.append(md_table_row([reason, count]))
    if strategy.get("pipeline_failure_reason_counts"):
        out.append("Operational AI-first pipeline failures (retryable, not editorial no-reply):")
        out.append(md_table_row(["reason", "count"]))
        out.append(md_table_row(["---", "---"]))
        for reason, count in strategy["pipeline_failure_reason_counts"].items():
            out.append(md_table_row([reason, count]))
    if strategy.get("routine_skip_reason_counts"):
        out.append("Routine scheduling skips (separate):")
        out.append(md_table_row(["reason", "count"]))
        out.append(md_table_row(["---", "---"]))
        for reason, count in strategy["routine_skip_reason_counts"].items():
            out.append(md_table_row([reason, count]))
    out.append("")

    if (
        pipeline_stages.get("tested_pipeline_decision_count")
        or pipeline_stages.get("evaluation_count")
    ):
        out.append("## Tested reply-pipeline stages")
        out.append(
            f"All tested-pipeline decisions: **"
            f"{pipeline_stages.get('tested_pipeline_decision_count', 0)}**."
        )
        out.append(
            f"All stage-summary events: **"
            f"{pipeline_stages.get('all_stage_summary_event_count', 0)}**."
        )
        out.append(
            f"Complete stage telemetry: **"
            f"{pipeline_stages.get('complete_stage_telemetry_count', 0)}**; "
            f"partial/legacy telemetry: **"
            f"{pipeline_stages.get('partial_or_legacy_telemetry_count', 0)}**."
        )
        versions = pipeline_stages.get("strategy_version_counts") or {}
        if versions:
            out.append("By strategy version:")
            out.append(md_table_row([
                "strategy version",
                "decisions",
                "stage summaries",
                "complete",
                "partial/legacy",
            ]))
            out.append(md_table_row(["---", "---:", "---:", "---:", "---:"]))
            for version, counts in versions.items():
                out.append(md_table_row([
                    version,
                    counts.get("decision_count", 0),
                    counts.get("stage_summary_count", 0),
                    counts.get("complete_stage_telemetry_count", 0),
                    counts.get("partial_or_legacy_telemetry_count", 0),
                ]))
            out.append(
                "Current/latest strategy-version subtotal "
                f"(`{pipeline_stages.get('latest_strategy_version')}`): **"
                f"{pipeline_stages.get('latest_strategy_version_decision_count', 0)}** "
                "tested-pipeline decisions."
            )
        out.append(
            f"Stage-summary events available: **{pipeline_stages.get('evaluation_count', 0)}**; "
            f"provider calls: **{compact_counts(pipeline_stages.get('provider_call_counts') or {})}**; "
            f"schema-invalid calls: **{pipeline_stages.get('schema_invalid_call_count', 0)}**."
        )
        out.append(
            "Gate decisions: "
            + compact_counts(pipeline_stages.get("gate_decision_counts") or {})
            + "; reply-necessity outcomes: "
            + compact_counts(
                pipeline_stages.get("reply_necessity_outcome_counts") or {}
            )
            + "; majority resolvability: "
            + compact_counts(
                pipeline_stages.get(
                    "reply_necessity_majority_resolvable_counts"
                ) or {}
            )
            + f"; gate overturns: {pipeline_stages.get('reply_necessity_overturn_count', 0)}."
        )
        out.append(
            f"Group-hostility candidates/reviews/suppressions: **"
            f"{pipeline_stages.get('group_hostility_candidate_count', 0)} / "
            f"{pipeline_stages.get('group_hostility_review_count', 0)} / "
            f"{pipeline_stages.get('group_hostility_suppression_count', 0)}**; "
            f"outcomes: {compact_counts(pipeline_stages.get('group_hostility_outcome_counts') or {})}."
        )
        out.append(
            f"Allegation/conspiracy candidates/reviews/suppressions: **"
            f"{pipeline_stages.get('allegation_conspiracy_candidate_count', 0)} / "
            f"{pipeline_stages.get('allegation_conspiracy_review_count', 0)} / "
            f"{pipeline_stages.get('allegation_conspiracy_suppression_count', 0)}**; "
            f"outcomes: {compact_counts(pipeline_stages.get('allegation_conspiracy_outcome_counts') or {})}; "
            f"majority resolvability: "
            f"{compact_counts(pipeline_stages.get('allegation_conspiracy_majority_resolvable_counts') or {})}."
        )
        out.append(
            "Attribution routes: "
            + compact_counts(pipeline_stages.get("attribution_route_counts") or {})
            + "; authentication outcomes: "
            + compact_counts(
                pipeline_stages.get("authentication_outcome_counts") or {}
            )
            + "."
        )
        out.append(
            f"Claim-risk evaluations/cleanups: **"
            f"{pipeline_stages.get('claim_risk_evaluation_count', 0)} / "
            f"{pipeline_stages.get('claim_cleanup_count', 0)}**; audit outcomes: "
            f"{compact_counts(pipeline_stages.get('claim_audit_outcome_counts') or {})}."
        )
        out.append(
            f"Exact duplicates/repairs: **"
            f"{pipeline_stages.get('exact_duplicate_count', 0)} / "
            f"{pipeline_stages.get('duplicate_repair_count', 0)}**; repair outcomes: "
            f"{compact_counts(pipeline_stages.get('duplicate_repair_outcome_counts') or {})}; "
            f"final validation: {compact_counts(pipeline_stages.get('final_validation_counts') or {})}."
        )

        majority_utilisation = (
            pipeline_stages.get("majority_review_utilisation") or {}
        )
        majority_coverage = majority_utilisation.get("coverage") or {}
        stage_events_examined = int(
            majority_coverage.get("stage_summary_events_examined", 0) or 0
        )
        if stage_events_examined:
            present_events = int(
                majority_coverage.get(
                    "events_with_majority_review_summaries", 0
                )
                or 0
            )
            absent_events = int(
                majority_coverage.get(
                    "events_without_majority_review_summaries", 0
                )
                or 0
            )
            empty_events = int(
                majority_coverage.get(
                    "events_with_empty_majority_review_summaries", 0
                )
                or 0
            )
            malformed_entries = int(
                majority_coverage.get("malformed_entries", 0) or 0
            )
            duplicate_events = int(
                majority_coverage.get(
                    "events_with_duplicate_family_entries", 0
                )
                or 0
            )
            out.append("")
            out.append("### Majority-review utilisation")
            out.append("")
            if not present_events:
                out.append(
                    "Majority-review utilisation is **unavailable for this "
                    "window**: all stage-summary events predate or lack "
                    "`majority_review_summaries`."
                )
            else:
                out.append(
                    f"Coverage: **{present_events}/{stage_events_examined} "
                    "stage-summary events supplied majority-review telemetry**; "
                    f"present and empty: **{empty_events}**."
                )
                out.append(
                    "Valid majority-family entries: **"
                    f"{majority_coverage.get('valid_majority_family_entries', 0)}"
                    "**; malformed entries excluded: **"
                    f"{malformed_entries}**; events with duplicate families: "
                    f"**{duplicate_events}**."
                )
                overall = majority_utilisation.get("overall") or {}
                out.append(
                    "Overall: **"
                    f"{plural_count(overall.get('completed_family_resolutions', 0), 'resolution')}; "
                    f"{plural_count(overall.get('actual_reviewer_calls_attempted', 0), 'call')} "
                    "made versus "
                    f"{overall.get('fixed_three_call_baseline', 0)} fixed-three "
                    "baseline; "
                    f"{plural_count(overall.get('reviewer_calls_saved', 0), 'call')} "
                    "saved; "
                    f"{plural_count(overall.get('total_invalid_or_unusable_votes', 0), 'invalid/unusable vote')}"
                    "**."
                )
                short_circuit = overall.get("short_circuit_rate_percentage")
                reduction = overall.get(
                    "reviewer_call_reduction_percentage"
                )
                out.append(
                    "Overall short-circuit rate: **"
                    + (
                        f"{float(short_circuit):.1f}%"
                        if short_circuit is not None
                        else "unavailable"
                    )
                    + "**; reviewer-call reduction: **"
                    + (
                        f"{float(reduction):.1f}%"
                        if reduction is not None
                        else "unavailable"
                    )
                    + "**."
                )
                out.append(
                    md_table_row([
                        "Family",
                        "Resolutions",
                        "2-call",
                        "3-call",
                        "Reviewer 3 skipped",
                        "Reviewer 3 called",
                        "Calls made",
                        "Calls saved",
                        "All-valid disagreements",
                        "With invalid/unusable vote",
                    ])
                )
                out.append(md_table_row(["---"] * 10))
                per_family = majority_utilisation.get("per_family") or {}
                table_rows = [
                    (family, per_family.get(family) or {})
                    for family in MAJORITY_REVIEW_FAMILIES
                ]
                table_rows.append(("Overall", overall))
                for family, counts in table_rows:
                    out.append(md_table_row([
                        family,
                        counts.get("completed_family_resolutions", 0),
                        counts.get("two_call_resolutions", 0),
                        counts.get("three_call_resolutions", 0),
                        counts.get(
                            "reviewer_3_skips_due_to_matching_first_two_votes",
                            0,
                        ),
                        counts.get("reviewer_3_calls", 0),
                        counts.get("actual_reviewer_calls_attempted", 0),
                        counts.get("reviewer_calls_saved", 0),
                        counts.get(
                            "three_call_resolutions_with_all_three_votes_valid",
                            0,
                        ),
                        counts.get(
                            "family_resolutions_with_invalid_or_unusable_votes",
                            0,
                        ),
                    ]))
            if absent_events:
                out.append(
                    "**Coverage warning:** "
                    f"{absent_events} legacy/incomplete stage-summary "
                    "event(s) lacked `majority_review_summaries`; they were not "
                    "treated as zero-call or zero-agreement events."
                )
            if malformed_entries:
                out.append(
                    "**Malformed telemetry warning:** "
                    f"{malformed_entries} malformed majority-review entry/entries "
                    "were excluded from utilisation totals."
                )
        out.append("")

    local_rejections = [
        event
        for event in (report.get("events") or [])
        if event.get("kind") == "reply_strategy_local_rejection"
    ]
    if local_rejections:
        out.append("## Effective local reply rejections")
        out.append(
            "These drafts passed an internal pipeline stage but did not become "
            "public replies because a later local guard rejected them."
        )
        for event in local_rejections:
            target = str(event.get("target_id") or "unavailable")
            proposed = event.get("proposed_draft")
            repaired = event.get("repaired_draft")
            out.append("")
            out.append(f"### Target `{target}`")
            out.append("")
            out.append(
                "- Incoming contribution: "
                + json.dumps(
                    event.get("incoming_contribution")
                    or "unavailable (not retained in the log window)",
                    ensure_ascii=False,
                )
            )
            out.append(
                "- Proposed draft: "
                + json.dumps(
                    proposed
                    or (
                        "unavailable (the provider response was not retained "
                        "before the local guard)"
                    ),
                    ensure_ascii=False,
                )
            )
            out.append(
                "- Original local rejection reason: `"
                + str(
                    event.get("original_local_rejection_reason")
                    or event.get("reason")
                    or "unavailable"
                )
                + "`"
            )
            attempted = event.get("direct_answer_repair_attempted")
            out.append(
                "- Direct-answer repair attempted: **"
                + ("yes" if attempted is True else "no" if attempted is False else "unavailable")
                + "**; outcome: `"
                + str(event.get("direct_answer_repair_outcome") or "unavailable")
                + "`"
            )
            if repaired:
                out.append(
                    "- Repaired draft: "
                    + json.dumps(repaired, ensure_ascii=False)
                )
            out.append(
                "- Final effective outcome: `"
                + str(event.get("effective_status") or "local_rejection")
                + "`; reason: `"
                + str(
                    event.get("effective_reason")
                    or event.get("reason")
                    or "unavailable"
                )
                + "`"
            )
        out.append("")

    bounded_protocol_warnings = [
        event
        for event in (report.get("events") or [])
        if event.get("kind") == "quote_pagination_repeated_token"
    ]
    if bounded_protocol_warnings:
        out.append("## Bounded protocol warnings")
        out.append(
            "Repeated quote-pagination tokens ended their individual traversal "
            "as bounded partial successes; they are warnings, not operational failures."
        )
        out.append(
            md_table_row(
                [
                    "time",
                    "post ID",
                    "token fingerprint",
                    "pages completed",
                    "results retained",
                ]
            )
        )
        out.append(md_table_row(["---"] * 5))
        for event in bounded_protocol_warnings:
            out.append(
                md_table_row(
                    [
                        event.get("time", ""),
                        event.get("post_id", ""),
                        event.get("token_fingerprint", ""),
                        event.get("pages_completed", ""),
                        event.get("results_retained", ""),
                    ]
                )
            )
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

    if strategy.get("review_classification_available"):
        usage_report = (
            report.get("provider_usage") or report.get("xai_usage") or {}
        )
        reviewed_decline_keys = {
            (
                normalise_reply_lane(candidate.get("lane")),
                str(candidate.get("context_id") or ""),
            )
            for candidate in (
                (usage_report.get("cost_summary") or {}).get("candidates")
                or []
            )
            if candidate.get("outcome") in {
                "deliberately_declined",
                "terminal_repetition_rejection",
                "terminal_clarification_mode_rejection",
            }
        }
        decline_sections = {
            "mention_grok_skip": ("mention", "mention_id"),
            "hot_post_reply_grok_skip": (
                "hot-post",
                "hot_post_reply_id",
            ),
            "quote_tweet_grok_skip": ("quote-tweet", "quote_tweet_id"),
        }
        for kind, (lane, target_field) in decline_sections.items():
            by_kind[kind] = [
                row
                for row in (by_kind.get(kind) or [])
                if (lane, str(row.get(target_field) or ""))
                in reviewed_decline_keys
            ]

    def section(
        kind: str,
        title: str,
        cols: List[str],
        *,
        column_labels: Optional[Dict[str, str]] = None,
        value_formatters: Optional[Dict[str, Any]] = None,
    ) -> None:
        rows = by_kind.get(kind) or []
        if not rows:
            return
        out.append(f"## {title}")
        labels = column_labels or {}
        formatters = value_formatters or {}
        out.append(md_table_row([labels.get(column, column) for column in cols]))
        out.append(md_table_row(["---"] * len(cols)))
        for ev in rows:
            out.append(md_table_row([
                formatters[c](ev.get(c, "")) if c in formatters else ev.get(c, "")
                for c in cols
            ]))
        out.append("")

    section("quote_image_posted", "Quote/image posts", ["time", "post_id", "line_no", "quote_hash", "image_basename", "image_no", "image_score", "made_with_ai", "text"])
    section("daily_meme_posted", "Daily meme posts", ["time", "post_id", "file", "summary"])
    section("quote_selected", "Regular quote selections", ["time", "line_no", "quote_hash", "weight", "seasonal_boost"])
    section("matched_image_selected", "Matched image selections", ["time", "image", "image_no", "score", "components"])
    section(
        "regular_image_selected",
        "Regular image selection metadata",
        ["time", "source", "basename", "score", "origin_quote_hash", "origin_quote_match", "origin_quote_boost"],
        value_formatters={
            "score": format_display_number,
            "origin_quote_boost": format_display_number,
        },
    )
    section("image_cycle_status", "Image cycle status", ["time", "used_count", "currently_eligible", "remaining_count", "seasonally_excluded", "stale_excluded", "cycle_reset"])
    section("quote_cycle_reset", "Quote cycle resets", ["time", "reason", "affected", "full_selectable", "full_hard_excluded"])
    section("mention_reply_posted", "Mention replies", ["time", "mention_id", "author_id", "incoming_text", "reply", "reply_post_id"])
    section("hot_post_reply_posted", "Hot-post replies", ["time", "hot_post_reply_id", "author_id", "incoming_text", "reply", "reply_post_id"])
    section("quote_tweet_reply_posted", "Quote-tweet replies", ["time", "quote_tweet_id", "author_id", "original_post_id", "incoming_text", "reply", "reply_post_id"])
    section(
        "historical_context_semantic_gate",
        "Historical context semantic gate",
        ["time", "status", "policy_version", "ledger_sha256", "projection_sha256", "blocked_quote_count", "reason"],
    )
    section(
        "historical_context_runtime",
        "Historical-context runtime availability",
        ["time", "status", "regular_post_eligibility_unchanged", "reason"],
    )
    section(
        "reply_evidence_unavailable",
        "Reply evidence unavailable",
        ["time", "lane", "target_id"],
    )
    section(
        "runtime_control_pause",
        "Runtime control pauses",
        ["time", "key", "lanes", "until_epoch"],
    )
    section(
        "clarification_reply_cap_override",
        "Clarification reply cap overrides",
        ["time", "target_id", "thread_id", "author_id", "bypassed_cap"],
    )
    section(
        "clarification_reply_used",
        "Clarification replies used",
        ["time", "target_id", "thread_id", "author_id", "reply_post_id", "trigger"],
    )
    section(
        "repair_reply_completed",
        "Repair replies completed",
        ["time", "target_id", "thread_id", "author_id", "reply_post_id"],
    )
    historical_rows = by_kind.get("historical_context_reply") or []
    if historical_rows:
        semantic_columns = (
            "semantic_review_disposition",
            "semantic_review_ledger_sha256",
            "semantic_review_projection_sha256",
        )
        reliable_semantic_metadata = any(
            all(row.get(field) not in (None, "") for field in semantic_columns)
            for row in historical_rows
        )
        cols = [
            "time", "status", "parent_post_id", "quote_id",
            "weighted_character_count", "verification_label", "source_class",
            "historical_confidence", "formatter_version", "rendering_mode",
            "shortening_applied", "reason",
        ]
        if reliable_semantic_metadata:
            cols.extend(semantic_columns)
        if report.get("verbose_replies"):
            cols.append("reply_preview")
        section(
            "historical_context_reply",
            "Historical context replies",
            cols,
            column_labels={
                "historical_confidence": "overall_reply_confidence",
            },
        )
        if not reliable_semantic_metadata:
            out.append(
                "Per-reply semantic-review disposition and ledger/projection hashes were "
                "not supplied reliably by these events; empty columns are omitted."
            )
            out.append("")
    terminal_context_parents = {
        str(row.get("parent_post_id") or "")
        for row in (by_kind.get("historical_context_obligation") or [])
        if row.get("context_reply_state") in {
            "context_reply_confirmed",
            "context_reply_not_required",
            "context_reply_failed_terminal",
        }
    }
    transaction_rows = by_kind.get("posting_transaction_state") or []
    resolved_pending = [
        row for row in transaction_rows
        if row.get("context_reply_state") == "context_reply_pending"
        and str(row.get("parent_post_id") or "") in terminal_context_parents
    ]
    outstanding_transaction_rows = [
        row for row in transaction_rows
        if row not in resolved_pending
        and row.get("context_reply_state") not in {
            "context_reply_confirmed",
            "context_reply_not_required",
            "context_reply_failed_terminal",
        }
    ]
    if resolved_pending or outstanding_transaction_rows:
        out.append("## Confirmed-main/context transaction states")
        if resolved_pending:
            out.append(
                f"**{len(resolved_pending)}** intermediate `context_reply_pending` "
                "states subsequently reached a terminal outbox state; they are not outstanding."
            )
        if outstanding_transaction_rows:
            out.append("Outstanding intermediate states:")
            transaction_columns = [
                "time",
                "parent_post_id",
                "main_post_state",
                "context_reply_state",
                "context_state_persisted",
                "reason",
            ]
            out.append(md_table_row(transaction_columns))
            out.append(md_table_row(["---"] * len(transaction_columns)))
            for row in outstanding_transaction_rows:
                out.append(md_table_row([
                    row.get(column, "") for column in transaction_columns
                ]))
        out.append("")
    section(
        "historical_context_obligation",
        "Historical-context outbox obligations",
        [
            "time",
            "status",
            "parent_post_id",
            "context_reply_state",
            "attempt_number",
            "remote_work_repeated",
            "error_type",
            "reason",
        ],
    )
    section(
        "historical_context_outbox",
        "Historical-context outbox health",
        [
            "time",
            "status",
            "parent_post_id",
            "error_type",
            "main_post_success_preserved",
            "unrelated_lanes_available",
            "reason",
        ],
    )
    section(
        "daily_meme_failure",
        "Daily meme failures by stage",
        ["time", "stage", "post_id", "error_type", "reason"],
    )
    section(
        "reply_pipeline_stage_summary",
        "Tested reply-pipeline evaluation detail",
        [
            "time",
            "lane",
            "target_id",
            "strategy_version",
            "pipeline_stage_status",
            "pipeline_stage_reason",
            "effective_status",
            "effective_reason",
            "direct_answer_repair_attempted",
            "direct_answer_repair_outcome",
            "xai_gate_decision",
            "reply_necessity_outcome",
            "reply_necessity_majority_resolvable",
            "group_hostility_outcome",
            "allegation_conspiracy_outcome",
            "allegation_conspiracy_majority_resolvable",
            "attribution_route",
            "claim_risk_categories",
            "duplicate_repair_outcome",
            "final_validation",
        ],
    )
    section(
        "reply_strategy_decision",
        "Reply strategy decisions",
        ["time", "lane", "strategy_version", "mode", "reply_requirement", "route_source", "tone", "evidence_confidence", "trusted_facts_supplied_count", "used_fact_count", "factual_claim", "grounded", "reviewer_verdict", "model_call_count", "revision_count", "author_quarantine_evidence", "no_reply_reason"],
    )
    section(
        "reply_strategy_outcome",
        "Reply strategy outcomes",
        ["time", "status", "lane", "target_id", "reply_post_id", "strategy_version", "mode", "reply_requirement", "route_source", "tone", "evidence_confidence", "trusted_facts_supplied_count", "used_fact_count", "factual_claim", "grounded", "reviewer_verdict", "model_call_count", "revision_count", "failure_reason"],
    )
    section(
        "reply_strategy_failure",
        "Operational reply-pipeline failures",
        ["time", "status", "lane", "target_id", "strategy_version", "reason", "model_call_count", "revision_count", "author_quarantine_evidence"],
    )
    section(
        "reply_target_terminal",
        "Terminal reply targets",
        ["time", "lane", "target_id", "outcome", "reason"],
    )
    section("hot_post_search_result", "Hot-post recent-search results", ["time", "original_post_id", "candidates"])
    deterministic_rows = strategy.get("deterministic_suppressions") or []
    if deterministic_rows:
        out.append("## Deterministic suppressions (no AI/provider review)")
        deterministic_columns = [
            "time",
            "lane",
            "target_id",
            "author_id",
            "original_post_id",
            "incoming_text",
            "route_source",
            "model_call_count",
            "reason",
        ]
        out.append(md_table_row(deterministic_columns))
        out.append(md_table_row(["---"] * len(deterministic_columns)))
        for row in deterministic_rows:
            out.append(md_table_row([
                row.get(column, "") for column in deterministic_columns
            ]))
        out.append("")
    section("mention_grok_skip", "Mention AI-reviewed declines", ["time", "mention_id", "author_id", "incoming_text"])
    section("hot_post_reply_grok_skip", "Hot-post AI-reviewed declines", ["time", "hot_post_reply_id", "author_id", "incoming_text"])
    section("quote_tweet_grok_skip", "Quote-tweet AI-reviewed declines", ["time", "quote_tweet_id", "author_id", "original_post_id", "incoming_text"])
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
        out.append("## Transactional receipt lifecycle")
        if receipt_events:
            lifecycle_summary = summarise_main_post_receipt_lifecycle(
                receipt_events
            )
            outstanding = lifecycle_summary["unresolved"]
            normal_pairs = lifecycle_summary["completed_count"]
            out.append(
                f"Routine two-phase receipt write/remove pairs completed: **{normal_pairs}**. "
                "The write event can be logged at WARNING while still being a normal durable "
                "transaction step; it is not an incident by itself."
            )
            out.append(
                "Completed main-post receipt lifecycles by lane: "
                f"regular quote/image **{lifecycle_summary['regular_completed_count']}**; "
                f"daily-meme **{lifecycle_summary['meme_completed_count']}**."
            )
            if lifecycle_summary["boundary_removal_count"]:
                out.append(
                    "Reconciled main-post receipt removals whose opening write was "
                    "outside the selected window: "
                    f"**{lifecycle_summary['boundary_removal_count']}**."
                )
            if outstanding:
                out.append("Stale or unresolved receipt events:")
                out.append(md_table_row(["time", "level", "lane", "kind", "post_id", "quote_hash", "image/file", "message"]))
                out.append(md_table_row(["---", "---", "---", "---", "---", "---", "---", "---"]))
            for item in outstanding:
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
    reconciled_ambiguity_receipts = (
        reply_recovery.get("durably_reconciled_ambiguity_receipts") or []
    )
    reconciled_ambiguity_event_counts = Counter(
        (
            _normalise_lane(item.get("lane")),
            str(item.get("target_id") or ""),
            str(item.get("source_time") or ""),
        )
        for item in reconciled_ambiguity_receipts
        if isinstance(item, dict)
        and item.get("lane")
        and item.get("target_id")
        and item.get("source_time")
    )
    if reply_receipt_events or reply_recovery_warnings:
        pending_sending_receipts: Dict[
            Tuple[str, str], List[Dict[str, Any]]
        ] = {}
        pending_reply_receipts: Counter = Counter()
        pending_reconciliations: List[
            Tuple[Tuple[str, str, str], Dict[str, Any]]
        ] = []
        unmatched_reply_receipts: List[Dict[str, Any]] = []
        normal_reply_pairs = 0
        terminal_reply_removals_outside_window = 0
        definite_non_success_clears = 0
        confirmed_state_fallback_clears = 0
        reconciled_ambiguity_sending_receipts = 0

        def clear_latest_reconciliation(
            *,
            identity: Tuple[str, str, str] | None = None,
            lane: str | None = None,
        ) -> Tuple[str, str, str] | None:
            for index in range(len(pending_reconciliations) - 1, -1, -1):
                candidate_identity, _item = pending_reconciliations[index]
                if identity is not None and candidate_identity != identity:
                    continue
                if lane is not None and candidate_identity[0] != lane:
                    continue
                pending_reconciliations.pop(index)
                return candidate_identity
            return None

        for item in reply_receipt_events:
            sending_identity = (
                str(item.get("lane") or ""),
                str(item.get("target_id") or ""),
            )
            identity = (
                *sending_identity,
                str(item.get("reply_post_id") or ""),
            )
            kind = str(item.get("kind") or "")
            if kind == "sending":
                pending_sending_receipts.setdefault(
                    sending_identity, []
                ).append(item)
            elif kind == "promoted":
                pending_for_identity = pending_sending_receipts.get(
                    sending_identity, []
                )
                if pending_for_identity:
                    pending_for_identity.pop()
                pending_reply_receipts[identity] += 1
            elif kind == "sending_removed":
                pending_for_identity = pending_sending_receipts.get(
                    sending_identity, []
                )
                if pending_for_identity:
                    pending_for_identity.pop()
                # The matching pre-send event may be outside the selected log
                # window.  This terminal event still proves that the receipt
                # was cleared after a definite non-success.
                definite_non_success_clears += 1
            elif kind == "confirmed_state_fallback_removed":
                pending_for_identity = pending_sending_receipts.get(
                    sending_identity, []
                )
                if pending_for_identity:
                    pending_for_identity.pop()
                # Likewise, a digest window can begin after the sending event.
                # The terminal fallback event is self-contained evidence that
                # the confirmed reply identity was durably preserved.
                confirmed_state_fallback_clears += 1
            elif kind == "written":
                pending_reply_receipts[identity] += 1
            elif kind == "reconciled":
                pending_reconciliations.append((identity, item))
            elif kind == "removed":
                clear_latest_reconciliation(identity=identity)
                if pending_reply_receipts[identity] > 0:
                    pending_reply_receipts[identity] -= 1
                    normal_reply_pairs += 1
                else:
                    # The opening write can legitimately precede the selected
                    # window.  A removal is nevertheless terminal evidence,
                    # not an unresolved receipt.
                    terminal_reply_removals_outside_window += 1
            elif kind in {
                "replay_suppressed_mention_check",
                "replay_suppressed_quote_tweet_check",
            }:
                completed_identity = clear_latest_reconciliation(
                    lane=sending_identity[0]
                )
                if (
                    completed_identity is not None
                    and pending_reply_receipts[completed_identity] > 0
                ):
                    pending_reply_receipts[completed_identity] -= 1
            else:
                unmatched_reply_receipts.append(item)
        unresolved_reply_receipts = list(unmatched_reply_receipts)
        for (lane, target_id), pending_events in sorted(
            pending_sending_receipts.items()
        ):
            for source in pending_events:
                event_identity = (
                    _normalise_lane(lane),
                    target_id,
                    str(source.get("time") or ""),
                )
                if reconciled_ambiguity_event_counts[event_identity] > 0:
                    reconciled_ambiguity_event_counts[event_identity] -= 1
                    reconciled_ambiguity_sending_receipts += 1
                    continue
                unresolved_reply_receipts.append(
                    {
                        **source,
                        "lane": lane,
                        "target_id": target_id,
                        "kind": "sending_unresolved",
                        "message": (
                            "Pre-send reply receipt remains unresolved at the end "
                            "of the observed window"
                        ),
                    }
                )
        pending_reconciliation_counts = Counter(
            identity for identity, _item in pending_reconciliations
        )
        for identity, source in pending_reconciliations:
            unresolved_reply_receipts.append(
                {
                    **source,
                    "lane": identity[0],
                    "target_id": identity[1],
                    "reply_post_id": identity[2],
                    "kind": "reconciliation_unresolved",
                    "message": (
                        "Confirmed-reply reconciliation began, but no terminal "
                        "receipt removal or completion was observed"
                    ),
                }
            )
        for (lane, target_id, reply_post_id), count in sorted(
            pending_reply_receipts.items()
        ):
            if count <= 0:
                continue
            identity = (lane, target_id, reply_post_id)
            if pending_reconciliation_counts[identity] >= count:
                continue
            source = next(
                (
                    item
                    for item in reversed(reply_receipt_events)
                    if str(item.get("kind") or "") in {"written", "promoted"}
                    and str(item.get("lane") or "") == lane
                    and str(item.get("target_id") or "") == target_id
                    and str(item.get("reply_post_id") or "") == reply_post_id
                ),
                {},
            )
            unresolved_reply_receipts.append(
                {
                    **source,
                    "lane": lane,
                    "target_id": target_id,
                    "reply_post_id": reply_post_id,
                    "kind": "confirmed_unresolved",
                    "message": (
                        "Confirmed reply receipt remains unresolved at the end "
                        "of the observed window"
                    ),
                }
            )
        has_actual_recovery = bool(
            reply_recovery_warnings
            or unresolved_reply_receipts
            or confirmed_state_fallback_clears
        )
        out.append(
            "## Confirmed-reply recovery"
            if has_actual_recovery
            else "## Confirmed-reply receipt lifecycle"
        )
        if reply_receipt_events:
            out.append(
                f"Routine confirmed-reply receipt write/remove pairs completed: "
                f"**{normal_reply_pairs}**."
            )
            if definite_non_success_clears:
                out.append(
                    "Prepared reply receipts cleared after a definite non-success: "
                    f"**{definite_non_success_clears}**."
                )
            if confirmed_state_fallback_clears:
                out.append(
                    "Confirmed replies preserved through the durable canonical-state "
                    f"fallback: **{confirmed_state_fallback_clears}**."
                )
            if reconciled_ambiguity_sending_receipts:
                out.append(
                    "Sending-receipt barrier observations durably reconciled with "
                    "their remote-write ambiguity: "
                    f"**{reconciled_ambiguity_sending_receipts}**. They remain "
                    "visible under historical/resolved incident errors."
                )
            if terminal_reply_removals_outside_window:
                out.append(
                    "Confirmed-reply receipt removals whose opening write was outside "
                    f"the observed window: **{terminal_reply_removals_outside_window}**."
                )
            if unresolved_reply_receipts:
                out.append("Stale or unresolved confirmed-reply receipts:")
                out.append(md_table_row(["time", "level", "lane", "kind", "target_id", "reply_post_id", "message"]))
                out.append(md_table_row(["---", "---", "---", "---", "---", "---", "---"]))
            for item in unresolved_reply_receipts:
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
            "tweet-create requests observed: "
            f"**{api_health.get('tweet_create_request_count', 0)}**; "
            "media-upload requests observed: "
            f"**{api_health.get('media_upload_request_count', 0)}**; "
            "failed post/reply requests: "
            f"**{api_health.get('posting_attempt_count', 0)}**; "
            f"reply-target eligibility 403 responses: **{api_health.get('target_eligibility_403_count', 0)}**; "
            f"deleted/inaccessible-tweet 403 responses: "
            f"**{api_health.get('deleted_or_inaccessible_tweet_403_count', 0)}**; "
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
            out.append(md_table_row(["time", "classification", "service", "endpoint", "status", "lane", "target", "message"]))
            out.append(md_table_row(["---", "---", "---", "---", "---", "---", "---", "---"]))
            for item in handled_restrictions:
                out.append(md_table_row([
                    item.get("time", ""),
                    str(item.get("restriction_kind") or "other").replace("_", " "),
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
            deleted_count = int(
                api_health.get("deleted_or_inaccessible_tweet_403_count", 0) or 0
            )
            target_count = int(api_health.get("target_eligibility_403_count", 0) or 0)
            if legacy_cooldowns:
                out.append(
                    "403 restriction summary: deterministic target restrictions were identified; "
                    f"**{legacy_cooldowns} legacy cooldown activation(s)** in this historical window "
                    "were caused by the pre-fix classification."
                )
            else:
                out.append(
                    "403 restriction summary: "
                    f"{plural_count(deleted_count, 'deleted/inaccessible target')} and "
                    f"{plural_count(target_count, 'reply-target eligibility restriction')} "
                    "were handled locally without being presented as current independent errors."
                )
            out.append("")

    self_test_errors = report.get("self_test_errors") or []
    if self_test_errors:
        out.append("## Self-test failures")
        out.append(md_table_row(["time", "level", "where", "message"]))
        out.append(md_table_row(["---", "---", "---", "---"]))
        for e in self_test_errors:
            out.append(md_table_row([e.get("time"), e.get("level"), e.get("where"), e.get("message")]))
        out.append("")

    error_health = report.get("error_health") or {}
    current_incidents = error_health.get("current_incidents") or []
    historical_incidents = error_health.get("historical_resolved_incidents") or []
    transient_observations = error_health.get("transient_provider_observations") or []
    out.append("## Transient provider observations")
    if not transient_observations:
        out.append("None observed in the selected window.")
    else:
        out.append(
            "A transient provider failure was observed. Provider recovery is unverified: "
            "these point-in-time observations are neither current local safety incidents "
            "nor historically resolved incidents."
        )
        out.append(
            md_table_row(
                [
                    "category",
                    "first seen",
                    "last seen",
                    "error records",
                    "tracebacks",
                    "locations",
                ]
            )
        )
        out.append(md_table_row(["---"] * 6))
        for observation in transient_observations:
            out.append(
                md_table_row(
                    [
                        str(observation.get("category") or "").replace("_", " "),
                        observation.get("first_seen", ""),
                        observation.get("last_seen", ""),
                        observation.get("record_count", 0),
                        observation.get("traceback_count", 0),
                        ", ".join(observation.get("affected_locations") or []),
                    ]
                )
            )
    out.append("")

    out.append("## Current independent errors")
    if not current_incidents:
        out.append("None unresolved in the selected window.")
    else:
        out.append(
            md_table_row(
                [
                    "category",
                    "first seen",
                    "last seen",
                    "error records",
                    "tracebacks",
                    "locations",
                    "root summary",
                ]
            )
        )
        out.append(md_table_row(["---"] * 7))
        for incident in current_incidents:
            out.append(
                md_table_row(
                    [
                        str(incident.get("category") or "").replace("_", " "),
                        incident.get("first_seen", ""),
                        incident.get("last_seen", ""),
                        incident.get("record_count", 0),
                        incident.get("traceback_count", 0),
                        ", ".join(incident.get("affected_locations") or []),
                        incident.get("summary", ""),
                    ]
                )
            )
    out.append("")

    out.append("## Historical/resolved incident errors")
    if not historical_incidents:
        out.append("None identified in the selected window.")
    else:
        out.append(
            "Repeated tracebacks are grouped under their root incident and retained here as "
            "historical evidence; they do not determine the current-health headline."
        )
        out.append(
            md_table_row(
                [
                    "category",
                    "first seen",
                    "last seen",
                    "error records",
                    "tracebacks",
                    "resolution",
                    "resolved at",
                ]
            )
        )
        out.append(md_table_row(["---"] * 7))
        for incident in historical_incidents:
            out.append(
                md_table_row(
                    [
                        str(incident.get("category") or "").replace("_", " "),
                        incident.get("first_seen", ""),
                        incident.get("last_seen", ""),
                        incident.get("record_count", 0),
                        incident.get("traceback_count", 0),
                        incident.get("resolution_reason", ""),
                        incident.get("resolution_time", ""),
                    ]
                )
            )
    out.append("")

    resolved_semantic_warning_times = {
        item.get("time")
        for item in (report.get("semantic_veto_load_lifecycle") or {}).get(
            "resolved_warnings", []
        )
    }
    warnings = [
        item
        for item in report.get("errors_and_warnings") or []
        if item.get("level") == "WARNING"
        and not (
            item.get("time") in resolved_semantic_warning_times
            and "Quote/image semantic-veto shadow unavailable" in str(item.get("message") or "")
        )
    ]
    out.append("## Other warnings")
    if not warnings:
        out.append("None found in selected window.")
    else:
        out.append(md_table_row(["time", "where", "message"]))
        out.append(md_table_row(["---", "---", "---"]))
        for item in warnings:
            out.append(
                md_table_row(
                    [item.get("time"), item.get("where"), item.get("message")]
                )
            )
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
        out.append("## On-disk local configuration overrides")
        if cfg.get("_config_source") == "mrsMThatcher.local.json":
            out.append(
                f"On-disk override source: `{cfg.get('_config_source_path')}`; "
                f"file timestamp: `{cfg.get('_config_source_time')}`."
            )
            out.append(
                "This read-only file observation does not establish which "
                "values are effective in a live process."
            )
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
            "MAX_AUTO_REPLIES_PER_DAY", "MAX_REPLIES_PER_AUTHOR_PER_DAY", "MAX_QUOTE_REPLIES_PER_DAY", "MIN_SECONDS_BETWEEN_REPLIES",
            "REPLY_CHECK_EVERY_SECONDS", "MAX_MENTIONS_PER_CHECK", "MENTIONS_MAX_PAGES_PER_CHECK",
            "AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD",
            "AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS",
            "AUTHOR_NO_REPLY_QUARANTINE_SECONDS",
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
    else:
        runtime_config_status = report.get("runtime_config_status") or {}
        out.append("## On-disk local configuration overrides")
        out.append(
            "On-disk local overrides: **unavailable** "
            f"(`{runtime_config_status.get('status') or 'not read'}`; "
            f"source `{runtime_config_status.get('path') or 'unavailable'}`)."
        )
        out.append(
            "Effective live configuration is not established; no digest resume "
            "snapshot or historical startup log is presented as current configuration."
        )
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
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.",
        suffix=".tmp",
        dir=output_path.parent,
        text=True,
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, output_path)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise


def validate_output_destinations(
    output_paths: Iterable[Path],
    logs: Iterable[Path],
    state_file: Path,
) -> None:
    """Reject destinations that would destroy digest inputs or resume state."""
    resolved_logs = {path.expanduser().resolve() for path in logs}
    resolved_state = state_file.expanduser().resolve()
    for output in output_paths:
        resolved_output = output.expanduser().resolve()
        if resolved_output in resolved_logs:
            raise SystemExit(f"Refusing to write digest: output path aliases an input log: {resolved_output}")
        if resolved_output == resolved_state:
            raise SystemExit(
                f"Refusing to write digest: output path aliases the resume-state file: {resolved_output}"
            )


def main(argv: Optional[List[str]] = None) -> int:
    """Run the command-line entry point."""
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
    ap.add_argument(
        "--detailed-appendix",
        action="store_true",
        help="Include detailed filename appendices that are abbreviated in the readable digest.",
    )
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
    """Run digest analysis, delivery, and resume-state persistence transactionally."""
    generation_time = datetime.now()
    if args.logs:
        logs = resolve_explicit_logs(args.logs, project_dir)
    else:
        logs = discover_logs(project_dir, args.glob)

    if not logs:
        raise SystemExit(
            f"No log files found. Run this in the log directory or pass files explicitly. "
            f"Auto-discovery pattern was: {args.glob!r}"
        )

    validate_output_destinations(
        (path for path in (args.output, args.markdown_output, args.json_output) if path is not None),
        logs,
        state_file,
    )

    since_source = None
    since_exclusive = False
    resume_boundary_counts: Counter[str] = Counter()
    saved_resume_tail: List[str] = []
    resume_cursor_mode = "timestamp"
    resume_tail_match_length = 0
    resume_data: Dict[str, Any] = {}

    if args.since:
        try:
            since = parse_dt(args.since)
        except ValueError as exc:
            raise SystemExit(str(exc)) from exc
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
            resume_boundary_counts = resume_boundary_fingerprint_counts(resume_data)
            saved_resume_tail = resume_fingerprint_tail(resume_data)
        if since or saved_resume_tail:
            since_source = "saved resume state"
            since_exclusive = not bool(resume_boundary_counts)
    else:
        since = None

    try:
        until = parse_dt(args.until)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    physical_records = read_records(logs, None, until, physical_order=True)
    tail_match = None
    if since_source == "saved resume state" and saved_resume_tail:
        tail_match = locate_resume_fingerprint_tail(physical_records, saved_resume_tail)
    if tail_match is not None:
        cursor_end, resume_tail_match_length = tail_match
        records = physical_records[cursor_end:]
        resume_cursor_mode = "fingerprint_tail"
    else:
        if saved_resume_tail:
            print(
                "WARNING: saved physical resume cursor was not found in retained logs; "
                "falling back to the timestamp boundary",
                file=sys.stderr,
            )
        records = filter_records_by_time(
            physical_records,
            since,
            since_exclusive=since_exclusive,
        )
        if since is not None and resume_boundary_counts:
            records = filter_resume_boundary_records(records, since, resume_boundary_counts)
    input_files = summarize_input_files(logs, since, until, since_exclusive=since_exclusive)
    initial_active_xai_context = None
    initial_active_xai_call_attempt = None
    initial_pending_mention = None
    initial_pending_qt = None
    if since_source == "saved resume state":
        if isinstance(resume_data.get("last_active_xai_context"), dict):
            initial_active_xai_context = resume_data.get("last_active_xai_context")
        if isinstance(resume_data.get("last_active_xai_call_attempt"), dict):
            initial_active_xai_call_attempt = resume_data.get(
                "last_active_xai_call_attempt"
            )
        if isinstance(resume_data.get("last_pending_mention"), dict):
            initial_pending_mention = dict(resume_data.get("last_pending_mention") or {})
            initial_pending_mention["considered_seq"] = -1
        if isinstance(resume_data.get("last_pending_qt"), dict):
            initial_pending_qt = dict(resume_data.get("last_pending_qt") or {})
            initial_pending_qt["considered_seq"] = -1
    try:
        current_remote_write_safety = remote_write_safety_snapshot(project_dir)
    except Exception as exc:
        current_remote_write_safety = {
            "configured": True,
            "available": False,
            "status": "inspection_failed",
            "blocking": None,
            "reason": f"{type(exc).__name__}: {exc}",
        }
    report = analyse(
        records,
        max_text=args.max_text,
        initial_active_xai_context=initial_active_xai_context,
        initial_active_xai_call_attempt=initial_active_xai_call_attempt,
        initial_pending_mention=initial_pending_mention,
        initial_pending_qt=initial_pending_qt,
        current_remote_write_safety=current_remote_write_safety,
        generation_time=generation_time,
    )
    report["generation_time"] = dt_text(generation_time)
    report["generation_epoch"] = int(generation_time.timestamp())
    report["remote_write_safety"] = current_remote_write_safety
    report_window_end = until or (max((record.ts for record in records), default=None))

    report["log_files"] = [str(p) for p in logs]
    report["input_files"] = input_files
    report["input_warning"] = None
    if (
        not records
        and resume_cursor_mode != "fingerprint_tail"
        and any(int(item.get("records_in_window") or 0) > 0 for item in input_files)
    ):
        report["input_warning"] = (
            "selected log sources contain timestamped records inside the requested window, "
            "but 0 records survived filtering"
        )
    report["input_retention_coverage"] = input_retention_coverage(input_files, since)
    report["input_warning"] = combine_input_warnings(
        report["input_warning"],
        report["input_retention_coverage"].get("warning"),
    )
    report["requested_since"] = dt_text(since) if since else None
    report["requested_until"] = dt_text(until) if until else None
    report["since_source"] = since_source
    report["since_exclusive"] = since_exclusive
    report["resume_cursor_mode"] = resume_cursor_mode
    report["resume_tail_match_length"] = resume_tail_match_length
    report["local_clock_rollback_count"] = sum(
        current.ts < previous.ts
        for previous, current in zip(records, records[1:])
    )
    report["resume_boundary_fingerprint_count"] = len(resume_boundary_counts)
    report["resume_boundary_occurrence_count"] = sum(resume_boundary_counts.values())
    report["project_dir"] = str(project_dir)
    report["resume_state_file"] = None if args.no_state else str(state_file)
    report["state_updated"] = False
    report["generated_image_pool_health"] = (
        generated_pool_health_snapshot(project_dir, now=report_window_end)
        if report_window_end is not None
        else generated_pool_health_snapshot(project_dir)
    )
    report["historical_context_corpus_snapshot"] = historical_context_corpus_snapshot(
        project_dir
    )
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
    report["shadow_feature_lifecycle"] = shadow_lifecycle_snapshot(project_dir)
    veto_section = report.setdefault("quote_image_semantic_veto_shadow", {"events": [], "summary": {}})
    veto_section["runtime_summary"] = quote_image_semantic_veto_shadow_snapshot(project_dir)

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

    runtime_state, runtime_state_path, runtime_state_ts, runtime_state_status = (
        load_current_runtime_state(project_dir)
    )
    report["runtime_state_status"] = {
        "status": runtime_state_status,
        "path": str(runtime_state_path),
    }
    report["latest_state"] = (
        summarize_latest_state(
            runtime_state,
            runtime_state_ts,
            source="bot_state.json",
            source_path=runtime_state_path,
        )
        if runtime_state is not None
        else {}
    )

    runtime_config, runtime_config_path, runtime_config_ts, runtime_config_status = (
        load_current_runtime_config(project_dir)
    )
    report["runtime_config_status"] = {
        "status": runtime_config_status,
        "path": str(runtime_config_path),
        "time": dt_text(runtime_config_ts) if runtime_config_ts else None,
    }
    report["latest_config"] = runtime_config or {}
    refresh_derived(report)

    if not records:
        report["saved_last_log_entry_time"] = dt_text(since) if since else None

    runway_config = load_runway_config(project_dir, dict(report.get("latest_config") or {}))
    report["generated_image_post_rates"] = generated_post_rate_history(
        logs,
        now=report_window_end,
    )
    report["generated_image_pool_runway"] = generated_pool_runway(report.get("generated_image_pool_health") or {}, report["generated_image_post_rates"], runway_config)
    report["generated_image_utilisation"] = generated_image_utilisation(
        report.get("generated_image_pool_health") or {},
        report["generated_image_post_rates"],
        limit=100000 if getattr(args, "detailed_appendix", False) else 10,
    )
    report["verbose_replies"] = bool(args.verbose_replies)
    report["detailed_appendix"] = bool(getattr(args, "detailed_appendix", False))
    selected_window_start = since or min(
        (record.ts for record in records), default=None
    )
    selected_window_end = until or max(
        (record.ts for record in records), default=None
    )
    report["openai_published_cost"] = openai_published_cost_report(
        cache_path=OPENAI_COST_CACHE_PATH,
        window_start_local=selected_window_start,
        window_end_local=selected_window_end,
        generation_time_local=generation_time,
        provider_usage=report.get("provider_usage") or report.get("xai_usage") or {},
    )

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
        last_ts = max(record.ts for record in physical_records)
        save_resume_time(
            state_file,
            last_ts,
            records,
            report,
            logs,
            preserve_existing_context=not args.reset_state,
            merge_existing_boundary_occurrences=since_source == "saved resume state",
            cursor_fingerprint_tail=[
                record_fingerprint(record)
                for record in physical_records[-RESUME_FINGERPRINT_TAIL_LIMIT:]
            ],
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
