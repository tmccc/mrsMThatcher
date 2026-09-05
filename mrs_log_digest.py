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

Enhanced v10: keeps the v9 retention checks, adds a
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
import subprocess
import sys
import tempfile
from collections import Counter
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple, Union
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

# Explicit imports preserve the existing digest helper import surface.
from mrs_log_digest_values import (
    MAX_REASONABLE_STATE_EPOCH,
    PUBLISHED_REPLY_TEXT_MAX_CHARACTERS,
    SHA256_LOWER_RE,
    PUBLIC_POST_ID_RE,
    valid_public_post_id,
    valid_string_public_post_id,
    valid_bounded_utf8_text,
    bounded_event_text,
    bounded_event_nonnegative_integer,
    bounded_event_boolean,
    _count_optional,
    most_common_with_cutoff_ties,
    GENERATED_POLICIES,
    REMOTE_WRITE_RECEIPT_ROLE_LABELS,
    UNKNOWN_MISSING_STATE_FIELD,
    ENGAGEMENT_CORRELATION_WARNING_LIMIT,
    parse_dt,
    dt_text,
    bounded_exception_status,
    int_or_none,
    short,
    plural_count,
    cooldown_state_text,
    _parse_openai_cost_decimal,
    _openai_decimal_text,
    _parse_openai_utc,
    _openai_window_text,
    _normalise_lane,
    _human_snapshot_age,
)
from mrs_log_digest_costs import (
    OPENAI_COST_CACHE_SCHEMA_VERSION,
    OPENAI_COST_CACHE_SOURCE,
    OPENAI_COST_CACHE_MAX_BYTES,
    OPENAI_COST_CACHE_STALE_AFTER_SECONDS,
    OPENAI_COST_SAMPLE_BOUNDARY_MAX_GAP_SECONDS,
    _validate_openai_money_map,
    local_digest_time_to_utc,
    estimate_openai_cost_window,
    load_openai_cost_cache as _load_openai_cost_cache,
    prepare_openai_published_cost_report as _prepare_openai_published_cost_report,
)
from mrs_log_digest_runtime import (
    CURRENT_RUNTIME_STATE_MAX_BYTES,
    CURRENT_RUNTIME_CONFIG_MAX_BYTES,
    CURRENT_CONFIG_REPORT_KEYS,
    REMOTE_WRITE_CONTROL_BOOLEAN_KEYS,
    REMOTE_WRITE_CONTROL_TIME_KEYS,
    REMOTE_WRITE_CONTROL_ALLOWED_KEYS,
    _control_boolean,
    _control_epoch,
    load_current_runtime_state as _load_current_runtime_state,
    load_current_runtime_config as _load_current_runtime_config,
    runtime_control_snapshot as _runtime_control_snapshot,
)
from mrs_log_digest_corpus import (
    historical_context_corpus_snapshot as _historical_context_corpus_snapshot,
)
from mrs_log_digest_generated_pool import (
    GENERATED_BASENAME_RE,
    GENERATED_ANALYSIS_SCHEMA_VERSION,
    GENERATED_ANALYSIS_KIND,
    GENERATED_AUDIT_SCHEMA_VERSION,
    GENERATED_AUDIT_KIND,
    generated_pool_health_snapshot as _generated_pool_health_snapshot,
)
from mrs_log_digest_historical_events import (
    _HISTORICAL_CONTEXT_VERIFICATION_LABELS,
    _HISTORICAL_CONTEXT_FORMATTER_VERSIONS,
    _HISTORICAL_CONTEXT_SOURCE_ROLE_AUDIT_VERSIONS,
    _HISTORICAL_CONTEXT_CONFIDENCE_DIMENSIONS,
    _HISTORICAL_CONTEXT_CONFIDENCE_VALUES,
    _historical_context_verification_counts,
    _historical_context_confidence_dimension_counts,
    historical_context_quality_summary,
    record_historical_context_semantic_gate,
    record_historical_context_runtime,
    record_historical_context_obligation,
    record_historical_context_outbox,
    prepare_historical_context_reply,
    count_historical_context_reply,
)
from mrs_log_digest_generated_identity import (
    generated_identity_shadow_summary,
    generated_identity_policy_summary,
    record_generated_identity_shadow,
    record_generated_identity_policy,
)
from mrs_log_digest_markdown import (
    format_rank,
    format_display_number,
    format_openai_usd,
    md_table_row,
    _cfg_bool,
    _seconds_to_minutes_text,
    _source_bits_for_state_config,
    _carried_state_presentation,
    render_markdown as _render_digest_markdown,
)

LOG_RE = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\s+"
    r"(?P<level>[A-Z]+)\s+"
    r"(?P<src>[^:]+?)(?::(?P<line>\d+))? - (?P<msg>.*)$"
)
# Version 3 is a major-versioned compatibility contract. Increment this integer
# before removing or renaming a JSON field, changing an established field's type
# or meaning, or otherwise making a consumer-visible incompatible change. Purely
# additive fields do not require an increment within a major version.
DIGEST_JSON_SCHEMA_VERSION = 3
DIGEST_JSON_OUTPUT_KIND = "mrs_log_digest"
DIGEST_SOURCE_MAX_BYTES = 4 * 1024 * 1024
CONFIRMED_REPLY_RECEIPT_MAX_BYTES = 1024 * 1024
HISTORICAL_REPLY_HISTORY_MAX_BYTES = 64 * 1024 * 1024
AUTHOR_NO_REPLY_PROGRESS_MAX_AUTHORS = 25_000
AUTHOR_NO_REPLY_PROGRESS_MAX_THRESHOLD = 250_000
AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY = (
    "single_sol_explicit_spam_or_abuse_v2"
)
AUTHOR_EVALUATION_QUARANTINE_PREVIOUS_EVIDENCE_POLICY = (
    "majority_resolvable_terminal_no_reply_v3"
)
AUTHOR_EVALUATION_QUARANTINE_SINGLE_SOL_V1_EVIDENCE_POLICY = (
    "single_sol_editorial_no_reply_v1"
)
AUTHOR_EVALUATION_QUARANTINE_SEEDED_EVIDENCE_POLICY = (
    "majority_spam_or_abuse_seeded_corroboration_v2"
)
AUTHOR_EVALUATION_QUARANTINE_LEGACY_EVIDENCE_POLICY = (
    "majority_spam_or_abuse_v1"
)
PUBLISHED_REPLY_WARNING_LIMIT = 100
MIN_CONFIRMED_PUBLICATION_EPOCH = 1_500_000_000
MAX_CONFIRMED_PUBLICATION_EPOCH = 4_102_444_800
SOURCE_REFERENCE_LIMIT = 8
LONDON = ZoneInfo("Europe/London")
PROVENANCE_EVENT_KINDS = frozenset(
    {
        "author_evaluation_quarantine_expired",
        "author_evaluation_quarantine_skip",
        "author_evaluation_quarantine_started",
        "daily_meme_posted",
        "historical_context_reply",
        "hot_post_reply_posted",
        "mention_backlog_completed",
        "mention_backlog_progress",
        "mention_backlog_reset",
        "mention_backlog_started",
        "mention_reply_posted",
        "quote_image_posted",
        "quote_tweet_reply_posted",
        "remote_write_succeeded",
    }
)
RESUME_FINGERPRINT_TAIL_LIMIT = 128
USD_TICKS_PER_DOLLAR = 10_000_000_000
USD_DISPLAY_QUANTUM = Decimal("0.00000001")
OPENAI_COST_CACHE_PATH = (
    Path.home() / ".local/state/mrsMThatcher/openai-costs/daily_costs.json"
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
REMOTE_WRITE_SOURCE_RECEIPT_ROLES = {
    "regular_post_receipt.json": "regular_quote_image_main_post",
    "meme_post_receipt.json": "daily_meme_main_post",
    "confirmed_reply_receipt.json": "conversational_confirmed_reply",
    "historical_context_reply_receipt.json": "historical_context_reply",
}
REMOTE_MEDIA_RECEIPT_BASENAME = "remote_media_upload_receipt.json"
REMOTE_MEDIA_FENCE_BASENAME = "remote_media_upload_receipt.json.fence.json"
REMOTE_TRANSPORT_JOURNAL_BASENAME = "remote_write_transport_journal.json"
REMOTE_TRANSPORT_FENCE_BASENAME = "remote_write_transport_fence.json"
REMOTE_WRITE_ARCHIVE_BASENAME = "remote_write_safety_marker_archive"
REMOTE_WRITE_SNAPSHOT_MAX_BYTES = 256 * 1024
RETIREMENT_SOURCE_IDENTITY_KEYS = frozenset(
    ("ctime_ns", "device", "inode", "link_count", "mode", "mtime_ns", "owner_uid", "size")
)
REMOTE_OPERATION_SCOPE_LABELS = {
    "all_remote_writes": "all remote writes",
    "all_replies": "all replies",
    "normal_replies": "normal replies (mention and hot-post)",
    "hot_post_replies": "hot-post replies",
    "quote_replies": "quote-tweet replies",
    "quote_image_posts": "regular quote/image posts",
    "daily_meme_posts": "daily-meme posts",
    "historical_context_replies": "historical-context replies",
    "unknown": "unknown",
}
REMOTE_CONTROL_SCOPE_BY_KEY = {
    "disable_all": "all_remote_writes",
    "pause_all": "all_remote_writes",
    "disable_replies": "all_replies",
    "pause_replies": "all_replies",
    "disable_normal_replies": "normal_replies",
    "pause_normal_replies": "normal_replies",
    "disable_hot_post_replies": "hot_post_replies",
    "pause_hot_post_replies": "hot_post_replies",
    "disable_quote_replies": "quote_replies",
    "pause_quote_replies": "quote_replies",
    "disable_quote_posts": "quote_image_posts",
    "pause_quote_posts": "quote_image_posts",
    "disable_meme_posts": "daily_meme_posts",
    "pause_meme_posts": "daily_meme_posts",
}
REMOTE_LANE_SCOPE = {
    "mention": "normal_replies",
    "mention_reply": "normal_replies",
    "normal": "normal_replies",
    "normal_reply": "normal_replies",
    "normal_replies": "normal_replies",
    "hot_post": "hot_post_replies",
    "hot_post_reply": "hot_post_replies",
    "quote_tweet": "quote_replies",
    "quote_tweet_reply": "quote_replies",
    "quote_reply": "quote_replies",
    "conversational_reply": "all_replies",
    "replies": "all_replies",
    "quote_image": "quote_image_posts",
    "regular_post": "quote_image_posts",
    "daily_meme": "daily_meme_posts",
    "meme": "daily_meme_posts",
    "historical_context": "historical_context_replies",
    "historical_context_reply": "historical_context_replies",
}
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
SAFE_SOURCE_LOGGER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,127}\Z")


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
        int(metadata.st_nlink),
        int(metadata.st_uid),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
        int(metadata.st_ctime_ns),
    )


def read_stable_regular_snapshot(
    path: Path,
    *,
    maximum: int,
    require_private: bool = False,
) -> Tuple[bytes, os.stat_result]:
    """Read bytes and metadata from one stable no-follow file observation."""

    path = Path(path)
    before_path = os.lstat(path)
    if not stat.S_ISREG(before_path.st_mode):
        raise ValueError(f"not a regular file: {path.name}")
    if require_private and (
        before_path.st_nlink != 1
        or before_path.st_uid != os.geteuid()
        or stat.S_IMODE(before_path.st_mode) != 0o600
    ):
        raise ValueError(f"unsafe private file metadata: {path.name}")
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
    return data, after_fd


def read_stable_regular_bytes(path: Path, *, maximum: int) -> bytes:
    """Read one bounded regular file twice-bound to its no-follow pathname."""

    data, _metadata = read_stable_regular_snapshot(path, maximum=maximum)
    return data


