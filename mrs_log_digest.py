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
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Optional, Tuple, TypeGuard, Union
from zoneinfo import ZoneInfo

# Explicit imports preserve the existing digest helper import surface.
from mrs_log_digest_analysis import (
    AnalysisSourceContext as _AnalysisSourceContext,
    DigestAnalysisState, DigestInputSelection, DigestCurrentSnapshots,
)
from mrs_log_digest_contracts import (
    ConfirmedReplyRecoverySection, DigestReport, InputFileSummary, MainPostRecoverySection,
    MentionBacklogSection, ProviderRequestCoverage, RestoredResumeContext,
    ResumeContext, RuntimeConfigSnapshot, RuntimeConfigStatus,
    RuntimeStateSnapshot, RuntimeStateStatus, SingleCallCostTotal,
    SourceReference, SummarySection, report_section,
)
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
    read_records_and_summaries as _read_records_and_summaries,
    filter_records_by_time,
    select_resume_window,
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
    ApiHealthPreparation,
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
    QUOTE_PUBLICATION_CORRELATION_WARNING_LIMIT,
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
    meme_queue_health_snapshot,
    runtime_control_snapshot as _runtime_control_snapshot,
    shadow_lifecycle_snapshot,
)
from mrs_log_digest_state_reporting import (
    _HeadlineComponents,
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
    valid_account_root_publication_identity as _valid_account_root_publication_identity,
    QuotePublicationCorrelation,
    record_main_post_publication,
    record_account_root_publication,
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
    record_provider_request_lifecycle,
)
from mrs_provider_request_records import (
    InvalidRequestRecord,
    discover_request_record_paths,
    read_provider_request_record,
    request_record_path,
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

# Version 4 is a major-versioned compatibility contract. Increment this integer
# before removing or renaming a JSON field, changing an established field's type
# or meaning, or otherwise making a consumer-visible incompatible change. Purely
# additive fields do not require an increment within a major version.
DIGEST_JSON_SCHEMA_VERSION = 4
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
    complete_report: Optional[DigestReport] = None,
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
        complete_report=complete_report,
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
def digest_execution_lock(path: Path) -> Iterator[None]:
    """Hold a nonblocking lock for one digest state or output resource."""
    _mkdir_durable(path.parent)
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
) -> SourceReference:
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
) -> List[InputFileSummary]:
    """Summarise input files."""
    return _summarize_input_files(
        paths, since, until, since_exclusive=since_exclusive,
        iter_records=iter_records, fromtimestamp=datetime.fromtimestamp, dt_text=dt_text,
    )


def read_records_and_summaries(
    paths: List[Path],
    since: Optional[datetime],
    until: Optional[datetime],
    *,
    since_exclusive: bool = False,
) -> Tuple[List[Record], List[InputFileSummary]]:
    """Read physical resume records and raw input summaries in one parse."""
    return _read_records_and_summaries(
        paths, since, until, since_exclusive=since_exclusive,
        iter_records=iter_records, fromtimestamp=datetime.fromtimestamp, dt_text=dt_text,
    )


def input_retention_coverage(
    input_files: List[InputFileSummary],
    since: Optional[datetime],
) -> Dict[str, Any]:
    """Describe whether retained records cover the requested lower boundary."""
    return _input_retention_coverage(
        input_files, since, parse_dt=parse_dt, dt_text=dt_text,
    )


def lit(value: str) -> Any:
    """Parse a Python repr string when possible, otherwise return raw."""
    value = value.strip()
    try:
        return ast.literal_eval(value)
    except Exception:
        return value.strip("'\"")


def valid_account_root_publication_identity(event: Any) -> TypeGuard[Dict[str, Any]]:
    """Return whether an account-root event has the producer's core contract."""

    return _valid_account_root_publication_identity(
        event,
        valid_string_public_post_id=valid_string_public_post_id,
    )


def state_list_count(state: Dict[str, Any], key: str) -> Any:
    """Return the state list count."""
    return _state_list_count(
        state, key,
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
) -> Tuple[Optional[RuntimeStateSnapshot], Path, Optional[datetime], str]:
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
) -> Tuple[Optional[RuntimeConfigSnapshot], Path, Optional[datetime], str]:
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
    runtime_state: Optional[RuntimeStateSnapshot],
    runtime_state_status: str,
    runtime_config: Optional[RuntimeConfigSnapshot],
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
    runtime_state: Optional[RuntimeStateSnapshot],
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
    runtime_state: Optional[RuntimeStateSnapshot],
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


