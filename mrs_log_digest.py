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

Logs and the default .mrs_log_digest_state.json cursor are discovered through
--project-dir (the script directory by default). Explicit relative output paths
resolve against the current working directory.

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
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple, Union
from zoneinfo import ZoneInfo

# Explicit imports preserve the existing digest helper import surface.
from mrs_log_digest_context import (
    INTERNAL_CONTEXT_KEYS,
    read_resume_data as _read_resume_data,
    save_resume_time as _save_resume_time,
    state_context_is_within_window as _state_context_is_within_window,
    strip_internal_context_markers as _strip_internal_context_markers,
    merge_context as _merge_context,
    extract_config_pairs,
    merge_context_from_log_backscan as _merge_context_from_log_backscan,
    find_latest_config_before as _find_latest_config_before,
    parse_partial_state_from_msg,
    apply_saved_context as _apply_saved_context,
)
from mrs_log_digest_input_io import (
    file_sha256,
    _stable_file_identity,
    read_stable_regular_snapshot as _read_stable_regular_snapshot,
    read_stable_regular_bytes as _read_stable_regular_bytes,
    read_stable_private_json_bytes as _read_stable_private_json_bytes,
    canonical_atomic_json_bytes,
    canonical_private_json_bytes,
    _strict_json_object,
    _strict_native_json_value,
    _strict_native_json_object as _strict_native_json_object_impl,
)
from mrs_log_digest_records import (
    LOG_RE,
    SOURCE_REFERENCE_LIMIT,
    RESUME_FINGERPRINT_TAIL_LIMIT,
    SAFE_SOURCE_LOGGER_RE,
    Record,
    safe_source_logger as _safe_source_logger,
    record_source_ref as _record_source_ref,
    bounded_source_refs,
    record_fingerprint as _record_fingerprint,
    resume_fingerprint_tail as _resume_fingerprint_tail,
    locate_resume_fingerprint_tail as _locate_resume_fingerprint_tail,
    resume_boundary_fingerprint_counts,
    filter_resume_boundary_records as _filter_resume_boundary_records,
    iter_records as _iter_records,
    read_records as _read_records,
    filter_records_by_time,
    summarize_input_files as _summarize_input_files,
    input_retention_coverage as _input_retention_coverage,
    combine_input_warnings,
)
from mrs_log_digest_transactions import (
    parse_x_request_start as _parse_x_request_start,
    classify_x_request_endpoint,
    parse_remote_write_transaction_event as _parse_remote_write_transaction_event,
    summarise_main_post_receipt_lifecycle,
    summarise_reply_receipt_lifecycle,
    is_media_v2_request_failure,
    is_media_fallback_warning,
    is_media_v1_success,
    is_media_v1_failure,
    is_main_post_success,
    find_recent_media_path,
    correlate_media_upload_incidents as _correlate_media_upload_incidents,
    add_receipt_event as _add_receipt_event,
    add_confirmed_reply_receipt_event as _add_confirmed_reply_receipt_event,
    add_reply_media_context_event as _add_reply_media_context_event,
    record_x_request_start,
    record_remote_write_transaction,
    handle_legacy_receipt_message,
    handle_legacy_reply_media_context_message,
    prepare_media_incidents_and_errors,
    append_unresolved_reply_receipt_errors,
    prepare_reply_receipt_recovery_reporting,
)
from mrs_log_digest_legacy_posts import (
    handle_legacy_quiet_message,
    handle_legacy_quote_image_selection,
    handle_legacy_generated_image_spacing,
    handle_legacy_quote_image_posting,
    handle_legacy_meme_posting,
    handle_legacy_created_post,
    handle_legacy_mention_reply,
    handle_legacy_quote_reply,
    try_parse_response_id_text,
    response_post_id_is_canonical_string as _response_post_id_is_canonical_string,
)
from mrs_log_digest_api_health import (
    is_reply_target_eligibility_restriction,
    is_deleted_or_inaccessible_tweet_403,
    handle_cooldown_message,
    handle_x_api_error,
    enrich_latest_api_error,
    prepare_api_health,
    api_health_report,
)
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
    bounded_event_nonnegative_integer_observation,
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
    normalise_reply_lane,
    _human_snapshot_age,
    _terminal_local_rejection_outcome,
    _is_terminal_pipeline_failure,
    _is_writer_local_failure,
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
from mrs_log_digest_provider_costs import (
    ROUND_HALF_UP,
    USD_TICKS_PER_DOLLAR,
    USD_DISPLAY_QUANTUM,
    int_usage_value,
    optional_int_usage_value,
    format_usd_ticks,
    format_reported_cost,
    _cache_metric_coverage,
    _format_cache_metric_coverage_line,
    xai_usage_totals,
    xai_reply_cost_summary,
)
from mrs_log_digest_provider_observations import (
    xai_usage_stage_from_msg,
    provider_usage_provider_from_msg,
    parse_xai_call_start,
    parse_xai_usage_from_msg,
    xai_usage_context_from_pending,
    unknown_xai_usage_context,
    normalise_active_xai_call_attempt as _normalise_active_xai_call_attempt,
    _cache_input_metric as _provider_cache_input_metric,
    summarize_xai_usage_event as _summarize_xai_usage_event,
    observe_provider_message,
    observe_provider_error,
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
    shadow_lifecycle_snapshot,
)
from mrs_log_digest_state_reporting import (
    AUTHOR_NO_REPLY_PROGRESS_MAX_AUTHORS,
    AUTHOR_NO_REPLY_PROGRESS_MAX_THRESHOLD,
    AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY,
    AUTHOR_EVALUATION_QUARANTINE_PREVIOUS_EVIDENCE_POLICY,
    AUTHOR_EVALUATION_QUARANTINE_SINGLE_SOL_V1_EVIDENCE_POLICY,
    AUTHOR_EVALUATION_QUARANTINE_SEEDED_EVIDENCE_POLICY,
    AUTHOR_EVALUATION_QUARANTINE_LEGACY_EVIDENCE_POLICY,
    UNKNOWN_INVALID_STATE_FIELD,
    CURRENT_COOLDOWN_FIELDS,
    epoch_to_human as _epoch_to_human,
    epoch_to_london_text as _epoch_to_london_text,
    state_list_count as _state_list_count,
    state_list_tail,
    state_list_head,
    summarize_engagement_question_experiment_state as _summarize_engagement_question_experiment_state,
    summarize_latest_state as _summarize_latest_state,
    current_author_no_reply_strike_progress as _current_author_no_reply_strike_progress,
    refresh_current_health_headline as _refresh_current_health_headline,
    refresh_derived as _refresh_derived,
    record_mention_backlog,
    record_author_evaluation_quarantine,
    prepare_mention_control_observations,
    prepare_headline_and_derived,
    prepare_reply_quality_headline,
)
from mrs_log_digest_reply_evidence import (
    CONFIRMED_REPLY_RECEIPT_MAX_BYTES,
    HISTORICAL_REPLY_HISTORY_MAX_BYTES,
    MIN_CONFIRMED_PUBLICATION_EPOCH,
    MAX_CONFIRMED_PUBLICATION_EPOCH,
    valid_conversational_public_reply_text,
    _structured_value_sha256,
    _valid_historical_formatter_metadata,
    _valid_durable_ai_reply_draft as _valid_durable_ai_reply_draft_impl,
    _confirmed_conversational_receipt_evidence as _confirmed_conversational_receipt_evidence_impl,
    load_confirmed_reply_receipt_evidence as _load_confirmed_reply_receipt_evidence,
    _valid_canonical_utc_timestamp as _valid_canonical_utc_timestamp_impl,
    _valid_historical_completed_item as _valid_historical_completed_item_impl,
    _valid_historical_failed_item as _valid_historical_failed_item_impl,
    load_historical_reply_history_evidence as _load_historical_reply_history_evidence,
    prepare_structured_reply_confirmation,
    prepare_structured_historical_publication_evidence,
    valid_structured_historical_completion_anchor,
)
from mrs_log_digest_quote_publication import (
    ENGAGEMENT_QUESTION_PUBLIC_TEXT_SEPARATOR,
    ENGAGEMENT_QUESTION_EXPERIMENT_ID,
    ENGAGEMENT_QUESTION_EXPERIMENT_STATE_SCHEMA_VERSION,
    ENGAGEMENT_QUESTION_EXPERIMENT_STATUSES,
    ENGAGEMENT_PAIR_ID_RE,
    ENGAGEMENT_PUBLICATION_ORDERS,
    ENGAGEMENT_ARMS,
    ENGAGEMENT_MAX_CONFIRMED_PUBLICATIONS,
    valid_account_root_publication_identity as _valid_account_root_publication_identity,
    valid_engagement_confirmation_event as _valid_engagement_confirmation_event,
    engagement_main_metadata_status as _engagement_main_metadata_status,
    QuotePublicationCorrelation,
    record_main_post_publication,
    record_account_root_publication,
    record_engagement_confirmation,
    record_engagement_trial_outcome,
)
from mrs_log_digest_reply_text import (
    PUBLISHED_REPLY_WARNING_LIMIT,
    _normalised_structured_reply_confirmation,
    _public_reply_text_result as _public_reply_text_result_impl,
    _durable_public_reply_text_candidates as _durable_public_reply_text_candidates_impl,
    enrich_published_reply_text as _enrich_published_reply_text,
)
from mrs_log_digest_incidents import (
    observe_error_warning,
    REMOTE_OPERATION_SCOPE_LABELS,
    REMOTE_CONTROL_SCOPE_BY_KEY,
    REMOTE_LANE_SCOPE,
    _incident_exception_line,
    _normalise_incident_text,
    classify_operational_error as _classify_operational_error,
    _event_time,
    _base_remote_control_key as _base_remote_control_key_impl,
    _remote_control_scope as _remote_control_scope_impl,
    _remote_operation_scope_for_lane as _remote_operation_scope_for_lane_impl,
    _explicit_remote_pause_scope as _explicit_remote_pause_scope_impl,
    summarise_operational_error_health as _summarise_operational_error_health,
)
from mrs_log_digest_remote_write import (
    REMOTE_WRITE_MARKER_BASENAMES,
    REMOTE_WRITE_SOURCE_RECEIPT_BASENAMES,
    REMOTE_WRITE_SOURCE_RECEIPT_ROLES,
    REMOTE_MEDIA_RECEIPT_BASENAME,
    REMOTE_MEDIA_FENCE_BASENAME,
    REMOTE_TRANSPORT_JOURNAL_BASENAME,
    REMOTE_TRANSPORT_FENCE_BASENAME,
    REMOTE_WRITE_ARCHIVE_BASENAME,
    REMOTE_WRITE_SNAPSHOT_MAX_BYTES,
    RETIREMENT_SOURCE_IDENTITY_KEYS,
    _canonical_retirement_source_identity,
    _safe_relative_project_path,
    _remote_write_document_identity,
    _group_active_remote_write_artifacts,
    _read_readonly_archive_bytes as _read_readonly_archive_bytes_impl,
    reconciliation_archive_snapshot as _reconciliation_archive_snapshot,
    remote_write_safety_snapshot as _remote_write_safety_snapshot,
    annotate_remote_write_snapshot_window as _annotate_remote_write_snapshot_window,
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
    RUNWAY_CONFIG_DEFAULTS,
    generated_post_rate_history as _generated_post_rate_history,
    load_runway_config as _load_runway_config,
)
from mrs_log_digest_consistency_events import (
    record_reply_evidence_unavailable,
    record_runtime_control_pause,
    record_runtime_control_clear,
    record_clarification_reply_cap_override,
    record_clarification_reply_used,
    record_repair_reply_completed,
    record_posting_transaction_state,
    record_daily_meme_failure,
    production_consistency_report,
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
from mrs_log_digest_original_editorial import (
    original_editorial_comparison_key,
    original_editorial_shadow_summary,
    record_original_editorial_selection,
    record_original_editorial_shadow,
)
from mrs_log_digest_generated_identity import (
    generated_identity_shadow_summary,
    generated_identity_policy_summary,
    record_generated_identity_shadow,
    record_generated_identity_policy,
)
from mrs_log_digest_single_call import (
    single_call_reply_summary,
    record_single_call_reply_decision,
    record_single_call_reply_provider_usage,
    record_single_call_reply_posting_outcome,
    record_single_call_reply_draft_recovered,
)
from mrs_log_digest_reply_pipeline import (
    record_ai_reply_pipeline_decision,
    record_ai_reply_pipeline_stage_summary,
    record_ai_reply_pipeline_effective_outcome,
    record_ai_reply_pipeline_failure,
    record_ai_reply_pipeline_outcome,
    reconcile_reply_pipeline_effective_outcomes as _reconcile_reply_pipeline_effective_outcomes,
    MAJORITY_REVIEW_FAMILIES,
    MAJORITY_REVIEW_SUMMARY_FIELDS,
    _valid_majority_review_summary,
    normalise_majority_review_telemetry,
    _majority_review_telemetry_for_event,
    _majority_review_utilisation_counts,
    majority_review_utilisation,
    reply_pipeline_stage_summary,
)
from mrs_log_digest_reply_strategy import (
    record_reply_strategy_decision,
    record_reply_strategy_outcome,
    record_reply_target_terminal,
    record_reply_strategy_rejection,
    prepare_inferred_reply_strategy_outcomes,
    add_or_merge_local_rejection as _add_or_merge_local_rejection,
    conversational_evidence_fields as _conversational_evidence_fields,
    _no_reply_category,
    reply_strategy_summary,
)
from mrs_log_digest_visual_context import parse_reply_visual_description_event
from mrs_log_digest_image_usage import (
    generated_image_utilisation,
    generated_pool_runway,
    regular_image_usage_summary,
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

# Version 3 is a major-versioned compatibility contract. Increment this integer
# before removing or renaming a JSON field, changing an established field's type
# or meaning, or otherwise making a consumer-visible incompatible change. Purely
# additive fields do not require an increment within a major version.
DIGEST_JSON_SCHEMA_VERSION = 3
DIGEST_JSON_OUTPUT_KIND = "mrs_log_digest"
DIGEST_SOURCE_MAX_BYTES = 4 * 1024 * 1024
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
OPENAI_COST_CACHE_PATH = (
    Path.home() / ".local/state/mrsMThatcher/openai-costs/daily_costs.json"
)


def read_stable_regular_snapshot(
    path: Path,
    *,
    maximum: int,
    require_private: bool = False,
) -> Tuple[bytes, os.stat_result]:
    """Read bytes and metadata from one stable no-follow file observation."""

    return _read_stable_regular_snapshot(
        path, maximum=maximum, require_private=require_private,
        stable_file_identity=_stable_file_identity,
    )


def read_stable_regular_bytes(path: Path, *, maximum: int) -> bytes:
    """Read one bounded regular file twice-bound to its no-follow pathname."""

    return _read_stable_regular_bytes(
        path, maximum=maximum, read_snapshot=read_stable_regular_snapshot,
    )


def read_stable_private_json_bytes(path: Path, *, maximum: int) -> bytes:
    """Read one stable, owned, single-link mode-0600 JSON authority."""

    return _read_stable_private_json_bytes(
        path, maximum=maximum, read_snapshot=read_stable_regular_snapshot,
    )


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


def _strict_native_json_object(data: bytes, *, label: str) -> Dict[str, Any]:
    """Parse one strict native JSON object."""

    return _strict_native_json_object_impl(
        data, label=label, parse_json_value=_strict_native_json_value,
    )


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


def runtime_control_snapshot(project_dir: Path) -> Dict[str, Any]:
    """Read current pause controls, sampling the digest clock inside validation."""
    return _runtime_control_snapshot(
        project_dir,
        read_bytes=read_stable_regular_bytes,
        parse_json_object=_strict_json_object,
        now=datetime.now,
    )


def _read_readonly_archive_bytes(project_dir: Path, value: Any) -> bytes:
    """Read a mode-0400 archive through the digest's current stable byte reader."""
    return _read_readonly_archive_bytes_impl(
        project_dir, value, read_bytes=read_stable_regular_bytes,
    )


def reconciliation_archive_snapshot(project_dir: Path) -> Dict[str, Any]:
    """Inspect archives with the digest's current reader, parser and diagnostics."""
    return _reconciliation_archive_snapshot(
        project_dir,
        read_bytes=read_stable_regular_bytes,
        parse_json_object=_strict_json_object,
        inspection_error=reconciliation_inspection_error,
        read_archive_bytes=_read_readonly_archive_bytes,
    )


def remote_write_safety_snapshot(project_dir: Path) -> Dict[str, Any]:
    """Inspect barriers with the digest's clock, readers and snapshot callbacks."""
    return _remote_write_safety_snapshot(
        project_dir,
        read_bytes=read_stable_regular_bytes,
        parse_json_object=_strict_json_object,
        now=datetime.now,
        control_snapshot=runtime_control_snapshot,
        archive_snapshot=reconciliation_archive_snapshot,
    )


def annotate_remote_write_snapshot_window(
    safety: Dict[str, Any],
    selected_window_end: Optional[datetime],
    *,
    current_snapshot_authoritative: bool = False,
) -> None:
    """Describe, without backdating, how current artefacts relate to a log window."""
    _annotate_remote_write_snapshot_window(
        safety, selected_window_end,
        current_snapshot_authoritative=current_snapshot_authoritative,
        strptime=datetime.strptime,
        fromtimestamp=datetime.fromtimestamp,
    )


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
    return _generated_post_rate_history(
        logs, now, days,
        read_records=read_records,
        try_parse_strict_json_object_from_msg=try_parse_strict_json_object_from_msg,
        GENERATED_BASENAME_RE=GENERATED_BASENAME_RE,
        clock_now=datetime.now,
    )


def load_runway_config(project_dir: Path, observed_config: Dict[str, Any]) -> Dict[str, Any]:
    """Resolve standalone runway inputs without importing production code."""
    return _load_runway_config(
        project_dir, observed_config,
        RUNWAY_CONFIG_DEFAULTS=RUNWAY_CONFIG_DEFAULTS,
        parse_json_object=_strict_native_json_object,
    )


def read_resume_data(state_file: Path) -> Dict[str, Any]:
    """Read the digest resume file.

    The timestamp is used for auto-resume. Newer versions also keep the last
    observed bot state/config so short quiet windows can still show budget and
    priority context.
    """
    return _read_resume_data(
        state_file,
        parse_json_object=_strict_native_json_object,
        read_bytes=read_stable_regular_bytes,
        diagnostic=lambda message: print(message, file=sys.stderr),
    )


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
    _save_resume_time(
        state_file,
        last_ts,
        records,
        report,
        logs,
        preserve_existing_context=preserve_existing_context,
        merge_existing_boundary_occurrences=merge_existing_boundary_occurrences,
        cursor_fingerprint_tail=cursor_fingerprint_tail,
        read_resume_data=read_resume_data,
        strip_internal_context_markers=strip_internal_context_markers,
        record_fingerprint=record_fingerprint,
        parse_dt=parse_dt,
        resume_boundary_fingerprint_counts=resume_boundary_fingerprint_counts,
        resume_fingerprint_tail=resume_fingerprint_tail,
        dt_text=dt_text,
        Counter=Counter,
        clock_now=lambda: datetime.now(),
        RESUME_FINGERPRINT_TAIL_LIMIT=RESUME_FINGERPRINT_TAIL_LIMIT,
    )


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
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise RuntimeError(f"Digest lock is not a regular file: {path}")
        handle = os.fdopen(fd, "r+", encoding="utf-8")
    except BaseException:
        os.close(fd)
        raise
    try:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            handle.seek(0)
            owner = handle.read(4096).strip() or "owner unavailable"
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
    return _epoch_to_human(
        value,
        fromtimestamp=datetime.fromtimestamp,
    )


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
    return _safe_source_logger(value, safe_source_logger_re=SAFE_SOURCE_LOGGER_RE)


def record_source_ref(
    record: Record,
    input_file_indexes: Optional[Dict[str, int]] = None,
) -> Dict[str, Any]:
    """Return bounded location metadata for one retained physical log record."""
    return _record_source_ref(
        record, input_file_indexes, dt_text=dt_text, safe_source_logger=safe_source_logger,
    )


def record_fingerprint(record: Record) -> str:
    """Record fingerprint."""
    return _record_fingerprint(record, dt_text=dt_text)


def resume_fingerprint_tail(data: Dict[str, Any]) -> List[str]:
    """Return the resume fingerprint tail."""
    return _resume_fingerprint_tail(data, tail_limit=RESUME_FINGERPRINT_TAIL_LIMIT)


def locate_resume_fingerprint_tail(records: List[Record], tail: List[str]) -> Optional[Tuple[int, int]]:
    """Locate the saved append-order tail, tolerating bounded rotation loss."""
    return _locate_resume_fingerprint_tail(records, tail, record_fingerprint=record_fingerprint)


def filter_resume_boundary_records(
    records: List[Record],
    boundary: datetime,
    processed_counts: Counter[str],
) -> List[Record]:
    """Filter resume boundary records."""
    return _filter_resume_boundary_records(
        records, boundary, processed_counts, record_fingerprint=record_fingerprint,
    )


def iter_records(path: Path) -> Iterable[Record]:
    """Yield iter records values."""
    yield from _iter_records(
        path, log_re=LOG_RE, record_type=Record, strptime=datetime.strptime,
    )


def read_records(
    paths: List[Path],
    since: Optional[datetime],
    until: Optional[datetime],
    *,
    since_exclusive: bool = False,
    physical_order: bool = False,
) -> List[Record]:
    """Read and deduplicate structured and legacy log records."""
    return _read_records(
        paths, since, until, since_exclusive=since_exclusive,
        physical_order=physical_order, iter_records=iter_records,
    )


def summarize_input_files(
    paths: List[Path],
    since: Optional[datetime],
    until: Optional[datetime],
    *,
    since_exclusive: bool = False,
) -> List[Dict[str, Any]]:
    """Summarise input files."""
    return _summarize_input_files(
        paths, since, until, since_exclusive=since_exclusive,
        iter_records=iter_records, fromtimestamp=datetime.fromtimestamp, dt_text=dt_text,
    )


def input_retention_coverage(
    input_files: List[Dict[str, Any]],
    since: Optional[datetime],
) -> Dict[str, Any]:
    """Describe whether retained records cover the requested lower boundary."""
    return _input_retention_coverage(
        input_files, since, parse_dt=parse_dt, dt_text=dt_text,
    )


def lit(value: str) -> str:
    """Parse a Python repr string when possible, otherwise return raw."""
    value = value.strip()
    try:
        return ast.literal_eval(value)
    except Exception:
        return value.strip("'\"")


def valid_account_root_publication_identity(event: Any) -> bool:
    """Return whether an account-root event has the producer's core contract."""

    return _valid_account_root_publication_identity(
        event,
        valid_string_public_post_id=valid_string_public_post_id,
    )


def valid_engagement_confirmation_event(event: Any) -> bool:
    """Return whether a trial confirmation matches its producer schema."""

    return _valid_engagement_confirmation_event(
        event,
        valid_string_public_post_id=valid_string_public_post_id,
        SHA256_LOWER_RE=SHA256_LOWER_RE,
        ENGAGEMENT_PAIR_ID_RE=ENGAGEMENT_PAIR_ID_RE,
        ENGAGEMENT_ARMS=ENGAGEMENT_ARMS,
        ENGAGEMENT_MAX_CONFIRMED_PUBLICATIONS=ENGAGEMENT_MAX_CONFIRMED_PUBLICATIONS,
    )


def engagement_main_metadata_status(event: Any) -> str:
    """Return absent, valid, or invalid for main-post experiment metadata."""

    return _engagement_main_metadata_status(
        event,
        SHA256_LOWER_RE=SHA256_LOWER_RE,
        ENGAGEMENT_QUESTION_EXPERIMENT_ID=ENGAGEMENT_QUESTION_EXPERIMENT_ID,
        ENGAGEMENT_PAIR_ID_RE=ENGAGEMENT_PAIR_ID_RE,
        ENGAGEMENT_ARMS=ENGAGEMENT_ARMS,
        ENGAGEMENT_PUBLICATION_ORDERS=ENGAGEMENT_PUBLICATION_ORDERS,
        ENGAGEMENT_MAX_CONFIRMED_PUBLICATIONS=ENGAGEMENT_MAX_CONFIRMED_PUBLICATIONS,
    )


def state_list_count(state: Dict[str, Any], key: str) -> Any:
    """Return the state list count."""
    return _state_list_count(
        state, key,
        UNKNOWN_MISSING_STATE_FIELD=UNKNOWN_MISSING_STATE_FIELD,
        UNKNOWN_INVALID_STATE_FIELD=UNKNOWN_INVALID_STATE_FIELD,
    )


def summarize_engagement_question_experiment_state(value: Any) -> Optional[Dict[str, Any]]:
    """Return a bounded summary of protected engagement-question state."""
    return _summarize_engagement_question_experiment_state(
        value,
        ENGAGEMENT_QUESTION_EXPERIMENT_STATE_SCHEMA_VERSION=ENGAGEMENT_QUESTION_EXPERIMENT_STATE_SCHEMA_VERSION,
        ENGAGEMENT_QUESTION_EXPERIMENT_ID=ENGAGEMENT_QUESTION_EXPERIMENT_ID,
        ENGAGEMENT_QUESTION_EXPERIMENT_STATUSES=ENGAGEMENT_QUESTION_EXPERIMENT_STATUSES,
        UNKNOWN_MISSING_STATE_FIELD=UNKNOWN_MISSING_STATE_FIELD,
        UNKNOWN_INVALID_STATE_FIELD=UNKNOWN_INVALID_STATE_FIELD,
    )


def summarize_latest_state(
    latest_state: Dict[str, Any],
    latest_state_ts: Optional[datetime],
    *,
    source: str = "log snapshot",
    source_path: Optional[Path] = None,
) -> Dict[str, Any]:
    """Summarise latest state."""
    return _summarize_latest_state(
        latest_state, latest_state_ts,
        source=source,
        source_path=source_path,
        clock_now=datetime.now,
        epoch_to_human=epoch_to_human,
        state_list_count=state_list_count,
        state_list_tail=state_list_tail,
        state_list_head=state_list_head,
        summarize_engagement_question_experiment_state=summarize_engagement_question_experiment_state,
        UNKNOWN_INVALID_STATE_FIELD=UNKNOWN_INVALID_STATE_FIELD,
    )


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
    return _epoch_to_london_text(
        value,
        fromtimestamp=datetime.fromtimestamp,
        london_timezone=LONDON,
    )


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
    return _current_author_no_reply_strike_progress(
        runtime_state, runtime_state_status, runtime_config, runtime_config_status, generation_time,
        state_observed_at=state_observed_at,
        epoch_to_london_text=epoch_to_london_text,
        valid_public_post_id=valid_public_post_id,
        MAX_REASONABLE_STATE_EPOCH=MAX_REASONABLE_STATE_EPOCH,
        AUTHOR_NO_REPLY_PROGRESS_MAX_AUTHORS=AUTHOR_NO_REPLY_PROGRESS_MAX_AUTHORS,
        AUTHOR_NO_REPLY_PROGRESS_MAX_THRESHOLD=AUTHOR_NO_REPLY_PROGRESS_MAX_THRESHOLD,
        AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY=AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY,
        AUTHOR_EVALUATION_QUARANTINE_PREVIOUS_EVIDENCE_POLICY=AUTHOR_EVALUATION_QUARANTINE_PREVIOUS_EVIDENCE_POLICY,
        AUTHOR_EVALUATION_QUARANTINE_SINGLE_SOL_V1_EVIDENCE_POLICY=AUTHOR_EVALUATION_QUARANTINE_SINGLE_SOL_V1_EVIDENCE_POLICY,
        AUTHOR_EVALUATION_QUARANTINE_SEEDED_EVIDENCE_POLICY=AUTHOR_EVALUATION_QUARANTINE_SEEDED_EVIDENCE_POLICY,
        AUTHOR_EVALUATION_QUARANTINE_LEGACY_EVIDENCE_POLICY=AUTHOR_EVALUATION_QUARANTINE_LEGACY_EVIDENCE_POLICY,
    )


def state_context_is_within_window(state: Dict[str, Any], window_end: Optional[datetime]) -> bool:
    """Return whether state context is within window."""
    return _state_context_is_within_window(
        state,
        window_end,
        parse_dt=parse_dt,
    )


def strip_internal_context_markers(value: Any) -> Any:
    """Remove digest-only annotations before persisting context."""
    return _strip_internal_context_markers(
        value,
        strip_internal_context_markers=strip_internal_context_markers,
        INTERNAL_CONTEXT_KEYS=INTERNAL_CONTEXT_KEYS,
    )


def merge_context(current: Dict[str, Any], previous: Dict[str, Any]) -> Dict[str, Any]:
    """Fill missing/None fields in current from previous, preserving current values.

    v3 only carried state/config forward when the whole object was absent. v4
    merges per field, so a quiet or partial window can still show reply budgets
    from the last known config while using the current state snapshot.
    """
    return _merge_context(
        current,
        previous,
        strip_internal_context_markers=strip_internal_context_markers,
        INTERNAL_CONTEXT_KEYS=INTERNAL_CONTEXT_KEYS,
    )


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
    return _merge_context_from_log_backscan(
        current,
        previous,
        backscan_ts=backscan_ts,
        strip_internal_context_markers=strip_internal_context_markers,
        INTERNAL_CONTEXT_KEYS=INTERNAL_CONTEXT_KEYS,
        dt_text=dt_text,
    )


def find_latest_config_before(paths: List[Path], before: Optional[datetime]) -> Tuple[Dict[str, str], Optional[datetime]]:
    """Scan earlier log records for the latest known Config values before a cutoff.

    Config is emitted as multiple `Config: KEY=VALUE` records at startup. v7
    collects matching records from *all* log files, de-duplicates them, then
    sorts chronologically before applying values. This matters with rotated logs:
    path/glob order is not guaranteed to be chronological, and an older rotated
    file must never overwrite newer config from the live log.
    """
    return _find_latest_config_before(
        paths,
        before,
        is_selftest_log_path=is_selftest_log_path,
        iter_records=iter_records,
        extract_config_pairs=extract_config_pairs,
    )

def response_post_id_is_canonical_string(msg: str) -> bool:
    """Return whether a legacy success response stores its ID as a string."""
    return _response_post_id_is_canonical_string(
        msg, valid_string_public_post_id=valid_string_public_post_id,
    )


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


def seconds_between(a: datetime, b: datetime) -> float:
    """Return the seconds between."""
    return abs((a - b).total_seconds())


def parse_x_request_start(message: str) -> Optional[Dict[str, str]]:
    """Parse the request identity logged immediately before X transport."""
    return _parse_x_request_start(
        message, classify_x_request_endpoint=classify_x_request_endpoint,
    )


def parse_remote_write_transaction_event(record: Record) -> Optional[Dict[str, Any]]:
    """Parse current receipt/media/transport lifecycle logs into one vocabulary."""
    return _parse_remote_write_transaction_event(record, short=short)


def classify_operational_error(message: str) -> str:
    """Classify a traceback/error by its root operational concern."""
    return _classify_operational_error(
        message,
        incident_exception_line=_incident_exception_line,
        normalise_incident_text=_normalise_incident_text,
        is_deleted_or_inaccessible_tweet_403=is_deleted_or_inaccessible_tweet_403,
    )


def _base_remote_control_key(value: Any) -> str:
    """Return a recognised runtime-control key without its timed suffix."""
    return _base_remote_control_key_impl(
        value,
        remote_control_scope_by_key=REMOTE_CONTROL_SCOPE_BY_KEY,
    )


def _remote_control_scope(value: Any) -> str:
    """Map one exact control key to the operation scope it pauses."""
    return _remote_control_scope_impl(
        value,
        base_remote_control_key=_base_remote_control_key,
        remote_control_scope_by_key=REMOTE_CONTROL_SCOPE_BY_KEY,
    )


def _remote_operation_scope_for_lane(value: Any) -> str:
    """Map one runtime/log lane to its successful-operation scope."""
    return _remote_operation_scope_for_lane_impl(
        value,
        remote_lane_scope=REMOTE_LANE_SCOPE,
    )


def _explicit_remote_pause_scope(
    message: Any,
) -> Tuple[str, str, List[str]]:
    """Extract an explicit control key or operation lane from one exception."""
    return _explicit_remote_pause_scope_impl(
        message,
        base_remote_control_key=_base_remote_control_key,
        remote_control_scope=_remote_control_scope,
        remote_operation_scope_for_lane=_remote_operation_scope_for_lane,
    )


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
    return _summarise_operational_error_health(
        errors,
        events,
        receipt_events,
        lifecycle=lifecycle,
        remote_write_transactions=remote_write_transactions,
        handled_api_restrictions=handled_api_restrictions,
        confirmed_reply_receipt_events=confirmed_reply_receipt_events,
        current_remote_write_safety=current_remote_write_safety,
        generation_time=generation_time,
        selected_window_end=selected_window_end,
        current_snapshot_authoritative=current_snapshot_authoritative,
        bounded_source_refs=bounded_source_refs,
        seconds_between=seconds_between,
        annotate_remote_write_snapshot_window=annotate_remote_write_snapshot_window,
        classify_operational_error=classify_operational_error,
        incident_exception_line=_incident_exception_line,
        normalise_incident_text=_normalise_incident_text,
        get_event_time=_event_time,
        base_remote_control_key=_base_remote_control_key,
        remote_control_scope=_remote_control_scope,
        remote_operation_scope_for_lane=_remote_operation_scope_for_lane,
        explicit_remote_pause_scope=_explicit_remote_pause_scope,
        remote_operation_scope_labels=REMOTE_OPERATION_SCOPE_LABELS,
        clock_now=datetime.now,
        fromtimestamp=datetime.fromtimestamp,
        datetime_min=datetime.min,
    )


def correlate_media_upload_incidents(
    records: List[Record],
    max_text: int,
    input_file_indexes: Optional[Dict[str, int]] = None,
) -> Tuple[List[Dict[str, Any]], set[str]]:
    """Return the correlate media upload incidents."""
    return _correlate_media_upload_incidents(
        records, max_text, input_file_indexes,
        short=short, seconds_between=seconds_between,
        record_source_ref=record_source_ref,
        record_fingerprint=record_fingerprint,
        bounded_source_refs=bounded_source_refs,
        is_media_fallback_warning=is_media_fallback_warning,
        find_recent_media_path=find_recent_media_path,
        is_media_v2_request_failure=is_media_v2_request_failure,
        is_media_v1_success=is_media_v1_success,
        is_main_post_success=is_main_post_success,
        is_media_v1_failure=is_media_v1_failure,
    )


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


def normalise_active_xai_call_attempt(value: Any) -> Optional[Dict[str, Any]]:
    """Return resumable provider metadata with the current lane normaliser."""
    return _normalise_active_xai_call_attempt(
        value, normalise_reply_lane=normalise_reply_lane,
    )


def _cache_input_metric(
    usage: Dict[str, Any],
    prompt_details: Dict[str, Any],
    input_details: Dict[str, Any],
    field_names: Tuple[str, ...],
) -> Optional[int]:
    """Read an explicit cache metric with the current optional converter."""
    return _provider_cache_input_metric(
        usage, prompt_details, input_details, field_names,
        optional_int_usage_value=optional_int_usage_value,
    )


def summarize_xai_usage_event(
    record: Record,
    usage: Dict[str, Any],
    context: Dict[str, Any],
    *,
    model: str = "",
    provider: str = "xAI",
    call_start_matched: bool = False,
) -> Dict[str, Any]:
    """Project provider usage with the current stage, cache and value helpers."""
    return _summarize_xai_usage_event(
        record, usage, context,
        model=model,
        provider=provider,
        call_start_matched=call_start_matched,
        xai_usage_stage_from_msg=xai_usage_stage_from_msg,
        _cache_input_metric=_cache_input_metric,
        int_usage_value=int_usage_value,
        optional_int_usage_value=optional_int_usage_value,
    )


def _valid_durable_ai_reply_draft(
    draft: Any,
    *,
    context: Dict[str, Any],
    lane: str,
    target_id: str,
    conversation_id: str,
    reply_text: str,
) -> bool:
    """Validate draft identity with the digest's current UTC validator."""
    return _valid_durable_ai_reply_draft_impl(
        draft,
        context=context,
        lane=lane,
        target_id=target_id,
        conversation_id=conversation_id,
        reply_text=reply_text,
        valid_utc_timestamp=_valid_canonical_utc_timestamp,
    )


def _confirmed_conversational_receipt_evidence(
    receipt: Any,
) -> Optional[Dict[str, Any]]:
    """Validate receipts with the digest's current text, draft and time seams."""
    return _confirmed_conversational_receipt_evidence_impl(
        receipt,
        valid_text=valid_conversational_public_reply_text,
        valid_draft=_valid_durable_ai_reply_draft,
        fromtimestamp=datetime.fromtimestamp,
        london=LONDON,
        encode_atomic_json=canonical_atomic_json_bytes,
    )


def load_confirmed_reply_receipt_evidence(
    project_dir: Path,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Load confirmed receipts through the digest's current private-file seams."""
    return _load_confirmed_reply_receipt_evidence(
        project_dir,
        read_bytes=read_stable_private_json_bytes,
        parse_json_object=_strict_native_json_object,
        encode_atomic_json=canonical_atomic_json_bytes,
        validate_receipt=_confirmed_conversational_receipt_evidence,
    )


def _valid_canonical_utc_timestamp(value: Any) -> bool:
    """Validate canonical UTC text with the digest's current ISO parser."""
    return _valid_canonical_utc_timestamp_impl(
        value, fromisoformat=datetime.fromisoformat,
    )


def _valid_historical_completed_item(
    parent_key: str,
    item: Any,
) -> Optional[Dict[str, Any]]:
    """Validate completed history with the digest's current private encoder."""
    return _valid_historical_completed_item_impl(
        parent_key, item, encode_private_json=canonical_private_json_bytes,
    )


def _valid_historical_failed_item(parent_key: str, item: Any) -> bool:
    """Validate failed history with the digest's current UTC validator."""
    return _valid_historical_failed_item_impl(
        parent_key, item, valid_utc_timestamp=_valid_canonical_utc_timestamp,
    )


def load_historical_reply_history_evidence(
    project_dir: Path,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Load durable history through the digest's current reader and validators."""
    return _load_historical_reply_history_evidence(
        project_dir,
        read_bytes=read_stable_private_json_bytes,
        parse_json_object=_strict_native_json_object,
        encode_private_json=canonical_private_json_bytes,
        validate_completed_item=_valid_historical_completed_item,
        validate_failed_item=_valid_historical_failed_item,
    )


def _public_reply_text_result(
    candidates: List[Dict[str, Any]],
    *,
    unavailable_reason: str,
) -> Dict[str, Any]:
    """Resolve exact authoritative text using the current source-reference helper."""

    return _public_reply_text_result_impl(
        candidates,
        unavailable_reason=unavailable_reason,
        bounded_source_refs=bounded_source_refs,
    )


def _durable_public_reply_text_candidates(
    runtime_state: Any,
    *,
    lane: str,
    target_id: str,
    reply_post_id: str,
    original_post_id: str = "",
    confirmed_receipt_evidence: Optional[List[Dict[str, Any]]] = None,
) -> List[Dict[str, Any]]:
    """Select prepared durable text using the current conversational validator."""

    return _durable_public_reply_text_candidates_impl(
        runtime_state,
        lane=lane,
        target_id=target_id,
        reply_post_id=reply_post_id,
        original_post_id=original_post_id,
        confirmed_receipt_evidence=confirmed_receipt_evidence,
        valid_public_text=valid_conversational_public_reply_text,
    )


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
    """Enrich confirmed reply records using current text and evidence helpers."""

    return _enrich_published_reply_text(
        report,
        runtime_state=runtime_state,
        structured_reply_confirmations=structured_reply_confirmations,
        historical_reply_text_evidence=historical_reply_text_evidence,
        confirmed_receipt_evidence=confirmed_receipt_evidence,
        durable_evidence_status=durable_evidence_status,
        production_event_object_ids=production_event_object_ids,
        bounded_source_refs=bounded_source_refs,
        epoch_to_london_text=epoch_to_london_text,
        normalise_confirmation=_normalised_structured_reply_confirmation,
        durable_candidates=_durable_public_reply_text_candidates,
        resolve_text=_public_reply_text_result,
        warning_limit=PUBLISHED_REPLY_WARNING_LIMIT,
    )


def reconcile_reply_pipeline_effective_outcomes(
    events: List[Dict[str, Any]],
) -> None:
    """Attach later terminal/public observations to stage-only telemetry."""
    _reconcile_reply_pipeline_effective_outcomes(
        events, _normalise_lane=_normalise_lane,
    )


@dataclass
class _AnalysisSourceContext:
    """Pending observations kept independently for production and self-test logs."""

    pending_quote: Dict[str, Any] = field(default_factory=dict)
    pending_meme: Dict[str, Any] = field(default_factory=dict)
    pending_mention: Dict[str, Any] = field(default_factory=dict)
    pending_qt: Dict[str, Any] = field(default_factory=dict)
    pending_confirmed_reply_receipt: Dict[str, Any] = field(default_factory=dict)
    last_created_post: Dict[str, Any] = field(default_factory=dict)
    active_xai_context: Optional[Dict[str, Any]] = None
    active_xai_call_attempt_index: Optional[int] = None


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

    production_context = _AnalysisSourceContext(
        pending_mention=dict(initial_pending_mention or {}),
        pending_qt=dict(initial_pending_qt or {}),
        active_xai_context=dict(initial_active_xai_context or {}) or None,
        active_xai_call_attempt_index=0 if restored_xai_call_attempt else None,
    )
    if production_context.pending_mention:
        production_context.pending_mention.setdefault("_identity_production", True)
        production_context.pending_mention.setdefault("_reply_post_id_production", True)
    if production_context.pending_qt:
        production_context.pending_qt.setdefault("_identity_production", True)
        production_context.pending_qt.setdefault("_reply_post_id_production", True)
    selftest_context = _AnalysisSourceContext()
    source_context = production_context
    structured_reply_confirmations: List[Dict[str, Any]] = []
    historical_reply_text_evidence: List[Dict[str, Any]] = list(
        historical_history_evidence or []
    )
    current_source_record: Optional[Record] = None
    production_event_object_ids: set[int] = set()
    quote_publications = QuotePublicationCorrelation(
        source_is_selftest=lambda: (
            current_source_record is not None
            and is_selftest_log_path(current_source_record.path)
        ),
        valid_string_public_post_id=lambda value: valid_string_public_post_id(value),
        valid_bounded_utf8_text=lambda value, **kwargs: valid_bounded_utf8_text(value, **kwargs),
        bounded_source_refs=lambda *groups: bounded_source_refs(*groups),
        warning_limit=lambda: ENGAGEMENT_CORRELATION_WARNING_LIMIT,
        question_separator=lambda: ENGAGEMENT_QUESTION_PUBLIC_TEXT_SEPARATOR,
    )

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

    local_rejections_by_identity: Dict[Tuple[str, str], Dict[str, Any]] = {}

    def add_or_merge_local_rejection(
        ts: datetime,
        *,
        lane: Any,
        target_id: Any,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Keep one enriched effective local-rejection record per target."""
        return _add_or_merge_local_rejection(
            ts, kwargs, lane=lane, target_id=target_id,
            local_rejections_by_identity=local_rejections_by_identity,
            max_text=max_text,
            valid_string_public_post_id=valid_string_public_post_id,
            _normalise_lane=_normalise_lane, bounded_event_text=bounded_event_text,
            bounded_event_string_list=bounded_event_string_list,
            short=short, add_event=add_event,
        )

    def add_receipt_event(kind: str, r: Record, **kwargs: Any) -> None:
        _add_receipt_event(
            kind, r, kwargs,
            input_file_indexes=input_file_indexes, stats=stats,
            short=short, is_selftest_log_path=is_selftest_log_path,
            record_source_ref=record_source_ref,
            receipt_events=receipt_events,
        )

    def add_confirmed_reply_receipt_event(kind: str, r: Record, **kwargs: Any) -> None:
        source_context.pending_confirmed_reply_receipt = _add_confirmed_reply_receipt_event(
            kind, r, kwargs,
            input_file_indexes=input_file_indexes, stats=stats,
            short=short, is_selftest_log_path=is_selftest_log_path,
            record_source_ref=record_source_ref,
            confirmed_reply_receipts=confirmed_reply_receipts,
            pending_confirmed_reply_receipt=source_context.pending_confirmed_reply_receipt,
        )

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
        _add_reply_media_context_event(
            r, kwargs, reply_media_context=reply_media_context,
            stats=stats, short=short,
        )

    def conversational_evidence_fields(
        event_obj: Dict[str, Any],
        *,
        evidence_ids: Any,
        factual_claim_count: Any,
    ) -> Dict[str, Any]:
        return _conversational_evidence_fields(
            event_obj, evidence_ids=evidence_ids,
            factual_claim_count=factual_claim_count,
            bounded_event_string_list=bounded_event_string_list,
        )

    for record_index, r in enumerate(records):
        current_source_record = r
        msg = r.msg
        production_record = not is_selftest_log_path(r.path)
        source_context = production_context if production_record else selftest_context
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
            record_x_request_start(
                request_start, r, input_file_indexes=input_file_indexes,
                x_requests=x_requests,
                latest_x_request_by_source=latest_x_request_by_source,
                stats=stats, record_source_ref=record_source_ref,
            )

        transaction_event = (
            parse_remote_write_transaction_event(r) if production_record else None
        )
        if transaction_event is not None:
            record_remote_write_transaction(
                transaction_event, r, input_file_indexes=input_file_indexes,
                remote_write_transactions=remote_write_transactions,
                stats=stats, record_source_ref=record_source_ref,
                add_receipt_event=add_receipt_event,
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

        is_asset_metadata_warning, is_handled_reply_restriction = observe_error_warning(
            r, msg,
            self_test_errors=self_test_errors,
            confirmed_post_recovery=confirmed_post_recovery,
            confirmed_reply_recovery=confirmed_reply_recovery,
            errors=errors,
            pending_mention=source_context.pending_mention,
            pending_qt=source_context.pending_qt,
            pending_meme=source_context.pending_meme,
            pending_quote=source_context.pending_quote,
            is_reply_visual_description_event=is_reply_visual_description_event,
            input_file_indexes=input_file_indexes,
            is_reply_target_eligibility_restriction=is_reply_target_eligibility_restriction,
            is_deleted_or_inaccessible_tweet_403=is_deleted_or_inaccessible_tweet_403,
            add_or_merge_local_rejection=add_or_merge_local_rejection,
            short=short, record_source_ref=record_source_ref,
            record_fingerprint=record_fingerprint,
            classify_operational_error=classify_operational_error,
        )

        (
            source_context.active_xai_context,
            source_context.active_xai_call_attempt_index,
        ) = observe_provider_message(
            r, msg,
            pending_mention=source_context.pending_mention,
            pending_qt=source_context.pending_qt,
            active_xai_context=source_context.active_xai_context,
            active_xai_call_attempt_index=source_context.active_xai_call_attempt_index,
            xai_call_attempts=xai_call_attempts,
            xai_usage_events=xai_usage_events,
            xai_usage_parse_errors=xai_usage_parse_errors,
            stats=stats,
            xai_usage_context_from_pending=xai_usage_context_from_pending,
            parse_xai_call_start=parse_xai_call_start,
            unknown_xai_usage_context=unknown_xai_usage_context,
            parse_xai_usage_from_msg=parse_xai_usage_from_msg,
            xai_usage_stage_from_msg=xai_usage_stage_from_msg,
            provider_usage_provider_from_msg=provider_usage_provider_from_msg,
            normalise_reply_lane=normalise_reply_lane,
            summarize_xai_usage_event=summarize_xai_usage_event,
            short=short,
        )

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
                    stats["reply_visual_description_events"] += 1
            elif event_obj and event_obj.get("event") == "main_post_posted":
                record_main_post_publication(
                    event_obj, strict_structured_event_obj, r.ts,
                    valid_string_public_post_id=valid_string_public_post_id,
                    SHA256_LOWER_RE=SHA256_LOWER_RE,
                    engagement_main_metadata_status=engagement_main_metadata_status,
                    retain_quote_post_evidence=quote_publications.retain,
                    note_invalid_quote_post_evidence=quote_publications.note_invalid,
                    make_source_ref=lambda: record_source_ref(r, input_file_indexes),
                )
                if event_obj.get("lane") == "daily_meme":
                    source_context.pending_meme.update({
                        "post_id": event_obj.get("post_id"),
                        "file": event_obj.get("filename"),
                    })
            elif event_obj and event_obj.get("event") == "account_root_posted":
                record_account_root_publication(
                    event_obj, strict_structured_event_obj, r.ts,
                    valid_account_root_publication_identity=valid_account_root_publication_identity,
                    SHA256_LOWER_RE=SHA256_LOWER_RE,
                    valid_bounded_utf8_text=valid_bounded_utf8_text,
                    retain_quote_post_evidence=quote_publications.retain,
                    note_invalid_quote_post_evidence=quote_publications.note_invalid,
                    make_source_ref=lambda: record_source_ref(r, input_file_indexes),
                )
            elif (
                event_obj
                and event_obj.get("event")
                == "engagement_question_experimental_member_confirmed"
            ):
                record_engagement_confirmation(
                    event_obj, strict_structured_event_obj, r.ts,
                    valid_engagement_confirmation_event=valid_engagement_confirmation_event,
                    retain_quote_post_evidence=quote_publications.retain,
                    note_invalid_quote_post_evidence=quote_publications.note_invalid,
                    make_source_ref=lambda: record_source_ref(r, input_file_indexes),
                )
            elif event_obj and event_obj.get("event") in {
                "engagement_question_experiment_invalid",
                "engagement_question_experimental_member_deferred",
                "engagement_question_treatment_notification_write_failed",
            }:
                record_engagement_trial_outcome(
                    event_obj, r.ts,
                    engagement_trial_outcomes=quote_publications.trial_outcomes,
                    bounded_event_text=bounded_event_text,
                    SHA256_LOWER_RE=SHA256_LOWER_RE,
                    valid_string_public_post_id=valid_string_public_post_id,
                    ENGAGEMENT_PAIR_ID_RE=ENGAGEMENT_PAIR_ID_RE,
                    bounded_event_nonnegative_integer=bounded_event_nonnegative_integer,
                    bounded_event_boolean=bounded_event_boolean,
                    make_source_ref=lambda: record_source_ref(r, input_file_indexes),
                )
            elif event_obj and event_obj.get("event") == "historical_context_semantic_gate":
                record_historical_context_semantic_gate(
                    event_obj, r.ts, add_event=add_event
                )
            elif event_obj and event_obj.get("event") == "historical_context_runtime":
                record_historical_context_runtime(
                    event_obj, r.ts, stats, add_event=add_event
                )
            elif event_obj and event_obj.get("event") == "reply_evidence_unavailable":
                record_reply_evidence_unavailable(
                    event_obj, r.ts, stats, add_event=add_event,
                    bounded_event_text=bounded_event_text,
                    valid_string_public_post_id=valid_string_public_post_id,
                )
            elif event_obj and event_obj.get("event") == "runtime_control_pause":
                record_runtime_control_pause(
                    event_obj, r.ts, stats, add_event=add_event,
                    bounded_event_string_list=bounded_event_string_list,
                    bounded_event_text=bounded_event_text,
                    bounded_event_nonnegative_integer=bounded_event_nonnegative_integer,
                )
            elif event_obj and event_obj.get("event") == "runtime_control_clear":
                record_runtime_control_clear(
                    event_obj, r.ts, stats, add_event=add_event,
                    bounded_event_string_list=bounded_event_string_list,
                    bounded_event_text=bounded_event_text,
                )
            elif event_obj and event_obj.get("event") == "clarification_reply_cap_override":
                record_clarification_reply_cap_override(
                    event_obj, r.ts, stats, add_event=add_event,
                    valid_string_public_post_id=valid_string_public_post_id,
                    bounded_event_text=bounded_event_text,
                )
            elif event_obj and event_obj.get("event") == "clarification_reply_used":
                record_clarification_reply_used(
                    event_obj, r.ts, stats, add_event=add_event,
                    valid_string_public_post_id=valid_string_public_post_id,
                    bounded_event_text=bounded_event_text,
                )
            elif event_obj and event_obj.get("event") == "repair_reply_completed":
                record_repair_reply_completed(
                    event_obj, r.ts, stats, add_event=add_event,
                    valid_string_public_post_id=valid_string_public_post_id,
                )
            elif event_obj and event_obj.get("event") in {
                "mention_backlog_started",
                "mention_backlog_progress",
                "mention_backlog_completed",
                "mention_backlog_reset",
            }:
                record_mention_backlog(
                    event_obj, r.ts, add_event=add_event, stats=stats,
                    valid_string_public_post_id=valid_string_public_post_id,
                    bounded_event_nonnegative_integer=bounded_event_nonnegative_integer,
                    bounded_event_boolean=bounded_event_boolean,
                    bounded_event_text=bounded_event_text,
                )
            elif event_obj and event_obj.get("event") in {
                "author_evaluation_quarantine_started",
                "author_evaluation_quarantine_skip",
                "author_evaluation_quarantine_expired",
            }:
                record_author_evaluation_quarantine(
                    event_obj, r.ts, add_event=add_event, stats=stats,
                    valid_string_public_post_id=valid_string_public_post_id,
                    bounded_event_nonnegative_integer=bounded_event_nonnegative_integer,
                )
            elif event_obj and event_obj.get("event") == "reply_posted":
                confirmation = prepare_structured_reply_confirmation(
                    strict_structured_event_obj,
                    production_record=production_record,
                    time_text=lambda: dt_text(r.ts),
                    event_insertion_index=lambda: len(events),
                    source_sequence=record_index,
                    make_source_ref=lambda: record_source_ref(r, input_file_indexes),
                )
                if confirmation is not None:
                    structured_reply_confirmations.append(confirmation)
            elif (
                event_obj
                and event_obj.get("event")
                == "historical_context_reply_posted"
            ):
                historical_reply_text_evidence.append(
                    prepare_structured_historical_publication_evidence(
                        event_obj, strict_structured_event_obj,
                        production_record=production_record,
                        valid_string_public_post_id=lambda value: valid_string_public_post_id(value),
                        valid_bounded_utf8_text=lambda value, **kwargs: valid_bounded_utf8_text(value, **kwargs),
                        sha256_fullmatch=lambda value: SHA256_LOWER_RE.fullmatch(value),
                        time_text=lambda: dt_text(r.ts),
                        event_insertion_index=lambda: len(events),
                        source_sequence=record_index,
                        make_source_ref=lambda: record_source_ref(r, input_file_indexes),
                    )
                )
            elif event_obj and event_obj.get("event") == "historical_context_reply":
                historical_fields = prepare_historical_context_reply(event_obj)
                status = historical_fields["status"]
                historical_event = add_event(
                    "historical_context_reply", r.ts, **historical_fields
                )
                if status in {"completed", "already_completed"}:
                    anchor_valid = valid_structured_historical_completion_anchor(
                        strict_structured_event_obj, status,
                        valid_string_public_post_id=lambda value: valid_string_public_post_id(value),
                        valid_bounded_utf8_text=lambda value, **kwargs: valid_bounded_utf8_text(value, **kwargs),
                        sha256_fullmatch=lambda value: SHA256_LOWER_RE.fullmatch(value),
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
                record_posting_transaction_state(
                    event_obj, r.ts, stats, add_event=add_event,
                    bounded_event_text=bounded_event_text,
                    valid_string_public_post_id=valid_string_public_post_id,
                    bounded_event_boolean=bounded_event_boolean,
                )
            elif event_obj and event_obj.get("event") == "historical_context_obligation":
                record_historical_context_obligation(
                    event_obj, r.ts, stats, add_event=add_event
                )
            elif event_obj and event_obj.get("event") == "historical_context_outbox":
                record_historical_context_outbox(
                    event_obj, r.ts, stats, add_event=add_event
                )
            elif event_obj and event_obj.get("event") == "daily_meme_failure":
                record_daily_meme_failure(
                    event_obj, r.ts, stats, add_event=add_event,
                    bounded_event_text=bounded_event_text,
                    valid_string_public_post_id=valid_string_public_post_id,
                )
            elif event_obj and event_obj.get("event") == "reply_strategy_decision":
                record_reply_strategy_decision(
                    event_obj, r.ts,
                    add_event=add_event,
                    conversational_evidence_fields=conversational_evidence_fields,
                )
            elif event_obj and event_obj.get("event") == "reply_strategy_outcome":
                record_reply_strategy_outcome(
                    event_obj, r.ts,
                    add_event=add_event,
                    conversational_evidence_fields=conversational_evidence_fields,
                )
            elif event_obj and event_obj.get("event") == "reply_target_terminal":
                record_reply_target_terminal(
                    event_obj, r.ts,
                    add_event=add_event,
                )
            elif event_obj and event_obj.get("event") == "reply_strategy_rejection":
                record_reply_strategy_rejection(
                    event_obj, r.ts,
                    add_event=add_event,
                )
            elif event_obj and event_obj.get("event") == "single_call_reply_decision":
                record_single_call_reply_decision(
                    event_obj, r.ts, add_event=add_event,
                )
            elif event_obj and event_obj.get("event") == "single_call_reply_provider_usage":
                record_single_call_reply_provider_usage(
                    event_obj, r.ts, add_event=add_event,
                )
            elif event_obj and event_obj.get("event") == "single_call_reply_posting_outcome":
                record_single_call_reply_posting_outcome(
                    event_obj, r.ts, add_event=add_event,
                )
            elif event_obj and event_obj.get("event") == "single_call_reply_draft_recovered":
                record_single_call_reply_draft_recovered(
                    event_obj, r.ts, add_event=add_event,
                )
            elif event_obj and event_obj.get("event") == "ai_reply_pipeline_decision":
                record_ai_reply_pipeline_decision(
                    event_obj, r.ts,
                    add_event=add_event,
                    add_or_merge_local_rejection=add_or_merge_local_rejection,
                    conversational_evidence_fields=conversational_evidence_fields,
                    bounded_event_string_list=bounded_event_string_list,
                )
            elif event_obj and event_obj.get("event") == "ai_reply_pipeline_stage_summary":
                record_ai_reply_pipeline_stage_summary(
                    event_obj, r.ts,
                    add_event=add_event,
                    bounded_event_string_list=bounded_event_string_list,
                    normalise_majority_review_telemetry=normalise_majority_review_telemetry,
                )
            elif event_obj and event_obj.get("event") == "ai_reply_pipeline_effective_outcome":
                record_ai_reply_pipeline_effective_outcome(
                    event_obj, r.ts,
                    add_or_merge_local_rejection=add_or_merge_local_rejection,
                )
            elif event_obj and event_obj.get("event") == "ai_reply_pipeline_failure":
                record_ai_reply_pipeline_failure(
                    event_obj, r.ts,
                    add_event=add_event,
                )
            elif event_obj and event_obj.get("event") == "ai_reply_pipeline_outcome":
                record_ai_reply_pipeline_outcome(
                    event_obj, r.ts,
                    add_event=add_event,
                    conversational_evidence_fields=conversational_evidence_fields,
                    bounded_event_string_list=bounded_event_string_list,
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

        if handle_legacy_receipt_message(
            r, msg,
            pending_confirmed_reply_receipt=source_context.pending_confirmed_reply_receipt,
            add_receipt_event=add_receipt_event,
            add_confirmed_reply_receipt_event=add_confirmed_reply_receipt_event,
        ):
            continue

        if handle_legacy_reply_media_context_message(
            r, msg, add_reply_media_context_event=add_reply_media_context_event,
        ):
            continue

        if is_asset_metadata_warning and r.level in {"ERROR", "CRITICAL", "WARNING"}:
            kind = "metadata_warning"
            if "Quote analysis" in msg or "quote analysis" in msg or "Skipping unanalysed current quote" in msg:
                kind = "quote_metadata_warning"
            elif "Image analysis" in msg or "image analysis" in msg or "Image metadata" in msg or "image analysis" in msg:
                kind = "image_metadata_warning"
            add_asset_health(kind, r)
            continue

        if handle_cooldown_message(
            r, msg, stats=stats, cooldown_active=cooldown_active,
            add_event=add_event,
        ):
            continue
        m = re.search(r"Migrated legacy pickle file (.+) to JSON file (.+)$", msg)
        if m:
            add_event("used_history_migrated", r.ts, legacy_file=m.group(1).strip(), json_file=m.group(2).strip())
            continue
        m = re.search(r"Normalized used-history JSON ordering in (.+)$", msg)
        if m:
            add_event("used_history_normalized", r.ts, json_file=m.group(1).strip())
            continue
        if handle_x_api_error(
            r, msg, latest_x_request_by_source=latest_x_request_by_source,
            pending_mention=source_context.pending_mention,
            pending_qt=source_context.pending_qt,
            is_handled_reply_restriction=is_handled_reply_restriction,
            api_errors=api_errors, handled_api_restrictions=handled_api_restrictions,
            stats=stats, input_file_indexes=input_file_indexes,
            parse_dt=parse_dt, seconds_between=seconds_between, short=short,
            record_source_ref=record_source_ref,
            is_deleted_or_inaccessible_tweet_403=is_deleted_or_inaccessible_tweet_403,
        ):
            continue
        source_context.active_xai_context = observe_provider_error(
            r, msg, active_xai_context=source_context.active_xai_context,
            api_errors=api_errors, stats=stats,
            input_file_indexes=input_file_indexes, short=short,
            record_source_ref=record_source_ref,
        )
        enrich_latest_api_error(msg, api_errors=api_errors, stats=stats)

        if handle_legacy_quiet_message(
            r, msg,
            stats=stats, add_event=add_event,
        ):
            continue

        if handle_legacy_quote_image_selection(
            r, msg,
            pending_quote=source_context.pending_quote,
            regular_image_usage_events=regular_image_usage_events, add_event=add_event,
        ):
            continue

        if "ORIGINAL_EDITORIAL_SELECTION_RESULT " in msg:
            record_original_editorial_selection(
                msg, r.ts, r.level,
                observations=original_editorial_shadow_events,
                pending_shadow_companions=pending_original_editorial_shadow_companions,
                stats=stats, errors=errors,
                parse_json_object=_strict_native_json_object,
                short_text=short,
                source_ref=lambda: record_source_ref(r, input_file_indexes),
            )
            continue

        if "ORIGINAL_EDITORIAL_SHADOW_RESULT " in msg:
            record_original_editorial_shadow(
                msg, r.ts, r.level,
                observations=original_editorial_shadow_events,
                pending_shadow_companions=pending_original_editorial_shadow_companions,
                stats=stats, errors=errors,
                parse_json_object=_strict_native_json_object,
                short_text=short,
                source_ref=lambda: record_source_ref(r, input_file_indexes),
            )
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

        handled, latest_generated_image_spacing = handle_legacy_generated_image_spacing(
            r, msg,
            latest_generated_image_spacing=latest_generated_image_spacing,
            generated_image_spacing_events=generated_image_spacing_events,
        )
        if handled:
            continue

        handled, source_context.pending_quote = handle_legacy_quote_image_posting(
            r, msg,
            pending_quote=source_context.pending_quote,
            regular_image_usage_events=regular_image_usage_events, add_event=add_event,
            lit=lit,
        )
        if handled:
            continue

        handled, source_context.pending_meme = handle_legacy_meme_posting(
            r, msg,
            pending_meme=source_context.pending_meme, add_event=add_event, lit=lit,
        )
        if handled:
            continue

        handled, source_context.last_created_post = handle_legacy_created_post(
            r, msg,
            production_record=production_record,
            last_created_post=source_context.last_created_post,
            stats=stats, production_event_object_ids=production_event_object_ids,
            add_event=add_event, try_parse_response_id_text=try_parse_response_id_text,
            response_post_id_is_canonical_string=response_post_id_is_canonical_string,
        )
        if handled:
            continue

        (
            handled,
            source_context.pending_mention,
            source_context.active_xai_context,
        ) = handle_legacy_mention_reply(
            r, msg,
            record_index=record_index, production_record=production_record,
            pending_mention=source_context.pending_mention,
            active_xai_context=source_context.active_xai_context,
            last_created_post=source_context.last_created_post,
            production_event_object_ids=production_event_object_ids,
            routine_skip_counts=routine_skip_counts, add_event=add_event, lit=lit,
        )
        if handled:
            continue

        (
            handled,
            source_context.pending_qt,
            source_context.active_xai_context,
        ) = handle_legacy_quote_reply(
            r, msg,
            record_index=record_index, production_record=production_record,
            pending_qt=source_context.pending_qt,
            active_xai_context=source_context.active_xai_context,
            last_created_post=source_context.last_created_post,
            production_event_object_ids=production_event_object_ids,
            routine_skip_counts=routine_skip_counts, add_event=add_event, lit=lit,
        )
        if handled:
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

    source_context = production_context
    current_source_record = None

    confirmed_experimental_publications = quote_publications.prepare_report(
        events, production_event_object_ids,
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
    media_upload_incidents, errors = prepare_media_incidents_and_errors(
        records=records,
        max_text=max_text,
        input_file_indexes=input_file_indexes,
        remote_write_transactions=remote_write_transactions,
        x_requests=x_requests,
        current_remote_write_safety=current_remote_write_safety,
        errors=errors,
        self_test_errors=self_test_errors,
        self_test_times=self_test_times,
        api_error_times=api_error_times,
        handled_restriction_times=handled_restriction_times,
        correlate_media_upload_incidents=correlate_media_upload_incidents,
        parse_dt=parse_dt,
        strptime=datetime.strptime,
        seconds_between=seconds_between,
    )

    append_unresolved_reply_receipt_errors(
        confirmed_reply_receipts=confirmed_reply_receipts,
        errors=errors,
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
    (
        durably_reconciled_reply_receipts,
        status_unavailable_reply_receipts,
        active_snapshot_reply_receipts,
    ) = prepare_reply_receipt_recovery_reporting(
        error_health=error_health,
        current_remote_write_safety=current_remote_write_safety,
        confirmed_reply_receipts=confirmed_reply_receipts,
        parse_dt=parse_dt,
        _normalise_lane=_normalise_lane,
        REMOTE_WRITE_RECEIPT_ROLE_LABELS=REMOTE_WRITE_RECEIPT_ROLE_LABELS,
    )

    # Build a short automatic headline around current health, not raw traceback volume.
    (
        headline, transient_provider_timeouts, handled_media_fallbacks,
        reconciled_media_uploads, unrecovered_media, derived,
    ) = prepare_headline_and_derived(
        stats=stats, error_health=error_health,
        current_remote_write_safety=current_remote_write_safety,
        handled_api_restrictions=handled_api_restrictions,
        media_upload_incidents=media_upload_incidents,
        self_test_errors=self_test_errors,
        confirmed_post_recovery=confirmed_post_recovery,
        confirmed_reply_recovery=confirmed_reply_recovery,
        receipt_events=receipt_events, asset_health=asset_health,
        latest_state_summary=latest_state_summary, records=records, configs=configs,
        plural_count=plural_count, int_or_none=int_or_none, parse_dt=parse_dt,
    )

    api_health_preparation = prepare_api_health(
        api_errors=api_errors, handled_api_restrictions=handled_api_restrictions,
        x_requests=x_requests, remote_write_transactions=remote_write_transactions,
        events=events, production_event_object_ids=production_event_object_ids,
        quote_post_correlations=quote_publications.evidence,
        structured_reply_confirmations=structured_reply_confirmations,
        historical_reply_text_evidence=historical_reply_text_evidence,
        transient_provider_timeouts=transient_provider_timeouts,
        handled_restriction_times=handled_restriction_times,
        event_counter=Counter, bounded_event_text=bounded_event_text,
        SHA256_LOWER_RE=SHA256_LOWER_RE,
        valid_string_public_post_id=valid_string_public_post_id,
        _normalised_structured_reply_confirmation=_normalised_structured_reply_confirmation,
        seconds_between=seconds_between, parse_dt=parse_dt,
        strptime=datetime.strptime, datetime_min=datetime.min,
    )

    prepare_inferred_reply_strategy_outcomes(
        events=events, handled_api_restrictions=handled_api_restrictions,
        _normalise_lane=_normalise_lane, parse_dt=parse_dt, add_event=add_event,
    )

    context_quality = historical_context_quality_summary(events)
    single_call_quality = single_call_reply_summary(events)
    (
        legacy_multi_stage, headline, headline_without_current_cooldown,
    ) = prepare_reply_quality_headline(
        events=events, headline=headline, single_call_quality=single_call_quality,
        plural_count=plural_count,
    )

    (
        mention_control_events,
        mention_control_counts,
        pipeline_evaluations_skipped,
    ) = prepare_mention_control_observations(events, event_counter=Counter)
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
        "api_health": api_health_report(
            api_health_preparation, api_errors=api_errors,
            handled_api_restrictions=handled_api_restrictions,
            cooldown_active=cooldown_active, x_requests=x_requests,
        ),
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
            "outcomes": quote_publications.trial_outcomes,
            "correlation_warnings": quote_publications.warnings,
            "correlation_warning_omitted_count": (
                quote_publications.warning_omitted_count
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
        "production_consistency": production_consistency_report(events, stats),
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
            "active_xai_context": source_context.active_xai_context,
            "active_xai_call_attempt": (
                dict(xai_call_attempts[source_context.active_xai_call_attempt_index])
                if source_context.active_xai_call_attempt_index is not None
                and xai_call_attempts[source_context.active_xai_call_attempt_index].get(
                    "usage_observed"
                )
                is not True
                else None
            ),
            "pending_mention": (
                {
                    key: value
                    for key, value in source_context.pending_mention.items()
                    if not key.startswith("_")
                }
                if source_context.pending_mention
                else None
            ),
            "pending_qt": (
                {
                    key: value
                    for key, value in source_context.pending_qt.items()
                    if not key.startswith("_")
                }
                if source_context.pending_qt
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


def refresh_current_health_headline(report: Dict[str, Any]) -> None:
    """Rebuild current-health and cooldown claims after runtime overlay."""
    _refresh_current_health_headline(
        report,
        int_or_none=int_or_none,
        plural_count=plural_count,
        cooldown_state_text=cooldown_state_text,
        CURRENT_COOLDOWN_FIELDS=CURRENT_COOLDOWN_FIELDS,
    )


def refresh_derived(report: Dict[str, Any]) -> None:
    """Recalculate derived sections after any carried-forward context is applied."""
    _refresh_derived(
        report,
        int_or_none=int_or_none,
        epoch_to_human=epoch_to_human,
        refresh_current_health_headline=refresh_current_health_headline,
    )


def apply_saved_context(
    report: Dict[str, Any],
    state_file: Path,
) -> None:
    """Load digest-cursor history without presenting it as current bot state."""
    _apply_saved_context(
        report,
        state_file,
        read_resume_data=read_resume_data,
        strip_internal_context_markers=strip_internal_context_markers,
        refresh_derived=refresh_derived,
    )



def render_markdown(report: Dict[str, Any]) -> str:
    """Render digest metrics as deterministic Markdown."""
    receipt_events = (report.get("main_post_recovery") or {}).get("receipt_events") or []
    main_post_lifecycle = (
        summarise_main_post_receipt_lifecycle(receipt_events)
        if receipt_events else {}
    )
    reply_recovery = report.get("confirmed_reply_recovery") or {}
    return _render_digest_markdown(
        report,
        main_post_receipt_lifecycle=main_post_lifecycle,
        reply_receipt_lifecycle=summarise_reply_receipt_lifecycle(
            reply_recovery.get("receipt_events") or [],
            reconciled_ambiguity_receipts=(
                reply_recovery.get("durably_reconciled_ambiguity_receipts") or []
            ),
            unavailable_receipts=(
                reply_recovery.get("status_unavailable_receipts") or []
            ),
            normalise_lane=_normalise_lane,
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
        help="Optional explicit log files. If omitted, logs are auto-discovered in --project-dir (the script directory by default).",
    )
    ap.add_argument("--since", help="Only include records at/after this local timestamp, e.g. '2026-06-25 08:00'. Overrides saved resume time.")
    ap.add_argument("--until", help="Only include records at/before this local timestamp.")
    ap.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of Markdown.")
    ap.add_argument("--output", type=Path, help="Atomically write the report to this file instead of stdout; relative paths resolve against CWD.")
    ap.add_argument("--markdown-output", type=Path, help="Also atomically write Markdown to this file; relative paths resolve against CWD.")
    ap.add_argument("--json-output", type=Path, help="Also atomically write structured JSON to this file; relative paths resolve against CWD.")
    ap.add_argument("--verbose-replies", action="store_true", help="Include full exact confirmed reply text and truncated context-reply previews in Markdown.")
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
                "falling back to the timestamp boundary, which can omit newly appended "
                "records after a backward clock jump",
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
    if saved_resume_tail and tail_match is None:
        report["input_warning"] = combine_input_warnings(
            report["input_warning"],
            "saved physical resume cursor was not found; timestamp fallback can "
            "omit newly appended records after a backward clock jump",
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

    if not args.no_state and not args.reset_state:
        apply_saved_context(report, state_file)

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