def read_stable_private_json_bytes(path: Path, *, maximum: int) -> bytes:
    """Read one stable, owned, single-link mode-0600 JSON authority."""

    data, metadata = read_stable_regular_snapshot(
        path,
        maximum=maximum,
        require_private=True,
    )
    if (
        metadata.st_nlink != 1
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise ValueError(f"unsafe private file metadata: {Path(path).name}")
    return data


def canonical_atomic_json_bytes(value: Any) -> bytes:
    """Return the canonical encoding used by conversational receipts."""

    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def canonical_private_json_bytes(value: Any) -> bytes:
    """Return the canonical encoding used by historical reply history."""

    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


SAFE_RECONCILIATION_INSPECTION_ERRORS = frozenset(
    {
        "archive path is not a safe project-relative path",
        "archive path is not a direct reconciliation-archive child",
        "archive is not a mode-0400 regular file",
        "marker audit/archive binding is invalid",
        "definite-non-success audit identity is invalid",
    }
)


def reconciliation_inspection_error(exc: BaseException) -> str:
    """Retain fixed legacy diagnostics while redacting data-derived failures."""

    message = str(exc)
    if type(exc) is ValueError and message in SAFE_RECONCILIATION_INSPECTION_ERRORS:
        return f"ValueError: {message}"
    return bounded_exception_status("inspection failed", exc)


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


def _strict_native_json_value(data: bytes, *, label: str) -> Any:
    """Parse duplicate-free finite UTF-8 JSON with ordinary float types."""

    def reject_constant(value: str) -> None:
        raise ValueError(f"{label} contains non-finite number {value}")

    def parse_finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError(f"{label} contains non-finite number {value}")
        return parsed

    def pairs(items: List[Tuple[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"{label} contains duplicate key {key!r}")
            result[key] = value
        return result

    def require_utf8_strings(value: Any) -> None:
        if isinstance(value, str):
            try:
                value.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ValueError(
                    f"{label} contains a non-UTF-8 string"
                ) from exc
        elif isinstance(value, list):
            for item in value:
                require_utf8_strings(item)
        elif isinstance(value, dict):
            for key, item in value.items():
                require_utf8_strings(key)
                require_utf8_strings(item)

    value = json.loads(
        data.decode("utf-8"),
        object_pairs_hook=pairs,
        parse_float=parse_finite_float,
        parse_constant=reject_constant,
    )
    require_utf8_strings(value)
    return value


def _strict_native_json_object(data: bytes, *, label: str) -> Dict[str, Any]:
    """Parse one strict native JSON object."""

    value = _strict_native_json_value(data, label=label)
    if not isinstance(value, dict):
        raise ValueError(f"{label} root is not an object")
    return value


def repository_head_sha(source_path: Path) -> Optional[str]:
    """Return the enclosing Git worktree HEAD, not source-byte identity."""

    try:
        completed = subprocess.run(
            ["git", "-C", str(source_path.parent), "rev-parse", "--verify", "HEAD"],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    candidate = completed.stdout.strip().lower()
    if completed.returncode == 0 and re.fullmatch(r"[0-9a-f]{40,64}", candidate):
        return candidate
    return None


def build_digest_contract(source_path: Optional[Path] = None) -> Dict[str, Any]:
    """Build the explicit versioned identity for this JSON producer."""

    resolved_source = Path(source_path or __file__).resolve()
    source_hash: Optional[str] = None
    source_status = "unavailable"
    try:
        source_bytes = read_stable_regular_bytes(
            resolved_source,
            maximum=DIGEST_SOURCE_MAX_BYTES,
        )
        source_hash = hashlib.sha256(source_bytes).hexdigest()
        source_status = "verified"
    except Exception as exc:
        source_status = (
            f"unavailable: {type(exc).__name__}: {str(exc)[:160]}"
        )
    return {
        "schema_version": DIGEST_JSON_SCHEMA_VERSION,
        "output_kind": DIGEST_JSON_OUTPUT_KIND,
        "producer": "mrs_log_digest.py",
        "compatibility_policy": "major-versioned",
        "producer_source_sha256": source_hash,
        "producer_source_status": source_status,
        "repository_head_sha": repository_head_sha(resolved_source),
        "projection_semantics": {
            "latest_state": "selected projection, not full bot_state.json",
            "latest_config": "selected projection, not full local configuration",
            "historical_retained_state": "selected historical projection",
            "historical_retained_config": "selected historical projection",
        },
        "source_reference_semantics": {
            "input_file_index": "zero-based index into the root input_files list",
            "record_number": "one-based timestamped record number within that physical file",
            "raw_log_line_included": False,
            "maximum_correlated_source_refs": SOURCE_REFERENCE_LIMIT,
        },
    }


def _canonical_retirement_source_identity(value: Any) -> str:
    """Return the stable structural representation of one exact source identity."""

    if (
        not isinstance(value, dict)
        or frozenset(value) != RETIREMENT_SOURCE_IDENTITY_KEYS
        or any(type(value[key]) is not int for key in RETIREMENT_SOURCE_IDENTITY_KEYS)
    ):
        raise ValueError("retirement source identity is not a strict exact-file identity")
    return json.dumps(
        value, allow_nan=False, sort_keys=True, separators=(",", ":")
    )


def runtime_control_snapshot(project_dir: Path) -> Dict[str, Any]:
    """Read current pause controls, sampling the digest clock inside validation."""
    return _runtime_control_snapshot(
        project_dir,
        read_bytes=read_stable_regular_bytes,
        parse_json_object=_strict_json_object,
        now=datetime.now,
    )


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
                marker_data = _read_readonly_archive_bytes(
                    project_dir,
                    value.get("archive_path"),
                )
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
                    or hashlib.sha256(marker_data).hexdigest() != expected_hash
                ):
                    raise ValueError("marker audit/archive binding is invalid")
                marker_identity: Dict[str, Any] = {}
                try:
                    marker = _strict_json_object(
                        marker_data,
                        label=str(value.get("archive_path") or "archived marker"),
                    )
                    marker_identity = {
                        "target_id": str(
                            marker.get("reply_to_id")
                            or marker.get("target_id")
                            or ""
                        ),
                        "transaction_id": str(
                            marker.get("transaction_id")
                            or marker.get("attempt_id")
                            or ""
                        ),
                    }
                except Exception as exc:
                    marker_identity["identity_error"] = (
                        reconciliation_inspection_error(exc)
                    )
                try:
                    resolution = _strict_json_object(
                        _read_readonly_archive_bytes(
                            project_dir,
                            reconciliation_reference,
                        ),
                        label=reconciliation_reference,
                    )
                    resolution_transaction_id = resolution.get("transaction_id")
                    resolution_target_id = resolution.get("target_id")
                    if (
                        resolution.get("schema_version") != 1
                        or resolution.get("operation")
                        != "offline_verified_definite_non_success_reply_archive"
                        or resolution.get("remote_disposition")
                        != "definite_non_success"
                        or resolution.get("audit_receipt_path")
                        != reconciliation_reference
                        or resolution.get("marker_sha256") != expected_hash
                        or not isinstance(resolution_transaction_id, str)
                        or re.fullmatch(
                            r"[0-9a-f]{64}", resolution_transaction_id
                        )
                        is None
                        or not isinstance(resolution_target_id, str)
                        or re.fullmatch(r"\d+", resolution_target_id) is None
                        or resolution.get(
                            "active_marker_preserved_after_transaction_reconciliation"
                        )
                        is not True
                        or resolution.get(
                            "successful_return_requires_active_transaction_pair_absent"
                        )
                        is not True
                        or (
                            marker_identity.get("target_id")
                            and marker_identity["target_id"]
                            != resolution_target_id
                        )
                        or (
                            marker_identity.get("transaction_id")
                            and marker_identity["transaction_id"]
                            != resolution_transaction_id
                        )
                    ):
                        raise ValueError(
                            "definite-non-success audit identity is invalid"
                        )
                    marker_identity.update(
                        {
                            "target_id": resolution_target_id,
                            "transaction_id": resolution_transaction_id,
                        }
                    )
                except Exception as exc:
                    marker_identity["reference_identity_error"] = (
                        reconciliation_inspection_error(exc)
                    )
                valid_marker.append(
                    {
                        "audit_path": str(path.relative_to(project_dir)),
                        "audit_sha256": hashlib.sha256(data).hexdigest(),
                        "archived_at_epoch": archived_at_epoch,
                        "marker_sha256": expected_hash,
                        "reconciliation_reference": reconciliation_reference,
                        **marker_identity,
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


def _remote_write_document_identity(
    document: Dict[str, Any],
) -> Dict[str, Any]:
    """Extract non-secret transaction identity from one active barrier document."""

    reply_context = document.get("reply_context")
    if not isinstance(reply_context, dict):
        reply_context = {}
    remote_payload = document.get("remote_payload")
    if not isinstance(remote_payload, dict):
        remote_payload = {}
    remote_reply = remote_payload.get("reply")
    if not isinstance(remote_reply, dict):
        remote_reply = {}
    source_receipt = document.get("source_receipt")
    if not isinstance(source_receipt, dict):
        source_receipt = {}

    def text_value(*values: Any) -> str:
        for value in values:
            if isinstance(value, (str, int)) and not isinstance(value, bool):
                rendered = str(value).strip()
                if rendered:
                    return rendered
        return ""

    epochs = [
        value
        for value in (
            document.get("recorded_at_epoch"),
            document.get("attempt_epoch"),
            document.get("reply_epoch"),
            document.get("created_at_epoch"),
        )
        if type(value) is int and value >= 0
    ]
    return {
        "transaction_id": text_value(
            document.get("transaction_id"),
            document.get("attempt_id"),
            document.get("media_transaction_id"),
        ),
        "lane": text_value(
            document.get("lane"),
            document.get("candidate_source"),
            reply_context.get("lane"),
        ),
        "target_id": text_value(
            document.get("target_id"),
            document.get("reply_to_id"),
            remote_reply.get("in_reply_to_tweet_id"),
            reply_context.get("target_id"),
        ),
        "source_receipt_name": text_value(source_receipt.get("basename")),
        "source_receipt_sha256": text_value(source_receipt.get("sha256")),
        "recorded_at_epoch": min(epochs) if epochs else None,
    }


def _group_active_remote_write_artifacts(
    artifacts: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Group active files only when their payload identities explicitly connect."""

    if not artifacts:
        return []
    artifacts = [dict(item) for item in artifacts]
    for item in artifacts:
        source_identity = item.get("retirement_source_identity")
        if source_identity is None:
            continue
        try:
            canonical = _canonical_retirement_source_identity(source_identity)
        except ValueError as exc:
            error = f"ValueError: {exc}"
            if item.get("identity_error"):
                error = str(item["identity_error"]) + "; " + error
            item["identity_error"] = error
            item.pop("retirement_source_identity", None)
            continue
        item["retirement_source_identity_canonical"] = canonical
        item["retirement_source_identity_sha256"] = hashlib.sha256(
            canonical.encode("utf-8")
        ).hexdigest()
    parents = list(range(len(artifacts)))

    def root(index: int) -> int:
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    def union(left: int, right: int) -> None:
        left_root = root(left)
        right_root = root(right)
        if left_root != right_root:
            parents[right_root] = left_root

    def component_values(index: int, *fields: str) -> set[str]:
        component = root(index)
        return {
            str(item.get(field) or "").strip()
            for item_index, item in enumerate(artifacts)
            if root(item_index) == component
            for field in fields
            if str(item.get(field) or "").strip()
        }

    def compatible(left: int, right: int) -> bool:
        def combined(*fields: str) -> set[str]:
            return component_values(left, *fields) | component_values(
                right, *fields
            )

        expected_or_bound_receipt = combined(
            "retirement_expected_sha256",
            "source_receipt_sha256",
        )
        return bool(
            len(combined("transaction_id")) <= 1
            and len(combined("target_id")) <= 1
            and len(combined("receipt_role")) <= 1
            and len(combined("retirement_source_basename")) <= 1
            and len(combined("retirement_expected_sha256")) <= 1
            and len(combined("retirement_expected_size")) <= 1
            and len(combined("retirement_source_identity_canonical")) <= 1
            and len(combined("source_receipt_name")) <= 1
            and len(combined("source_receipt_sha256")) <= 1
            and len(expected_or_bound_receipt) <= 1
        )

    def union_matching(fields: Tuple[str, ...]) -> None:
        for left in range(len(artifacts)):
            for right in range(left):
                if (
                    component_values(left, *fields)
                    & component_values(right, *fields)
                    and compatible(left, right)
                ):
                    union(left, right)

    # Transaction IDs are authoritative.  Exact document/source-receipt
    # hashes are the next strongest binding; filenames are deliberately not
    # identity because every transaction reuses the same small namespace.
    union_matching(("transaction_id",))
    union_matching(
        (
            "document_sha256",
            "artifact_sha256",
            "source_receipt_sha256",
            "retirement_expected_sha256",
        )
    )

    # Lane plus target is the final fallback.  A marker may omit its lane, so
    # permit that member only where the target has one compatible lane family
    # and no competing transaction ID.
    roots_by_target: Dict[str, set[int]] = {}
    for index, artifact in enumerate(artifacts):
        target_id = str(artifact.get("target_id") or "").strip()
        if target_id:
            roots_by_target.setdefault(target_id, set()).add(root(index))
    for target_roots in roots_by_target.values():
        transaction_ids = set().union(
            *(component_values(index, "transaction_id") for index in target_roots)
        )
        lanes = set().union(
            *(component_values(index, "lane") for index in target_roots)
        )
        specific_lanes = lanes - {"conversational_reply"}
        if len(transaction_ids) > 1 or len(specific_lanes) > 1 or not lanes:
            continue
        ordered_roots = sorted(target_roots)
        first_root = ordered_roots[0]
        for component_root in ordered_roots[1:]:
            if compatible(first_root, component_root):
                union(first_root, component_root)

    # Snapshot-only fallback: one malformed canonical auxiliary may join the
    # sole non-contradictory active generation for its exact source basename.
    roots_by_retirement_source: Dict[str, set[int]] = {}
    for index in range(len(artifacts)):
        if "receipt_retirement_auxiliary" not in component_values(index, "kind"):
            continue
        sources = component_values(index, "retirement_source_basename")
        if len(sources) == 1:
            roots_by_retirement_source.setdefault(next(iter(sources)), set()).add(
                root(index)
            )

    def values_for_roots(component_roots: set[int], *fields: str) -> set[str]:
        return {
            str(item.get(field) or "").strip()
            for item_index, item in enumerate(artifacts)
            if root(item_index) in component_roots
            for field in fields
            if str(item.get(field) or "").strip()
        }

    for component_roots in roots_by_retirement_source.values():
        if len(component_roots) <= 1:
            continue
        conflict_fields = (
            ("retirement_expected_sha256", "retirement_expected_sha256"),
            ("retirement_expected_size", "retirement_expected_size"),
            ("retirement_source_identity_canonical", "retirement_source_identity"),
            ("receipt_role", "receipt_role"),
            ("transaction_id", "transaction_id"),
            ("target_id", "target_id"),
            ("lane", "lane"),
            ("source_receipt_name", "source_receipt_binding"),
            ("source_receipt_sha256", "source_receipt_binding"),
        )
        conflict_reasons = {
            reason
            for field, reason in conflict_fields
            if len(values_for_roots(component_roots, field)) > 1
        }
        bound_receipt_hashes = values_for_roots(
            component_roots,
            "source_receipt_sha256",
        )
        if bound_receipt_hashes and len(
            bound_receipt_hashes
            | values_for_roots(
                component_roots,
                "retirement_expected_sha256",
            )
        ) > 1:
            conflict_reasons.add("source_receipt_binding")
        conflict_reasons = sorted(conflict_reasons)
        if conflict_reasons:
            for index, item in enumerate(artifacts):
                if root(index) in component_roots:
                    item["retirement_snapshot_conflict"] = True
                    item["retirement_conflict_reasons"] = conflict_reasons
            continue
        first_root, *other_roots = sorted(component_roots)
        for component_root in other_roots:
            union(first_root, component_root)

    components: Dict[int, List[Dict[str, Any]]] = {}
    for index, artifact in enumerate(artifacts):
        components.setdefault(root(index), []).append(artifact)

    grouped: List[Dict[str, Any]] = []
    for rows in components.values():
        def values(field: str) -> List[str]:
            return sorted({str(item[field]) for item in rows if item.get(field)})

        transaction_ids = values("transaction_id")
        target_ids = values("target_id")
        lanes = values("lane")
        recorded_epochs = [
            item.get("recorded_at_epoch")
            for item in rows
            if type(item.get("recorded_at_epoch")) is int
        ]
        receipt_roles = values("receipt_role")
        document_sha256s = values("document_sha256")
        artifact_sha256s = values("artifact_sha256")
        source_receipt_sha256s = values("source_receipt_sha256")
        retirement_expected_sha256s = values("retirement_expected_sha256")
        retirement_source_basenames = values("retirement_source_basename")
        retirement_phases = sorted(
            set(values("retirement_phase"))
            | set(values("retirement_document_phase"))
        )
        retirement_source_identity_canonicals = values(
            "retirement_source_identity_canonical"
        )
        retirement_source_identity_sha256s = values(
            "retirement_source_identity_sha256"
        )
        snapshot_identity_tokens = [
            *("document_sha256:" + value for value in document_sha256s),
            *("artifact_sha256:" + value for value in artifact_sha256s),
            *(
                "retirement_expected_sha256:"
                + source
                + ":"
                + expected
                for source in retirement_source_basenames
                for expected in retirement_expected_sha256s
            ),
            *(
                "retirement_source_identity:"
                + source
                + ":"
                + identity_sha256
                for source in retirement_source_basenames
                for identity_sha256 in retirement_source_identity_sha256s
            ),
            *(
                "source_receipt_sha256:" + value
                for value in source_receipt_sha256s
            ),
        ]
        invalid_artifact_names = sorted(
            {
                str(item.get("name"))
                for item in rows
                if item.get("safe_regular") is False
                or item.get("identity_error")
                or item.get("artifact_error")
            }
        )
        inspection_error_identities = sorted(
            {
                str(item.get("name") or "unavailable")
                + ":"
                + str(error).split(":", 1)[0]
                + ":"
                + hashlib.sha256(
                    str(error).encode("utf-8", errors="replace")
                ).hexdigest()
                for item in rows
                for error in (
                    item.get("identity_error"),
                    item.get("artifact_error"),
                    item.get("reason")
                    if item.get("safe_regular") is False
                    else None,
                )
                if error
            }
        )
        grouped.append(
            {
                "transaction_ids": transaction_ids,
                "target_ids": target_ids,
                "lanes": lanes,
                "artifact_names": values("name"),
                "artifact_kinds": values("kind"),
                "receipt_roles": receipt_roles,
                "receipt_role_labels": [
                    REMOTE_WRITE_RECEIPT_ROLE_LABELS.get(role, role)
                    for role in receipt_roles
                ],
                "transaction_states": values("transaction_state"),
                "invalid_artifact_names": invalid_artifact_names,
                "inspection_error_identities": inspection_error_identities,
                "document_sha256s": document_sha256s,
                "artifact_sha256s": artifact_sha256s,
                "source_receipt_sha256s": source_receipt_sha256s,
                "retirement_source_basenames": retirement_source_basenames,
                "retirement_expected_sha256s": retirement_expected_sha256s,
                "retirement_expected_sizes": sorted(
                    {
                        int(item["retirement_expected_size"])
                        for item in rows
                        if type(item.get("retirement_expected_size")) is int
                    }
                ),
                "retirement_phases": retirement_phases,
                "retirement_source_identities": [
                    json.loads(canonical)
                    for canonical in retirement_source_identity_canonicals
                ],
                "retirement_source_identity_sha256s": (
                    retirement_source_identity_sha256s
                ),
                "retirement_auxiliary_names": sorted(
                    {
                        str(item.get("name"))
                        for item in rows
                        if item.get("kind") == "receipt_retirement_auxiliary"
                        and item.get("name")
                    }
                ),
                "retirement_snapshot_conflict": any(
                    item.get("retirement_snapshot_conflict") is True
                    for item in rows
                ),
                "retirement_conflict_reasons": sorted(
                    {
                        str(reason)
                        for item in rows
                        for reason in item.get("retirement_conflict_reasons") or []
                    }
                ),
                "snapshot_identity_tokens": snapshot_identity_tokens,
                "recorded_at_epoch": min(recorded_epochs) if recorded_epochs else None,
                "attribution_identity_available": bool(
                    transaction_ids or target_ids or lanes
                ),
                "stable_incident_identity_available": bool(
                    document_sha256s
                    or artifact_sha256s
                    or source_receipt_sha256s
                    or retirement_expected_sha256s
                    or retirement_source_identity_sha256s
                    or any(item.get("name") for item in rows)
                ),
                # Retain the established field for JSON consumers while
                # keeping attribution separate from stable incident identity.
                "identity_available": bool(transaction_ids or target_ids or lanes),
            }
        )
    return sorted(
        grouped,
        key=lambda item: (
            item.get("recorded_at_epoch") if item.get("recorded_at_epoch") is not None else -1,
            item.get("transaction_ids") or [],
            item.get("target_ids") or [],
            item.get("artifact_names") or [],
        ),
    )


def remote_write_safety_snapshot(project_dir: Path) -> Dict[str, Any]:
    """Inspect every current v2 remote-write barrier without mutating state."""

    project_dir = Path(project_dir)
    observed_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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
            "observed_at": observed_at,
            "status": "not_configured",
            "blocking": False,
            "control": control,
            "reconciliation_archive": archive,
        }

    active_entries: List[Dict[str, Any]] = []

    def observe_name(
        name: str,
        kind: str,
        *,
        receipt_role: Optional[str] = None,
        retirement_source_basename: str = "",
        retirement_path_phase: str = "",
    ) -> None:
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
                    "receipt_role": receipt_role,
                    "retirement_source_basename": retirement_source_basename,
                    "retirement_phase": retirement_path_phase,
                    "safe_regular": False,
                    "reason": f"{type(exc).__name__}: {exc}",
                }
            )
            return
        entry = {
            "name": name,
            "kind": kind,
            "receipt_role": receipt_role,
            "retirement_source_basename": retirement_source_basename,
            "retirement_phase": retirement_path_phase,
            "safe_regular": stat.S_ISREG(metadata.st_mode),
            "mode": oct(stat.S_IMODE(metadata.st_mode)),
            "size": int(metadata.st_size),
        }
        if entry["safe_regular"]:
            try:
                data = read_stable_regular_bytes(
                    path,
                    maximum=REMOTE_WRITE_SNAPSHOT_MAX_BYTES,
                )
                entry["artifact_sha256"] = hashlib.sha256(data).hexdigest()
                document = _strict_json_object(data, label=name)
                entry.update(_remote_write_document_identity(document))
                entry["document_sha256"] = entry["artifact_sha256"]
                if kind == "receipt_retirement_auxiliary":
                    payload_source = document.get("source_basename")
                    if (
                        isinstance(payload_source, str)
                        and payload_source
                        and payload_source != retirement_source_basename
                    ):
                        raise ValueError(
                            "retirement source basename does not match its "
                            "canonical auxiliary path"
                        )
                    expected_sha256 = document.get("expected_sha256")
                    if (
                        isinstance(expected_sha256, str)
                        and re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
                    ):
                        entry["retirement_expected_sha256"] = expected_sha256
                    elif retirement_path_phase == "cleanup":
                        # A cleanup entry without a marker binding is the
                        # displaced exact source receipt itself.
                        entry["retirement_expected_sha256"] = entry[
                            "document_sha256"
                        ]
                    expected_size = document.get("expected_size")
                    if type(expected_size) is int and expected_size > 0:
                        entry["retirement_expected_size"] = expected_size
                    elif retirement_path_phase == "cleanup":
                        entry["retirement_expected_size"] = len(data)
                    phase = document.get("phase")
                    if isinstance(phase, str) and phase:
                        entry["retirement_document_phase"] = phase
                    source_identity = document.get("source_identity")
                    if source_identity is not None:
                        canonical_identity = (
                            _canonical_retirement_source_identity(source_identity)
                        )
                        entry["retirement_source_identity"] = source_identity
                        entry["retirement_source_identity_canonical"] = (
                            canonical_identity
                        )
                        entry["retirement_source_identity_sha256"] = (
                            hashlib.sha256(
                                canonical_identity.encode("utf-8")
                            ).hexdigest()
                        )
            except Exception as exc:
                entry["identity_error"] = bounded_exception_status(
                    "inspection failed",
                    exc,
                )
        active_entries.append(entry)

    for name in REMOTE_WRITE_MARKER_BASENAMES:
        observe_name(name, "ambiguity_marker")
    for name in REMOTE_WRITE_SOURCE_RECEIPT_BASENAMES:
        observe_name(
            name,
            "source_receipt",
            receipt_role=REMOTE_WRITE_SOURCE_RECEIPT_ROLES[name],
        )

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
    retirement_auxiliaries: List[Dict[str, str]] = []
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
                    "receipt_role": REMOTE_WRITE_SOURCE_RECEIPT_ROLES[name],
                    "valid": inspection.valid,
                    "blocking": inspection.blocking,
                    "state": inspection.state,
                    "sequence": inspection.sequence,
                    "record_sha256": inspection.record_sha256,
                    "detail": inspection.detail,
                    "ledger_path": inspection.ledger_path,
                    "exchange_path": inspection.exchange_path,
                }
            )
            ledger_row = ledger_rows[-1]
            if inspection.state in {"exchange_staged", "exchange_committed"}:
                binding_path = Path(
                    inspection.exchange_path
                    if inspection.state == "exchange_staged"
                    else inspection.ledger_path
                )
                try:
                    binding_data = read_stable_regular_bytes(
                        binding_path, maximum=REMOTE_WRITE_SNAPSHOT_MAX_BYTES
                    )
                    binding_document = _strict_json_object(
                        binding_data, label=binding_path.name
                    )
                    binding = binding_document.get("source_binding")
                    if not isinstance(binding, dict):
                        raise ValueError("recoverable exchange has no source binding")
                    expected_sha256 = binding.get("expected_sha256")
                    expected_size = binding.get("expected_size")
                    source_identity = binding.get("source_identity")
                    if (
                        binding_document.get("source_basename") != name
                        or binding_document.get("state") != "completed"
                        or not isinstance(expected_sha256, str)
                        or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
                        or type(expected_size) is not int
                        or expected_size <= 0
                    ):
                        raise ValueError("recoverable exchange binding is invalid")
                    canonical_identity = _canonical_retirement_source_identity(
                        source_identity
                    )
                    ledger_row.update(
                        {
                            "retirement_expected_sha256": expected_sha256,
                            "retirement_expected_size": expected_size,
                            "retirement_source_identity": source_identity,
                            "retirement_source_identity_sha256": hashlib.sha256(
                                canonical_identity.encode("utf-8")
                            ).hexdigest(),
                        }
                    )
                except Exception as exc:
                    ledger_row["binding_inspection_error"] = f"{type(exc).__name__}: {exc}"
            auxiliary_phases = (
                "prepared",
                "committed",
                "cleanup",
                "prepared",
                "committed",
            )
            for path, phase in zip(
                retirement_auxiliary_paths(source),
                auxiliary_phases,
            ):
                try:
                    os.lstat(path)
                except FileNotFoundError:
                    continue
                retirement_auxiliaries.append(
                    {
                        "name": path.name,
                        "source_basename": name,
                        "receipt_role": REMOTE_WRITE_SOURCE_RECEIPT_ROLES[name],
                        "phase": phase,
                    }
                )
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
    for auxiliary in retirement_auxiliaries:
        observe_name(
            auxiliary["name"],
            "receipt_retirement_auxiliary",
            receipt_role=auxiliary["receipt_role"],
            retirement_source_basename=auxiliary["source_basename"],
            retirement_path_phase=auxiliary["phase"],
        )

    transport_artifact: Optional[Dict[str, Any]] = None
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
        transport_snapshot_name = REMOTE_TRANSPORT_JOURNAL_BASENAME
        transport_snapshot = transport_state.journal
        if transport_snapshot is None and transport_state.fence is not None:
            transport_snapshot_name = "remote_write_transport_fence.json"
            transport_snapshot = transport_state.fence
        if transport_snapshot is not None:
            transport_identity = _remote_write_document_identity(
                transport_snapshot.document
            )
            transport.update(transport_identity)
            transport["document_sha256"] = transport_snapshot.sha256
            transport_artifact = {
                "name": transport_snapshot_name,
                "kind": "transport_journal",
                "transaction_state": transport_state.classification,
                **transport_identity,
                "document_sha256": transport["document_sha256"],
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

    def error_identity(value: Any) -> str:
        detail = str(value or "unavailable")
        return (detail.split(":", 1)[0].strip() or "unavailable") + ":" + hashlib.sha256(
            detail.encode("utf-8", errors="replace")
        ).hexdigest()

    snapshot_incident_evidence: List[Dict[str, Any]] = []

    def add_blocker(
        category: str,
        kind: str,
        identity: str,
        summary: str,
        **detail: Any,
    ) -> None:
        snapshot_incident_evidence.append(
            {
                "category": category,
                "blocker_kind": kind,
                "signature": f"snapshot:{kind.replace('_', '-')}:{identity}",
                "summary": summary,
                "snapshot_identity_tokens": [f"{kind}:{identity}"],
                **detail,
            }
        )

    if protocol.get("valid") is not True:
        protocol_identity = str(
            protocol.get("sha256")
            or protocol.get("audit_sha256")
            or error_identity(protocol.get("reason"))
        )
        add_blocker(
            "remote_write_protocol_barrier",
            "protocol_activation",
            str(protocol.get("status") or "invalid") + ":" + protocol_identity,
            "Remote-write protocol activation is invalid or missing: "
            + str(protocol.get("reason") or protocol.get("status") or "unavailable"),
            artifact_names=[
                ".mrs_remote_write_safety_protocol_v2",
                ".mrs_remote_write_safety_protocol_v2.activation_audit.json",
            ],
        )
    for ledger in ledger_rows:
        if ledger.get("valid") is True and ledger.get("blocking") is not True:
            continue
        source_basename = str(ledger.get("source") or "inspection")
        receipt_role = REMOTE_WRITE_SOURCE_RECEIPT_ROLES.get(source_basename, "")
        ledger_identity = str(
            ledger.get("record_sha256")
            or error_identity(ledger.get("detail"))
        )
        expected_sha256 = str(
            ledger.get("retirement_expected_sha256") or ""
        )
        source_identity_sha256 = str(
            ledger.get("retirement_source_identity_sha256") or ""
        )
        add_blocker(
            "remote_write_transaction_barrier",
            "retirement_ledger",
            source_basename + ":" + ledger_identity,
            "Permanent receipt-retirement ledger blocks remote writes: "
            f"{source_basename}, state {ledger.get('state') or 'unavailable'}, "
            f"sequence {ledger.get('sequence', 'unavailable')}, detail "
            + str(ledger.get("detail") or "unavailable"),
            retirement_source_basenames=[source_basename],
            retirement_expected_sha256s=[expected_sha256]
            if expected_sha256
            else [],
            retirement_expected_sizes=[ledger["retirement_expected_size"]]
            if type(ledger.get("retirement_expected_size")) is int
            else [],
            retirement_source_identities=[ledger["retirement_source_identity"]]
            if isinstance(ledger.get("retirement_source_identity"), dict)
            else [],
            retirement_source_identity_sha256s=[source_identity_sha256]
            if source_identity_sha256
            else [],
            receipt_roles=[receipt_role] if receipt_role else [],
            receipt_role_labels=[REMOTE_WRITE_RECEIPT_ROLE_LABELS[receipt_role]]
            if receipt_role
            else [],
            ledger_state=ledger.get("state"),
            ledger_sequence=ledger.get("sequence"),
            ledger_detail=ledger.get("detail"),
            ledger_record_sha256=ledger.get("record_sha256"),
            artifact_names=[
                Path(str(path)).name
                for path in (ledger.get("ledger_path"), ledger.get("exchange_path"))
                if path
            ],
        )
    if transport.get("classification") in {
        "inspection_unavailable",
        "directory_unavailable",
        "invalid",
    }:
        transport_errors = ",".join(transport.get("errors") or [])
        transport_identity = str(
            transport.get("document_sha256")
            or error_identity(transport_errors)
        )
        add_blocker(
            "remote_write_transaction_barrier",
            "transport_inspection",
            str(transport.get("classification") or "invalid") + ":" + transport_identity,
            "Remote-write transport-journal inspection failed: "
            + str(transport.get("classification") or "unavailable")
            + ("; " + transport_errors if transport_errors else ""),
            transaction_id=str(transport.get("transaction_id") or ""),
            lane=str(transport.get("lane") or ""),
            document_sha256s=[transport["document_sha256"]]
            if transport.get("document_sha256")
            else [],
            artifact_names=[REMOTE_TRANSPORT_JOURNAL_BASENAME, REMOTE_TRANSPORT_FENCE_BASENAME],
        )
    if media.get("classification") == "invalid" or media.get("reason"):
        media_identity = error_identity(media.get("reason"))
        add_blocker(
            "remote_write_transaction_barrier",
            "media_inspection",
            media_identity,
            "Remote-media receipt inspection failed: "
            + str(media.get("reason") or media.get("classification") or "unavailable"),
            transaction_id=str(media.get("transaction_id") or ""),
            lane=str(media.get("lane") or ""),
            artifact_names=[REMOTE_MEDIA_RECEIPT_BASENAME, REMOTE_MEDIA_FENCE_BASENAME],
        )
    identity_artifacts = [dict(item) for item in active_entries]
    if transport_artifact is not None and transport.get("blocking") is True:
        identity_artifacts.append(transport_artifact)
    if media.get("blocking") is True:
        identity_artifacts.append(
            {
                "name": REMOTE_MEDIA_RECEIPT_BASENAME,
                "kind": "media_receipt",
                "transaction_id": str(media.get("transaction_id") or ""),
                "lane": str(media.get("lane") or ""),
                "target_id": "",
            }
        )
    active_transaction_identities = _group_active_remote_write_artifacts(
        identity_artifacts
    )
    return {
        "configured": True,
        "available": True,
        "observed_at": observed_at,
        "status": status,
        "blocking": blocking,
        "ready_for_remote_writes": not blocking and not global_pause and not control_invalid,
        "active_entries": active_entries,
        "active_marker_names": active_marker_names,
        "active_transaction_identities": active_transaction_identities,
        "snapshot_incident_evidence": snapshot_incident_evidence,
        "identity_snapshot_available": True,
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


def annotate_remote_write_snapshot_window(
    safety: Dict[str, Any],
    selected_window_end: Optional[datetime],
    *,
    current_snapshot_authoritative: bool = False,
) -> None:
    """Describe, without backdating, how current artefacts relate to a log window."""

    safety["selected_window_end"] = (
        selected_window_end.strftime("%Y-%m-%d %H:%M:%S")
        if selected_window_end is not None
        else None
    )
    try:
        snapshot_observed_at = datetime.strptime(
            str(safety.get("observed_at") or ""),
            "%Y-%m-%d %H:%M:%S",
        )
    except ValueError:
        snapshot_observed_at = None

    if selected_window_end is None or snapshot_observed_at is None:
        snapshot_relationship = "unavailable"
    elif snapshot_observed_at > selected_window_end:
        snapshot_relationship = "snapshot_postdates_selected_window"
    else:
        snapshot_relationship = "snapshot_observed_within_selected_window"
    safety["selected_window_relationship"] = snapshot_relationship
    safety["current_health_snapshot_authoritative"] = bool(
        current_snapshot_authoritative
    )

    def annotate(item: Dict[str, Any]) -> None:
        recorded_epoch = item.get("recorded_at_epoch")
        recorded_at = (
            datetime.fromtimestamp(recorded_epoch)
            if type(recorded_epoch) is int
            else None
        )
        if selected_window_end is None:
            relationship = "unavailable"
            reason = "selected report-window end is unavailable"
        elif recorded_at is not None:
            if recorded_at <= selected_window_end:
                relationship = "recorded_at_or_before_selected_window_end"
                reason = "reliable transaction time falls inside the selected window"
            else:
                relationship = "recorded_after_selected_window_end"
                reason = "reliable transaction time post-dates the selected window"
        elif (
            snapshot_observed_at is not None
            and snapshot_observed_at <= selected_window_end
        ):
            relationship = "snapshot_observed_at_or_before_selected_window_end"
            reason = "the read-only snapshot itself was observed by the selected cut-off"
        else:
            relationship = "unavailable"
            reason = (
                "current artefact has no reliable transaction time and the "
                "filesystem snapshot post-dates the selected window"
            )
        item["selected_window_relationship"] = relationship
        item["selected_window_relationship_reason"] = reason
        item["current_health_relationship"] = (
            "authoritative_current_snapshot"
            if current_snapshot_authoritative
            else relationship
        )

    for entry in safety.get("active_entries") or []:
        if isinstance(entry, dict):
            annotate(entry)
    for component in safety.get("active_transaction_identities") or []:
        if isinstance(component, dict):
            annotate(component)
    for blocker in safety.get("snapshot_incident_evidence") or []:
        if isinstance(blocker, dict):
            annotate(blocker)


def historical_context_corpus_snapshot(project_dir: Path) -> Dict[str, Any]:
    """Read current corpus, unresolved, eligibility, gate, and audit counts."""
    return _historical_context_corpus_snapshot(
        project_dir,
        parse_json_object=_strict_native_json_object,
        sha256_file=file_sha256,
    )


def generated_pool_health_snapshot(base_dir: Path, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Read current generated-pool health without mutating project files."""
    return _generated_pool_health_snapshot(
        base_dir, now,
        parse_json_object=_strict_native_json_object,
        parse_json_value=_strict_native_json_value,
        sha256_file=file_sha256,
        clock_now=datetime.now,
        fromisoformat=datetime.fromisoformat,
    )


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
        local_config = _strict_native_json_object(
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


def read_resume_data(state_file: Path) -> Dict[str, Any]:
    """Read the digest resume file.

    The timestamp is used for auto-resume. Newer versions also keep the last
    observed bot state/config so short quiet windows can still show budget and
    priority context.
    """
    if not state_file.exists():
        return {}
    try:
        return _strict_native_json_object(
            state_file.read_bytes(), label="digest resume state"
        )
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


def valid_conversational_public_reply_text(value: Any) -> bool:
    """Validate durable exact reply text, including long and multiline posts."""

    # Publication receipts and current state describe text already confirmed by
    # X, not a draft to be revalidated against today's generation policy.  The
    # evidence contract therefore preserves valid long posts and embedded
    # newlines exactly, while retaining the digest's explicit safety bound.
    return valid_bounded_utf8_text(value)


def bounded_event_nonnegative_integer_observation(
    document: Mapping[str, Any],
    key: str,
    *,
    maximum: int = MAX_REASONABLE_STATE_EPOCH,
) -> Tuple[Optional[int], str]:
    """Project an integer while retaining why required telemetry is unavailable."""

    if key not in document:
        return None, "missing"
    value = document.get(key)
    if type(value) is not int or value < 0:
        return None, "malformed"
    if value > maximum:
        return None, "out_of_range"
    return value, "available"


def bounded_event_finite_number(
    value: Any,
    *,
    absolute_maximum: float = 1_000_000_000.0,
) -> Optional[Union[int, float]]:
    """Project one bounded finite structured display number."""

    if type(value) is int and abs(value) <= absolute_maximum:
        return value
    if (
        type(value) is float
        and math.isfinite(value)
        and abs(value) <= absolute_maximum
    ):
        return value
    return None


def bounded_event_string_list(
    value: Any,
    *,
    limit: int = 100,
    item_max_characters: int = 240,
) -> List[str]:
    """Project a bounded list containing only bounded exact strings."""

    if not isinstance(value, list):
        return []
    projected: List[str] = []
    for item in value[:limit]:
        text = bounded_event_text(
            item,
            max_characters=item_max_characters,
        )
        if text is not None:
            projected.append(text)
    return projected


def safe_source_logger(value: Any) -> str:
    """Return a non-sensitive bounded logger/function identifier."""

    logger = str(value or "")
    return logger if SAFE_SOURCE_LOGGER_RE.fullmatch(logger) else "unavailable"


def record_source_ref(
    record: Record,
    input_file_indexes: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """Return bounded location metadata for one retained physical log record."""

    reference: Dict[str, Any] = {}
    index = (input_file_indexes or {}).get(record.path)
    if type(index) is int and index >= 0:
        reference["input_file_index"] = index
    else:
        reference["source_basename"] = Path(record.path).name
    reference.update(
        {
            "record_number": record.ordinal,
            "timestamp": dt_text(record.ts),
            "logger": safe_source_logger(record.src),
        }
    )
    if record.line > 0:
        reference["logged_source_line_number"] = record.line
    return reference


def bounded_source_refs(
    *collections: Any,
    limit: int = SOURCE_REFERENCE_LIMIT,
) -> Tuple[List[Dict[str, Any]], int]:
    """Merge, de-duplicate and cap source references without raw log content."""

    unique: List[Dict[str, Any]] = []
    identities: set[str] = set()
    for collection in collections:
        if isinstance(collection, dict):
            candidates = [collection]
        elif isinstance(collection, list):
            candidates = collection
        else:
            continue
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            allowed = {
                key: candidate[key]
                for key in (
                    "input_file_index",
                    "source_basename",
                    "record_number",
                    "timestamp",
                    "logger",
                    "logged_source_line_number",
                )
                if key in candidate
            }
            if not allowed or "record_number" not in allowed:
                continue
            identity = json.dumps(
                allowed,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if identity in identities:
                continue
            identities.add(identity)
            unique.append(allowed)
    omitted = max(0, len(unique) - limit)
    return unique[:limit], omitted


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


UNKNOWN_INVALID_STATE_FIELD = "unknown (invalid in latest snapshot)"
ENGAGEMENT_QUESTION_PUBLIC_TEXT_SEPARATOR = "\n\nQuestion — "
ENGAGEMENT_QUESTION_EXPERIMENT_ID = "substantive-question-v1"
ENGAGEMENT_QUESTION_EXPERIMENT_STATE_SCHEMA_VERSION = 1
ENGAGEMENT_QUESTION_EXPERIMENT_STATUSES = {
    "not_started",
    "active",
    "paused",
    "completed",
    "invalid",
}
ENGAGEMENT_PAIR_ID_RE = re.compile(r"pair-[0-9a-f]{24}\Z")
ENGAGEMENT_PUBLICATION_ORDERS = {"control_first", "treatment_first"}
ENGAGEMENT_ARMS = {"control", "treatment"}
ENGAGEMENT_MAX_CONFIRMED_PUBLICATIONS = 60


def valid_account_root_publication_identity(event: Any) -> bool:
    """Return whether an account-root event has the producer's core contract."""

    if not isinstance(event, dict):
        return False
    post_id = event.get("post_id")
    return bool(
        event.get("event") == "account_root_posted"
        and type(event.get("event_version")) is int
        and event.get("event_version") == 1
        and type(event.get("lane")) is str
        and event.get("lane") in {"quote_image", "daily_meme"}
        and valid_string_public_post_id(post_id)
        and event.get("root_post_id") == post_id
        and event.get("conversation_id") == post_id
        and event.get("publication_authority") == "confirmed_transport"
    )


def valid_engagement_confirmation_event(event: Any) -> bool:
    """Return whether a trial confirmation matches its producer schema."""

    return bool(
        isinstance(event, dict)
        and event.get("event")
        == "engagement_question_experimental_member_confirmed"
        and valid_string_public_post_id(event.get("post_id"))
        and isinstance(event.get("plan_sha256"), str)
        and SHA256_LOWER_RE.fullmatch(event["plan_sha256"]) is not None
        and isinstance(event.get("pair_id"), str)
        and ENGAGEMENT_PAIR_ID_RE.fullmatch(event["pair_id"]) is not None
        and type(event.get("member_position")) is int
        and event.get("member_position") in {1, 2}
        and type(event.get("arm")) is str
        and event.get("arm") in ENGAGEMENT_ARMS
        and type(event.get("publication_sequence")) is int
        and 1
        <= event.get("publication_sequence")
        <= ENGAGEMENT_MAX_CONFIRMED_PUBLICATIONS
    )


def engagement_main_metadata_status(event: Any) -> str:
    """Return absent, valid, or invalid for main-post experiment metadata."""

    fields = (
        "engagement_experiment_id",
        "engagement_experiment_plan_sha256",
        "engagement_experiment_pair_id",
        "engagement_experiment_arm",
        "engagement_experiment_member_position",
        "engagement_experiment_publication_order",
        "engagement_experiment_sequence",
        "engagement_question_present",
        "engagement_approved_question_sha256",
        "engagement_public_text_sha256",
    )
    if not isinstance(event, dict) or not any(field in event for field in fields):
        return "absent"
    arm = event.get("engagement_experiment_arm")
    valid = bool(
        event.get("engagement_experiment_id")
        == ENGAGEMENT_QUESTION_EXPERIMENT_ID
        and isinstance(event.get("engagement_experiment_plan_sha256"), str)
        and SHA256_LOWER_RE.fullmatch(
            event["engagement_experiment_plan_sha256"]
        )
        is not None
        and isinstance(event.get("engagement_experiment_pair_id"), str)
        and ENGAGEMENT_PAIR_ID_RE.fullmatch(
            event["engagement_experiment_pair_id"]
        )
        is not None
        and type(event.get("engagement_experiment_member_position")) is int
        and event.get("engagement_experiment_member_position") in {1, 2}
        and type(arm) is str
        and arm in ENGAGEMENT_ARMS
        and type(event.get("engagement_experiment_publication_order")) is str
        and event.get("engagement_experiment_publication_order")
        in ENGAGEMENT_PUBLICATION_ORDERS
        and type(event.get("engagement_experiment_sequence")) is int
        and 1
        <= event.get("engagement_experiment_sequence")
        <= ENGAGEMENT_MAX_CONFIRMED_PUBLICATIONS
        and type(event.get("engagement_question_present")) is bool
        and event.get("engagement_question_present")
        is (arm == "treatment")
        and isinstance(
            event.get("engagement_approved_question_sha256"), str
        )
        and SHA256_LOWER_RE.fullmatch(
            event["engagement_approved_question_sha256"]
        )
        is not None
        and isinstance(event.get("engagement_public_text_sha256"), str)
        and SHA256_LOWER_RE.fullmatch(
            event["engagement_public_text_sha256"]
        )
        is not None
    )
    return "valid" if valid else "invalid"


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


def summarize_engagement_question_experiment_state(value: Any) -> Optional[Dict[str, Any]]:
    """Return a bounded summary of protected engagement-question state."""
    if (
        not isinstance(value, dict)
        or value.get("schema_version")
        != ENGAGEMENT_QUESTION_EXPERIMENT_STATE_SCHEMA_VERSION
        or value.get("experiment_id") != ENGAGEMENT_QUESTION_EXPERIMENT_ID
    ):
        return None

    def scalar(key: str, expected_type: Any = None) -> Any:
        if key not in value:
            return UNKNOWN_MISSING_STATE_FIELD
        item = value.get(key)
        if expected_type is not None and type(item) is not expected_type:
            return UNKNOWN_INVALID_STATE_FIELD
        return item

    status = scalar("status", str)
    if (
        isinstance(status, str)
        and status not in ENGAGEMENT_QUESTION_EXPERIMENT_STATUSES
    ):
        status = UNKNOWN_INVALID_STATE_FIELD

    publications = value.get("confirmed_publications")
    if "confirmed_publications" not in value:
        confirmed_publication_count: Any = UNKNOWN_MISSING_STATE_FIELD
    elif isinstance(publications, list):
        confirmed_publication_count = len(publications)
    else:
        confirmed_publication_count = UNKNOWN_INVALID_STATE_FIELD

    if "current_deferral_reason" not in value:
        current_deferral_reason: Any = UNKNOWN_MISSING_STATE_FIELD
    else:
        raw_deferral = value.get("current_deferral_reason")
        if raw_deferral is None:
            current_deferral_reason = None
        elif isinstance(raw_deferral, dict):
            current_deferral_reason = {
                key: raw_deferral.get(key)
                for key in ("code", "pair_id", "member_position", "recorded_epoch")
                if key in raw_deferral
            }
        else:
            current_deferral_reason = UNKNOWN_INVALID_STATE_FIELD

    return {
        "experiment_id": scalar("experiment_id", str),
        "active_plan_sha256": scalar("active_plan_sha256", str),
        "status": status,
        "current_pair_index": scalar("current_pair_index", int),
        "active_pair_id": scalar("active_pair_id"),
        "next_pair_member_position": scalar("next_pair_member_position", int),
        "completed_pair_count": scalar("completed_pair_count", int),
        "confirmed_publication_count": confirmed_publication_count,
        "treatment_publication_count": scalar(
            "treatment_publication_count", int
        ),
        "last_experimental_publication_local_date": scalar(
            "last_experimental_publication_local_date"
        ),
        "current_deferral_reason": current_deferral_reason,
    }


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
        "openai_api_cooldown_until_epoch": latest_state.get("openai_api_cooldown_until_epoch"),
        "openai_api_cooldown_until_human": epoch_to_human(latest_state.get("openai_api_cooldown_until_epoch")),
        "openai_api_cooldown_reason": latest_state.get("openai_api_cooldown_reason"),
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
    experiment_summary = summarize_engagement_question_experiment_state(
        latest_state.get("engagement_question_experiment")
    )
    if experiment_summary is not None:
        summary["engagement_question_experiment"] = experiment_summary
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
            data = _strict_native_json_object(
                path.read_bytes(), label="authoritative bot state"
            )
            mtime = datetime.fromtimestamp(path.stat().st_mtime)
            return data, path, mtime
        except Exception as e:
            print(f"WARNING: could not read authoritative bot state {path}: {e}", file=sys.stderr)
    return None, None, None


def load_current_runtime_state(
    project_dir: Path,
) -> Tuple[Optional[Dict[str, Any]], Path, Optional[datetime], str]:
    """Read current state with the digest's stable reader and file-time conversion."""
    return _load_current_runtime_state(
        project_dir,
        read_snapshot=read_stable_regular_snapshot,
        parse_json_object=_strict_native_json_object,
        fromtimestamp=datetime.fromtimestamp,
        maximum=CURRENT_RUNTIME_STATE_MAX_BYTES,
    )


def load_current_runtime_config(
    project_dir: Path,
) -> Tuple[Optional[Dict[str, Any]], Path, Optional[datetime], str]:
    """Read allow-listed overrides with the digest's reader and time conversion."""
    return _load_current_runtime_config(
        project_dir,
        read_snapshot=read_stable_regular_snapshot,
        parse_json_object=_strict_native_json_object,
        fromtimestamp=datetime.fromtimestamp,
        maximum=CURRENT_RUNTIME_CONFIG_MAX_BYTES,
        report_keys=CURRENT_CONFIG_REPORT_KEYS,
    )


def epoch_to_london_text(value: int) -> Optional[str]:
    """Render a validated epoch in the digest's explicit London timezone."""

    try:
        return datetime.fromtimestamp(value, tz=LONDON).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    except (OverflowError, OSError, ValueError):
        return None


def current_author_no_reply_strike_progress(
    runtime_state: Any,
    runtime_state_status: str,
    runtime_config: Any,
    runtime_config_status: str,
    generation_time: datetime,
    *,
    state_observed_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Project current durable author strikes without mutating runtime state."""

    observation_time = state_observed_at or generation_time
    try:
        as_of_epoch = int(observation_time.timestamp())
    except (OverflowError, OSError, ValueError):
        as_of_epoch = -1
    as_of_time = epoch_to_london_text(as_of_epoch)
    limitation = (
        "Expired or cleared sub-threshold strikes cannot be reconstructed when "
        "current durable state and retained structured logs no longer contain them."
    )
    result: Dict[str, Any] = {
        "available": False,
        "reason": "",
        "source": "bot_state.json",
        "authority_scope": (
            "authoritative current state at JSON generation time, independent of "
            "selected log window"
        ),
        "as_of_epoch": as_of_epoch,
        "as_of_time": as_of_time,
        "time_zone": "Europe/London",
        "rolling_window_semantics": (
            "retain strikes where cutoff_epoch < strike_epoch <= as_of_epoch"
        ),
        "quarantine_active_semantics": (
            "quarantine_until_epoch > as_of_epoch; exact expiry is inactive and "
            "clears retained strikes"
        ),
        "threshold": None,
        "window_seconds": None,
        "quarantine_seconds": None,
        "authors": None,
        "author_count": None,
        "omitted_author_count": None,
        "discarded_legacy_author_count": None,
        "migrated_prior_policy_author_count": None,
        "limitation": limitation,
    }

    config_keys = (
        "AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD",
        "AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS",
        "AUTHOR_NO_REPLY_QUARANTINE_SECONDS",
    )
    if runtime_config_status != "available" or not isinstance(
        runtime_config, dict
    ):
        result["reason"] = (
            "current quarantine configuration unavailable: "
            + str(runtime_config_status or "unknown")[:320]
        )[:512]
        return result
    config_values = [runtime_config.get(key) for key in config_keys]
    if any(type(value) is not int or value <= 0 for value in config_values):
        result["reason"] = (
            "current quarantine configuration is missing or malformed"
        )
        return result
    threshold, window_seconds, quarantine_seconds = config_values
    result.update(
        {
            "threshold": threshold,
            "window_seconds": window_seconds,
            "quarantine_seconds": quarantine_seconds,
        }
    )
    if (
        as_of_epoch < 0
        or as_of_epoch > MAX_REASONABLE_STATE_EPOCH
        or as_of_time is None
    ):
        result["reason"] = "current state observation time is not representable"
        return result
    if (
        threshold > AUTHOR_NO_REPLY_PROGRESS_MAX_THRESHOLD
        or window_seconds > MAX_REASONABLE_STATE_EPOCH
        or quarantine_seconds > MAX_REASONABLE_STATE_EPOCH
        or as_of_epoch + window_seconds > MAX_REASONABLE_STATE_EPOCH
        or as_of_epoch + quarantine_seconds > MAX_REASONABLE_STATE_EPOCH
    ):
        result["reason"] = (
            "current quarantine configuration exceeds bounded reporting limits"
        )
        return result

    if runtime_state_status != "available" or not isinstance(
        runtime_state, dict
    ):
        result["reason"] = (
            "current bot state unavailable: "
            + str(runtime_state_status or "unknown")[:360]
        )[:512]
        return result
    if "author_evaluation_quarantines" not in runtime_state:
        result["reason"] = (
            "current bot state does not contain author_evaluation_quarantines"
        )
        return result
    records = runtime_state.get("author_evaluation_quarantines")
    if not isinstance(records, dict):
        result["reason"] = (
            "current author_evaluation_quarantines value is malformed"
        )
        return result

    required_fields = {
        "recent_no_reply_epochs",
        "quarantine_until_epoch",
        "last_updated_epoch",
        "latest_explicit_spam_or_abuse_epoch",
        "evidence_policy",
    }
    legacy_fields = {
        "recent_no_reply_epochs",
        "quarantine_until_epoch",
        "last_updated_epoch",
    }
    previous_policy_fields = legacy_fields | {"evidence_policy"}
    epoch_limit = max(100, threshold * 4)
    validated: List[Tuple[str, List[int], int]] = []
    discarded_legacy_author_count = 0
    migrated_prior_policy_author_count = 0
    ordered_records = sorted(
        records.items(),
        key=lambda item: (
            not valid_public_post_id(item[0]),
            len(str(item[0])),
            str(item[0]),
        ),
    )
    for position, (raw_author_id, raw_record) in enumerate(ordered_records, 1):
        author_id = str(raw_author_id)
        if not valid_public_post_id(author_id) or not isinstance(raw_record, dict):
            result["reason"] = (
                f"malformed author quarantine record at position {position}"
            )
            return result
        record_fields = set(raw_record)
        if record_fields == legacy_fields:
            # Production deliberately discards this pre-policy broad evidence
            # instead of treating it as current qualifying no-reply history.
            discarded_legacy_author_count += 1
            continue
        if (
            record_fields != previous_policy_fields
            and record_fields != required_fields
        ):
            result["reason"] = (
                f"malformed author quarantine record at position {position}"
            )
            return result
        timestamps = raw_record.get("recent_no_reply_epochs")
        until = raw_record.get("quarantine_until_epoch")
        updated = raw_record.get("last_updated_epoch")
        evidence_policy = raw_record.get("evidence_policy")
        is_legacy_policy = (
            record_fields == previous_policy_fields
            and evidence_policy
            == AUTHOR_EVALUATION_QUARANTINE_LEGACY_EVIDENCE_POLICY
        )
        is_seeded_policy = (
            record_fields == required_fields
            and evidence_policy
            == AUTHOR_EVALUATION_QUARANTINE_SEEDED_EVIDENCE_POLICY
        )
        is_current_policy = (
            record_fields == required_fields
            and evidence_policy
            == AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY
        )
        is_previous_policy = (
            record_fields == required_fields
            and evidence_policy
            == AUTHOR_EVALUATION_QUARANTINE_PREVIOUS_EVIDENCE_POLICY
        )
        is_single_sol_v1_policy = (
            record_fields == required_fields
            and evidence_policy
            == AUTHOR_EVALUATION_QUARANTINE_SINGLE_SOL_V1_EVIDENCE_POLICY
        )
        malformed = (
            not (
                is_legacy_policy
                or is_seeded_policy
                or is_previous_policy
                or is_single_sol_v1_policy
                or is_current_policy
            )
            or not isinstance(timestamps, list)
            or len(timestamps) > epoch_limit
            or any(
                type(epoch) is not int
                or epoch < 0
                or epoch > MAX_REASONABLE_STATE_EPOCH
                for epoch in timestamps
            )
            or timestamps != sorted(timestamps)
            or type(until) is not int
            or until < 0
            or until > MAX_REASONABLE_STATE_EPOCH
            or type(updated) is not int
            or updated < 0
            or updated > MAX_REASONABLE_STATE_EPOCH
        )
        if malformed:
            result["reason"] = (
                f"malformed author quarantine record at position {position}"
            )
            return result
        if is_legacy_policy:
            if not timestamps and not until:
                discarded_legacy_author_count += 1
                continue
            migrated_prior_policy_author_count += 1
        else:
            explicit_epoch = raw_record.get(
                "latest_explicit_spam_or_abuse_epoch"
            )
            if (
                type(explicit_epoch) is not int
                or explicit_epoch < 0
                or explicit_epoch > MAX_REASONABLE_STATE_EPOCH
                or explicit_epoch > updated
                or (
                    is_seeded_policy
                    and not until
                    and explicit_epoch
                    and explicit_epoch not in timestamps
                )
            ):
                result["reason"] = (
                    f"malformed author quarantine record at position {position}"
                )
                return result
            if is_seeded_policy or is_previous_policy or is_single_sol_v1_policy:
                migrated_prior_policy_author_count += 1
            if is_single_sol_v1_policy:
                timestamps = (
                    [explicit_epoch]
                    if explicit_epoch and explicit_epoch in timestamps
                    else []
                )
                until = 0
                if not timestamps:
                    continue
        validated.append((author_id, list(timestamps), until))

    cutoff = as_of_epoch - window_seconds
    authors: List[Dict[str, Any]] = []
    for author_id, timestamps, until in validated:
        recent = [
            epoch
            for epoch in timestamps
            if cutoff < epoch <= as_of_epoch
        ][-epoch_limit:]
        if until and until <= as_of_epoch:
            # Production pruning treats exact expiry as terminal and clears the
            # retained strike window together with the quarantine.
            recent = []
            until = 0
        quarantine_active = until > as_of_epoch
        if not recent and not quarantine_active:
            continue
        oldest_expiry = recent[0] + window_seconds if recent else None
        authors.append(
            {
                "author_id": author_id,
                "recent_qualifying_no_reply_epochs": recent,
                "recent_qualifying_no_reply_times": [
                    epoch_to_london_text(epoch) for epoch in recent
                ],
                "strike_count": len(recent),
                "strikes_remaining": (
                    0
                    if quarantine_active
                    else max(0, threshold - len(recent))
                ),
                "oldest_strike_expires_epoch": oldest_expiry,
                "oldest_strike_expires_time": (
                    epoch_to_london_text(oldest_expiry)
                    if oldest_expiry is not None
                    else None
                ),
                "quarantine_active": quarantine_active,
                "quarantine_until_epoch": until if quarantine_active else None,
                "quarantine_until_time": (
                    epoch_to_london_text(until)
                    if quarantine_active
                    else None
                ),
            }
        )
    omitted = max(0, len(authors) - AUTHOR_NO_REPLY_PROGRESS_MAX_AUTHORS)
    result.update(
        {
            "available": True,
            "reason": "",
            "authors": authors[:AUTHOR_NO_REPLY_PROGRESS_MAX_AUTHORS],
            "author_count": min(
                len(authors), AUTHOR_NO_REPLY_PROGRESS_MAX_AUTHORS
            ),
            "omitted_author_count": omitted,
            "discarded_legacy_author_count": discarded_legacy_author_count,
            "migrated_prior_policy_author_count": (
                migrated_prior_policy_author_count
            ),
        }
    )
    return result


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


def response_post_id_is_canonical_string(msg: str) -> bool:
    """Return whether a legacy success response stores its ID as a string."""

    marker = "response="
    if marker not in msg:
        return False
    raw = msg.split(marker, 1)[1].strip()
    try:
        data = ast.literal_eval(raw)
        payload = data.get("data") if isinstance(data, dict) else None
        return bool(
            isinstance(payload, dict)
            and valid_string_public_post_id(payload.get("id"))
        )
    except Exception:
        # The compatibility parser may salvage an old display event below,
        # but malformed payload text is never immutable success authority.
        return False


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


def try_parse_strict_json_object_from_msg(
    msg: str,
) -> Optional[Dict[str, Any]]:
    """Parse an authority-bearing EVENT with one unambiguous JSON object."""

    prefix = "EVENT "
    if not msg.startswith(prefix + "{"):
        return None
    raw = msg[len(prefix):]
    try:
        return _strict_native_json_object(
            raw.encode("utf-8"),
            label="structured EVENT",
        )
    except Exception:
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
    if (
        "remoteoperationspaused" in lowered
        or "global runtime control pause blocks remote operation" in lowered
    ):
        return "remote_operations_paused"
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


def _base_remote_control_key(value: Any) -> str:
    """Return a recognised runtime-control key without its timed suffix."""

    key = str(value or "").strip().lower()
    if key.endswith("_until"):
        key = key.removesuffix("_until")
    return key if key in REMOTE_CONTROL_SCOPE_BY_KEY else ""


def _remote_control_scope(value: Any) -> str:
    """Map one exact control key to the operation scope it pauses."""

    return REMOTE_CONTROL_SCOPE_BY_KEY.get(
        _base_remote_control_key(value),
        "unknown",
    )


def _remote_operation_scope_for_lane(value: Any) -> str:
    """Map one runtime/log lane to its successful-operation scope."""

    lane = str(value or "").strip().lower().replace("-", "_")
    return REMOTE_LANE_SCOPE.get(lane, "unknown")


def _explicit_remote_pause_scope(
    message: Any,
) -> Tuple[str, str, List[str]]:
    """Extract an explicit control key or operation lane from one exception."""

    lowered = str(message or "").lower()
    key_matches = [
        match.group(0).lower()
        for match in re.finditer(
            r"\b(?:disable|pause)_(?:all|replies|normal_replies|"
            r"quote_replies|hot_post_replies|quote_posts|meme_posts)"
            r"(?:_until)?\b",
            lowered,
        )
    ]
    if key_matches:
        keys = sorted({_base_remote_control_key(key) for key in key_matches})
        return _remote_control_scope(keys[0]), f"explicit control key {key_matches[0]}", keys
    if "global runtime control pause" in lowered:
        return "all_remote_writes", "explicit global runtime-control wording", []
    lane_match = re.search(
        r"\b(?:lane|source)\s*[=:]\s*([a-z][a-z0-9_-]*)",
        lowered,
    )
    if lane_match is not None:
        scope = _remote_operation_scope_for_lane(lane_match.group(1))
        if scope != "unknown":
            return scope, f"explicit lane {lane_match.group(1)} in the exception", []
    for phrase, scope in {
        "historical context": "historical_context_replies",
        "historical-context": "historical_context_replies",
        "quote/image": "quote_image_posts",
        "quote image": "quote_image_posts",
        "daily meme": "daily_meme_posts",
        "quote-tweet reply": "quote_replies",
        "quote tweet reply": "quote_replies",
        "hot-post reply": "hot_post_replies",
        "hot post reply": "hot_post_replies",
        "mention reply": "normal_replies",
    }.items():
        if phrase in lowered:
            return scope, f"explicit {phrase} wording in the exception", []
    return "unknown", "", []


def summarise_operational_error_health(
    errors: List[Dict[str, Any]],
    events: List[Dict[str, Any]],
    receipt_events: List[Dict[str, Any]],
    lifecycle: Iterable[Dict[str, Any]] = (),
    remote_write_transactions: Iterable[Dict[str, Any]] = (),
    handled_api_restrictions: Iterable[Dict[str, Any]] = (),
    confirmed_reply_receipt_events: Iterable[Dict[str, Any]] = (),
    current_remote_write_safety: Optional[Dict[str, Any]] = None,
    generation_time: Optional[datetime] = None,
    selected_window_end: Optional[datetime] = None,
    current_snapshot_authoritative: bool = False,
) -> Dict[str, Any]:
    """Group traceback cascades and distinguish recovered from current incidents."""
    generated_at = generation_time or datetime.now()
    lifecycle = list(lifecycle)
    remote_write_transactions = list(remote_write_transactions)
    handled_api_restrictions = list(handled_api_restrictions)
    confirmed_reply_receipt_events = list(confirmed_reply_receipt_events)
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
        "remote_operations_paused",
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
    transport_attempts: List[Dict[str, Any]] = []
    for transaction in remote_write_transactions:
        if (
            transaction.get("kind") != "tweet_transport"
            or transaction.get("phase") != "request_started"
        ):
            continue
        transaction_time = _event_time(transaction)
        target_id = str(transaction.get("reply_to_id") or "")
        transaction_id = str(transaction.get("transaction_id") or "")
        if (
            transaction_time is None
            or not target_id
            or target_id.lower() in {"none", "null"}
        ):
            continue
        transport_attempts.append(
            {
                "time": transaction_time,
                "target_id": target_id,
                "transaction_id": transaction_id,
                "lane": str(transaction.get("lane") or ""),
            }
        )

    ambiguous_reply_outcomes: List[Dict[str, Any]] = []
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
        matching_attempts = [
            attempt
            for attempt in transport_attempts
            if attempt["target_id"] == target_id
            and 0 <= (outcome_time - attempt["time"]).total_seconds() <= 10
        ]
        matching_attempts.sort(
            key=lambda attempt: (
                (outcome_time - attempt["time"]).total_seconds(),
                attempt["transaction_id"],
            )
        )
        transaction_id = (
            matching_attempts[0]["transaction_id"] if matching_attempts else ""
        )
        ambiguous_reply_outcomes.append(
            {
                "time": outcome_time,
                "lane": lane,
                "target_id": target_id,
                "transaction_id": transaction_id,
            }
        )

    ambiguous_media_outcomes: List[Dict[str, Any]] = []
    for transaction in remote_write_transactions:
        if (
            transaction.get("kind") != "media_upload"
            or transaction.get("phase") != "ambiguous"
        ):
            continue
        transaction_time = _event_time(transaction)
        if transaction_time is None:
            continue
        ambiguous_media_outcomes.append(
            {
                "time": transaction_time,
                "image": str(transaction.get("image") or ""),
            }
        )

    def matching_ambiguity_identity(
        raw: str,
        item_time: Optional[datetime],
    ) -> Optional[Dict[str, Any]]:
        """Return the strongest uniquely associated ambiguity identity."""

        if item_time is None:
            return None
        direct = re.search(r"\blane=([^\s]+) target_id=([^\s]+)", raw)
        if direct is not None:
            lane = _normalise_lane(direct.group(1))
            target_id = direct.group(2)
            matches = [
                outcome
                for outcome in ambiguous_reply_outcomes
                if outcome["lane"] == lane and outcome["target_id"] == target_id
                and seconds_between(item_time, outcome["time"]) <= 300
            ]
            if matches:
                return min(
                    matches,
                    key=lambda outcome: seconds_between(item_time, outcome["time"]),
                )
            attempts = [
                attempt
                for attempt in transport_attempts
                if attempt["target_id"] == target_id
                and seconds_between(item_time, attempt["time"]) <= 300
            ]
            attempt = min(
                attempts,
                key=lambda value: seconds_between(item_time, value["time"]),
                default={},
            )
            return {
                "time": item_time,
                "lane": lane,
                "target_id": target_id,
                "transaction_id": str(attempt.get("transaction_id") or ""),
            }

        transaction_match = re.search(
            r"\btransaction_id=([0-9a-f]{64})\b", raw
        )
        if transaction_match is not None:
            transaction_id = transaction_match.group(1)
            for outcome in ambiguous_reply_outcomes:
                if outcome.get("transaction_id") == transaction_id:
                    return outcome

        near_reply = [
            outcome
            for outcome in ambiguous_reply_outcomes
            if seconds_between(item_time, outcome["time"]) <= 10
        ]
        if near_reply:
            nearest_delta = min(
                seconds_between(item_time, outcome["time"])
                for outcome in near_reply
            )
            nearest = [
                outcome
                for outcome in near_reply
                if seconds_between(item_time, outcome["time"]) == nearest_delta
            ]
            identities = {
                (
                    str(outcome.get("transaction_id") or ""),
                    outcome["lane"],
                    outcome["target_id"],
                )
                for outcome in nearest
            }
            if len(identities) == 1:
                return nearest[0]

        near_media = [
            outcome
            for outcome in ambiguous_media_outcomes
            if seconds_between(item_time, outcome["time"]) <= 10
        ]
        if len(near_media) == 1:
            return near_media[0]

        first_line = raw.splitlines()[0].strip() if raw else ""
        persistent_barrier = bool(
            "All remote posting and reply lanes are paused by the durable "
            "remote-write safety barrier" in first_line
            or "lane stopped by the global remote-write safety barrier" in first_line
            or "reply stopped after an ambiguous remote outcome" in first_line
            or "lane created an ambiguous-post barrier" in first_line
        )
        if persistent_barrier:
            prior = [
                outcome
                for outcome in [*ambiguous_reply_outcomes, *ambiguous_media_outcomes]
                if outcome["time"] <= item_time
            ]
            if prior:
                return max(prior, key=lambda outcome: outcome["time"])
        return None

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
                outcome["lane"] == lane
                and outcome["target_id"] == target_id
                and 0
                <= (outcome["time"] - item_time).total_seconds()
                <= 300
                for outcome in ambiguous_reply_outcomes
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
            0 <= (item_time - outcome["time"]).total_seconds() <= 5
            for outcome in ambiguous_reply_outcomes
        )

    def pause_scope_for_item(
        item: Dict[str, Any],
    ) -> Tuple[str, str, List[str]]:
        """Use ordered, transaction-local evidence to scope one pause error."""

        raw = str(item.get("_raw_message") or item.get("message") or "")
        explicit = _explicit_remote_pause_scope(raw)
        if explicit[0] != "unknown":
            return explicit

        item_time = _event_time(item)
        if item_time is not None:
            nearby = sorted(
                (
                    (abs((item_time - event_time).total_seconds()), event)
                    for event in events
                    if event.get("kind") == "runtime_control_pause"
                    and (event_time := _event_time(event)) is not None
                    and abs((item_time - event_time).total_seconds()) <= 60
                ),
                key=lambda pair: (pair[0], str(pair[1].get("time") or "")),
            )
        else:
            nearby = []
        for _distance, event in nearby:
            key = _base_remote_control_key(event.get("key"))
            if key:
                return _remote_control_scope(key), "nearby structured runtime_control_pause event", [key]
            raw_lanes = event.get("control_lanes") or event.get("lanes")
            lanes = (
                raw_lanes
                if isinstance(raw_lanes, list)
                else re.split(r"\s*,\s*", str(raw_lanes or ""))
            )
            event_scopes = set()
            for lane in lanes:
                scope = _remote_control_scope(lane)
                event_scopes.add(
                    _remote_operation_scope_for_lane(lane)
                    if scope == "unknown"
                    else scope
                )
            event_scopes.discard("unknown")
            if len(event_scopes) == 1:
                return event_scopes.pop(), "nearby structured runtime_control_pause lane", []

        pending_lane = str(item.get("_pause_pending_lane") or "")
        pending_scope = _remote_operation_scope_for_lane(pending_lane)
        if pending_scope != "unknown":
            return pending_scope, f"exact pending lane {pending_lane} associated with the exception", []

        if item_time is not None:
            attempt_scopes = {
                _remote_operation_scope_for_lane(attempt.get("lane"))
                for attempt in transport_attempts
                if 0
                <= (item_time - attempt["time"]).total_seconds()
                <= 10
            } - {"unknown"}
            if len(attempt_scopes) == 1:
                return attempt_scopes.pop(), "exact pending transport-request lane associated with the exception", []
        return "unknown", "scope unavailable from retained evidence", []

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
        remote_identity: Optional[Dict[str, Any]] = None
        if category == "remote_write_ambiguity_barrier":
            remote_identity = matching_ambiguity_identity(raw, item_time)
        elif category == "conversational_reply_receipt_barrier":
            remote_identity = matching_ambiguity_identity(raw, item_time)
        if remote_identity is not None:
            item["_remote_write_identity"] = {
                "transaction_id": str(
                    remote_identity.get("transaction_id") or ""
                ),
                "lane": str(remote_identity.get("lane") or ""),
                "target_id": str(remote_identity.get("target_id") or ""),
                "image": str(remote_identity.get("image") or ""),
            }
            if remote_identity.get("transaction_id"):
                signature = "transaction:" + str(
                    remote_identity["transaction_id"]
                )
            elif remote_identity.get("target_id"):
                signature = (
                    "reply:"
                    + str(remote_identity.get("lane") or "unavailable")
                    + ":"
                    + str(remote_identity["target_id"])
                )
                if category == "conversational_reply_receipt_barrier":
                    signature += ":" + dt_text(item_time)
            else:
                signature = (
                    "media:"
                    + str(remote_identity.get("image") or "unavailable")
                    + ":"
                    + dt_text(remote_identity.get("time"))
                )
        elif category == "remote_write_ambiguity_barrier" and item_time is not None:
            signature = f"{category}:{dt_text(item_time)}"
            for (candidate_category, candidate_signature), rows in reversed(
                list(groups.items())
            ):
                previous_time = _event_time(rows[-1])
                if (
                    candidate_category == category
                    and previous_time is not None
                    and seconds_between(item_time, previous_time) <= 10
                    and not rows[-1].get("_remote_write_identity")
                ):
                    signature = candidate_signature
                    break
        elif category == "xai_provider_timeout" and item_time is not None:
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
        elif category == "remote_operations_paused":
            pause_scope, pause_evidence, pause_keys = pause_scope_for_item(item)
            item["_pause_scope"] = pause_scope
            item["_pause_scope_evidence"] = pause_evidence
            item["_pause_control_keys"] = pause_keys
            signature = f"{category}:{pause_scope}"
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
        message = str(item.get("message") or "")
        ts = _event_time(item)
        if ts is not None and "Bot started successfully" in message:
            successful_restart_times.append(ts)

    remote_write_success_times = sorted(
        ts
        for kind in (
            "remote_write_succeeded",
            "daily_meme_posted",
            "quote_image_posted",
            "mention_reply_posted",
            "hot_post_reply_posted",
            "quote_tweet_reply_posted",
        )
        for ts in event_times.get(kind, [])
    )
    remote_operation_successes: List[Dict[str, Any]] = []
    success_scopes = {
        "daily_meme_posted": {"daily_meme_posts", "all_remote_writes"},
        "quote_image_posted": {"quote_image_posts", "all_remote_writes"},
        "mention_reply_posted": {"normal_replies", "all_replies", "all_remote_writes"},
        "hot_post_reply_posted": {"hot_post_replies", "normal_replies", "all_replies", "all_remote_writes"},
        "quote_tweet_reply_posted": {"quote_replies", "all_replies", "all_remote_writes"},
        # This generic transport confirmation has no lane identity.  It can
        # prove only that a process-wide pause cleared, never a lane pause.
        "remote_write_succeeded": {"all_remote_writes"},
    }
    for event in events:
        event_time = _event_time(event)
        kind = str(event.get("kind") or "")
        scopes = success_scopes.get(kind)
        if (
            kind == "historical_context_reply"
            and event.get("status") in {"completed", "already_completed"}
        ):
            scopes = {"historical_context_replies", "all_replies", "all_remote_writes"}
        if event_time is not None and scopes:
            remote_operation_successes.append(
                {"time": event_time, "kind": kind, "scopes": scopes}
            )
    terminal_reply_receipts: List[Dict[str, Any]] = []
    for item in confirmed_reply_receipt_events:
        if item.get("source_class") == "selftest":
            continue
        if item.get("kind") not in {
            "sending_removed",
            "confirmed_state_fallback_removed",
            "removed",
        }:
            continue
        ts = _event_time(item)
        if ts is not None:
            terminal_reply_receipts.append({**item, "_time": ts})

    safety = current_remote_write_safety or {}
    if current_remote_write_safety is not None:
        annotate_remote_write_snapshot_window(
            safety,
            selected_window_end,
            current_snapshot_authoritative=current_snapshot_authoritative,
        )
    active_remote_components = (
        safety.get("active_transaction_identities")
        if isinstance(safety.get("active_transaction_identities"), list)
        else []
    )
    snapshot_incident_evidence = (
        safety.get("snapshot_incident_evidence")
        if isinstance(safety.get("snapshot_incident_evidence"), list)
        else []
    )
    identity_snapshot_available = bool(
        safety.get("configured") is True
        and safety.get("available") is True
        and safety.get("identity_snapshot_available") is True
    )
    identity_snapshot_explicitly_unavailable = bool(
        current_remote_write_safety is not None
        and not identity_snapshot_available
        and (
            safety.get("configured") is False
            or safety.get("available") is False
        )
    )

    def component_matches_identity(
        component: Dict[str, Any],
        identity: Dict[str, Any],
    ) -> bool:
        transaction_id = str(identity.get("transaction_id") or "")
        target_id = str(identity.get("target_id") or "")
        component_transactions = component.get("transaction_ids") or []
        if transaction_id and component_transactions:
            return transaction_id in component_transactions
        if target_id and target_id in (component.get("target_ids") or []):
            identity_lane = _normalise_lane(identity.get("lane"))
            component_lanes = {
                _normalise_lane(value)
                for value in component.get("lanes") or []
            } - {"unavailable"}
            if (
                identity_lane == "unavailable"
                or not component_lanes
                or identity_lane in component_lanes
            ):
                return True
        component_tokens = set(component.get("snapshot_identity_tokens") or [])
        component_tokens.update(
            "document_sha256:" + str(value)
            for value in component.get("document_sha256s") or []
        )
        component_tokens.update(
            "retirement_expected_sha256:"
            + str(source)
            + ":"
            + str(expected)
            for source in component.get("retirement_source_basenames") or []
            for expected in component.get("retirement_expected_sha256s") or []
        )
        identity_tokens = set(identity.get("snapshot_identity_tokens") or [])
        identity_tokens.update(
            "document_sha256:" + str(value)
            for value in identity.get("document_sha256s") or []
        )
        identity_tokens.update(
            "retirement_expected_sha256:"
            + str(source)
            + ":"
            + str(expected)
            for source in identity.get("retirement_source_basenames") or []
            for expected in identity.get("retirement_expected_sha256s") or []
        )
        return bool(component_tokens & identity_tokens)

    def component_is_related_to_selected_window(
        component: Dict[str, Any],
    ) -> bool:
        return bool(
            current_snapshot_authoritative
            or component.get("selected_window_relationship") in {
            "recorded_at_or_before_selected_window_end",
            "snapshot_observed_at_or_before_selected_window_end",
            }
        )

    def component_is_relevant_to_category(
        component: Dict[str, Any],
        category: str,
    ) -> bool:
        if category == "remote_write_ambiguity_barrier":
            return "ambiguity_marker" in (component.get("artifact_kinds") or [])
        if category != "conversational_reply_receipt_barrier":
            return True
        return "conversational_confirmed_reply" in (
            component.get("receipt_roles") or []
        )

    def active_component_matches(
        category: str,
        identity: Dict[str, Any],
    ) -> bool:
        return any(
            component_is_related_to_selected_window(component)
            and component_is_relevant_to_category(component, category)
            and component_matches_identity(component, identity)
            for component in active_remote_components
        )

    def remote_write_recovery_status(
        category: str,
        identity: Dict[str, Any],
        first_time: datetime,
        last_time: datetime,
    ) -> Tuple[str, str, Optional[datetime]]:
        """Reconcile one identified receipt/ambiguity transaction conservatively."""

        if identity_snapshot_available and active_component_matches(
            category,
            identity,
        ):
            return "current_unresolved", "", None
        unidentified_active = any(
            component.get("identity_available") is not True
            for component in active_remote_components
            if component_is_related_to_selected_window(component)
            and component_is_relevant_to_category(component, category)
        )
        if identity_snapshot_available and unidentified_active:
            return (
                "resolution_unavailable",
                "current barrier artefacts lack enough identity to establish whether they match this transaction",
                None,
            )
        if identity_snapshot_explicitly_unavailable:
            return (
                "resolution_unavailable",
                "current status cannot be established from retained evidence because no usable filesystem snapshot is available",
                None,
            )
        if not identity_snapshot_available:
            return "legacy_fallback", "", None

        transaction_id = str(identity.get("transaction_id") or "")
        target_id = str(identity.get("target_id") or "")
        lane = _normalise_lane(identity.get("lane"))
        archive = safety.get("reconciliation_archive") or {}
        marker_audits = archive.get("marker_reconciliations") or []
        if not marker_audits:
            latest_audit = archive.get("latest_marker_reconciliation")
            marker_audits = [latest_audit] if latest_audit else []
        def audit_matches(audit: Dict[str, Any]) -> bool:
            audit_epoch = audit.get("archived_at_epoch")
            if (
                archive.get("valid") is not True
                or type(audit_epoch) is not int
                or audit_epoch < int(last_time.timestamp())
            ):
                return False
            audit_transaction_id = str(audit.get("transaction_id") or "")
            audit_target_id = str(audit.get("target_id") or "")
            if transaction_id and audit_transaction_id:
                return bool(
                    transaction_id == audit_transaction_id
                    and (
                        not target_id
                        or not audit_target_id
                        or target_id == audit_target_id
                    )
                )
            return bool(
                target_id
                and audit_target_id == target_id
                and audit_epoch
                <= int((last_time + timedelta(hours=6)).timestamp())
            )

        matching_audits = [audit for audit in marker_audits if audit_matches(audit)]
        if category == "remote_write_ambiguity_barrier" and matching_audits:
            resolution_audit = min(
                matching_audits,
                key=lambda audit: (
                    audit["archived_at_epoch"],
                    str(audit.get("audit_path") or ""),
                ),
            )
            return (
                "historical_resolved",
                "matching durable offline reconciliation audit retired this transaction; current active barriers belong to another identity",
                datetime.fromtimestamp(resolution_audit["archived_at_epoch"]),
            )

        terminal_matches = [
            item
            for item in terminal_reply_receipts
            if str(item.get("target_id") or "") == target_id
            and (
                lane == "unavailable"
                or _normalise_lane(item.get("lane")) == lane
            )
            and item["_time"] >= first_time
        ]
        handled_deleted = [
            item
            for item in handled_api_restrictions
            if item.get("restriction_kind") == "deleted_or_inaccessible_tweet"
            and str(item.get("target_id") or "") == target_id
            and (
                lane == "unavailable"
                or _normalise_lane(item.get("lane")) == lane
            )
            and (restriction_time := _event_time(item)) is not None
            # The handled 403 is logged immediately before the ambiguity
            # wrapper, so allow it to precede the root record narrowly.
            and first_time - timedelta(minutes=5)
            <= restriction_time
            <= last_time + timedelta(minutes=5)
        ]
        if terminal_matches and handled_deleted:
            terminal_time = min(item["_time"] for item in terminal_matches)
            later_successes = [
                ts for ts in remote_write_success_times if ts > terminal_time
            ]
            if later_successes:
                return (
                    "historical_resolved",
                    "deleted/inaccessible target was handled, its sending receipt was retired, and a later remote write succeeded",
                    terminal_time,
                )

        return (
            "resolution_unavailable",
            "no matching active artefact remains, but terminal resolution is unavailable from retained evidence",
            None,
        )

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

    def remote_pause_recovery_status(
        scope: str,
        control_keys: List[str],
        last_time: datetime,
    ) -> Tuple[str, str, Optional[datetime]]:
        """Require both scope-matched control clearance and later success."""

        if scope == "unknown":
            return (
                "resolution_unavailable",
                "affected pause scope is unavailable from retained evidence; unrelated remote-write success cannot establish recovery",
                None,
            )

        normalised_keys = {key for value in control_keys if (key := _base_remote_control_key(value))}
        control = safety.get("control") or {}
        current_control_clear = bool(
            safety.get("available") is True
            and control.get("valid") is True
            and not any(
                _remote_control_scope(value)
                in (
                    {scope}
                    if normalised_keys
                    else {
                        "all_remote_writes": {"all_remote_writes"},
                        "all_replies": {"all_remote_writes", "all_replies"},
                        "normal_replies": {"all_remote_writes", "all_replies", "normal_replies"},
                        "hot_post_replies": {"all_remote_writes", "all_replies", "normal_replies", "hot_post_replies"},
                        "quote_replies": {"all_remote_writes", "all_replies", "quote_replies"},
                        "quote_image_posts": {"all_remote_writes", "quote_image_posts"},
                        "daily_meme_posts": {"all_remote_writes", "daily_meme_posts"},
                        "historical_context_replies": {"all_remote_writes", "all_replies", "historical_context_replies"},
                    }.get(scope, set())
                )
                for value in control.get("active_keys") or []
            )
        )

        explicit_clears = []
        for item in [*events, *lifecycle]:
            clear_time = _event_time(item)
            message = str(item.get("message") or "")
            if item.get("kind") == "runtime_control_clear":
                clear_scope = _remote_control_scope(item.get("key"))
            elif "runtime control pause cleared" in message.lower():
                clear_scope = _explicit_remote_pause_scope(message)[0]
            else:
                continue
            if clear_time is not None and clear_time > last_time and clear_scope == scope:
                explicit_clears.append(clear_time)

        for recovery in sorted(remote_operation_successes, key=lambda item: (item["time"], item["kind"])):
            if recovery["time"] <= last_time or scope not in recovery["scopes"]:
                continue
            if current_control_clear or any(clear <= recovery["time"] for clear in explicit_clears):
                return (
                    "historical_resolved",
                    "the affected control scope cleared and later successful "
                    + str(recovery["kind"]).replace("_", " ")
                    + " occurred in the same scope",
                    recovery["time"],
                )
        return "current_unresolved", "", None

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
        remote_identities = [
            identity
            for item in ordered
            if isinstance(identity := item.get("_remote_write_identity"), dict)
        ]
        remote_identity: Dict[str, Any] = {}
        for field in ("transaction_id", "lane", "target_id", "image"):
            values = sorted(
                {
                    str(identity.get(field) or "")
                    for identity in remote_identities
                    if identity.get(field)
                }
            )
            if values:
                remote_identity[field] = values[0]
        pause_scopes = {
            str(item.get("_pause_scope") or "unknown") for item in ordered
        }
        pause_scope = (
            next(iter(pause_scopes))
            if len(pause_scopes) == 1
            else "unknown"
        )
        pause_scope_evidence = sorted(
            {
                str(item.get("_pause_scope_evidence") or "")
                for item in ordered
                if item.get("_pause_scope_evidence")
            }
        )
        pause_control_keys = sorted(
            {
                str(key)
                for item in ordered
                for key in item.get("_pause_control_keys") or []
                if key
            }
        )
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
        elif category == "remote_operations_paused":
            status, resolution_reason, resolution_time = (
                remote_pause_recovery_status(
                    pause_scope,
                    pause_control_keys,
                    last_time,
                )
            )
            resolved = status == "historical_resolved"
        elif category in {
            "remote_write_ambiguity_barrier",
            "conversational_reply_receipt_barrier",
        } and remote_identity:
            status, resolution_reason, resolution_time = remote_write_recovery_status(
                category,
                remote_identity,
                first_time,
                last_time,
            )
            if status == "legacy_fallback":
                resolved, resolution_reason, resolution_time = recovered_after(
                    category, last_time
                )
                status = (
                    "historical_resolved" if resolved else "current_unresolved"
                )
            else:
                resolved = status == "historical_resolved"
        elif (
            category
            in {
                "remote_write_ambiguity_barrier",
                "conversational_reply_receipt_barrier",
            }
            and identity_snapshot_explicitly_unavailable
        ):
            resolved, resolution_time = False, None
            status = "resolution_unavailable"
            resolution_reason = (
                "current status cannot be established from retained evidence "
                "because no usable filesystem snapshot is available"
            )
        else:
            resolved, resolution_reason, resolution_time = recovered_after(category, last_time)
            status = "historical_resolved" if resolved else "current_unresolved"
        if remote_identity and category in {
            "remote_write_ambiguity_barrier",
            "conversational_reply_receipt_barrier",
        }:
            identity_parts = []
            if remote_identity.get("transaction_id"):
                identity_parts.append(
                    f"transaction {remote_identity['transaction_id']}"
                )
            if remote_identity.get("lane"):
                identity_parts.append(f"lane {remote_identity['lane']}")
            if remote_identity.get("target_id"):
                identity_parts.append(f"target {remote_identity['target_id']}")
            if remote_identity.get("image"):
                identity_parts.append(f"media {remote_identity['image']}")
            representative = (
                "Remote-write ambiguity: "
                if category == "remote_write_ambiguity_barrier"
                else "Conversational reply receipt: "
            ) + ", ".join(identity_parts)
        elif category == "remote_operations_paused":
            representative = (
                "Remote operations paused for "
                + REMOTE_OPERATION_SCOPE_LABELS.get(pause_scope, pause_scope)
            )
        elif pipeline_identity is not None:
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
        incident_source_refs, incident_source_ref_omitted = bounded_source_refs(
            *[item.get("source_refs") for item in ordered]
        )
        if incident_source_refs:
            incident["source_refs"] = incident_source_refs
        if incident_source_ref_omitted:
            incident["source_ref_omitted_count"] = (
                incident_source_ref_omitted
            )
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
        if category == "remote_operations_paused":
            incident["pause_scope"] = pause_scope
            incident["pause_scope_label"] = REMOTE_OPERATION_SCOPE_LABELS.get(
                pause_scope,
                pause_scope,
            )
            incident["pause_scope_evidence"] = pause_scope_evidence
            incident["pause_control_keys"] = pause_control_keys
        if remote_identity:
            incident.update(
                {
                    key: value
                    for key, value in remote_identity.items()
                    if value
                }
            )
            matching_components = [
                component
                for component in active_remote_components
                if component_matches_identity(component, remote_identity)
            ]
            if matching_components:
                incident["active_artifact_names"] = sorted(
                    {
                        name
                        for component in matching_components
                        for name in component.get("artifact_names") or []
                    }
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

    def evidence_values(
        value: Dict[str, Any],
        plural: str,
        singular: str = "",
    ) -> set[str]:
        supplied = value.get(plural) or []
        if not isinstance(supplied, (list, tuple, set)):
            supplied = [supplied]
        else:
            supplied = list(supplied)
        if singular and value.get(singular):
            supplied.append(value[singular])
        return {str(item).strip() for item in supplied if str(item).strip()}

    def evidence_blocker_kinds(value: Dict[str, Any]) -> set[str]:
        return evidence_values(value, "blocker_kinds", "blocker_kind")

    def retirement_exchange_matches_component(
        ledger: Dict[str, Any],
        retirement: Dict[str, Any],
    ) -> bool:
        if (
            str(ledger.get("ledger_state") or "")
            not in {"exchange_staged", "exchange_committed"}
            or "retirement_ledger" not in evidence_blocker_kinds(ledger)
            or "receipt_retirement" not in evidence_blocker_kinds(retirement)
        ):
            return False
        ledger_sources = evidence_values(ledger, "retirement_source_basenames")
        retirement_sources = evidence_values(
            retirement, "retirement_source_basenames"
        )
        if (
            len(ledger_sources) != 1
            or len(retirement_sources) != 1
            or ledger_sources != retirement_sources
        ):
            return False

        def non_conflicting(plural: str, singular: str = "") -> bool:
            return len(
                evidence_values(ledger, plural, singular)
                | evidence_values(retirement, plural, singular)
            ) <= 1

        expected_or_bound_hashes = (
            evidence_values(ledger, "retirement_expected_sha256s")
            | evidence_values(retirement, "retirement_expected_sha256s")
            | evidence_values(ledger, "source_receipt_sha256s")
            | evidence_values(retirement, "source_receipt_sha256s")
        )
        return bool(
            non_conflicting("receipt_roles")
            and len(expected_or_bound_hashes) <= 1
            and non_conflicting("retirement_source_identity_sha256s")
            and non_conflicting("transaction_ids", "transaction_id")
            and non_conflicting("target_ids", "target_id")
            and non_conflicting("lanes", "lane")
            and non_conflicting("source_receipt_sha256s")
        )

    def retirement_incident_summary(value: Dict[str, Any]) -> str:
        sources = sorted(
            evidence_values(value, "retirement_source_basenames")
        )
        role_labels = sorted(evidence_values(value, "receipt_role_labels"))
        expected = sorted(
            evidence_values(value, "retirement_expected_sha256s")
        )
        phases = sorted(evidence_values(value, "retirement_phases"))
        invalid = sorted(evidence_values(value, "invalid_artifact_names"))
        summary = (
            "Interrupted exact receipt retirement: source receipt "
            f"{', '.join(sources) or 'unavailable'}, role "
            f"{', '.join(role_labels) or 'unavailable'}, expected SHA-256 "
            f"{expected[0] if len(expected) == 1 else 'unavailable'}, "
            "observed retirement phases "
            f"{', '.join(phases) or 'unavailable'}"
        )
        if invalid:
            summary += "; malformed or unreadable artefacts " + ", ".join(
                invalid
            )
        ledger_state = str(value.get("ledger_state") or "")
        if ledger_state in {"exchange_staged", "exchange_committed"}:
            summary += "; blocking ledger exchange state " + ledger_state
        conflict_reasons = sorted(
            evidence_values(value, "retirement_conflict_reasons")
        )
        if conflict_reasons:
            summary += "; conflicting snapshot evidence " + ", ".join(
                conflict_reasons
            )
        return short(summary, 600)

    def merge_retirement_evidence(
        incident: Dict[str, Any],
        evidence: Dict[str, Any],
    ) -> None:
        if evidence.get("blocker_kind") == "receipt_retirement":
            incident["signature"] = evidence["signature"]
        for field, incoming in evidence.items():
            if isinstance(incoming, list) and field != "retirement_source_identities":
                existing = incident.get(field)
                if not isinstance(existing, list):
                    existing = []
                incident[field] = sorted(set(existing) | set(incoming))
        if not incident.get("retirement_source_identities"):
            incident["retirement_source_identities"] = list(
                evidence.get("retirement_source_identities") or []
            )
        kinds = evidence_blocker_kinds(incident) | evidence_blocker_kinds(evidence)
        incident["blocker_kinds"] = sorted(kinds)
        for field in (
            "ledger_state",
            "ledger_sequence",
            "ledger_detail",
            "ledger_record_sha256",
            "selected_window_relationship",
            "current_health_relationship",
        ):
            if field in evidence and evidence.get(field) not in {None, ""}:
                incident[field] = evidence[field]
        artifact_names = sorted(
            evidence_values(incident, "active_artifact_names")
            | evidence_values(incident, "artifact_names")
            | evidence_values(evidence, "artifact_names")
        )
        incident["active_artifact_names"] = artifact_names
        incident["active_artifact_count"] = len(artifact_names)
        incident["summary"] = retirement_incident_summary(incident)

    def evidence_matches_incident(
        evidence: Dict[str, Any],
        incident: Dict[str, Any],
    ) -> bool:
        if evidence.get("category") != incident.get("category"):
            return False
        blocker_kind = str(evidence.get("blocker_kind") or "")
        if (
            blocker_kind == "retirement_ledger"
            and str(evidence.get("ledger_state") or "")
            in {"exchange_staged", "exchange_committed"}
        ):
            return evidence.get("signature") == incident.get("signature")
        if (
            blocker_kind == "receipt_retirement"
            and "receipt_retirement" in evidence_blocker_kinds(incident)
            and (
                evidence.get("retirement_snapshot_conflict") is True
                or incident.get("retirement_snapshot_conflict") is True
            )
        ):
            return evidence.get("signature") == incident.get("signature")
        if (
            evidence.get("signature") == incident.get("signature")
            or component_matches_identity(evidence, incident)
        ):
            return True
        incident_summary = str(incident.get("summary") or "").lower()
        if blocker_kind == "ambiguity_marker":
            hashes = evidence.get("document_sha256s") or evidence.get("artifact_sha256s") or []
            return any(str(value).lower() in incident_summary for value in hashes)
        if blocker_kind == "protocol_activation":
            return True
        if blocker_kind == "transport_inspection":
            return "transport" in incident_summary and "journal" in incident_summary
        if blocker_kind == "media_inspection":
            return "media" in incident_summary and "receipt" in incident_summary
        if blocker_kind == "retirement_ledger":
            sources = evidence.get("retirement_source_basenames") or []
            detail = str(evidence.get("ledger_detail") or "").lower()
            return len(sources) == 1 and sources[0].lower() in incident_summary and detail in incident_summary
        if blocker_kind == "receipt_retirement":
            sources = evidence.get("retirement_source_basenames") or []
            expected = evidence.get("retirement_expected_sha256s") or []
            return len(sources) == len(expected) == 1 and sources[0].lower() in incident_summary and expected[0] in incident_summary
        return False

    if identity_snapshot_available:
        snapshot_candidates: List[Dict[str, Any]] = []
        for component in active_remote_components:
            if not component_is_related_to_selected_window(component):
                continue
            kinds = set(component.get("artifact_kinds") or [])
            roles = set(component.get("receipt_roles") or [])
            states = set(component.get("transaction_states") or [])
            invalid = component.get("invalid_artifact_names") or []
            hashes = (
                component.get("document_sha256s")
                or component.get("artifact_sha256s")
                or []
            )
            names = component.get("artifact_names") or []
            sources = component.get("retirement_source_basenames") or []
            expected = component.get("retirement_expected_sha256s") or []
            transactions = component.get("transaction_ids") or []
            targets = component.get("target_ids") or []
            lanes = component.get("lanes") or []
            fallback = hashlib.sha256(
                "|".join([*hashes, *names, *(component.get("inspection_error_identities") or [])]).encode("utf-8")
            ).hexdigest()
            retirement_fallback = hashlib.sha256(
                json.dumps(
                    {
                        "artifact_names": sorted(
                            component.get("retirement_auxiliary_names") or names
                        ),
                        "inspection_error_identities": sorted(
                            component.get("inspection_error_identities") or []
                        ),
                    },
                    ensure_ascii=False,
                    allow_nan=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            if "ambiguity_marker" in kinds:
                category, kind = "remote_write_ambiguity_barrier", "ambiguity_marker"
                identity = hashes[0] if len(hashes) == 1 else fallback
                signature = "snapshot:ambiguity-marker:" + identity
                summary = (
                    "Active remote-write ambiguity marker: transaction "
                    f"{', '.join(transactions) or 'unavailable'}, lane {', '.join(lanes) or 'unavailable'}, "
                    f"target {', '.join(targets) or 'unavailable'}, marker/document SHA-256 "
                    f"{identity[:16]}, artefacts {', '.join(names)}"
                )
            elif "receipt_retirement_auxiliary" in kinds:
                category, kind = "remote_write_transaction_barrier", "receipt_retirement"
                source = sources[0] if len(sources) == 1 else "unavailable"
                source_identity_sha256s = component.get(
                    "retirement_source_identity_sha256s"
                ) or []
                identity = (
                    expected[0]
                    if len(expected) == 1
                    else source_identity_sha256s[0]
                    if len(source_identity_sha256s) == 1
                    else retirement_fallback
                )
                signature = f"snapshot:receipt-retirement:{source}:{identity}"
                if component.get("retirement_snapshot_conflict") is True:
                    signature += ":conflict:" + retirement_fallback
                summary = ""
            elif invalid and "conversational_confirmed_reply" in roles:
                category, kind = "conversational_reply_receipt_barrier", "invalid_confirmed_reply_receipt"
                signature = "snapshot:confirmed-reply-receipt:" + fallback
                summary = "Invalid conversational confirmed-reply receipt blocks remote writes: " + ", ".join(names)
            elif invalid or states & {"invalid", "incomplete_pair", "directory_unavailable"}:
                category, kind = "remote_write_transaction_barrier", "invalid_transaction_artifact"
                signature = "snapshot:transaction:" + transactions[0] if len(transactions) == 1 else "snapshot:target:" + targets[0] if len(targets) == 1 else "snapshot:artifacts:" + fallback
                summary = (
                    "Active invalid remote-write transaction barrier: transaction "
                    f"{', '.join(transactions) or 'unavailable'}, lane {', '.join(lanes) or 'unavailable'}, "
                    f"target {', '.join(targets) or 'unavailable'}"
                )
            else:
                continue
            candidate = {
                **component,
                "category": category,
                "blocker_kind": kind,
                "blocker_kinds": [kind],
                "signature": signature,
                "summary": short(summary, 300),
            }
            if kind == "receipt_retirement":
                candidate["summary"] = retirement_incident_summary(candidate)
            snapshot_candidates.append(candidate)
        for evidence in snapshot_incident_evidence:
            if not isinstance(evidence, dict) or not component_is_related_to_selected_window(evidence):
                continue
            blocker = dict(evidence)
            if str(blocker.get("ledger_state") or "") in {
                "exchange_staged",
                "exchange_committed",
            }:
                matches = [
                    item
                    for item in snapshot_candidates
                    if retirement_exchange_matches_component(blocker, item)
                ]
                if len(matches) == 1:
                    merge_retirement_evidence(matches[0], blocker)
                    continue
            snapshot_candidates.append(blocker)
        for evidence in snapshot_candidates:
            for singular, plural in (("transaction_id", "transaction_ids"),
                                     ("target_id", "target_ids"), ("lane", "lanes")):
                evidence.setdefault(plural, [evidence[singular]] if evidence.get(singular) else [])
            matches = [item for item in incidents if evidence_matches_incident(evidence, item)]
            if matches:
                for item in matches:
                    if evidence.get("blocker_kind") == "receipt_retirement":
                        merge_retirement_evidence(item, evidence)
                    else:
                        names = sorted(
                            evidence_values(item, "active_artifact_names")
                            | evidence_values(evidence, "artifact_names")
                        )
                        item["active_artifact_names"] = names
                        item["active_artifact_count"] = len(names)
                continue
            epoch = evidence.get("recorded_at_epoch")
            observed = datetime.fromtimestamp(epoch) if type(epoch) is int else _event_time({"time": str(safety.get("observed_at") or "")})
            if observed is None:
                continue
            transaction_ids = evidence.get("transaction_ids") or []
            target_ids = evidence.get("target_ids") or []
            lanes = evidence.get("lanes") or []
            artifact_names = sorted(
                evidence_values(evidence, "active_artifact_names")
                | evidence_values(evidence, "artifact_names")
            )
            incident = {
                **evidence,
                "category": evidence["category"], "signature": evidence["signature"],
                "status": "current_unresolved", "first_seen": dt_text(observed),
                "last_seen": dt_text(observed), "record_count": 0, "traceback_count": 0,
                "affected_locations": ["current filesystem snapshot"], "summary": evidence["summary"],
                "resolution_reason": "", "resolution_time": None,
                "transaction_id": transaction_ids[0] if len(transaction_ids) == 1 else "",
                "target_id": target_ids[0] if len(target_ids) == 1 else "",
                "lane": lanes[0] if len(lanes) == 1 else "",
                "active_artifact_names": artifact_names,
                "active_artifact_count": len(artifact_names),
                "blocker_kinds": sorted(evidence_blocker_kinds(evidence)),
                "snapshot_only": True,
            }
            incidents.append(incident)
    incidents.sort(key=lambda item: (item["first_seen"], item["category"], item["signature"]))
    current = [item for item in incidents if item["status"] == "current_unresolved"]
    resolved = [item for item in incidents if item["status"] == "historical_resolved"]
    resolution_unavailable = [
        item for item in incidents if item["status"] == "resolution_unavailable"
    ]
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
        "resolution_unavailable_incident_count": len(resolution_unavailable),
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
        "resolution_unavailable_incidents": resolution_unavailable,
        "transient_provider_observations": transient_provider_observations,
        "selected_window_end": (
            dt_text(selected_window_end) if selected_window_end else None
        ),
        "safety_snapshot_observed_at": safety.get("observed_at"),
        "current_health_snapshot_authoritative": bool(
            current_snapshot_authoritative
        ),
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


def correlate_media_upload_incidents(
    records: List[Record],
    max_text: int,
    input_file_indexes: Optional[Dict[str, int]] = None,
) -> Tuple[List[Dict[str, Any]], set[str]]:
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
        source_refs, source_ref_omitted = bounded_source_refs(
            *[
                record_source_ref(item, input_file_indexes)
                for item in chain_records
            ]
        )

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
            **({"source_refs": source_refs} if source_refs else {}),
            **(
                {"source_ref_omitted_count": source_ref_omitted}
                if source_ref_omitted
                else {}
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


def load_openai_cost_cache(
    cache_path: Optional[Path] = None,
    *,
    now_utc: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Resolve digest cache/clock defaults and validate one offline snapshot."""

    path = Path(cache_path or OPENAI_COST_CACHE_PATH).expanduser()
    observed_now = now_utc or datetime.now(timezone.utc)
    return _load_openai_cost_cache(
        path,
        now_utc=observed_now,
        read_bytes=read_stable_regular_bytes,
        parse_json_object=_strict_json_object,
    )


def openai_published_cost_report(
    *,
    cache_path: Optional[Path],
    window_start_local: Optional[datetime],
    window_end_local: Optional[datetime],
    generation_time_local: datetime,
    provider_usage: Dict[str, Any],
) -> Dict[str, Any]:
    """Load the digest's offline cache and prepare its published-cost view."""

    # Kept as an input solely for callers spanning digest schema versions. The
    # current production architecture has no second conversational provider to
    # combine with OpenAI published cost.
    del provider_usage

    now_utc = local_digest_time_to_utc(generation_time_local)
    cache = load_openai_cost_cache(cache_path, now_utc=now_utc)
    return _prepare_openai_published_cost_report(
        cache,
        window_start_local=window_start_local,
        window_end_local=window_end_local,
        now_utc=now_utc,
    )


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
        writer_local_failure = any(
            _is_writer_local_failure(value)
            for value in (
                (decision or {}).get("effective_reason"),
                (decision or {}).get("reason"),
                (decision or {}).get("no_reply_reason"),
                (local_rejection or {}).get("effective_reason"),
                (local_rejection or {}).get("reason"),
            )
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
        elif writer_local_failure:
            disposition = "writer_local_failure"
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
        "writer_local_failure",
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


def _structured_value_sha256(value: Any) -> str:
    """Hash one immutable draft value using the production approval encoding."""

    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _valid_durable_ai_reply_draft(
    draft: Any,
    *,
    context: Dict[str, Any],
    lane: str,
    target_id: str,
    conversation_id: str,
    reply_text: str,
) -> bool:
    """Validate immutable draft identity and its reviewer-approval hash."""

    if not isinstance(draft, dict):
        return False
    common = {
        "schema_version",
        "strategy_version",
        "target_id",
        "thread_id",
        "candidate_source",
        "contribution_hash",
        "context_hash",
        "proposed_reply",
        "mode",
        "reviewer_verdict",
        "creation_time",
        "approval_hash",
    }
    strategy = draft.get("strategy_version")
    if strategy == "tested-reply-pipeline-20260817":
        required = common | {
            "trusted_facts_hash",
            "final_reply_kind",
            "tone",
            "factual_claims",
            "evidence_ids",
            "trusted_facts_supplied_count",
            "trusted_fact_ids_supplied",
            "used_fact_count",
            "used_fact_ids",
            "claim_risk_categories",
            "model_call_count",
            "revision_count",
            "reply_requirement",
            "route_source",
        }
        optional_repair = {
            "direct_answer_repair_attempted",
            "direct_answer_repair_outcome",
            "original_local_rejection_reason",
            "original_proposed_reply",
        }
        if (
            type(draft.get("schema_version")) is not int
            or draft.get("schema_version") != 1
            or not required.issubset(draft)
            or set(draft) - required - optional_repair
            or not SHA256_LOWER_RE.fullmatch(
                str(draft.get("trusted_facts_hash") or "")
            )
        ):
            return False
        supplied_fact_ids = draft.get("trusted_fact_ids_supplied")
        supplied_fact_count = draft.get("trusted_facts_supplied_count")
        used_fact_count = draft.get("used_fact_count")
        used_fact_ids = draft.get("used_fact_ids")
        if (
            draft.get("mode")
            not in {"direct_factual_answer", "opinion_or_principle"}
            or draft.get("final_reply_kind") not in {"factual", "unknown"}
            or draft.get("tone") != "unknown"
            or not isinstance(draft.get("factual_claims"), list)
            or not all(
                isinstance(item, str) for item in draft["factual_claims"]
            )
            or draft.get("evidence_ids") is not None
            or type(supplied_fact_count) is not int
            or supplied_fact_count < 0
            or not isinstance(supplied_fact_ids, list)
            or not all(isinstance(item, str) for item in supplied_fact_ids)
            or supplied_fact_count != len(supplied_fact_ids)
            or (
                used_fact_count != "unknown"
                and (type(used_fact_count) is not int or used_fact_count < 0)
            )
            or (
                used_fact_count == "unknown" and used_fact_ids is not None
            )
            or (
                type(used_fact_count) is int
                and (
                    not isinstance(used_fact_ids, list)
                    or not all(isinstance(item, str) for item in used_fact_ids)
                    or used_fact_count != len(used_fact_ids)
                )
            )
            or not isinstance(draft.get("claim_risk_categories"), list)
            or not all(
                isinstance(item, str)
                for item in draft["claim_risk_categories"]
            )
            or type(draft.get("model_call_count")) is not int
            or draft["model_call_count"] < 1
            or type(draft.get("revision_count")) is not int
            or draft["revision_count"] < 0
            or draft.get("reply_requirement")
            not in {"general", "claim_free", "supported_factual", "premise_neutral"}
            or not isinstance(draft.get("route_source"), str)
            or not draft["route_source"]
            or len(draft["route_source"]) > 128
        ):
            return False
    elif strategy == "ai-first-reply-v3":
        required = common | {
            "direct_factual_question_present",
            "requested_answer_type",
            "direct_answer_text",
            "tone",
            "factual_claims",
            "exact_thatcher_wording_used",
            "exact_thatcher_wording",
            "evidence_ids",
            "claim_evidence",
            "source_hashes",
            "evidence_input_hashes",
            "reviewer_reasons",
            "reviewer_sentence_assessments",
            "claim_auditor_sentence_assessments",
            "resolved_quote_id",
            "resolved_quote_context_hash",
            "proposer_model",
            "evidence_model",
            "reviewer_model",
            "claim_auditor_model",
            "proposer_prompt_version",
            "evidence_prompt_version",
            "reviewer_prompt_version",
            "claim_auditor_prompt_version",
            "model_call_count",
            "revision_count",
        }
        if (
            type(draft.get("schema_version")) is not int
            or draft.get("schema_version") != 9
            or frozenset(draft)
            not in {
                frozenset(required),
                frozenset(required | {"retrieved_count"}),
            }
        ):
            return False
        if (
            draft.get("mode")
            not in {
                "direct_factual_answer",
                "opinion_or_principle",
                "light_humour",
                "courtesy",
            }
            or draft.get("tone")
            not in {"firm", "dry", "wry", "warm", "neutral", "light", "none"}
            or type(draft.get("model_call_count")) is not int
            or draft["model_call_count"] < 1
            or type(draft.get("revision_count")) is not int
            or draft["revision_count"] not in {0, 1}
            or (
                "retrieved_count" in draft
                and (
                    type(draft.get("retrieved_count")) is not int
                    or draft["retrieved_count"] < 0
                )
            )
        ):
            return False
    else:
        return False
    incoming = context.get("incoming_contribution")
    approval = draft.get("approval_hash")
    unsigned = {key: value for key, value in draft.items() if key != "approval_hash"}
    return bool(
        draft.get("target_id") == target_id
        and draft.get("thread_id") == conversation_id
        and draft.get("candidate_source") == lane
        and draft.get("proposed_reply") == reply_text
        and draft.get("reviewer_verdict") == "approve"
        and isinstance(draft.get("mode"), str)
        and bool(draft["mode"])
        and isinstance(incoming, str)
        and bool(incoming.strip())
        and draft.get("contribution_hash")
        == hashlib.sha256(incoming.encode("utf-8")).hexdigest()
        and draft.get("context_hash") == _structured_value_sha256(context)
        and _valid_canonical_utc_timestamp(draft.get("creation_time"))
        and isinstance(approval, str)
        and SHA256_LOWER_RE.fullmatch(approval)
        and approval == _structured_value_sha256(unsigned)
    )


def _confirmed_conversational_receipt_evidence(
    receipt: Any,
) -> Optional[Dict[str, Any]]:
    """Project exact text from one narrowly validated confirmed receipt."""

    if not isinstance(receipt, dict):
        return None
    schema_version = receipt.get("schema_version")
    if type(schema_version) is not int or schema_version not in {2, 3, 4}:
        return None
    if schema_version == 2:
        if "lifecycle_state" in receipt:
            return None
    elif receipt.get("lifecycle_state") != "confirmed":
        # A sending receipt proves only an unresolved attempt, never publication.
        return None
    source = receipt.get("candidate_source")
    target_value = receipt.get("target_id")
    reply_post_value = receipt.get("reply_post_id")
    author_value = receipt.get("author_id")
    conversation_value = receipt.get("conversation_id")
    lane = (
        source
        if type(source) is str
        and source in {"mention", "hot_post_reply", "quote_tweet"}
        else "unavailable"
    )
    target_id = str(target_value or "")
    reply_post_id = str(reply_post_value or "")
    author_id = str(author_value or "")
    conversation_id = str(conversation_value or "")
    reply_text = receipt.get("reply_text")
    reply_epoch = receipt.get("reply_epoch")
    if (
        lane not in {"mention", "hot_post_reply", "quote_tweet"}
        or not valid_string_public_post_id(target_value)
        or not valid_string_public_post_id(reply_post_value)
        or not valid_string_public_post_id(author_value)
        or not valid_string_public_post_id(conversation_value)
        or not valid_conversational_public_reply_text(reply_text)
        or type(reply_epoch) is not int
        or not (
            MIN_CONFIRMED_PUBLICATION_EPOCH
            <= reply_epoch
            <= MAX_CONFIRMED_PUBLICATION_EPOCH
        )
    ):
        return None
    context = receipt.get("reply_context")
    if (
        not isinstance(context, dict)
        or context.get("target_id") != target_id
        or context.get("thread_id") != conversation_id
        or context.get("lane") != lane
    ):
        return None
    draft = receipt.get("ai_reply_draft")
    if not _valid_durable_ai_reply_draft(
        draft,
        context=context,
        lane=lane,
        target_id=target_id,
        conversation_id=conversation_id,
        reply_text=reply_text,
    ):
        return None
    original_post_id = receipt.get("original_post_id")
    if lane == "quote_tweet":
        quoted_post = context.get("quoted_post")
        if (
            not valid_string_public_post_id(original_post_id)
            or not isinstance(quoted_post, dict)
            or type(quoted_post.get("post_id")) is not str
            or quoted_post.get("post_id") != original_post_id
        ):
            return None
        original_post_id = str(original_post_id)
    elif original_post_id is not None:
        return None
    else:
        original_post_id = ""
    if "mention_pagination" in receipt:
        pagination = receipt.get("mention_pagination")
        if (
            schema_version not in {3, 4}
            or lane != "mention"
            or not isinstance(pagination, dict)
            or set(pagination) != {"base_since_id", "next_token"}
            or type(pagination.get("base_since_id")) is not str
            or (
                pagination.get("base_since_id") != ""
                and not valid_string_public_post_id(
                    pagination.get("base_since_id")
                )
            )
            or type(pagination.get("next_token")) is not str
            or not pagination.get("next_token")
            or pagination.get("next_token")
            != pagination.get("next_token").strip()
            or any(
                character.isspace()
                for character in pagination.get("next_token")
            )
        ):
            return None
    clarification = receipt.get("clarification_reply")
    if clarification is not None:
        if (
            lane not in {"mention", "hot_post_reply"}
            or not isinstance(clarification, dict)
            or set(clarification)
            != {
                "thread_id",
                "prior_bot_reply_id",
                "original_question_id",
                "trigger",
            }
            or any(
                not valid_string_public_post_id(clarification.get(field))
                for field in (
                    "thread_id",
                    "prior_bot_reply_id",
                    "original_question_id",
                )
            )
            or clarification.get("trigger")
            not in {"explicit_correction", "restated_question"}
            or clarification.get("thread_id") != conversation_id
            or draft.get("mode") != "direct_factual_answer"
        ):
            return None
    if schema_version == 4:
        attempt_epoch = receipt.get("attempt_epoch")
        confirmation_epoch = receipt.get("confirmation_epoch")
        if (
            type(attempt_epoch) is not int
            or type(confirmation_epoch) is not int
            or not (
                MIN_CONFIRMED_PUBLICATION_EPOCH
                <= attempt_epoch
                <= confirmation_epoch
                <= MAX_CONFIRMED_PUBLICATION_EPOCH
            )
            or reply_epoch != confirmation_epoch
        ):
            return None
        confirmation_date = datetime.fromtimestamp(
            confirmation_epoch,
            tz=LONDON,
        ).strftime("%Y-%m-%d")
        if receipt.get("daily_reply_date") != confirmation_date:
            return None
        if lane == "quote_tweet":
            if receipt.get("daily_quote_reply_date") != confirmation_date:
                return None
        elif "daily_quote_reply_date" in receipt:
            return None
        if "source_receipt_sha256" in receipt:
            source_hash = receipt.get("source_receipt_sha256")
            if not isinstance(source_hash, str) or not SHA256_LOWER_RE.fullmatch(
                source_hash
            ):
                return None
            sending = dict(receipt)
            sending.pop("reply_post_id", None)
            sending.pop("confirmation_epoch", None)
            sending.pop("source_receipt_sha256", None)
            sending["lifecycle_state"] = "sending"
            sending["reply_epoch"] = attempt_epoch
            attempt_date = datetime.fromtimestamp(
                attempt_epoch,
                tz=LONDON,
            ).strftime("%Y-%m-%d")
            sending["daily_reply_date"] = attempt_date
            if lane == "quote_tweet":
                sending["daily_quote_reply_date"] = attempt_date
            else:
                sending.pop("daily_quote_reply_date", None)
            if hashlib.sha256(canonical_atomic_json_bytes(sending)).hexdigest() != source_hash:
                return None
    else:
        for date_key in ("daily_reply_date", "daily_quote_reply_date"):
            if receipt.get(date_key) is not None and not isinstance(
                receipt.get(date_key),
                str,
            ):
                return None
        if "source_receipt_sha256" in receipt:
            return None
    return {
        "lane": lane,
        "target_id": target_id,
        "reply_post_id": reply_post_id,
        "original_post_id": original_post_id,
        "reply_text": reply_text,
        "reply_epoch": reply_epoch,
        "source": "confirmed_reply_receipt.json",
    }


def load_confirmed_reply_receipt_evidence(
    project_dir: Path,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Load an active exact confirmed conversational receipt, if present."""

    path = Path(project_dir) / "confirmed_reply_receipt.json"
    status: Dict[str, Any] = {
        "source": path.name,
        "available": False,
        "status": "absent",
        "record_count": 0,
        "reason": "",
    }
    try:
        raw = read_stable_private_json_bytes(
            path,
            maximum=CONFIRMED_REPLY_RECEIPT_MAX_BYTES,
        )
    except FileNotFoundError:
        return [], status
    except Exception as exc:
        status.update(
            {
                "status": "unavailable",
                "reason": bounded_exception_status("unavailable", exc),
            }
        )
        return [], status
    try:
        receipt = _strict_native_json_object(
            raw,
            label="confirmed_reply_receipt.json",
        )
        if canonical_atomic_json_bytes(receipt) != raw:
            raise ValueError("receipt is not canonical JSON")
        evidence = _confirmed_conversational_receipt_evidence(receipt)
        if evidence is None:
            raise ValueError("receipt is not a confirmed publication authority")
    except Exception as exc:
        status.update(
            {
                "status": "unavailable",
                "reason": bounded_exception_status("unavailable", exc),
            }
        )
        return [], status
    status.update({"available": True, "status": "available", "record_count": 1})
    return [evidence], status


def _valid_canonical_utc_timestamp(value: Any) -> bool:
    """Return whether a value is one bounded canonical UTC timestamp."""

    if type(value) is not str or not value.endswith("Z") or len(value) > 64:
        return False
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return False
    return bool(
        parsed.tzinfo is not None
        and parsed.utcoffset() == timezone.utc.utcoffset(parsed)
        and parsed.isoformat().replace("+00:00", "Z") == value
    )


def _valid_historical_formatter_metadata(value: Any) -> bool:
    """Mirror the durable history store's bounded formatter metadata schema."""

    if not isinstance(value, dict):
        return False
    v2_keys = {
        "formatter_version",
        "template_variant",
        "meaning_included",
        "meaning_decision_reason",
        "raw_character_count",
        "weighted_character_count",
        "verification_label",
        "source_class",
        "historical_confidence",
        "shortening_applied",
    }
    v3_keys = v2_keys | {"confidence_dimensions", "source_role_audit_version"}
    v4_keys = v3_keys | {"rendering_mode"}
    keys = frozenset(value)
    if keys not in {frozenset(v2_keys), frozenset(v3_keys), frozenset(v4_keys)}:
        return False
    version = value.get("formatter_version")
    if keys == v3_keys and version != "historical_context_reply_schema_v3":
        return False
    if keys == v4_keys and version not in {
        "historical_context_reply_schema_v4",
        "historical_context_reply_schema_v5",
    }:
        return False
    if version in {
        "historical_context_reply_schema_v3",
        "historical_context_reply_schema_v4",
        "historical_context_reply_schema_v5",
    }:
        dimensions = value.get("confidence_dimensions")
        audit_versions = {
            "historical-context-source-roles-v1",
            "historical-context-source-roles-v2-recovered-citations",
            "historical-context-source-roles-v4-multi-provider-guarded-approximate-80",
            "historical-context-source-roles-v5-independent-review-and-exclusive-counts",
            "historical-context-source-roles-v7-curated-source-adjudications",
            "historical-context-source-roles-v8-claim-specific-public-context",
            "historical-context-source-roles-v9-archive-provenance",
        }
        if (
            not isinstance(dimensions, dict)
            or set(dimensions)
            != {
                "attribution",
                "wording",
                "source_event",
                "date",
                "historical_context",
                "interpretation",
            }
            or any(
                item not in {"high", "medium", "low", "unknown"}
                for item in dimensions.values()
            )
            or value.get("source_role_audit_version") not in audit_versions
        ):
            return False
    if version in {
        "historical_context_reply_schema_v4",
        "historical_context_reply_schema_v5",
    } and value.get("rendering_mode") not in {"public", "internal"}:
        return False
    return bool(
        isinstance(version, str)
        and version.startswith("historical_context_reply_schema_v")
        and isinstance(value.get("template_variant"), str)
        and bool(value["template_variant"])
        and type(value.get("meaning_included")) is bool
        and isinstance(value.get("meaning_decision_reason"), str)
        and bool(value["meaning_decision_reason"])
        and type(value.get("raw_character_count")) is int
        and value["raw_character_count"] >= 1
        and type(value.get("weighted_character_count")) is int
        and value["weighted_character_count"] >= 1
        and (
            value.get("verification_label") is None
            or isinstance(value["verification_label"], str)
        )
        and isinstance(value.get("source_class"), str)
        and value.get("historical_confidence")
        in {"high", "medium", "low", "unavailable"}
        and type(value.get("shortening_applied")) is bool
    )


def _valid_historical_completed_item(
    parent_key: str,
    item: Any,
) -> Optional[Dict[str, Any]]:
    """Validate and project one completed historical-context history row."""

    if not isinstance(item, dict) or item.get("status") != "completed":
        return None
    receipt = {key: value for key, value in item.items() if key != "status"}
    required = {
        "schema_version",
        "parent_post_id",
        "reply_post_id",
        "quote_id",
        "reply_text",
        "reply_epoch",
        "confirmed_at",
    }
    lifecycle = required | {"lifecycle_state", "started_at", "attempt_number"}
    permitted = {
        frozenset(required),
        frozenset(lifecycle),
        frozenset(lifecycle | {"formatter_metadata"}),
        frozenset(lifecycle | {"source_receipt_sha256"}),
        frozenset(lifecycle | {"formatter_metadata", "source_receipt_sha256"}),
    }
    parent_value = receipt.get("parent_post_id")
    reply_post_value = receipt.get("reply_post_id")
    parent_id = str(parent_value or "")
    reply_post_id = str(reply_post_value or "")
    quote_id = receipt.get("quote_id")
    reply_text = receipt.get("reply_text")
    if (
        frozenset(receipt) not in permitted
        or receipt.get("schema_version") != 1
        or type(receipt.get("schema_version")) is not int
        or parent_id != parent_key
        or not valid_string_public_post_id(parent_value)
        or not valid_string_public_post_id(reply_post_value)
        or not isinstance(quote_id, str)
        or not SHA256_LOWER_RE.fullmatch(quote_id)
        or not valid_bounded_utf8_text(reply_text)
        or not reply_text.strip()
        or type(receipt.get("reply_epoch")) is not int
        or receipt["reply_epoch"] < 0
        or not isinstance(receipt.get("confirmed_at"), str)
        or not receipt["confirmed_at"].strip()
    ):
        return None
    if "lifecycle_state" in receipt and receipt.get("lifecycle_state") != "confirmed":
        return None
    if "started_at" in receipt and (
        not isinstance(receipt["started_at"], str)
        or not receipt["started_at"].strip()
    ):
        return None
    if "attempt_number" in receipt and (
        type(receipt["attempt_number"]) is not int
        or receipt["attempt_number"] < 1
    ):
        return None
    if "formatter_metadata" in receipt and not _valid_historical_formatter_metadata(
        receipt["formatter_metadata"]
    ):
        return None
    if "source_receipt_sha256" in receipt:
        source_hash = receipt.get("source_receipt_sha256")
        if not isinstance(source_hash, str) or not SHA256_LOWER_RE.fullmatch(
            source_hash
        ):
            return None
        if not (
            MIN_CONFIRMED_PUBLICATION_EPOCH
            <= receipt["reply_epoch"]
            <= MAX_CONFIRMED_PUBLICATION_EPOCH
        ):
            return None
        sending = dict(receipt)
        sending.pop("reply_post_id", None)
        sending.pop("confirmed_at", None)
        sending.pop("source_receipt_sha256", None)
        sending["lifecycle_state"] = "sending"
        if hashlib.sha256(canonical_private_json_bytes(sending)).hexdigest() != source_hash:
            return None
    return {
        "time": str(receipt.get("confirmed_at") or ""),
        "parent_post_id": parent_id,
        "reply_post_id": reply_post_id,
        "quote_id": quote_id,
        "authoritative": True,
        "reply_text": reply_text,
        "source": "historical_context_reply_history.json",
        "durable_only": True,
    }


def _valid_historical_failed_item(parent_key: str, item: Any) -> bool:
    """Validate enough of a failed row to prove the history object is coherent."""

    if not isinstance(item, dict) or item.get("status") != "failed":
        return False
    required = {
        "parent_post_id",
        "quote_id",
        "reply_text",
        "status",
        "failure",
        "attempt_count",
        "updated_at",
    }
    source_proof = {
        "remote_outcome",
        "source_receipt_sha256",
        "source_receipt_attempt_number",
    }
    allowed_sets = {
        frozenset(required),
        frozenset(required | {"formatter_metadata"}),
        frozenset(required | source_proof),
        frozenset(required | source_proof | {"formatter_metadata"}),
    }
    if (
        frozenset(item) not in allowed_sets
        or type(item.get("parent_post_id")) is not str
        or item.get("parent_post_id") != parent_key
        or not valid_string_public_post_id(parent_key)
        or not isinstance(item.get("quote_id"), str)
        or not SHA256_LOWER_RE.fullmatch(item["quote_id"])
        or not valid_bounded_utf8_text(item.get("reply_text"))
        or not item["reply_text"].strip()
        or not isinstance(item.get("failure"), str)
        or not item["failure"].strip()
        or type(item.get("attempt_count")) is not int
        or item["attempt_count"] < 1
        or not _valid_canonical_utc_timestamp(item.get("updated_at"))
        or (
            "formatter_metadata" in item
            and not _valid_historical_formatter_metadata(
                item["formatter_metadata"]
            )
        )
    ):
        return False
    if source_proof & set(item):
        return bool(
            source_proof.issubset(item)
            and item.get("remote_outcome") == "proved_non_success"
            and isinstance(item.get("source_receipt_sha256"), str)
            and SHA256_LOWER_RE.fullmatch(item["source_receipt_sha256"])
            and type(item.get("source_receipt_attempt_number")) is int
            and item["source_receipt_attempt_number"] >= 1
            and item["source_receipt_attempt_number"] == item["attempt_count"]
        )
    return True


def load_historical_reply_history_evidence(
    project_dir: Path,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Load bounded exact text from completed durable historical reply history."""

    path = Path(project_dir) / "historical_context_reply_history.json"
    status: Dict[str, Any] = {
        "source": path.name,
        "available": False,
        "status": "absent",
        "completed_record_count": 0,
        "failed_record_count": 0,
        "reason": "",
        "byte_limit": HISTORICAL_REPLY_HISTORY_MAX_BYTES,
    }
    try:
        raw = read_stable_private_json_bytes(
            path,
            maximum=HISTORICAL_REPLY_HISTORY_MAX_BYTES,
        )
    except FileNotFoundError:
        return [], status
    except Exception as exc:
        status.update(
            {
                "status": "unavailable",
                "reason": bounded_exception_status("unavailable", exc),
            }
        )
        return [], status
    try:
        history = _strict_native_json_object(
            raw,
            label="historical_context_reply_history.json",
        )
        if canonical_private_json_bytes(history) != raw:
            raise ValueError("history is not canonical JSON")
        if (
            set(history) != {"schema_version", "items"}
            or type(history.get("schema_version")) is not int
            or history.get("schema_version") != 1
            or not isinstance(history.get("items"), dict)
        ):
            raise ValueError("history root schema is invalid")
        evidence: List[Dict[str, Any]] = []
        failed_count = 0
        for position, (parent_key, item) in enumerate(
            history["items"].items(),
            1,
        ):
            projected = _valid_historical_completed_item(parent_key, item)
            if projected is not None:
                evidence.append(projected)
            elif _valid_historical_failed_item(parent_key, item):
                failed_count += 1
            else:
                raise ValueError(
                    f"invalid history item at position {position}"
                )
    except Exception as exc:
        status.update(
            {
                "status": "unavailable",
                "reason": bounded_exception_status("unavailable", exc),
            }
        )
        return [], status
    status.update(
        {
            "available": True,
            "status": "available",
            "completed_record_count": len(evidence),
            "failed_record_count": failed_count,
        }
    )
    return evidence, status


def _public_reply_text_result(
    candidates: List[Dict[str, Any]],
    *,
    unavailable_reason: str,
) -> Dict[str, Any]:
    """Resolve exact authoritative text candidates without selecting conflicts."""

    valid = [
        candidate
        for candidate in candidates
        if valid_bounded_utf8_text(candidate.get("text"))
    ]
    invalid_count = len(candidates) - len(valid)
    invalid_reasons = list(
        dict.fromkeys(
            str(candidate.get("invalid_reason") or "").strip()
            for candidate in candidates
            if not (
                valid_bounded_utf8_text(candidate.get("text"))
            )
            and str(candidate.get("invalid_reason") or "").strip()
        )
    )
    texts = list(dict.fromkeys(candidate["text"] for candidate in valid))
    sources = list(
        dict.fromkeys(
            str(candidate.get("source") or "authoritative evidence")
            for candidate in candidates
        )
    )
    references, omitted = bounded_source_refs(
        *[candidate.get("source_refs") for candidate in candidates]
    )
    if invalid_count or len(texts) > 1:
        result = {
            "public_reply_text": None,
            "public_reply_text_sha256": None,
            "public_reply_text_character_count": None,
            "public_reply_text_complete": False,
            "public_reply_text_status": "conflict",
            "public_reply_text_source": " + ".join(sources) or None,
            "public_reply_text_reason": (
                "; ".join(invalid_reasons)[:320]
                or "authoritative text evidence is malformed or exceeds the supported bound"
                if invalid_count
                else "authoritative text sources disagree"
            ),
            "correlation_status": "conflict",
        }
    elif not texts:
        result = {
            "public_reply_text": None,
            "public_reply_text_sha256": None,
            "public_reply_text_character_count": None,
            "public_reply_text_complete": False,
            "public_reply_text_status": "unavailable",
            "public_reply_text_source": None,
            "public_reply_text_reason": unavailable_reason[:320],
            "correlation_status": "unavailable",
        }
    else:
        text = texts[0]
        result = {
            "public_reply_text": text,
            "public_reply_text_sha256": hashlib.sha256(
                text.encode("utf-8")
            ).hexdigest(),
            "public_reply_text_character_count": len(text),
            "public_reply_text_complete": True,
            "public_reply_text_status": "confirmed",
            "public_reply_text_source": " + ".join(sources),
            "public_reply_text_reason": "",
            "correlation_status": "exact",
        }
    if references:
        result["source_refs"] = references
    if omitted:
        result["source_ref_omitted_count"] = omitted
    return result


def _durable_public_reply_text_candidates(
    runtime_state: Any,
    *,
    lane: str,
    target_id: str,
    reply_post_id: str,
    original_post_id: str = "",
    confirmed_receipt_evidence: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Read narrowly validated exact text for one immutable confirmed identity."""

    candidates: List[Dict[str, Any]] = []
    for item in confirmed_receipt_evidence or []:
        if (
            not isinstance(item, dict)
            or item.get("reply_post_id") != reply_post_id
        ):
            continue
        identity_matches = (
            item.get("target_id") == target_id
            and item.get("lane") == lane
            and (item.get("original_post_id") or "")
            == (original_post_id if lane == "quote_tweet" else "")
        )
        text_valid = valid_conversational_public_reply_text(
            item.get("reply_text")
        )
        candidates.append(
            {
                "text": (
                    item.get("reply_text")
                    if identity_matches and text_valid
                    else None
                ),
                "source": str(
                    item.get("source") or "confirmed_reply_receipt.json"
                ),
                "invalid_reason": (
                    "confirmed reply receipt identity disagrees with structured confirmation"
                    if not identity_matches
                    else "confirmed reply receipt text violates the conversational public-text contract"
                    if not text_valid
                    else ""
                ),
            }
        )
    if not isinstance(runtime_state, dict):
        return candidates
    history = runtime_state.get("ai_reply_history")
    if isinstance(history, list):
        for item in history[-1000:]:
            if not isinstance(item, dict):
                continue
            if item.get("reply_post_id") != reply_post_id:
                continue
            identity_matches = (
                item.get("target_id") == target_id
                and item.get("candidate_source") == lane
                and (
                    lane != "quote_tweet"
                    or item.get("original_post_id") in (None, "")
                    or item.get("original_post_id") == original_post_id
                )
            )
            text_valid = valid_conversational_public_reply_text(
                item.get("proposed_reply")
            )
            candidates.append(
                {
                    "text": (
                        item.get("proposed_reply")
                        if identity_matches and text_valid
                        else None
                    ),
                    "source": "bot_state.json.ai_reply_history",
                    "invalid_reason": (
                        "ai_reply_history identity disagrees with structured confirmation"
                        if not identity_matches
                        else "ai_reply_history text violates the conversational public-text contract"
                        if not text_valid
                        else ""
                    ),
                }
            )
    cache = runtime_state.get("tweet_cache")
    cached = cache.get(reply_post_id) if isinstance(cache, dict) else None
    if isinstance(cached, dict):
        references = cached.get("referenced_tweets")
        exact_reference = (
            references[0]
            if isinstance(references, list)
            and len(references) == 1
            and isinstance(references[0], dict)
            else None
        )
        identity_matches = (
            cached.get("id") == reply_post_id
            and cached.get("post_type") == "auto_reply"
            and isinstance(exact_reference, dict)
            and exact_reference.get("type") == "replied_to"
            and exact_reference.get("id") == target_id
            and valid_string_public_post_id(exact_reference.get("id"))
        )
        text_valid = valid_conversational_public_reply_text(
            cached.get("text")
        )
        candidates.append(
            {
                "text": (
                    cached.get("text")
                    if identity_matches and text_valid
                    else None
                ),
                "source": "bot_state.json.tweet_cache",
                "invalid_reason": (
                    "tweet_cache identity disagrees with structured confirmation"
                    if not identity_matches
                    else "tweet_cache text violates the conversational public-text contract"
                    if not text_valid
                    else ""
                ),
            }
        )
    return candidates


def _normalised_structured_reply_confirmation(
    value: Any,
) -> Optional[Dict[str, Any]]:
    """Validate the immutable identity carried by one reply-posted event."""

    if not isinstance(value, dict):
        return None
    logged_lane = value.get("lane")
    lane = (
        "mention"
        if logged_lane == "mention+hot_post_reply"
        else logged_lane
    )
    target_value = value.get("target_id")
    reply_post_value = value.get("reply_post_id")
    original_post_value = value.get("original_post_id")
    if (
        type(logged_lane) is not str
        or logged_lane
        not in {
            "mention",
            "mention+hot_post_reply",
            "hot_post_reply",
            "quote_tweet",
        }
        or not valid_string_public_post_id(target_value)
        or not valid_string_public_post_id(reply_post_value)
        or (
            lane == "quote_tweet"
            and not valid_string_public_post_id(original_post_value)
        )
        or (
            lane != "quote_tweet"
            and original_post_value is not None
            and original_post_value != ""
        )
    ):
        return None
    target_id = str(target_value)
    reply_post_id = str(reply_post_value)
    original_post_id = (
        str(original_post_value) if lane == "quote_tweet" else ""
    )
    return {
        **value,
        "lane": lane,
        "target_id": target_id,
        "reply_post_id": reply_post_id,
        "original_post_id": (
            original_post_id if lane == "quote_tweet" else ""
        ),
    }


def enrich_published_reply_text(
    report: Dict[str, Any],
    *,
    runtime_state: Any,
    structured_reply_confirmations: List[Dict[str, Any]],
    historical_reply_text_evidence: List[Dict[str, Any]],
    confirmed_receipt_evidence: Optional[List[Dict[str, Any]]] = None,
    durable_evidence_status: Optional[Dict[str, Any]] = None,
    production_event_object_ids: Optional[set[int]] = None,
) -> None:
    """Enrich confirmed reply records from exact immutable publication evidence."""

    events = report.get("events")
    if not isinstance(events, list):
        return
    warnings: List[Dict[str, Any]] = []
    warning_omitted_count = 0
    synthesized_events: List[Tuple[int, int, Dict[str, Any]]] = []
    production_ids = (
        {id(event) for event in events if isinstance(event, dict)}
        if production_event_object_ids is None
        else production_event_object_ids
    )

    def warn(
        *,
        reply_post_id: str,
        target_id: str,
        lane: str,
        reason: str,
    ) -> None:
        nonlocal warning_omitted_count
        item = {
            "reply_post_id": reply_post_id,
            "target_id": target_id,
            "lane": lane,
            "status": "conflict",
            "reason": reason[:240],
        }
        if item in warnings:
            return
        if len(warnings) >= PUBLISHED_REPLY_WARNING_LIMIT:
            warning_omitted_count += 1
            return
        warnings.append(item)

    representation_specs = {
        "mention_reply_posted": ("mention", "mention_id"),
        "hot_post_reply_posted": ("hot_post_reply", "hot_post_reply_id"),
        "quote_tweet_reply_posted": ("quote_tweet", "quote_tweet_id"),
    }
    legacy_records: List[Dict[str, Any]] = []
    legacy_records_by_reply: Dict[str, List[Dict[str, Any]]] = {}
    for event in events:
        if not isinstance(event, dict) or event.get("kind") not in representation_specs:
            continue
        legacy_records.append(event)
        reply_post_id = str(event.get("reply_post_id") or "")
        if (
            id(event) in production_ids
            and valid_public_post_id(reply_post_id)
        ):
            legacy_records_by_reply.setdefault(reply_post_id, []).append(event)

    def legacy_identity(event: Dict[str, Any]) -> Tuple[str, str, str]:
        expected_lane, target_field = representation_specs[str(event["kind"])]
        return (
            expected_lane,
            str(event.get(target_field) or ""),
            (
                str(event.get("original_post_id") or "")
                if expected_lane == "quote_tweet"
                else ""
            ),
        )

    confirmations_by_reply: Dict[str, List[Dict[str, Any]]] = {}
    for raw_confirmation in structured_reply_confirmations:
        confirmation = _normalised_structured_reply_confirmation(
            raw_confirmation
        )
        if confirmation is None:
            continue
        reply_post_id = str(confirmation["reply_post_id"])
        confirmations_by_reply.setdefault(reply_post_id, []).append(
            confirmation
        )
    for receipt_index, receipt in enumerate(confirmed_receipt_evidence or []):
        confirmation = _normalised_structured_reply_confirmation(
            {
                "time": epoch_to_london_text(
                    int(receipt.get("reply_epoch") or 0)
                )
                or "",
                "lane": receipt.get("lane"),
                "target_id": receipt.get("target_id"),
                "reply_post_id": receipt.get("reply_post_id"),
                "original_post_id": receipt.get("original_post_id"),
                "publication_authority": "confirmed_reply_receipt.json",
                "current_snapshot_authority": True,
                "_event_insertion_index": len(events),
                "_source_sequence": len(events) + receipt_index,
            }
        )
        if confirmation is None:
            continue
        reply_post_id = str(confirmation["reply_post_id"])
        if reply_post_id not in confirmations_by_reply:
            confirmations_by_reply[reply_post_id] = [confirmation]

    enriched_records: set[int] = set()
    for reply_post_id, confirmations in confirmations_by_reply.items():
        identities = {
            (
                str(item.get("lane")),
                str(item.get("target_id")),
                str(item.get("original_post_id") or ""),
            )
            for item in confirmations
        }
        identity_conflict = len(identities) != 1
        lane, target_id, original_post_id = sorted(identities)[0]
        same_reply_records = legacy_records_by_reply.get(reply_post_id, [])
        matching_records = (
            [
                event
                for event in same_reply_records
                if legacy_identity(event)
                == (lane, target_id, original_post_id)
            ]
            if not identity_conflict
            else []
        )
        matching_record_ids = {id(event) for event in matching_records}
        mismatched_records = [
            event
            for event in same_reply_records
            if id(event) not in matching_record_ids
        ]
        confirmation_refs, confirmation_refs_omitted = bounded_source_refs(
            *[item.get("source_refs") for item in confirmations]
        )
        publication_authorities = list(
            dict.fromkeys(
                str(
                    item.get("publication_authority")
                    or "structured reply_posted"
                )
                for item in confirmations
            )
        )
        current_snapshot_authority = any(
            item.get("current_snapshot_authority") is True
            for item in confirmations
        )
        if not matching_records:
            matching_records = [
                {
                    "kind": "confirmed_public_reply",
                    "time": min(
                        str(item.get("time") or "") for item in confirmations
                    ),
                    "status": "confirmed",
                    "publication_authority": " + ".join(
                        publication_authorities
                    ),
                    **(
                        {
                            "evidence_scope": (
                                "authoritative current durable snapshot, independent "
                                "of the selected log window"
                            )
                        }
                        if current_snapshot_authority
                        else {}
                    ),
                    "lane": lane if not identity_conflict else None,
                    "target_id": target_id if not identity_conflict else None,
                    "reply_post_id": reply_post_id,
                    **(
                        {"original_post_id": original_post_id}
                        if not identity_conflict and original_post_id
                        else {}
                    ),
                    **(
                        {"source_refs": confirmation_refs}
                        if confirmation_refs
                        else {}
                    ),
                }
            ]
            synthesized_events.append(
                (
                    min(
                        int(item.get("_event_insertion_index") or 0)
                        for item in confirmations
                    ),
                    min(
                        int(item.get("_source_sequence") or 0)
                        for item in confirmations
                    ),
                    matching_records[0],
                )
            )
        if identity_conflict:
            text_result = _public_reply_text_result(
                [
                    {
                        "text": None,
                        "source": "structured reply_posted",
                        "source_refs": confirmation_refs,
                    }
                ],
                unavailable_reason="",
            )
            text_result["public_reply_text_reason"] = (
                "structured reply confirmations disagree on immutable identity"
            )
            warn(
                reply_post_id=reply_post_id,
                target_id=target_id,
                lane=lane,
                reason=text_result["public_reply_text_reason"],
            )
        else:
            candidates = _durable_public_reply_text_candidates(
                runtime_state,
                lane=lane,
                target_id=target_id,
                reply_post_id=reply_post_id,
                original_post_id=original_post_id,
                confirmed_receipt_evidence=confirmed_receipt_evidence,
            )
            text_result = _public_reply_text_result(
                candidates,
                unavailable_reason=(
                    "exact confirmed text is no longer retained in current durable state"
                ),
            )
            if text_result["public_reply_text_status"] == "conflict":
                warn(
                    reply_post_id=reply_post_id,
                    target_id=target_id,
                    lane=lane,
                    reason=str(text_result["public_reply_text_reason"]),
                )
        for event in matching_records:
            legacy_kind = str(event["kind"])
            expected_lane: Optional[str] = None
            target_field: Optional[str] = None
            legacy_target_id = ""
            legacy_original_post_id = ""
            if legacy_kind in representation_specs:
                expected_lane, target_field = representation_specs[legacy_kind]
                legacy_target_id = str(event.get(target_field) or "")
                if expected_lane == "quote_tweet":
                    legacy_original_post_id = str(
                        event.get("original_post_id") or ""
                    )
            if not identity_conflict:
                event["lane"] = lane
                event["target_id"] = target_id
                if original_post_id:
                    event["original_post_id"] = (
                        legacy_original_post_id or original_post_id
                    )
            event["reply_post_id"] = reply_post_id
            combined_refs, omitted = bounded_source_refs(
                event.get("source_refs"),
                confirmation_refs,
                text_result.get("source_refs"),
            )
            event.update(
                {
                    key: value
                    for key, value in text_result.items()
                    if key not in {"source_refs", "source_ref_omitted_count"}
                }
            )
            if combined_refs:
                event["source_refs"] = combined_refs
            total_omitted = omitted + confirmation_refs_omitted + int(
                text_result.get("source_ref_omitted_count") or 0
            )
            if total_omitted:
                event["source_ref_omitted_count"] = total_omitted
            if target_field is not None and not identity_conflict:
                event.setdefault(target_field, target_id)
            enriched_records.add(id(event))

        for event in mismatched_records:
            expected_lane, target_field = representation_specs[str(event["kind"])]
            legacy_target_id = str(event.get(target_field) or "")
            event.setdefault("lane", expected_lane)
            event.setdefault("target_id", legacy_target_id or None)
            event["reply_post_id"] = reply_post_id
            combined_refs, omitted = bounded_source_refs(
                event.get("source_refs"),
                confirmation_refs,
            )
            if combined_refs:
                event["source_refs"] = combined_refs
            total_omitted = omitted + confirmation_refs_omitted
            if total_omitted:
                event["source_ref_omitted_count"] = total_omitted
            event.update(
                {
                    "correlation_status": "conflict",
                    "public_reply_text": None,
                    "public_reply_text_sha256": None,
                    "public_reply_text_character_count": None,
                    "public_reply_text_complete": False,
                    "public_reply_text_status": "conflict",
                    "public_reply_text_source": None,
                    "public_reply_text_reason": (
                        "legacy posted record identity disagrees with structured confirmation"
                    ),
                }
            )
            warn(
                reply_post_id=reply_post_id,
                target_id=target_id,
                lane=lane,
                reason=str(event["public_reply_text_reason"]),
            )
            enriched_records.add(id(event))

    consumed_historical_evidence: set[int] = set()
    for event in legacy_records:
        if (
            not isinstance(event, dict)
            or event.get("kind") not in representation_specs
            or id(event) in enriched_records
            or id(event) not in production_ids
        ):
            continue
        expected_lane, target_field = representation_specs[str(event["kind"])]
        target_id = str(event.get(target_field) or "")
        reply_post_id = str(event.get("reply_post_id") or "")
        event["lane"] = expected_lane
        event["target_id"] = target_id or None
        event["reply_post_id"] = reply_post_id or None
        event.update(
            _public_reply_text_result(
                [],
                unavailable_reason=(
                    "no retained structured reply_posted confirmation binds this "
                    "legacy posted record to exact durable text"
                ),
            )
        )
        enriched_records.add(id(event))

    evidence_by_identity: Dict[
        Tuple[str, str], List[Dict[str, Any]]
    ] = {}
    historical_evidence_by_reply: Dict[str, List[Dict[str, Any]]] = {}
    for evidence in historical_reply_text_evidence:
        if evidence.get("authoritative") is not True:
            continue
        parent_id = evidence.get("parent_post_id")
        quote_id = evidence.get("quote_id")
        reply_post_id = evidence.get("reply_post_id")
        if (
            not valid_string_public_post_id(parent_id)
            or not valid_string_public_post_id(reply_post_id)
            or not isinstance(quote_id, str)
            or SHA256_LOWER_RE.fullmatch(quote_id) is None
        ):
            continue
        evidence_by_identity.setdefault((parent_id, quote_id), []).append(
            evidence
        )
        historical_evidence_by_reply.setdefault(reply_post_id, []).append(
            evidence
        )
    for event in events:
        if (
            not isinstance(event, dict)
            or event.get("kind") != "historical_context_reply"
            or event.get("status") not in {"completed", "already_completed"}
            or id(event) not in production_ids
        ):
            continue
        parent_value = event.get("parent_post_id")
        quote_value = event.get("quote_id")
        selected_identity_valid = bool(
            valid_string_public_post_id(parent_value)
            and isinstance(quote_value, str)
            and SHA256_LOWER_RE.fullmatch(quote_value) is not None
        )
        parent_id = parent_value if selected_identity_valid else ""
        quote_id = quote_value if selected_identity_valid else ""
        evidence = (
            evidence_by_identity.get((parent_id, quote_id), [])
            if selected_identity_valid
            else []
        )
        reply_ids = {
            item["reply_post_id"] for item in evidence
        }
        identity_conflict = False
        if len(reply_ids) == 1:
            evidence = historical_evidence_by_reply.get(
                next(iter(reply_ids)),
                evidence,
            )
            identity_conflict = len(
                {
                    (
                        item.get("parent_post_id"),
                        item.get("quote_id"),
                    )
                    for item in evidence
                }
            ) != 1
        elif reply_ids:
            evidence = [
                item
                for reply_post_id in reply_ids
                for item in historical_evidence_by_reply.get(
                    reply_post_id,
                    [],
                )
            ]
        consumed_historical_evidence.update(id(item) for item in evidence)
        candidates = [
            {
                "text": item.get("reply_text"),
                "source": str(
                    item.get("source")
                    or "structured historical_context_reply_posted"
                ),
                "source_refs": item.get("source_refs"),
            }
            for item in evidence
        ]
        text_result = _public_reply_text_result(
            candidates,
            unavailable_reason=(
                "selected historical-context identity is not canonical"
                if not selected_identity_valid
                else "no retained exact historical_context_reply_posted evidence"
            ),
        )
        if len(reply_ids) > 1 or identity_conflict:
            text_result.update(
                {
                    "public_reply_text": None,
                    "public_reply_text_sha256": None,
                    "public_reply_text_character_count": None,
                    "public_reply_text_complete": False,
                    "public_reply_text_status": "conflict",
                    "public_reply_text_reason": (
                        "structured historical-context evidence disagrees on "
                        "reply or parent identity"
                    ),
                    "correlation_status": "conflict",
                }
            )
        event["reply_post_id"] = (
            next(iter(reply_ids)) if len(reply_ids) == 1 else None
        )
        combined_refs, omitted = bounded_source_refs(
            event.get("source_refs"),
            text_result.get("source_refs"),
        )
        event.update(
            {
                key: value
                for key, value in text_result.items()
                if key not in {"source_refs", "source_ref_omitted_count"}
            }
        )
        if combined_refs:
            event["source_refs"] = combined_refs
        total_omitted = omitted + int(
            text_result.get("source_ref_omitted_count") or 0
        )
        if total_omitted:
            event["source_ref_omitted_count"] = total_omitted
        if event["public_reply_text_status"] == "conflict":
            warn(
                reply_post_id=str(event.get("reply_post_id") or ""),
                target_id=parent_id,
                lane="historical_context_reply",
                reason=str(event["public_reply_text_reason"]),
            )
        enriched_records.add(id(event))

    remaining_historical_by_reply: Dict[str, List[Dict[str, Any]]] = {}
    for evidence in historical_reply_text_evidence:
        parent_id = str(evidence.get("parent_post_id") or "")
        quote_id = str(evidence.get("quote_id") or "")
        reply_post_id = str(evidence.get("reply_post_id") or "")
        if (
            id(evidence) in consumed_historical_evidence
            or evidence.get("authoritative") is not True
            or evidence.get("durable_only") is True
            or not valid_public_post_id(parent_id)
            or not valid_public_post_id(reply_post_id)
            or not quote_id
        ):
            continue
        remaining_historical_by_reply.setdefault(reply_post_id, []).append(
            evidence
        )
    for reply_post_id, evidence_items in remaining_historical_by_reply.items():
        identities = {
            (
                str(item.get("parent_post_id") or ""),
                str(item.get("quote_id") or ""),
            )
            for item in evidence_items
        }
        identity_conflict = len(identities) != 1
        parent_id, quote_id = sorted(identities)[0]
        text_result = _public_reply_text_result(
            [
                {
                    "text": item.get("reply_text"),
                    "source": str(
                        item.get("source")
                        or "structured historical_context_reply_posted"
                    ),
                    "source_refs": item.get("source_refs"),
                }
                for item in evidence_items
            ],
            unavailable_reason=(
                "confirmed historical-context text is unavailable"
            ),
        )
        if identity_conflict:
            text_result.update(
                {
                    "public_reply_text": None,
                    "public_reply_text_sha256": None,
                    "public_reply_text_character_count": None,
                    "public_reply_text_complete": False,
                    "public_reply_text_status": "conflict",
                    "public_reply_text_reason": (
                        "structured historical-context confirmations disagree "
                        "on immutable identity"
                    ),
                    "correlation_status": "conflict",
                }
            )
        normalized_event: Dict[str, Any] = {
            "kind": "confirmed_public_reply",
            "time": min(str(item.get("time") or "") for item in evidence_items),
            "status": "confirmed",
            "publication_authority": (
                "structured historical_context_reply_posted"
            ),
            "lane": "historical_context_reply",
            "target_id": parent_id if not identity_conflict else None,
            "parent_post_id": parent_id if not identity_conflict else None,
            "reply_post_id": reply_post_id,
            "quote_id": quote_id if not identity_conflict else None,
        }
        normalized_event.update(text_result)
        synthesized_events.append(
            (
                min(
                    int(item.get("_event_insertion_index") or 0)
                    for item in evidence_items
                ),
                min(
                    int(item.get("_source_sequence") or 0)
                    for item in evidence_items
                ),
                normalized_event,
            )
        )
        enriched_records.add(id(normalized_event))
        if normalized_event["public_reply_text_status"] == "conflict":
            warn(
                reply_post_id=reply_post_id,
                target_id=parent_id,
                lane="historical_context_reply",
                reason=str(normalized_event["public_reply_text_reason"]),
            )

    for offset, (insertion_index, _source_sequence, event) in enumerate(
        sorted(synthesized_events, key=lambda item: (item[0], item[1]))
    ):
        events.insert(min(insertion_index + offset, len(events)), event)
    historical_section = report.get("historical_context_replies")
    if isinstance(historical_section, dict):
        historical_section["events"] = [
            event
            for event in events
            if event.get("kind") == "historical_context_reply"
            or (
                event.get("kind") == "confirmed_public_reply"
                and event.get("lane") == "historical_context_reply"
            )
        ]

    report["published_reply_text_health"] = {
        "confirmed_record_count": len(enriched_records),
        "complete_text_record_count": sum(
            id(event) in enriched_records
            and event.get("public_reply_text_complete") is True
            for event in events
            if isinstance(event, dict)
        ),
        "conflict_count": sum(
            id(event) in enriched_records
            and event.get("public_reply_text_status") == "conflict"
            for event in events
            if isinstance(event, dict)
        ),
        "warnings": warnings,
        "warning_omitted_count": warning_omitted_count,
        "state_evidence_scope": (
            "current bounded bot_state.json retention, not reconstructed drafts"
        ),
        "durable_evidence": dict(durable_evidence_status or {}),
    }


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


def _is_writer_local_failure(reason: Any) -> bool:
    """Identify a terminal failure to obtain locally compliant writer prose."""
    normalised = str(reason or "").strip().lower()
    return (
        normalised.startswith("writer_local_rejection:")
        or normalised == "writer_link_repair_failed"
        or normalised.startswith("writer_link_repair_local_rejection:")
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


def single_call_reply_summary(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Summarise the sole production conversational decision architecture."""

    decisions = [
        item for item in events
        if item.get("kind") == "single_call_reply_decision"
    ]
    usage = [
        item for item in events
        if item.get("kind") == "single_call_reply_provider_usage"
    ]
    posting = [
        item for item in events
        if item.get("kind") == "single_call_reply_posting_outcome"
    ]
    recovered = [
        item for item in events
        if item.get("kind") == "single_call_reply_draft_recovered"
    ]

    def count_values(rows: List[Dict[str, Any]], field: str) -> Dict[str, int]:
        return dict(Counter(
            str(item.get(field) or "unavailable") for item in rows
        ).most_common())

    def average(field: str) -> Optional[float]:
        values = [
            item[field]
            for item in decisions
            if type(item.get(field)) is int
        ]
        return (sum(values) / len(values)) if values else None

    editorial_no_reply = [
        item for item in decisions
        if item.get("pipeline_status") == "no_reply"
        and item.get("outcome_type") == "editorial"
        and item.get("local_validation_status") == "passed"
    ]
    operational = [
        item for item in decisions
        if item.get("pipeline_status") == "operational_failure"
        or item.get("outcome_type") == "operational"
    ]
    model_attempts = [
        item for item in decisions
        if type(item.get("model_call_count")) is int
        and item["model_call_count"] > 0
    ]

    def candidate_key(item: Mapping[str, Any]) -> Tuple[str, str]:
        return (
            normalise_reply_lane(item.get("lane")),
            str(item.get("target_id") or ""),
        )
    invalid_call_counts = [
        item for item in decisions
        if type(item.get("model_call_count")) is not int
        or item["model_call_count"] not in {0, 1}
        or (
            item.get("pipeline_status") in {"reply", "no_reply"}
            and item["model_call_count"] != 1
        )
    ]
    model_attempt_decisions_per_candidate = Counter(
        candidate_key(item) for item in model_attempts
    )
    repeated_model_attempt_keys = {
        key
        for key, count in model_attempt_decisions_per_candidate.items()
        if count > 1
    }
    usage_per_candidate = Counter(candidate_key(item) for item in usage)
    excess_usage_keys = {
        key
        for key, count in usage_per_candidate.items()
        if count > max(1, model_attempt_decisions_per_candidate.get(key, 0))
    }

    attempt_violation_keys: set[Tuple[str, str]] = set()
    incomplete_attempt_keys: set[Tuple[str, str]] = set()
    incomplete_attempt_decision_ids: set[int] = set()
    authorised_retry_decisions: Counter[Tuple[str, str]] = Counter()
    authorised_retry_usage: Counter[Tuple[str, str]] = Counter()
    attempt_metadata_status_counts: Counter[str] = Counter()
    decision_attempt_counts: Dict[Tuple[str, str], set[int]] = {}
    usage_attempt_counts: Dict[Tuple[str, str], set[int]] = {}

    for item in [*decisions, *usage]:
        is_decision = item.get("kind") == "single_call_reply_decision"
        count_key = (
            "provider_request_attempt_count" if is_decision
            else "request_attempt_count"
        )
        status_key = f"{count_key}_status"
        status = item.get(status_key)
        if status not in {"available", "missing", "malformed", "out_of_range"}:
            if count_key not in item:
                status = "missing"
            elif type(item.get(count_key)) is int and item[count_key] >= 0:
                status = "available"
            else:
                status = "malformed"
        attempt_metadata_status_counts[str(status)] += 1
        key = candidate_key(item)
        if status in {"missing", "malformed"}:
            incomplete_attempt_keys.add(key)
            if is_decision:
                incomplete_attempt_decision_ids.add(id(item))
            continue
        if status == "out_of_range":
            attempt_violation_keys.add(key)
            continue

        attempt_count = item.get(count_key)
        assert type(attempt_count) is int
        if attempt_count == 2:
            (
                authorised_retry_decisions
                if is_decision
                else authorised_retry_usage
            )[key] += 1
        if attempt_count > 2:
            attempt_violation_keys.add(key)
        if is_decision:
            model_call_count = item.get("model_call_count")
            if model_call_count == 0 and attempt_count != 0:
                attempt_violation_keys.add(key)
            elif model_call_count == 1:
                decision_attempt_counts.setdefault(key, set()).add(attempt_count)
                if attempt_count not in {1, 2}:
                    attempt_violation_keys.add(key)
        else:
            usage_attempt_counts.setdefault(key, set()).add(attempt_count)
            if attempt_count not in {1, 2}:
                attempt_violation_keys.add(key)

    attempt_count_mismatch_keys = {
        key
        for key in decision_attempt_counts.keys() & usage_attempt_counts.keys()
        if model_attempt_decisions_per_candidate.get(key) == 1
        and usage_per_candidate.get(key) == 1
        and decision_attempt_counts[key] != usage_attempt_counts[key]
    }
    authorised_pre_execution_retry_count = sum(
        max(
            authorised_retry_decisions.get(key, 0),
            authorised_retry_usage.get(key, 0),
        )
        for key in authorised_retry_decisions.keys() | authorised_retry_usage.keys()
    )
    invalid_call_ids = {id(item) for item in invalid_call_counts}
    violating_candidate_keys = (
        excess_usage_keys
        | attempt_violation_keys
        | attempt_count_mismatch_keys
    )
    decision_candidate_keys = {candidate_key(item) for item in decisions}
    violating_decision_ids = invalid_call_ids | {
        id(item)
        for item in decisions
        if candidate_key(item) in violating_candidate_keys
    }
    incomplete_decision_ids = {
        id(item)
        for item in decisions
        if id(item) not in violating_decision_ids
        and (
            id(item) in incomplete_attempt_decision_ids
            or candidate_key(item) in incomplete_attempt_keys
        )
    }
    compliant_decisions = [
        item
        for item in decisions
        if id(item) not in violating_decision_ids
        and id(item) not in incomplete_decision_ids
    ]
    one_call_violations = len(violating_decision_ids) + len(
        violating_candidate_keys - decision_candidate_keys
    )
    one_call_incomplete = len(incomplete_decision_ids) + len(
        incomplete_attempt_keys
        - violating_candidate_keys
        - decision_candidate_keys
    )
    token_fields = (
        "input_tokens",
        "cached_input_tokens",
        "cache_write_input_tokens",
        "output_tokens",
        "reasoning_tokens",
        "total_tokens",
    )
    token_totals = {
        field: sum(
            item[field] for item in usage if type(item.get(field)) is int
        )
        for field in token_fields
    }
    latencies = [
        item["provider_latency_ms"]
        for item in usage
        if type(item.get("provider_latency_ms")) is int
    ]
    confirmed_keys = {
        (normalise_reply_lane(item.get("lane")), str(item.get("target_id") or ""))
        for item in posting
        if item.get("status") == "confirmed"
    }
    posting_failures = [
        item for item in posting
        if item.get("status") not in {"confirmed", "dry_run"}
    ]
    schema_failure_count = sum(
        item.get("error_category") == "schema_validation"
        for item in decisions
    )
    local_failure_count = sum(
        item.get("local_validation_status") == "failed"
        and item.get("error_category") != "schema_validation"
        for item in decisions
    )
    return {
        "candidate_evaluation_count": len(decisions),
        "reply_decision_count": sum(
            item.get("pipeline_status") == "reply" for item in decisions
        ),
        "replies_posted_count": len(confirmed_keys),
        "editorial_no_reply_count": len(editorial_no_reply),
        "operational_failure_count": len(operational),
        "posting_failure_count": len(posting_failures),
        "recovered_draft_count": len(recovered),
        "model_attempt_count": len(model_attempts),
        "one_call_compliant_count": len(compliant_decisions),
        "one_call_violation_count": one_call_violations,
        "one_call_incomplete_count": one_call_incomplete,
        "one_call_compliance": (
            "failed" if one_call_violations else
            "incomplete" if one_call_incomplete else
            "passed" if decisions else "no_candidates"
        ),
        "repeated_model_attempt_candidate_count": len(
            repeated_model_attempt_keys
        ),
        "excess_provider_usage_candidate_count": len(excess_usage_keys),
        "authorised_pre_execution_retry_count": (
            authorised_pre_execution_retry_count
        ),
        "provider_request_attempt_metadata_status_counts": dict(
            attempt_metadata_status_counts.most_common()
        ),
        "provider_request_attempt_mismatch_candidate_count": len(
            attempt_count_mismatch_keys
        ),
        "strategy_version_counts": count_values(decisions, "strategy_version"),
        "model_counts": count_values(decisions, "model"),
        "lane_counts": count_values(decisions, "lane"),
        "reply_kind_counts": count_values(
            [item for item in decisions if item.get("pipeline_status") == "reply"],
            "reply_kind",
        ),
        "no_reply_reason_counts": count_values(editorial_no_reply, "reason_code"),
        "operational_failure_reason_counts": count_values(
            operational, "failure_reason"
        ),
        "error_category_counts": count_values(operational, "error_category"),
        "schema_validation_failure_count": schema_failure_count,
        "local_validation_failure_count": local_failure_count,
        "average_visible_turn_count": average("visible_turn_count"),
        "average_visible_character_count": average("visible_character_count"),
        "average_same_author_interaction_count": average(
            "same_author_interaction_count"
        ),
        "average_recent_conversational_reply_count": average(
            "recent_conversational_reply_count"
        ),
        "average_trusted_fact_count": average("trusted_fact_count"),
        "average_supplied_image_count": average("supplied_image_count"),
        "provider_usage_event_count": len(usage),
        "token_totals": token_totals,
        "provider_latency_average_ms": (
            sum(latencies) / len(latencies) if latencies else None
        ),
        "provider_latency_maximum_ms": max(latencies) if latencies else None,
        "provider_request_attempt_counts": count_values(
            usage, "request_attempt_count"
        ),
        "cost_total": {
            "status": "unavailable_during_log_analysis",
            "amount": None,
        },
    }


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
    selected_window_end: Optional[datetime] = None,
    current_snapshot_authoritative: bool = False,
    current_runtime_state: Optional[Dict[str, Any]] = None,
    input_file_indexes: Optional[Dict[str, int]] = None,
    confirmed_receipt_evidence: Optional[List[Dict[str, Any]]] = None,
    historical_history_evidence: Optional[List[Dict[str, Any]]] = None,
    durable_reply_evidence_status: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Aggregate parsed production records into digest metrics."""
    if selected_window_end is None:
        selected_window_end = max(
            (record.ts for record in records),
            default=None,
        )
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
    reply_visual_description_events: List[Dict[str, Any]] = []
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
    pending_original_editorial_shadow_companions: Counter = Counter()
    generated_identity_shadow_events: List[Dict[str, Any]] = []
    generated_identity_policy_events: List[Dict[str, Any]] = []
    generated_image_spacing_events: List[Dict[str, Any]] = []
    latest_generated_image_spacing: Dict[str, Any] = {}
    cooldown_active: List[Dict[str, Any]] = []
    lifecycle: List[Dict[str, Any]] = []
    routine_skip_counts = Counter()
    configs: Dict[str, str] = {}
    latest_state: Optional[Dict[str, Any]] = None
    latest_state_ts: Optional[datetime] = None

    pending_quote: Dict[str, Any] = {}
    quote_post_correlations: Dict[str, Dict[str, Dict[str, Any]]] = {}
    invalid_quote_post_evidence: Dict[str, set[str]] = {}
    engagement_trial_outcomes: List[Dict[str, Any]] = []
    engagement_correlation_warnings: List[Dict[str, Any]] = []
    engagement_correlation_warning_keys: set[
        Tuple[str, str, str, str, str]
    ] = set()
    engagement_correlation_warning_counts: Counter[str] = Counter()
    engagement_correlation_warning_omitted_count = 0
    pending_meme: Dict[str, Any] = {}
    pending_mention: Dict[str, Any] = dict(initial_pending_mention or {})
    pending_qt: Dict[str, Any] = dict(initial_pending_qt or {})
    if pending_mention:
        pending_mention.setdefault("_identity_production", True)
        pending_mention.setdefault("_reply_post_id_production", True)
    if pending_qt:
        pending_qt.setdefault("_identity_production", True)
        pending_qt.setdefault("_reply_post_id_production", True)
    pending_confirmed_reply_receipt: Dict[str, Any] = {}
    active_xai_context: Optional[Dict[str, Any]] = dict(initial_active_xai_context or {}) or None
    active_xai_call_attempt_index: Optional[int] = (
        0 if restored_xai_call_attempt else None
    )
    production_active_xai_call_attempt_index = active_xai_call_attempt_index
    selftest_active_xai_call_attempt_index: Optional[int] = None
    last_created_post: Dict[str, Any] = {}
    production_pending_quote = pending_quote
    production_pending_meme = pending_meme
    production_pending_mention = pending_mention
    production_pending_qt = pending_qt
    production_pending_confirmed_reply_receipt = pending_confirmed_reply_receipt
    production_last_created_post = last_created_post
    production_active_xai_context = active_xai_context
    selftest_pending_quote: Dict[str, Any] = {}
    selftest_pending_meme: Dict[str, Any] = {}
    selftest_pending_mention: Dict[str, Any] = {}
    selftest_pending_qt: Dict[str, Any] = {}
    selftest_pending_confirmed_reply_receipt: Dict[str, Any] = {}
    selftest_last_created_post: Dict[str, Any] = {}
    selftest_active_xai_context: Optional[Dict[str, Any]] = None
    previous_record_production = True
    structured_reply_confirmations: List[Dict[str, Any]] = []
    historical_reply_text_evidence: List[Dict[str, Any]] = list(
        historical_history_evidence or []
    )
    current_source_record: Optional[Record] = None
    production_event_object_ids: set[int] = set()

    def add_event(kind: str, ts: datetime, **kwargs: Any) -> Dict[str, Any]:
        ev = {"time": ts.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind}
        for k, v in kwargs.items():
            if isinstance(v, str):
                ev[k] = short(v, max_text)
            else:
                ev[k] = v
        if kind in PROVENANCE_EVENT_KINDS and current_source_record is not None:
            ev["source_refs"] = [
                record_source_ref(current_source_record, input_file_indexes)
            ]
        events.append(ev)
        if (
            current_source_record is not None
            and not is_selftest_log_path(current_source_record.path)
        ):
            production_event_object_ids.add(id(ev))
        stats[kind] += 1
        return ev

    def add_engagement_correlation_warning(
        *,
        time_text: str,
        post_id: str,
        field: str,
        left_event: str,
        right_event: str,
        status: str = "conflict",
    ) -> None:
        nonlocal engagement_correlation_warning_omitted_count
        event_pair = tuple(sorted((left_event, right_event)))
        key = (post_id, field, event_pair[0], event_pair[1], status)
        if key in engagement_correlation_warning_keys:
            return
        engagement_correlation_warning_keys.add(key)
        engagement_correlation_warning_counts[post_id] += 1
        if (
            len(engagement_correlation_warnings)
            >= ENGAGEMENT_CORRELATION_WARNING_LIMIT
        ):
            engagement_correlation_warning_omitted_count += 1
            return
        engagement_correlation_warnings.append(
            {
                "time": time_text,
                "post_id": post_id,
                "field": field,
                "event_types": list(event_pair),
                "status": status,
                "message": (
                    f"post_id={post_id} field={field} {status} between "
                    f"{event_pair[0]} and {event_pair[1]}"
                ),
            }
        )

    def retain_quote_post_evidence(
        post_id: str,
        event_type: str,
        payload: Dict[str, Any],
    ) -> None:
        """Keep one fixed-shape evidence slot per structured event and post."""
        if (
            current_source_record is not None
            and is_selftest_log_path(current_source_record.path)
        ):
            return
        slots = quote_post_correlations.setdefault(post_id, {})
        existing = slots.get(event_type)
        if existing is None:
            payload["_conflicted_fields"] = set()
            slots[event_type] = payload
            return
        time_text = str(payload.get("time") or existing.get("time") or "")
        conflicted_fields = existing.setdefault("_conflicted_fields", set())
        for field, value in payload.items():
            if field in {"event", "time"} or value is None or value == "":
                continue
            if field == "source_refs":
                references, omitted = bounded_source_refs(
                    existing.get("source_refs"), value
                )
                existing["source_refs"] = references
                prior_omitted = existing.get("source_ref_omitted_count")
                cumulative_omitted = (
                    prior_omitted
                    if type(prior_omitted) is int and prior_omitted >= 0
                    else 0
                ) + omitted
                if cumulative_omitted:
                    existing["source_ref_omitted_count"] = (
                        cumulative_omitted
                    )
                continue
            if field in conflicted_fields:
                continue
            old_value = existing.get(field)
            if old_value is None or old_value == "":
                existing[field] = value
            elif old_value != value:
                conflicted_fields.add(field)
                existing[field] = None
                add_engagement_correlation_warning(
                    time_text=time_text,
                    post_id=post_id,
                    field=field,
                    left_event=event_type,
                    right_event=event_type,
                )

    def note_invalid_quote_post_evidence(
        post_id: Any,
        event_type: str,
        time_text: str,
    ) -> None:
        """Record malformed observability without letting it replace authority."""

        if (
            not valid_string_public_post_id(post_id)
            or (
                current_source_record is not None
                and is_selftest_log_path(current_source_record.path)
            )
        ):
            return
        invalid_quote_post_evidence.setdefault(post_id, set()).add(event_type)
        add_engagement_correlation_warning(
            time_text=time_text,
            post_id=post_id,
            field="producer_schema",
            left_event=event_type,
            right_event="required_contract",
            status="invalid",
        )

    local_rejections_by_identity: Dict[Tuple[str, str], Dict[str, Any]] = {}

    def add_or_merge_local_rejection(
        ts: datetime,
        *,
        lane: Any,
        target_id: Any,
        **kwargs: Any,
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

    def add_receipt_event(kind: str, r: Record, **kwargs: Any) -> None:
        item = {
            "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
            "kind": kind,
            "level": r.level,
            "message": short(r.msg, 500),
            "source_class": (
                "selftest" if is_selftest_log_path(r.path) else "production"
            ),
        }
        item.update(kwargs)
        item["source_refs"] = [record_source_ref(r, input_file_indexes)]
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
            "source_class": (
                "selftest" if is_selftest_log_path(r.path) else "production"
            ),
        }
        item.update(kwargs)
        item["source_refs"] = [record_source_ref(r, input_file_indexes)]
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

    for record_index, r in enumerate(records):
        current_source_record = r
        msg = r.msg
        production_record = not is_selftest_log_path(r.path)
        if previous_record_production:
            production_pending_quote = pending_quote
            production_pending_meme = pending_meme
            production_pending_mention = pending_mention
            production_pending_qt = pending_qt
            production_pending_confirmed_reply_receipt = (
                pending_confirmed_reply_receipt
            )
            production_last_created_post = last_created_post
            production_active_xai_context = active_xai_context
            production_active_xai_call_attempt_index = (
                active_xai_call_attempt_index
            )
        else:
            selftest_pending_quote = pending_quote
            selftest_pending_meme = pending_meme
            selftest_pending_mention = pending_mention
            selftest_pending_qt = pending_qt
            selftest_pending_confirmed_reply_receipt = (
                pending_confirmed_reply_receipt
            )
            selftest_last_created_post = last_created_post
            selftest_active_xai_context = active_xai_context
            selftest_active_xai_call_attempt_index = (
                active_xai_call_attempt_index
            )
        if production_record:
            pending_quote = production_pending_quote
            pending_meme = production_pending_meme
            pending_mention = production_pending_mention
            pending_qt = production_pending_qt
            pending_confirmed_reply_receipt = (
                production_pending_confirmed_reply_receipt
            )
            last_created_post = production_last_created_post
            active_xai_context = production_active_xai_context
            active_xai_call_attempt_index = (
                production_active_xai_call_attempt_index
            )
        else:
            pending_quote = selftest_pending_quote
            pending_meme = selftest_pending_meme
            pending_mention = selftest_pending_mention
            pending_qt = selftest_pending_qt
            pending_confirmed_reply_receipt = (
                selftest_pending_confirmed_reply_receipt
            )
            last_created_post = selftest_last_created_post
            active_xai_context = selftest_active_xai_context
            active_xai_call_attempt_index = (
                selftest_active_xai_call_attempt_index
            )
        previous_record_production = production_record
        compatibility_event_obj = (
            try_parse_json_object_from_msg(msg)
            if msg.startswith("EVENT ")
            else None
        )
        strict_structured_event_obj = (
            try_parse_strict_json_object_from_msg(msg)
            if msg.startswith("EVENT ")
            else None
        )
        # Structured EVENT fields are projected into the JSON contract only
        # after duplicate-key, finite-number, UTF-8 and exact-envelope
        # validation.  The compatibility parse is detection-only so malformed
        # events can still contribute bounded parser diagnostics without
        # leaking non-standard JSON values into the digest.
        structured_event_obj = strict_structured_event_obj
        is_reply_visual_description_event = bool(
            (
                compatibility_event_obj
                and compatibility_event_obj.get("event")
                == "reply_visual_description"
            )
            or (
                compatibility_event_obj is None
                and msg.startswith("EVENT ")
                and re.search(
                    r'"event"\s*:\s*"reply_visual_description"', msg
                )
            )
        )

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
                "source_refs": [record_source_ref(r, input_file_indexes)],
            }
            x_requests.append(request_event)
            latest_x_request_by_source[r.src] = request_event
            stats[f"x_request_endpoint_{request_start['endpoint'].replace('/', '_')}"] += 1

        transaction_event = (
            parse_remote_write_transaction_event(r) if production_record else None
        )
        if transaction_event is not None:
            transaction_event["source_refs"] = [
                record_source_ref(r, input_file_indexes)
            ]
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
            or "runtime control pause cleared" in msg.lower()
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
                "source_refs": [record_source_ref(r, input_file_indexes)],
            })
        elif is_confirmed_post_recovery and r.level in {"ERROR", "CRITICAL", "WARNING"}:
            confirmed_post_recovery.append({
                "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                "level": r.level,
                "where": f"{r.src}:{r.line}",
                "message": short(msg, 900),
                "source_refs": [record_source_ref(r, input_file_indexes)],
            })
        elif is_confirmed_reply_recovery and r.level in {"ERROR", "CRITICAL", "WARNING"}:
            confirmed_reply_recovery.append({
                "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                "level": r.level,
                "where": f"{r.src}:{r.line}",
                "message": short(msg, 900),
                "source_refs": [record_source_ref(r, input_file_indexes)],
            })
        elif is_receipt_routine and r.level in {"ERROR", "CRITICAL", "WARNING"}:
            pass
        elif is_asset_metadata_warning and r.level in {"ERROR", "CRITICAL", "WARNING"}:
            pass
        elif is_reply_media_context and r.level in {"ERROR", "CRITICAL", "WARNING"}:
            pass
        elif is_reply_visual_description_event and r.level in {
            "ERROR",
            "CRITICAL",
            "WARNING",
        }:
            pass
        elif is_handled_reply_restriction and r.level in {"ERROR", "CRITICAL", "WARNING"}:
            # The raw X API 403 is classified below. Follow-up warnings such as
            # "marking skipped without consuming quota" are expected handling.
            pass
        elif r.level in {"ERROR", "CRITICAL"} or (r.level == "WARNING" and "Bot stopped by KeyboardInterrupt" not in msg):
            error_item = {
                "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                "level": r.level,
                "where": f"{r.src}:{r.line}",
                "message": short(msg, 900),
                "_raw_message": msg,
                "_fingerprint": record_fingerprint(r),
                "source_refs": [record_source_ref(r, input_file_indexes)],
            }
            if classify_operational_error(msg) == "remote_operations_paused":
                source = str(r.src or "").lower()
                pending_lane = ""
                if "historical_context" in source:
                    pending_lane = "historical_context_reply"
                elif "quote_tweet" in source and pending_qt:
                    pending_lane = "quote_tweet"
                elif (
                    any(token in source for token in ("mention", "normal", "reply"))
                    and pending_mention
                ):
                    pending_lane = str(
                        pending_mention.get("source") or "mention"
                    )
                elif "meme" in source and pending_meme:
                    pending_lane = "daily_meme"
                elif "quote" in source and pending_quote:
                    pending_lane = "quote_image"
                if pending_lane:
                    error_item["_pause_pending_lane"] = pending_lane
            errors.append(error_item)

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

        # Strict structured EVENT lines provide immutable publication evidence;
        # older human-readable success lines still define the final digest event.
        if msg.startswith("EVENT "):
            event_obj = structured_event_obj
            if is_reply_visual_description_event:
                visual_event = (
                    parse_reply_visual_description_event(event_obj)
                    if event_obj is not None
                    else None
                )
                if visual_event is None:
                    stats["reply_visual_description_malformed_events"] += 1
                else:
                    reply_visual_description_events.append(
                        {
                            **visual_event,
                            "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                        }
                    )
                    stats["reply_visual_description_events"] += 1
            elif event_obj and event_obj.get("event") == "main_post_posted":
                authority_event_obj = (
                    strict_structured_event_obj
                    if strict_structured_event_obj
                    and strict_structured_event_obj.get("event")
                    == "main_post_posted"
                    else None
                )
                raw_post_id = event_obj.get("post_id")
                main_core_valid = bool(
                    authority_event_obj is not None
                    and valid_string_public_post_id(
                        authority_event_obj.get("post_id")
                    )
                    and type(authority_event_obj.get("lane")) is str
                    and authority_event_obj.get("lane")
                    in {"quote_image", "daily_meme"}
                )
                if main_core_valid:
                    raw_post_id = authority_event_obj.get("post_id")
                    post_id = raw_post_id
                    payload: Dict[str, Any] = {
                        "event": "main_post_posted",
                        "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                        "post_id": post_id,
                        "lane": authority_event_obj.get("lane"),
                        "source_refs": [
                            record_source_ref(r, input_file_indexes)
                        ],
                    }
                    for key in ("line_no", "image_no"):
                        value = authority_event_obj.get(key)
                        if type(value) is int and value >= 0:
                            payload[key] = value
                    for key in ("image_basename",):
                        value = authority_event_obj.get(key)
                        if isinstance(value, str) and len(value) <= 500:
                            payload[key] = value
                    for key in ("image_hash", "quote_hash"):
                        value = authority_event_obj.get(key)
                        if (
                            isinstance(value, str)
                            and SHA256_LOWER_RE.fullmatch(value) is not None
                        ):
                            payload[key] = value
                    image_score = authority_event_obj.get("image_score")
                    if (
                        (
                            type(image_score) is int
                            and abs(image_score) <= 1_000_000_000
                        )
                        or (
                            type(image_score) is float
                            and math.isfinite(image_score)
                            and abs(image_score) <= 1_000_000_000
                        )
                    ):
                        payload["image_score"] = image_score
                    experiment_status = engagement_main_metadata_status(
                        authority_event_obj
                    )
                    if experiment_status == "valid":
                        payload.update(
                            {
                                key: authority_event_obj.get(key)
                                for key in (
                                    "engagement_experiment_id",
                                    "engagement_experiment_plan_sha256",
                                    "engagement_experiment_pair_id",
                                    "engagement_experiment_arm",
                                    "engagement_experiment_member_position",
                                    "engagement_experiment_publication_order",
                                    "engagement_experiment_sequence",
                                    "engagement_question_present",
                                    "engagement_approved_question_sha256",
                                    "engagement_public_text_sha256",
                                )
                            }
                        )
                    elif experiment_status == "invalid":
                        note_invalid_quote_post_evidence(
                            post_id,
                            "main_post_posted.engagement_metadata",
                            r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                        )
                    retain_quote_post_evidence(
                        post_id,
                        "main_post_posted",
                        payload,
                    )
                else:
                    note_invalid_quote_post_evidence(
                        raw_post_id,
                        "main_post_posted",
                        r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                    )
                if event_obj.get("lane") == "daily_meme":
                    pending_meme.update({
                        "post_id": event_obj.get("post_id"),
                        "file": event_obj.get("filename"),
                    })
            elif event_obj and event_obj.get("event") == "account_root_posted":
                authority_event_obj = (
                    strict_structured_event_obj
                    if strict_structured_event_obj
                    and strict_structured_event_obj.get("event")
                    == "account_root_posted"
                    else None
                )
                raw_post_id = event_obj.get("post_id")
                if valid_account_root_publication_identity(
                    authority_event_obj
                ):
                    raw_post_id = authority_event_obj.get("post_id")
                    post_id = raw_post_id
                    payload = {
                        "event": "account_root_posted",
                        "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                        "post_id": post_id,
                        "event_version": authority_event_obj.get(
                            "event_version"
                        ),
                        "root_post_id": authority_event_obj.get(
                            "root_post_id"
                        ),
                        "conversation_id": authority_event_obj.get(
                            "conversation_id"
                        ),
                        "lane": authority_event_obj.get("lane"),
                        "publication_authority": authority_event_obj.get(
                            "publication_authority"
                        ),
                        "source_refs": [
                            record_source_ref(r, input_file_indexes)
                        ],
                    }
                    optional_fields_valid = True
                    quote_id = authority_event_obj.get("quote_id")
                    if quote_id is None:
                        payload["quote_id"] = None
                    elif (
                        isinstance(quote_id, str)
                        and SHA256_LOWER_RE.fullmatch(quote_id) is not None
                    ):
                        payload["quote_id"] = quote_id
                    else:
                        optional_fields_valid = False
                    for key in ("quote_text", "public_text"):
                        value = authority_event_obj.get(key)
                        if value is None:
                            payload[key] = None
                        elif valid_bounded_utf8_text(
                            value,
                            allow_empty=True,
                        ):
                            payload[key] = value
                        else:
                            optional_fields_valid = False
                    visible_source = authority_event_obj.get(
                        "visible_text_source"
                    )
                    if (
                        visible_source is None
                        or (
                            isinstance(visible_source, str)
                            and len(visible_source) <= 100
                        )
                    ):
                        payload["visible_text_source"] = visible_source
                    else:
                        optional_fields_valid = False
                    if not optional_fields_valid:
                        for key in (
                            "quote_id",
                            "quote_text",
                            "public_text",
                            "visible_text_source",
                        ):
                            payload.pop(key, None)
                        note_invalid_quote_post_evidence(
                            post_id,
                            "account_root_posted.optional_fields",
                            r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                        )
                    retain_quote_post_evidence(
                        post_id,
                        "account_root_posted",
                        payload,
                    )
                else:
                    note_invalid_quote_post_evidence(
                        raw_post_id,
                        "account_root_posted",
                        r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                    )
            elif (
                event_obj
                and event_obj.get("event")
                == "engagement_question_experimental_member_confirmed"
            ):
                authority_event_obj = (
                    strict_structured_event_obj
                    if strict_structured_event_obj
                    and strict_structured_event_obj.get("event")
                    == "engagement_question_experimental_member_confirmed"
                    else None
                )
                raw_post_id = (
                    authority_event_obj.get("post_id")
                    if authority_event_obj is not None
                    else None
                )
                if valid_engagement_confirmation_event(authority_event_obj):
                    post_id = raw_post_id
                    retain_quote_post_evidence(
                        post_id,
                        "engagement_question_experimental_member_confirmed",
                        {
                            "event": (
                                "engagement_question_experimental_member_confirmed"
                            ),
                            "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                            "post_id": post_id,
                            "source_refs": [
                                record_source_ref(r, input_file_indexes)
                            ],
                            **{
                                key: authority_event_obj.get(key)
                                for key in (
                                    "plan_sha256",
                                    "pair_id",
                                    "member_position",
                                    "arm",
                                    "publication_sequence",
                                )
                            },
                        },
                    )
                else:
                    note_invalid_quote_post_evidence(
                        event_obj.get("post_id"),
                        "engagement_question_experimental_member_confirmed",
                        r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                    )
            elif event_obj and event_obj.get("event") in {
                "engagement_question_experiment_invalid",
                "engagement_question_experimental_member_deferred",
                "engagement_question_treatment_notification_write_failed",
            }:
                event_name = str(event_obj["event"])
                outcome = {
                    "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                    "event": event_name,
                    "source_refs": [
                        record_source_ref(r, input_file_indexes)
                    ],
                    "experiment_id": bounded_event_text(
                        event_obj.get("experiment_id"), max_characters=200
                    ),
                    "plan_sha256": (
                        event_obj.get("plan_sha256")
                        if isinstance(event_obj.get("plan_sha256"), str)
                        and SHA256_LOWER_RE.fullmatch(
                            event_obj["plan_sha256"]
                        )
                        else None
                    ),
                    "post_id": (
                        event_obj.get("post_id")
                        if valid_string_public_post_id(
                            event_obj.get("post_id")
                        )
                        else None
                    ),
                    "pair_id": (
                        event_obj.get("pair_id")
                        if isinstance(event_obj.get("pair_id"), str)
                        and ENGAGEMENT_PAIR_ID_RE.fullmatch(
                            event_obj["pair_id"]
                        )
                        else None
                    ),
                    "member_position": bounded_event_nonnegative_integer(
                        event_obj.get("member_position"), maximum=2
                    ),
                    "reason": bounded_event_text(
                        event_obj.get("reason"), max_characters=240
                    ),
                    "started": bounded_event_boolean(
                        event_obj.get("started")
                    ),
                    "exception_class": bounded_event_text(
                        event_obj.get("exception_class"), max_characters=200
                    ),
                    "authority_component": bounded_event_text(
                        event_obj.get("authority_component"),
                        max_characters=200,
                    ),
                }
                engagement_trial_outcomes.append(outcome)
            elif event_obj and event_obj.get("event") == "historical_context_semantic_gate":
                record_historical_context_semantic_gate(
                    event_obj, r.ts, add_event=add_event
                )
            elif event_obj and event_obj.get("event") == "historical_context_runtime":
                record_historical_context_runtime(
                    event_obj, r.ts, stats, add_event=add_event
                )
            elif event_obj and event_obj.get("event") == "reply_evidence_unavailable":
                lane = bounded_event_text(
                    event_obj.get("lane"),
                    default="unavailable",
                    max_characters=100,
                )
                add_event(
                    "reply_evidence_unavailable",
                    r.ts,
                    lane=lane,
                    target_id=(
                        event_obj.get("target_id")
                        if valid_string_public_post_id(
                            event_obj.get("target_id")
                        )
                        else ""
                    ),
                )
                stats[f"reply_evidence_unavailable_lane_{lane}"] += 1
            elif event_obj and event_obj.get("event") == "runtime_control_pause":
                control_lanes = bounded_event_string_list(
                    event_obj.get("lanes"), limit=20, item_max_characters=100
                )
                add_event(
                    "runtime_control_pause",
                    r.ts,
                    key=bounded_event_text(
                        event_obj.get("key"),
                        default="unavailable",
                        max_characters=200,
                    ),
                    lanes=", ".join(control_lanes),
                    control_lanes=control_lanes,
                    until_epoch=bounded_event_nonnegative_integer(
                        event_obj.get("until_epoch")
                    ),
                )
                stats["runtime_control_pause"] += 1
            elif event_obj and event_obj.get("event") == "runtime_control_clear":
                control_lanes = bounded_event_string_list(
                    event_obj.get("lanes"), limit=20, item_max_characters=100
                )
                add_event(
                    "runtime_control_clear",
                    r.ts,
                    key=bounded_event_text(
                        event_obj.get("key"),
                        default="unavailable",
                        max_characters=200,
                    ),
                    lanes=", ".join(control_lanes),
                    control_lanes=control_lanes,
                )
                stats["runtime_control_clear"] += 1
            elif event_obj and event_obj.get("event") == "clarification_reply_cap_override":
                add_event(
                    "clarification_reply_cap_override",
                    r.ts,
                    target_id=(
                        event_obj.get("target_id")
                        if valid_string_public_post_id(
                            event_obj.get("target_id")
                        )
                        else ""
                    ),
                    thread_id=(
                        event_obj.get("thread_id")
                        if valid_string_public_post_id(
                            event_obj.get("thread_id")
                        )
                        else ""
                    ),
                    author_id=(
                        event_obj.get("author_id")
                        if valid_string_public_post_id(
                            event_obj.get("author_id")
                        )
                        else ""
                    ),
                    bypassed_cap=bounded_event_text(
                        event_obj.get("bypassed_cap"),
                        default="",
                        max_characters=100,
                    ),
                )
                stats["clarification_reply_cap_override"] += 1
            elif event_obj and event_obj.get("event") == "clarification_reply_used":
                add_event(
                    "clarification_reply_used",
                    r.ts,
                    target_id=(event_obj.get("target_id") if valid_string_public_post_id(event_obj.get("target_id")) else ""),
                    thread_id=(event_obj.get("thread_id") if valid_string_public_post_id(event_obj.get("thread_id")) else ""),
                    author_id=(event_obj.get("author_id") if valid_string_public_post_id(event_obj.get("author_id")) else ""),
                    reply_post_id=(event_obj.get("reply_post_id") if valid_string_public_post_id(event_obj.get("reply_post_id")) else ""),
                    trigger=bounded_event_text(
                        event_obj.get("trigger"),
                        default="",
                        max_characters=100,
                    ),
                )
                stats["clarification_reply_used"] += 1
            elif event_obj and event_obj.get("event") == "repair_reply_completed":
                add_event(
                    "repair_reply_completed",
                    r.ts,
                    target_id=(event_obj.get("target_id") if valid_string_public_post_id(event_obj.get("target_id")) else ""),
                    thread_id=(event_obj.get("thread_id") if valid_string_public_post_id(event_obj.get("thread_id")) else ""),
                    author_id=(event_obj.get("author_id") if valid_string_public_post_id(event_obj.get("author_id")) else ""),
                    reply_post_id=(event_obj.get("reply_post_id") if valid_string_public_post_id(event_obj.get("reply_post_id")) else ""),
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
                    since_id=(
                        event_obj.get("since_id")
                        if event_obj.get("since_id") is None
                        or valid_string_public_post_id(event_obj.get("since_id"))
                        else None
                    ),
                    pages_completed=bounded_event_nonnegative_integer(
                        event_obj.get("pages_completed"), maximum=1_000_000
                    ),
                    highest_mention_id=(
                        event_obj.get("highest_mention_id")
                        if event_obj.get("highest_mention_id") is None
                        or valid_string_public_post_id(
                            event_obj.get("highest_mention_id")
                        )
                        else None
                    ),
                    continuation_token_present=bounded_event_boolean(
                        event_obj.get("continuation_token_present")
                    ),
                    backlog_age_seconds=bounded_event_nonnegative_integer(
                        event_obj.get("backlog_age_seconds")
                    ),
                    reason=bounded_event_text(
                        event_obj.get("reason"), default="", max_characters=240
                    ),
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
                    author_id=(
                        event_obj.get("author_id")
                        if valid_string_public_post_id(
                            event_obj.get("author_id")
                        )
                        else ""
                    ),
                    target_id=(
                        event_obj.get("target_id")
                        if valid_string_public_post_id(
                            event_obj.get("target_id")
                        )
                        else ""
                    ),
                    strike_count=bounded_event_nonnegative_integer(
                        event_obj.get("strike_count"), maximum=250_000
                    ),
                    quarantine_until_epoch=bounded_event_nonnegative_integer(
                        event_obj.get("quarantine_until_epoch")
                    ),
                    pipeline_evaluations_skipped=bounded_event_nonnegative_integer(
                        event_obj.get("pipeline_evaluations_skipped"),
                        maximum=1_000_000,
                    ),
                )
                stats[kind] += 1
            elif event_obj and event_obj.get("event") == "reply_posted":
                authority_event_obj = (
                    strict_structured_event_obj
                    if strict_structured_event_obj
                    and strict_structured_event_obj.get("event")
                    == "reply_posted"
                    else None
                )
                if production_record and authority_event_obj is not None:
                    structured_reply_confirmations.append(
                        {
                            "time": dt_text(r.ts),
                            "lane": authority_event_obj.get("lane"),
                            "target_id": authority_event_obj.get("target_id"),
                            "reply_post_id": authority_event_obj.get(
                                "reply_post_id"
                            ),
                            "author_id": authority_event_obj.get("author_id"),
                            "original_post_id": authority_event_obj.get(
                                "original_post_id"
                            ),
                            "_event_insertion_index": len(events),
                            "_source_sequence": record_index,
                            "source_refs": [
                                record_source_ref(r, input_file_indexes)
                            ],
                        }
                    )
            elif (
                event_obj
                and event_obj.get("event")
                == "historical_context_reply_posted"
            ):
                authority_event_obj = (
                    strict_structured_event_obj
                    if strict_structured_event_obj
                    and strict_structured_event_obj.get("event")
                    == "historical_context_reply_posted"
                    else None
                )
                publication_event_obj = authority_event_obj or event_obj
                parent_value = publication_event_obj.get("parent_post_id")
                reply_post_value = publication_event_obj.get("reply_post_id")
                root_value = publication_event_obj.get("root_post_id")
                conversation_value = publication_event_obj.get(
                    "conversation_id"
                )
                quote_value = publication_event_obj.get("quote_id")
                parent_post_id = (
                    parent_value if isinstance(parent_value, str) else ""
                )
                reply_post_id = (
                    reply_post_value
                    if isinstance(reply_post_value, str)
                    else ""
                )
                quote_id = quote_value if isinstance(quote_value, str) else ""
                reply_text = publication_event_obj.get("reply_text")
                authoritative = (
                    production_record
                    and authority_event_obj is not None
                    and type(authority_event_obj.get("event_version")) is int
                    and authority_event_obj.get("event_version") == 1
                    and authority_event_obj.get("lane")
                    == "historical_context_reply"
                    and authority_event_obj.get("publication_authority")
                    == "confirmed_transport"
                    and valid_string_public_post_id(parent_value)
                    and valid_string_public_post_id(reply_post_value)
                    and valid_string_public_post_id(root_value)
                    and valid_string_public_post_id(conversation_value)
                    and root_value == parent_value
                    and conversation_value == parent_value
                    and isinstance(quote_value, str)
                    and SHA256_LOWER_RE.fullmatch(quote_value) is not None
                    and valid_bounded_utf8_text(reply_text)
                )
                historical_reply_text_evidence.append(
                    {
                        "time": dt_text(r.ts),
                        "parent_post_id": parent_post_id,
                        "reply_post_id": reply_post_id,
                        "quote_id": quote_id,
                        "authoritative": authoritative,
                        "reply_text": reply_text if authoritative else None,
                        "source": "structured historical_context_reply_posted",
                        "durable_only": False,
                        "_event_insertion_index": len(events),
                        "_source_sequence": record_index,
                        "source_refs": [
                            record_source_ref(r, input_file_indexes)
                        ],
                    }
                )
            elif event_obj and event_obj.get("event") == "historical_context_reply":
                historical_fields = prepare_historical_context_reply(event_obj)
                status = historical_fields["status"]
                historical_event = add_event(
                    "historical_context_reply", r.ts, **historical_fields
                )
                if status in {"completed", "already_completed"}:
                    strict_anchor = (
                        strict_structured_event_obj
                        if strict_structured_event_obj
                        and strict_structured_event_obj.get("event")
                        == "historical_context_reply"
                        else None
                    )
                    anchor_valid = bool(
                        strict_anchor is not None
                        and type(strict_anchor.get("status")) is str
                        and strict_anchor.get("status") == status
                        and valid_string_public_post_id(
                            strict_anchor.get("parent_post_id")
                        )
                        and isinstance(strict_anchor.get("quote_id"), str)
                        and SHA256_LOWER_RE.fullmatch(
                            strict_anchor["quote_id"]
                        )
                        is not None
                        and type(strict_anchor.get("character_count")) is int
                        and 0 <= strict_anchor.get("character_count") <= 25_000
                        and (
                            "reply_preview" not in strict_anchor
                            or valid_bounded_utf8_text(
                                strict_anchor.get("reply_preview"),
                                allow_empty=True,
                            )
                        )
                    )
                    if not anchor_valid:
                        historical_event["reply_post_id"] = None
                        historical_event.update(
                            _public_reply_text_result(
                                [],
                                unavailable_reason=(
                                    "structured historical-context anchor is not canonical"
                                ),
                            )
                        )
                        production_event_object_ids.discard(
                            id(historical_event)
                        )
                count_historical_context_reply(status, stats)
            elif event_obj and event_obj.get("event") == "posting_transaction_state":
                context_state = bounded_event_text(
                    event_obj.get("context_reply_state"),
                    default="unavailable",
                    max_characters=100,
                )
                add_event(
                    "posting_transaction_state",
                    r.ts,
                    parent_post_id=(
                        event_obj.get("parent_post_id")
                        if valid_string_public_post_id(
                            event_obj.get("parent_post_id")
                        )
                        else ""
                    ),
                    main_post_state=bounded_event_text(
                        event_obj.get("main_post_state"),
                        default="unavailable",
                        max_characters=100,
                    ),
                    context_reply_state=context_state,
                    context_state_persisted=bounded_event_boolean(
                        event_obj.get("context_state_persisted")
                    ),
                    reason=bounded_event_text(
                        event_obj.get("reason"),
                        default="",
                        max_characters=1000,
                    ),
                )
                stats[f"context_transaction_state_{context_state}"] += 1
            elif event_obj and event_obj.get("event") == "historical_context_obligation":
                record_historical_context_obligation(
                    event_obj, r.ts, stats, add_event=add_event
                )
            elif event_obj and event_obj.get("event") == "historical_context_outbox":
                record_historical_context_outbox(
                    event_obj, r.ts, stats, add_event=add_event
                )
            elif event_obj and event_obj.get("event") == "daily_meme_failure":
                stage = bounded_event_text(
                    event_obj.get("stage"),
                    default="unavailable",
                    max_characters=100,
                )
                add_event(
                    "daily_meme_failure",
                    r.ts,
                    status=bounded_event_text(
                        event_obj.get("status"),
                        default="failed",
                        max_characters=100,
                    ),
                    stage=stage,
                    post_id=(
                        event_obj.get("post_id")
                        if valid_string_public_post_id(
                            event_obj.get("post_id")
                        )
                        else ""
                    ),
                    error_type=bounded_event_text(
                        event_obj.get("error_type"),
                        default="",
                        max_characters=200,
                    ),
                    reason=bounded_event_text(
                        event_obj.get("reason"),
                        default="",
                        max_characters=1000,
                    ),
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
                        min(len(retrieved_ids), 1_000_000)
                        if isinstance(retrieved_ids, list)
                        else None
                    )
                evidence_fields["grounded"] = bounded_event_boolean(
                    event_obj.get("grounded")
                )
                decision_event = add_event(
                    "reply_strategy_decision",
                    r.ts,
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
                        min(len(retrieved_ids), 1_000_000)
                        if isinstance(retrieved_ids, list)
                        else None
                    )
                evidence_fields["grounded"] = bounded_event_boolean(
                    event_obj.get("grounded")
                )
                add_event(
                    "reply_strategy_outcome", r.ts,
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
            elif event_obj and event_obj.get("event") == "reply_target_terminal":
                add_event(
                    "reply_target_terminal", r.ts,
                    lane=bounded_event_text(event_obj.get("lane"), default="unavailable", max_characters=100),
                    target_id=(event_obj.get("target_id") if valid_string_public_post_id(event_obj.get("target_id")) else ""),
                    outcome=bounded_event_text(event_obj.get("outcome"), default="reply_not_permitted", max_characters=100),
                    reason=bounded_event_text(event_obj.get("reason"), default="", max_characters=500),
                )
            elif event_obj and event_obj.get("event") == "reply_strategy_rejection":
                add_event(
                    "reply_strategy_rejection", r.ts,
                    lane=bounded_event_text(event_obj.get("lane"), default="unavailable", max_characters=100),
                    reason=bounded_event_text(event_obj.get("reason"), default="other", max_characters=500),
                )
            elif event_obj and event_obj.get("event") == "single_call_reply_decision":
                temperature = event_obj.get("temperature")
                if type(temperature) not in {int, float} or not math.isfinite(
                    float(temperature)
                ):
                    temperature = None
                (
                    provider_request_attempt_count,
                    provider_request_attempt_count_status,
                ) = bounded_event_nonnegative_integer_observation(
                    event_obj,
                    "provider_request_attempt_count",
                    maximum=1_000_000,
                )
                provider_status_code = event_obj.get("provider_status_code")
                if (
                    type(provider_status_code) is not int
                    or not 100 <= provider_status_code <= 599
                ):
                    provider_status_code = None
                add_event(
                    "single_call_reply_decision",
                    r.ts,
                    lane=normalise_reply_lane(event_obj.get("lane")),
                    target_id=(
                        event_obj.get("target_id")
                        if valid_string_public_post_id(event_obj.get("target_id"))
                        else ""
                    ),
                    strategy_version=bounded_event_text(
                        event_obj.get("strategy_version"),
                        default="unavailable",
                        max_characters=200,
                    ),
                    model=bounded_event_text(
                        event_obj.get("model"),
                        default="unavailable",
                        max_characters=200,
                    ),
                    reasoning_effort=bounded_event_text(
                        event_obj.get("reasoning_effort"), max_characters=50
                    ),
                    temperature=temperature,
                    prompt_sha256=bounded_event_text(
                        event_obj.get("prompt_sha256"), max_characters=64
                    ),
                    response_schema_sha256=bounded_event_text(
                        event_obj.get("response_schema_sha256"), max_characters=64
                    ),
                    payload_sha256=bounded_event_text(
                        event_obj.get("payload_sha256"), max_characters=64
                    ),
                    decision=bounded_event_text(
                        event_obj.get("decision"), max_characters=50
                    ),
                    reply_kind=bounded_event_text(
                        event_obj.get("reply_kind"), max_characters=100
                    ),
                    reason_code=bounded_event_text(
                        event_obj.get("reason_code"), max_characters=100
                    ),
                    used_fact_count=bounded_event_nonnegative_integer(
                        event_obj.get("used_fact_count"), maximum=32
                    ),
                    visible_turn_count=bounded_event_nonnegative_integer(
                        event_obj.get("visible_turn_count"), maximum=12
                    ),
                    visible_character_count=bounded_event_nonnegative_integer(
                        event_obj.get("visible_character_count"), maximum=12_000
                    ),
                    same_author_interaction_count=bounded_event_nonnegative_integer(
                        event_obj.get("same_author_interaction_count"), maximum=8
                    ),
                    recent_conversational_reply_count=bounded_event_nonnegative_integer(
                        event_obj.get(
                            "recent_conversational_reply_count",
                            event_obj.get("recent_reply_count"),
                        ),
                        maximum=30,
                    ),
                    trusted_fact_count=bounded_event_nonnegative_integer(
                        event_obj.get("trusted_fact_count"), maximum=32
                    ),
                    supplied_image_count=bounded_event_nonnegative_integer(
                        event_obj.get("supplied_image_count"), maximum=2
                    ),
                    model_call_count=bounded_event_nonnegative_integer(
                        event_obj.get("model_call_count"), maximum=1
                    ),
                    local_validation_status=bounded_event_text(
                        event_obj.get("local_validation_status"), max_characters=50
                    ),
                    error_category=bounded_event_text(
                        event_obj.get("error_category"), max_characters=100
                    ),
                    failure_reason=bounded_event_text(
                        event_obj.get("failure_reason"), max_characters=200
                    ),
                    outcome_type=bounded_event_text(
                        event_obj.get("outcome_type"), max_characters=50
                    ),
                    pipeline_status=bounded_event_text(
                        event_obj.get("pipeline_status"), max_characters=50
                    ),
                    provider_latency_ms=bounded_event_nonnegative_integer(
                        event_obj.get("provider_latency_ms"), maximum=86_400_000
                    ),
                    provider_request_attempt_count=provider_request_attempt_count,
                    provider_request_attempt_count_status=(
                        provider_request_attempt_count_status
                    ),
                    provider_status_code=provider_status_code,
                    provider_reset_epoch=bounded_event_nonnegative_integer(
                        event_obj.get("provider_reset_epoch")
                    ),
                    provider_retry_after_seconds=bounded_event_nonnegative_integer(
                        event_obj.get("provider_retry_after_seconds"),
                        maximum=7 * 24 * 60 * 60,
                    ),
                    provider_response_id=bounded_event_text(
                        event_obj.get("provider_response_id"), max_characters=300
                    ),
                )
            elif event_obj and event_obj.get("event") == "single_call_reply_provider_usage":
                (
                    request_attempt_count,
                    request_attempt_count_status,
                ) = bounded_event_nonnegative_integer_observation(
                    event_obj,
                    "request_attempt_count",
                    maximum=1_000_000,
                )
                add_event(
                    "single_call_reply_provider_usage",
                    r.ts,
                    lane=normalise_reply_lane(event_obj.get("lane")),
                    target_id=(
                        event_obj.get("target_id")
                        if valid_string_public_post_id(event_obj.get("target_id"))
                        else ""
                    ),
                    strategy_version=bounded_event_text(
                        event_obj.get("strategy_version"),
                        default="unavailable",
                        max_characters=200,
                    ),
                    model=bounded_event_text(
                        event_obj.get("model"),
                        default="unavailable",
                        max_characters=200,
                    ),
                    provider_response_id=bounded_event_text(
                        event_obj.get("provider_response_id"), max_characters=300
                    ),
                    provider_latency_ms=bounded_event_nonnegative_integer(
                        event_obj.get("provider_latency_ms"), maximum=86_400_000
                    ),
                    request_attempt_count=request_attempt_count,
                    request_attempt_count_status=request_attempt_count_status,
                    input_tokens=bounded_event_nonnegative_integer(
                        event_obj.get("input_tokens"), maximum=100_000_000
                    ),
                    cached_input_tokens=bounded_event_nonnegative_integer(
                        event_obj.get("cached_input_tokens"), maximum=100_000_000
                    ),
                    cache_write_input_tokens=bounded_event_nonnegative_integer(
                        event_obj.get("cache_write_input_tokens"), maximum=100_000_000
                    ),
                    output_tokens=bounded_event_nonnegative_integer(
                        event_obj.get("output_tokens"), maximum=100_000_000
                    ),
                    reasoning_tokens=bounded_event_nonnegative_integer(
                        event_obj.get("reasoning_tokens"), maximum=100_000_000
                    ),
                    total_tokens=bounded_event_nonnegative_integer(
                        event_obj.get("total_tokens"), maximum=100_000_000
                    ),
                )
            elif event_obj and event_obj.get("event") == "single_call_reply_posting_outcome":
                add_event(
                    "single_call_reply_posting_outcome",
                    r.ts,
                    status=bounded_event_text(
                        event_obj.get("status"), default="unavailable", max_characters=100
                    ),
                    lane=normalise_reply_lane(event_obj.get("lane")),
                    target_id=(
                        event_obj.get("target_id")
                        if valid_string_public_post_id(event_obj.get("target_id"))
                        else ""
                    ),
                    reply_post_id=(
                        event_obj.get("reply_post_id")
                        if valid_string_public_post_id(event_obj.get("reply_post_id"))
                        else ""
                    ),
                    strategy_version=bounded_event_text(
                        event_obj.get("strategy_version"), max_characters=200
                    ),
                    reply_kind=bounded_event_text(
                        event_obj.get("reply_kind"), max_characters=100
                    ),
                    reason_code=bounded_event_text(
                        event_obj.get("reason_code"), max_characters=100
                    ),
                    used_fact_count=bounded_event_nonnegative_integer(
                        event_obj.get("used_fact_count"), maximum=32
                    ),
                    supplied_image_count=bounded_event_nonnegative_integer(
                        event_obj.get("supplied_image_count"), maximum=2
                    ),
                    model_call_count=bounded_event_nonnegative_integer(
                        event_obj.get("model_call_count"), maximum=1
                    ),
                    validated_draft_hash=bounded_event_text(
                        event_obj.get("validated_draft_hash"), max_characters=64
                    ),
                    failure_reason=bounded_event_text(
                        event_obj.get("failure_reason"), max_characters=200
                    ),
                )
            elif event_obj and event_obj.get("event") == "single_call_reply_draft_recovered":
                add_event(
                    "single_call_reply_draft_recovered",
                    r.ts,
                    lane=normalise_reply_lane(event_obj.get("lane")),
                    target_id=(
                        event_obj.get("target_id")
                        if valid_string_public_post_id(event_obj.get("target_id"))
                        else ""
                    ),
                    strategy_version=bounded_event_text(
                        event_obj.get("strategy_version"), max_characters=200
                    ),
                    model=bounded_event_text(
                        event_obj.get("model"), max_characters=200
                    ),
                    validated_draft_hash=bounded_event_text(
                        event_obj.get("validated_draft_hash"), max_characters=64
                    ),
                    model_call_count=bounded_event_nonnegative_integer(
                        event_obj.get("model_call_count"), maximum=1
                    ),
                )
            elif event_obj and event_obj.get("event") == "ai_reply_pipeline_decision":
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
                    r.ts,
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
                    r.ts,
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
                    status=bounded_event_text(event_obj.get("status"), default="operational_failure", max_characters=100),
                    lane=bounded_event_text(event_obj.get("lane"), default="unavailable", max_characters=100),
                    target_id=(event_obj.get("target_id") if valid_string_public_post_id(event_obj.get("target_id")) else ""),
                    strategy_version=bounded_event_text(event_obj.get("strategy_version"), default="unavailable", max_characters=200),
                    reason=bounded_event_text(event_obj.get("reason"), default="unknown_pipeline_failure", max_characters=1000),
                    model_call_count=bounded_event_nonnegative_integer(event_obj.get("model_call_count"), maximum=1000),
                    revision_count=bounded_event_nonnegative_integer(event_obj.get("revision_count"), maximum=1000),
                    author_quarantine_evidence=bounded_event_text(event_obj.get("author_quarantine_evidence"), max_characters=200),
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
            elif event_obj and event_obj.get("event") == "quote_pagination_repeated_token":
                add_event(
                    "quote_pagination_repeated_token",
                    r.ts,
                    post_id=(event_obj.get("post_id") if valid_string_public_post_id(event_obj.get("post_id")) else ""),
                    token_fingerprint=bounded_event_text(event_obj.get("token_fingerprint"), default="", max_characters=200),
                    pages_completed=bounded_event_nonnegative_integer(event_obj.get("pages_completed"), maximum=1_000_000),
                    results_retained=bounded_event_nonnegative_integer(event_obj.get("results_retained"), maximum=1_000_000),
                )
                stats["quote_pagination_repeated_token"] += 1
            elif event_obj and event_obj.get("event") == "candidate_skipped":
                add_event(
                    "candidate_skipped", r.ts,
                    lane=bounded_event_text(event_obj.get("lane"), default="unavailable", max_characters=100),
                    target_id=(event_obj.get("id") if valid_string_public_post_id(event_obj.get("id")) else ""),
                    reason=bounded_event_text(event_obj.get("reason"), default="other", max_characters=500),
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
                "source_refs": [record_source_ref(r, input_file_indexes)],
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
                "source_refs": [record_source_ref(r, input_file_indexes)],
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

        if "ORIGINAL_EDITORIAL_SELECTION_RESULT " in msg:
            raw = msg.split("ORIGINAL_EDITORIAL_SELECTION_RESULT ", 1)[1].strip()
            try:
                parsed = _strict_native_json_object(
                    raw.encode("utf-8"),
                    label="ORIGINAL_EDITORIAL_SELECTION_RESULT",
                )
            except Exception as exc:
                errors.append({
                    "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                    "level": r.level,
                    "message": f"Malformed ORIGINAL_EDITORIAL_SELECTION_RESULT: {exc}: {short(raw, 240)}",
                    "source_refs": [record_source_ref(r, input_file_indexes)],
                })
                stats["original_editorial_selection_parse_errors"] += 1
                continue
            parsed["time"] = r.ts.strftime("%Y-%m-%d %H:%M:%S")
            parsed["event_mode"] = "selection"
            original_editorial_shadow_events.append(parsed)
            pending_original_editorial_shadow_companions[
                original_editorial_comparison_key(parsed)
            ] += 1
            stats["original_editorial_selection_observations"] += 1
            continue

        if "ORIGINAL_EDITORIAL_SHADOW_RESULT " in msg:
            raw = msg.split("ORIGINAL_EDITORIAL_SHADOW_RESULT ", 1)[1].strip()
            try:
                parsed = _strict_native_json_object(
                    raw.encode("utf-8"),
                    label="ORIGINAL_EDITORIAL_SHADOW_RESULT",
                )
            except Exception as exc:
                errors.append({
                    "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
                    "level": r.level,
                    "message": f"Malformed ORIGINAL_EDITORIAL_SHADOW_RESULT: {exc}: {short(raw, 240)}",
                    "source_refs": [record_source_ref(r, input_file_indexes)],
                })
                stats["original_editorial_shadow_parse_errors"] += 1
                continue
            parsed["time"] = r.ts.strftime("%Y-%m-%d %H:%M:%S")
            parsed["event_mode"] = "shadow"
            companion_key = original_editorial_comparison_key(parsed)
            if pending_original_editorial_shadow_companions[companion_key] > 0:
                pending_original_editorial_shadow_companions[companion_key] -= 1
                stats["original_editorial_shadow_companion_observations"] += 1
                stats["original_editorial_shadow_observations"] += 1
                continue
            original_editorial_shadow_events.append(parsed)
            stats["original_editorial_shadow_observations"] += 1
            continue

        if "GENERATED_IDENTITY_POLICY_SHADOW_RESULT " in msg:
            record_generated_identity_shadow(
                msg, r.ts, r.level,
                observations=generated_identity_shadow_events,
                stats=stats, errors=errors,
                parse_json_object=_strict_native_json_object,
                short_text=short,
                source_ref=lambda: record_source_ref(r, input_file_indexes),
            )
            continue

        if "GENERATED_IDENTITY_POLICY_APPLIED " in msg:
            record_generated_identity_policy(
                msg, r.ts, r.level,
                observations=generated_identity_policy_events,
                stats=stats, errors=errors,
                parse_json_object=_strict_native_json_object,
                short_text=short,
                source_ref=lambda: record_source_ref(r, input_file_indexes),
            )
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
            last_created_post = {
                "time": r.ts,
                "post_id": post_id,
                "post_text": post_text,
                "canonical_post_id": response_post_id_is_canonical_string(msg),
                "production_identity": production_record,
            }
            stats["created_x_posts"] += 1
            if post_id and re.fullmatch(r"\d+", post_id):
                success_event = add_event(
                    "remote_write_succeeded",
                    r.ts,
                    post_id=post_id,
                )
                if not response_post_id_is_canonical_string(msg):
                    production_event_object_ids.discard(id(success_event))
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
                "_identity_production": production_record,
            }
            continue

        m = re.search(r"Generated reply to mention (\d+): (.*)$", msg, re.S)
        if m:
            if pending_mention.get("mention_id") != m.group(1):
                pending_mention = {
                    "mention_id": m.group(1),
                    "source": "unknown",
                    "_identity_production": production_record,
                }
            pending_mention["reply"] = lit(m.group(2))
            pending_mention["generated_at"] = r.ts.strftime("%Y-%m-%d %H:%M:%S")
            active_xai_context = None
            continue

        m = re.search(r"Recorded and cached own auto-reply id=(\d+)", msg)
        if m and pending_mention:
            pending_mention["reply_post_id"] = m.group(1)
            pending_mention["_reply_post_id_production"] = production_record
            continue

        if msg == "Reply posted successfully" and pending_mention:
            reply_id_from_nonauthoritative_response = False
            if not pending_mention.get("reply_post_id") and last_created_post.get("post_id"):
                pending_mention["reply_post_id"] = last_created_post.get("post_id")
                reply_id_from_nonauthoritative_response = not bool(
                    last_created_post.get("canonical_post_id")
                )
                pending_mention["_reply_post_id_production"] = bool(
                    last_created_post.get("production_identity")
                )
            reply_identity_is_production = bool(
                pending_mention.get("_identity_production", True)
                and pending_mention.get(
                    "_reply_post_id_production", True
                )
            )
            source = pending_mention.get("source", "mention")
            if source == "hot_post_reply":
                data = dict(pending_mention)
                data.pop("mention_id", None)
                data.pop("source", None)
                data = {
                    key: value
                    for key, value in data.items()
                    if not key.startswith("_")
                }
                posted_event = add_event(
                    "hot_post_reply_posted", r.ts, **data
                )
            else:
                data = dict(pending_mention)
                data.pop("source", None)
                data = {
                    key: value
                    for key, value in data.items()
                    if not key.startswith("_")
                }
                posted_event = add_event(
                    "mention_reply_posted", r.ts, **data
                )
            if (
                reply_id_from_nonauthoritative_response
                or not reply_identity_is_production
            ):
                production_event_object_ids.discard(id(posted_event))
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
                "_identity_production": production_record,
            }
            continue

        m = re.search(r"Generated reply to quote tweet (\d+): (.*)$", msg, re.S)
        if m:
            if pending_qt.get("quote_tweet_id") != m.group(1):
                pending_qt = {
                    "quote_tweet_id": m.group(1),
                    "_identity_production": production_record,
                }
            pending_qt["reply"] = lit(m.group(2))
            pending_qt["generated_at"] = r.ts.strftime("%Y-%m-%d %H:%M:%S")
            active_xai_context = None
            continue

        m = re.search(r"Recorded and cached own quote-tweet auto-reply id=(\d+)", msg)
        if m and pending_qt:
            pending_qt["reply_post_id"] = m.group(1)
            pending_qt["_reply_post_id_production"] = production_record
            continue

        if msg == "Quote-tweet reply posted successfully" and pending_qt:
            reply_id_from_nonauthoritative_response = False
            if not pending_qt.get("reply_post_id") and last_created_post.get("post_id"):
                pending_qt["reply_post_id"] = last_created_post.get("post_id")
                reply_id_from_nonauthoritative_response = not bool(
                    last_created_post.get("canonical_post_id")
                )
                pending_qt["_reply_post_id_production"] = bool(
                    last_created_post.get("production_identity")
                )
            reply_identity_is_production = bool(
                pending_qt.get("_identity_production", True)
                and pending_qt.get("_reply_post_id_production", True)
            )
            posted_data = {
                key: value
                for key, value in pending_qt.items()
                if not key.startswith("_")
            }
            posted_event = add_event(
                "quote_tweet_reply_posted", r.ts, **posted_data
            )
            if (
                reply_id_from_nonauthoritative_response
                or not reply_identity_is_production
            ):
                production_event_object_ids.discard(id(posted_event))
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

    if previous_record_production:
        production_pending_quote = pending_quote
        production_pending_meme = pending_meme
        production_pending_mention = pending_mention
        production_pending_qt = pending_qt
        production_pending_confirmed_reply_receipt = (
            pending_confirmed_reply_receipt
        )
        production_last_created_post = last_created_post
        production_active_xai_context = active_xai_context
        production_active_xai_call_attempt_index = (
            active_xai_call_attempt_index
        )
    pending_quote = production_pending_quote
    pending_meme = production_pending_meme
    pending_mention = production_pending_mention
    pending_qt = production_pending_qt
    pending_confirmed_reply_receipt = (
        production_pending_confirmed_reply_receipt
    )
    last_created_post = production_last_created_post
    active_xai_context = production_active_xai_context
    active_xai_call_attempt_index = production_active_xai_call_attempt_index
    current_source_record = None

    def correlated_quote_post_fields(
        post_id: str,
        *,
        legacy: Optional[Dict[str, Any]] = None,
        warning_time: str = "",
    ) -> Dict[str, Any]:
        """Resolve fixed structured evidence for one immutable post identity."""
        slots = quote_post_correlations.get(post_id) or {}
        invalid_evidence = invalid_quote_post_evidence.get(post_id) or set()
        main_event = slots.get("main_post_posted") or {}
        root_event = slots.get("account_root_posted") or {}
        confirmation = slots.get(
            "engagement_question_experimental_member_confirmed"
        ) or {}
        legacy = legacy or {}
        effective_time = str(
            warning_time
            or confirmation.get("time")
            or root_event.get("time")
            or main_event.get("time")
            or ""
        )
        conflicted_evidence = object()

        def evidence_value(event: Dict[str, Any], field: str) -> Any:
            if field in (event.get("_conflicted_fields") or set()):
                return conflicted_evidence
            return event.get(field)

        def present(value: Any) -> bool:
            return value is not None and value != ""

        def resolve(
            field: str,
            candidates: List[Tuple[str, Any]],
        ) -> Any:
            if any(value is conflicted_evidence for _, value in candidates):
                return None
            available = [
                (event_type, value)
                for event_type, value in candidates
                if present(value)
            ]
            if not available:
                return None
            first_event, first_value = available[0]
            for event_type, value in available[1:]:
                if value != first_value:
                    add_engagement_correlation_warning(
                        time_text=effective_time,
                        post_id=post_id,
                        field=field,
                        left_event=first_event,
                        right_event=event_type,
                    )
                    return None
            return first_value

        main_or_confirmation_type = (
            "main_post_posted" if main_event else
            "engagement_question_experimental_member_confirmed"
        )
        main_or_confirmation_post_id = (
            evidence_value(main_event, "post_id")
            if main_event
            else evidence_value(confirmation, "post_id")
        )
        resolved_post_id = resolve(
            "post_id",
            [
                (main_or_confirmation_type, main_or_confirmation_post_id),
                (
                    "account_root_posted",
                    evidence_value(root_event, "root_post_id"),
                ),
            ],
        )
        lane = resolve(
            "lane",
            [
                ("main_post_posted", evidence_value(main_event, "lane")),
                ("account_root_posted", evidence_value(root_event, "lane")),
            ],
        )
        quote_hash = resolve(
            "quote_hash",
            [
                (
                    "main_post_posted",
                    evidence_value(main_event, "quote_hash"),
                ),
                (
                    "account_root_posted",
                    evidence_value(root_event, "quote_id"),
                ),
            ],
        )
        if quote_hash is None and not any(
            present(value)
            for value in (
                evidence_value(main_event, "quote_hash"),
                evidence_value(root_event, "quote_id"),
            )
        ):
            quote_hash = legacy.get("quote_hash")

        public_text = evidence_value(root_event, "public_text")
        quote_text = evidence_value(root_event, "quote_text")
        experimental_evidence = bool(
            confirmation
            or any("engagement" in item for item in invalid_evidence)
            or any(
                present(evidence_value(main_event, key))
                for key in (
                    "engagement_experiment_id",
                    "engagement_experiment_plan_sha256",
                    "engagement_experiment_pair_id",
                    "engagement_experiment_arm",
                    "engagement_question_present",
                )
            )
        )
        account_root_authoritative = bool(
            root_event
            and type(root_event.get("event_version")) is int
            and root_event.get("event_version") == 1
            and root_event.get("post_id") == post_id
            and root_event.get("root_post_id") == post_id
            and root_event.get("conversation_id") == post_id
            and type(root_event.get("lane")) is str
            and root_event.get("lane") == "quote_image"
            and root_event.get("publication_authority")
            == "confirmed_transport"
        )
        account_root_usable = bool(
            account_root_authoritative
            and lane == "quote_image"
            and resolved_post_id == post_id
            and valid_bounded_utf8_text(public_text)
            and (
                quote_text is None
                or valid_bounded_utf8_text(
                    quote_text,
                    allow_empty=True,
                )
            )
        )
        public_text_sha256 = ""
        resolved_public_text_sha256: Optional[str] = None
        if account_root_usable:
            if not isinstance(quote_text, str):
                quote_text = ""
            public_text_sha256 = hashlib.sha256(
                public_text.encode("utf-8")
            ).hexdigest()
            resolved_public_text_sha256 = resolve(
                "public_text_sha256",
                [
                    (
                        "main_post_posted",
                        evidence_value(
                            main_event, "engagement_public_text_sha256"
                        ),
                    ),
                    ("account_root_posted", public_text_sha256),
                ],
            )
        else:
            public_text = ""
            quote_text = ""
        public_text_status = (
            "confirmed"
            if account_root_usable
            else "unavailable_inconsistent"
            if root_event or invalid_evidence or experimental_evidence
            else ""
        )

        engagement_experiment_id = resolve(
            "engagement_experiment_id",
            [
                (
                    "main_post_posted",
                    evidence_value(main_event, "engagement_experiment_id"),
                ),
            ],
        )
        engagement_plan_sha256 = resolve(
            "engagement_plan_sha256",
            [
                (
                    "main_post_posted",
                    evidence_value(
                        main_event, "engagement_experiment_plan_sha256"
                    ),
                ),
                (
                    "engagement_question_experimental_member_confirmed",
                    evidence_value(confirmation, "plan_sha256"),
                ),
            ],
        )
        engagement_pair_id = resolve(
            "engagement_pair_id",
            [
                (
                    "main_post_posted",
                    evidence_value(
                        main_event, "engagement_experiment_pair_id"
                    ),
                ),
                (
                    "engagement_question_experimental_member_confirmed",
                    evidence_value(confirmation, "pair_id"),
                ),
            ],
        )
        engagement_member_position = resolve(
            "engagement_member_position",
            [
                (
                    "main_post_posted",
                    evidence_value(
                        main_event, "engagement_experiment_member_position"
                    ),
                ),
                (
                    "engagement_question_experimental_member_confirmed",
                    evidence_value(confirmation, "member_position"),
                ),
            ],
        )
        engagement_arm = resolve(
            "engagement_arm",
            [
                (
                    "main_post_posted",
                    evidence_value(main_event, "engagement_experiment_arm"),
                ),
                (
                    "engagement_question_experimental_member_confirmed",
                    evidence_value(confirmation, "arm"),
                ),
            ],
        )
        engagement_publication_sequence = resolve(
            "engagement_publication_sequence",
            [
                (
                    "main_post_posted",
                    evidence_value(
                        main_event, "engagement_experiment_sequence"
                    ),
                ),
                (
                    "engagement_question_experimental_member_confirmed",
                    evidence_value(confirmation, "publication_sequence"),
                ),
            ],
        )
        engagement_publication_order = resolve(
            "engagement_publication_order",
            [
                (
                    "main_post_posted",
                    evidence_value(
                        main_event, "engagement_experiment_publication_order"
                    ),
                ),
            ],
        )
        engagement_question_present = resolve(
            "engagement_question_present",
            [
                (
                    "main_post_posted",
                    evidence_value(main_event, "engagement_question_present"),
                ),
            ],
        )
        engagement_approved_question_sha256 = resolve(
            "engagement_approved_question_sha256",
            [
                (
                    "main_post_posted",
                    evidence_value(
                        main_event,
                        "engagement_approved_question_sha256",
                    ),
                ),
            ],
        )

        engagement_question_text: Any = ""
        engagement_question_text_status = ""
        if engagement_question_present is True:
            prefix = quote_text + ENGAGEMENT_QUESTION_PUBLIC_TEXT_SEPARATOR
            candidate_question = (
                public_text[len(prefix):]
                if account_root_usable
                and bool(quote_text)
                and public_text.startswith(prefix)
                and len(public_text) > len(prefix)
                else None
            )
            candidate_question_sha256 = (
                hashlib.sha256(
                    candidate_question.encode("utf-8")
                ).hexdigest()
                if isinstance(candidate_question, str)
                else None
            )
            if (
                engagement_arm == "treatment"
                and candidate_question is not None
                and resolved_public_text_sha256 == public_text_sha256
                and engagement_approved_question_sha256
                == candidate_question_sha256
            ):
                engagement_question_text = candidate_question
                engagement_question_text_status = "validated"
            else:
                engagement_question_text = None
                engagement_question_text_status = "unavailable_inconsistent"
                add_engagement_correlation_warning(
                    time_text=effective_time,
                    post_id=post_id,
                    field="engagement_question_text",
                    left_event="main_post_posted",
                    right_event="account_root_posted",
                    status=("conflict" if root_event else "unavailable"),
                )
        elif engagement_question_present is False:
            engagement_question_text_status = "not_present"
            if account_root_usable and public_text != quote_text:
                engagement_question_text_status = "unavailable_inconsistent"
                add_engagement_correlation_warning(
                    time_text=effective_time,
                    post_id=post_id,
                    field="engagement_question_text",
                    left_event="main_post_posted",
                    right_event="account_root_posted",
                )

        selection_fields = {
            key: (
                None
                if evidence_value(main_event, key) is conflicted_evidence
                else evidence_value(main_event, key)
                if present(evidence_value(main_event, key))
                else legacy.get(key)
            )
            for key in (
                "line_no",
                "image_no",
                "image_basename",
                "image_score",
            )
        }
        post_warning_count = engagement_correlation_warning_counts[post_id]
        source_refs, source_ref_omitted = bounded_source_refs(
            main_event.get("source_refs"),
            root_event.get("source_refs"),
            confirmation.get("source_refs"),
        )
        source_ref_omitted += sum(
            omitted
            for item in (main_event, root_event, confirmation)
            for omitted in [item.get("source_ref_omitted_count")]
            if type(omitted) is int and omitted >= 0
        )
        result = {
            **selection_fields,
            "quote_hash": quote_hash,
            "engagement_experiment_id": engagement_experiment_id or "",
            "engagement_plan_sha256": engagement_plan_sha256 or "",
            "engagement_pair_id": engagement_pair_id or "",
            "engagement_member_position": engagement_member_position,
            "engagement_arm": engagement_arm or "",
            "engagement_publication_order": engagement_publication_order or "",
            "engagement_publication_sequence": engagement_publication_sequence,
            "engagement_question_present": engagement_question_present,
            "engagement_approved_question_sha256": (
                engagement_approved_question_sha256 or ""
            ),
            "engagement_public_text_sha256": (
                resolved_public_text_sha256 or ""
            ),
            "engagement_question_text": engagement_question_text,
            "engagement_question_text_status": engagement_question_text_status,
            "engagement_question_display": (
                "unavailable/inconsistent"
                if engagement_question_present is True
                and engagement_question_text_status == "unavailable_inconsistent"
                else engagement_question_text or ""
            ),
            "quote_text": quote_text,
            "public_text": public_text,
            "public_text_status": public_text_status,
            "correlation_status": (
                "inconsistent" if post_warning_count else "consistent"
            ),
            "correlation_warning_count": post_warning_count,
        }
        if source_refs:
            result["source_refs"] = source_refs
        if source_ref_omitted:
            result["source_ref_omitted_count"] = source_ref_omitted
        return result

    quote_image_events = [
        event
        for event in events
        if event.get("kind") == "quote_image_posted"
        and id(event) in production_event_object_ids
    ]
    for event in quote_image_events:
        post_id = str(event.get("post_id") or "")
        if not post_id:
            continue
        correlated = correlated_quote_post_fields(
            post_id,
            legacy=event,
            warning_time=str(event.get("time") or ""),
        )
        source_refs, source_ref_omitted = bounded_source_refs(
            event.get("source_refs"), correlated.get("source_refs")
        )
        event.update(
            {
                key: value
                for key, value in correlated.items()
                if key not in {"source_refs", "source_ref_omitted_count"}
            }
        )
        if source_refs:
            event["source_refs"] = source_refs
        total_source_ref_omitted = source_ref_omitted + int(
            correlated.get("source_ref_omitted_count") or 0
        )
        if total_source_ref_omitted:
            event["source_ref_omitted_count"] = total_source_ref_omitted
        if correlated.get("public_text_status") == "confirmed":
            # Preserve exact structured text, including real newlines, in JSON.
            event["text"] = correlated["public_text"]
        elif correlated.get("public_text_status") == "unavailable_inconsistent":
            # Do not present mutable selection text as confirmed public text.
            event["text"] = ""

    confirmed_experimental_publications: List[Dict[str, Any]] = []
    for post_id, slots in quote_post_correlations.items():
        confirmation = slots.get(
            "engagement_question_experimental_member_confirmed"
        )
        if not confirmation:
            continue
        correlated = correlated_quote_post_fields(
            post_id,
            warning_time=str(confirmation.get("time") or ""),
        )
        confirmed_experimental_publications.append(
            {
                "time": confirmation.get("time") or "",
                "post_id": post_id,
                "experiment_id": correlated.get("engagement_experiment_id") or "",
                "plan_sha256": correlated.get("engagement_plan_sha256") or "",
                "pair_id": correlated.get("engagement_pair_id") or "",
                "member_position": correlated.get("engagement_member_position"),
                "arm": correlated.get("engagement_arm") or "",
                "publication_order": (
                    correlated.get("engagement_publication_order") or ""
                ),
                "publication_sequence": correlated.get(
                    "engagement_publication_sequence"
                ),
                "question_present": correlated.get(
                    "engagement_question_present"
                ),
                "question": correlated.get("engagement_question_text"),
                "question_status": correlated.get(
                    "engagement_question_text_status"
                ),
                "quote_hash": correlated.get("quote_hash") or "",
                "quote_text": correlated.get("quote_text") or "",
                "public_text": correlated.get("public_text") or "",
                "correlation_status": correlated.get("correlation_status"),
                **(
                    {"source_refs": correlated["source_refs"]}
                    if correlated.get("source_refs")
                    else {}
                ),
                **(
                    {
                        "source_ref_omitted_count": correlated[
                            "source_ref_omitted_count"
                        ]
                    }
                    if correlated.get("source_ref_omitted_count")
                    else {}
                ),
            }
        )
    confirmed_experimental_publications.sort(
        key=lambda item: (str(item.get("time") or ""), str(item.get("post_id") or ""))
    )
    engagement_trial_outcomes.sort(
        key=lambda item: (str(item.get("time") or ""), str(item.get("event") or ""))
    )
    engagement_correlation_warnings.sort(
        key=lambda item: (
            str(item.get("time") or ""),
            str(item.get("post_id") or ""),
            str(item.get("field") or ""),
        )
    )

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
    media_upload_incidents, media_suppressed_fingerprints = (
        correlate_media_upload_incidents(
            records,
            max_text,
            input_file_indexes,
        )
    )
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
                **(
                    {"source_refs": list(event.get("source_refs") or [])}
                    if event.get("source_refs")
                    else {}
                ),
                **(
                    {
                        "source_ref_omitted_count": int(
                            event["source_ref_omitted_count"]
                        )
                    }
                    if event.get("source_ref_omitted_count")
                    else {}
                ),
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
        if item.get("source_class") == "selftest":
            continue
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
                    "source_refs": list(source.get("source_refs") or []),
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
                "source_refs": list(source.get("source_refs") or []),
            }
        )

    error_health = summarise_operational_error_health(
        errors,
        events,
        receipt_events,
        lifecycle,
        remote_write_transactions=remote_write_transactions,
        handled_api_restrictions=handled_api_restrictions,
        confirmed_reply_receipt_events=confirmed_reply_receipts,
        current_remote_write_safety=current_remote_write_safety,
        generation_time=generation_time,
        selected_window_end=selected_window_end,
        current_snapshot_authoritative=current_snapshot_authoritative,
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
    status_unavailable_reply_receipts: List[Dict[str, Any]] = []
    for incident in error_health.get("resolution_unavailable_incidents") or []:
        receipt_evidence = list(
            incident.get("correlated_reply_receipt_events") or []
        )
        if (
            not receipt_evidence
            and incident.get("target_id")
            and incident.get("lane")
        ):
            receipt_evidence = [
                {
                    "lane": str(item.get("lane") or ""),
                    "target_id": str(item.get("target_id") or ""),
                    "source_time": str(item.get("time") or ""),
                }
                for item in confirmed_reply_receipts
                if item.get("kind") == "sending"
                and str(item.get("target_id") or "")
                == str(incident.get("target_id") or "")
                and _normalise_lane(item.get("lane"))
                == _normalise_lane(incident.get("lane"))
            ]
        for receipt_event in receipt_evidence:
            status_unavailable_reply_receipts.append(
                {
                    "lane": str(receipt_event.get("lane") or ""),
                    "target_id": str(receipt_event.get("target_id") or ""),
                    "source_time": str(receipt_event.get("source_time") or ""),
                    "reason": incident.get("resolution_reason"),
                }
            )
    active_snapshot_reply_receipts: List[Dict[str, Any]] = []
    for component in (
        (current_remote_write_safety or {}).get(
            "active_transaction_identities", []
        )
        or []
    ):
        if "conversational_confirmed_reply" not in (
            component.get("receipt_roles") or []
        ):
            continue
        lanes = [
            str(lane)
            for lane in component.get("lanes") or []
            if str(lane) != "conversational_reply"
        ] or [str(lane) for lane in component.get("lanes") or []]
        active_snapshot_reply_receipts.append(
            {
                "transaction_ids": component.get("transaction_ids") or [],
                "target_ids": component.get("target_ids") or [],
                "lane": lanes[0] if len(lanes) == 1 else ", ".join(lanes),
                "artifact_names": component.get("artifact_names") or [],
                "receipt_role": "conversational_confirmed_reply",
                "receipt_role_label": REMOTE_WRITE_RECEIPT_ROLE_LABELS[
                    "conversational_confirmed_reply"
                ],
                "selected_window_relationship": component.get(
                    "selected_window_relationship"
                ),
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
    unavailable_incidents = int(
        error_health.get("resolution_unavailable_incident_count", 0)
    )
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
    elif unavailable_incidents:
        headline.append("current health: no active incident established")
    else:
        headline.append("current health: no unresolved operational incidents")
    if unavailable_incidents:
        headline.append(
            plural_count(
                unavailable_incidents,
                "incident with current status unavailable from retained evidence",
                "incidents with current status unavailable from retained evidence",
            )
        )
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
    openai_cooldown_until_epoch = int_or_none(latest_state_summary.get("openai_api_cooldown_until_epoch"))
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
            cooldown_headline(openai_cooldown_until_epoch, label="OpenAI"),
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
    observed_tweet_transport_by_id: Dict[str, Dict[str, Any]] = {}
    observed_tweet_transport_lanes_by_id: Dict[str, set[str]] = {}
    for item in remote_write_transactions:
        if (
            item.get("kind") == "tweet_transport"
            and item.get("phase") == "request_started"
            and re.fullmatch(
                r"[0-9a-f]{64}", str(item.get("transaction_id") or "")
            )
        ):
            transaction_id = str(item["transaction_id"])
            observed_tweet_transport_by_id.setdefault(transaction_id, item)
            lane = bounded_event_text(
                item.get("lane"), default="unavailable", max_characters=100
            )
            observed_tweet_transport_lanes_by_id.setdefault(
                transaction_id, set()
            ).add(lane or "unavailable")
    observed_tweet_transport_lane_conflict_ids = sorted(
        transaction_id
        for transaction_id, lanes in observed_tweet_transport_lanes_by_id.items()
        if len(lanes) > 1
    )
    observed_tweet_transport_lane_counts = Counter(
        (
            next(iter(lanes))
            if len(lanes) == 1
            else "conflicted"
        )
        for lanes in observed_tweet_transport_lanes_by_id.values()
    )
    observed_tweet_transport_main_post_lanes = (
        "quote_image",
        "daily_meme",
    )
    observed_tweet_transport_reply_lanes = (
        "conversational_reply",
        "historical_context_reply",
        "mention",
        "hot_post_reply",
        "quote_tweet",
    )
    observed_tweet_transport_main_post_request_count = sum(
        observed_tweet_transport_lane_counts[lane]
        for lane in observed_tweet_transport_main_post_lanes
    )
    observed_tweet_transport_reply_request_count = sum(
        observed_tweet_transport_lane_counts[lane]
        for lane in observed_tweet_transport_reply_lanes
    )
    observed_tweet_transport_unclassified_request_count = (
        len(observed_tweet_transport_by_id)
        - observed_tweet_transport_main_post_request_count
        - observed_tweet_transport_reply_request_count
    )
    observed_media_upload_request_count = sum(
        item.get("kind") == "media_upload"
        and item.get("phase") == "request_started"
        for item in remote_write_transactions
    )
    observed_media_upload_successes = {
        (str(item.get("attempt_id")), str(item.get("media_id")))
        for item in remote_write_transactions
        if item.get("kind") == "media_upload"
        and item.get("phase") == "confirmed_handoff"
        and SHA256_LOWER_RE.fullmatch(str(item.get("attempt_id") or ""))
        and valid_string_public_post_id(item.get("media_id"))
    }
    observed_success_post_ids = {
        str(event.get("post_id") or event.get("reply_post_id") or "")
        for event in events
        if id(event) in production_event_object_ids
        and event.get("kind")
        in {
            "remote_write_succeeded",
            "quote_image_posted",
            "daily_meme_posted",
            "mention_reply_posted",
            "hot_post_reply_posted",
            "quote_tweet_reply_posted",
        }
        and valid_string_public_post_id(
            event.get("post_id") or event.get("reply_post_id")
        )
    }
    for declared_post_id, slots in quote_post_correlations.items():
        if not valid_string_public_post_id(declared_post_id):
            continue
        main_confirmation = slots.get("main_post_posted") or {}
        if (
            main_confirmation.get("post_id") == declared_post_id
            and type(main_confirmation.get("lane")) is str
            and main_confirmation.get("lane") in ("quote_image", "daily_meme")
        ):
            observed_success_post_ids.add(declared_post_id)
        root_confirmation = slots.get("account_root_posted") or {}
        if (
            type(root_confirmation.get("event_version")) is int
            and root_confirmation.get("event_version") == 1
            and root_confirmation.get("post_id") == declared_post_id
            and root_confirmation.get("root_post_id") == declared_post_id
            and root_confirmation.get("conversation_id") == declared_post_id
            and type(root_confirmation.get("lane")) is str
            and root_confirmation.get("lane")
            in ("quote_image", "daily_meme")
            and root_confirmation.get("publication_authority")
            == "confirmed_transport"
        ):
            observed_success_post_ids.add(declared_post_id)
    observed_success_post_ids.update(
        str(confirmation.get("reply_post_id") or "")
        for item in structured_reply_confirmations
        for confirmation in [_normalised_structured_reply_confirmation(item)]
        if confirmation is not None
    )
    observed_success_post_ids.update(
        item["reply_post_id"]
        for item in historical_reply_text_evidence
        if item.get("durable_only") is not True
        and item.get("authoritative") is True
        and valid_string_public_post_id(item.get("reply_post_id"))
        and isinstance(item.get("reply_text"), str)
    )
    api_counter_semantics = {
        "posting_attempt_count": {
            "retained_compatibility_field": True,
            "scope": (
                "failed or handled-restriction X post/reply API records only; "
                "not all posting attempts"
            ),
            "preferred_field": "observed_tweet_transport_request_count",
        },
        "tweet_create_request_count": {
            "retained_compatibility_field": True,
            "scope": (
                "X request-start message-pattern records parsed as POST /2/tweets; "
                "parsed regardless of log level and not complete when low-level "
                "transport messages are absent"
            ),
            "preferred_field": "observed_tweet_transport_request_count",
        },
        "media_upload_request_count": {
            "retained_compatibility_field": True,
            "scope": (
                "X request-start message-pattern records whose parsed URL path is "
                "/2/media/upload; the legacy classifier does not constrain the "
                "method; parsed regardless of log level, and incomplete when "
                "low-level transport messages are absent"
            ),
            "preferred_field": "observed_media_upload_request_count",
        },
        "observed_tweet_transport_request_count": {
            "scope": (
                "selected production log records only: unique durable tweet-transport "
                "start message patterns immediately before the request callback; "
                "self-test logs are excluded; evidence of an observed pre-request "
                "boundary, not proof that X received the request; parsed regardless "
                "of log level and incomplete when confirmation logs are absent"
            ),
            "deduplication": "unique durable transaction_id",
        },
        "observed_tweet_transport_request_counts_by_lane": {
            "scope": (
                "the same unique durable tweet-transport starts, grouped by the "
                "literal bounded lane recorded at that boundary"
            ),
            "deduplication": "unique durable transaction_id before grouping",
        },
        "observed_tweet_transport_main_post_request_count": {
            "scope": (
                "subset of observed_tweet_transport_request_count whose lane is "
                "quote_image or daily_meme"
            ),
            "classification": list(observed_tweet_transport_main_post_lanes),
        },
        "observed_tweet_transport_reply_request_count": {
            "scope": (
                "subset of observed_tweet_transport_request_count whose lane is "
                "conversational_reply, historical_context_reply, mention, "
                "hot_post_reply or quote_tweet"
            ),
            "classification": list(observed_tweet_transport_reply_lanes),
        },
        "observed_tweet_transport_unclassified_request_count": {
            "scope": (
                "subset of observed_tweet_transport_request_count whose literal "
                "lane is not in either documented main-post or reply lane set, "
                "including transaction identities observed with conflicting lanes"
            ),
            "relationship": (
                "main-post plus reply plus unclassified equals the observed "
                "tweet-transport request total"
            ),
        },
        "observed_tweet_transport_lane_conflict_count": {
            "scope": (
                "unique durable transaction_id values whose selected production "
                "request-start records disagree on the literal lane"
            ),
            "classification": (
                "conflicts remain in the request total but are classified as "
                "unclassified rather than choosing a log-order-dependent lane"
            ),
        },
        "observed_media_upload_request_count": {
            "scope": (
                "selected production log records only: receipt-bound media-upload "
                "start message patterns before X API v2 transport; self-test logs "
                "are excluded; parsed regardless of log level and incomplete when "
                "transport logs are absent"
            ),
            "deduplication": "retained physical log-record identity",
        },
        "observed_media_upload_success_count": {
            "scope": (
                "selected production log records only: durable confirmed media "
                "handoffs to a bound main-post attempt; self-test logs are excluded; "
                "not a request-start count or proof of final post publication"
            ),
            "deduplication": "unique validated attempt_id and media_id pair",
        },
        "observed_remote_write_success_count": {
            "scope": (
                "selected production log records only: unique immutable post IDs in "
                "validated confirmed remote-write evidence; self-test records, "
                "durable-only historical history and current durable receipt/state "
                "snapshots are excluded; incomplete when confirmation logs are absent"
            ),
            "deduplication": "unique post or reply post ID across lane and generic confirmations",
        },
    }
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

    context_quality = historical_context_quality_summary(events)
    single_call_quality = single_call_reply_summary(events)
    legacy_multi_stage = {
        "decision_count": sum(
            item.get("kind") == "reply_strategy_decision" for item in events
        ),
        "stage_summary_event_count": sum(
            item.get("kind") == "reply_pipeline_stage_summary" for item in events
        ),
    }
    tested_decisions = int(legacy_multi_stage["decision_count"])
    headline = [
        item for item in headline
        if not item.endswith("Grok skip") and not item.endswith("Grok skips")
    ]
    health_index = next(
        (index for index, item in enumerate(headline) if item.startswith("current health:")),
        len(headline),
    )
    single_candidates = int(
        single_call_quality.get("candidate_evaluation_count", 0) or 0
    )
    if single_candidates:
        headline.insert(
            health_index,
            f"{plural_count(single_candidates, 'single-call candidate')} evaluated; "
            f"{plural_count(single_call_quality.get('replies_posted_count', 0), 'reply', 'replies')} posted; "
            f"{plural_count(single_call_quality.get('editorial_no_reply_count', 0), 'editorial no-reply decision')}; "
            f"{plural_count(single_call_quality.get('operational_failure_count', 0), 'operational failure')}; "
            f"one-call compliance {single_call_quality.get('one_call_compliance')}",
        )
        health_index += 1
    if tested_decisions or legacy_multi_stage["stage_summary_event_count"]:
        headline.insert(
            health_index,
            f"legacy multi-stage decisions {tested_decisions}; "
            f"legacy stage summaries {legacy_multi_stage['stage_summary_event_count']}",
        )
    cooldown_claims = {
        "API cooldown occurred",
        "no API cooldown",
    }
    cooldown_claim_prefixes = (
        "X read API cooldown ",
        "X write API cooldown ",
        "OpenAI cooldown ",
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
    report = {
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
            "counter_semantics": api_counter_semantics,
            "observed_tweet_transport_request_count": len(
                observed_tweet_transport_by_id
            ),
            "observed_tweet_transport_request_counts_by_lane": dict(
                sorted(observed_tweet_transport_lane_counts.items())
            ),
            "observed_tweet_transport_main_post_request_count": (
                observed_tweet_transport_main_post_request_count
            ),
            "observed_tweet_transport_reply_request_count": (
                observed_tweet_transport_reply_request_count
            ),
            "observed_tweet_transport_unclassified_request_count": (
                observed_tweet_transport_unclassified_request_count
            ),
            "observed_tweet_transport_lane_conflict_count": len(
                observed_tweet_transport_lane_conflict_ids
            ),
            "observed_tweet_transport_lane_conflict_transaction_ids": (
                observed_tweet_transport_lane_conflict_ids[:100]
            ),
            "observed_tweet_transport_lane_conflict_transaction_id_omitted_count": max(
                0,
                len(observed_tweet_transport_lane_conflict_ids) - 100,
            ),
            "observed_media_upload_request_count": (
                observed_media_upload_request_count
            ),
            "observed_media_upload_success_count": len(
                observed_media_upload_successes
            ),
            "observed_remote_write_success_count": len(
                observed_success_post_ids
            ),
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
            "status_unavailable_receipts": status_unavailable_reply_receipts,
            "active_snapshot_receipts": active_snapshot_reply_receipts,
        },
        "engagement_question_trial": {
            "confirmed_publications": confirmed_experimental_publications,
            "outcomes": engagement_trial_outcomes,
            "correlation_warnings": engagement_correlation_warnings,
            "correlation_warning_omitted_count": (
                engagement_correlation_warning_omitted_count
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
                    "runtime_control_clear",
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
        "single_call_reply": single_call_quality,
        "legacy_multi_stage": legacy_multi_stage,
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
        "generated_image_spacing": {
            "latest": latest_generated_image_spacing,
            "events": generated_image_spacing_events,
        },
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
            "pending_mention": (
                {
                    key: value
                    for key, value in pending_mention.items()
                    if not key.startswith("_")
                }
                if active_xai_context
                else None
            ),
            "pending_qt": (
                {
                    key: value
                    for key, value in pending_qt.items()
                    if not key.startswith("_")
                }
                if active_xai_context
                else None
            ),
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
    enrich_published_reply_text(
        report,
        runtime_state=current_runtime_state,
        structured_reply_confirmations=structured_reply_confirmations,
        historical_reply_text_evidence=historical_reply_text_evidence,
        confirmed_receipt_evidence=confirmed_receipt_evidence,
        durable_evidence_status=durable_reply_evidence_status,
        production_event_object_ids=production_event_object_ids,
    )
    return report


CURRENT_COOLDOWN_FIELDS = (
    ("api_cooldown_until_epoch", "X read API"),
    ("x_write_api_cooldown_until_epoch", "X write API"),
    ("openai_api_cooldown_until_epoch", "OpenAI"),
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
    unavailable_incidents = int(
        (report.get("error_health") or {}).get(
            "resolution_unavailable_incident_count", 0
        )
        or 0
    )
    health_claim = (
        "current health: "
        + plural_count(current_incidents, "unresolved operational incident")
        if current_incidents
        else "current health: no active incident established"
        if unavailable_incidents
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
    for prefix in ("api_cooldown", "x_write_api_cooldown", "openai_api_cooldown", "quote_api_cooldown"):
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



def render_markdown(report: Dict[str, Any]) -> str:
    """Render digest metrics as deterministic Markdown."""
    receipt_events = (report.get("main_post_recovery") or {}).get("receipt_events") or []
    return _render_digest_markdown(
        report,
        main_post_receipt_lifecycle=(
            summarise_main_post_receipt_lifecycle(receipt_events)
            if receipt_events else {}
        ),
    )


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
    input_file_indexes = {
        str(path): index for index, path in enumerate(logs)
    }
    runtime_state, runtime_state_path, runtime_state_ts, runtime_state_status = (
        load_current_runtime_state(project_dir)
    )
    runtime_state_observed_at = datetime.now()
    runtime_config, runtime_config_path, runtime_config_ts, runtime_config_status = (
        load_current_runtime_config(project_dir)
    )
    confirmed_receipt_evidence: List[Dict[str, Any]] = []
    historical_history_evidence: List[Dict[str, Any]] = []
    durable_reply_evidence_status: Dict[str, Any] = {}
    if args.json or args.json_output is not None:
        confirmed_receipt_evidence, confirmed_receipt_status = (
            load_confirmed_reply_receipt_evidence(project_dir)
        )
        historical_history_evidence, historical_history_status = (
            load_historical_reply_history_evidence(project_dir)
        )
        durable_reply_evidence_status = {
            "confirmed_reply_receipt": confirmed_receipt_status,
            "historical_context_reply_history": historical_history_status,
        }
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
            "observed_at": dt_text(generation_time),
            "status": "inspection_failed",
            "blocking": None,
            "reason": f"{type(exc).__name__}: {exc}",
        }
    report_window_end = until or max(
        (record.ts for record in records),
        default=None,
    )
    report = analyse(
        records,
        max_text=args.max_text,
        initial_active_xai_context=initial_active_xai_context,
        initial_active_xai_call_attempt=initial_active_xai_call_attempt,
        initial_pending_mention=initial_pending_mention,
        initial_pending_qt=initial_pending_qt,
        current_remote_write_safety=current_remote_write_safety,
        generation_time=generation_time,
        selected_window_end=report_window_end,
        current_snapshot_authoritative=until is None,
        current_runtime_state=runtime_state,
        input_file_indexes=input_file_indexes,
        confirmed_receipt_evidence=confirmed_receipt_evidence,
        historical_history_evidence=historical_history_evidence,
        durable_reply_evidence_status=durable_reply_evidence_status,
    )
    report["generation_time"] = dt_text(generation_time)
    report["generation_epoch"] = int(generation_time.timestamp())
    report["remote_write_safety"] = current_remote_write_safety

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

    report["runtime_state_status"] = {
        "status": runtime_state_status,
        "path": str(runtime_state_path),
        "observed_at": dt_text(runtime_state_observed_at),
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

    report["runtime_config_status"] = {
        "status": runtime_config_status,
        "path": str(runtime_config_path),
        "time": dt_text(runtime_config_ts) if runtime_config_ts else None,
    }
    report["latest_config"] = runtime_config or {}
    strike_progress = current_author_no_reply_strike_progress(
        runtime_state,
        runtime_state_status,
        runtime_config,
        runtime_config_status,
        generation_time,
        state_observed_at=runtime_state_observed_at,
    )
    report.setdefault("mention_backlog_and_quarantine", {})[
        "current_author_no_reply_strike_progress"
    ] = strike_progress
    if (
        strike_progress.get("available") is True
        and strike_progress.get("omitted_author_count") == 0
        and report.get("latest_state")
    ):
        report["latest_state"][
            "current_subthreshold_author_no_reply_strike_author_count"
        ] = sum(
            0 < item.get("strike_count", 0) < strike_progress["threshold"]
            and item.get("quarantine_active") is False
            for item in strike_progress.get("authors") or []
        )
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
        provider_usage={},
    )
    single_call_cost = (report.get("openai_published_cost") or {}).get(
        "selected_window"
    ) or {}
    report.setdefault("single_call_reply", {})["cost_total"] = {
        "status": str(single_call_cost.get("status") or "unavailable"),
        "amount": single_call_cost.get("amount"),
        "scope": (report.get("openai_published_cost") or {}).get("scope"),
        "method": single_call_cost.get("method"),
    }
    json_rendered: Optional[str] = None
    if args.json or args.json_output is not None:
        json_report = dict(report)
        json_report["digest_contract"] = build_digest_contract()
        json_rendered = json.dumps(
            json_report,
            indent=2,
            ensure_ascii=False,
            allow_nan=False,
        ) + "\n"

    if args.json:
        assert json_rendered is not None
        rendered = json_rendered
    else:
        rendered = render_markdown(report) + "\n"
        if not records:
            rendered += "\n<!-- no matching records; resume state not advanced -->\n"

    deliver_report(rendered, args.output)
    if args.markdown_output is not None:
        deliver_report(render_markdown(report) + "\n", args.markdown_output)
    if args.json_output is not None:
        assert json_rendered is not None
        deliver_report(json_rendered, args.json_output)

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