class DigestAnalysis(DigestAnalysisState):
    """Route records in order and finalise one read-only digest report."""

    def __init__(
        self,
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
        current_runtime_state: Optional[RuntimeStateSnapshot] = None,
        input_file_indexes: Optional[Dict[str, int]] = None,
        confirmed_receipt_evidence: Optional[List[Dict[str, Any]]] = None,
        historical_history_evidence: Optional[List[Dict[str, Any]]] = None,
        durable_reply_evidence_status: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Bind invocation inputs and initialise independent source contexts."""
        super().__init__()
        # Retain the entry module's injectable counter factory and one callback
        # identity across observations in the same invocation.
        self.stats = Counter()
        self.routine_skip_counts = Counter()
        self.pending_original_editorial_shadow_companions = Counter()
        self._local_rejection_callback = self.add_or_merge_local_rejection
        self.records = records
        self.max_text = max_text
        self.current_remote_write_safety = current_remote_write_safety
        self.generation_time = generation_time
        self.selected_window_end = selected_window_end
        self.current_snapshot_authoritative = current_snapshot_authoritative
        self.current_runtime_state = current_runtime_state
        self.input_file_indexes = input_file_indexes
        self.confirmed_receipt_evidence = confirmed_receipt_evidence
        self.durable_reply_evidence_status = durable_reply_evidence_status
        if self.selected_window_end is None:
            self.selected_window_end = max((record.ts for record in records), default=None)
        restored_xai_call_attempt = normalise_active_xai_call_attempt(
            initial_active_xai_call_attempt
        )
        self.xai_call_attempts = (
            [restored_xai_call_attempt] if restored_xai_call_attempt else []
        )
        self.production_context = _AnalysisSourceContext(
            pending_mention=dict(initial_pending_mention or {}),
            pending_qt=dict(initial_pending_qt or {}),
            active_xai_context=dict(initial_active_xai_context or {}) or None,
            active_xai_call_attempt_index=0 if restored_xai_call_attempt else None,
        )
        if self.production_context.pending_mention:
            self.production_context.pending_mention.setdefault("_identity_production", True)
            self.production_context.pending_mention.setdefault("_reply_post_id_production", True)
        if self.production_context.pending_qt:
            self.production_context.pending_qt.setdefault("_identity_production", True)
            self.production_context.pending_qt.setdefault("_reply_post_id_production", True)
        self.source_context = self.production_context
        self.historical_reply_text_evidence = list(historical_history_evidence or [])
        # These fields exist throughout the coordinator lifetime. A missing
        # preparation object is the honest pre-reconciliation state.
        self.latest_state_summary: Dict[str, Any] = {}
        self.handled_restriction_times: List[datetime] = []
        self.error_health: Dict[str, Any] = {}
        self.durably_reconciled_reply_receipts: List[Dict[str, Any]] = []
        self.status_unavailable_reply_receipts: List[Dict[str, Any]] = []
        self.active_snapshot_reply_receipts: List[Dict[str, Any]] = []
        self.headline_components: _HeadlineComponents = {
            "activity": [], "reply_quality": [], "current_health": "",
            "observations": [], "cooldown": [],
        }
        self.transient_provider_timeouts = 0
        self.handled_media_fallbacks: List[Dict[str, Any]] = []
        self.reconciled_media_uploads: List[Dict[str, Any]] = []
        self.unrecovered_media: List[Dict[str, Any]] = []
        self.derived: Dict[str, Any] = {}
        self.api_health_preparation: Optional[ApiHealthPreparation] = None
        self.context_quality: Dict[str, Any] = {}
        self.single_call_quality: Dict[str, Any] = {}
        self.legacy_multi_stage: Dict[str, int] = {}
        self.headline: List[str] = []
        self.headline_without_current_cooldown: List[str] = []
        self.mention_control_events: List[Dict[str, Any]] = []
        self.mention_control_counts: Counter[str] = Counter()
        self.pipeline_evaluations_skipped = 0
        self.quote_publications = QuotePublicationCorrelation(
            source_is_selftest=lambda: (
                self.current_source_record is not None
                and is_selftest_log_path(self.current_source_record.path)
            ),
            valid_string_public_post_id=lambda value: valid_string_public_post_id(value),
            valid_bounded_utf8_text=lambda value, **kwargs: valid_bounded_utf8_text(value, **kwargs),
            bounded_source_refs=lambda *groups: bounded_source_refs(*groups),
            warning_limit=lambda: QUOTE_PUBLICATION_CORRELATION_WARNING_LIMIT,
        )

    def add_event(self, kind: str, ts: datetime, **kwargs: Any) -> Dict[str, Any]:
        """Insert a bounded event with source provenance and statistics."""
        # Keep parser-bounded values intact for status counts, identity joins and
        # diagnostic classification. Display limits are applied after analysis.
        ev: Dict[str, Any] = {"time": ts.strftime("%Y-%m-%d %H:%M:%S"), "kind": kind}
        for key, value in kwargs.items():
            # Structured handlers have tighter field-specific limits; this
            # guard also bounds free-form captures from older plain-text logs.
            ev[key] = (
                None if isinstance(value, str)
                and not valid_bounded_utf8_text(value, allow_empty=True)
                else value
            )
        if kind in PROVENANCE_EVENT_KINDS and self.current_source_record is not None:
            ev["source_refs"] = [
                record_source_ref(self.current_source_record, self.input_file_indexes)
            ]
        self.events.append(ev)
        if (
            self.current_source_record is not None
            and not is_selftest_log_path(self.current_source_record.path)
        ):
            self.production_event_object_ids.add(id(ev))
        self.stats[kind] += 1
        return ev


    def add_or_merge_local_rejection(
        self,
        ts: datetime,
        *,
        lane: Any,
        target_id: Any,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Keep one enriched effective local-rejection record per target."""
        return _add_or_merge_local_rejection(
            ts, kwargs, lane=lane, target_id=target_id,
            local_rejections_by_identity=self.local_rejections_by_identity,
            valid_string_public_post_id=valid_string_public_post_id,
            _normalise_lane=_normalise_lane, bounded_event_text=bounded_event_text,
            bounded_event_string_list=bounded_event_string_list,
            add_event=self.add_event,
        )

    def add_receipt_event(self, kind: str, r: Record, **kwargs: Any) -> None:
        """Record a main-post receipt observation."""
        _add_receipt_event(
            kind, r, kwargs,
            input_file_indexes=self.input_file_indexes, stats=self.stats,
            short=short, is_selftest_log_path=is_selftest_log_path,
            record_source_ref=record_source_ref,
            receipt_events=self.receipt_events,
        )

    def add_confirmed_reply_receipt_event(self, kind: str, r: Record, **kwargs: Any) -> None:
        """Record a reply receipt within the selected source context."""
        self.source_context.pending_confirmed_reply_receipt = _add_confirmed_reply_receipt_event(
            kind, r, kwargs,
            input_file_indexes=self.input_file_indexes, stats=self.stats,
            short=short, is_selftest_log_path=is_selftest_log_path,
            record_source_ref=record_source_ref,
            confirmed_reply_receipts=self.confirmed_reply_receipts,
            pending_confirmed_reply_receipt=self.source_context.pending_confirmed_reply_receipt,
        )

    def add_asset_health(self, kind: str, r: Record, **kwargs: Any) -> None:
        """Collect an asset health observation and count its kind."""
        item = {
            "time": r.ts.strftime("%Y-%m-%d %H:%M:%S"),
            "kind": kind,
            "level": r.level,
            "message": short(r.msg, 500),
        }
        item.update(kwargs)
        self.asset_health.append(item)
        self.stats[f"asset_{kind}"] += 1

    def add_reply_media_context_event(self, r: Record, **kwargs: Any) -> None:
        """Collect reply media context for later incident reconciliation."""
        _add_reply_media_context_event(
            r, kwargs, reply_media_context=self.reply_media_context,
            stats=self.stats, short=short,
        )

    def conversational_evidence_fields(
        self,
        event_obj: Dict[str, Any],
        *,
        evidence_ids: Any,
        factual_claim_count: Any,
    ) -> Dict[str, Any]:
        """Project bounded conversational evidence onto an event."""
        return _conversational_evidence_fields(
            event_obj, evidence_ids=evidence_ids,
            factual_claim_count=factual_claim_count,
            bounded_event_string_list=bounded_event_string_list,
        )


    def begin_record(self, r: Record) -> None:
        """Select source context and parse a strict structured envelope."""
        self.current_source_record = r
        self.msg = r.msg
        self.production_record = not is_selftest_log_path(r.path)
        self.source_context = self.production_context if self.production_record else self.selftest_context
        self.strict_structured_event_obj = (
            try_parse_strict_json_object_from_msg(self.msg)
            if self.msg.startswith("EVENT ")
            else None
        )
        # Structured EVENT fields are projected into the JSON contract only
        # after duplicate-key, finite-number, UTF-8 and exact-envelope
        # validation. Only a failed strict parse needs compatibility detection
        # so malformed visual events retain bounded parser diagnostics without
        # supplying fields to structured handlers.
        diagnostic_event_obj = self.strict_structured_event_obj
        if diagnostic_event_obj is None and self.msg.startswith("EVENT "):
            diagnostic_event_obj = try_parse_json_object_from_msg(self.msg)
        self.is_reply_visual_description_event = bool(
            (
                diagnostic_event_obj
                and diagnostic_event_obj.get("event")
                == "reply_visual_description"
            )
            or (
                diagnostic_event_obj is None
                and self.msg.startswith("EVENT ")
                and re.search(
                    r'"event"\s*:\s*"reply_visual_description"', self.msg
                )
            )
        )

    def observe_transport(self, r: Record) -> None:
        """Observe X requests and remote-write transactions before routing."""
        request_start = (
            parse_x_request_start(self.msg)
            if self.production_record and r.src in {"x_request", "x_bearer_request"}
            else None
        )
        if request_start is not None:
            record_x_request_start(
                request_start, r, input_file_indexes=self.input_file_indexes,
                x_requests=self.x_requests,
                latest_x_request_by_source=self.latest_x_request_by_source,
                stats=self.stats, record_source_ref=record_source_ref,
            )

        transaction_event = (
            parse_remote_write_transaction_event(r) if self.production_record else None
        )
        if transaction_event is not None:
            record_remote_write_transaction(
                transaction_event, r, input_file_indexes=self.input_file_indexes,
                remote_write_transactions=self.remote_write_transactions,
                stats=self.stats, record_source_ref=record_source_ref,
                add_receipt_event=self.add_receipt_event,
            )

    def observe_logged_runtime(self, r: Record) -> None:
        """Collect lifecycle, configuration and logged-state observations."""
        # Lifecycle/config/state
        if self.production_record and (
            self.msg == "Bot starting"
            or self.msg == "Bot started successfully"
            or "Bot stopped by KeyboardInterrupt" in self.msg
            or "runtime control pause cleared" in self.msg.lower()
        ):
            self.lifecycle.append({"time": r.ts.strftime("%Y-%m-%d %H:%M:%S"), "level": r.level, "message": self.msg.splitlines()[0]})

        config_pairs = extract_config_pairs(self.msg) if self.production_record else {}
        if config_pairs:
            self.configs.update(config_pairs)

        if self.production_record and (self.msg.startswith("State being saved:") or self.msg.startswith("Loaded state:")):
            state = try_parse_json_object_from_msg(self.msg) or parse_partial_state_from_msg(self.msg)
            if state is not None:
                self.latest_state = state
                self.latest_state_ts = r.ts

    def observe_errors_and_provider(self, r: Record) -> None:
        """Classify warnings and observe provider messages before routing."""
        self.is_asset_metadata_warning, self.is_handled_reply_restriction = observe_error_warning(
            r, self.msg,
            self_test_errors=self.self_test_errors,
            confirmed_post_recovery=self.confirmed_post_recovery,
            confirmed_reply_recovery=self.confirmed_reply_recovery,
            errors=self.errors,
            pending_mention=self.source_context.pending_mention,
            pending_qt=self.source_context.pending_qt,
            pending_meme=self.source_context.pending_meme,
            pending_quote=self.source_context.pending_quote,
            is_reply_visual_description_event=self.is_reply_visual_description_event,
            input_file_indexes=self.input_file_indexes,
            is_reply_target_eligibility_restriction=is_reply_target_eligibility_restriction,
            is_deleted_or_inaccessible_tweet_403=is_deleted_or_inaccessible_tweet_403,
            add_or_merge_local_rejection=self._local_rejection_callback,
            short=short, record_source_ref=record_source_ref,
            record_fingerprint=record_fingerprint,
            classify_operational_error=classify_operational_error,
        )

        (
            self.source_context.active_xai_context,
            self.source_context.active_xai_call_attempt_index,
        ) = observe_provider_message(
            r, self.msg,
            pending_mention=self.source_context.pending_mention,
            pending_qt=self.source_context.pending_qt,
            active_xai_context=self.source_context.active_xai_context,
            active_xai_call_attempt_index=self.source_context.active_xai_call_attempt_index,
            xai_call_attempts=self.xai_call_attempts,
            xai_usage_events=self.xai_usage_events,
            xai_usage_parse_errors=self.xai_usage_parse_errors,
            stats=self.stats,
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

    def observe_structured_event(self, r: Record, record_index: int) -> bool:
        """Route strict EVENT records; they never enter legacy parsing."""
        # Strict structured EVENT lines provide immutable publication evidence;
        # older human-readable success lines still define the final digest event.
        if self.msg.startswith("EVENT "):
            event_obj = self.strict_structured_event_obj
            if self.is_reply_visual_description_event:
                visual_event = (
                    parse_reply_visual_description_event(event_obj)
                    if event_obj is not None
                    else None
                )
                if visual_event is None:
                    self.stats["reply_visual_description_malformed_events"] += 1
                else:
                    self.stats["reply_visual_description_events"] += 1
            elif event_obj and event_obj.get("event") == "main_post_posted":
                record_main_post_publication(
                    event_obj, self.strict_structured_event_obj, r.ts,
                    valid_string_public_post_id=valid_string_public_post_id,
                    SHA256_LOWER_RE=SHA256_LOWER_RE,
                    retain_quote_post_evidence=self.quote_publications.retain,
                    note_invalid_quote_post_evidence=self.quote_publications.note_invalid,
                    make_source_ref=lambda: record_source_ref(r, self.input_file_indexes),
                )
                if event_obj.get("lane") == "daily_meme":
                    self.source_context.pending_meme.update({
                        "post_id": event_obj.get("post_id"),
                        "file": event_obj.get("filename"),
                    })
            elif event_obj and event_obj.get("event") == "account_root_posted":
                record_account_root_publication(
                    event_obj, self.strict_structured_event_obj, r.ts,
                    valid_account_root_publication_identity=valid_account_root_publication_identity,
                    SHA256_LOWER_RE=SHA256_LOWER_RE,
                    valid_bounded_utf8_text=valid_bounded_utf8_text,
                    retain_quote_post_evidence=self.quote_publications.retain,
                    note_invalid_quote_post_evidence=self.quote_publications.note_invalid,
                    make_source_ref=lambda: record_source_ref(r, self.input_file_indexes),
                )
            elif event_obj and event_obj.get("event") == "historical_context_semantic_gate":
                record_historical_context_semantic_gate(
                    event_obj, r.ts, add_event=self.add_event
                )
            elif event_obj and event_obj.get("event") == "historical_context_runtime":
                record_historical_context_runtime(
                    event_obj, r.ts, self.stats, add_event=self.add_event
                )
            elif event_obj and event_obj.get("event") == "reply_evidence_unavailable":
                record_reply_evidence_unavailable(
                    event_obj, r.ts, self.stats, add_event=self.add_event,
                    bounded_event_text=bounded_event_text,
                    valid_string_public_post_id=valid_string_public_post_id,
                )
            elif event_obj and event_obj.get("event") == "runtime_control_pause":
                record_runtime_control_pause(
                    event_obj, r.ts, self.stats, add_event=self.add_event,
                    bounded_event_string_list=bounded_event_string_list,
                    bounded_event_text=bounded_event_text,
                    bounded_event_nonnegative_integer=bounded_event_nonnegative_integer,
                )
            elif event_obj and event_obj.get("event") == "runtime_control_clear":
                record_runtime_control_clear(
                    event_obj, r.ts, self.stats, add_event=self.add_event,
                    bounded_event_string_list=bounded_event_string_list,
                    bounded_event_text=bounded_event_text,
                )
            elif event_obj and event_obj.get("event") == "clarification_reply_cap_override":
                record_clarification_reply_cap_override(
                    event_obj, r.ts, self.stats, add_event=self.add_event,
                    valid_string_public_post_id=valid_string_public_post_id,
                    bounded_event_text=bounded_event_text,
                )
            elif event_obj and event_obj.get("event") == "clarification_reply_used":
                record_clarification_reply_used(
                    event_obj, r.ts, self.stats, add_event=self.add_event,
                    valid_string_public_post_id=valid_string_public_post_id,
                    bounded_event_text=bounded_event_text,
                )
            elif event_obj and event_obj.get("event") == "repair_reply_completed":
                record_repair_reply_completed(
                    event_obj, r.ts, self.stats, add_event=self.add_event,
                    valid_string_public_post_id=valid_string_public_post_id,
                )
            elif event_obj and event_obj.get("event") in {
                "mention_backlog_started",
                "mention_backlog_progress",
                "mention_backlog_completed",
                "mention_backlog_reset",
            }:
                record_mention_backlog(
                    event_obj, r.ts, add_event=self.add_event, stats=self.stats,
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
                    event_obj, r.ts, add_event=self.add_event, stats=self.stats,
                    valid_string_public_post_id=valid_string_public_post_id,
                    bounded_event_nonnegative_integer=bounded_event_nonnegative_integer,
                )
            elif event_obj and event_obj.get("event") == "reply_posted":
                confirmation = prepare_structured_reply_confirmation(
                    self.strict_structured_event_obj,
                    production_record=self.production_record,
                    time_text=lambda: dt_text(r.ts),
                    event_insertion_index=lambda: len(self.events),
                    source_sequence=record_index,
                    make_source_ref=lambda: record_source_ref(r, self.input_file_indexes),
                )
                if confirmation is not None:
                    self.structured_reply_confirmations.append(confirmation)
            elif (
                event_obj
                and event_obj.get("event")
                == "historical_context_reply_posted"
            ):
                self.historical_reply_text_evidence.append(
                    prepare_structured_historical_publication_evidence(
                        event_obj, self.strict_structured_event_obj,
                        production_record=self.production_record,
                        valid_string_public_post_id=lambda value: valid_string_public_post_id(value),
                        valid_bounded_utf8_text=lambda value, **kwargs: valid_bounded_utf8_text(value, **kwargs),
                        sha256_fullmatch=lambda value: SHA256_LOWER_RE.fullmatch(value),
                        time_text=lambda: dt_text(r.ts),
                        event_insertion_index=lambda: len(self.events),
                        source_sequence=record_index,
                        make_source_ref=lambda: record_source_ref(r, self.input_file_indexes),
                    )
                )
            elif event_obj and event_obj.get("event") == "historical_context_reply":
                historical_fields = prepare_historical_context_reply(event_obj)
                status = historical_fields["status"]
                historical_event = self.add_event(
                    "historical_context_reply", r.ts, **historical_fields
                )
                if status in {"completed", "already_completed"}:
                    anchor_valid = valid_structured_historical_completion_anchor(
                        self.strict_structured_event_obj, status,
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
                        self.production_event_object_ids.discard(
                            id(historical_event)
                        )
                count_historical_context_reply(status, self.stats)
            elif event_obj and event_obj.get("event") == "posting_transaction_state":
                record_posting_transaction_state(
                    event_obj, r.ts, self.stats, add_event=self.add_event,
                    bounded_event_text=bounded_event_text,
                    valid_string_public_post_id=valid_string_public_post_id,
                    bounded_event_boolean=bounded_event_boolean,
                )
            elif event_obj and event_obj.get("event") == "historical_context_obligation":
                record_historical_context_obligation(
                    event_obj, r.ts, self.stats, add_event=self.add_event
                )
            elif event_obj and event_obj.get("event") == "historical_context_outbox":
                record_historical_context_outbox(
                    event_obj, r.ts, self.stats, add_event=self.add_event
                )
            elif event_obj and event_obj.get("event") == "daily_meme_failure":
                record_daily_meme_failure(
                    event_obj, r.ts, self.stats, add_event=self.add_event,
                    bounded_event_text=bounded_event_text,
                    valid_string_public_post_id=valid_string_public_post_id,
                )
            elif event_obj and event_obj.get("event") == "reply_strategy_decision":
                record_reply_strategy_decision(
                    event_obj, r.ts,
                    add_event=self.add_event,
                    conversational_evidence_fields=self.conversational_evidence_fields,
                )
            elif event_obj and event_obj.get("event") == "reply_strategy_outcome":
                record_reply_strategy_outcome(
                    event_obj, r.ts,
                    add_event=self.add_event,
                    conversational_evidence_fields=self.conversational_evidence_fields,
                )
            elif event_obj and event_obj.get("event") == "reply_target_terminal":
                record_reply_target_terminal(
                    event_obj, r.ts,
                    add_event=self.add_event,
                )
            elif event_obj and event_obj.get("event") == "reply_strategy_rejection":
                record_reply_strategy_rejection(
                    event_obj, r.ts,
                    add_event=self.add_event,
                )
            elif event_obj and event_obj.get("event") == "single_call_reply_decision":
                record_single_call_reply_decision(
                    event_obj, r.ts, add_event=self.add_event,
                )
            elif event_obj and event_obj.get("event") == "single_call_reply_provider_usage":
                record_single_call_reply_provider_usage(
                    event_obj, r.ts, add_event=self.add_event,
                )
            elif event_obj and event_obj.get("event") == "single_call_reply_posting_outcome":
                record_single_call_reply_posting_outcome(
                    event_obj, r.ts, add_event=self.add_event,
                )
            elif event_obj and event_obj.get("event") == "single_call_reply_draft_recovered":
                record_single_call_reply_draft_recovered(
                    event_obj, r.ts, add_event=self.add_event,
                )
            elif event_obj and event_obj.get("event") in {
                "provider_request_prepared",
                "provider_request_recording_failed",
                "provider_request_attempt_started",
                "provider_request_attempt_outcome",
            }:
                record_provider_request_lifecycle(
                    event_obj, r.ts, add_event=self.add_event,
                )
            elif event_obj and event_obj.get("event") == "ai_reply_pipeline_decision":
                record_ai_reply_pipeline_decision(
                    event_obj, r.ts,
                    add_event=self.add_event,
                    add_or_merge_local_rejection=self.add_or_merge_local_rejection,
                    conversational_evidence_fields=self.conversational_evidence_fields,
                    bounded_event_string_list=bounded_event_string_list,
                )
            elif event_obj and event_obj.get("event") == "ai_reply_pipeline_stage_summary":
                record_ai_reply_pipeline_stage_summary(
                    event_obj, r.ts,
                    add_event=self.add_event,
                    bounded_event_string_list=bounded_event_string_list,
                    normalise_majority_review_telemetry=normalise_majority_review_telemetry,
                )
            elif event_obj and event_obj.get("event") == "ai_reply_pipeline_effective_outcome":
                record_ai_reply_pipeline_effective_outcome(
                    event_obj, r.ts,
                    add_or_merge_local_rejection=self.add_or_merge_local_rejection,
                )
            elif event_obj and event_obj.get("event") == "ai_reply_pipeline_failure":
                record_ai_reply_pipeline_failure(
                    event_obj, r.ts,
                    add_event=self.add_event,
                )
            elif event_obj and event_obj.get("event") == "ai_reply_pipeline_outcome":
                record_ai_reply_pipeline_outcome(
                    event_obj, r.ts,
                    add_event=self.add_event,
                    conversational_evidence_fields=self.conversational_evidence_fields,
                    bounded_event_string_list=bounded_event_string_list,
                )
            elif event_obj and event_obj.get("event") == "quote_pagination_repeated_token":
                self.add_event(
                    "quote_pagination_repeated_token",
                    r.ts,
                    post_id=(event_obj.get("post_id") if valid_string_public_post_id(event_obj.get("post_id")) else ""),
                    token_fingerprint=bounded_event_text(event_obj.get("token_fingerprint"), default="", max_characters=200),
                    pages_completed=bounded_event_nonnegative_integer(event_obj.get("pages_completed"), maximum=1_000_000),
                    results_retained=bounded_event_nonnegative_integer(event_obj.get("results_retained"), maximum=1_000_000),
                )
            elif event_obj and event_obj.get("event") == "candidate_skipped":
                self.add_event(
                    "candidate_skipped", r.ts,
                    lane=bounded_event_text(event_obj.get("lane"), default="unavailable", max_characters=100),
                    target_id=(event_obj.get("id") if valid_string_public_post_id(event_obj.get("id")) else ""),
                    reason=bounded_event_text(event_obj.get("reason"), default="other", max_characters=500),
                )
            return True
        return False

    def observe_legacy_evidence(self, r: Record) -> bool:
        """Observe receipt, media, cooldown and API evidence in original order."""
        quote_success = re.match(
            r"Fetched \d+ quote tweet\(s\) for post_id=(\d+)$",
            self.msg,
        )
        if quote_success:
            self.add_event(
                "quote_lane_activity_succeeded",
                r.ts,
                post_id=quote_success.group(1),
            )
            self.add_event("x_activity_succeeded", r.ts, activity="quote_lookup")
            return True
        if re.match(r"Fetched \d+ mentions$", self.msg):
            self.add_event("x_activity_succeeded", r.ts, activity="mention_lookup")
            return True

        if handle_legacy_receipt_message(
            r, self.msg,
            pending_confirmed_reply_receipt=self.source_context.pending_confirmed_reply_receipt,
            add_receipt_event=self.add_receipt_event,
            add_confirmed_reply_receipt_event=self.add_confirmed_reply_receipt_event,
        ):
            return True

        if handle_legacy_reply_media_context_message(
            r, self.msg, add_reply_media_context_event=self.add_reply_media_context_event,
        ):
            return True

        if self.is_asset_metadata_warning and r.level in {"ERROR", "CRITICAL", "WARNING"}:
            kind = "metadata_warning"
            if "Quote analysis" in self.msg or "quote analysis" in self.msg or "Skipping unanalysed current quote" in self.msg:
                kind = "quote_metadata_warning"
            elif "Image analysis" in self.msg or "image analysis" in self.msg or "Image metadata" in self.msg or "image analysis" in self.msg:
                kind = "image_metadata_warning"
            self.add_asset_health(kind, r)
            return True

        if handle_cooldown_message(
            r, self.msg, stats=self.stats, cooldown_active=self.cooldown_active,
            add_event=self.add_event,
        ):
            return True
        m = re.search(r"Migrated legacy pickle file (.+) to JSON file (.+)$", self.msg)
        if m:
            self.add_event("used_history_migrated", r.ts, legacy_file=m.group(1).strip(), json_file=m.group(2).strip())
            return True
        m = re.search(r"Normalized used-history JSON ordering in (.+)$", self.msg)
        if m:
            self.add_event("used_history_normalized", r.ts, json_file=m.group(1).strip())
            return True
        if handle_x_api_error(
            r, self.msg, latest_x_request_by_source=self.latest_x_request_by_source,
            pending_mention=self.source_context.pending_mention,
            pending_qt=self.source_context.pending_qt,
            is_handled_reply_restriction=self.is_handled_reply_restriction,
            api_errors=self.api_errors, handled_api_restrictions=self.handled_api_restrictions,
            stats=self.stats, input_file_indexes=self.input_file_indexes,
            parse_dt=parse_dt, seconds_between=seconds_between, short=short,
            record_source_ref=record_source_ref,
            is_deleted_or_inaccessible_tweet_403=is_deleted_or_inaccessible_tweet_403,
        ):
            return True
        self.source_context.active_xai_context = observe_provider_error(
            r, self.msg, active_xai_context=self.source_context.active_xai_context,
            api_errors=self.api_errors, stats=self.stats,
            input_file_indexes=self.input_file_indexes, short=short,
            record_source_ref=record_source_ref,
        )
        enrich_latest_api_error(self.msg, api_errors=self.api_errors, stats=self.stats)
        return False

    def observe_legacy_posting(self, r: Record, record_index: int) -> bool:
        """Route legacy publication and reply observations in original order."""
        if handle_legacy_quiet_message(
            r, self.msg,
            stats=self.stats, add_event=self.add_event,
        ):
            return True

        if handle_legacy_quote_image_selection(
            r, self.msg,
            pending_quote=self.source_context.pending_quote,
            regular_image_usage_events=self.regular_image_usage_events, add_event=self.add_event,
        ):
            return True

        if "ORIGINAL_EDITORIAL_SELECTION_RESULT " in self.msg:
            record_original_editorial_selection(
                self.msg, r.ts, r.level,
                observations=self.original_editorial_shadow_events,
                pending_shadow_companions=self.pending_original_editorial_shadow_companions,
                stats=self.stats, errors=self.errors,
                parse_json_object=_strict_native_json_object,
                short_text=short,
                source_ref=lambda: record_source_ref(r, self.input_file_indexes),
            )
            return True

        if "ORIGINAL_EDITORIAL_SHADOW_RESULT " in self.msg:
            record_original_editorial_shadow(
                self.msg, r.ts, r.level,
                observations=self.original_editorial_shadow_events,
                pending_shadow_companions=self.pending_original_editorial_shadow_companions,
                stats=self.stats, errors=self.errors,
                parse_json_object=_strict_native_json_object,
                short_text=short,
                source_ref=lambda: record_source_ref(r, self.input_file_indexes),
            )
            return True

        if "GENERATED_IDENTITY_POLICY_SHADOW_RESULT " in self.msg:
            record_generated_identity_shadow(
                self.msg, r.ts, r.level,
                observations=self.generated_identity_shadow_events,
                stats=self.stats, errors=self.errors,
                parse_json_object=_strict_native_json_object,
                short_text=short,
                source_ref=lambda: record_source_ref(r, self.input_file_indexes),
            )
            return True

        if "GENERATED_IDENTITY_POLICY_APPLIED " in self.msg:
            record_generated_identity_policy(
                self.msg, r.ts, r.level,
                observations=self.generated_identity_policy_events,
                stats=self.stats, errors=self.errors,
                parse_json_object=_strict_native_json_object,
                short_text=short,
                source_ref=lambda: record_source_ref(r, self.input_file_indexes),
            )
            return True

        handled, self.latest_generated_image_spacing = handle_legacy_generated_image_spacing(
            r, self.msg,
            latest_generated_image_spacing=self.latest_generated_image_spacing,
            generated_image_spacing_events=self.generated_image_spacing_events,
        )
        if handled:
            return True

        handled, self.source_context.pending_quote = handle_legacy_quote_image_posting(
            r, self.msg,
            pending_quote=self.source_context.pending_quote,
            regular_image_usage_events=self.regular_image_usage_events, add_event=self.add_event,
            lit=lit,
        )
        if handled:
            return True

        meme_availability_kind = {
            "All meme candidates have already been posted": "meme_cycle_exhausted",
            "No meme available to post": "meme_unavailable",
            "RESET_MEME_CYCLE_WHEN_ALL_POSTED=True, clearing meme history": "meme_cycle_recycled",
        }.get(self.msg)
        if self.production_record and meme_availability_kind:
            self.add_event(meme_availability_kind, r.ts, message=self.msg)
            if meme_availability_kind != "meme_cycle_recycled":
                self.add_asset_health(meme_availability_kind, r, severity="warning")
            return True

        handled, self.source_context.pending_meme = handle_legacy_meme_posting(
            r, self.msg,
            pending_meme=self.source_context.pending_meme, add_event=self.add_event, lit=lit,
        )
        if handled:
            return True

        handled, self.source_context.last_created_post = handle_legacy_created_post(
            r, self.msg,
            production_record=self.production_record,
            last_created_post=self.source_context.last_created_post,
            stats=self.stats, production_event_object_ids=self.production_event_object_ids,
            add_event=self.add_event, try_parse_response_id_text=try_parse_response_id_text,
            response_post_id_is_canonical_string=response_post_id_is_canonical_string,
        )
        if handled:
            return True

        (
            handled,
            self.source_context.pending_mention,
            self.source_context.active_xai_context,
        ) = handle_legacy_mention_reply(
            r, self.msg,
            record_index=record_index, production_record=self.production_record,
            pending_mention=self.source_context.pending_mention,
            active_xai_context=self.source_context.active_xai_context,
            last_created_post=self.source_context.last_created_post,
            production_event_object_ids=self.production_event_object_ids,
            routine_skip_counts=self.routine_skip_counts, add_event=self.add_event, lit=lit,
        )
        if handled:
            return True

        (
            handled,
            self.source_context.pending_qt,
            self.source_context.active_xai_context,
        ) = handle_legacy_quote_reply(
            r, self.msg,
            record_index=record_index, production_record=self.production_record,
            pending_qt=self.source_context.pending_qt,
            active_xai_context=self.source_context.active_xai_context,
            last_created_post=self.source_context.last_created_post,
            production_event_object_ids=self.production_event_object_ids,
            routine_skip_counts=self.routine_skip_counts, add_event=self.add_event, lit=lit,
        )
        if handled:
            return True
        return False

    def observe_routine_skip(self, r: Record) -> None:
        """Count the remaining routine availability messages."""
        # Other interesting skip/rate/cap messages.
        if self.msg in {
            "Daily generated/replied cap reached",
            "Skipping mention check: minimum interval between replies not reached",
            "Skipping quote-tweet check: total daily reply cap reached",
            "Skipping quote-tweet check: daily quote-reply cap reached",
        }:
            self.routine_skip_counts[self.msg] += 1
            if self.msg == "Skipping mention check: minimum interval between replies not reached":
                self.stats["mention_checks_skipped_spacing"] += 1

    def observe(self) -> None:
        """Preserve the record observer order and every routing short circuit."""
        for record_index, r in enumerate(self.records):
            self.begin_record(r)
            self.observe_transport(r)
            self.observe_logged_runtime(r)
            self.observe_errors_and_provider(r)
            if self.observe_structured_event(r, record_index):
                continue
            if self.observe_legacy_evidence(r):
                continue
            if self.observe_legacy_posting(r, record_index):
                continue
            self.observe_routine_skip(r)

    def reconcile_observations(self) -> None:
        """Reconcile publication, media, receipt and operational error evidence."""
        self.source_context = self.production_context
        self.current_source_record = None

        self.quote_publications.prepare_report(
            self.events, self.production_event_object_ids,
        )

        self.latest_state_summary = {}
        if self.latest_state is not None:
            self.latest_state_summary = summarize_latest_state(self.latest_state, self.latest_state_ts)

        self_test_times = {str(item.get("time")) for item in self.self_test_errors}
        api_error_times = {str(item.get("time")) for item in self.api_errors}
        self.handled_restriction_times = [
            datetime.strptime(str(item["time"]), "%Y-%m-%d %H:%M:%S")
            for item in self.handled_api_restrictions
            if item.get("time")
        ]
        self.media_upload_incidents, self.errors = prepare_media_incidents_and_errors(
            records=self.records,
            max_text=self.max_text,
            input_file_indexes=self.input_file_indexes,
            remote_write_transactions=self.remote_write_transactions,
            x_requests=self.x_requests,
            current_remote_write_safety=self.current_remote_write_safety,
            errors=self.errors,
            self_test_errors=self.self_test_errors,
            self_test_times=self_test_times,
            api_error_times=api_error_times,
            handled_restriction_times=self.handled_restriction_times,
            correlate_media_upload_incidents=correlate_media_upload_incidents,
            parse_dt=parse_dt,
            strptime=datetime.strptime,
            seconds_between=seconds_between,
        )

        append_unresolved_reply_receipt_errors(
            confirmed_reply_receipts=self.confirmed_reply_receipts,
            errors=self.errors,
        )

        self.error_health = summarise_operational_error_health(
            self.errors,
            self.events,
            self.receipt_events,
            self.lifecycle,
            remote_write_transactions=self.remote_write_transactions,
            handled_api_restrictions=self.handled_api_restrictions,
            confirmed_reply_receipt_events=self.confirmed_reply_receipts,
            current_remote_write_safety=self.current_remote_write_safety,
            generation_time=self.generation_time,
            selected_window_end=self.selected_window_end,
            current_snapshot_authoritative=self.current_snapshot_authoritative,
        )
        (
            self.durably_reconciled_reply_receipts,
            self.status_unavailable_reply_receipts,
            self.active_snapshot_reply_receipts,
        ) = prepare_reply_receipt_recovery_reporting(
            error_health=self.error_health,
            current_remote_write_safety=self.current_remote_write_safety,
            confirmed_reply_receipts=self.confirmed_reply_receipts,
            parse_dt=parse_dt,
            _normalise_lane=_normalise_lane,
            REMOTE_WRITE_RECEIPT_ROLE_LABELS=REMOTE_WRITE_RECEIPT_ROLE_LABELS,
        )

    def prepare_report_sections(self) -> None:
        """Calculate headline, API health, quality and mention-control sections."""
        # Build a short automatic headline around current health, not raw traceback volume.
        (
            self.headline_components, self.transient_provider_timeouts, self.handled_media_fallbacks,
            self.reconciled_media_uploads, self.unrecovered_media, self.derived,
        ) = prepare_headline_and_derived(
            stats=self.stats, error_health=self.error_health,
            current_remote_write_safety=self.current_remote_write_safety,
            handled_api_restrictions=self.handled_api_restrictions,
            media_upload_incidents=self.media_upload_incidents,
            self_test_errors=self.self_test_errors,
            confirmed_post_recovery=self.confirmed_post_recovery,
            confirmed_reply_recovery=self.confirmed_reply_recovery,
            receipt_events=self.receipt_events, asset_health=self.asset_health,
            latest_state_summary=self.latest_state_summary, records=self.records, configs=self.configs,
            plural_count=plural_count, int_or_none=int_or_none, parse_dt=parse_dt,
        )

        self.api_health_preparation = prepare_api_health(
            api_errors=self.api_errors, handled_api_restrictions=self.handled_api_restrictions,
            x_requests=self.x_requests, remote_write_transactions=self.remote_write_transactions,
            events=self.events, production_event_object_ids=self.production_event_object_ids,
            quote_post_correlations=self.quote_publications.evidence,
            structured_reply_confirmations=self.structured_reply_confirmations,
            historical_reply_text_evidence=self.historical_reply_text_evidence,
            transient_provider_timeouts=self.transient_provider_timeouts,
            handled_restriction_times=self.handled_restriction_times,
            event_counter=Counter, bounded_event_text=bounded_event_text,
            SHA256_LOWER_RE=SHA256_LOWER_RE,
            valid_string_public_post_id=valid_string_public_post_id,
            _normalised_structured_reply_confirmation=_normalised_structured_reply_confirmation,
            seconds_between=seconds_between, parse_dt=parse_dt,
            strptime=datetime.strptime, datetime_min=datetime.min,
        )

        prepare_inferred_reply_strategy_outcomes(
            events=self.events, handled_api_restrictions=self.handled_api_restrictions,
            _normalise_lane=_normalise_lane, parse_dt=parse_dt, add_event=self.add_event,
        )

        self.context_quality = historical_context_quality_summary(self.events)
        self.single_call_quality = single_call_reply_summary(self.events)
        (
            self.legacy_multi_stage, self.headline, self.headline_without_current_cooldown,
            self.headline_components,
        ) = prepare_reply_quality_headline(
            events=self.events, headline=self.headline_components,
            single_call_quality=self.single_call_quality,
            plural_count=plural_count,
        )

        (
            self.mention_control_events,
            self.mention_control_counts,
            self.pipeline_evaluations_skipped,
        ) = prepare_mention_control_observations(self.events, event_counter=Counter)

    def build_report(self) -> None:
        """Assemble the stable JSON report sections from prepared observations."""
        assert self.api_health_preparation is not None
        summary: SummarySection = {
                "record_count": len(self.records),
                "time_start": self.records[0].ts.strftime("%Y-%m-%d %H:%M:%S") if self.records else None,
                "time_end": self.records[-1].ts.strftime("%Y-%m-%d %H:%M:%S") if self.records else None,
                "headline": "; ".join(self.headline),
                "_headline_without_current_cooldown": (
                    self.headline_without_current_cooldown
                ),
                "_headline_components": self.headline_components,
                "stats": dict(self.stats),
                "routine_skip_counts": dict(self.routine_skip_counts),
        }
        mention_backlog: MentionBacklogSection = {
            "events": self.mention_control_events,
            "event_counts": dict(sorted(self.mention_control_counts.items())),
            "pipeline_evaluations_skipped": self.pipeline_evaluations_skipped,
        }
        main_post_recovery: MainPostRecoverySection = {
            "receipt_events": self.receipt_events,
            "confirmed_post_recovery": self.confirmed_post_recovery,
        }
        confirmed_reply_recovery: ConfirmedReplyRecoverySection = {
            "receipt_events": self.confirmed_reply_receipts,
            "warnings": self.confirmed_reply_recovery,
            "durably_reconciled_ambiguity_receipts": self.durably_reconciled_reply_receipts,
            "status_unavailable_receipts": self.status_unavailable_reply_receipts,
            "active_snapshot_receipts": self.active_snapshot_reply_receipts,
        }
        resume_context: ResumeContext = {
            "active_xai_context": self.source_context.active_xai_context,
            "active_xai_call_attempt": (
                dict(self.xai_call_attempts[self.source_context.active_xai_call_attempt_index])
                if self.source_context.active_xai_call_attempt_index is not None
                and self.xai_call_attempts[self.source_context.active_xai_call_attempt_index].get(
                    "usage_observed"
                ) is not True
                else None
            ),
            "pending_mention": (
                {
                    key: value
                    for key, value in self.source_context.pending_mention.items()
                    if not key.startswith("_")
                }
                if self.source_context.pending_mention
                else None
            ),
            "pending_qt": (
                {
                    key: value
                    for key, value in self.source_context.pending_qt.items()
                    if not key.startswith("_")
                }
                if self.source_context.pending_qt
                else None
            ),
        }
        self.report = DigestReport({
            "summary": summary,
            "latest_config": self.configs,
            "latest_state": self.latest_state_summary,
            "derived": self.derived,
            "mention_backlog_and_quarantine": mention_backlog,
            "api_health": api_health_report(
                self.api_health_preparation, api_errors=self.api_errors,
                handled_api_restrictions=self.handled_api_restrictions,
                cooldown_active=self.cooldown_active, x_requests=self.x_requests,
            ),
            "main_post_recovery": main_post_recovery,
            "remote_write_transactions": self.remote_write_transactions,
            "confirmed_reply_recovery": confirmed_reply_recovery,
            "quote_publication": {
                "correlation_warnings": self.quote_publications.warnings,
                "correlation_warning_omitted_count": (
                    self.quote_publications.warning_omitted_count
                ),
            },
            "historical_context_replies": {
                "events": [item for item in self.events if item.get("kind") == "historical_context_reply"],
                "status_counts": {
                    key.removeprefix("historical_context_reply_status_"): value
                    for key, value in sorted(self.stats.items())
                    if key.startswith("historical_context_reply_status_")
                },
            },
            "production_consistency": production_consistency_report(self.events, self.stats),
            "historical_context_quality": self.context_quality,
            "single_call_reply": self.single_call_quality,
            "provider_request_correlations": [
                item for item in self.events
                if item.get("kind") in {
                    "single_call_reply_decision",
                    "single_call_reply_provider_usage",
                    "single_call_reply_posting_outcome",
                    "single_call_reply_draft_recovered",
                    "provider_request_prepared",
                    "provider_request_recording_failed",
                    "provider_request_attempt_started",
                    "provider_request_attempt_outcome",
                }
            ],
            "legacy_multi_stage": self.legacy_multi_stage,
            "asset_health": self.asset_health,
            "media_upload": {
                "incidents": self.media_upload_incidents,
                "handled_fallbacks": self.handled_media_fallbacks,
                "reconciled_incidents": self.reconciled_media_uploads,
                "unrecovered_failures": self.unrecovered_media,
            },
            "regular_image_usage": {
                "events": self.regular_image_usage_events,
                "summary": regular_image_usage_summary(self.regular_image_usage_events),
            },
            "original_editorial_shadow": {
                "events": self.original_editorial_shadow_events,
                "summary": original_editorial_shadow_summary(self.original_editorial_shadow_events),
            },
            "generated_identity_shadow": {
                "events": self.generated_identity_shadow_events,
                "summary": generated_identity_shadow_summary(self.generated_identity_shadow_events),
            },
            "generated_identity_policy": {
                "events": self.generated_identity_policy_events,
                "summary": generated_identity_policy_summary(self.generated_identity_policy_events),
            },
            "generated_image_spacing": {
                "latest": self.latest_generated_image_spacing,
                "events": self.generated_image_spacing_events,
            },
            "resume_context": resume_context,
            "lifecycle": self.lifecycle[-12:],
            "events": self.events,
            "self_test_errors": self.self_test_errors[-40:],
            "error_health": self.error_health,
            "errors_and_warnings": [
                {
                    key: value
                    for key, value in item.items()
                    if not key.startswith("_")
                }
                for item in self.errors[-40:]
            ],
        })

    def complete_report(self) -> DigestReport:
        """Enrich exact reply text, bound display prose and attach lifecycle summaries."""
        enrich_published_reply_text(
            self.report,
            runtime_state=self.current_runtime_state,
            structured_reply_confirmations=self.structured_reply_confirmations,
            historical_reply_text_evidence=self.historical_reply_text_evidence,
            confirmed_receipt_evidence=self.confirmed_receipt_evidence,
            durable_evidence_status=self.durable_reply_evidence_status,
            production_event_object_ids=self.production_event_object_ids,
        )
        # Prose previews can participate in analysis (notably historical-context
        # classification), so shorten them only once every summary and correlation
        # has consumed the original values. Semantic fields and separately bounded
        # public/rejected reply evidence remain exact.
        for event in self.events:
            for field in (
                "text", "incoming_text", "incoming_contribution", "reply_preview", "proposed_draft",
                "repaired_draft", "summary", "components",
            ):
                value = event.get(field)
                if field == "text" and event.get("public_text_status") == "confirmed":
                    continue
                if isinstance(value, str):
                    event[field] = short(value, self.max_text)
        main_post_lifecycle, reply_lifecycle = _receipt_lifecycle_summaries(
            self.report, complete_report=self.report,
        )
        report_section(self.report, "main_post_recovery")["receipt_lifecycle"] = main_post_lifecycle
        report_section(self.report, "confirmed_reply_recovery")["receipt_lifecycle"] = reply_lifecycle
        return self.report

    def finalize(self) -> DigestReport:
        """Reconcile the complete record stream and build its report."""
        self.reconcile_observations()
        self.prepare_report_sections()
        self.build_report()
        return self.complete_report()

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
    current_runtime_state: Optional[RuntimeStateSnapshot] = None,
    input_file_indexes: Optional[Dict[str, int]] = None,
    confirmed_receipt_evidence: Optional[List[Dict[str, Any]]] = None,
    historical_history_evidence: Optional[List[Dict[str, Any]]] = None,
    durable_reply_evidence_status: Optional[Dict[str, Any]] = None,
) -> DigestReport:
    """Aggregate parsed production records into digest metrics."""
    analysis = DigestAnalysis(
        records, max_text,
        initial_active_xai_context=initial_active_xai_context,
        initial_active_xai_call_attempt=initial_active_xai_call_attempt,
        initial_pending_mention=initial_pending_mention,
        initial_pending_qt=initial_pending_qt,
        current_remote_write_safety=current_remote_write_safety,
        generation_time=generation_time,
        selected_window_end=selected_window_end,
        current_snapshot_authoritative=current_snapshot_authoritative,
        current_runtime_state=current_runtime_state,
        input_file_indexes=input_file_indexes,
        confirmed_receipt_evidence=confirmed_receipt_evidence,
        historical_history_evidence=historical_history_evidence,
        durable_reply_evidence_status=durable_reply_evidence_status,
    )
    analysis.observe()
    return analysis.finalize()


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


def _receipt_lifecycle_summaries(
    report: Dict[str, Any],
    *,
    complete_report: Optional[DigestReport] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Reuse prepared summaries or derive them for older, unprepared reports."""
    main_recovery = (
        report_section(complete_report, "main_post_recovery")
        if complete_report is not None else report.get("main_post_recovery") or {}
    )
    main_post_lifecycle = main_recovery.get("receipt_lifecycle")
    if main_post_lifecycle is None:
        receipt_events = main_recovery.get("receipt_events") or []
        main_post_lifecycle = (
            summarise_main_post_receipt_lifecycle(receipt_events)
            if receipt_events else {}
        )
    reply_recovery = (
        report_section(complete_report, "confirmed_reply_recovery")
        if complete_report is not None else report.get("confirmed_reply_recovery") or {}
    )
    reply_lifecycle = reply_recovery.get("receipt_lifecycle")
    if reply_lifecycle is None:
        reply_lifecycle = summarise_reply_receipt_lifecycle(
            reply_recovery.get("receipt_events") or [],
            reconciled_ambiguity_receipts=(
                reply_recovery.get("durably_reconciled_ambiguity_receipts") or []
            ),
            unavailable_receipts=(
                reply_recovery.get("status_unavailable_receipts") or []
            ),
            normalise_lane=_normalise_lane,
        )
    return main_post_lifecycle, reply_lifecycle


def render_markdown(report: Dict[str, Any]) -> str:
    """Render a prepared digest, also accepting older reports without summaries."""
    main_post_lifecycle, reply_lifecycle = _receipt_lifecycle_summaries(report)
    return _render_digest_markdown(
        report,
        main_post_receipt_lifecycle=main_post_lifecycle,
        reply_receipt_lifecycle=reply_lifecycle,
    )


def _fsync_directory(path: Path) -> None:
    """Persist directory entries after creating or replacing a digest file."""
    directory_fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _mkdir_durable(path: Path) -> None:
    """Create an output directory and persist any new ancestor entries."""
    missing: List[Path] = []
    ancestor = path
    while not ancestor.exists():
        missing.append(ancestor)
        ancestor = ancestor.parent
    path.mkdir(parents=True, exist_ok=True)
    for created in reversed(missing):
        _fsync_directory(created.parent)


def deliver_report(rendered: str, output_path: Optional[Path] = None) -> None:
    """Deliver a complete report before the caller advances resume state."""
    if output_path is None:
        sys.stdout.write(rendered)
        sys.stdout.flush()
        return
    output_path = output_path.expanduser().resolve()
    _mkdir_durable(output_path.parent)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{output_path.name}.",
        suffix=".tmp",
        dir=output_path.parent,
        text=True,
    )
    tmp = Path(tmp_name)
    try:
        try:
            handle = os.fdopen(fd, "w", encoding="utf-8")
        except BaseException:
            os.close(fd)
            raise
        with handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, output_path)
        _fsync_directory(output_path.parent)
    finally:
        tmp.unlink(missing_ok=True)


def validate_output_destinations(
    output_paths: Iterable[Path],
    logs: Iterable[Path],
    state_file: Path,
    *,
    lock_paths: Iterable[Path] = (),
) -> None:
    """Reject output and lock aliases before any digest lock is opened."""
    def aliases(first: Path, second: Path) -> bool:
        if first.resolve() == second.resolve():
            return True
        try:
            return first.samefile(second)
        except FileNotFoundError:
            return False

    resolved_outputs = [path.expanduser().resolve() for path in output_paths]
    resolved_logs = [path.expanduser().resolve() for path in logs]
    resolved_state = state_file.expanduser().resolve()
    for index, output in enumerate(resolved_outputs):
        if any(aliases(output, other) for other in resolved_outputs[:index]):
            raise SystemExit(f"Refusing to write digest: output destinations alias each other: {output}")
        if any(aliases(output, log) for log in resolved_logs):
            raise SystemExit(f"Refusing to write digest: output path aliases an input log: {output}")
        if aliases(output, resolved_state):
            raise SystemExit(
                f"Refusing to write digest: output path aliases the resume-state file: {output}"
            )
    for lock in lock_paths:
        if aliases(lock, resolved_state):
            raise SystemExit(f"Refusing to write digest: lock path aliases the resume-state file: {lock}")
        if any(aliases(lock, log) for log in resolved_logs):
            raise SystemExit(f"Refusing to write digest: lock path aliases an input log: {lock}")
        if any(aliases(lock, output) for output in resolved_outputs):
            raise SystemExit(f"Refusing to write digest: output paths must not alias digest lock files: {lock}")


def distinct_digest_lock_paths(lock_paths: Iterable[Path]) -> List[Path]:
    """Acquire each requested lock inode once, in deterministic pathname order.

    Validate every requested path against data files before calling this helper.
    Identity inspection is a preflight snapshot; the no-follow open and flock
    remain the acquisition authorities if a path changes afterwards.
    """
    distinct: List[Path] = []
    seen_inodes: set[Tuple[int, int]] = set()
    for path in sorted(set(lock_paths), key=str):
        try:
            identity = path.lstat()
        except FileNotFoundError:
            distinct.append(path)
            continue
        if not stat.S_ISREG(identity.st_mode):
            raise RuntimeError(f"Digest lock is not a regular file: {path}")
        inode = identity.st_dev, identity.st_ino
        if inode not in seen_inodes:
            seen_inodes.add(inode)
            distinct.append(path)
    return distinct


def resolve_digest_logs(args: argparse.Namespace, project_dir: Path) -> List[Path]:
    """Select CLI input paths without reading log contents or changing the filesystem."""
    logs = (
        resolve_explicit_logs(args.logs, project_dir)
        if args.logs else discover_logs(project_dir, args.glob)
    )
    if not logs:
        raise SystemExit(
            f"No log files found. Run this in the log directory or pass files explicitly. "
            f"Auto-discovery pattern was: {args.glob!r}"
        )
    return logs


def provider_request_export(
    directory: Path,
    correlations: Iterable[Mapping[str, Any]],
    *,
    window_start: datetime | None,
    window_end: datetime | None,
) -> tuple[list[dict[str, Any]], ProviderRequestCoverage]:
    """Resolve complete request captures for calls and unmatched files in a window."""

    rows = [dict(item) for item in correlations]
    referenced_ids = {
        str(item["call_id"])
        for item in rows
        if isinstance(item.get("call_id"), str) and item.get("call_id")
    }
    historical = [
        item for item in rows
        if item.get("kind") == "single_call_reply_decision"
        and item.get("model_call_count") == 1
        and not item.get("call_id")
    ]
    exported: list[dict[str, Any]] = []
    category_counts: Counter[str] = Counter()
    observed_paths: set[Path] = set()
    recording_failed_ids = {
        str(item["call_id"])
        for item in rows
        if item.get("kind") == "provider_request_recording_failed"
        and isinstance(item.get("call_id"), str)
        and item.get("call_id")
    }

    for call_id in sorted(referenced_ids):
        if call_id in recording_failed_ids:
            category_counts["recording_failed"] += 1
            exported.append({
                "call_id": call_id,
                "capture_status": "recording_failed",
            })
            continue
        path = request_record_path(directory, call_id)
        observed_paths.add(path)
        try:
            record = read_provider_request_record(path)
        except InvalidRequestRecord as exc:
            status = (
                "missing_expected_record" if not path.exists()
                else "corrupt_or_hash_mismatch"
            )
            category_counts[status] += 1
            exported.append({
                "call_id": call_id,
                "capture_status": status,
                "error": str(exc),
            })
            continue
        category_counts["complete"] += 1
        exported.append({**record, "capture_status": "complete"})

    try:
        discovered = discover_request_record_paths(directory)
    except InvalidRequestRecord as exc:
        discovered = ()
        category_counts["capture_directory_invalid"] += 1
        exported.append({
            "call_id": None,
            "capture_status": "capture_directory_invalid",
            "error": str(exc),
        })
    for path in discovered:
        if path in observed_paths:
            continue
        try:
            modified = datetime.fromtimestamp(path.lstat().st_mtime)
        except OSError:
            continue
        if window_start is not None and modified < window_start:
            continue
        if window_end is not None and modified > window_end + timedelta(seconds=2):
            continue
        try:
            record = read_provider_request_record(path)
        except InvalidRequestRecord as exc:
            category_counts["corrupt_or_hash_mismatch"] += 1
            exported.append({
                "call_id": path.name[:-8],
                "capture_status": "corrupt_or_hash_mismatch",
                "error": str(exc),
            })
            continue
        category_counts["unmatched_capture"] += 1
        exported.append({
            **record,
            "capture_status": "unmatched_prepared_or_outcome_unknown",
        })

    category_counts["historical_not_recorded"] += len(historical)
    starts_by_call: dict[str, set[tuple[str, object]]] = {}
    anonymous_starts: set[tuple[str, object]] = set()
    decision_attempts_by_call: dict[str, int] = {}

    def source_identity(item: Mapping[str, Any], index: int) -> object:
        """Use retained record provenance when lifecycle identity is unavailable."""

        refs = item.get("source_refs")
        if isinstance(refs, list) and refs:
            return json.dumps(
                refs, allow_nan=False, sort_keys=True, separators=(",", ":"),
            )
        return index

    for index, item in enumerate(rows):
        if item.get("kind") == "provider_request_attempt_started":
            observed_call_id = item.get("call_id")
            attempt_number = item.get("attempt_number")
            attempt_identity = (
                ("attempt_number", attempt_number)
                if type(attempt_number) is int and attempt_number >= 1
                else ("source", source_identity(item, index))
            )
            if isinstance(observed_call_id, str) and observed_call_id:
                starts_by_call.setdefault(observed_call_id, set()).add(attempt_identity)
            else:
                anonymous_starts.add(("source", source_identity(item, index)))
        elif (
            item.get("kind") == "single_call_reply_decision"
            and item.get("model_call_count") == 1
            and isinstance(item.get("call_id"), str)
            and item.get("call_id")
        ):
            attempt_count = item.get("provider_request_attempt_count")
            if type(attempt_count) is int and attempt_count >= 0:
                call_id = str(item["call_id"])
                decision_attempts_by_call[call_id] = max(
                    decision_attempts_by_call.get(call_id, 0), attempt_count,
                )

    physical_attempt_count = len(anonymous_starts)
    for call_id in referenced_ids:
        starts = starts_by_call.get(call_id)
        physical_attempt_count += (
            len(starts) if starts else decision_attempts_by_call.get(call_id, 0)
        )
    physical_attempt_count += sum(
        item["provider_request_attempt_count"]
        for item in historical
        if type(item.get("provider_request_attempt_count")) is int
        and item["provider_request_attempt_count"] >= 0
    )
    coverage: ProviderRequestCoverage = {
        "logical_call_denominator": len(referenced_ids) + len(historical),
        "physical_attempt_denominator": physical_attempt_count,
        "category_counts": dict(sorted(category_counts.items())),
        "historical_not_recorded_calls": [
            {
                "time": item.get("time"),
                "lane": item.get("lane"),
                "target_id": item.get("target_id"),
                "capture_status": "historical_not_recorded",
            }
            for item in historical
        ],
    }
    return sorted(exported, key=lambda item: str(item.get("call_id") or "")), coverage


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
    ap.add_argument("--max-text", type=int, default=280, help="Maximum prose-preview length; identifiers, analysis fields and exact reply evidence retain their own bounds. Default: 280.")
    ap.add_argument("--glob", default="mrsMThatcher*.log*", help="Log glob to use when no explicit log files are supplied. Default: mrsMThatcher*.log*")
    ap.add_argument("--project-dir", type=Path, default=Path(__file__).resolve().parent, help="Project directory for config, metadata, history and auto-discovered logs.")
    ap.add_argument("--request-record-dir", type=Path, help="Private provider-request capture directory; defaults to <project-dir>/ai-request-records.")
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
    logs = resolve_digest_logs(args, project_dir)
    lock_paths: List[Path] = []
    if not args.no_state:
        lock_paths.append(state_file.with_suffix(state_file.suffix + ".lock"))
    for output in output_paths:
        lock_paths.append(output.with_suffix(output.suffix + ".lock"))
    validate_output_destinations(output_paths, logs, state_file, lock_paths=lock_paths)

    if not lock_paths:
        return run_digest(args, project_dir=project_dir, state_file=state_file, logs=logs)
    acquisition_paths = distinct_digest_lock_paths(lock_paths)
    with ExitStack() as stack:
        for lock_path in acquisition_paths:
            stack.enter_context(digest_execution_lock(lock_path))
        return run_digest(args, project_dir=project_dir, state_file=state_file, logs=logs)


def select_digest_inputs(args: argparse.Namespace, project_dir: Path, state_file: Path, logs: List[Path]) -> DigestInputSelection:
    """Read validated logs and select the resume window under digest locks."""

    since_source = None
    since_exclusive = False
    resume_boundary_counts: Counter[str] = Counter()
    saved_resume_tail: List[str] = []
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
    physical_records, input_files = read_records_and_summaries(
        logs, since, until, since_exclusive=since_exclusive,
    )
    selection = select_resume_window(
        physical_records, since,
        since_exclusive=since_exclusive,
        saved_resume_tail=saved_resume_tail,
        resume_boundary_counts=resume_boundary_counts,
        locate_resume_fingerprint_tail=locate_resume_fingerprint_tail,
        filter_records_by_time=filter_records_by_time,
        filter_resume_boundary_records=filter_resume_boundary_records,
        warn_timestamp_fallback=lambda: print(
            "WARNING: saved physical resume cursor was not found in retained logs; "
            "falling back to conservative timestamp-based selection, which can replay retained "
            "records or omit new records after a backward clock jump",
            file=sys.stderr,
        ),
    )
    return DigestInputSelection(
        logs, since, until, since_source, since_exclusive,
        resume_boundary_counts, resume_data, physical_records, input_files, selection,
    )


def collect_current_snapshots(project_dir: Path, generation_time: datetime) -> DigestCurrentSnapshots:
    """Read current runtime, durable reply and remote-write evidence."""
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
    return DigestCurrentSnapshots(
        runtime_state, runtime_state_path, runtime_state_ts, runtime_state_status,
        runtime_state_observed_at, runtime_config, runtime_config_path,
        runtime_config_ts, runtime_config_status, confirmed_receipt_evidence,
        historical_history_evidence, durable_reply_evidence_status,
        current_remote_write_safety,
    )


def resume_analysis_context(inputs: DigestInputSelection) -> RestoredResumeContext:
    """Restore only the pending production context from a saved cursor."""
    initial_active_xai_context = None
    initial_active_xai_call_attempt = None
    initial_pending_mention = None
    initial_pending_qt = None
    if inputs.since_source == "saved resume state":
        if isinstance(inputs.resume_data.get("last_active_xai_context"), dict):
            initial_active_xai_context = inputs.resume_data.get("last_active_xai_context")
        if isinstance(inputs.resume_data.get("last_active_xai_call_attempt"), dict):
            initial_active_xai_call_attempt = inputs.resume_data.get(
                "last_active_xai_call_attempt"
            )
        if isinstance(inputs.resume_data.get("last_pending_mention"), dict):
            initial_pending_mention = dict(inputs.resume_data.get("last_pending_mention") or {})
            initial_pending_mention["considered_seq"] = -1
        if isinstance(inputs.resume_data.get("last_pending_qt"), dict):
            initial_pending_qt = dict(inputs.resume_data.get("last_pending_qt") or {})
            initial_pending_qt["considered_seq"] = -1
    return RestoredResumeContext(
        initial_active_xai_context, initial_active_xai_call_attempt,
        initial_pending_mention, initial_pending_qt,
    )


def annotate_input_report(report: DigestReport, inputs: DigestInputSelection, snapshots: DigestCurrentSnapshots, args: argparse.Namespace, project_dir: Path, state_file: Path, generation_time: datetime) -> None:
    """Attach generation, coverage and resume-selection metadata."""
    report["generation_time"] = dt_text(generation_time)
    report["generation_epoch"] = int(generation_time.timestamp())
    report["remote_write_safety"] = snapshots.remote_write_safety

    report["log_files"] = [str(p) for p in inputs.logs]
    report["input_files"] = inputs.input_files
    report["input_warning"] = None
    if (
        not inputs.records
        and inputs.selection.cursor_mode != "fingerprint_tail"
        and any(int(item.get("records_in_window") or 0) > 0 for item in inputs.input_files)
    ):
        report["input_warning"] = (
            "selected log sources contain timestamped records inside the requested window, "
            "but 0 records survived filtering"
        )
    if inputs.selection.timestamp_fallback:
        report["input_warning"] = combine_input_warnings(
            report["input_warning"],
            "saved physical resume cursor was not found; timestamp fallback can "
            "omit newly appended records after a backward clock jump",
        )
    report["input_retention_coverage"] = input_retention_coverage(inputs.input_files, inputs.since)
    report["input_warning"] = combine_input_warnings(
        report["input_warning"],
        report["input_retention_coverage"].get("warning"),
    )
    report["requested_since"] = dt_text(inputs.since) if inputs.since else None
    report["requested_until"] = dt_text(inputs.until) if inputs.until else None
    report["since_source"] = inputs.since_source
    report["since_exclusive"] = inputs.since_exclusive
    report["resume_cursor_mode"] = inputs.selection.cursor_mode
    report["resume_tail_match_length"] = inputs.selection.tail_match_length
    report["local_clock_rollback_count"] = sum(
        current.ts < previous.ts
        for previous, current in zip(inputs.records, inputs.records[1:])
    )
    report["resume_boundary_fingerprint_count"] = len(inputs.resume_boundary_counts)
    report["resume_boundary_occurrence_count"] = sum(inputs.resume_boundary_counts.values())
    report["project_dir"] = str(project_dir)
    report["resume_state_file"] = None if args.no_state else str(state_file)
    report["state_updated"] = False


def add_historical_and_optional_evidence(report: DigestReport, project_dir: Path) -> None:
    """Attach retired feature, historical corpus and optional analytics sections."""
    # Preserve the section names for readers of older reports, but do not scan
    # archived assets or calculate live capacity for a retired runtime feature.
    for section in (
        "generated_image_pool_health", "generated_image_post_rates",
        "generated_image_pool_runway", "generated_image_utilisation",
    ):
        report[section] = {
            "available": False,
            "status": "retired",
            "reason": "Generated-image selection has been removed from the bot runtime.",
        }
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


def overlay_current_runtime(report: DigestReport, inputs: DigestInputSelection, snapshots: DigestCurrentSnapshots, args: argparse.Namespace, project_dir: Path, state_file: Path, generation_time: datetime) -> None:
    """Overlay explicitly current state/config and then saved historical context."""
    state_status: RuntimeStateStatus = {
        "status": snapshots.runtime_state_status,
        "path": str(snapshots.runtime_state_path),
        "observed_at": dt_text(snapshots.runtime_state_observed_at),
    }
    report["runtime_state_status"] = state_status
    report["latest_state"] = (
        summarize_latest_state(
            snapshots.runtime_state,
            snapshots.runtime_state_ts,
            source="bot_state.json",
            source_path=snapshots.runtime_state_path,
        )
        if snapshots.runtime_state is not None
        else {}
    )

    config_status: RuntimeConfigStatus = {
        "status": snapshots.runtime_config_status,
        "path": str(snapshots.runtime_config_path),
        "time": dt_text(snapshots.runtime_config_ts) if snapshots.runtime_config_ts else None,
    }
    report["runtime_config_status"] = config_status
    report["latest_config"] = snapshots.runtime_config or {}
    report["meme_queue_health"] = meme_queue_health_snapshot(
        project_dir,
        runtime_state=snapshots.runtime_state,
        runtime_state_status=snapshots.runtime_state_status,
        runtime_config=snapshots.runtime_config,
        runtime_config_status=snapshots.runtime_config_status,
        observed_at=datetime.now(),
        state_observed_at=snapshots.runtime_state_observed_at,
        read_snapshot=read_stable_regular_snapshot,
    )
    strike_progress = current_author_no_reply_strike_progress(
        snapshots.runtime_state,
        snapshots.runtime_state_status,
        snapshots.runtime_config,
        snapshots.runtime_config_status,
        generation_time,
        state_observed_at=snapshots.runtime_state_observed_at,
    )
    report_section(report, "mention_backlog_and_quarantine")[
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
    # Saved history does not replace live state/configuration. Apply it after
    # the runtime overlay so its complete-result refresh is the only refresh.
    if not args.no_state and not args.reset_state:
        apply_saved_context(report, state_file)
    else:
        refresh_derived(report)

    if not inputs.records:
        report["saved_last_log_entry_time"] = dt_text(inputs.since) if inputs.since else None


def add_provider_and_cost_evidence(report: DigestReport, inputs: DigestInputSelection, args: argparse.Namespace, project_dir: Path, generation_time: datetime) -> None:
    """Export optional provider captures and selected-window costs."""
    report["verbose_replies"] = bool(args.verbose_replies)
    report["detailed_appendix"] = bool(getattr(args, "detailed_appendix", False))
    selected_window_start = inputs.since or min(
        (record.ts for record in inputs.records), default=None
    )
    selected_window_end = inputs.until or max(
        (record.ts for record in inputs.records), default=None
    )
    request_record_directory = args.request_record_dir or (
        project_dir / "ai-request-records"
    )
    request_record_directory = request_record_directory.expanduser()
    if not request_record_directory.is_absolute():
        request_record_directory = project_dir / request_record_directory
    correlations = report.get("provider_request_correlations") or []
    provider_requests, provider_request_coverage = provider_request_export(
        request_record_directory,
        correlations if isinstance(correlations, list) else [],
        window_start=selected_window_start,
        window_end=selected_window_end,
    )
    report["provider_requests"] = provider_requests
    report["provider_request_coverage"] = provider_request_coverage
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
    cost_total: SingleCallCostTotal = {
        "status": str(single_call_cost.get("status") or "unavailable"),
        "amount": single_call_cost.get("amount"),
        "scope": (report.get("openai_published_cost") or {}).get("scope"),
        "method": single_call_cost.get("method"),
    }
    report_section(report, "single_call_reply")["cost_total"] = cost_total


def render_and_deliver_digest(report: DigestReport, inputs: DigestInputSelection, args: argparse.Namespace) -> None:
    """Render and deliver every requested destination before cursor persistence."""
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
        if not inputs.records:
            rendered += "\n<!-- no matching records; resume state not advanced -->\n"

    deliver_report(rendered, args.output)
    if args.markdown_output is not None:
        deliver_report(render_markdown(report) + "\n", args.markdown_output)
    if args.json_output is not None:
        assert json_rendered is not None
        deliver_report(json_rendered, args.json_output)


def commit_digest_cursor(report: DigestReport, inputs: DigestInputSelection, args: argparse.Namespace, state_file: Path) -> None:
    """Advance the physical cursor only after all report deliveries succeed."""
    if inputs.records and not args.no_state and not args.no_update_state:
        # A time filter can leave holes or a suffix in physical order. Commit
        # only the contiguous selected run beginning at its first record; a
        # later analysed record may replay, but an unseen hole is not skipped.
        cursor_start = next(
            index for index, record in enumerate(inputs.physical_records)
            if record is inputs.records[0]
        )
        committed_count = 0
        for record in inputs.physical_records[cursor_start:]:
            if (
                committed_count == len(inputs.records)
                or record is not inputs.records[committed_count]
            ):
                break
            committed_count += 1
        cursor_end = cursor_start + committed_count
        cursor_records = inputs.physical_records[:cursor_end]
        last_ts = cursor_records[-1].ts
        save_resume_time(
            state_file,
            last_ts,
            inputs.records[:committed_count],
            report,
            inputs.logs,
            preserve_existing_context=not args.reset_state,
            merge_existing_boundary_occurrences=(
                inputs.since_source == "saved resume state"
                and not inputs.selection.timestamp_fallback
            ),
            cursor_fingerprint_tail=[
                record_fingerprint(record)
                for record in cursor_records[-RESUME_FINGERPRINT_TAIL_LIMIT:]
            ],
            complete_report=report,
        )


def run_digest(args: argparse.Namespace, *, project_dir: Path, state_file: Path, logs: List[Path]) -> int:
    """Run input selection, current observation, analysis, output and cursor commit."""
    generation_time = datetime.now()
    inputs = select_digest_inputs(args, project_dir, state_file, logs)
    snapshots = collect_current_snapshots(project_dir, generation_time)
    resumed = resume_analysis_context(inputs)
    report_window_end = inputs.until or max(
        (record.ts for record in inputs.records), default=None,
    )
    report = analyse(
        inputs.records,
        max_text=args.max_text,
        initial_active_xai_context=resumed.active_xai_context,
        initial_active_xai_call_attempt=resumed.active_xai_call_attempt,
        initial_pending_mention=resumed.pending_mention,
        initial_pending_qt=resumed.pending_qt,
        current_remote_write_safety=snapshots.remote_write_safety,
        generation_time=generation_time,
        selected_window_end=report_window_end,
        current_snapshot_authoritative=inputs.until is None,
        current_runtime_state=snapshots.runtime_state,
        input_file_indexes={str(path): index for index, path in enumerate(inputs.logs)},
        confirmed_receipt_evidence=snapshots.confirmed_receipt_evidence,
        historical_history_evidence=snapshots.historical_history_evidence,
        durable_reply_evidence_status=snapshots.durable_reply_evidence_status,
    )
    annotate_input_report(report, inputs, snapshots, args, project_dir, state_file, generation_time)
    add_historical_and_optional_evidence(report, project_dir)
    overlay_current_runtime(report, inputs, snapshots, args, project_dir, state_file, generation_time)
    add_provider_and_cost_evidence(report, inputs, args, project_dir, generation_time)
    render_and_deliver_digest(report, inputs, args)
    commit_digest_cursor(report, inputs, args, state_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
