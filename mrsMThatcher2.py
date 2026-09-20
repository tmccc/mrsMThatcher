#!/usr/bin/env python3
"""Run the production MrsMThatcher posting and conversational-reply bot."""


from __future__ import annotations

# importlib.reload executes in the existing module dictionary. Refuse before
# reassigning any descriptor, latch, signal guard, bootstrap or client authority.
if globals().get("_LIFECYCLE_AUTHORITY_ACQUIRED", False):
    raise RuntimeError("Refusing to reload mrsMThatcher2 after lifecycle authority was acquired")
_LIFECYCLE_AUTHORITY_ACQUIRED = False

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mrs_bot_state_generation import StateCommitProof
    from mrs_bot_main_post_confirmation_persistence import RegularPostPersistenceResult

import base64
import hashlib
import functools
import json
import math
import os
import sys
from collections.abc import Mapping
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urlunsplit


# Direct command-line validity precedes every configuration read, including
# the reload guard below.  Keep candidates separate until the guard proves a
# reload cannot mix old transport authority with new safety configuration.
_CANDIDATE_IMPORT_TIME_CLI_ARGUMENTS = tuple(sys.argv[1:])
_CANDIDATE_IMPORT_TIME_TEST_MODE = os.environ.get("MRS_TEST_MODE") == "1"

DOCUMENTED_CLI_MODE_FLAGS = (
    "--initialise",
    "--self-test",
    "--test-cycle",
    "--test-main-tick",
    "--test-post-quote",
    "--test-post-meme",
)
TEST_MODE_REQUIRED_CLI_FLAGS = frozenset(
    {
        "--test-cycle",
        "--test-main-tick",
        "--test-post-quote",
        "--test-post-meme",
    }
)
CLI_USAGE = (
    "usage: mrsMThatcher2.py ["
    + " | ".join(DOCUMENTED_CLI_MODE_FLAGS)
    + "]"
)


class CliUsageError(ValueError):
    """The command line is not one exact documented execution mode."""


def parse_cli_mode(argv: list[str] | tuple[str, ...]) -> str | None:
    """Return the sole requested mode, rejecting every ambiguous argv."""

    arguments = tuple(argv)
    if not arguments:
        return None
    if len(arguments) != 1:
        raise CliUsageError(
            "exactly one documented command mode may be supplied"
        )
    mode = arguments[0]
    if type(mode) is not str or mode not in DOCUMENTED_CLI_MODE_FLAGS:
        raise CliUsageError(f"unknown command mode: {mode!r}")
    return mode


# A directly executed bot validates its complete argv before inspecting API
# configuration, credentials, logging destinations, or the filesystem.
if __name__ == "__main__":
    try:
        import_time_mode = parse_cli_mode(
            _CANDIDATE_IMPORT_TIME_CLI_ARGUMENTS
        )
        if (
            import_time_mode in TEST_MODE_REQUIRED_CLI_FLAGS
            and not _CANDIDATE_IMPORT_TIME_TEST_MODE
        ):
            raise CliUsageError(
                f"{import_time_mode} requires MRS_TEST_MODE=1 before bot import"
            )
    except CliUsageError as exc:
        print(f"{CLI_USAGE}\nmrsMThatcher2.py: error: {exc}", file=sys.stderr)
        raise SystemExit(2) from None


def _normalise_x_origin_before_runtime_configuration(raw: object) -> str:
    """Normalise one X origin before reload can overwrite safety globals."""

    value = str(raw or "").strip()
    if not value or any(ord(character) < 0x20 for character in value):
        raise ValueError("API base URL is empty or contains control characters")
    parsed = urlsplit(value)
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("API base URL has an invalid port") from exc
    if parsed.scheme not in {"http", "https"}:
        raise ValueError("API base URL must use http or https")
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("API base URL must be an origin without user information")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError(
            "API base URL must not contain a query or fragment, and X API "
            "bases must be origin-only"
        )
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host if port is None else f"{host}:{port}"
    from provider_endpoint_policy import validate_provider_endpoint
    normalised = urlunsplit((parsed.scheme.lower(), netloc, "", "", ""))
    validate_provider_endpoint(
        normalised, provider="x", test_mode=_CANDIDATE_IMPORT_TIME_TEST_MODE,
    )
    return normalised


def _effective_request_timeout_before_runtime_configuration(raw: object) -> float:
    """Derive the effective request timeout without changing runtime globals."""

    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 60.0
    if not math.isfinite(value) or value <= 0 or value > 60.0:
        return 60.0
    return value


def _x_request_provider_reload_fingerprint_from_environment() -> tuple[object, ...]:
    """Fingerprint proof-transport config before a reload mutates the module."""

    credential_bytes = json.dumps(
        [
            os.getenv("X_CONSUMER_KEY", ""),
            os.getenv("X_CONSUMER_SECRET", ""),
            os.getenv("X_ACCESS_TOKEN", ""),
            os.getenv("X_ACCESS_SECRET", ""),
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    timeout_seconds = _effective_request_timeout_before_runtime_configuration(
        os.getenv("MRS_REQUEST_TIMEOUT_SECONDS", "60")
    )
    create_url = (
        _normalise_x_origin_before_runtime_configuration(
            os.getenv("X_API_BASE_URL", "https://api.x.com")
        )
        + "/2/tweets"
    )
    return (
        "sealed-x-request-provider-reload-v1",
        os.environ.get("MRS_TEST_MODE") == "1",
        create_url,
        timeout_seconds,
        min(10.0, timeout_seconds),
        hashlib.sha256(
            b"mrsMThatcher-x-oauth-config-v1\x00" + credential_bytes
        ).hexdigest(),
    )


_CURRENT_X_REQUEST_PROVIDER_RELOAD_FINGERPRINT = (
    _x_request_provider_reload_fingerprint_from_environment()
)
from remote_write_transport_journal import (  # noqa: E402
    _configured_x_request_reload_record,
)

(
    _X_REQUEST_PROVIDER_RELOAD_RECORD_STATE,
    _X_REQUEST_PROVIDER_INSTALLED_FINGERPRINT,
) = _configured_x_request_reload_record(sys.modules[__name__])
if _X_REQUEST_PROVIDER_RELOAD_RECORD_STATE == "invalid" or (
    _X_REQUEST_PROVIDER_RELOAD_RECORD_STATE == "installed"
    and _X_REQUEST_PROVIDER_INSTALLED_FINGERPRINT
    != _CURRENT_X_REQUEST_PROVIDER_RELOAD_FINGERPRINT
):
    raise RuntimeError(
        "Refusing to reload mrsMThatcher2 with changed sealed X request "
        "configuration"
    )


# Command-mode authority is fixed before any application import or bootstrap
# work.  Neither a later mutation of ``sys.argv`` nor the executable name may
# silently change the mode whose module-level configuration was constructed.
IMPORT_TIME_CLI_ARGUMENTS = _CANDIDATE_IMPORT_TIME_CLI_ARGUMENTS
IMPORT_TIME_TEST_MODE = _CANDIDATE_IMPORT_TIME_TEST_MODE

import html
import copy
from decimal import Decimal
import errno
import fcntl
import ipaddress
import logging
import mimetypes
import posixpath
import random
import re
import signal
import socket
import stat
import struct
import tempfile
import threading
from collections.abc import Callable
from collections import Counter
from datetime import datetime, timedelta, timezone
from glob import glob
from logging.handlers import RotatingFileHandler
from pathlib import Path
from time import monotonic, sleep
from urllib.parse import unquote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from requests_oauthlib import OAuth1
from urllib3.util import Timeout

import mrs_bot_image_scoring as _image_scoring
import mrs_bot_original_editorial as _original_editorial
import mrs_bot_asset_metadata as _asset_metadata
import mrs_bot_quote_candidates as _quote_candidates
import mrs_bot_image_selection as _image_selection
import mrs_bot_quote_posting as _quote_posting
import mrs_bot_daily_meme as _daily_meme
import mrs_bot_legacy_reply_validation as _legacy_reply_validation
import mrs_bot_reply_state as _reply_state
import mrs_bot_reply_drafts as _reply_drafts
import mrs_bot_reply_history as _reply_history
import mrs_bot_reply_generation as _reply_generation
import mrs_bot_reply_model_transport as _reply_model_transport
import mrs_bot_reply_receipt_values as _reply_receipt_values
import mrs_bot_reply_reconciliation as _reply_reconciliation
import mrs_bot_reply_delivery as _reply_delivery
import mrs_bot_reply_cycle_interfaces as _reply_cycle_interfaces
import mrs_bot_normal_reply_cycle as _normal_reply_cycle
import mrs_bot_quote_reply_cycle as _quote_reply_cycle
import mrs_bot_quote_discovery as _quote_discovery
import mrs_bot_hot_post_discovery as _hot_post_discovery
import mrs_bot_mention_discovery as _mention_discovery
import mrs_bot_mention_authority as _mention_authority
import mrs_bot_reply_evaluation_state as _reply_evaluation_state
import mrs_bot_author_quarantines as _author_quarantines
import mrs_bot_tweet_lookup_cache as _tweet_lookup_cache
import mrs_bot_reply_context as _reply_context
import mrs_bot_reply_native_media as _reply_native_media
import mrs_bot_reply_lane_policy as _reply_lane_policy
import mrs_bot_daily_reply_accounting as _daily_reply_accounting
import mrs_bot_reply_clarifications as _reply_clarifications
import mrs_bot_runtime_control as _runtime_control
import mrs_bot_api_cooldowns as _api_cooldowns
import mrs_bot_x_pagination as _x_pagination
import mrs_bot_request_route_values as _request_route_values
import mrs_bot_x_response_diagnostics as _x_response_diagnostics
import mrs_bot_tick_coordination as _tick_coordination
import mrs_bot_durable_json_io as _durable_json_io
import mrs_bot_state_value_normalisation as _state_value_normalisation
import mrs_bot_state_persistence as _state_persistence
import mrs_bot_state_candidate_validation as _state_candidate_validation
import mrs_bot_state_loading as _state_loading
import mrs_bot_main_post_receipts as _main_post_receipts
import mrs_bot_main_post_receipt_storage as _main_post_receipt_storage
import mrs_bot_main_post_attempt_values as _main_post_attempt_values
import mrs_bot_main_post_confirmation_persistence as _main_post_confirmation_persistence
import mrs_bot_x_request as _x_request
import mrs_bot_post_creation as _post_creation
import mrs_bot_main_post_reconciliation as _main_post_reconciliation
import mrs_bot_historical_context_delivery as _historical_context_delivery
import mrs_bot_historical_context_queue as _historical_context_queue
import mrs_bot_historical_context_runtime as _historical_context_runtime
import mrs_bot_receipt_retirement as _receipt_retirement
import mrs_bot_remote_write_barriers as _remote_write_barriers
import mrs_bot_local_config as _local_config
import mrs_bot_runtime_configuration as _runtime_configuration
import mrs_bot_self_test as _self_test
import mrs_bot_observability as _observability
import mrs_bot_runtime_service_initialisation as _runtime_service_initialisation
import mrs_bot_safety_marker_snapshots as _safety_marker_snapshots
import mrs_bot_remote_write_incidents as _remote_write_incidents
import mrs_bot_instance_lock_checks as _instance_lock_checks
import mrs_bot_installation_lifecycle as _installation_lifecycle
import mrs_bot_transaction_recovery as _transaction_recovery
import mrs_bot_transport_source_preparation as _transport_source_preparation
import mrs_bot_cli_execution as _cli_execution
import mrs_bot_used_history as _used_history
import mrs_bot_receipt_primitives as _receipt_primitives
import mrs_bot_runtime_state_helpers as _runtime_state_helpers

from single_call_reply import (
    MAX_IMAGE_BYTES as SINGLE_CALL_MAX_IMAGE_BYTES,
    MAX_RECENT_ACCOUNT_REPLIES,
    MAX_SAME_AUTHOR_INTERACTIONS,
    MAX_SUPPLIED_IMAGES,
    MAX_TRUSTED_FACTS,
    MAX_VISIBLE_TEXT_CHARACTERS,
    MAX_VISIBLE_TURNS,
    MAX_WEIGHTED_CHARACTERS,
    MODEL as SINGLE_CALL_MODEL,
    RESEARCH_CORPUS_PATH as SINGLE_CALL_REPLY_RESEARCH_CORPUS_PATH,
    REASONING_EFFORT as SINGLE_CALL_REASONING_EFFORT,
    STRATEGY_VERSION as SINGLE_CALL_STRATEGY_VERSION,
    TEMPERATURE as SINGLE_CALL_TEMPERATURE,
    ContextValidationError,
    PipelineResult,
    ReplyValidationError,
    ValidatedReply,
    bound_visible_conversation,
    decision_telemetry as single_call_decision_telemetry,
    default_config as single_call_reply_default_config,
    quoted_post_reference_id,
    run_reply_pipeline as run_single_call_reply_pipeline,
    validate_config as validate_single_call_reply_config,
    validate_persisted_draft as validate_single_call_persisted_draft,
    validate_supplied_images,
)

from mrs_bot_health import (
    BotHealthReporter,
    HealthLoggingObserver,
    health_file_path_from_environment,
)

from remote_write_safety_protocol import (
    ACTIVATION_AUDIT_BASENAME as REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_AUDIT_BASENAME,
    ACTIVATION_BASENAME as REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_BASENAME,
    ProtocolActivationError,
    inspect_protocol_activation,
)
from remote_media_upload_receipt import (
    RETIREMENT_GUARD_PREFIX as MEDIA_RETIREMENT_GUARD_PREFIX,
    TRANSITION_PREFIX as MEDIA_TRANSITION_PREFIX,
    ConfirmedMediaUpload,
    ReceiptBoundMediaPayload,
    MediaUploadAuthority,
    MediaUploadPreflightError,
    MediaUploadReceiptError,
    abort_untransmitted_media_upload,
    begin_media_upload,
    bind_media_handoff_to_transport,
    bind_media_upload_payload,
    confirm_media_upload,
    consume_media_upload_authority,
    fence_path_for_receipt as media_fence_path_for_receipt,
    inspect_media_upload_receipt,
    load_confirmed_media_upload,
    media_upload_has_valid_restart_barrier,
    media_upload_receipt_is_blocking,
    resume_interrupted_confirmed_media_retirement,
    retire_confirmed_media_upload,
)
from remote_write_transport_journal import (
    BoundSourceReceiptTransitionError,
    LANE_SOURCE_VALIDATOR_ID,
    JOURNAL_RETIREMENT_PREFIX,
    JOURNAL_STAGING_PREFIX,
    MAX_CONFIRMATION_EPOCH,
    MIN_CONFIRMATION_EPOCH,
    SourceReceiptBinding,
    TransportAuthority,
    TransportJournalError,
    _bind_transport_authority_to_configured_x_request,
    _configured_x_request_is_sealed_test_loopback,
    _install_configured_x_request_provider,
    abort_untransmitted_transport_transaction,
    arm_transport_transaction,
    bind_confirmed_transport_source,
    bind_transport_source,
    begin_transport_transaction,
    confirm_transport_transaction,
    consume_transport_authority,
    fence_path_for_journal,
    freeze_tweet_request,
    inspect_confirmed_transport_transaction,
    inspect_transport_state,
    journal_path_for_receipt,
    perform_consumed_x_request,
    replace_bound_source_receipt,
    replace_exact_source_receipt_document,
    retire_consumed_transport_transaction_after_proved_remote_non_success,
    retire_confirmed_transport_transaction,
    transport_journal_has_valid_restart_barrier,
    transport_journal_is_blocking,
    verify_confirmed_transport_source_lineage,
)
from x_api_error_semantics import (
    DeterministicReplyCreateRejectionProof,
    ValidatedXErrorResponse,
    XErrorResponseValidationError,
    X_API_ERROR_ENDPOINT_NOT_FOUND,
    X_API_ERROR_GLOBAL_DENIAL,
    X_API_ERROR_LOOKUP_TARGET_UNAVAILABLE,
    X_API_ERROR_OTHER,
    X_API_ERROR_REPLY_TARGET_RESTRICTED,
    X_API_ERROR_REPLY_TARGET_UNAVAILABLE,
    _activate_coordinator_reply_create_rejection_proof,
    claim_reply_create_rejection_for_receipt_retirement,
    classify_x_api_error,
    invalidate_reply_create_rejection_proof,
    parse_validated_x_error_response,
    reply_create_rejection_payload,
)
from exact_receipt_retirement import (
    ExactReceiptRetirementError,
    inspect_exact_receipt_retirement,
    inspect_interrupted_receipt_retirement,
    inspect_retirement_ledger,
    initialise_retirement_ledger,
    prepare_exact_receipt_retirement,
    recover_retirement_ledger_exchange_if_present,
    retirement_auxiliary_barrier_exists,
    retirement_auxiliary_paths,
    retirement_ledger_is_blocking,
    retirement_ledger_paths,
    resume_interrupted_receipt_retirement,
    retire_or_resume_exact_receipt,
)
from transaction_mutation_authority import (
    TransactionMutationAuthority,
    issue_transaction_mutation_authority,
)


SELF_TEST_REQUESTED = IMPORT_TIME_CLI_ARGUMENTS == ("--self-test",)
TEST_CYCLE_REQUESTED = IMPORT_TIME_CLI_ARGUMENTS == ("--test-cycle",)
TEST_MAIN_TICK_REQUESTED = IMPORT_TIME_CLI_ARGUMENTS == (
    "--test-main-tick",
)
TEST_POST_QUOTE_REQUESTED = IMPORT_TIME_CLI_ARGUMENTS == (
    "--test-post-quote",
)
TEST_POST_MEME_REQUESTED = IMPORT_TIME_CLI_ARGUMENTS == (
    "--test-post-meme",
)
INITIALISE_REQUESTED = IMPORT_TIME_CLI_ARGUMENTS == ("--initialise",)
TEST_MODE = IMPORT_TIME_TEST_MODE

# ---------------------------------------------------------------------
# Quote/image posting schedule
# ---------------------------------------------------------------------

POST_SLEEP_MIN = 7200
POST_SLEEP_MAX = 9000

QUOTE_SEASON_SOFT_WEIGHT = 4.0
QUOTE_SEASON_STRONG_WEIGHT = 12.0
QUOTE_SEASON_DATE_SPECIFIC_WEIGHT = 16.0
QUOTE_QUALITY_WEIGHT_MAX_MULTIPLIER = 1.35

IMAGE_STRONG_MISMATCH_PENALTY = -10_000.0
MAX_QUOTE_IMAGE_PAIR_ATTEMPTS = 25

# ---------------------------------------------------------------------
# Daily anti-socialist meme posting
# ---------------------------------------------------------------------

ENABLE_DAILY_MEME_POSTS = True

# Daily meme timing:
#   * the first normal quote/image post after midday schedules the meme
#   * the meme posts a random 35-75 minutes after that post
#   * if no such quote/image post has happened by 16:00, a fallback meme post is due
MEME_TRIGGER_AFTER_HOUR = 12
MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS = 35 * 60
MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS = 75 * 60
MEME_FALLBACK_HOUR = 16
MEME_FALLBACK_MINUTE = 0
MEME_POST_TEXT = ""
MEME_SCHEDULE_VERSION = 2

# Calendar decisions for durable main-post recovery are part of the
# transaction protocol, not an ambient process setting.  Current attempt
# receipts bind this exact IANA identifier so a restarted process derives the
# same local date/hour and DST offset even when its inherited ``TZ`` differs.
MAIN_POST_SCHEDULE_TIMEZONE = "Europe/London"

RESET_MEME_CYCLE_WHEN_ALL_POSTED = False
MEME_MIN_SECONDS_AFTER_QUOTE_POST = 1800

# ---------------------------------------------------------------------
# Quote-post / quote-tweet reply lane
# ---------------------------------------------------------------------

ENABLE_QUOTE_TWEET_CHECKS = True

QUOTE_CHECK_EVERY_SECONDS = 3600
QUOTE_CHECK_SPACING_RETRY_SECONDS = 300
QUOTE_REPLY_DELAY_SECONDS = 600

MAX_QUOTE_POSTS_PER_CHECK = 10
MAX_QUOTE_REPLIES_PER_DAY = 12

QUOTE_POST_LOOKBACK_MAIN_POSTS = 5
RECENT_OWN_POST_IDS_MAX = 20

# Recent search accepts 10-100 results per page. Combined queries share the
# existing total page allowance: pages per post times posts in the query.
QUOTE_LOOKUP_API_MAX_RESULTS = 10
QUOTE_LOOKUP_MAX_PAGES_PER_POST = 3
QUOTE_REPEATED_CURSOR_BACKOFF_SECONDS = 21600
QUOTE_REPEATED_CURSOR_SUPPRESSION_MAX_ENTRIES = 64

QUOTE_CHECK_STATUS_CHECKED = _reply_cycle_interfaces.QUOTE_CHECK_STATUS_CHECKED
QUOTE_CHECK_STATUS_POSTED = _reply_cycle_interfaces.QUOTE_CHECK_STATUS_POSTED
QUOTE_CHECK_STATUS_SKIPPED_SPACING = _reply_cycle_interfaces.QUOTE_CHECK_STATUS_SKIPPED_SPACING
QUOTE_CHECK_STATUS_SKIPPED_CAP = _reply_cycle_interfaces.QUOTE_CHECK_STATUS_SKIPPED_CAP
QUOTE_CHECK_STATUS_SKIPPED_COOLDOWN = _reply_cycle_interfaces.QUOTE_CHECK_STATUS_SKIPPED_COOLDOWN
QUOTE_CHECK_STATUS_DISABLED = _reply_cycle_interfaces.QUOTE_CHECK_STATUS_DISABLED

NORMAL_CHECK_STATUS_CHECKED = _reply_cycle_interfaces.NORMAL_CHECK_STATUS_CHECKED
NORMAL_CHECK_STATUS_POSTED = _reply_cycle_interfaces.NORMAL_CHECK_STATUS_POSTED
NORMAL_CHECK_STATUS_SKIPPED_SPACING = _reply_cycle_interfaces.NORMAL_CHECK_STATUS_SKIPPED_SPACING
NORMAL_CHECK_STATUS_SKIPPED_CAP = _reply_cycle_interfaces.NORMAL_CHECK_STATUS_SKIPPED_CAP
NORMAL_CHECK_STATUS_SKIPPED_COOLDOWN = _reply_cycle_interfaces.NORMAL_CHECK_STATUS_SKIPPED_COOLDOWN
NORMAL_CHECK_STATUS_DISABLED = _reply_cycle_interfaces.NORMAL_CHECK_STATUS_DISABLED
NORMAL_CHECK_STATUS_API_ERROR = _reply_cycle_interfaces.NORMAL_CHECK_STATUS_API_ERROR

REPLY_EVALUATION_MIN_RETENTION_SECONDS = 30 * 24 * 60 * 60
REPLY_EVALUATION_MAX_RECORDS = 25_000
MENTION_BACKLOG_CONTINUATION_TOKEN_LIMIT = 10_000
AI_REPLY_HISTORY_MAX_AGE_SECONDS = 2 * 365 * 24 * 60 * 60
AI_REPLY_HISTORY_MAX_RECORDS = 1_000

# ---------------------------------------------------------------------
# Reply automation
# ---------------------------------------------------------------------

ENABLE_AUTO_REPLIES = True

REPLY_CHECK_EVERY_SECONDS = 900
MAX_AUTO_REPLIES_PER_DAY = 48
MAX_REPLIES_PER_AUTHOR_PER_DAY = 6
CLARIFICATION_REPLY_WINDOW_SECONDS = 24 * 60 * 60
MAX_MENTIONS_PER_CHECK = 5
MENTIONS_MAX_PAGES_PER_CHECK = 3
AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD = 3
AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS = 21600
AUTHOR_NO_REPLY_QUARANTINE_SECONDS = 43200
AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY = (
    "single_sol_explicit_spam_or_abuse_v2"
)
AUTHOR_EVALUATION_QUARANTINE_PREVIOUS_EVIDENCE_POLICY = _author_quarantines.AUTHOR_EVALUATION_QUARANTINE_PREVIOUS_EVIDENCE_POLICY
AUTHOR_EVALUATION_QUARANTINE_SINGLE_SOL_V1_EVIDENCE_POLICY = _author_quarantines.AUTHOR_EVALUATION_QUARANTINE_SINGLE_SOL_V1_EVIDENCE_POLICY
AUTHOR_EVALUATION_QUARANTINE_SEEDED_EVIDENCE_POLICY = _author_quarantines.AUTHOR_EVALUATION_QUARANTINE_SEEDED_EVIDENCE_POLICY
AUTHOR_EVALUATION_QUARANTINE_LEGACY_EVIDENCE_POLICY = _author_quarantines.AUTHOR_EVALUATION_QUARANTINE_LEGACY_EVIDENCE_POLICY

# Optional hot-post reply lane. This reuses the same watched post ID file
# as the quote-tweet lane, but looks for ordinary replies in that post
# conversation via recent search and feeds them through the normal reply logic.
ENABLE_HOT_POST_REPLY_CHECKS = True
MAX_HOT_POST_REPLIES_PER_CHECK = 10
HOT_POST_REPLY_SEARCH_API_MAX_RESULTS = 10
HOT_POST_REPLY_SEARCH_MAX_PAGES_PER_CHECK = 3
HOT_POST_REPLY_USE_SINCE_ID = True
HOT_POST_REPLY_FULL_RESCAN_EVERY_CHECKS = 12

MIN_SECONDS_BETWEEN_REPLIES = 900

MARK_AI_REPLIES_AS_AI = False

ALWAYS_FETCH_PARENT_FOR_CONTEXT = True
SKIP_REPLIES_TO_OWN_AUTO_REPLIES = False

# Parent traversal is independently bounded so a verified root can still be
# found before the model-facing path is reduced to MAX_VISIBLE_TURNS.
THREAD_CONTEXT_MAX_DEPTH = 64
THREAD_CONTEXT_MAX_NETWORK_FETCHES = 3
THREAD_CONTEXT_MAX_CHARS_PER_POST = MAX_VISIBLE_TEXT_CHARACTERS
THREAD_CONTEXT_MAX_TOTAL_CHARS = MAX_VISIBLE_TEXT_CHARACTERS
MAX_REPLY_CONTEXT_PHOTOS = MAX_SUPPLIED_IMAGES

TWEET_CACHE_MAX_AGE_SECONDS = 7 * 24 * 3600
TWEET_CACHE_MAX_ITEMS = 500

# ---------------------------------------------------------------------
# Safety / circuit breaker
# ---------------------------------------------------------------------

ERROR_WINDOW_SECONDS = 3600
MAX_X_ERRORS_PER_WINDOW = 3
MAX_OPENAI_ERRORS_PER_WINDOW = 3
COOLDOWN_AFTER_REPEATED_ERRORS_SECONDS = 3600
COOLDOWN_AFTER_429_SECONDS = 3600

# ---------------------------------------------------------------------
# Single-call conversational generation
# ---------------------------------------------------------------------

MAX_REPLY_CHARS = MAX_WEIGHTED_CHARACTERS
REPLY_INCOMING_MAX_CHARS = 10_000

# ---------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------

PRODUCTION_BASE_DIR = Path("/disks/disk1/etc/mrsMThatcher")
BASE_DIR = Path(os.getenv("MRS_BASE_DIR", str(PRODUCTION_BASE_DIR))).expanduser()


def path_is_same_or_child(path: Path, parent: Path) -> bool:
    """Return whether a path equals or is contained by a parent path."""
    try:
        path_resolved = path.resolve()
        parent_resolved = parent.resolve()
        return path_resolved == parent_resolved or parent_resolved in path_resolved.parents
    except Exception:
        path_abs = path.absolute()
        parent_abs = parent.absolute()
        return path_abs == parent_abs or parent_abs in path_abs.parents


def test_process_production_state_write_blocked(path: Path) -> bool:
    """Prevent tests and self-tests from ever persisting production bot state."""
    test_process = bool(os.getenv("PYTEST_CURRENT_TEST")) or SELF_TEST_REQUESTED or TEST_MODE
    return test_process and path_is_same_or_child(path, PRODUCTION_BASE_DIR)


if TEST_MODE and path_is_same_or_child(BASE_DIR, PRODUCTION_BASE_DIR):
    print(
        f"Refusing to run in MRS_TEST_MODE with production BASE_DIR={BASE_DIR}",
        file=sys.stderr,
    )
    sys.exit(2)

LINES_FILE = BASE_DIR / "mrsMThatcher.txt"
IMAGE_GLOB = str(BASE_DIR / "images/t*")
QUOTE_ANALYSIS_FILE = BASE_DIR / "quote_analysis.json"
IMAGE_ANALYSIS_FILE = BASE_DIR / "image_analysis.json"
ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING = False
ORIGINAL_EDITORIAL_ANALYSIS_FILE = str(BASE_DIR / "original_image_editorial_analysis_experiment_v1.json")
ORIGINAL_EDITORIAL_SHADOW_WEIGHT = 0.32
ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT = 4.0
QUOTE_ANALYSIS_OVERRIDES_FILE = BASE_DIR / "quote_analysis_overrides.json"
HISTORICAL_CONTEXT_RESEARCH_DIR = BASE_DIR / "semantic_alignment_research" / "quote_research_full_001"
COMPLETED_QUOTE_RESEARCH_FILE = HISTORICAL_CONTEXT_RESEARCH_DIR / "research_packets.json"
RUNTIME_ELIGIBLE_QUOTE_MANIFEST_FILE = (
    BASE_DIR
    / "semantic_alignment_research"
    / "quote_attribution_cleanup_001"
    / "deployment_candidate"
    / "runtime_eligible_quote_manifest.json"
)
HISTORICAL_CONTEXT_REPLY_HISTORY_FILE = BASE_DIR / "historical_context_reply_history.json"
HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE = BASE_DIR / "historical_context_reply_receipt.json"
HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE = BASE_DIR / "historical_context_reply_outbox.json"
historical_context_reply = {
    "enabled": False,
    "maximum_length": 4000,
    "include_meaning": True,
    "include_source": True,
    "include_verification": True,
}
single_call_reply = single_call_reply_default_config()

LINES_USED_FILE = BASE_DIR / "lines_used.json"
IMAGES_USED_FILE = BASE_DIR / "images_used.json"
REGULAR_POST_RECEIPT_FILE = BASE_DIR / "regular_post_receipt.json"
MEME_POST_RECEIPT_FILE = BASE_DIR / "meme_post_receipt.json"
CONFIRMED_REPLY_RECEIPT_FILE = BASE_DIR / "confirmed_reply_receipt.json"
MEDIA_UPLOAD_RECEIPT_FILE = BASE_DIR / "remote_media_upload_receipt.json"
AMBIGUOUS_POST_OUTCOME_FILE = BASE_DIR / "ambiguous_post_outcome.json"
AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE = (
    BASE_DIR / "ambiguous_post_outcome.restart_barrier.json"
)
REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE = (
    BASE_DIR / REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_BASENAME
)
REMOTE_WRITE_SAFETY_MARKER_MAX_BYTES = 64 * 1024
_AMBIGUOUS_REMOTE_POST_SEEN = False
_AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN = False
_RETAINED_CONFIRMED_POST_SIGINT_GUARD = None
MEME_SCHEDULE_MODES = {
    "",
    "fallback",
    "fallback_startup",
    "fallback_migrated",
    "after_first_quote_after_midday",
    "delayed_runtime_control",
    "delayed_recent_quote",
    "delayed_write_api_cooldown",
    "delayed_api_error",
    "delayed_exception",
}
MAX_REASONABLE_STATE_EPOCH = _runtime_control.MAX_CONTROL_EPOCH
PICKLE_FILE = BASE_DIR / "lines_used.pickle"
IMAGE_PICKLE_FILE = BASE_DIR / "images_used.pickle"
STATE_FILE = BASE_DIR / "bot_state.json"
INSTALLATION_MARKER_FILE = BASE_DIR / ".mrsMThatcher.initialised.json"
INSTALLATION_IN_PROGRESS_FILE = BASE_DIR / ".mrsMThatcher.initialising.json"
LOG_FILE = Path(os.getenv("MRS_LOG_FILE", str(BASE_DIR / "mrsMThatcher.log"))).expanduser()
if SELF_TEST_REQUESTED and "MRS_LOG_FILE" not in os.environ:
    LOG_FILE = BASE_DIR / "mrsMThatcher.selftest.log"
if TEST_MODE and path_is_same_or_child(LOG_FILE, PRODUCTION_BASE_DIR):
    print(
        f"Refusing to run in MRS_TEST_MODE with production LOG_FILE={LOG_FILE}",
        file=sys.stderr,
    )
    sys.exit(2)
LOCAL_CONFIG_FILE = BASE_DIR / "mrsMThatcher.local.json"
LOCAL_CONFIG_MAX_BYTES = 64 * 1024
CONTROL_FILE = BASE_DIR / "mrsMThatcher.control.json"
LOCK_FILE = BASE_DIR / "mrsMThatcher.lock"
STATE_BACKUP_COUNT = 5
STATE_READER_VERSION = 5
STATE_MINIMUM_READER_VERSION = 5
STATE_READER_COMPATIBILITY_FENCE = {
    "__mrs_state_reader_compatibility_fence__": STATE_MINIMUM_READER_VERSION,
}
STATE_PREVIOUS_READER_COMPATIBILITY_FENCES = (
    {"__mrs_state_reader_compatibility_fence__": 4},
    {"__mrs_state_reader_compatibility_fence__": 2},
)

# Re-read before each quote-tweet check; edit this file while the bot is running.
EXTRA_QUOTE_WATCH_FILE = BASE_DIR / "extra_quote_watch_post_ids.txt"
MAX_EXTRA_QUOTE_WATCH_POSTS = 5

MEME_DIR = BASE_DIR / "final_posting_queue_top90_as_is" / "images"
MEME_ANALYSIS_FILE = BASE_DIR / "final_posting_queue_top90_as_is" / "renamed_png_v3_top90_posting_queue.json"


# ---------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------

PRODUCTION_LOG_MAX_BYTES = 2_000_000
PRODUCTION_LOG_BACKUP_COUNT = 100
_MANAGED_LOG_HANDLER_ATTR = _observability._MANAGED_LOG_HANDLER_ATTR


def remove_managed_log_handlers(logger: logging.Logger) -> None:
    """Detach and close handlers installed by this module."""
    return _observability.remove_managed_log_handlers(
        logger,
    )


def mark_managed_log_handler(handler: logging.Handler, kind: str) -> logging.Handler:
    """Mark a logging handler as owned by this module."""
    return _observability.mark_managed_log_handler(
        handler,
        kind,
    )


def setup_logging(
    *,
    log_path: Path | None = None,
    configure_file_logging: bool = True,
) -> logging.Logger:
    """Configure console and optional rotating-file logging."""
    return _observability.setup_logging(
        log_path=log_path,
        configure_file_logging=configure_file_logging,
        LOG_FILE=LOG_FILE,
        PRODUCTION_BASE_DIR=PRODUCTION_BASE_DIR,
        PRODUCTION_LOG_BACKUP_COUNT=PRODUCTION_LOG_BACKUP_COUNT,
        PRODUCTION_LOG_MAX_BYTES=PRODUCTION_LOG_MAX_BYTES,
        Path=Path,
        RotatingFileHandler=RotatingFileHandler,
        logging=logging,
        os=os,
        path_is_same_or_child=path_is_same_or_child,
        sys=sys,
    )


log = logging.getLogger("mrsMThatcher")
_IMPORT_LOG_LEVEL = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
log.setLevel(_IMPORT_LOG_LEVEL)
log.propagate = False
remove_managed_log_handlers(log)
_IMPORT_CONSOLE_HANDLER = mark_managed_log_handler(
    logging.StreamHandler(sys.stdout),
    "import_console",
)
_IMPORT_CONSOLE_HANDLER.setLevel(_IMPORT_LOG_LEVEL)
_IMPORT_CONSOLE_HANDLER.setFormatter(logging.Formatter(
    fmt="%(asctime)s %(levelname)-8s %(funcName)s:%(lineno)d - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
))
log.addHandler(_IMPORT_CONSOLE_HANDLER)
_LOCK_FH = None
_LOCK_ACQUISITION_IDENTITY: tuple[int, int, int] | None = None
_STATE_DIR_LOCK_FD: int | None = None
_STATE_DIR_LOCK_IDENTITY: tuple[int, int] | None = None
_LOCK_SOCKET: socket.socket | None = None
_LOCK_SOCKET_NAME: bytes | None = None
_OFD_LOCK_FORMAT = _instance_lock_checks._OFD_LOCK_FORMAT
_BOT_HEALTH_REPORTER: BotHealthReporter | None = None
_BOT_HEALTH_LOGGING_OBSERVER: HealthLoggingObserver | None = None


def _get_bot_health_reporter() -> BotHealthReporter | None:
    """Return the root's bot health reporter."""
    return _BOT_HEALTH_REPORTER

def _set_bot_health_reporter(value: BotHealthReporter | None) -> None:
    """Set the root's bot health reporter."""
    global _BOT_HEALTH_REPORTER
    _BOT_HEALTH_REPORTER = value

def _set_bot_health_logging_observer(value: HealthLoggingObserver | None) -> None:
    """Set the root's bot health logging observer."""
    global _BOT_HEALTH_LOGGING_OBSERVER
    _BOT_HEALTH_LOGGING_OBSERVER = value

def _get_reply_evidence_repository_cache() -> object | None:
    """Return the root's cached reply evidence repository."""
    return _REPLY_EVIDENCE_REPOSITORY

def _set_reply_evidence_repository_cache(value: object | None) -> None:
    """Set the root's cached reply evidence repository."""
    global _REPLY_EVIDENCE_REPOSITORY
    _REPLY_EVIDENCE_REPOSITORY = value

def _get_reply_evidence_load_error() -> str | None:
    """Return the root's cached reply evidence load error."""
    return _REPLY_EVIDENCE_LOAD_ERROR

def _set_reply_evidence_load_error(value: str | None) -> None:
    """Set the root's cached reply evidence load error."""
    global _REPLY_EVIDENCE_LOAD_ERROR
    _REPLY_EVIDENCE_LOAD_ERROR = value

def _get_historical_context_semantic_gate() -> object | None:
    """Return the root's historical-context semantic gate."""
    return _HISTORICAL_CONTEXT_SEMANTIC_GATE

def _set_historical_context_semantic_gate(value: object | None) -> None:
    """Set the root's historical-context semantic gate."""
    global _HISTORICAL_CONTEXT_SEMANTIC_GATE
    _HISTORICAL_CONTEXT_SEMANTIC_GATE = value

def _get_bot_logger() -> logging.Logger:
    """Return the current root logger."""
    return log


def initialise_bot_health_reporting() -> None:
    """Initialise fail-open telemetry after production logging is ready."""
    return _runtime_service_initialisation.initialise_bot_health_reporting(
        BASE_DIR=BASE_DIR,
        BotHealthReporter=BotHealthReporter,
        HealthLoggingObserver=HealthLoggingObserver,
        INITIALISE_REQUESTED=INITIALISE_REQUESTED,
        SELF_TEST_REQUESTED=SELF_TEST_REQUESTED,
        TEST_MODE=TEST_MODE,
        _get_bot_health_reporter=_get_bot_health_reporter,
        _get_bot_logger=_get_bot_logger,
        _set_bot_health_logging_observer=_set_bot_health_logging_observer,
        _set_bot_health_reporter=_set_bot_health_reporter,
        health_file_path_from_environment=health_file_path_from_environment,
    )


def report_bot_health_progress(
    phase: str,
    *,
    paused: bool | None = None,
    remote_write_blocked: bool | None = None,
    loop_started: bool = False,
    loop_completed: bool = False,
) -> None:
    """Advance observational telemetry without affecting bot operation."""
    return _observability.report_bot_health_progress(
        phase,
        paused=paused,
        remote_write_blocked=remote_write_blocked,
        loop_started=loop_started,
        loop_completed=loop_completed,
        _BOT_HEALTH_REPORTER=_BOT_HEALTH_REPORTER,
    )


def instance_lock_abstract_socket_name(base_dir: Path | None = None) -> bytes:
    """Return one Linux abstract-socket name bound to the state directory."""
    return _instance_lock_checks.instance_lock_abstract_socket_name(
        base_dir,
        BASE_DIR=BASE_DIR,
        os=os,
    )


def instance_lock_abstract_socket_name_for_identity(
    device: int,
    inode: int,
) -> bytes:
    """Return one supplementary singleton name for a directory identity."""
    return _instance_lock_checks.instance_lock_abstract_socket_name_for_identity(
        device,
        inode,
    )


def ofd_lock_record(lock_type: int) -> bytes:
    """Return one one-byte-range Linux OFD lock request."""
    return _instance_lock_checks.ofd_lock_record(
        lock_type,
    )


def descriptor_owns_exclusive_flock(
    descriptor: int,
    *,
    expected_device: int,
    expected_inode: int,
) -> bool:
    """Return whether Linux fdinfo binds an exclusive flock to this exact fd."""
    return _instance_lock_checks.descriptor_owns_exclusive_flock(
        descriptor,
        expected_device=expected_device,
        expected_inode=expected_inode,
        Path=Path,
        os=os,
    )


def test_mode_excludes_live_remote_writes() -> bool:
    """Return whether test mode uses only explicitly local fake endpoints."""
    return _instance_lock_checks.test_mode_excludes_live_remote_writes(
        LIVE_ENDPOINT_TEST_OVERRIDE_PHRASE=LIVE_ENDPOINT_TEST_OVERRIDE_PHRASE,
        OPENAI_BASE=OPENAI_BASE,
        TEST_MODE=TEST_MODE,
        X_BASE=X_BASE,
        X_UPLOAD_BASE=X_UPLOAD_BASE,
        _configured_x_request_is_sealed_test_loopback=_configured_x_request_is_sealed_test_loopback,
        endpoint_is_loopback=endpoint_is_loopback,
        os=os,
        single_call_reply=single_call_reply,
    )


def require_instance_lock_for_remote_write(operation: str) -> None:
    """Prove exact OFD, pathname and abstract-singleton process ownership.

    ``F_OFD_GETLK`` on the designated descriptor distinguishes its ownership
    from a lock held by some other open file description.  A separate probe
    must remain excluded, the pathname identity must remain bound, and the
    non-filesystem singleton must still be live.  Fake-endpoint tests may
    bypass this production boundary; the explicit live-endpoint test override
    may not.
    """
    return _instance_lock_checks.require_instance_lock_for_remote_write(
        operation,
        BASE_DIR=BASE_DIR,
        LOCK_FILE=LOCK_FILE,
        _LOCK_ACQUISITION_IDENTITY=_LOCK_ACQUISITION_IDENTITY,
        _LOCK_FH=_LOCK_FH,
        _LOCK_SOCKET=_LOCK_SOCKET,
        _LOCK_SOCKET_NAME=_LOCK_SOCKET_NAME,
        _STATE_DIR_LOCK_FD=_STATE_DIR_LOCK_FD,
        _STATE_DIR_LOCK_IDENTITY=_STATE_DIR_LOCK_IDENTITY,
        descriptor_owns_exclusive_flock=descriptor_owns_exclusive_flock,
        fcntl=fcntl,
        os=os,
        test_mode_excludes_live_remote_writes=test_mode_excludes_live_remote_writes,
    )


def transaction_mutation_authority(
    operation: str,
) -> TransactionMutationAuthority:
    """Issue an authority which re-proves the live instance lock on use."""
    return _instance_lock_checks.transaction_mutation_authority(
        operation,
        issue_transaction_mutation_authority=issue_transaction_mutation_authority,
        require_instance_lock_for_remote_write=require_instance_lock_for_remote_write,
    )


def acquire_instance_lock() -> None:
    """Prevent two bot processes from sharing one mutable state directory."""
    global _LOCK_FH
    global _LOCK_ACQUISITION_IDENTITY
    global _STATE_DIR_LOCK_FD
    global _STATE_DIR_LOCK_IDENTITY
    global _LOCK_SOCKET
    global _LOCK_SOCKET_NAME
    global _LIFECYCLE_AUTHORITY_ACQUIRED

    _LIFECYCLE_AUTHORITY_ACQUIRED = True
    if _LOCK_FH is not None:
        require_instance_lock_for_remote_write("Instance-lock reuse")
        return

    BASE_DIR.mkdir(parents=True, exist_ok=True)
    state_directory_fd: int | None = None
    instance_socket: socket.socket | None = None
    descriptor: int | None = None
    fh = None
    ownership_handoff_committed = False
    try:
        directory_flags = (
            os.O_RDONLY
            | getattr(os, "O_DIRECTORY", 0)
            | getattr(os, "O_CLOEXEC", 0)
        )
        state_directory_fd = os.open(BASE_DIR, directory_flags)
        state_directory_identity = os.fstat(state_directory_fd)
        current_state_directory = os.stat(BASE_DIR, follow_symlinks=True)
        if (
            not stat.S_ISDIR(state_directory_identity.st_mode)
            or not stat.S_ISDIR(current_state_directory.st_mode)
            or state_directory_identity.st_dev != current_state_directory.st_dev
            or state_directory_identity.st_ino != current_state_directory.st_ino
        ):
            raise RuntimeError(
                "State-directory identity changed while its instance lock was opened"
            )
        try:
            fcntl.flock(
                state_directory_fd,
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError:
            log.critical(
                "Another MrsMThatcher instance owns state-directory lock %s",
                BASE_DIR,
            )
            sys.exit(2)
        if not descriptor_owns_exclusive_flock(
            state_directory_fd,
            expected_device=int(state_directory_identity.st_dev),
            expected_inode=int(state_directory_identity.st_ino),
        ):
            raise RuntimeError(
                "State-directory descriptor does not own its acquired flock"
            )

        socket_name = instance_lock_abstract_socket_name_for_identity(
            int(state_directory_identity.st_dev),
            int(state_directory_identity.st_ino),
        )
        instance_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            instance_socket.bind(socket_name)
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                log.critical(
                    "Another MrsMThatcher instance owns process singleton %s",
                    socket_name[1:].decode("ascii", errors="replace"),
                )
                sys.exit(2)
            raise

        flags = (
            os.O_RDWR
            | os.O_CREAT
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(
                LOCK_FILE.name,
                flags,
                0o600,
                dir_fd=state_directory_fd,
            )
        except OSError:
            log.critical("Instance lock cannot be opened safely: %s", LOCK_FILE)
            raise
        opened = os.fstat(descriptor)
        current = os.stat(
            LOCK_FILE.name,
            dir_fd=state_directory_fd,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(current.st_mode)
            or opened.st_nlink != 1
            or current.st_nlink != 1
            or opened.st_dev != current.st_dev
            or opened.st_ino != current.st_ino
        ):
            raise RuntimeError(
                "Instance-lock pathname does not identify one ordinary file"
            )
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            log.critical(
                "Another MrsMThatcher instance already holds lock %s",
                LOCK_FILE,
            )
            sys.exit(2)
        if not descriptor_owns_exclusive_flock(
            descriptor,
            expected_device=int(opened.st_dev),
            expected_inode=int(opened.st_ino),
        ):
            raise RuntimeError(
                "Instance-lock descriptor does not own its acquired flock"
            )

        identity = os.fstat(descriptor)
        after_lock = os.stat(
            LOCK_FILE.name,
            dir_fd=state_directory_fd,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(identity.st_mode)
            or not stat.S_ISREG(after_lock.st_mode)
            or identity.st_nlink != 1
            or after_lock.st_nlink != 1
            or identity.st_dev != after_lock.st_dev
            or identity.st_ino != after_lock.st_ino
        ):
            raise RuntimeError(
                "Instance-lock pathname changed while exclusive ownership was acquired"
            )
        try:
            fcntl.fcntl(
                descriptor,
                fcntl.F_OFD_SETLK,
                ofd_lock_record(fcntl.F_WRLCK),
            )
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                log.critical(
                    "Another MrsMThatcher instance owns OFD lock %s",
                    LOCK_FILE,
                )
                sys.exit(2)
            raise

        owner_pid = os.getpid()
        record = f"pid={owner_pid}\n".encode("ascii")
        os.ftruncate(descriptor, 0)
        written = os.pwrite(descriptor, record, 0)
        if written != len(record):
            raise RuntimeError("Instance-lock owner record write was incomplete")
        os.fsync(descriptor)
        if os.pread(descriptor, len(record) + 1, 0) != record:
            raise RuntimeError(
                "Instance-lock owner record did not round-trip exactly"
            )
        final_path = os.stat(
            LOCK_FILE.name,
            dir_fd=state_directory_fd,
            follow_symlinks=False,
        )
        final_state_directory = os.stat(BASE_DIR, follow_symlinks=True)
        if (
            not stat.S_ISREG(final_path.st_mode)
            or final_path.st_nlink != 1
            or final_path.st_dev != identity.st_dev
            or final_path.st_ino != identity.st_ino
            or final_state_directory.st_dev != state_directory_identity.st_dev
            or final_state_directory.st_ino != state_directory_identity.st_ino
        ):
            raise RuntimeError(
                "Instance-lock pathname changed while its owner record was committed"
            )
        fh = os.fdopen(descriptor, "r+", encoding="ascii")
        descriptor = None
        lock_acquisition_identity = (
            int(identity.st_dev),
            int(identity.st_ino),
            owner_pid,
        )
        state_directory_lock_identity = (
            int(state_directory_identity.st_dev),
            int(state_directory_identity.st_ino),
        )
        (
            _LOCK_FH,
            _LOCK_ACQUISITION_IDENTITY,
            _STATE_DIR_LOCK_FD,
            _STATE_DIR_LOCK_IDENTITY,
            _LOCK_SOCKET,
            _LOCK_SOCKET_NAME,
        ) = (
            fh,
            lock_acquisition_identity,
            state_directory_fd,
            state_directory_lock_identity,
            instance_socket,
            socket_name,
        )
        ownership_handoff_committed = True
    except BaseException:
        if not ownership_handoff_committed:
            (
                _LOCK_FH,
                _LOCK_ACQUISITION_IDENTITY,
                _STATE_DIR_LOCK_FD,
                _STATE_DIR_LOCK_IDENTITY,
                _LOCK_SOCKET,
                _LOCK_SOCKET_NAME,
            ) = (None, None, None, None, None, None)
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if fh is not None:
                try:
                    fh.close()
                except OSError:
                    pass
            if instance_socket is not None:
                try:
                    instance_socket.close()
                except OSError:
                    pass
            if state_directory_fd is not None:
                try:
                    os.close(state_directory_fd)
                except OSError:
                    pass
        raise
    initialise_bot_health_reporting()
    log.info("Acquired instance lock %s", LOCK_FILE)


redact_secret = _observability.redact_secret


def log_json_debug(label: str, obj: object, max_chars: int = 4000) -> None:
    """Log bounded JSON with recursively redacted credential-like values."""
    return _observability.log_json_debug(
        label,
        obj,
        max_chars,
        log=log,
    )


state_debug_summary = _observability.state_debug_summary


def log_event(event: str, **fields: object) -> None:
    """Emit a stable one-line structured event for digest scripts."""
    return _observability.log_event(
        event,
        fields=fields,
        log=log,
    )


def _log_descriptive_observability_failure(message: str) -> None:
    return _observability._log_descriptive_observability_failure(
        message,
        log=log,
    )


def emit_account_root_posted(
    *,
    lane: str,
    post_id: object,
    public_text: object = None,
    quote_id: object = None,
    quote_text: object = None,
    image_summary: object = None,
    post_created_at: object = None,
) -> None:
    """Describe an already durable account root without affecting its outcome."""
    return _observability.emit_account_root_posted(
        lane=lane,
        post_id=post_id,
        public_text=public_text,
        quote_id=quote_id,
        quote_text=quote_text,
        image_summary=image_summary,
        post_created_at=post_created_at,
        _log_descriptive_observability_failure=_log_descriptive_observability_failure,
        log_event=log_event,
    )


def emit_historical_context_reply_posted(
    *,
    parent_post_id: object,
    reply_post_id: object,
    reply_text: object,
    quote_id: object,
    reply_created_at: object = None,
) -> None:
    """Describe an already durable historical-context reply without transport."""
    return _observability.emit_historical_context_reply_posted(
        parent_post_id=parent_post_id,
        reply_post_id=reply_post_id,
        reply_text=reply_text,
        quote_id=quote_id,
        reply_created_at=reply_created_at,
        _log_descriptive_observability_failure=_log_descriptive_observability_failure,
        log_event=log_event,
    )


def emit_historical_context_history_observation(item: object) -> None:
    """Emit the v1 contract only for one validated completed history item."""
    return _observability.emit_historical_context_history_observation(
        item,
        emit_historical_context_reply_posted=emit_historical_context_reply_posted,
    )


def emit_historical_context_store_observation(
    store: object,
    parent_post_id: object,
) -> None:
    """Read completed history for observability without affecting recovery."""
    return _observability.emit_historical_context_store_observation(
        store,
        parent_post_id,
        _log_descriptive_observability_failure=_log_descriptive_observability_failure,
        emit_historical_context_history_observation=emit_historical_context_history_observation,
    )


LOCAL_CONFIG_ALLOWED_KEYS = {
    # Posting schedule
    "POST_SLEEP_MIN",
    "POST_SLEEP_MAX",

    # Daily meme schedule
    "ENABLE_DAILY_MEME_POSTS",
    "MEME_TRIGGER_AFTER_HOUR",
    "MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS",
    "MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS",
    "MEME_FALLBACK_HOUR",
    "MEME_FALLBACK_MINUTE",
    "MEME_POST_TEXT",
    "RESET_MEME_CYCLE_WHEN_ALL_POSTED",
    "MEME_MIN_SECONDS_AFTER_QUOTE_POST",

    # Reply and quote-tweet lanes
    "ENABLE_AUTO_REPLIES",
    "ENABLE_QUOTE_TWEET_CHECKS",
    "ENABLE_HOT_POST_REPLY_CHECKS",
    "REPLY_CHECK_EVERY_SECONDS",
    "QUOTE_CHECK_EVERY_SECONDS",
    "QUOTE_CHECK_SPACING_RETRY_SECONDS",
    "QUOTE_REPLY_DELAY_SECONDS",
    "MAX_AUTO_REPLIES_PER_DAY",
    "MAX_QUOTE_REPLIES_PER_DAY",
    "MAX_REPLIES_PER_AUTHOR_PER_DAY",
    "MAX_MENTIONS_PER_CHECK",
    "AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD",
    "AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS",
    "AUTHOR_NO_REPLY_QUARANTINE_SECONDS",
    "MIN_SECONDS_BETWEEN_REPLIES",
    "MAX_HOT_POST_REPLIES_PER_CHECK",
    "HOT_POST_REPLY_SEARCH_API_MAX_RESULTS",
    "HOT_POST_REPLY_USE_SINCE_ID",
    "HOT_POST_REPLY_FULL_RESCAN_EVERY_CHECKS",
    "MAX_QUOTE_POSTS_PER_CHECK",
    "QUOTE_POST_LOOKBACK_MAIN_POSTS",
    "RECENT_OWN_POST_IDS_MAX",
    "QUOTE_LOOKUP_API_MAX_RESULTS",
    "QUOTE_LOOKUP_MAX_PAGES_PER_POST",
    "MENTIONS_MAX_PAGES_PER_CHECK",
    "HOT_POST_REPLY_SEARCH_MAX_PAGES_PER_CHECK",

    # Context/model/API behaviour
    "MARK_AI_REPLIES_AS_AI",
    "ALWAYS_FETCH_PARENT_FOR_CONTEXT",
    "SKIP_REPLIES_TO_OWN_AUTO_REPLIES",
    "TWEET_CACHE_MAX_AGE_SECONDS",
    "TWEET_CACHE_MAX_ITEMS",
    "ERROR_WINDOW_SECONDS",
    "MAX_X_ERRORS_PER_WINDOW",
    "MAX_OPENAI_ERRORS_PER_WINDOW",
    "COOLDOWN_AFTER_REPEATED_ERRORS_SECONDS",
    "COOLDOWN_AFTER_429_SECONDS",

    "ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING",
    "ORIGINAL_EDITORIAL_ANALYSIS_FILE",
    "ORIGINAL_EDITORIAL_SHADOW_WEIGHT",
    "ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT",

    # Operational hardening
    "STATE_BACKUP_COUNT",
    "historical_context_reply",
    "single_call_reply",
}


class LocalConfigError(RuntimeError):
    """An existing production local-config file is unsafe to apply."""


def load_strict_runtime_json(
    handle_or_document,
    *,
    label: str,
    parse_floats_as_decimal: bool = False,
) -> object:
    """Load one UTF-8 control/config document without ambiguous JSON.

    ``parse_floats_as_decimal`` preserves numeric-token semantics for callers
    which must validate mathematical integrality before any binary rounding.
    """
    return _local_config.load_strict_runtime_json(
        handle_or_document,
        label=label,
        parse_floats_as_decimal=parse_floats_as_decimal,
    )


class ReplyEvidenceUnavailable(RuntimeError):
    """The local reply-evidence corpus could not be loaded safely."""


_PRODUCTION_BOOTSTRAPPED = False
_HISTORICAL_CONTEXT_SEMANTIC_GATE: object | None = None
_HISTORICAL_CONTEXT_CORPUS_SNAPSHOT: tuple[dict, dict] | None = None
_HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON: str | None = None
_HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON: str | None = None
_REPLY_EVIDENCE_REPOSITORY: object | None = None
_REPLY_EVIDENCE_LOAD_ERROR: str | None = None


_coerce_local_config_value = _local_config._coerce_local_config_value


def _runtime_config_namespace() -> dict[str, object]:
    """Return the current root namespace for runtime configuration."""
    return globals()


def validate_runtime_config_values(values: dict[str, object]) -> list[str]:
    """Return validation errors for runtime config values.

    Numeric bounds live here for both source defaults and coerced local
    overrides. The loader checks JSON types before validating the complete
    proposed configuration; invalid override sets are rejected atomically.
    """
    return _runtime_configuration.validate_runtime_config_values(
        values,
        _runtime_config_namespace=_runtime_config_namespace,
        validate_single_call_reply_config=validate_single_call_reply_config,
    )


SOURCE_DEFAULT_CONFIG_VALUES = {
    name: copy.deepcopy(globals()[name])
    for name in LOCAL_CONFIG_ALLOWED_KEYS
    if name in globals()
}


_local_config_stat_identity = _local_config._local_config_stat_identity


def _local_configuration_owner() -> _local_config.LocalConfiguration:
    """Bind current config inputs without reading files or applying overrides."""
    return _local_config.LocalConfiguration(
        config_file=LOCAL_CONFIG_FILE,
        maximum_bytes=LOCAL_CONFIG_MAX_BYTES,
        error_type=LocalConfigError,
        os=os,
        stat=stat,
        source_defaults=SOURCE_DEFAULT_CONFIG_VALUES,
        log=log,
        validate_runtime_values=validate_runtime_config_values,
    )


def _read_stable_local_config_bytes() -> bytes | None:
    """Read one optional regular local-config file without following links."""
    return _local_configuration_owner().read_snapshot()


def load_validated_local_config_overrides() -> dict[str, object] | None:
    """Read and validate local overrides without mutating runtime globals."""
    return _local_configuration_owner().load_overrides()


def apply_local_config() -> None:
    """Apply optional local JSON config overrides without editing the bot script."""
    return _runtime_configuration.apply_local_config(
        LOCAL_CONFIG_FILE=LOCAL_CONFIG_FILE,
        _runtime_config_namespace=_runtime_config_namespace,
        load_validated_local_config_overrides=load_validated_local_config_overrides,
        log=log,
        log_json_debug=log_json_debug,
    )


SOURCE_DEFAULT_CONFIG_ERRORS = validate_runtime_config_values(
    copy.deepcopy(SOURCE_DEFAULT_CONFIG_VALUES)
)
if SOURCE_DEFAULT_CONFIG_ERRORS:
    raise RuntimeError("Invalid source default config: " + "; ".join(SOURCE_DEFAULT_CONFIG_ERRORS))


def conversational_reply_pipeline_enabled() -> bool:
    """Return whether the sole production conversational pipeline is enabled."""
    return single_call_reply.get("enabled") is True


def reply_evidence_repository():
    """Load claim evidence on first use and cache a fail-closed load failure."""
    return _runtime_service_initialisation.reply_evidence_repository(
        BASE_DIR=BASE_DIR,
        Path=Path,
        ReplyEvidenceUnavailable=ReplyEvidenceUnavailable,
        SINGLE_CALL_REPLY_RESEARCH_CORPUS_PATH=SINGLE_CALL_REPLY_RESEARCH_CORPUS_PATH,
        _get_bot_logger=_get_bot_logger,
        _get_reply_evidence_load_error=_get_reply_evidence_load_error,
        _get_reply_evidence_repository_cache=_get_reply_evidence_repository_cache,
        _set_reply_evidence_load_error=_set_reply_evidence_load_error,
        _set_reply_evidence_repository_cache=_set_reply_evidence_repository_cache,
    )


def initialise_historical_context_semantic_gate(
    packets: dict[str, dict],
) -> object:
    """Load the reviewed gate without failing the independent main-post lane."""
    return _runtime_service_initialisation.initialise_historical_context_semantic_gate(
        packets,
        BASE_DIR=BASE_DIR,
        _get_bot_logger=_get_bot_logger,
        _get_historical_context_semantic_gate=_get_historical_context_semantic_gate,
        _set_historical_context_semantic_gate=_set_historical_context_semantic_gate,
        historical_context_reply=historical_context_reply,
        log_event=log_event,
    )


def production_bootstrap(
    *,
    log_path: Path | None = None,
    configure_file_logging: bool = True,
) -> None:
    """Apply and validate deployment-local configuration exactly once."""
    global _HISTORICAL_CONTEXT_CORPUS_SNAPSHOT
    global _HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON
    global _PRODUCTION_BOOTSTRAPPED, _LIFECYCLE_AUTHORITY_ACQUIRED, log
    if _PRODUCTION_BOOTSTRAPPED:
        return
    _LIFECYCLE_AUTHORITY_ACQUIRED = True
    log = setup_logging(log_path=log_path, configure_file_logging=configure_file_logging)
    apply_local_config()
    errors = validate_runtime_config_values(
        {name: globals()[name] for name in LOCAL_CONFIG_ALLOWED_KEYS if name in globals()}
    )
    if errors:
        raise LocalConfigError(f"Invalid runtime config after loading {LOCAL_CONFIG_FILE}: " + "; ".join(errors))
    load_runtime_resources = not (
        SELF_TEST_REQUESTED or INITIALISE_REQUESTED or TEST_MODE
    )
    if load_runtime_resources:
        load_completed_research_quote_hashes()
        # History validation is read-only here because bootstrap precedes the
        # single-process lock. Durable receipt/outbox reconciliation happens
        # immediately after that lock is acquired.
        context_store = historical_context_reply_store()
        try:
            context_store.history()
        except Exception as exc:
            if HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists():
                log.critical(
                    "Historical-context history is incompatible while a durable "
                    "context receipt exists; refusing production startup",
                    exc_info=True,
                )
                raise
            _HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON = (
                f"{type(exc).__name__}: {exc}"
            )

    if (
        load_runtime_resources
        and historical_context_reply["enabled"]
        and _HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON is None
    ):
        try:
            from historical_context_formatter import load_and_validate_corpus

            historical_packets, historical_unresolved = load_and_validate_corpus(
                HISTORICAL_CONTEXT_RESEARCH_DIR,
                require_source_role_audit=True,
            )
            _HISTORICAL_CONTEXT_CORPUS_SNAPSHOT = (
                historical_packets,
                historical_unresolved,
            )
            initialise_historical_context_semantic_gate(historical_packets)
        except Exception as exc:
            _HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON = (
                f"{type(exc).__name__}: {exc}"
            )
    if load_runtime_resources and _HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON:
        reason = _HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON
        log.critical(
            "Historical-context runtime disabled for this process; ordinary "
            "posting remains available. reason=%s",
            reason,
        )
        log_event(
            "historical_context_runtime",
            status="unavailable",
            reason=reason,
            regular_post_eligibility_unchanged=True,
        )
    if not load_runtime_resources:
        log.info("Deferred optional runtime-resource loading for non-operational command")
    if not SELF_TEST_REQUESTED and not INITIALISE_REQUESTED:
        validate_production_credentials()
    _PRODUCTION_BOOTSTRAPPED = True


def require_production_bootstrap() -> None:
    """Enforce explicit bootstrap before entering an operational command."""
    if not _PRODUCTION_BOOTSTRAPPED:
        raise RuntimeError(
            "Production bootstrap has not completed; "
            "call production_bootstrap() before entering an operational command"
        )


def reconcile_runtime_historical_context_state() -> None:
    """Validate and reconcile durable context state while holding the process lock."""
    return _historical_context_runtime.reconcile_runtime_historical_context_state(
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE=HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        _set_historical_context_outbox_unavailable_reason=_set_historical_context_outbox_unavailable_reason,
        global_remote_writes_paused=global_remote_writes_paused,
        historical_context_outbox_store=historical_context_outbox_store,
        historical_context_reply_store=historical_context_reply_store,
        inspect_transport_state=inspect_transport_state,
        journal_path_for_receipt=journal_path_for_receipt,
        log=log,
        log_event=log_event,
        now_epoch=now_epoch,
        receipt_namespace_entry_exists=receipt_namespace_entry_exists,
        recover_interrupted_historical_context_attempt=recover_interrupted_historical_context_attempt,
        resume_source_receipt_retirement_for_control_snapshot=resume_source_receipt_retirement_for_control_snapshot,
    )


def required_installation_files_missing() -> list[Path]:
    """Return durable files that cannot be recovered from local backups."""
    return _installation_lifecycle.required_installation_files_missing(
        HISTORICAL_CONTEXT_REPLY_HISTORY_FILE=HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE=HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE,
        IMAGES_USED_FILE=IMAGES_USED_FILE,
        INSTALLATION_IN_PROGRESS_FILE=INSTALLATION_IN_PROGRESS_FILE,
        LINES_USED_FILE=LINES_USED_FILE,
        STATE_BACKUP_COUNT=STATE_BACKUP_COUNT,
        STATE_FILE=STATE_FILE,
        durable_state_namespace_is_owned_single_link_file=durable_state_namespace_is_owned_single_link_file,
        os=os,
        remote_source_receipt_paths=remote_source_receipt_paths,
        retirement_ledger_is_blocking=retirement_ledger_is_blocking,
        retirement_ledger_paths=retirement_ledger_paths,
    )


def require_established_installation() -> None:
    """Refuse operational startup after unexpected durable-state loss."""
    return _installation_lifecycle.require_established_installation(
        required_installation_files_missing=required_installation_files_missing,
    )


def recover_interrupted_retirement_ledger_exchanges_at_startup() -> tuple[Path, ...]:
    """Finish exact ledger exchanges before installation completeness checks.

    A completed ledger transition can leave its old generation in the fixed
    exchange pathname if the process dies between the atomic exchange and its
    final cleanup.  That pathname must block every remote preflight, but under
    the already-held singleton lock a current ledger-aware activation may
    deterministically finish the exact predecessor/successor exchange.  No
    missing, malformed, ambiguous, or pre-ledger installation is repaired
    here.
    """
    return _installation_lifecycle.recover_interrupted_retirement_ledger_exchanges_at_startup(
        ProtocolActivationError=ProtocolActivationError,
        REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE=REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE,
        inspect_protocol_activation=inspect_protocol_activation,
        inspect_retirement_ledger=inspect_retirement_ledger,
        recover_retirement_ledger_exchange_if_present=recover_retirement_ledger_exchange_if_present,
        remote_source_receipt_paths=remote_source_receipt_paths,
        transaction_mutation_authority=transaction_mutation_authority,
    )


def require_established_installation_after_ledger_recovery() -> None:
    """Recover exact ledger exchanges, then require a complete installation."""
    return _installation_lifecycle.require_established_installation_after_ledger_recovery(
        log=log,
        recover_interrupted_retirement_ledger_exchanges_at_startup=recover_interrupted_retirement_ledger_exchanges_at_startup,
        require_established_installation=require_established_installation,
    )


def initialise_installation() -> int:
    """Create a new state/history set without starting production."""
    return _installation_lifecycle.initialise_installation(
        AMBIGUOUS_POST_OUTCOME_FILE=AMBIGUOUS_POST_OUTCOME_FILE,
        AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE=AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE,
        BASE_DIR=BASE_DIR,
        CONFIRMED_REPLY_RECEIPT_FILE=CONFIRMED_REPLY_RECEIPT_FILE,
        ENABLE_DAILY_MEME_POSTS=ENABLE_DAILY_MEME_POSTS,
        HISTORICAL_CONTEXT_REPLY_HISTORY_FILE=HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE=HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE,
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE=HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        IMAGES_USED_FILE=IMAGES_USED_FILE,
        INSTALLATION_IN_PROGRESS_FILE=INSTALLATION_IN_PROGRESS_FILE,
        INSTALLATION_MARKER_FILE=INSTALLATION_MARKER_FILE,
        JOURNAL_RETIREMENT_PREFIX=JOURNAL_RETIREMENT_PREFIX,
        JOURNAL_STAGING_PREFIX=JOURNAL_STAGING_PREFIX,
        LINES_USED_FILE=LINES_USED_FILE,
        MEDIA_RETIREMENT_GUARD_PREFIX=MEDIA_RETIREMENT_GUARD_PREFIX,
        MEDIA_TRANSITION_PREFIX=MEDIA_TRANSITION_PREFIX,
        MEDIA_UPLOAD_RECEIPT_FILE=MEDIA_UPLOAD_RECEIPT_FILE,
        MEME_POST_RECEIPT_FILE=MEME_POST_RECEIPT_FILE,
        POST_SLEEP_MIN=POST_SLEEP_MIN,
        REGULAR_POST_RECEIPT_FILE=REGULAR_POST_RECEIPT_FILE,
        REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_AUDIT_BASENAME=REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_AUDIT_BASENAME,
        REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE=REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE,
        STATE_BACKUP_COUNT=STATE_BACKUP_COUNT,
        STATE_FILE=STATE_FILE,
        acquire_instance_lock=acquire_instance_lock,
        atomic_write_json=atomic_write_json,
        default_state=default_state,
        ensure_meme_schedule_initialized=ensure_meme_schedule_initialized,
        fence_path_for_journal=fence_path_for_journal,
        fsync_parent_dir=fsync_parent_dir,
        historical_context_outbox_store=historical_context_outbox_store,
        historical_context_reply_store=historical_context_reply_store,
        initialise_retirement_ledger=initialise_retirement_ledger,
        journal_path_for_receipt=journal_path_for_receipt,
        log=log,
        media_fence_path_for_receipt=media_fence_path_for_receipt,
        now_epoch=now_epoch,
        os=os,
        remote_source_receipt_paths=remote_source_receipt_paths,
        require_production_bootstrap=require_production_bootstrap,
        retirement_auxiliary_paths=retirement_auxiliary_paths,
        retirement_ledger_paths=retirement_ledger_paths,
        save_image_used_basenames=save_image_used_basenames,
        save_quote_used_hashes=save_quote_used_hashes,
        save_state=save_state,
        transaction_mutation_authority=transaction_mutation_authority,
    )


# ---------------------------------------------------------------------
# Runtime control / pause file
# ---------------------------------------------------------------------

_CONTROL_CACHE: dict[str, object] = {
    "signature": None,
    "data": {},
    "has_valid": False,
    "failure_signature": None,
}
RUNTIME_CONTROL_MAX_BYTES = 64 * 1024


class _RuntimeControlAbsent(FileNotFoundError):
    """The optional control pathname was absent before a read began."""

CONTROL_BOOLEAN_KEYS = _runtime_control.CONTROL_BOOLEAN_KEYS
CONTROL_TIME_KEYS = _runtime_control.CONTROL_TIME_KEYS
CONTROL_METADATA_KEYS = _runtime_control.CONTROL_METADATA_KEYS
CONTROL_ALLOWED_KEYS = _runtime_control.CONTROL_ALLOWED_KEYS


def parse_control_time(value: object) -> int:
    """Parse a runtime-control timestamp into an epoch value."""
    return _runtime_control.parse_control_time(
        value,
        Decimal=Decimal,
        MAX_REASONABLE_STATE_EPOCH=MAX_REASONABLE_STATE_EPOCH,
        datetime=datetime,
        math=math,
    )


def validate_control_document(data: object) -> dict:
    """Validate control document."""
    return _runtime_control.validate_control_document(
        data,
        CONTROL_ALLOWED_KEYS=CONTROL_ALLOWED_KEYS,
        CONTROL_BOOLEAN_KEYS=CONTROL_BOOLEAN_KEYS,
        CONTROL_TIME_KEYS=CONTROL_TIME_KEYS,
        Decimal=Decimal,
        parse_control_time=parse_control_time,
    )


def _runtime_controls_owner() -> _runtime_control.RuntimeControls:
    """Bind current control authorities without reading the file, cache or clock."""
    return _runtime_control.RuntimeControls(
        control_file=CONTROL_FILE,
        cache=_CONTROL_CACHE,
        maximum_bytes=RUNTIME_CONTROL_MAX_BYTES,
        absent_error=_RuntimeControlAbsent,
        hashlib=hashlib,
        os=os,
        stat=stat,
        log=log,
        log_json_debug=log_json_debug,
        validate_document=validate_control_document,
        now_epoch=now_epoch,
        parse_time=parse_control_time,
        datetime=datetime,
        log_event=log_event,
    )


def control_failure_result(reason: str, *, signature: object) -> dict:
    """Build the fail-closed result for an invalid runtime-control document."""
    return _runtime_controls_owner().failure_result(reason, signature=signature)


def _runtime_control_stat_identity(file_stat: os.stat_result) -> tuple[int, ...]:
    """Return the fields which must remain stable for one control snapshot."""
    return _runtime_controls_owner().stat_identity(file_stat)


def _read_stable_runtime_control() -> tuple[bytes, tuple[object, ...]]:
    """Read one regular, non-symlink control file as a stable byte snapshot."""
    return _runtime_controls_owner().read_snapshot()


def load_control() -> dict:
    """Load and validate the optional fail-safe runtime-control document."""
    return _runtime_controls_owner().load()


def control_bool(data: dict, key: str) -> bool:
    """Return a validated boolean runtime-control value."""
    return _runtime_controls_owner().boolean(data, key)


def control_pause_active(data: dict, *keys: str) -> tuple[bool, str, int]:
    """Return whether the runtime-control document currently pauses a lane."""
    return _runtime_controls_owner().pause_active(data, *keys)


def lane_paused(*lane_keys: str) -> bool:
    """Return whether a named posting lane is paused."""
    return _runtime_controls_owner().lane_paused(*lane_keys)


def global_remote_writes_paused() -> bool:
    """Return whether the runtime control pauses every remote-write lane."""
    return _runtime_controls_owner().global_paused()


# ---------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------

CONSUMER_KEY = os.getenv("X_CONSUMER_KEY", "")
CONSUMER_SECRET = os.getenv("X_CONSUMER_SECRET", "")
ACCESS_TOKEN = os.getenv("X_ACCESS_TOKEN", "")
ACCESS_SECRET = os.getenv("X_ACCESS_SECRET", "")
MY_USER_ID = os.getenv("X_MY_USER_ID", "")
MY_USERNAME = os.getenv("X_MY_USERNAME", "MrsMThatcher").strip().lstrip("@")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")

# Optional. If set, quote lookup uses Bearer auth. If not set, the script
# falls back to OAuth1, as used by the other X v2 calls.
X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN", "")

log.debug("Credential presence:")
log.debug("  X_CONSUMER_KEY=%s", redact_secret(CONSUMER_KEY))
log.debug("  X_CONSUMER_SECRET=%s", redact_secret(CONSUMER_SECRET))
log.debug("  X_ACCESS_TOKEN=%s", redact_secret(ACCESS_TOKEN))
log.debug("  X_ACCESS_SECRET=%s", redact_secret(ACCESS_SECRET))
log.debug("  X_MY_USER_ID=%s", MY_USER_ID or "<missing>")
log.debug("  OPENAI_API_KEY=%s", redact_secret(OPENAI_API_KEY))
log.debug("  X_BEARER_TOKEN=%s", redact_secret(X_BEARER_TOKEN))

def validate_production_credentials() -> None:
    """Validate required credentials without logging their values."""
    return _runtime_configuration.validate_production_credentials(
        ACCESS_SECRET=ACCESS_SECRET,
        ACCESS_TOKEN=ACCESS_TOKEN,
        CONSUMER_KEY=CONSUMER_KEY,
        CONSUMER_SECRET=CONSUMER_SECRET,
        ENABLE_AUTO_REPLIES=ENABLE_AUTO_REPLIES,
        MY_USER_ID=MY_USER_ID,
        OPENAI_API_KEY=OPENAI_API_KEY,
        single_call_reply=single_call_reply,
    )

AUTH = OAuth1(
    CONSUMER_KEY,
    client_secret=CONSUMER_SECRET,
    resource_owner_key=ACCESS_TOKEN,
    resource_owner_secret=ACCESS_SECRET,
)


def normalise_base_url(raw: str, *, require_origin: bool = False, provider: str | None = None) -> str:
    """Return one validated API base or fail during configuration.

    Route classification is performed against paths which this module appends
    itself.  A configured path prefix, query, fragment or user-info component
    could make the literal route and the prepared on-wire route disagree, so
    the X request and upload bases must be origins. Versioned OpenAI and xAI
    bases remain usable through this generic normalizer. Credential-bearing
    configuration passes its explicit provider to prevent cross-provider
    credential disclosure.
    """
    return _request_route_values.normalise_base_url(
        raw,
        require_origin=require_origin,
        provider=provider,
        test_mode=TEST_MODE,
        _normalise_x_origin_before_runtime_configuration=_normalise_x_origin_before_runtime_configuration,
    )


def endpoint_host(url: str) -> str:
    """Return the normalised host from an API endpoint URL."""
    return _request_route_values.endpoint_host(
        url,
    )


def endpoint_is_loopback(url: str) -> bool:
    """Return whether one configured endpoint is an explicit loopback host."""
    return _request_route_values.endpoint_is_loopback(
        url,
    )


X_BASE = normalise_base_url(
    os.getenv("X_API_BASE_URL", "https://api.x.com"),
    require_origin=True,
)
X_UPLOAD_BASE = normalise_base_url(
    os.getenv("X_UPLOAD_BASE_URL", X_BASE),
    require_origin=True,
)
OPENAI_BASE = normalise_base_url(
    os.getenv("OPENAI_API_BASE_URL", "https://api.openai.com/v1"), provider="openai",
)
LIVE_ENDPOINT_TEST_OVERRIDE_PHRASE = "I_UNDERSTAND_THIS_CAN_POST_TO_LIVE_X"


# A confirmed-post transaction can make two sequential X create requests when
# the made_with_ai compatibility fallback is needed.  Keep both total budgets,
# plus local receipt persistence, inside the service's 180-second stop window.
MAX_REQUEST_TIMEOUT_SECONDS = 60.0
MAX_REQUEST_CONNECT_TIMEOUT_SECONDS = 10.0


def parse_request_timeout_seconds() -> float:
    """Parse and validate the configured HTTP timeout."""
    raw = os.getenv("MRS_REQUEST_TIMEOUT_SECONDS", "60")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        log.error("Invalid MRS_REQUEST_TIMEOUT_SECONDS=%r; using default 60", raw)
        return 60.0

    if (
        not math.isfinite(value)
        or value <= 0
        or value > MAX_REQUEST_TIMEOUT_SECONDS
    ):
        log.error("Invalid MRS_REQUEST_TIMEOUT_SECONDS=%r; using default 60", raw)
        return 60.0

    return value


REQUEST_TIMEOUT_SECONDS = parse_request_timeout_seconds()


def request_timeout() -> Timeout:
    """Return one combined Requests connect/read budget for an HTTP call."""
    return Timeout(
        total=REQUEST_TIMEOUT_SECONDS,
        connect=min(
            MAX_REQUEST_CONNECT_TIMEOUT_SECONDS,
            REQUEST_TIMEOUT_SECONDS,
        ),
    )


if (
    TEST_MODE
    and os.getenv("MRS_ALLOW_LIVE_ENDPOINTS_IN_TEST") != LIVE_ENDPOINT_TEST_OVERRIDE_PHRASE
):
    live_endpoints = [
        f"{name}={value}"
        for name, value in (
            ("X_API_BASE_URL", X_BASE),
            ("X_UPLOAD_BASE_URL", X_UPLOAD_BASE),
            ("OPENAI_API_BASE_URL", OPENAI_BASE),
        )
        if not endpoint_is_loopback(value)
    ]

    if live_endpoints:
        log.critical(
            "Refusing to run in MRS_TEST_MODE with live endpoint(s): %s. "
            "Set local fake endpoints or MRS_ALLOW_LIVE_ENDPOINTS_IN_TEST=%s to override deliberately.",
            ", ".join(live_endpoints),
            LIVE_ENDPOINT_TEST_OVERRIDE_PHRASE,
        )
        sys.exit(2)


# ---------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------

class ApiError(Exception):
    """Raised when an X or OpenAI API operation fails."""
    def __init__(
        self,
        message: str,
        *,
        service: str,
        status_code: int | None = None,
        reset_epoch: int | None = None,
        request_method: str | None = None,
        request_path: str | None = None,
        x_error_response: ValidatedXErrorResponse | None = None,
        x_error_message_fallback: bool = True,
    ) -> None:
        """Initialise the API error."""
        super().__init__(message)
        self.service = service
        self.status_code = status_code
        self.reset_epoch = reset_epoch
        self.request_method = (
            str(request_method).upper() if request_method else None
        )
        self.request_path = str(request_path) if request_path else None
        self.x_error_response = x_error_response
        self.x_error_message_fallback = x_error_message_fallback is True


class AmbiguousRemotePostOutcome(ApiError):
    """X may have accepted a write that could not be confirmed safely."""

    def __init__(
        self,
        message: str,
        *,
        response_body_length: int | None = None,
        response_body_sha256: str | None = None,
        diagnostic_sha256: str | None = None,
        diagnostic_event: str | None = None,
        **kwargs: object,
    ) -> None:
        """Retain optional compact evidence for response-bound anomalies."""

        super().__init__(message, **kwargs)
        self.response_body_length = response_body_length
        self.response_body_sha256 = response_body_sha256
        self.diagnostic_sha256 = diagnostic_sha256
        self.diagnostic_event = diagnostic_event


class ProvedRemotePostNonSuccess(ApiError):
    """X deterministically rejected one exact conversational reply create."""

    def __init__(
        self,
        message: str,
        *,
        remote_non_success_proof: DeterministicReplyCreateRejectionProof,
        **kwargs: object,
    ) -> None:
        """Bind the exception to one classifier-issued registered proof."""

        if not isinstance(
            remote_non_success_proof,
            DeterministicReplyCreateRejectionProof,
        ):
            raise ValueError(
                "proved remote non-success requires a classifier-issued proof"
            )
        super().__init__(message, **kwargs)
        self.remote_non_success_proof = remote_non_success_proof


class PaginationCursorProtocolError(ApiError):
    """An X collection returned a pagination-token cycle."""


class _MentionBacklogContinuationLimit(RuntimeError):
    """Stop one mention traversal after safely resetting its oversized cursor history."""


class RemoteOperationsPaused(RuntimeError):
    """A global runtime-control pause blocked a remote operation."""

    propagate_from_single_call_pipeline = True


def api_error_proves_remote_non_success(error: BaseException) -> bool:
    """Return whether an X create is proved not to have succeeded remotely.

    HTTP status alone is not an idempotency or reconciliation contract.  In
    particular, the project has no provider-contract evidence which proves
    that every ``POST /2/tweets`` 4xx response excludes an accepted write.
    A local preflight pause proves no transmission.  The only post-boundary
    case is a classifier-issued proof that one exact conversational reply was
    rejected for target-specific reasons.  Every other exception remains
    fail-closed.
    """
    if isinstance(error, RemoteOperationsPaused):
        return True
    return bool(
        isinstance(error, ProvedRemotePostNonSuccess)
        and reply_create_rejection_payload(
            error.remote_non_success_proof
        )
        is not None
    )


def require_remote_operation_unpaused(
    operation: str,
    *,
    transaction_authorization: object | None = None,
) -> None:
    """Fail before a remote boundary while a global pause is active."""
    require_instance_lock_for_remote_write(operation)
    if isinstance(
        transaction_authorization,
        (TransportAuthority, MediaUploadAuthority),
    ):
        # The exact payload-bound journal is validated and consumed again at
        # the transport boundary.  Recheck only process-wide incident state
        # here so the transaction does not block itself.
        block_if_remote_write_safety_incident_latched()
        if historical_context_outbox_remote_attempt_is_blocking(
            prepared_transport_authority=(
                transaction_authorization
                if isinstance(transaction_authorization, TransportAuthority)
                else None
            )
        ):
            raise AmbiguousRemotePostOutcome(
                "A historical-context outbox attempt may have reached remote "
                "transport and blocks this remote operation",
                service="x",
            )
    else:
        # Direct transport/provider calls have no prepared-receipt authority.
        # Every unresolved transaction lane must therefore block them.
        block_if_ambiguous_remote_post()
    if not global_remote_writes_paused():
        return
    log.warning(
        "Global runtime control pause blocked remote operation: %s",
        operation,
    )
    raise RemoteOperationsPaused(
        f"Global runtime control pause blocks remote operation: {operation}"
    )


def api_error_is_reply_not_allowed(error: Exception) -> bool:
    """Return whether one reply target deterministically rejected the write."""
    return classify_x_api_error(error) in {
        X_API_ERROR_REPLY_TARGET_RESTRICTED,
        X_API_ERROR_REPLY_TARGET_UNAVAILABLE,
    }


def api_error_is_permanent_target_failure(error: Exception) -> bool:
    """Return true for target-specific failures that should not trip breakers."""
    return classify_x_api_error(error) == X_API_ERROR_LOOKUP_TARGET_UNAVAILABLE


# ---------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------

DURABLE_RUNTIME_JSON_MAX_BYTES = 64 * 1024 * 1024


class UnsafeDurableStateNamespace(RuntimeError):
    """A state/history pathname exists but is not one stable owned file."""


def durable_state_namespace_is_owned_single_link_file(
    path: Path,
    *,
    maximum_bytes: int | None = DURABLE_RUNTIME_JSON_MAX_BYTES,
) -> bool:
    """Return true only for one current-owner ordinary-file namespace entry."""
    return _durable_json_io.durable_state_namespace_is_owned_single_link_file(
        path,
        maximum_bytes=maximum_bytes,
        os=os,
        stat=stat,
    )


def read_stable_owned_json_bytes_no_follow(
    path: Path,
) -> tuple[bool, bytes | None]:
    """Read one bounded stable owned JSON authority without following links."""
    return _durable_json_io.read_stable_owned_json_bytes_no_follow(
        path,
        DURABLE_RUNTIME_JSON_MAX_BYTES=DURABLE_RUNTIME_JSON_MAX_BYTES,
        UnsafeDurableStateNamespace=UnsafeDurableStateNamespace,
        durable_state_namespace_is_owned_single_link_file=durable_state_namespace_is_owned_single_link_file,
        os=os,
        stat=stat,
    )

coerce_used_set = _used_history.coerce_used_set


used_set_to_sorted_list = _used_history.used_set_to_sorted_list


def _used_history_owner() -> _used_history.UsedHistory:
    """Bind current used-history paths and authorities without reading files."""
    return _used_history.UsedHistory(
        corrupt_error=CorruptUsedHistoryError,
        unsafe_namespace=UnsafeDurableStateNamespace,
        json=json,
        log=log,
        read_stable_bytes=read_stable_owned_json_bytes_no_follow,
        write_json=atomic_write_json,
        re=re,
        hashlib=hashlib,
        quote_hashes_by_line=current_quote_hashes_by_line,
        quote_history_file=LINES_USED_FILE,
        legacy_quote_file=PICKLE_FILE,
        quote_analysis=load_quote_analysis,
        path_type=Path,
        image_history_file=IMAGES_USED_FILE,
        legacy_image_file=IMAGE_PICKLE_FILE,
        image_analysis=load_image_analysis,
    )


def load_used_set(path: Path, *, legacy_pickle_path: Path | None = None) -> set:
    """Load a fail-closed durable used-history set."""
    return _used_history_owner().load_used_set(path, legacy_pickle_path=legacy_pickle_path)


def save_used_set(path: Path, value: set, *, durable: bool = False) -> None:
    """Persist a used-history set atomically."""
    return _used_history_owner().save_used_set(path, value, durable=durable)


def default_state() -> dict:
    """Build a new runtime-state document with safe defaults."""
    return _runtime_state_helpers.default_state(
        STATE_MINIMUM_READER_VERSION=STATE_MINIMUM_READER_VERSION,
    )


append_unique_capped = _runtime_state_helpers.append_unique_capped


append_unique_durable = _runtime_state_helpers.append_unique_durable


def _state_values_owner() -> _state_value_normalisation.StateValues:
    """Bind current value-normalization policy without inspecting caller state."""
    return _state_value_normalisation.StateValues(
        log=log,
        maximum_epoch=MAX_REASONABLE_STATE_EPOCH,
    )


bounded_tweet_id_value = _state_value_normalisation.bounded_tweet_id_value


def normalise_state_int(value: object, *, key: str, path: Path) -> int | None:
    """Normalise state int."""
    return _state_values_owner().integer(value, key=key, path=path)


def normalise_state_epoch(value: object, *, key: str, path: Path) -> int | None:
    """Normalise state epoch."""
    return _state_values_owner().epoch(value, key=key, path=path)


def normalise_string_list(value: object, *, key: str, path: Path) -> list[str] | None:
    """Normalise string list."""
    return _state_values_owner().strings(value, key=key, path=path)


def normalise_int_list(value: object, *, key: str, path: Path) -> list[int] | None:
    """Normalise int list."""
    return _state_values_owner().integers(value, key=key, path=path)


def normalise_epoch_list(value: object, *, key: str, path: Path) -> list[int] | None:
    """Normalise epoch list."""
    return _state_values_owner().epochs(value, key=key, path=path)


def normalise_string_map(value: object, *, key: str, path: Path) -> dict[str, str] | None:
    """Normalise string map."""
    return _state_values_owner().string_map(value, key=key, path=path)


def normalise_int_map(value: object, *, key: str, path: Path) -> dict[str, int] | None:
    """Normalise int map."""
    return _state_values_owner().integer_map(value, key=key, path=path)


def normalise_record_map(value: object, *, key: str, path: Path) -> dict[str, dict] | None:
    """Normalise record map."""
    return _state_values_owner().record_map(value, key=key, path=path)


def quote_repeated_cursor_suppression_record(
    post_id: object,
    value: object,
    *,
    current_epoch: int,
    allow_expired: bool = False,
) -> dict[str, object] | None:
    """Delegate to quote discovery with current root dependencies."""
    return _quote_discovery.quote_repeated_cursor_suppression_record(
        post_id,
        value,
        current_epoch=current_epoch,
        allow_expired=allow_expired,
        MAX_REASONABLE_STATE_EPOCH=MAX_REASONABLE_STATE_EPOCH,
        QUOTE_REPEATED_CURSOR_BACKOFF_SECONDS=QUOTE_REPEATED_CURSOR_BACKOFF_SECONDS,
    )


def normalise_quote_repeated_cursor_suppressions(
    value: object,
    *,
    current_epoch: int | None = None,
) -> tuple[dict[str, dict[str, object]], int]:
    """Delegate to quote discovery with current root dependencies."""
    return _quote_discovery.normalise_quote_repeated_cursor_suppressions(
        value,
        current_epoch=current_epoch,
        QUOTE_REPEATED_CURSOR_SUPPRESSION_MAX_ENTRIES=QUOTE_REPEATED_CURSOR_SUPPRESSION_MAX_ENTRIES,
        now_epoch=now_epoch,
        quote_repeated_cursor_suppression_record=quote_repeated_cursor_suppression_record,
    )


completed_mention_watermark_covers_target = _reply_evaluation_state.completed_mention_watermark_covers_target


def _reply_evaluation_owner() -> _reply_evaluation_state.ReplyEvaluations:
    """Bind current terminal evaluation limits without reading state or the clock."""
    return _reply_evaluation_state.ReplyEvaluations(
        now_epoch=now_epoch,
        log=log,
        quarantine_evidence_policy=AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY,
        maximum_state_epoch=MAX_REASONABLE_STATE_EPOCH,
        maximum_records=REPLY_EVALUATION_MAX_RECORDS,
        minimum_retention_seconds=REPLY_EVALUATION_MIN_RETENTION_SECONDS,
    )


def prune_completed_mention_quarantine_evaluations(state: dict) -> int:
    """Drop unsafe legacy or watermark-covered local quarantine skips."""
    return _reply_evaluation_owner().prune_completed_mentions(state)


def prune_reply_evaluation_records(
    state: dict,
    *,
    current_epoch: int | None = None,
) -> None:
    """Prune old terminal evaluations while preserving recent replay protection."""
    return _reply_evaluation_owner().prune(state, current_epoch=current_epoch)


def _author_quarantine_owner() -> _author_quarantines.AuthorQuarantines:
    """Bind current author quarantine policy without reading state or the clock."""
    return _author_quarantines.AuthorQuarantines(
        now_epoch=now_epoch,
        log=log,
        log_event=log_event,
        maximum_state_epoch=MAX_REASONABLE_STATE_EPOCH,
        threshold=AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD,
        window_seconds=AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS,
        quarantine_seconds=AUTHOR_NO_REPLY_QUARANTINE_SECONDS,
        evidence_policy=AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY,
    )


def author_no_reply_epoch_limit() -> int:
    """Return retention sized from the effective runtime quarantine threshold."""
    return _author_quarantine_owner().epoch_limit()


def prune_author_evaluation_quarantines(
    state: dict,
    *,
    current_epoch: int | None = None,
) -> bool:
    """Expire quarantines and discard author strikes outside the live window."""
    return _author_quarantine_owner().prune(state, current_epoch=current_epoch)


def active_author_evaluation_quarantine(
    state: dict,
    author_id: str,
    *,
    current_epoch: int | None = None,
) -> dict | None:
    """Return an active mention-lane author quarantine, expiring old state first."""
    return _author_quarantine_owner().active(state, author_id, current_epoch=current_epoch)


def record_qualifying_author_no_reply(
    state: dict,
    author_id: str,
    *,
    current_epoch: int | None = None,
    explicit_spam_or_abuse: bool = True,
) -> bool:
    """Add one mechanically valid editorial no-reply strike for an author."""
    return _author_quarantine_owner().record_no_reply(
        state,
        author_id,
        current_epoch=current_epoch,
        explicit_spam_or_abuse=explicit_spam_or_abuse,
    )


clear_author_evaluation_quarantine_history = _author_quarantines.clear_author_evaluation_quarantine_history


def _tweet_lookup_cache_owner() -> _tweet_lookup_cache.TweetLookupCache:
    """Bind current lookup/cache boundaries without clocks, requests or state access."""
    return _tweet_lookup_cache.TweetLookupCache(
        normalise_state_epoch=normalise_state_epoch,
        maximum_age_seconds=TWEET_CACHE_MAX_AGE_SECONDS,
        maximum_items=TWEET_CACHE_MAX_ITEMS,
        log=log,
        now_epoch=now_epoch,
        maximum_recent_own_posts=RECENT_OWN_POST_IDS_MAX,
        user_id=MY_USER_ID,
        state_file=STATE_FILE,
        current_datetime=current_datetime,
        api_error=ApiError,
        log_json_debug=log_json_debug,
        request=x_request,
        is_permanent_target_failure=api_error_is_permanent_target_failure,
        save_state=save_state,
    )


def normalise_tweet_cache_entry(tweet_id: object, entry: dict, *, path: Path) -> dict[str, object] | None:
    """Delegate tweet lookup/cache work with current root dependencies."""
    return _tweet_lookup_cache_owner().normalise_entry(tweet_id, entry, path=path)


def normalise_tweet_cache(value: object, *, path: Path) -> dict[str, dict] | None:
    """Delegate tweet lookup/cache work with current root dependencies."""
    return _tweet_lookup_cache_owner().normalise(value, path=path)


def _mention_authority_owner() -> _mention_authority.MentionAuthority:
    """Bind current mention authority policy without inspecting or saving state."""
    return _mention_authority.MentionAuthority(
        state_file=STATE_FILE,
        maximum_epoch=MAX_REASONABLE_STATE_EPOCH,
        token_limit=MENTION_BACKLOG_CONTINUATION_TOKEN_LIMIT,
        log=log,
        log_event=log_event,
    )


def normalise_mention_pagination(value: object, *, path: Path) -> dict[str, str] | None:
    """Delegate to mention authority with current root dependencies."""
    return _mention_authority_owner().normalise_pagination(value, path=path)


def normalise_mention_backlog_reset_guard(
    value: object,
    *,
    path: Path,
) -> dict[str, object] | None:
    """Delegate to mention authority with current root dependencies."""
    return _mention_authority_owner().normalise_reset_guard(value, path=path)


active_mention_backlog_reset_guard = _mention_authority.active_mention_backlog_reset_guard


def normalise_mention_backlog(
    value: object,
    *,
    path: Path,
    reset_token_overflow: bool = False,
) -> dict | None:
    """Delegate to mention authority with current root dependencies."""
    return _mention_authority_owner().normalise_backlog(value, path=path, reset_token_overflow=reset_token_overflow)


def canonical_mention_pending_candidates(
    value: object,
    *,
    path: Path,
) -> dict[str, dict] | None:
    """Delegate to mention authority with current root dependencies."""
    return _mention_authority_owner().canonical_candidates(value, path=path)


def _emit_mention_authority_recovery(
    recovery: dict[str, object],
    *,
    path: Path,
    recovery_events: list[dict[str, object]] | None,
) -> None:
    """Delegate to mention authority with current root dependencies."""
    return _mention_authority_owner().emit_recovery(recovery, path=path, recovery_events=recovery_events)


_reset_mention_candidate_authority = _mention_authority._reset_mention_candidate_authority


def validate_pending_mention_candidate_authority(
    state: dict,
    *,
    path: Path,
    recover_pending_identity: bool,
    recovery_events: list[dict[str, object]] | None = None,
) -> tuple[bool, bool]:
    """Delegate to mention authority with current root dependencies."""
    return _mention_authority_owner().validate_pending(
        state,
        path=path,
        recover_pending_identity=recover_pending_identity,
        recovery_events=recovery_events,
    )


def mention_pagination_has_canonical_page_ownership(
    state: dict,
    pagination: object,
    *,
    target_id: str,
) -> bool:
    """Delegate to mention authority with current root dependencies."""
    return _mention_authority_owner().owns_page(state, pagination, target_id=target_id)


def normalise_author_evaluation_quarantines(value: object, *, path: Path) -> dict | None:
    """Validate compact per-author no-reply strike and quarantine records."""
    return _author_quarantine_owner().normalise(value, path=path)


def normalise_optional_scalar(value: object, *, key: str, path: Path) -> str | None:
    """Normalise optional scalar."""
    return _state_values_owner().optional_scalar(value, key=key, path=path)


def normalise_optional_numeric_id(value: object, *, key: str, path: Path) -> str | None:
    """Normalise optional numeric ID."""
    return _state_values_owner().optional_id(value, key=key, path=path)


def validate_meme_schedule_state(state: dict, *, path: Path) -> bool:
    """Validate meme schedule state."""
    return _state_candidate_validation.validate_meme_schedule_state(
        state,
        path=path,
        MAIN_POST_SCHEDULE_TIMEZONE=MAIN_POST_SCHEDULE_TIMEZONE,
        MEME_SCHEDULE_MODES=MEME_SCHEDULE_MODES,
        log=log,
        safe_bound_schedule_date_str=safe_bound_schedule_date_str,
        valid_receipt_epoch=valid_receipt_epoch,
    )


def validate_meme_schedule_version_for_candidate(state: dict, *, path: Path) -> bool:
    """Validate meme schedule version for candidate."""
    return _state_candidate_validation.validate_meme_schedule_version_for_candidate(
        state,
        path=path,
        MEME_SCHEDULE_VERSION=MEME_SCHEDULE_VERSION,
        log=log,
        validate_meme_schedule_state=validate_meme_schedule_state,
    )


class IncompatibleStateReaderError(RuntimeError):
    """Raised when durable state requires a newer executable."""


def require_compatible_state_reader(
    state: dict,
    *,
    path: Path,
    reader_version: int | None = None,
) -> int:
    """Return the declared minimum after rejecting an incompatible reader."""
    return _state_candidate_validation.require_compatible_state_reader(
        state,
        path=path,
        reader_version=reader_version,
        IncompatibleStateReaderError=IncompatibleStateReaderError,
        STATE_READER_VERSION=STATE_READER_VERSION,
    )


def state_document_for_persistence(state: dict) -> dict:
    """Return state with the reader declaration and pre-reader rollback fence."""
    return _state_persistence.state_document_for_persistence(
        state,
        STATE_FILE=STATE_FILE,
        STATE_MINIMUM_READER_VERSION=STATE_MINIMUM_READER_VERSION,
        STATE_PREVIOUS_READER_COMPATIBILITY_FENCES=STATE_PREVIOUS_READER_COMPATIBILITY_FENCES,
        STATE_READER_COMPATIBILITY_FENCE=STATE_READER_COMPATIBILITY_FENCE,
        require_compatible_state_reader=require_compatible_state_reader,
    )


def normalise_state_candidate(
    state: dict,
    *,
    path: Path,
    recovery_events: list[dict[str, object]] | None = None,
    recover_pending_identity: bool = False,
) -> dict | None:
    """Normalise state candidate."""
    return _state_candidate_validation.normalise_state_candidate(
        state,
        path=path,
        recovery_events=recovery_events,
        recover_pending_identity=recover_pending_identity,
        MENTION_BACKLOG_CONTINUATION_TOKEN_LIMIT=MENTION_BACKLOG_CONTINUATION_TOKEN_LIMIT,
        STATE_MINIMUM_READER_VERSION=STATE_MINIMUM_READER_VERSION,
        canonical_mention_pending_candidates=canonical_mention_pending_candidates,
        default_state=default_state,
        log=log,
        normalise_author_evaluation_quarantines=normalise_author_evaluation_quarantines,
        normalise_epoch_list=normalise_epoch_list,
        normalise_int_map=normalise_int_map,
        normalise_mention_backlog=normalise_mention_backlog,
        normalise_mention_backlog_reset_guard=normalise_mention_backlog_reset_guard,
        normalise_mention_pagination=normalise_mention_pagination,
        normalise_optional_numeric_id=normalise_optional_numeric_id,
        normalise_optional_scalar=normalise_optional_scalar,
        normalise_quote_repeated_cursor_suppressions=normalise_quote_repeated_cursor_suppressions,
        normalise_record_map=normalise_record_map,
        normalise_state_epoch=normalise_state_epoch,
        normalise_state_int=normalise_state_int,
        normalise_string_list=normalise_string_list,
        normalise_string_map=normalise_string_map,
        normalise_tweet_cache=normalise_tweet_cache,
        prune_author_evaluation_quarantines=prune_author_evaluation_quarantines,
        prune_reply_evaluation_records=prune_reply_evaluation_records,
        require_compatible_state_reader=require_compatible_state_reader,
        validate_meme_schedule_version_for_candidate=validate_meme_schedule_version_for_candidate,
        validate_pending_mention_candidate_authority=validate_pending_mention_candidate_authority,
    )


def load_state() -> dict:
    """Load, validate, and recover runtime state from durable storage."""
    return _state_loading.load_state(
        STATE_BACKUP_COUNT=STATE_BACKUP_COUNT,
        STATE_FILE=STATE_FILE,
        STATE_MINIMUM_READER_VERSION=STATE_MINIMUM_READER_VERSION,
        STATE_PREVIOUS_READER_COMPATIBILITY_FENCES=STATE_PREVIOUS_READER_COMPATIBILITY_FENCES,
        STATE_READER_COMPATIBILITY_FENCE=STATE_READER_COMPATIBILITY_FENCE,
        UnsafeDurableStateNamespace=UnsafeDurableStateNamespace,
        default_state=default_state,
        log=log,
        log_event=log_event,
        log_json_debug=log_json_debug,
        normalise_state_candidate=normalise_state_candidate,
        read_stable_owned_json_bytes_no_follow=read_stable_owned_json_bytes_no_follow,
        require_compatible_state_reader=require_compatible_state_reader,
        save_state=save_state,
    )


def scheduler_epoch_from_state(state: dict, key: str, *, current: int | None = None) -> tuple[int, bool]:
    """Return the scheduler epoch from state."""
    return _runtime_state_helpers.scheduler_epoch_from_state(
        state,
        key,
        current=current,
        log=log,
    )


def _state_backups_owner() -> _state_persistence.StateBackups:
    """Bind current backup paths and file authorities without accessing storage."""
    return _state_persistence.StateBackups(
        path_type=Path,
        unsafe_namespace=UnsafeDurableStateNamespace,
        fsync_parent=fsync_parent_dir,
        os=os,
        read_stable_bytes=read_stable_owned_json_bytes_no_follow,
        tempfile=tempfile,
        backup_count=STATE_BACKUP_COUNT,
        state_file=STATE_FILE,
        log=log,
    )


def copy_state_backup(src: Path, dst: Path, *, durable: bool = False) -> None:
    """Copy one exact stable state generation without following links."""
    return _state_backups_owner().copy(src, dst, durable=durable)


def rotate_state_backups_before_commit(*, durable: bool = False) -> None:
    """Rotate state backups before commit."""
    return _state_backups_owner().rotate(durable=durable)


def write_latest_state_backup(*, durable: bool = False) -> None:
    """Write latest state backup."""
    return _state_backups_owner().write_latest(durable=durable)


class StateBackupWriteError(RuntimeError):
    """Raised after canonical state commits but its latest backup write fails."""
    pass


def state_generation_context():
    """Bind current reader limits, validation and lock ownership to publication."""
    from mrs_bot_state_generation import StateGenerationContext

    return StateGenerationContext(
        maximum_bytes=DURABLE_RUNTIME_JSON_MAX_BYTES,
        backup_count=STATE_BACKUP_COUNT,
        validate=normalise_state_candidate,
        read=read_stable_owned_json_bytes_no_follow,
        require_lock=require_instance_lock_for_remote_write,
    )


def save_state(state: dict, *, durable: bool = False) -> StateCommitProof:
    """Persist state atomically, logging only a value-free structural summary."""
    return _state_persistence.save_state(
        state,
        durable=durable,
        Path=Path,
        STATE_FILE=STATE_FILE,
        StateBackupWriteError=StateBackupWriteError,
        fsync_parent_dir=fsync_parent_dir,
        log=log,
        log_json_debug=log_json_debug,
        os=os,
        rotate_state_backups_before_commit=rotate_state_backups_before_commit,
        state_document_for_persistence=state_document_for_persistence,
        tempfile=tempfile,
        test_process_production_state_write_blocked=test_process_production_state_write_blocked,
        write_latest_state_backup=write_latest_state_backup,
        state_generation_context=state_generation_context,
    )


def _daily_reply_accounting_owner() -> _daily_reply_accounting.DailyReplyAccounting:
    """Bind current daily accounting boundaries without reading dates or state."""
    return _daily_reply_accounting.DailyReplyAccounting(
        log=log,
        reply_cap_date_str=reply_cap_date_str,
    )


def reset_daily_reply_count_if_needed(state: dict) -> None:
    """Reset daily reply count if needed."""
    return _daily_reply_accounting_owner().reset(state)


def reset_daily_quote_reply_count_if_needed(state: dict) -> None:
    """Reset daily quote reply count if needed."""
    return _daily_reply_accounting_owner().reset_quotes(state)


daily_author_reply_counts = _daily_reply_accounting.daily_author_reply_counts


def daily_author_reply_count(state: dict, author_id: str) -> int:
    """Return the daily author reply count."""
    return _daily_reply_accounting_owner().author_count(state, author_id)


def mark_daily_author_replied(state: dict, author_id: str) -> None:
    """Mark daily author replied."""
    return _daily_reply_accounting_owner().mark_author(state, author_id)


def _clarification_reply_owner() -> _reply_clarifications.ClarificationReplies:
    """Bind current clarification capabilities and policy without reading state."""
    return _reply_clarifications.ClarificationReplies(
        pipeline_enabled=conversational_reply_pipeline_enabled,
        parent_id=get_immediate_parent_id,
        get_tweet_by_id_cached=get_tweet_by_id_cached,
        api_error_is_permanent_target_failure=api_error_is_permanent_target_failure,
        is_our_auto_reply=is_our_auto_reply,
        api_error=ApiError,
        invalid_receipt=InvalidConfirmedReplyReceipt,
        window_seconds=CLARIFICATION_REPLY_WINDOW_SECONDS,
        log_event=log_event,
    )


CLARIFICATION_CUE_RE = _reply_clarifications.CLARIFICATION_CUE_RE
CLARIFICATION_TOKEN_RE = _reply_clarifications.CLARIFICATION_TOKEN_RE
CLARIFICATION_TOKEN_STOPWORDS = _reply_clarifications.CLARIFICATION_TOKEN_STOPWORDS


clarification_thread_id = _reply_clarifications.clarification_thread_id


def clarification_thread_is_terminal(state: dict, candidate: dict) -> bool:
    """Delegate clarification behavior to its owner with current dependencies."""
    return _clarification_reply_owner().thread_is_terminal(state, candidate)


def author_used_clarification_recently(state: dict, author_id: str, *, current: int) -> bool:
    """Delegate clarification behavior to its owner with current dependencies."""
    return _clarification_reply_owner().author_used_recently(state, author_id, current=current)


_clarification_tokens = _reply_clarifications._clarification_tokens


def clarification_reply_context(state: dict, candidate: dict, *, current: int) -> dict | None:
    """Delegate clarification behavior to its owner with current dependencies."""
    return _clarification_reply_owner().context(state, candidate, current=current)


# ---------------------------------------------------------------------
# Time / cooldown / error handling
# ---------------------------------------------------------------------

def now_epoch() -> int:
    """Return the now epoch."""
    if TEST_MODE and os.getenv("MRS_FAKE_NOW_EPOCH"):
        return int(os.getenv("MRS_FAKE_NOW_EPOCH", "0"))
    return int(datetime.now().timestamp())


def current_utc_datetime() -> datetime:
    """Return authoritative UTC time for reply payload and draft binding."""
    return datetime.fromtimestamp(now_epoch(), tz=timezone.utc)


def current_datetime() -> datetime:
    """Return the current datetime."""
    return datetime.fromtimestamp(now_epoch())


def parse_x_datetime_to_epoch(value: str | None) -> int | None:
    """Parse x datetime to epoch."""
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return int(parsed.timestamp())
    except Exception:
        log.warning("Could not parse X datetime: %r", value)
        return None


def parse_tweet_id(value: object, *, context: str) -> int | None:
    """Parse tweet ID."""
    value_str = str(value or "")
    if not re.fullmatch(r"\d{1,30}", value_str):
        log.warning("Skipping %s with invalid tweet id=%r", context, value)
        return None
    return int(value_str)


def valid_tweets_sorted_by_id(tweets: list[dict], *, context: str) -> list[dict]:
    """Return whether valid tweets sorted by ID."""
    valid: list[tuple[int, dict]] = []
    seen_ids: set[int] = set()
    for tweet in tweets:
        tweet_id = parse_tweet_id(tweet.get("id"), context=context)
        if tweet_id is None:
            continue
        if tweet_id in seen_ids:
            log.warning("Dropping duplicate %s tweet id=%s from paged API results", context, tweet_id)
            continue
        seen_ids.add(tweet_id)
        valid.append((tweet_id, tweet))
    return [tweet for _, tweet in sorted(valid, key=lambda item: item[0])]


def _api_cooldown_owner() -> _api_cooldowns.ApiCooldowns:
    """Bind current cooldown policy without reading clocks or caller state."""
    return _api_cooldowns.ApiCooldowns(
        datetime=datetime,
        log=log,
        now_epoch=now_epoch,
        error_window_seconds=ERROR_WINDOW_SECONDS,
        rate_limit_seconds=COOLDOWN_AFTER_429_SECONDS,
        repeated_error_seconds=COOLDOWN_AFTER_REPEATED_ERRORS_SECONDS,
        maximum_openai_errors=MAX_OPENAI_ERRORS_PER_WINDOW,
        maximum_x_errors=MAX_X_ERRORS_PER_WINDOW,
        reply_not_allowed=api_error_is_reply_not_allowed,
        save_state=save_state,
    )


def in_api_cooldown(state: dict, *, scope: str = "api") -> bool:
    """Return the in API cooldown."""
    return _api_cooldown_owner().active(state, scope=scope)


def clear_expired_api_cooldowns(state: dict) -> bool:
    """Clear expired API cooldowns."""
    return _api_cooldown_owner().clear_expired(state)


def sanitize_next_reply_lane_priority(state: dict) -> bool:
    """Sanitise next reply lane priority."""
    return _tick_coordination.sanitize_next_reply_lane_priority(
        state,
        log=log,
    )


def load_runtime_state() -> dict:
    """Load runtime state and apply daily maintenance safely."""
    return _runtime_state_helpers.load_runtime_state(
        clear_expired_api_cooldowns=clear_expired_api_cooldowns,
        load_state=load_state,
        sanitize_next_reply_lane_priority=sanitize_next_reply_lane_priority,
    )


def prune_error_epochs(epochs: list[int]) -> list[int]:
    """Prune error epochs."""
    return _api_cooldown_owner().prune_epochs(epochs)


def cooldown_until_for_rate_limit(current: int, reset_epoch: int | None) -> int:
    """Return the cooldown until for rate limit."""
    return _api_cooldown_owner().rate_limit_until(current, reset_epoch)


def record_api_error(state: dict, error: Exception, service: str, *, scope: str = "api") -> None:
    """Record API error."""
    return _api_cooldown_owner().record_error(state, error, service, scope=scope)


# ---------------------------------------------------------------------
# X API helpers
# ---------------------------------------------------------------------

def _x_request_routes_owner() -> _request_route_values.XRequestRoutes:
    """Bind configured routes without preparing or sending a request."""
    return _request_route_values.XRequestRoutes(
        primary_base=X_BASE, upload_base=X_UPLOAD_BASE,
        requests=requests, ambiguous_outcome=AmbiguousRemotePostOutcome,
    )


def x_request_base_url(method: str, path: str) -> str:
    """Select the configured origin for one literal X request.

    Only the exact authorised v2 media-upload write uses the optional upload
    origin.  Reads, tweet creation and every non-literal spelling stay on the
    primary X API origin.
    """
    return _x_request_routes_owner().base_url(method, path)


def normalised_prepared_x_request_path(method: str, path: str) -> str:
    """Return the conservative path which Requests will place on the wire.

    ``requests`` normalises dot segments and some percent-encoded characters
    while preparing a request.  Security decisions made against the caller's
    unprepared string can therefore misclassify a tweet-create target.  Decode
    repeatedly as a conservative allowance for an upstream HTTP router doing
    another decoding pass, normalise separators/dot segments, and collapse
    repeated slashes before comparing protected endpoints.
    """
    return _x_request_routes_owner().normalised_path(method, path)


def x_request_targets_tweet_create(method: str, path: str) -> bool:
    """Return whether one prepared X request targets the tweet-create route."""
    return _x_request_routes_owner().targets_tweet_create(method, path)


def x_request_targets_media_upload(method: str, path: str) -> bool:
    """Return whether one prepared X request targets the v2 media-create route."""
    return _x_request_routes_owner().targets_media_upload(method, path)


def prepared_x_create_route(method: str, path: str) -> str | None:
    """Classify the create route produced by Requests preparation."""
    return _x_request_routes_owner().prepared_route(method, path)


exact_x_create_route = _request_route_values.exact_x_create_route


def frozen_strict_json_object(value: object, *, label: str) -> dict:
    """Return an isolated strict-JSON copy suitable for request transport."""
    return _request_route_values.frozen_strict_json_object(
        value,
        label=label,
        AmbiguousRemotePostOutcome=AmbiguousRemotePostOutcome,
    )


if _X_REQUEST_PROVIDER_RELOAD_RECORD_STATE == "unconfigured":
    current_record_state, _current_record_fingerprint = (
        _configured_x_request_reload_record(sys.modules[__name__])
    )
    if current_record_state != "unconfigured":
        raise RuntimeError(
            "Configured X request authority changed during module import"
        )
    _install_configured_x_request_provider(
        lambda _url=f"{x_request_base_url('POST', '/2/tweets')}/2/tweets",
        _auth=AUTH,
        _timeout=request_timeout(): (_url, _auth, _timeout),
        owner_module=sys.modules[__name__],
        reload_fingerprint=_CURRENT_X_REQUEST_PROVIDER_RELOAD_FINGERPRINT,
    )
else:
    current_record_state, current_record_fingerprint = (
        _configured_x_request_reload_record(sys.modules[__name__])
    )
    if (
        current_record_state != "installed"
        or current_record_fingerprint
        != _CURRENT_X_REQUEST_PROVIDER_RELOAD_FINGERPRINT
    ):
        raise RuntimeError(
            "Configured X request authority changed during module reload"
        )

def print_rate_limit_headers(response: requests.Response) -> int | None:
    """Log rate limit headers."""
    return _observability.print_rate_limit_headers(
        response,
        datetime=datetime,
        log=log,
    )


X_CREATE_RESPONSE_ANOMALY_EVENT = _x_response_diagnostics.X_CREATE_RESPONSE_ANOMALY_EVENT
X_CREATE_RESPONSE_ANOMALY_SCHEMA_VERSION = _x_response_diagnostics.X_CREATE_RESPONSE_ANOMALY_SCHEMA_VERSION
X_CREATE_RESPONSE_ANOMALY_BODY_MAX_BYTES = _x_response_diagnostics.X_CREATE_RESPONSE_ANOMALY_BODY_MAX_BYTES
X_CREATE_RESPONSE_SAFE_CORRELATION_HEADERS = _x_response_diagnostics.X_CREATE_RESPONSE_SAFE_CORRELATION_HEADERS
_X_CREATE_RESPONSE_HEADER_MAX_CHARACTERS = _x_response_diagnostics._X_CREATE_RESPONSE_HEADER_MAX_CHARACTERS
_X_CREATE_RESPONSE_ID_VALUE_MAX_CHARACTERS = _x_response_diagnostics._X_CREATE_RESPONSE_ID_VALUE_MAX_CHARACTERS
_X_CREATE_RESPONSE_TOP_LEVEL_KEY_LIMIT = _x_response_diagnostics._X_CREATE_RESPONSE_TOP_LEVEL_KEY_LIMIT
_X_CREATE_RESPONSE_TOP_LEVEL_KEY_MAX_CHARACTERS = _x_response_diagnostics._X_CREATE_RESPONSE_TOP_LEVEL_KEY_MAX_CHARACTERS


def _x_create_diagnostics_owner() -> _x_response_diagnostics.XCreateDiagnostics:
    """Bind diagnostic capabilities without observing time or runtime state."""
    return _x_response_diagnostics.XCreateDiagnostics(
        valid_post_id=valid_post_id, log=log, now_epoch=now_epoch,
    )


def x_create_response_anomaly_reason(decoded: object) -> str | None:
    """Return why one decoded tweet-create response cannot confirm success."""

    return _x_create_diagnostics_owner().anomaly_reason(decoded)


_x_create_diagnostic_json_type = _x_response_diagnostics._x_create_diagnostic_json_type


_bounded_x_create_diagnostic_text = _x_response_diagnostics._bounded_x_create_diagnostic_text


_x_create_response_elapsed_ms = _x_response_diagnostics._x_create_response_elapsed_ms


def emit_x_create_response_anomaly(
    *,
    response: requests.Response,
    transport_authority: TransportAuthority,
    request_payload: dict,
    reason: str,
    raw_body: bytes,
    json_decode_succeeded: bool,
    decoded: object,
    json_error: json.JSONDecodeError | None = None,
) -> dict[str, object]:
    """Emit one canonical, bounded ERROR event for an anomalous tweet create.

    ``diagnostic_sha256`` hashes the canonical UTF-8 JSON object before that
    hash field is added.  The complete cached response body is included only
    when it is no larger than ``X_CREATE_RESPONSE_ANOMALY_BODY_MAX_BYTES``.
    """

    return _x_create_diagnostics_owner().emit_anomaly(
        response=response,
        transport_authority=transport_authority,
        request_payload=request_payload,
        reason=reason,
        raw_body=raw_body,
        json_decode_succeeded=json_decode_succeeded,
        decoded=decoded,
        json_error=json_error,
    )


def x_request(
    method: str,
    path: str,
    *,
    ambiguous_write: bool = False,
    _remote_write_authorization: TransportAuthority | MediaUploadAuthority | None = None,
    _remote_media_payload: ReceiptBoundMediaPayload | None = None,
    _remote_media_payload_metadata: dict[str, object] | None = None,
    **kwargs,
) -> dict:
    """Send an authenticated X API request without automatic retries."""
    return _x_request.x_request(
        method,
        path,
        ambiguous_write=ambiguous_write,
        _remote_write_authorization=_remote_write_authorization,
        _remote_media_payload=_remote_media_payload,
        _remote_media_payload_metadata=_remote_media_payload_metadata,
        kwargs=kwargs,
        AUTH=AUTH,
        AmbiguousRemotePostOutcome=AmbiguousRemotePostOutcome,
        ApiError=ApiError,
        DeterministicReplyCreateRejectionProof=DeterministicReplyCreateRejectionProof,
        MEDIA_UPLOAD_RECEIPT_FILE=MEDIA_UPLOAD_RECEIPT_FILE,
        MediaUploadAuthority=MediaUploadAuthority,
        MediaUploadReceiptError=MediaUploadReceiptError,
        ProvedRemotePostNonSuccess=ProvedRemotePostNonSuccess,
        ReceiptBoundMediaPayload=ReceiptBoundMediaPayload,
        TransportAuthority=TransportAuthority,
        TransportJournalError=TransportJournalError,
        ValidatedXErrorResponse=ValidatedXErrorResponse,
        XErrorResponseValidationError=XErrorResponseValidationError,
        _activate_coordinator_reply_create_rejection_proof=_activate_coordinator_reply_create_rejection_proof,
        _bind_transport_authority_to_configured_x_request=_bind_transport_authority_to_configured_x_request,
        block_if_unrelated_receipt_appeared_for_media_transport=block_if_unrelated_receipt_appeared_for_media_transport,
        block_if_unrelated_receipt_appeared_for_tweet_transport=block_if_unrelated_receipt_appeared_for_tweet_transport,
        canonical_transport_receipt_path_for_lane=canonical_transport_receipt_path_for_lane,
        consume_media_upload_authority=consume_media_upload_authority,
        emit_x_create_response_anomaly=emit_x_create_response_anomaly,
        frozen_strict_json_object=frozen_strict_json_object,
        invalidate_reply_create_rejection_proof=invalidate_reply_create_rejection_proof,
        log=log,
        log_json_debug=log_json_debug,
        parse_validated_x_error_response=parse_validated_x_error_response,
        perform_consumed_x_request=perform_consumed_x_request,
        prepared_x_create_route=prepared_x_create_route,
        print_rate_limit_headers=print_rate_limit_headers,
        report_bot_health_progress=report_bot_health_progress,
        request_timeout=request_timeout,
        requests=requests,
        require_remote_operation_unpaused=require_remote_operation_unpaused,
        x_create_response_anomaly_reason=x_create_response_anomaly_reason,
        x_request_base_url=x_request_base_url,
    )


def x_bearer_request(method: str, path: str, **kwargs) -> dict:
    """Send a bearer-authenticated X API request without automatic retries."""
    return _x_request.x_bearer_request(
        method,
        path,
        kwargs=kwargs,
        AmbiguousRemotePostOutcome=AmbiguousRemotePostOutcome,
        ApiError=ApiError,
        X_BASE=X_BASE,
        X_BEARER_TOKEN=X_BEARER_TOKEN,
        log=log,
        log_json_debug=log_json_debug,
        print_rate_limit_headers=print_rate_limit_headers,
        report_bot_health_progress=report_bot_health_progress,
        request_timeout=request_timeout,
        requests=requests,
    )


def x_quote_lookup_request(path: str, params: dict) -> dict:
    """
    Quote discovery uses recent search with Bearer auth. If X_BEARER_TOKEN
    is set, use it. Otherwise try the same OAuth1 path used by the rest of
    this bot.
    """
    if path == "/2/tweets/search/recent":
        query = str(params.get("query", ""))
        if "quotes_of_tweet_id:" in query:
            log.info("Quote recent-search request")
        elif "conversation_id:" in query:
            log.info("Hot-post recent-search request")
    if X_BEARER_TOKEN:
        return x_bearer_request("GET", path, params=params)

    log.warning("X_BEARER_TOKEN not set; trying quote lookup with OAuth1")
    return x_request("GET", path, params=params)


def api_error_is_invalid_pagination_cursor(error: BaseException) -> bool:
    """Recognise only X 400 responses which specifically reject a cursor."""
    return _x_pagination.api_error_is_invalid_pagination_cursor(
        error,
        ApiError=ApiError,
    )


def x_paginated_get(
    request_func,
    path: str,
    params: dict,
    *,
    max_pages: int,
    label: str,
    on_invalid_cursor=None,
    on_repeated_cursor=None,
    on_page=None,
    should_request_cursor=None,
    initial_requested_tokens: set[str] | None = None,
    retry_invalid_cursor_from_head: bool = True,
) -> dict:
    """
    Read bounded pages from an X API collection endpoint.

    A cursor-specific HTTP 400 gets one recovery from the original collection
    head. The caller clears its durable saved cursor before that retry. Other
    client errors remain fail-closed. Every validated page is passed to
    ``on_page`` before a repeated returned token terminates traversal. By
    default the repeated token is then rejected before it can be requested
    twice. A caller may instead supply ``on_repeated_cursor`` to retain the
    bounded partial result and stop normally. ``should_request_cursor`` may
    optionally stop before a continuation request as a bounded partial success.
    Missing data is valid only with an explicit zero result count. Incomplete
    or error-only pages raise before any page-completion callback.
    """
    return _x_pagination.x_paginated_get(
        request_func,
        path,
        params,
        max_pages=max_pages,
        label=label,
        on_invalid_cursor=on_invalid_cursor,
        on_repeated_cursor=on_repeated_cursor,
        on_page=on_page,
        should_request_cursor=should_request_cursor,
        initial_requested_tokens=initial_requested_tokens,
        retry_invalid_cursor_from_head=retry_invalid_cursor_from_head,
        ApiError=ApiError,
        PaginationCursorProtocolError=PaginationCursorProtocolError,
        api_error_is_invalid_pagination_cursor=api_error_is_invalid_pagination_cursor,
        log=log,
    )


# ---------------------------------------------------------------------
# Tweet cache / thread context
# ---------------------------------------------------------------------

normalise_tweet_text = _tweet_lookup_cache.normalise_tweet_text
tweet_text_is_complete = _tweet_lookup_cache.tweet_text_is_complete


def prune_tweet_cache(state: dict) -> None:
    """Delegate tweet lookup/cache work with current root dependencies."""
    return _tweet_lookup_cache_owner().prune(state)


def record_recent_own_post(state: dict, tweet_id: str) -> None:
    """Delegate tweet lookup/cache work with current root dependencies."""
    return _tweet_lookup_cache_owner().record_recent_own_post(state, tweet_id)


def seed_recent_own_post_ids_from_cache(state: dict) -> None:
    """Delegate tweet lookup/cache work with current root dependencies."""
    return _tweet_lookup_cache_owner().seed_recent_own_posts(state)


def cache_tweet(
    state: dict,
    *,
    tweet_id: str,
    text: str,
    author_id: str,
    conversation_id: str | None = None,
    referenced_tweets: list[dict] | None = None,
    created_at: str | None = None,
    image_summary: str | None = None,
    post_type: str | None = None,
) -> dict:
    """Delegate tweet lookup/cache work with current root dependencies."""
    return _tweet_lookup_cache_owner().store(
        state, tweet_id=tweet_id, text=text, author_id=author_id,
        conversation_id=conversation_id, referenced_tweets=referenced_tweets,
        created_at=created_at, image_summary=image_summary, post_type=post_type,
    )


_DEFAULT_REPLY_CONTEXT_POST_MAXIMUM_CHARS = MAX_VISIBLE_TEXT_CHARACTERS


def _reply_context_owner() -> _reply_context.ReplyContext:
    """Bind current context boundaries without fetching posts or retaining state."""
    return _reply_context.ReplyContext(
        api_error=ApiError,
        parse_tweet_id=parse_tweet_id,
        maximum_parent_depth=THREAD_CONTEXT_MAX_DEPTH,
        maximum_parent_network_fetches=THREAD_CONTEXT_MAX_NETWORK_FETCHES,
        is_permanent_target_failure=api_error_is_permanent_target_failure,
        get_tweet_by_id_cached=get_tweet_by_id_cached,
        log=log,
        log_json_debug=log_json_debug,
        prune_tweet_cache=prune_tweet_cache,
        user_id=MY_USER_ID,
        parse_x_datetime_to_epoch=parse_x_datetime_to_epoch,
        always_fetch_parent=ALWAYS_FETCH_PARENT_FOR_CONTEXT,
        context_validation_error=ContextValidationError,
        incoming_maximum_chars=REPLY_INCOMING_MAX_CHARS,
        maximum_visible_chars=MAX_VISIBLE_TEXT_CHARACTERS,
        skip_own_auto_replies=SKIP_REPLIES_TO_OWN_AUTO_REPLIES,
        bound_visible_conversation=bound_visible_conversation,
        current_utc_datetime=current_utc_datetime,
        reply_media_context_for_candidate=reply_media_context_for_candidate,
        default_post_maximum_chars=_DEFAULT_REPLY_CONTEXT_POST_MAXIMUM_CHARS,
    )


def get_immediate_parent_id(tweet: dict) -> str | None:
    """Read a parent identity through the context owner."""
    return _reply_context_owner().parent_id(tweet)


def _verified_tweet_lookup_row(
    tweet: object,
    *,
    requested_tweet_id: str,
) -> dict | None:
    """Delegate tweet lookup/cache work with current root dependencies."""
    return _tweet_lookup_cache_owner().verified_row(tweet, requested_tweet_id=requested_tweet_id)


def get_tweet_by_id(
    tweet_id: str,
    *,
    include_media: bool = False,
) -> dict | None:
    """Delegate tweet lookup/cache work with current root dependencies."""
    return _tweet_lookup_cache_owner().fetch(tweet_id, include_media=include_media)


def reply_target_is_available_immediately_before_send(target_id: str) -> bool:
    """Delegate tweet lookup/cache work with current root dependencies."""
    return _tweet_lookup_cache_owner().target_is_available(target_id)


def get_tweet_by_id_cached(
    tweet_id: str,
    state: dict,
    *,
    include_media: bool = False,
) -> dict | None:
    """Delegate tweet lookup/cache work with current root dependencies."""
    return _tweet_lookup_cache_owner().get_cached(tweet_id, state, include_media=include_media)


clean_text_for_reply_context = _reply_context.clean_text_for_reply_context


attach_media_to_tweets = _reply_native_media.attach_media_to_tweets


def _reply_media_owner() -> _reply_native_media.ReplyMedia:
    """Bind current media policy and capabilities without performing work."""
    return _reply_native_media.ReplyMedia(
        maximum_context_photos=MAX_REPLY_CONTEXT_PHOTOS,
        maximum_supplied_images=MAX_SUPPLIED_IMAGES,
        maximum_image_bytes=SINGLE_CALL_MAX_IMAGE_BYTES,
        image_mime_types=_REPLY_IMAGE_MIME_TYPES,
        log=log,
        media_unavailable=ReplyMediaUnavailable,
        media_transient_unavailable=ReplyMediaTransientUnavailable,
        test_mode=TEST_MODE,
        endpoint_is_loopback=endpoint_is_loopback,
        require_remote_operation_unpaused=require_remote_operation_unpaused,
        requests=requests,
        request_timeout=request_timeout,
        validate_supplied_images=validate_supplied_images,
    )


def candidate_native_photo_media(candidate: dict) -> tuple[list[dict], int]:
    """Select candidate photos through the media owner."""
    return _reply_media_owner().candidate_photos(candidate)


def reply_media_context_for_candidate(
    candidate: dict,
    *,
    lane: str,
    target_id: str,
    quoted_candidate: dict | None = None,
) -> dict:
    """Build media context through the media owner."""
    return _reply_media_owner().context(
        candidate,
        lane=lane,
        target_id=target_id,
        quoted_candidate=quoted_candidate,
    )


tweet_context_text = _reply_context.tweet_context_text


trim_context_text = _reply_context.trim_context_text


def build_parent_chain(mention: dict, state: dict) -> list[dict]:
    """Build a bounded parent path through the context owner."""
    return _reply_context_owner().parent_chain(mention, state)


def is_our_auto_reply(tweet: dict | None, state: dict) -> bool:
    """Identify this account's conversational replies through the context owner."""
    return _reply_context_owner().is_our_auto_reply(tweet, state)


def _reply_context_post(
    tweet: dict,
    *,
    principal_author_id: str,
    maximum_chars: int = MAX_VISIBLE_TEXT_CHARACTERS,
) -> dict[str, str]:
    """Prepare one visible post through the context owner."""
    return _reply_context_owner().post(
        tweet, principal_author_id=principal_author_id, maximum_chars=maximum_chars,
    )


def _log_single_call_context_summary(
    label: str, prepared: _reply_cycle_interfaces.PreparedReplyContext,
) -> None:
    """Log bounded context structure through its owner."""
    return _reply_context_owner().log_summary(label, prepared)


def _log_validated_single_call_reply(
    *,
    target_description: str,
    target_id: str,
    reply: object,
) -> None:
    """Log validated output metadata without retaining exact public prose."""
    return _observability._log_validated_single_call_reply(
        target_description=target_description,
        target_id=target_id,
        reply=reply,
        log=log,
    )


def _directly_quoted_tweet_for_reply_context(
    candidate: dict,
    state: dict,
    *,
    include_media: bool = True,
) -> dict | None:
    """Fetch a directly quoted post through the context owner."""
    return _reply_context_owner().directly_quoted_tweet(
        candidate, state, include_media=include_media,
    )


_direct_quote_id = _reply_context._direct_quote_id


def _quoted_post_for_reply_context(
    candidate: dict,
    state: dict,
    *,
    principal_author_id: str,
) -> dict[str, str] | None:
    """Prepare a directly quoted post through the context owner."""
    return _reply_context_owner().quoted_post(
        candidate, state, principal_author_id=principal_author_id,
    )


def _parent_path_is_contiguous(path: list[dict], target: dict) -> bool:
    """Check parent-path continuity through the context owner."""
    return _reply_context_owner().parent_path_is_contiguous(path, target)


def _parent_path_is_chronological(path: list[dict], target: dict) -> bool:
    """Check verified parent timestamps through the context owner."""
    return _reply_context_owner().parent_path_is_chronological(path, target)


def build_context_for_reply_ai(
    mention: dict,
    state: dict,
) -> _reply_cycle_interfaces.PreparedReplyContext | None:
    """Build canonical context and separate native media through their owner."""
    return _reply_context_owner().build(mention, state)
# ---------------------------------------------------------------------
# Mentions
# ---------------------------------------------------------------------

def reply_target_is_directly_eligible(tweet: dict) -> bool:
    """Delegate reply-lane policy with current root dependencies."""
    return _reply_lane_policy.reply_target_is_directly_eligible(
        tweet,
        MY_USERNAME=MY_USERNAME,
        MY_USER_ID=MY_USER_ID,
        re=re,
    )


def _mention_queue_owner() -> _mention_discovery.MentionQueue:
    """Bind current mention queue boundaries without reading state or files."""
    return _mention_discovery.MentionQueue(
        state_file=STATE_FILE,
        validate_authority=validate_pending_mention_candidate_authority,
        save=save_state,
        sort_candidates=valid_tweets_sorted_by_id,
        log=log,
    )


def pending_mention_candidates(state: dict) -> list[dict]:
    """Delegate to the mention queue with current root dependencies."""
    return _mention_queue_owner().pending(state)


remove_pending_mention_candidate = _mention_discovery.remove_pending_mention_candidate


def get_mentions(state: dict) -> list[dict]:
    """Delegate to the mention owner with current root dependencies."""
    return _mention_discovery.get_mentions(
        state,
        ApiError=ApiError,
        MAX_MENTIONS_PER_CHECK=MAX_MENTIONS_PER_CHECK,
        MENTIONS_MAX_PAGES_PER_CHECK=MENTIONS_MAX_PAGES_PER_CHECK,
        MENTION_BACKLOG_CONTINUATION_TOKEN_LIMIT=MENTION_BACKLOG_CONTINUATION_TOKEN_LIMIT,
        MY_USER_ID=MY_USER_ID,
        _MentionBacklogContinuationLimit=_MentionBacklogContinuationLimit,
        api_error_is_invalid_pagination_cursor=api_error_is_invalid_pagination_cursor,
        cache_tweet=cache_tweet,
        log=log,
        log_event=log_event,
        log_json_debug=log_json_debug,
        now_epoch=now_epoch,
        mention_queue=_mention_queue_owner(),
        prune_completed_mention_quarantine_evaluations=prune_completed_mention_quarantine_evaluations,
        save_state=save_state,
        valid_tweets_sorted_by_id=valid_tweets_sorted_by_id,
        x_paginated_get=x_paginated_get,
        x_request=x_request,
        api_error_is_permanent_target_failure=api_error_is_permanent_target_failure,
        get_tweet_by_id=get_tweet_by_id,
    )


def get_hot_post_reply_candidates(state: dict) -> list[dict]:
    """Delegate to the hot-post owner with current root dependencies."""
    return _hot_post_discovery.get_hot_post_reply_candidates(
        state,
        ApiError=ApiError,
        ENABLE_HOT_POST_REPLY_CHECKS=ENABLE_HOT_POST_REPLY_CHECKS,
        EXTRA_QUOTE_WATCH_FILE=EXTRA_QUOTE_WATCH_FILE,
        HOT_POST_REPLY_FULL_RESCAN_EVERY_CHECKS=HOT_POST_REPLY_FULL_RESCAN_EVERY_CHECKS,
        HOT_POST_REPLY_SEARCH_API_MAX_RESULTS=HOT_POST_REPLY_SEARCH_API_MAX_RESULTS,
        HOT_POST_REPLY_SEARCH_MAX_PAGES_PER_CHECK=HOT_POST_REPLY_SEARCH_MAX_PAGES_PER_CHECK,
        HOT_POST_REPLY_USE_SINCE_ID=HOT_POST_REPLY_USE_SINCE_ID,
        MAX_HOT_POST_REPLIES_PER_CHECK=MAX_HOT_POST_REPLIES_PER_CHECK,
        MY_USER_ID=MY_USER_ID,
        cache_tweet=cache_tweet,
        retire_ineligible_draft=_reply_draft_owner().retire_ineligible,
        in_api_cooldown=in_api_cooldown,
        lane_paused=lane_paused,
        load_extra_quote_watch_post_ids=load_extra_quote_watch_post_ids,
        log=log,
        log_event=log_event,
        log_json_debug=log_json_debug,
        mark_hot_post_reply_skipped=mark_hot_post_reply_skipped,
        record_terminal_reply_evaluation=record_terminal_reply_evaluation,
        reply_target_is_directly_eligible=reply_target_is_directly_eligible,
        save_state=save_state,
        valid_tweets_sorted_by_id=valid_tweets_sorted_by_id,
        x_paginated_get=x_paginated_get,
        x_quote_lookup_request=x_quote_lookup_request,
    )

def mark_hot_post_reply_skipped(
    state: dict,
    reply_id: str,
    *,
    reason: str = "unspecified",
    original_post_id: str | None = None,
    retryable: bool | None = None,
) -> None:
    """Delegate to the hot-post owner with current root dependencies."""
    return _hot_post_discovery.mark_hot_post_reply_skipped(
        state,
        reply_id,
        reason=reason,
        original_post_id=original_post_id,
        retryable=retryable,
        log_event=log_event,
        now_epoch=now_epoch,
    )


def maybe_mark_hot_post_reply_skipped(state: dict, candidate: dict, reason: str = "unspecified") -> None:
    """Delegate to the hot-post owner with current root dependencies."""
    return _hot_post_discovery.maybe_mark_hot_post_reply_skipped(
        state,
        candidate,
        reason,
        mark_hot_post_reply_skipped=mark_hot_post_reply_skipped,
    )


def dedupe_reply_candidates(mentions: list[dict], hot_post_replies: list[dict]) -> list[dict]:
    """Delegate to the hot-post owner with current root dependencies."""
    return _hot_post_discovery.dedupe_reply_candidates(
        mentions,
        hot_post_replies,
        log=log,
    )


# ---------------------------------------------------------------------
# Media / posting
# ---------------------------------------------------------------------

def validate_media_upload_payload_metadata(
    value: object,
    *,
    form: dict[str, object],
) -> dict[str, object]:
    """Validate local receipt metadata against one exact remote media form."""
    return _post_creation.validate_media_upload_payload_metadata(
        value,
        form=form,
    )


def media_upload_payload_metadata(
    form: dict[str, object],
) -> dict[str, object]:
    """Bind the durable media receipt to its exact remote form."""
    return _post_creation.media_upload_payload_metadata(
        form,
    )


def upload_media_v2(
    *,
    authority: MediaUploadAuthority,
    payload: ReceiptBoundMediaPayload,
    payload_metadata: dict[str, object] | None = None,
) -> str:
    """Upload once through v2 and return its confirmed media identity."""
    return _post_creation.upload_media_v2(
        authority=authority,
        payload=payload,
        payload_metadata=payload_metadata,
        AmbiguousRemotePostOutcome=AmbiguousRemotePostOutcome,
        log=log,
        x_request=x_request,
    )


def upload_media_v1_1(image_path: str) -> str:
    """Refuse the retired legacy endpoint before any transport."""

    del image_path
    raise AmbiguousRemotePostOutcome(
        "Legacy v1.1 media upload is disabled because it has no durable "
        "transaction authority",
        service="x",
        request_method="POST",
        request_path="/1.1/media/upload.json",
    )


def upload_media(
    image_path: str,
    *,
    lane: str,
) -> str:
    """Upload once under a restart-visible, image-bound sending receipt."""
    return _post_creation.upload_media(
        image_path,
        lane=lane,
        AmbiguousRemotePostOutcome=AmbiguousRemotePostOutcome,
        MEDIA_UPLOAD_RECEIPT_FILE=MEDIA_UPLOAD_RECEIPT_FILE,
        MediaUploadPreflightError=MediaUploadPreflightError,
        MediaUploadReceiptError=MediaUploadReceiptError,
        RemoteOperationsPaused=RemoteOperationsPaused,
        abort_untransmitted_media_upload=abort_untransmitted_media_upload,
        begin_confirmed_post_sigint_deferral=begin_confirmed_post_sigint_deferral,
        begin_media_upload=begin_media_upload,
        bind_media_upload_payload=bind_media_upload_payload,
        block_if_ambiguous_remote_post=block_if_ambiguous_remote_post,
        confirm_media_upload=confirm_media_upload,
        end_confirmed_post_sigint_deferral=end_confirmed_post_sigint_deferral,
        log=log,
        mimetypes=mimetypes,
        record_ambiguous_remote_post=record_ambiguous_remote_post,
        require_remote_operation_unpaused=require_remote_operation_unpaused,
        transaction_mutation_authority=transaction_mutation_authority,
        upload_media_v2=upload_media_v2,
    )


def unresolved_conversational_reply_receipt_is_blocking() -> bool:
    """Return whether a reply receipt forbids another remote write."""
    return _remote_write_barriers.unresolved_conversational_reply_receipt_is_blocking(
        load_confirmed_reply_receipt=load_confirmed_reply_receipt,
    )


def unresolved_main_post_attempt_is_blocking() -> bool:
    """Return whether a main-post attempt forbids another remote write."""
    return _remote_write_barriers.unresolved_main_post_attempt_is_blocking(
        load_meme_post_receipt=load_meme_post_receipt,
        load_regular_post_receipt=load_regular_post_receipt,
    )


def remote_write_safety_incident_is_latched() -> bool:
    """Return whether process memory requires every remote operation to stop."""
    return _remote_write_barriers.remote_write_safety_incident_is_latched(
        _AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN=_AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN,
        _AMBIGUOUS_REMOTE_POST_SEEN=_AMBIGUOUS_REMOTE_POST_SEEN,
    )


def remote_write_safety_protocol_is_active() -> bool:
    """Return whether the exact restart-persistent protocol is activated.

    Absence, malformed bytes, unsafe metadata and inspection failure all mean
    that no remote-write lane may open.  This deliberately durable negative
    condition survives arbitrary process loss without relying on Python
    globals or an ambiguity marker which another process could remove.
    """
    return _remote_write_barriers.remote_write_safety_protocol_is_active(
        ProtocolActivationError=ProtocolActivationError,
        REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE=REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE,
        inspect_protocol_activation=inspect_protocol_activation,
        remote_receipt_retirement_is_blocking=remote_receipt_retirement_is_blocking,
    )


def historical_context_receipt_path_present_or_unsafe() -> bool:
    """Treat any historical-context receipt namespace entry as blocking."""
    return _remote_write_barriers.historical_context_receipt_path_present_or_unsafe(
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE=HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        log=log,
        os=os,
    )


def historical_context_outbox_remote_attempt_is_blocking(
    *,
    prepared_receipt: dict | None = None,
    prepared_transport_authority: TransportAuthority | None = None,
    allow_local_reconciliation_parent_id: str | None = None,
) -> bool:
    """Treat every possibly transmitted outbox attempt as a global barrier.

    The outbox can outlive a lost source receipt and transport journal.  An
    explicit ``False`` phase proves only local pre-transport work; ``True`` or
    an absent legacy phase means the remote create may have started and must
    block every unrelated remote-write lane until reconciliation.
    """
    return _remote_write_barriers.historical_context_outbox_remote_attempt_is_blocking(
        prepared_receipt=prepared_receipt,
        prepared_transport_authority=prepared_transport_authority,
        allow_local_reconciliation_parent_id=allow_local_reconciliation_parent_id,
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE=HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        Path=Path,
        hashlib=hashlib,
        historical_context_outbox_store=historical_context_outbox_store,
        historical_context_reply_store=historical_context_reply_store,
        inspect_transport_state=inspect_transport_state,
        journal_path_for_receipt=journal_path_for_receipt,
        log=log,
    )


def historical_context_outbox_remote_attempt_parent_for_local_reconciliation(
) -> str | None:
    """Return the sole risky outbox parent eligible for local-only recovery."""
    return _remote_write_barriers.historical_context_outbox_remote_attempt_parent_for_local_reconciliation(
        historical_context_outbox_store=historical_context_outbox_store,
    )


def historical_context_receipt_parent_for_local_reconciliation() -> str | None:
    """Return the parent bound by a stable no-follow transaction receipt."""
    return _remote_write_barriers.historical_context_receipt_parent_for_local_reconciliation(
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE=HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
    )


def exact_historical_context_sending_receipt_matches(
    receipt: dict | None,
) -> bool:
    """Match the owning sending receipt by schema, identity and exact bytes."""
    return _remote_write_barriers.exact_historical_context_sending_receipt_matches(
        receipt,
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE=HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        json=json,
        os=os,
        stat=stat,
    )


def block_if_remote_write_safety_incident_latched() -> None:
    """Fail before remote work when a marker or either process latch exists."""
    return _remote_write_barriers.block_if_remote_write_safety_incident_latched(
        AMBIGUOUS_POST_OUTCOME_FILE=AMBIGUOUS_POST_OUTCOME_FILE,
        AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE=AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE,
        AmbiguousRemotePostOutcome=AmbiguousRemotePostOutcome,
        REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE=REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE,
        remote_write_safety_incident_is_latched=remote_write_safety_incident_is_latched,
        remote_write_safety_marker_path_present_or_unsafe=remote_write_safety_marker_path_present_or_unsafe,
        remote_write_safety_protocol_is_active=remote_write_safety_protocol_is_active,
    )


def remote_write_transport_journal_paths() -> tuple[Path, ...]:
    """Return every distinct transaction-journal path used by active lanes."""
    return _transport_source_preparation.remote_write_transport_journal_paths(
        CONFIRMED_REPLY_RECEIPT_FILE=CONFIRMED_REPLY_RECEIPT_FILE,
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE=HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        MEME_POST_RECEIPT_FILE=MEME_POST_RECEIPT_FILE,
        REGULAR_POST_RECEIPT_FILE=REGULAR_POST_RECEIPT_FILE,
        journal_path_for_receipt=journal_path_for_receipt,
    )


def remote_source_receipt_paths() -> tuple[Path, ...]:
    """Return the four current public-create source receipt paths."""
    return _transport_source_preparation.remote_source_receipt_paths(
        CONFIRMED_REPLY_RECEIPT_FILE=CONFIRMED_REPLY_RECEIPT_FILE,
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE=HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        MEME_POST_RECEIPT_FILE=MEME_POST_RECEIPT_FILE,
        REGULAR_POST_RECEIPT_FILE=REGULAR_POST_RECEIPT_FILE,
    )


def canonical_transport_receipt_path_for_lane(lane: str) -> Path | None:
    """Return the only receipt pathname allowed to authorise one public lane."""
    return _transport_source_preparation.canonical_transport_receipt_path_for_lane(
        lane,
        CONFIRMED_REPLY_RECEIPT_FILE=CONFIRMED_REPLY_RECEIPT_FILE,
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE=HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        MEME_POST_RECEIPT_FILE=MEME_POST_RECEIPT_FILE,
        REGULAR_POST_RECEIPT_FILE=REGULAR_POST_RECEIPT_FILE,
    )


TRANSPORT_SOURCE_VALIDATOR_ID = LANE_SOURCE_VALIDATOR_ID


def transport_source_semantic_validator(
    lane: str,
    receipt: dict,
    payload: dict,
) -> bool:
    """Prove that one lane-owned source receipt authorises one tweet body."""
    return _transport_source_preparation.transport_source_semantic_validator(
        lane,
        receipt,
        payload,
        main_post_attempt_binds_payload=main_post_attempt_binds_payload,
        sending_reply_receipt_is_semantically_valid=sending_reply_receipt_is_semantically_valid,
    )


def _legacy_conversational_transport_source_semantic_validator(
    lane: str,
    receipt: dict,
    payload: dict,
) -> bool:
    """Validate a frozen reply source solely for confirmed-journal recovery."""
    return _transport_source_preparation._legacy_conversational_transport_source_semantic_validator(
        lane,
        receipt,
        payload,
        _legacy_sending_reply_receipt_is_semantically_valid=_legacy_sending_reply_receipt_is_semantically_valid,
    )


def bind_lane_transport_source(
    *,
    receipt_path: Path,
    receipt: dict,
    lane: str,
    payload: dict,
) -> SourceReceiptBinding:
    """Create the only accepted semantic source binding for a public tweet."""
    return _transport_source_preparation.bind_lane_transport_source(
        receipt_path=receipt_path,
        receipt=receipt,
        lane=lane,
        payload=payload,
        TRANSPORT_SOURCE_VALIDATOR_ID=TRANSPORT_SOURCE_VALIDATOR_ID,
        bind_transport_source=bind_transport_source,
        transport_source_semantic_validator=transport_source_semantic_validator,
    )


def block_if_unrelated_receipt_appeared_for_tweet_transport(
    expected_receipt_path: Path,
) -> None:
    """Reject a lane which appeared after the transaction's initial preflight."""
    return _transport_source_preparation.block_if_unrelated_receipt_appeared_for_tweet_transport(
        expected_receipt_path,
        CONFIRMED_REPLY_RECEIPT_FILE=CONFIRMED_REPLY_RECEIPT_FILE,
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE=HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        MEDIA_UPLOAD_RECEIPT_FILE=MEDIA_UPLOAD_RECEIPT_FILE,
        MEME_POST_RECEIPT_FILE=MEME_POST_RECEIPT_FILE,
        REGULAR_POST_RECEIPT_FILE=REGULAR_POST_RECEIPT_FILE,
        TransportJournalError=TransportJournalError,
        media_upload_receipt_is_blocking=media_upload_receipt_is_blocking,
        receipt_namespace_entry_exists=receipt_namespace_entry_exists,
        remote_receipt_retirement_is_blocking=remote_receipt_retirement_is_blocking,
    )


def block_if_unrelated_receipt_appeared_for_media_transport() -> None:
    """Reject media transport if any other transaction owns remote writes."""
    return _transport_source_preparation.block_if_unrelated_receipt_appeared_for_media_transport(
        CONFIRMED_REPLY_RECEIPT_FILE=CONFIRMED_REPLY_RECEIPT_FILE,
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE=HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        MEME_POST_RECEIPT_FILE=MEME_POST_RECEIPT_FILE,
        MediaUploadReceiptError=MediaUploadReceiptError,
        REGULAR_POST_RECEIPT_FILE=REGULAR_POST_RECEIPT_FILE,
        receipt_namespace_entry_exists=receipt_namespace_entry_exists,
        remote_receipt_retirement_is_blocking=remote_receipt_retirement_is_blocking,
        remote_write_transport_journal_is_blocking=remote_write_transport_journal_is_blocking,
    )


def remote_write_transport_journal_is_blocking() -> bool:
    """Return whether any valid, invalid, or torn transport journal exists."""
    return _remote_write_barriers.remote_write_transport_journal_is_blocking(
        remote_write_transport_journal_paths=remote_write_transport_journal_paths,
        transport_journal_is_blocking=transport_journal_is_blocking,
    )


def remote_receipt_retirement_is_blocking() -> bool:
    """Return whether any source-receipt retirement is incomplete or unsafe."""
    return _remote_write_barriers.remote_receipt_retirement_is_blocking(
        remote_source_receipt_paths=remote_source_receipt_paths,
        retirement_auxiliary_barrier_exists=retirement_auxiliary_barrier_exists,
        retirement_ledger_is_blocking=retirement_ledger_is_blocking,
    )


confirmed_context_outbox_matches_receipt = _receipt_retirement.confirmed_context_outbox_matches_receipt


def require_historical_context_retirement_outbox_authority() -> None:
    """Bind an interrupted context retirement to its durable outbox outcome.

    A confirmed reply or a proved remote non-success is written to history and
    outbox before exact source retirement starts.  No retirement namespace may
    be resumed until its marker hash matches exactly one such durable outcome.
    """
    return _receipt_retirement.require_historical_context_retirement_outbox_authority(
        ExactReceiptRetirementError=ExactReceiptRetirementError,
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE=HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        historical_context_outbox_store=historical_context_outbox_store,
        historical_context_reply_store=historical_context_reply_store,
        inspect_exact_receipt_retirement=inspect_exact_receipt_retirement,
        inspect_interrupted_receipt_retirement=inspect_interrupted_receipt_retirement,
        journal_path_for_receipt=journal_path_for_receipt,
        now_epoch=now_epoch,
        re=re,
        retirement_auxiliary_barrier_exists=retirement_auxiliary_barrier_exists,
        transport_journal_is_blocking=transport_journal_is_blocking,
    )


def resume_interrupted_source_receipt_retirement_if_present() -> bool:
    """Finish one journal-free source retirement under the process lock.

    A matching transport journal must retain the source receipt until its own
    retirement has completed.  Multiple lane auxiliaries are never selected
    automatically, and every namespace inspection includes broken symlinks.
    """
    return _receipt_retirement.resume_interrupted_source_receipt_retirement_if_present(
        CONFIRMED_REPLY_RECEIPT_FILE=CONFIRMED_REPLY_RECEIPT_FILE,
        ExactReceiptRetirementError=ExactReceiptRetirementError,
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE=HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        MEME_POST_RECEIPT_FILE=MEME_POST_RECEIPT_FILE,
        REGULAR_POST_RECEIPT_FILE=REGULAR_POST_RECEIPT_FILE,
        historical_context_reply_store=historical_context_reply_store,
        inspect_transport_state=inspect_transport_state,
        journal_path_for_receipt=journal_path_for_receipt,
        load_confirmed_reply_receipt=load_confirmed_reply_receipt,
        load_meme_post_receipt=load_meme_post_receipt,
        load_regular_post_receipt=load_regular_post_receipt,
        inspect_interrupted_receipt_retirement=inspect_interrupted_receipt_retirement,
        log=log,
        remote_write_transport_journal_paths=remote_write_transport_journal_paths,
        require_historical_context_retirement_outbox_authority=require_historical_context_retirement_outbox_authority,
        resume_interrupted_receipt_retirement=resume_interrupted_receipt_retirement,
        retire_lane_transport_journal_if_present=retire_lane_transport_journal_if_present,
        retirement_auxiliary_barrier_exists=retirement_auxiliary_barrier_exists,
        transaction_mutation_authority=transaction_mutation_authority,
        recover_state_receipt_commit_proof=recover_state_receipt_commit_proof,
        state_commit_mutation_authority=state_commit_mutation_authority,
        transport_journal_is_blocking=transport_journal_is_blocking,
    )


def resume_source_receipt_retirement_for_control_snapshot(
    *,
    maintenance_paused: bool,
) -> bool:
    """Apply one already-read pause snapshot to local retirement mutation."""

    if maintenance_paused:
        return False
    try:
        return resume_interrupted_source_receipt_retirement_if_present()
    except BaseException:
        # The last namespace unlink can succeed before its directory fsync
        # raises.  At that point no auxiliary pathname may remain for the
        # ordinary barrier scan.  Preserve a process-local fail-closed latch so
        # this daemon tick cannot continue into any scheduler or provider lane.
        global _AMBIGUOUS_REMOTE_POST_SEEN
        _AMBIGUOUS_REMOTE_POST_SEEN = True
        raise


def latch_source_receipt_retirement_uncertainty() -> None:
    """Keep this process fail closed after any uncertain receipt retirement."""

    global _AMBIGUOUS_REMOTE_POST_SEEN
    _AMBIGUOUS_REMOTE_POST_SEEN = True


def recover_state_receipt_commit_proof(receipt_path: Path):
    """Rebind an interrupted receipt marker to its persisted confirmed-state effect."""
    from mrs_bot_state_generation import protect_history_files

    inspection = inspect_interrupted_receipt_retirement(receipt_path)
    if not inspection.valid:
        raise RuntimeError("interrupted receipt retirement has no stable marker")
    state = load_state()
    entry = state.get("_confirmed_receipt_commits", {}).get(inspection.expected_sha256)
    if not isinstance(entry, dict):
        raise RuntimeError("interrupted retirement is not bound to a committed state generation")
    files = []
    if receipt_path == REGULAR_POST_RECEIPT_FILE:
        for path, key in ((LINES_USED_FILE, "quote_hash"), (IMAGES_USED_FILE, "image_basename")):
            present, data = read_stable_owned_json_bytes_no_follow(path)
            if not present or data is None:
                raise RuntimeError("interrupted regular receipt has no durable used history")
            try:
                history = coerce_used_set(json.loads(data), path=path)
            except (TypeError, ValueError) as exc:
                raise RuntimeError("interrupted regular receipt has unreadable used history") from exc
            if entry.get(key) not in history:
                raise RuntimeError("interrupted regular receipt has no matching durable used history")
            files.append((path, data))
    proof = save_state(state, durable=True)
    return protect_history_files(proof, tuple(files)) if files else proof


def state_commit_mutation_authority(commit_proof, operation: str):
    """Bind exact state-generation proof to every low-level retirement mutation."""
    if commit_proof is None:
        return transaction_mutation_authority(operation)

    def verify(current_operation: str) -> None:
        """Reprove both instance ownership and the committed generation."""
        from mrs_bot_state_generation import require_commit_proof
        require_instance_lock_for_remote_write(current_operation)
        require_commit_proof(commit_proof)

    return issue_transaction_mutation_authority(verify, operation=operation)


def retire_current_source_receipt(
    receipt_path: Path,
    expected_receipt_bytes: bytes,
    *,
    commit_proof=None,
    disposition: str | None = None,
) -> None:
    """Resume a prepared removal, or start retirement when no journal existed."""
    return _receipt_retirement.retire_current_source_receipt(
        receipt_path,
        expected_receipt_bytes,
        disposition=disposition,
        latch_source_receipt_retirement_uncertainty=latch_source_receipt_retirement_uncertainty,
        retire_or_resume_exact_receipt=retire_or_resume_exact_receipt,
        transaction_mutation_authority=functools.partial(state_commit_mutation_authority, commit_proof),
    )


def block_if_remote_receipt_retirement_exists() -> None:
    """Fail closed while a receipt-removal transaction remains unfinished."""
    return _remote_write_barriers.block_if_remote_receipt_retirement_exists(
        AmbiguousRemotePostOutcome=AmbiguousRemotePostOutcome,
        remote_receipt_retirement_is_blocking=remote_receipt_retirement_is_blocking,
    )


def block_if_remote_write_transport_journal_exists() -> None:
    """Fail closed before unrelated remote work while a journal is unresolved."""
    return _remote_write_barriers.block_if_remote_write_transport_journal_exists(
        AmbiguousRemotePostOutcome=AmbiguousRemotePostOutcome,
        remote_write_transport_journal_is_blocking=remote_write_transport_journal_is_blocking,
    )


def confirmed_main_receipt_is_sole_local_recovery_barrier() -> bool:
    """Return whether one main receipt may finish under its confirmed journal.

    This is intentionally narrower than ignoring the global journal barrier.
    It exists so the regular and meme entry points can perform local recovery
    when called directly, just as the daemon's pre-barrier reconciler does.
    """
    return _remote_write_barriers.confirmed_main_receipt_is_sole_local_recovery_barrier(
        CONFIRMED_REPLY_RECEIPT_FILE=CONFIRMED_REPLY_RECEIPT_FILE,
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE=HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        MEME_POST_RECEIPT_FILE=MEME_POST_RECEIPT_FILE,
        REGULAR_POST_RECEIPT_FILE=REGULAR_POST_RECEIPT_FILE,
        inspect_transport_state=inspect_transport_state,
        journal_path_for_receipt=journal_path_for_receipt,
        load_meme_post_receipt=load_meme_post_receipt,
        load_regular_post_receipt=load_regular_post_receipt,
        log=log,
        receipt_namespace_entry_exists=receipt_namespace_entry_exists,
        remote_write_transport_journal_paths=remote_write_transport_journal_paths,
        transport_journal_is_blocking=transport_journal_is_blocking,
        verify_lane_transport_source_lineage_if_present=verify_lane_transport_source_lineage_if_present,
    )


def remote_media_upload_receipt_is_blocking() -> bool:
    """Return whether a valid, invalid, or torn media transaction exists."""
    return _remote_write_barriers.remote_media_upload_receipt_is_blocking(
        MEDIA_UPLOAD_RECEIPT_FILE=MEDIA_UPLOAD_RECEIPT_FILE,
        media_upload_receipt_is_blocking=media_upload_receipt_is_blocking,
    )


def block_if_remote_media_upload_receipt_exists() -> None:
    """Fail closed before unrelated work while a media upload is unresolved."""
    return _remote_write_barriers.block_if_remote_media_upload_receipt_exists(
        AmbiguousRemotePostOutcome=AmbiguousRemotePostOutcome,
        remote_media_upload_receipt_is_blocking=remote_media_upload_receipt_is_blocking,
    )


def resume_interrupted_confirmed_media_retirement_if_present() -> bool:
    """Finish one exact media-fence retirement before the global barrier.

    The only automatically selected state is the documented crash boundary in
    which the confirmed media receipt is absent, its immutable media fence
    survives, and exactly one canonical main-lane tweet journal/fence pair is
    still ``prepared``.  That tweet pair and its source receipt remain intact
    and continue to block every remote-write lane after this local repair; no
    retransmission or automatic transaction abort is authorised here.
    """
    return _receipt_retirement.resume_interrupted_confirmed_media_retirement_if_present(
        MEDIA_UPLOAD_RECEIPT_FILE=MEDIA_UPLOAD_RECEIPT_FILE,
        MEME_POST_RECEIPT_FILE=MEME_POST_RECEIPT_FILE,
        MediaUploadReceiptError=MediaUploadReceiptError,
        Path=Path,
        REGULAR_POST_RECEIPT_FILE=REGULAR_POST_RECEIPT_FILE,
        fence_path_for_journal=fence_path_for_journal,
        inspect_transport_state=inspect_transport_state,
        journal_path_for_receipt=journal_path_for_receipt,
        log=log,
        media_fence_path_for_receipt=media_fence_path_for_receipt,
        os=os,
        require_instance_lock_for_remote_write=require_instance_lock_for_remote_write,
        resume_interrupted_confirmed_media_retirement=resume_interrupted_confirmed_media_retirement,
        transaction_mutation_authority=transaction_mutation_authority,
    )


def expected_lane_transport_source_receipt_bytes(
    *,
    receipt: dict,
    lane: str,
    current_receipt_bytes: bytes,
) -> bytes:
    """Reconstruct the exact pre-transport receipt for one public lane."""
    return _receipt_retirement.expected_lane_transport_source_receipt_bytes(
        receipt=receipt,
        lane=lane,
        current_receipt_bytes=current_receipt_bytes,
        TransportJournalError=TransportJournalError,
        confirmed_pending_schedule_receipt_is_semantically_valid=confirmed_pending_schedule_receipt_is_semantically_valid,
        conversational_sending_receipt_from_confirmed=conversational_sending_receipt_from_confirmed,
        current_main_post_attempt_is_semantically_valid=current_main_post_attempt_is_semantically_valid,
        hashlib=hashlib,
        re=re,
        sending_reply_receipt_is_semantically_valid=sending_reply_receipt_is_semantically_valid,
    )


def verify_lane_transport_source_lineage_if_present(
    *,
    receipt_path: Path,
    receipt: dict,
    lane: str,
    post_id: str,
    current_receipt_bytes: bytes | None = None,
) -> bool:
    """Validate a current journal before any derived receipt changes state."""
    return _receipt_retirement.verify_lane_transport_source_lineage_if_present(
        receipt_path=receipt_path,
        receipt=receipt,
        lane=lane,
        post_id=post_id,
        current_receipt_bytes=current_receipt_bytes,
        TRANSPORT_SOURCE_VALIDATOR_ID=TRANSPORT_SOURCE_VALIDATOR_ID,
        TransportJournalError=TransportJournalError,
        expected_lane_transport_source_receipt_bytes=expected_lane_transport_source_receipt_bytes,
        journal_path_for_receipt=journal_path_for_receipt,
        receipt_int=receipt_int,
        transport_journal_is_blocking=transport_journal_is_blocking,
        verify_confirmed_transport_source_lineage=verify_confirmed_transport_source_lineage,
    )


def retire_lane_transport_journal_if_present(
    *,
    receipt_path: Path,
    receipt: dict,
    lane: str,
    post_id: str,
    current_receipt_bytes: bytes | None = None,
    commit_proof=None,
) -> bool:
    """Retire a confirmed journal while an exact source-removal guard overlaps."""
    if lane in {"quote_image", "daily_meme", "conversational_reply"}:
        from mrs_bot_state_generation import require_commit_proof
        require_commit_proof(commit_proof)
        commit_proof.require_receipt(receipt)
    return _receipt_retirement.retire_lane_transport_journal_if_present(
        receipt_path=receipt_path,
        receipt=receipt,
        lane=lane,
        post_id=post_id,
        current_receipt_bytes=current_receipt_bytes,
        expected_lane_transport_source_receipt_bytes=expected_lane_transport_source_receipt_bytes,
        journal_path_for_receipt=journal_path_for_receipt,
        prepare_exact_receipt_retirement=prepare_exact_receipt_retirement,
        retire_confirmed_transport_transaction=retire_confirmed_transport_transaction,
        transaction_mutation_authority=functools.partial(state_commit_mutation_authority, commit_proof),
        transport_journal_is_blocking=transport_journal_is_blocking,
    )


def block_if_ambiguous_remote_post(
    *,
    prepared_conversational_reply_receipt: dict | None = None,
    prepared_historical_context_reply_receipt: dict | None = None,
    prepared_main_post_attempt: dict | None = None,
    allow_confirmed_pending_schedule_reconciliation: bool = False,
    allow_historical_context_receipt_reconciliation: bool = False,
    allow_historical_context_outbox_reconciliation_parent_id: str | None = None,
    prepared_transport_authority: TransportAuthority | None = None,
) -> None:
    """Refuse posting while a remote-write safety incident is unresolved."""
    return _remote_write_barriers.block_if_ambiguous_remote_post(
        prepared_conversational_reply_receipt=prepared_conversational_reply_receipt,
        prepared_historical_context_reply_receipt=prepared_historical_context_reply_receipt,
        prepared_main_post_attempt=prepared_main_post_attempt,
        allow_confirmed_pending_schedule_reconciliation=allow_confirmed_pending_schedule_reconciliation,
        allow_historical_context_receipt_reconciliation=allow_historical_context_receipt_reconciliation,
        allow_historical_context_outbox_reconciliation_parent_id=allow_historical_context_outbox_reconciliation_parent_id,
        prepared_transport_authority=prepared_transport_authority,
        AmbiguousRemotePostOutcome=AmbiguousRemotePostOutcome,
        CONFIRMED_REPLY_RECEIPT_FILE=CONFIRMED_REPLY_RECEIPT_FILE,
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE=HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        InvalidMemePostReceipt=InvalidMemePostReceipt,
        InvalidRegularPostReceipt=InvalidRegularPostReceipt,
        MEME_POST_RECEIPT_FILE=MEME_POST_RECEIPT_FILE,
        Path=Path,
        REGULAR_POST_RECEIPT_FILE=REGULAR_POST_RECEIPT_FILE,
        block_if_remote_media_upload_receipt_exists=block_if_remote_media_upload_receipt_exists,
        block_if_remote_receipt_retirement_exists=block_if_remote_receipt_retirement_exists,
        block_if_remote_write_safety_incident_latched=block_if_remote_write_safety_incident_latched,
        block_if_remote_write_transport_journal_exists=block_if_remote_write_transport_journal_exists,
        confirmed_main_receipt_is_sole_local_recovery_barrier=confirmed_main_receipt_is_sole_local_recovery_barrier,
        current_main_post_attempt_is_semantically_valid=current_main_post_attempt_is_semantically_valid,
        exact_historical_context_sending_receipt_matches=exact_historical_context_sending_receipt_matches,
        historical_context_outbox_remote_attempt_is_blocking=historical_context_outbox_remote_attempt_is_blocking,
        historical_context_receipt_parent_for_local_reconciliation=historical_context_receipt_parent_for_local_reconciliation,
        historical_context_receipt_path_present_or_unsafe=historical_context_receipt_path_present_or_unsafe,
        inspect_transport_state=inspect_transport_state,
        load_confirmed_reply_receipt=load_confirmed_reply_receipt,
        load_meme_post_receipt=load_meme_post_receipt,
        load_regular_post_receipt=load_regular_post_receipt,
        remote_write_transport_journal_paths=remote_write_transport_journal_paths,
        transport_journal_is_blocking=transport_journal_is_blocking,
    )


def ambiguous_remote_post_is_blocking() -> bool:
    """Return the global write barrier state without starting any remote work."""
    return _remote_write_barriers.ambiguous_remote_post_is_blocking(
        historical_context_outbox_remote_attempt_is_blocking=historical_context_outbox_remote_attempt_is_blocking,
        historical_context_receipt_path_present_or_unsafe=historical_context_receipt_path_present_or_unsafe,
        log=log,
        remote_media_upload_receipt_is_blocking=remote_media_upload_receipt_is_blocking,
        remote_receipt_retirement_is_blocking=remote_receipt_retirement_is_blocking,
        remote_write_safety_incident_is_latched=remote_write_safety_incident_is_latched,
        remote_write_safety_marker_path_present_or_unsafe=remote_write_safety_marker_path_present_or_unsafe,
        remote_write_safety_protocol_is_active=remote_write_safety_protocol_is_active,
        remote_write_transport_journal_is_blocking=remote_write_transport_journal_is_blocking,
        unresolved_conversational_reply_receipt_is_blocking=unresolved_conversational_reply_receipt_is_blocking,
        unresolved_main_post_attempt_is_blocking=unresolved_main_post_attempt_is_blocking,
    )


class ConfirmedPostSigintDeferral:
    """Process-wide Python SIGINT handler state for one remote-post transaction."""

    def __init__(self) -> None:
        """Capture the prior handler and initialise the deferred-signal state."""
        self.previous_handler = signal.getsignal(signal.SIGINT)
        self.pending = False
        self.pending_frame: object | None = None

    def handle(self, _signum: int, frame: object | None) -> None:
        """Record a pending SIGINT without interrupting the durability window."""
        self.pending = True
        self.pending_frame = frame


def begin_confirmed_post_sigint_deferral() -> ConfirmedPostSigintDeferral:
    """Defer controlled SIGINT while a remote post gains durable identity.

    The production service stops its Python child with SIGINT. Once an X create
    request begins, the process-wide Python handler records that signal without
    raising until the durable receipt/fallback write completes. This also
    covers process-directed SIGINT delivered through another Python thread.
    """
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError(
            "Confirmed main-post creation requires the main thread for SIGINT deferral"
        )
    guard = ConfirmedPostSigintDeferral()
    signal.signal(signal.SIGINT, guard.handle)
    return guard


def end_confirmed_post_sigint_deferral(
    guard: ConfirmedPostSigintDeferral | None,
) -> None:
    """Restore the prior handler and deliver any deferred controlled SIGINT."""
    if guard is None:
        return
    signal.signal(signal.SIGINT, guard.previous_handler)
    if not guard.pending:
        return
    previous_handler = guard.previous_handler
    if previous_handler == signal.SIG_IGN:
        return
    if callable(previous_handler):
        previous_handler(signal.SIGINT, guard.pending_frame)
        return
    raise KeyboardInterrupt


def latch_remote_write_safety_marker_observation() -> None:
    """Keep both process barriers after a marker is seen or cannot be excluded."""
    global _AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN
    global _AMBIGUOUS_REMOTE_POST_SEEN
    _AMBIGUOUS_REMOTE_POST_SEEN = True
    _AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN = True


def remote_write_safety_marker_path_present_or_unsafe() -> bool:
    """Treat either barrier namespace entry or inspection error as blocking.

    ``Path.exists()`` follows symlinks and therefore reports a dangling link as
    absent.  A malformed, replaced, unreadable or otherwise unusual entry is
    not evidence that the remote-write incident has been reconciled.
    """
    return _safety_marker_snapshots.remote_write_safety_marker_path_present_or_unsafe(
        AMBIGUOUS_POST_OUTCOME_FILE=AMBIGUOUS_POST_OUTCOME_FILE,
        AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE=AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE,
        latch_remote_write_safety_marker_observation=latch_remote_write_safety_marker_observation,
        log=log,
        os=os,
    )


def require_remote_write_marker_removal_protocol() -> None:
    """Require the process-lifetime instance lock for marker acknowledgement.

    Production marker removal is supported only while the bot is stopped and a
    reconciler holds ``mrsMThatcher.lock`` exclusively.  The running daemon
    holds that lock for its lifetime, so a cooperating reconciler cannot remove
    the marker after the acknowledgement recheck.  Tests use isolated paths and
    exercise the same byte/identity checks without a production lock.
    """
    return _safety_marker_snapshots.require_remote_write_marker_removal_protocol(
        require_instance_lock_for_remote_write=require_instance_lock_for_remote_write,
    )


def read_remote_write_safety_marker_snapshot(
    path: Path | None = None,
    *,
    accepted_link_counts: frozenset[int] = frozenset({1}),
) -> tuple[int, int, int, int, bytes]:
    """Read one bounded, no-follow marker snapshot with stable file identity."""
    return _safety_marker_snapshots.read_remote_write_safety_marker_snapshot(
        path,
        accepted_link_counts=accepted_link_counts,
        AMBIGUOUS_POST_OUTCOME_FILE=AMBIGUOUS_POST_OUTCOME_FILE,
        REMOTE_WRITE_SAFETY_MARKER_MAX_BYTES=REMOTE_WRITE_SAFETY_MARKER_MAX_BYTES,
        latch_remote_write_safety_marker_observation=latch_remote_write_safety_marker_observation,
        os=os,
    )


def read_remote_write_safety_barrier_snapshot(
) -> tuple[Path, tuple[int, int, int, int, bytes]]:
    """Return one exact supported marker/successor state.

    The only supported two-name state is the fixed original/successor pair
    referring to one inode with exactly two links.  A sole successor with one
    link is the expected restart state after loss of the original pathname.
    Any other hard link, replacement, type change or identity split fails
    closed.
    """
    return _safety_marker_snapshots.read_remote_write_safety_barrier_snapshot(
        AMBIGUOUS_POST_OUTCOME_FILE=AMBIGUOUS_POST_OUTCOME_FILE,
        AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE=AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE,
        latch_remote_write_safety_marker_observation=latch_remote_write_safety_marker_observation,
        os=os,
        read_remote_write_safety_marker_snapshot=read_remote_write_safety_marker_snapshot,
    )


def acknowledge_durable_remote_write_safety_marker(
    *,
    expected_bytes: bytes | None = None,
) -> bool:
    """Synchronise and revalidate one unchanged marker namespace entry."""
    return _safety_marker_snapshots.acknowledge_durable_remote_write_safety_marker(
        expected_bytes=expected_bytes,
        AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE=AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE,
        fsync_parent_dir=fsync_parent_dir,
        read_remote_write_safety_barrier_snapshot=read_remote_write_safety_barrier_snapshot,
        require_remote_write_marker_removal_protocol=require_remote_write_marker_removal_protocol,
    )


def ensure_durable_remote_write_safety_marker(marker: dict) -> bool:
    """Write or acknowledge a marker without trusting atomic-write return alone."""
    return _safety_marker_snapshots.ensure_durable_remote_write_safety_marker(
        marker,
        AMBIGUOUS_POST_OUTCOME_FILE=AMBIGUOUS_POST_OUTCOME_FILE,
        AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE=AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE,
        acknowledge_durable_remote_write_safety_marker=acknowledge_durable_remote_write_safety_marker,
        atomic_write_json=atomic_write_json,
        log=log,
        os=os,
        remote_write_safety_protocol_is_active=remote_write_safety_protocol_is_active,
    )


def _set_ambiguous_marker_durability_uncertain(value: bool) -> None:
    """Set the root's uncertainty about remote-write marker durability."""
    global _AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN
    _AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN = value

def _set_ambiguous_remote_post_seen(value: bool) -> None:
    """Set the root's process-local remote-write incident latch."""
    global _AMBIGUOUS_REMOTE_POST_SEEN
    _AMBIGUOUS_REMOTE_POST_SEEN = value


def durable_remote_write_safety_marker_exists() -> bool:
    """Return whether restart safety survives loss of the in-process latch."""
    return _remote_write_incidents.durable_remote_write_safety_marker_exists(
        _set_ambiguous_marker_durability_uncertain=_set_ambiguous_marker_durability_uncertain,
        acknowledge_durable_remote_write_safety_marker=acknowledge_durable_remote_write_safety_marker,
        latch_remote_write_safety_marker_observation=latch_remote_write_safety_marker_observation,
        log=log,
        release_retained_sigint_deferral_after_durable_barrier=release_retained_sigint_deferral_after_durable_barrier,
        remote_write_safety_marker_path_present_or_unsafe=remote_write_safety_marker_path_present_or_unsafe,
        remote_write_safety_protocol_is_active=remote_write_safety_protocol_is_active,
    )


def durable_remote_write_safety_barrier_exists() -> bool:
    """Return whether restart safety survives loss of the process latch."""
    return _safety_marker_snapshots.durable_remote_write_safety_barrier_exists(
        MEDIA_UPLOAD_RECEIPT_FILE=MEDIA_UPLOAD_RECEIPT_FILE,
        durable_remote_write_safety_marker_exists=durable_remote_write_safety_marker_exists,
        historical_context_reply_store=historical_context_reply_store,
        load_confirmed_reply_receipt=load_confirmed_reply_receipt,
        load_meme_post_receipt=load_meme_post_receipt,
        load_regular_post_receipt=load_regular_post_receipt,
        media_upload_has_valid_restart_barrier=media_upload_has_valid_restart_barrier,
        release_retained_sigint_deferral_after_durable_barrier=release_retained_sigint_deferral_after_durable_barrier,
        remote_write_safety_protocol_is_active=remote_write_safety_protocol_is_active,
        remote_write_transport_journal_paths=remote_write_transport_journal_paths,
        transport_journal_has_valid_restart_barrier=transport_journal_has_valid_restart_barrier,
    )


def retain_sigint_deferral_without_durable_barrier(
    *,
    lane: str,
    guard: ConfirmedPostSigintDeferral | None,
) -> None:
    """Explain why controlled shutdown must remain deferred after total loss."""
    global _RETAINED_CONFIRMED_POST_SIGINT_GUARD
    if guard is not None:
        if (
            _RETAINED_CONFIRMED_POST_SIGINT_GUARD is not None
            and _RETAINED_CONFIRMED_POST_SIGINT_GUARD is not guard
        ):
            log.critical(
                "A different confirmed-post SIGINT guard was already retained; "
                "preserving the earlier process-wide guard lane=%s",
                lane,
            )
        else:
            _RETAINED_CONFIRMED_POST_SIGINT_GUARD = guard
    log.critical(
        "Keeping SIGINT deferred for lane=%s because the confirmed/ambiguous "
        "remote write has no durable local barrier. The process must remain "
        "alive and idle until manual reconciliation; do not restart it.",
        lane,
    )


def release_retained_sigint_deferral_after_durable_barrier() -> None:
    """Restore and deliver a deferred SIGINT once restart safety is durable."""
    global _RETAINED_CONFIRMED_POST_SIGINT_GUARD
    guard = _RETAINED_CONFIRMED_POST_SIGINT_GUARD
    if guard is None:
        return
    try:
        end_confirmed_post_sigint_deferral(guard)
    except BaseException:
        current_handler = signal.getsignal(signal.SIGINT)
        if getattr(current_handler, "__self__", None) is not guard:
            # Restoration succeeded and only delivery of the deferred signal
            # raised.  Do not retain a guard whose handler is no longer active.
            _RETAINED_CONFIRMED_POST_SIGINT_GUARD = None
        raise
    _RETAINED_CONFIRMED_POST_SIGINT_GUARD = None


def record_ambiguous_remote_post(payload: dict) -> None:
    """Persist a manual-reconciliation barrier without claiming success or failure."""
    return _remote_write_incidents.record_ambiguous_remote_post(
        payload,
        AMBIGUOUS_POST_OUTCOME_FILE=AMBIGUOUS_POST_OUTCOME_FILE,
        _set_ambiguous_marker_durability_uncertain=_set_ambiguous_marker_durability_uncertain,
        _set_ambiguous_remote_post_seen=_set_ambiguous_remote_post_seen,
        ensure_durable_remote_write_safety_marker=ensure_durable_remote_write_safety_marker,
        log=log,
        now_epoch=now_epoch,
    )


def latch_confirmed_post_persistence_failure(
    *,
    lane: str,
    post_id: str,
    failure_components: list[str],
) -> bool:
    """Block all writes after a confirmed post loses every complete recovery path.

    The in-process latch is set before any file operation.  This is essential
    when the durability failure is caused by an unwritable filesystem: exiting
    would let the service wrapper restart without a durable record and risk a
    duplicate post.

    Return whether the durable barrier marker was written (or already exists).
    """
    return _remote_write_incidents.latch_confirmed_post_persistence_failure(
        lane=lane,
        post_id=post_id,
        failure_components=failure_components,
        AMBIGUOUS_POST_OUTCOME_FILE=AMBIGUOUS_POST_OUTCOME_FILE,
        _set_ambiguous_marker_durability_uncertain=_set_ambiguous_marker_durability_uncertain,
        _set_ambiguous_remote_post_seen=_set_ambiguous_remote_post_seen,
        ensure_durable_remote_write_safety_marker=ensure_durable_remote_write_safety_marker,
        log=log,
        now_epoch=now_epoch,
    )


def confirmation_epoch_after_remote_success(source_receipt: dict) -> int:
    """Return a usable confirmation time without losing a known post ID.

    Reading the wall clock is deliberately best-effort *after* X has returned
    a valid post identity.  A clock failure at that point must not strand the
    transport journal in ``attempting`` state and throw away the one piece of
    information which makes automatic restart recovery possible.  Every
    supported sending receipt already contains a durable pre-request epoch;
    that is a conservative lower-bound fallback.
    """
    return _receipt_primitives.confirmation_epoch_after_remote_success(
        source_receipt,
        TransportJournalError=TransportJournalError,
        log=log,
        now_epoch=now_epoch,
        valid_receipt_epoch=valid_receipt_epoch,
    )


def create_post(
    text: str,
    media_ids: list[str] | None = None,
    reply_to_id: str | None = None,
    made_with_ai: bool = False,
    *,
    prepared_conversational_reply_receipt: dict | None = None,
    prepared_historical_context_reply_receipt: dict | None = None,
    prepared_main_post_attempt: dict | None = None,
    prepared_transport_authority: TransportAuthority | None = None,
    prepared_transport_source: SourceReceiptBinding | None = None,
    on_remote_transaction_started: Callable[[], None] | None = None,
) -> dict:
    """Create an X post with transactional ambiguity handling."""
    return _post_creation.create_post(
        text,
        media_ids,
        reply_to_id,
        made_with_ai,
        prepared_conversational_reply_receipt=prepared_conversational_reply_receipt,
        prepared_historical_context_reply_receipt=prepared_historical_context_reply_receipt,
        prepared_main_post_attempt=prepared_main_post_attempt,
        prepared_transport_authority=prepared_transport_authority,
        prepared_transport_source=prepared_transport_source,
        on_remote_transaction_started=on_remote_transaction_started,
        AmbiguousRemotePostOutcome=AmbiguousRemotePostOutcome,
        CONFIRMED_REPLY_RECEIPT_FILE=CONFIRMED_REPLY_RECEIPT_FILE,
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE=HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        ProvedRemotePostNonSuccess=ProvedRemotePostNonSuccess,
        RemoteOperationsPaused=RemoteOperationsPaused,
        TransportJournalError=TransportJournalError,
        abort_untransmitted_transport_transaction=abort_untransmitted_transport_transaction,
        arm_transport_transaction=arm_transport_transaction,
        begin_transport_transaction=begin_transport_transaction,
        bind_lane_transport_source=bind_lane_transport_source,
        block_if_ambiguous_remote_post=block_if_ambiguous_remote_post,
        block_if_remote_write_safety_incident_latched=block_if_remote_write_safety_incident_latched,
        confirm_transport_transaction=confirm_transport_transaction,
        confirmation_epoch_after_remote_success=confirmation_epoch_after_remote_success,
        current_main_post_attempt_is_semantically_valid=current_main_post_attempt_is_semantically_valid,
        freeze_tweet_request=freeze_tweet_request,
        global_remote_writes_paused=global_remote_writes_paused,
        journal_path_for_receipt=journal_path_for_receipt,
        log=log,
        main_post_attempt_binds_payload=main_post_attempt_binds_payload,
        main_post_attempt_path=main_post_attempt_path,
        mark_main_post_attempt_attempting=mark_main_post_attempt_attempting,
        record_ambiguous_remote_post=record_ambiguous_remote_post,
        require_instance_lock_for_remote_write=require_instance_lock_for_remote_write,
        retire_consumed_transport_transaction_after_proved_remote_non_success=retire_consumed_transport_transaction_after_proved_remote_non_success,
        sending_reply_receipt_is_semantically_valid=sending_reply_receipt_is_semantically_valid,
        transaction_mutation_authority=transaction_mutation_authority,
        x_request=x_request,
    )


# ---------------------------------------------------------------------
# Quote/image posting
# ---------------------------------------------------------------------

class NoEligibleImageForQuote(RuntimeError):
    """Raised when no eligible image remains for a quotation."""
    pass


class QuoteSpecificImageMismatch(NoEligibleImageForQuote):
    """Raised when an image affirmatively conflicts with a quotation."""
    pass


class NoViableQuoteImagePair(RuntimeError):
    """Raised when no viable quotation-image pair can be selected."""
    def __init__(self, message: str, attempts: int, excluded_last_image: str | None = None) -> None:
        """Initialise the no viable quote image pair."""
        super().__init__(message)
        self.attempts = attempts
        self.excluded_last_image = excluded_last_image


class GlobalImageUnavailable(NoEligibleImageForQuote):
    """Raised when an image is unavailable across all quotation attempts."""
    pass


class InvalidRegularPostReceipt(RuntimeError):
    """Raised when a regular-post receipt fails semantic validation."""
    pass


class UnresolvedRegularPostReceipt(RuntimeError):
    """Raised when a regular-post receipt requires operator reconciliation."""
    pass


class InvalidMemePostReceipt(RuntimeError):
    """Raised when a meme-post receipt fails semantic validation."""
    pass


class UnresolvedMemePostReceipt(RuntimeError):
    """Raised when a meme-post receipt requires operator reconciliation."""
    pass


class ConfirmedPostLocalPersistenceError(RuntimeError):
    # This covers failures after a valid remote post id is known. A hard crash
    # after receiving that id but before durable receipt fsync can still leave
    # no replay record. Separately, if X accepts a post but no response reaches
    # this process, there is no known post id to receipt.
    """Raised when a confirmed remote post cannot be persisted locally."""
    pass


class UnrecoverableConfirmedPostPersistenceError(ConfirmedPostLocalPersistenceError):
    """Raised when a confirmed main post has no complete durable representation."""
    pass


class ConfirmedPendingScheduleDurabilityUncertain(
    UnrecoverableConfirmedPostPersistenceError
):
    """Raised when a promoted pending receipt cannot be proved directory-durable."""

    def __init__(self, message: str, *, durable_barrier: bool) -> None:
        """Record whether a separate restart-safe marker was established."""
        super().__init__(message)
        self.durable_barrier = bool(durable_barrier)


class InvalidConfirmedReplyReceipt(RuntimeError):
    """Raised when a confirmed-reply receipt fails semantic validation."""
    pass


class UnresolvedSendingReplyReceipt(RuntimeError):
    """Raised when a pre-send reply receipt requires manual reconciliation."""
    pass


class ConfirmedReplyLocalPersistenceError(RuntimeError):
    """Raised when a reply transaction needs durable local recovery.

    Conversational replies now have a pre-send lifecycle receipt, so the
    exception does not imply that restart identity was lost.
    """
    pass


class UnrecoverableConfirmedReplyPersistenceError(
    ConfirmedReplyLocalPersistenceError
):
    """Raised when a confirmed conversational reply has no durable identity."""
    pass


class CorruptUsedHistoryError(RuntimeError):
    """Raised when durable used-history data is corrupt or unsafe."""
    pass


class UnsafeImageHistoryMigration(RuntimeError):
    """Raised when legacy image history cannot be migrated unambiguously."""
    pass


class StaleImageMetadata(RuntimeError):
    """Raised when image metadata does not match the current file."""
    pass

TOKEN_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in",
    "is", "it", "of", "on", "or", "our", "the", "their", "this", "to", "with",
}


def _asset_metadata_owner() -> _asset_metadata.AssetMetadata:
    """Bind current external boundaries without runtime work or caller state."""
    return _asset_metadata.AssetMetadata(
        log=log,
        quote_file=QUOTE_ANALYSIS_FILE,
        quote_overrides_file=QUOTE_ANALYSIS_OVERRIDES_FILE,
        image_file=IMAGE_ANALYSIS_FILE,
        image_glob=IMAGE_GLOB,
        glob=glob,
        image_sha256=current_image_sha256,
        stale_image_metadata=StaleImageMetadata,
        meme_file=MEME_ANALYSIS_FILE,
    )


def load_json_object(path: Path, *, label: str) -> dict | None:
    """Load JSON object."""
    return _asset_metadata_owner().load_json(path, label=label)


collapse_quote_whitespace = _asset_metadata.collapse_quote_whitespace


def quote_text_hash(text: str) -> str:
    """Return whether quote text hash."""
    return _asset_metadata.quote_text_hash(text)


def file_sha256(path: Path) -> str:
    """Return the file SHA-256."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def deep_merge_dict(base: dict, patch: dict) -> dict:
    """Return the deep merge dict."""
    return _asset_metadata.deep_merge_dict(
        base,
        patch,
    )


def apply_quote_analysis_overrides(raw_analysis: dict, overrides: dict | None) -> dict:
    """Apply quote analysis overrides."""
    return _asset_metadata_owner().apply_quote_overrides(raw_analysis, overrides)


def load_quote_analysis() -> dict | None:
    """Load validated quotation-analysis metadata and local overrides."""
    return _asset_metadata_owner().load_quote()


def load_image_analysis_file(path: Path, *, label: str) -> dict | None:
    """Load image analysis file."""
    return _asset_metadata_owner().load_image_file(path, label=label)


def load_image_analysis() -> dict | None:
    """Load metadata for the original-image corpus."""
    return _asset_metadata_owner().load_image()


mm_dd_in_window = _quote_candidates.mm_dd_in_window


def _quote_candidates_owner() -> _quote_candidates.QuoteCandidates:
    """Bind current external boundaries without runtime work or caller state."""
    return _quote_candidates.QuoteCandidates(
        season_date_specific_weight=QUOTE_SEASON_DATE_SPECIFIC_WEIGHT,
        season_strong_weight=QUOTE_SEASON_STRONG_WEIGHT,
        season_soft_weight=QUOTE_SEASON_SOFT_WEIGHT,
        quality_weight_max_multiplier=QUOTE_QUALITY_WEIGHT_MAX_MULTIPLIER,
        quote_text_hash=quote_text_hash,
        metadata_for_hash=quote_metadata_for_hash,
        log=log,
        lines_file=LINES_FILE,
        load_quote_analysis=load_quote_analysis,
        validate_analysis=validate_quote_analysis_against_lines,
        current_datetime=current_datetime,
        research_dir=HISTORICAL_CONTEXT_RESEARCH_DIR,
        eligible_manifest_file=RUNTIME_ELIGIBLE_QUOTE_MANIFEST_FILE,
        research_file=COMPLETED_QUOTE_RESEARCH_FILE,
        load_json_object=load_json_object,
        file_sha256=file_sha256,
    )


def any_window_matches_today(windows: object, today_mm_dd: str) -> bool:
    """Return whether any window matches today."""
    return _quote_candidates_owner().any_window(windows, today_mm_dd)


def quote_season_status(analysis: dict | None, *, today_mm_dd: str) -> dict:
    """Return the quote season status."""
    return _quote_candidates_owner().season_status(analysis, today_mm_dd=today_mm_dd)


def quote_candidate_weight(analysis: dict | None, *, today_mm_dd: str) -> tuple[float, dict]:
    """Return the quote candidate weight."""
    return _quote_candidates_owner().weight(analysis, today_mm_dd=today_mm_dd)


weighted_random_choice = _quote_candidates.weighted_random_choice


def quote_metadata_for_hash(quote_analysis: dict | None, quote_hash: str, text: str = "") -> dict | None:
    """Return whether quote metadata for hash."""
    return _asset_metadata_owner().quote_for_hash(quote_analysis, quote_hash, text)


def current_quote_hashes_by_line(lines: list[str]) -> dict[int, str]:
    """Return whether current quote hashes by line."""
    return _quote_candidates_owner().hashes_by_line(lines)


def quote_used_history_has_legacy_indices(value: set) -> bool:
    """Return whether quote used history has legacy indices."""
    return _used_history_owner().quote_used_history_has_legacy_indices(value)


def quote_source_matches_analysis(quote_analysis: dict | None, lines: list[str]) -> bool:
    """Return whether quote source matches analysis."""
    return _used_history_owner().quote_source_matches_analysis(quote_analysis, lines)


def normalise_quote_used_hashes(raw_used: set, lines: list[str], quote_analysis: dict | None = None) -> tuple[set, bool]:
    """Return whether normalise quote used hashes."""
    return _used_history_owner().normalise_quote_used_hashes(raw_used, lines, quote_analysis)


def load_quote_used_hashes(lines: list[str]) -> set[str]:
    """Return whether load quote used hashes."""
    return _used_history_owner().load_quote_used_hashes(lines)


def save_quote_used_hashes(path: Path, value: set[str], *, durable: bool = False) -> None:
    """Return whether save quote used hashes."""
    return _used_history_owner().save_quote_used_hashes(path, value, durable=durable)


def validate_quote_analysis_against_lines(quote_analysis: dict, lines: list[str]) -> None:
    """Validate quote analysis against lines."""
    return _asset_metadata_owner().validate_quote_lines(quote_analysis, lines)


def current_image_paths() -> list[str]:
    """Return the current image paths."""
    return _asset_metadata_owner().image_paths()


def save_image_used_basenames(path: Path, value: set[str], *, durable: bool = False) -> None:
    """Save image used basenames."""
    return _used_history_owner().save_image_used_basenames(path, value, durable=durable)


def fsync_parent_dir(path: Path, *, strict: bool = False) -> None:
    """Synchronise parent dir."""
    return _durable_json_io.fsync_parent_dir(
        path,
        strict=strict,
        log=log,
        os=os,
    )


def atomic_write_json(path: Path, value: object, *, durable: bool = False) -> None:
    """Write JSON atomically and optionally durably."""
    return _durable_json_io.atomic_write_json(
        path,
        value,
        durable=durable,
        Path=Path,
        DURABLE_RUNTIME_JSON_MAX_BYTES=DURABLE_RUNTIME_JSON_MAX_BYTES,
        fsync_parent_dir=fsync_parent_dir,
        os=os,
        tempfile=tempfile,
    )


canonical_atomic_json_bytes = _durable_json_io.canonical_atomic_json_bytes


RECEIPT_JSON_MAX_BYTES = 1024 * 1024


class UnsafeReceiptNamespace(RuntimeError):
    """A receipt pathname exists but cannot be trusted as one stable file."""


def receipt_namespace_entry_exists(path: Path) -> bool:
    """Return lexical namespace presence without following a symbolic link."""
    try:
        os.lstat(path)
    except FileNotFoundError:
        return False
    return True


def _strict_receipt_json_bytes(data: bytes) -> object:
    """Parse one receipt without duplicate names or non-finite constants."""
    return _durable_json_io._strict_receipt_json_bytes(
        data,
        UnsafeReceiptNamespace=UnsafeReceiptNamespace,
    )


def load_receipt_json_no_follow(path: Path) -> tuple[bool, object | None]:
    """Read one bounded stable ordinary receipt, distinguishing true absence.

    A dangling symlink, directory, FIFO, replacement or disappearance after
    initial observation is an unsafe existing authority, never an absent file.
    """
    return _durable_json_io.load_receipt_json_no_follow(
        path,
        RECEIPT_JSON_MAX_BYTES=RECEIPT_JSON_MAX_BYTES,
        UnsafeReceiptNamespace=UnsafeReceiptNamespace,
        _strict_receipt_json_bytes=_strict_receipt_json_bytes,
        os=os,
        stat=stat,
    )


def durable_create_receipt_json(path: Path, value: object) -> None:
    """Publish a new receipt with O_EXCL and prove its exact stable bytes."""
    return _durable_json_io.durable_create_receipt_json(
        path,
        value,
        RECEIPT_JSON_MAX_BYTES=RECEIPT_JSON_MAX_BYTES,
        UnsafeReceiptNamespace=UnsafeReceiptNamespace,
        fsync_parent_dir=fsync_parent_dir,
        os=os,
        stat=stat,
    )


def atomic_json_file_exactly_matches(path: Path, value: object) -> bool:
    """Compare a receipt with its expected canonical bytes without JSON parsing."""
    return _main_post_confirmation_persistence.atomic_json_file_exactly_matches(
        path,
        value,
    )


def valid_post_id(value: object) -> bool:
    """Return whether valid post ID."""
    return _receipt_primitives.valid_post_id(
        value,
    )


def valid_string_post_id(value: object) -> bool:
    """Return whether a durable receipt stores an exact string post ID."""
    return _receipt_primitives.valid_string_post_id(
        value,
    )


def valid_receipt_epoch(value: object) -> bool:
    """Return whether valid receipt epoch."""
    return _receipt_primitives.valid_receipt_epoch(
        value,
        MAX_CONFIRMATION_EPOCH=MAX_CONFIRMATION_EPOCH,
        MIN_CONFIRMATION_EPOCH=MIN_CONFIRMATION_EPOCH,
    )


receipt_int = _receipt_primitives.receipt_int


receipt_bool = _receipt_primitives.receipt_bool


def _receipt_dates_owner() -> _receipt_primitives.ReceiptDates:
    """Bind current external boundaries without runtime work or caller state."""
    return _receipt_primitives.ReceiptDates(
        datetime=datetime,
        now_epoch=now_epoch,
        reply_cap_timezone=MAIN_POST_SCHEDULE_TIMEZONE,
        zone_info=ZoneInfo,
    )


def safe_epoch_date_str(epoch: int) -> str | None:
    """Return the safe epoch date str."""
    return _receipt_dates_owner().safe_local_date(epoch)


def safe_reply_cap_date_str(epoch: int) -> str | None:
    """Return a safe Europe/London conversational daily-cap date."""
    return _receipt_dates_owner().safe_reply_cap_date(epoch)


def main_post_schedule_zone(timezone_name: object) -> ZoneInfo:
    """Return the sole calendar zone accepted by current main-post receipts."""
    return _receipt_primitives.main_post_schedule_zone(
        timezone_name,
        MAIN_POST_SCHEDULE_TIMEZONE=MAIN_POST_SCHEDULE_TIMEZONE,
        ZoneInfo=ZoneInfo,
        ZoneInfoNotFoundError=ZoneInfoNotFoundError,
    )


def bound_schedule_datetime(epoch: int, timezone_name: object) -> datetime:
    """Interpret one durable epoch in its exact receipt-bound calendar zone."""
    return _receipt_primitives.bound_schedule_datetime(
        epoch,
        timezone_name,
        datetime=datetime,
        main_post_schedule_zone=main_post_schedule_zone,
    )


def safe_bound_schedule_date_str(
    epoch: int,
    timezone_name: object,
) -> str | None:
    """Return a bound calendar date, or ``None`` for invalid receipt input."""
    return _receipt_primitives.safe_bound_schedule_date_str(
        epoch,
        timezone_name,
        bound_schedule_datetime=bound_schedule_datetime,
    )


def valid_receipt_basename(value: object) -> bool:
    """Return whether valid receipt basename."""
    return _receipt_primitives.valid_receipt_basename(
        value,
    )


def canonical_remote_post_payload_sha256(payload: dict) -> str:
    """Return the stable identity of one exact X create payload."""
    return _main_post_attempt_values.canonical_remote_post_payload_sha256(payload)


main_post_attempt_payload = _main_post_attempt_values.main_post_attempt_payload


BOUND_MEME_SCHEDULE_STATE_KEYS = _main_post_attempt_values.BOUND_MEME_SCHEDULE_STATE_KEYS
MAIN_POST_ATTEMPT_MAX_BOUND_DELAY_SECONDS = _main_post_attempt_values.MAIN_POST_ATTEMPT_MAX_BOUND_DELAY_SECONDS


def bound_meme_schedule_state(
    state: dict,
    *,
    schedule_timezone: str | None = None,
) -> dict:
    """Capture the exact meme-schedule inputs bound before a regular X write."""
    return _main_post_attempt_values.bound_meme_schedule_state(
        state,
        schedule_timezone=schedule_timezone,
        MAIN_POST_SCHEDULE_TIMEZONE=MAIN_POST_SCHEDULE_TIMEZONE,
        MEME_SCHEDULE_VERSION=MEME_SCHEDULE_VERSION,
        safe_bound_schedule_date_str=safe_bound_schedule_date_str,
    )


def bound_meme_schedule_state_is_valid(
    value: object,
    *,
    schedule_timezone: str | None = None,
) -> bool:
    """Return whether a pre-send meme-schedule snapshot is self-consistent."""
    return _main_post_attempt_values.bound_meme_schedule_state_is_valid(
        value,
        schedule_timezone=schedule_timezone,
        MAIN_POST_SCHEDULE_TIMEZONE=MAIN_POST_SCHEDULE_TIMEZONE,
        MEME_SCHEDULE_MODES=MEME_SCHEDULE_MODES,
        MEME_SCHEDULE_VERSION=MEME_SCHEDULE_VERSION,
        safe_bound_schedule_date_str=safe_bound_schedule_date_str,
        valid_receipt_epoch=valid_receipt_epoch,
    )


def main_post_attempt_is_semantically_valid(data: object) -> bool:
    """Return whether a pre-send regular or meme attempt is self-consistent."""
    return _main_post_receipts.main_post_attempt_is_semantically_valid(
        data,
        MAIN_POST_SCHEDULE_TIMEZONE=MAIN_POST_SCHEDULE_TIMEZONE,
        MEME_SCHEDULE_VERSION=MEME_SCHEDULE_VERSION,
        bound_meme_schedule_state_is_valid=bound_meme_schedule_state_is_valid,
        safe_bound_schedule_date_str=safe_bound_schedule_date_str,
        valid_receipt_epoch=valid_receipt_epoch,
    )


def main_post_attempt_binds_payload(attempt: dict, payload: dict) -> bool:
    """Return whether an attempt authorises exactly one remote payload."""
    return _main_post_attempt_values.main_post_attempt_binds_payload(
        attempt,
        payload,
        current_main_post_attempt_is_semantically_valid=current_main_post_attempt_is_semantically_valid,
    )


def current_main_post_attempt_is_semantically_valid(data: object) -> bool:
    """Return whether an attempt belongs to the current writable generation."""
    return _main_post_attempt_values.current_main_post_attempt_is_semantically_valid(
        data,
        main_post_attempt_is_semantically_valid=main_post_attempt_is_semantically_valid,
    )


def build_main_post_attempt(
    *,
    lane: str,
    text: str,
    media_ids: list[str],
    made_with_ai: bool,
    selected_identity: dict,
    recovery_plan: dict,
    attempt_epoch: int | None = None,
) -> dict:
    """Build a durable pre-send identity for one main-post transaction."""
    return _main_post_attempt_values.build_main_post_attempt(
        lane=lane,
        text=text,
        media_ids=media_ids,
        made_with_ai=made_with_ai,
        selected_identity=selected_identity,
        recovery_plan=recovery_plan,
        attempt_epoch=attempt_epoch,
        MAIN_POST_SCHEDULE_TIMEZONE=MAIN_POST_SCHEDULE_TIMEZONE,
        current_main_post_attempt_is_semantically_valid=current_main_post_attempt_is_semantically_valid,
        now_epoch=now_epoch,
        os=os,
    )


def main_post_attempt_path(attempt: dict) -> Path:
    """Return the receipt path which owns one main-post attempt."""
    return _main_post_receipt_storage.main_post_attempt_path(
        attempt,
        MEME_POST_RECEIPT_FILE=MEME_POST_RECEIPT_FILE,
        REGULAR_POST_RECEIPT_FILE=REGULAR_POST_RECEIPT_FILE,
    )


def write_main_post_attempt(attempt: dict) -> None:
    """Durably record a main-post transaction before its X create request."""
    return _main_post_receipt_storage.write_main_post_attempt(
        attempt,
        CONFIRMED_REPLY_RECEIPT_FILE=CONFIRMED_REPLY_RECEIPT_FILE,
        InvalidConfirmedReplyReceipt=InvalidConfirmedReplyReceipt,
        MEME_POST_RECEIPT_FILE=MEME_POST_RECEIPT_FILE,
        REGULAR_POST_RECEIPT_FILE=REGULAR_POST_RECEIPT_FILE,
        UnresolvedRegularPostReceipt=UnresolvedRegularPostReceipt,
        current_main_post_attempt_is_semantically_valid=current_main_post_attempt_is_semantically_valid,
        durable_create_receipt_json=durable_create_receipt_json,
        log=log,
        main_post_attempt_path=main_post_attempt_path,
        receipt_namespace_entry_exists=receipt_namespace_entry_exists,
        remote_receipt_retirement_is_blocking=remote_receipt_retirement_is_blocking,
    )


def prepare_main_tweet_transport(
    attempt: dict,
) -> tuple[dict, SourceReceiptBinding, TransportAuthority]:
    """Publish a prepared tweet owner before retiring confirmed media state."""
    return _transport_source_preparation.prepare_main_tweet_transport(
        attempt,
        TransportJournalError=TransportJournalError,
        begin_transport_transaction=begin_transport_transaction,
        bind_lane_transport_source=bind_lane_transport_source,
        current_main_post_attempt_is_semantically_valid=current_main_post_attempt_is_semantically_valid,
        main_post_attempt_path=main_post_attempt_path,
        mark_main_post_attempt_attempting=mark_main_post_attempt_attempting,
    )


def validate_confirmed_media_upload_metadata(
    confirmation: ConfirmedMediaUpload,
) -> None:
    """Validate metadata from the exact confirmed media generation."""
    return _transport_source_preparation.validate_confirmed_media_upload_metadata(
        confirmation,
        ConfirmedMediaUpload=ConfirmedMediaUpload,
        MediaUploadReceiptError=MediaUploadReceiptError,
        inspect_media_upload_receipt=inspect_media_upload_receipt,
    )


def handoff_confirmed_media_upload_to_main_attempt(
    attempt: dict,
    transport_authority: TransportAuthority,
) -> None:
    """Retire media state only beneath an independent prepared tweet pair."""
    return _post_creation.handoff_confirmed_media_upload_to_main_attempt(
        attempt,
        transport_authority,
        MEDIA_UPLOAD_RECEIPT_FILE=MEDIA_UPLOAD_RECEIPT_FILE,
        MediaUploadReceiptError=MediaUploadReceiptError,
        bind_media_handoff_to_transport=bind_media_handoff_to_transport,
        validate_confirmed_media_upload_metadata=validate_confirmed_media_upload_metadata,
        load_confirmed_media_upload=load_confirmed_media_upload,
        log=log,
        main_post_attempt_path=main_post_attempt_path,
        retire_confirmed_media_upload=retire_confirmed_media_upload,
        transaction_mutation_authority=transaction_mutation_authority,
    )


def mark_main_post_attempt_attempting(attempt: dict) -> dict:
    """Atomically consume one sending authorisation before remote transmission."""
    return _main_post_receipt_storage.mark_main_post_attempt_attempting(
        attempt,
        AmbiguousRemotePostOutcome=AmbiguousRemotePostOutcome,
        REGULAR_POST_RECEIPT_FILE=REGULAR_POST_RECEIPT_FILE,
        current_main_post_attempt_is_semantically_valid=current_main_post_attempt_is_semantically_valid,
        load_meme_post_receipt=load_meme_post_receipt,
        load_regular_post_receipt=load_regular_post_receipt,
        log=log,
        main_post_attempt_path=main_post_attempt_path,
        replace_exact_source_receipt_document=replace_exact_source_receipt_document,
        transaction_mutation_authority=transaction_mutation_authority,
    )


def remove_main_post_attempt(
    attempt: dict,
    *,
    sending_disposition: str,
    commit_proof=None,
) -> None:
    """Retire an exact sending attempt after one proved-safe disposition."""
    if sending_disposition == "confirmed_state_fallback":
        from mrs_bot_state_generation import require_commit_proof
        require_commit_proof(commit_proof)
        commit_proof.require_receipt(attempt)
    return _main_post_receipt_storage.remove_main_post_attempt(
        attempt,
        sending_disposition=sending_disposition,
        AmbiguousRemotePostOutcome=AmbiguousRemotePostOutcome,
        current_main_post_attempt_is_semantically_valid=current_main_post_attempt_is_semantically_valid,
        load_receipt_json_no_follow=load_receipt_json_no_follow,
        log=log,
        main_post_attempt_path=main_post_attempt_path,
        retire_current_source_receipt=functools.partial(
            retire_current_source_receipt, commit_proof=commit_proof,
            **({"disposition": "definite_non_success"}
               if sending_disposition == "definite_non_success" else {}),
        ),
    )


def confirmed_receipt_matches_main_attempt(receipt: dict, attempt: dict) -> bool:
    """Return whether a confirmed receipt atomically promotes one attempt."""
    return _main_post_attempt_values.confirmed_receipt_matches_main_attempt(
        receipt,
        attempt,
        main_post_attempt_is_semantically_valid=main_post_attempt_is_semantically_valid,
    )


def confirmed_pending_schedule_receipt_is_semantically_valid(
    data: object,
    *,
    expected_lane: str | None = None,
) -> bool:
    """Validate a remote-confirmed receipt awaiting local schedule materialisation."""
    return _main_post_receipts.confirmed_pending_schedule_receipt_is_semantically_valid(
        data,
        expected_lane=expected_lane,
        main_post_attempt_is_semantically_valid=main_post_attempt_is_semantically_valid,
        valid_receipt_epoch=valid_receipt_epoch,
    )


def build_confirmed_pending_schedule_receipt(
    attempt: dict,
    *,
    post_id: str,
    confirmation_epoch: int,
    image_summary: str = "",
) -> dict:
    """Build a versioned confirmed receipt without deriving local schedules."""
    return _main_post_attempt_values.build_confirmed_pending_schedule_receipt(
        attempt,
        post_id=post_id,
        confirmation_epoch=confirmation_epoch,
        image_summary=image_summary,
        confirmed_pending_schedule_receipt_is_semantically_valid=confirmed_pending_schedule_receipt_is_semantically_valid,
        main_post_attempt_is_semantically_valid=main_post_attempt_is_semantically_valid,
        valid_receipt_epoch=valid_receipt_epoch,
    )


def confirmation_epoch_for_main_attempt(attempt: dict, observed_epoch: int) -> int:
    """Return a confirmation epoch which cannot precede its durable attempt."""
    return _main_post_attempt_values.confirmation_epoch_for_main_attempt(
        attempt,
        observed_epoch,
        log=log,
    )


def promote_main_post_attempt_to_confirmed_pending_schedule(
    attempt: dict,
    *,
    post_id: str,
    confirmation_epoch: int,
    image_summary: str = "",
) -> dict:
    """Atomically bind a confirmed remote identity before fallible local work."""
    return _main_post_confirmation_persistence.promote_main_post_attempt_to_confirmed_pending_schedule(
        attempt,
        post_id=post_id,
        confirmation_epoch=confirmation_epoch,
        image_summary=image_summary,
        AmbiguousRemotePostOutcome=AmbiguousRemotePostOutcome,
        BoundSourceReceiptTransitionError=BoundSourceReceiptTransitionError,
        ConfirmedPendingScheduleDurabilityUncertain=ConfirmedPendingScheduleDurabilityUncertain,
        REGULAR_POST_RECEIPT_FILE=REGULAR_POST_RECEIPT_FILE,
        TRANSPORT_SOURCE_VALIDATOR_ID=TRANSPORT_SOURCE_VALIDATOR_ID,
        TransportJournalError=TransportJournalError,
        _set_ambiguous_remote_post_seen=_set_ambiguous_remote_post_seen,
        atomic_json_file_exactly_matches=atomic_json_file_exactly_matches,
        bind_confirmed_transport_source=bind_confirmed_transport_source,
        build_confirmed_pending_schedule_receipt=build_confirmed_pending_schedule_receipt,
        fsync_parent_dir=fsync_parent_dir,
        journal_path_for_receipt=journal_path_for_receipt,
        latch_confirmed_post_persistence_failure=latch_confirmed_post_persistence_failure,
        load_meme_post_receipt=load_meme_post_receipt,
        load_regular_post_receipt=load_regular_post_receipt,
        log=log,
        main_post_attempt_path=main_post_attempt_path,
        remote_write_safety_incident_is_latched=remote_write_safety_incident_is_latched,
        replace_bound_source_receipt=replace_bound_source_receipt,
        transaction_mutation_authority=transaction_mutation_authority,
        transport_source_semantic_validator=transport_source_semantic_validator,
    )


def materialize_bound_regular_schedule_receipt(
    pending: dict,
    *,
    _validate_result: bool = True,
) -> dict:
    """Build a full regular receipt solely from its durable bound plan."""
    return _main_post_receipts.materialize_bound_regular_schedule_receipt(
        pending,
        _validate_result=_validate_result,
        InvalidRegularPostReceipt=InvalidRegularPostReceipt,
        bound_schedule_datetime=bound_schedule_datetime,
        confirmed_pending_schedule_receipt_is_semantically_valid=confirmed_pending_schedule_receipt_is_semantically_valid,
        regular_post_receipt_is_semantically_valid=regular_post_receipt_is_semantically_valid,
        safe_bound_schedule_date_str=safe_bound_schedule_date_str,
    )


def materialize_bound_meme_schedule_receipt(
    pending: dict,
    *,
    _validate_result: bool = True,
) -> dict:
    """Build a full meme receipt solely from its durable bound plan."""
    return _main_post_receipts.materialize_bound_meme_schedule_receipt(
        pending,
        _validate_result=_validate_result,
        InvalidMemePostReceipt=InvalidMemePostReceipt,
        bound_schedule_datetime=bound_schedule_datetime,
        confirmed_pending_schedule_receipt_is_semantically_valid=confirmed_pending_schedule_receipt_is_semantically_valid,
        meme_post_receipt_is_semantically_valid=meme_post_receipt_is_semantically_valid,
    )


def finalize_confirmed_pending_schedule_receipt(
    pending: dict,
) -> dict:
    """Atomically replace one pending schedule with its complete local receipt."""
    return _main_post_receipt_storage.finalize_confirmed_pending_schedule_receipt(
        pending,
        REGULAR_POST_RECEIPT_FILE=REGULAR_POST_RECEIPT_FILE,
        confirmed_pending_schedule_receipt_is_semantically_valid=confirmed_pending_schedule_receipt_is_semantically_valid,
        load_meme_post_receipt=load_meme_post_receipt,
        load_regular_post_receipt=load_regular_post_receipt,
        log=log,
        main_post_attempt_path=main_post_attempt_path,
        materialize_bound_meme_schedule_receipt=materialize_bound_meme_schedule_receipt,
        materialize_bound_regular_schedule_receipt=materialize_bound_regular_schedule_receipt,
        write_meme_post_receipt=write_meme_post_receipt,
        write_regular_post_receipt=write_regular_post_receipt,
    )


def write_regular_post_receipt(receipt: dict) -> None:
    """Write regular post receipt."""
    return _main_post_receipt_storage.write_regular_post_receipt(
        receipt,
        MEME_POST_RECEIPT_FILE=MEME_POST_RECEIPT_FILE,
        REGULAR_POST_RECEIPT_FILE=REGULAR_POST_RECEIPT_FILE,
        UnresolvedMemePostReceipt=UnresolvedMemePostReceipt,
        UnresolvedRegularPostReceipt=UnresolvedRegularPostReceipt,
        atomic_write_json=atomic_write_json,
        confirmed_pending_schedule_receipt_is_semantically_valid=confirmed_pending_schedule_receipt_is_semantically_valid,
        confirmed_receipt_matches_main_attempt=confirmed_receipt_matches_main_attempt,
        durable_create_receipt_json=durable_create_receipt_json,
        load_regular_post_receipt=load_regular_post_receipt,
        log=log,
        materialize_bound_regular_schedule_receipt=materialize_bound_regular_schedule_receipt,
        receipt_namespace_entry_exists=receipt_namespace_entry_exists,
        regular_post_receipt_is_semantically_valid=regular_post_receipt_is_semantically_valid,
        remote_receipt_retirement_is_blocking=remote_receipt_retirement_is_blocking,
    )


def regular_post_receipt_is_semantically_valid(data: dict) -> bool:
    """Return whether a regular-post receipt is internally consistent."""
    return _main_post_receipts.regular_post_receipt_is_semantically_valid(
        data,
        MAIN_POST_SCHEDULE_TIMEZONE=MAIN_POST_SCHEDULE_TIMEZONE,
        MEME_SCHEDULE_MODES=MEME_SCHEDULE_MODES,
        MEME_SCHEDULE_VERSION=MEME_SCHEDULE_VERSION,
        confirmed_pending_schedule_receipt_is_semantically_valid=confirmed_pending_schedule_receipt_is_semantically_valid,
        main_post_attempt_is_semantically_valid=main_post_attempt_is_semantically_valid,
        materialize_bound_regular_schedule_receipt=materialize_bound_regular_schedule_receipt,
        safe_bound_schedule_date_str=safe_bound_schedule_date_str,
        valid_receipt_epoch=valid_receipt_epoch,
    )


def load_regular_post_receipt() -> tuple[str, dict | None]:
    """Load regular post receipt."""
    return _main_post_receipt_storage.load_regular_post_receipt(
        REGULAR_POST_RECEIPT_FILE=REGULAR_POST_RECEIPT_FILE,
        confirmed_pending_schedule_receipt_is_semantically_valid=confirmed_pending_schedule_receipt_is_semantically_valid,
        load_receipt_json_no_follow=load_receipt_json_no_follow,
        log=log,
        main_post_attempt_is_semantically_valid=main_post_attempt_is_semantically_valid,
        regular_post_receipt_is_semantically_valid=regular_post_receipt_is_semantically_valid,
    )


def remove_regular_post_receipt(receipt: dict, *, commit_proof=None) -> None:
    """Retire one exact reconciled regular-post receipt."""
    from mrs_bot_state_generation import require_commit_proof
    require_commit_proof(commit_proof)
    commit_proof.require_receipt(receipt)
    return _main_post_receipt_storage.remove_regular_post_receipt(
        receipt,
        REGULAR_POST_RECEIPT_FILE=REGULAR_POST_RECEIPT_FILE,
        log=log,
        retire_current_source_receipt=functools.partial(retire_current_source_receipt, commit_proof=commit_proof),
    )


def write_meme_post_receipt(receipt: dict) -> None:
    """Write meme post receipt."""
    return _main_post_receipt_storage.write_meme_post_receipt(
        receipt,
        MEME_POST_RECEIPT_FILE=MEME_POST_RECEIPT_FILE,
        REGULAR_POST_RECEIPT_FILE=REGULAR_POST_RECEIPT_FILE,
        UnresolvedMemePostReceipt=UnresolvedMemePostReceipt,
        UnresolvedRegularPostReceipt=UnresolvedRegularPostReceipt,
        atomic_write_json=atomic_write_json,
        confirmed_pending_schedule_receipt_is_semantically_valid=confirmed_pending_schedule_receipt_is_semantically_valid,
        confirmed_receipt_matches_main_attempt=confirmed_receipt_matches_main_attempt,
        durable_create_receipt_json=durable_create_receipt_json,
        load_meme_post_receipt=load_meme_post_receipt,
        log=log,
        materialize_bound_meme_schedule_receipt=materialize_bound_meme_schedule_receipt,
        meme_post_receipt_is_semantically_valid=meme_post_receipt_is_semantically_valid,
        receipt_namespace_entry_exists=receipt_namespace_entry_exists,
        remote_receipt_retirement_is_blocking=remote_receipt_retirement_is_blocking,
    )


def meme_post_receipt_is_semantically_valid(data: dict) -> bool:
    """Return whether a meme-post receipt is internally consistent."""
    return _main_post_receipts.meme_post_receipt_is_semantically_valid(
        data,
        MEME_SCHEDULE_MODES=MEME_SCHEDULE_MODES,
        MEME_SCHEDULE_VERSION=MEME_SCHEDULE_VERSION,
        confirmed_pending_schedule_receipt_is_semantically_valid=confirmed_pending_schedule_receipt_is_semantically_valid,
        main_post_attempt_is_semantically_valid=main_post_attempt_is_semantically_valid,
        materialize_bound_meme_schedule_receipt=materialize_bound_meme_schedule_receipt,
        valid_receipt_epoch=valid_receipt_epoch,
    )


def load_meme_post_receipt() -> tuple[str, dict | None]:
    """Load meme post receipt."""
    return _main_post_receipt_storage.load_meme_post_receipt(
        MEME_POST_RECEIPT_FILE=MEME_POST_RECEIPT_FILE,
        confirmed_pending_schedule_receipt_is_semantically_valid=confirmed_pending_schedule_receipt_is_semantically_valid,
        load_receipt_json_no_follow=load_receipt_json_no_follow,
        log=log,
        main_post_attempt_is_semantically_valid=main_post_attempt_is_semantically_valid,
        meme_post_receipt_is_semantically_valid=meme_post_receipt_is_semantically_valid,
    )


def remove_meme_post_receipt(receipt: dict, *, commit_proof=None) -> None:
    """Retire one exact reconciled meme-post receipt."""
    from mrs_bot_state_generation import require_commit_proof
    require_commit_proof(commit_proof)
    commit_proof.require_receipt(receipt)
    return _main_post_receipt_storage.remove_meme_post_receipt(
        receipt,
        MEME_POST_RECEIPT_FILE=MEME_POST_RECEIPT_FILE,
        log=log,
        retire_current_source_receipt=functools.partial(retire_current_source_receipt, commit_proof=commit_proof),
    )


def apply_meme_post_receipt(receipt: dict, state: dict) -> None:
    """Apply meme post receipt."""
    return _main_post_reconciliation.apply_meme_post_receipt(
        receipt,
        state,
        MEME_POST_TEXT=MEME_POST_TEXT,
        MEME_SCHEDULE_VERSION=MEME_SCHEDULE_VERSION,
        MY_USER_ID=MY_USER_ID,
        cache_tweet=cache_tweet,
        log=log,
        meme_schedule_date_str=meme_schedule_date_str,
        record_recent_own_post=record_recent_own_post,
    )


def reconcile_meme_post_receipt(state: dict) -> bool:
    """Reconcile a durable meme receipt without duplicating a remote post."""
    return _main_post_reconciliation.reconcile_meme_post_receipt(
        state,
        InvalidMemePostReceipt=InvalidMemePostReceipt,
        MEME_POST_RECEIPT_FILE=MEME_POST_RECEIPT_FILE,
        MEME_POST_TEXT=MEME_POST_TEXT,
        apply_meme_post_receipt=apply_meme_post_receipt,
        emit_account_root_posted=emit_account_root_posted,
        finalize_confirmed_pending_schedule_receipt=finalize_confirmed_pending_schedule_receipt,
        load_meme_post_receipt=load_meme_post_receipt,
        log=log,
        remove_meme_post_receipt=remove_meme_post_receipt,
        retire_lane_transport_journal_if_present=retire_lane_transport_journal_if_present,
        save_state=save_state,
        verify_lane_transport_source_lineage_if_present=verify_lane_transport_source_lineage_if_present,
    )


def apply_regular_post_receipt(receipt: dict, lines_used: set, images_used: set, state: dict) -> None:
    """Apply regular post receipt."""
    return _main_post_reconciliation.apply_regular_post_receipt(
        receipt,
        lines_used,
        images_used,
        state,
        MEME_SCHEDULE_VERSION=MEME_SCHEDULE_VERSION,
        MY_USER_ID=MY_USER_ID,
        cache_tweet=cache_tweet,
        log=log,
        maybe_schedule_meme_after_quote_post=maybe_schedule_meme_after_quote_post,
        meme_schedule_date_str=meme_schedule_date_str,
        record_recent_own_post=record_recent_own_post,
    )


def save_regular_post_protected_state(lines_used: set, images_used: set, state: dict, *, durable: bool) -> StateCommitProof:
    """Save regular post protected state."""
    return _main_post_confirmation_persistence.save_regular_post_protected_state(
        lines_used,
        images_used,
        state,
        durable=durable,
        IMAGES_USED_FILE=IMAGES_USED_FILE,
        LINES_USED_FILE=LINES_USED_FILE,
        save_image_used_basenames=save_image_used_basenames,
        save_quote_used_hashes=save_quote_used_hashes,
        save_state=save_state,
    )


def json_file_matches(path: Path, expected: object, *, commit_proof=None) -> bool:
    """Prove stable no-follow content and, for state, its exact sealed generation."""
    from mrs_bot_state_generation import canonical_bytes, generation_number, strict_document

    try:
        if commit_proof is not None:
            commit_proof.require_current()
        present, data = read_stable_owned_json_bytes_no_follow(path)
        if not present or data is None:
            return False
        comparison = (
            state_document_for_persistence(expected)
            if path == STATE_FILE and isinstance(expected, dict)
            else expected
        )
        document = strict_document(data)
        if path == STATE_FILE:
            if not generation_number(document):
                return False
            return data == canonical_bytes(comparison)
        return document == comparison
    except Exception:
        return False


def emergency_persist_confirmed_regular_post(lines_used: set, images_used: set, state: dict) -> RegularPostPersistenceResult:
    """Persist emergency confirmed-post effects and return exact restart authority."""
    return _main_post_confirmation_persistence.emergency_persist_confirmed_regular_post(
        lines_used,
        images_used,
        state,
        IMAGES_USED_FILE=IMAGES_USED_FILE,
        LINES_USED_FILE=LINES_USED_FILE,
        STATE_FILE=STATE_FILE,
        StateBackupWriteError=StateBackupWriteError,
        json_file_matches=json_file_matches,
        log=log,
        save_image_used_basenames=save_image_used_basenames,
        save_quote_used_hashes=save_quote_used_hashes,
        save_state=save_state,
    )


def confirmed_regular_emergency_representation_is_complete(
    *,
    post_id: str,
    post_epoch: int | None,
    quote_hash: str,
    image_basename: str,
    lines_used: set,
    images_used: set,
    state: dict,
    main_post_attempt: dict,
) -> bool:
    """Return whether fallback state exactly implements the pre-send plan."""
    return _main_post_reconciliation.confirmed_regular_emergency_representation_is_complete(
        post_id=post_id,
        post_epoch=post_epoch,
        quote_hash=quote_hash,
        image_basename=image_basename,
        lines_used=lines_used,
        images_used=images_used,
        state=state,
        main_post_attempt=main_post_attempt,
        build_confirmed_pending_schedule_receipt=build_confirmed_pending_schedule_receipt,
        materialize_bound_regular_schedule_receipt=materialize_bound_regular_schedule_receipt,
        valid_receipt_epoch=valid_receipt_epoch,
    )


def confirmed_meme_emergency_representation_is_complete(
    *,
    post_id: str,
    post_epoch: int | None,
    meme_basename: str,
    state: dict,
    main_post_attempt: dict,
) -> bool:
    """Return whether fallback state exactly implements the pre-send plan."""
    return _main_post_reconciliation.confirmed_meme_emergency_representation_is_complete(
        post_id=post_id,
        post_epoch=post_epoch,
        meme_basename=meme_basename,
        state=state,
        main_post_attempt=main_post_attempt,
        build_confirmed_pending_schedule_receipt=build_confirmed_pending_schedule_receipt,
        materialize_bound_meme_schedule_receipt=materialize_bound_meme_schedule_receipt,
        safe_bound_schedule_date_str=safe_bound_schedule_date_str,
        valid_receipt_epoch=valid_receipt_epoch,
    )


def maybe_post_historical_context_reply(
    *,
    quote_hash: str,
    quote_text: str,
    parent_post_id: str,
    dry_run: bool = False,
    on_source_receipt_published: Callable[[str, int], None] | None = None,
    on_remote_transaction_started: Callable[[], None] | None = None,
    on_definite_non_success: Callable[[BaseException], str] | None = None,
    on_confirmed_receipt: Callable[[dict, int], None] | None = None,
) -> dict:
    """Post an optional canonical context reply without affecting the main post."""
    return _historical_context_delivery.maybe_post_historical_context_reply(
        quote_hash=quote_hash,
        quote_text=quote_text,
        parent_post_id=parent_post_id,
        dry_run=dry_run,
        on_source_receipt_published=on_source_receipt_published,
        on_remote_transaction_started=on_remote_transaction_started,
        on_definite_non_success=on_definite_non_success,
        on_confirmed_receipt=on_confirmed_receipt,
        HISTORICAL_CONTEXT_RESEARCH_DIR=HISTORICAL_CONTEXT_RESEARCH_DIR,
        RemoteOperationsPaused=RemoteOperationsPaused,
        _HISTORICAL_CONTEXT_CORPUS_SNAPSHOT=_HISTORICAL_CONTEXT_CORPUS_SNAPSHOT,
        _HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON=_HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON,
        _HISTORICAL_CONTEXT_SEMANTIC_GATE=_HISTORICAL_CONTEXT_SEMANTIC_GATE,
        begin_confirmed_post_sigint_deferral=begin_confirmed_post_sigint_deferral,
        block_if_ambiguous_remote_post=block_if_ambiguous_remote_post,
        create_post=create_post,
        emit_historical_context_reply_posted=emit_historical_context_reply_posted,
        end_confirmed_post_sigint_deferral=end_confirmed_post_sigint_deferral,
        historical_context_reply=historical_context_reply,
        historical_context_reply_store=historical_context_reply_store,
        initialise_historical_context_semantic_gate=initialise_historical_context_semantic_gate,
        log=log,
        log_event=log_event,
        now_epoch=now_epoch,
        re=re,
    )


def historical_context_reply_store(*, allow_missing_history: bool = False):
    """Return the reply store with production history-loss protection."""
    return _historical_context_delivery.historical_context_reply_store(
        allow_missing_history=allow_missing_history,
        HISTORICAL_CONTEXT_REPLY_HISTORY_FILE=HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE=HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        TEST_MODE=TEST_MODE,
        latch_source_receipt_retirement_uncertainty=latch_source_receipt_retirement_uncertainty,
        transaction_mutation_authority=transaction_mutation_authority,
    )


def historical_context_outbox_store():
    """Return the durable store for auxiliary context-reply obligations."""
    return _historical_context_delivery.historical_context_outbox_store(
        HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE=HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE,
        TEST_MODE=TEST_MODE,
    )


def require_historical_context_outbox_writable() -> None:
    """Fail before a main X post when its context state cannot be persisted."""
    return _historical_context_runtime.require_historical_context_outbox_writable(
        _HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON=_HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON,
        _get_historical_context_outbox_unavailable_reason=_get_historical_context_outbox_unavailable_reason,
        _set_historical_context_outbox_unavailable_reason=_set_historical_context_outbox_unavailable_reason,
        historical_context_outbox_store=historical_context_outbox_store,
        historical_context_reply=historical_context_reply,
    )


def canonical_context_obligation_quote_id(quote_hash: str, quote_text: str) -> str:
    """Resolve a stable canonical packet identity without fuzzy matching."""
    return _historical_context_delivery.canonical_context_obligation_quote_id(
        quote_hash,
        quote_text,
        _HISTORICAL_CONTEXT_CORPUS_SNAPSHOT=_HISTORICAL_CONTEXT_CORPUS_SNAPSHOT,
    )


def _get_historical_context_outbox_unavailable_reason() -> str | None:
    """Return the current historical-context outbox failure reason."""
    return _HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON

def _set_historical_context_outbox_unavailable_reason(reason: str | None) -> None:
    """Set the shared historical-context outbox failure reason."""
    global _HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON
    _HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON = reason


def enqueue_historical_context_obligation(receipt: dict) -> dict:
    """Persist an optional context obligation before releasing a main receipt."""
    return _historical_context_queue.enqueue_historical_context_obligation(
        receipt,
        _HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON=_HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON,
        _set_historical_context_outbox_unavailable_reason=_set_historical_context_outbox_unavailable_reason,
        canonical_context_obligation_quote_id=canonical_context_obligation_quote_id,
        historical_context_outbox_store=historical_context_outbox_store,
        historical_context_reply=historical_context_reply,
        log=log,
        log_event=log_event,
    )


_record_context_outbox_failure = _historical_context_delivery._record_context_outbox_failure


def _record_or_verify_proved_context_failure(
    store,
    *,
    parent_post_id: str,
    attempt_number: int,
    error,
    failed_epoch: int,
) -> str:
    """Preserve or verify one exact proved-non-success outbox outcome."""
    return _historical_context_delivery._record_or_verify_proved_context_failure(
        store,
        parent_post_id=parent_post_id,
        attempt_number=attempt_number,
        error=error,
        failed_epoch=failed_epoch,
    )


def recover_interrupted_historical_context_attempt(
    store,
    obligation: dict,
    *,
    recovered_epoch: int,
    receipt_was_observed: bool = False,
) -> dict:
    """Resolve a durable interrupted claim without repeating its remote work."""
    return _historical_context_delivery.recover_interrupted_historical_context_attempt(
        store,
        obligation,
        recovered_epoch=recovered_epoch,
        receipt_was_observed=receipt_was_observed,
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE=HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        emit_historical_context_history_observation=emit_historical_context_history_observation,
        hashlib=hashlib,
        historical_context_reply_store=historical_context_reply_store,
        journal_path_for_receipt=journal_path_for_receipt,
        re=re,
        transport_journal_is_blocking=transport_journal_is_blocking,
    )


def _process_due_historical_context_obligations(
    *,
    store,
    parent_post_id: str | None = None,
    limit: int = 1,
    runtime_state: dict | None = None,
    historical_context_receipt_reconciliation_only: bool = False,
    historical_context_outbox_reconciliation_only: bool = False,
) -> list[dict]:
    """Retry only auxiliary context work; never invoke the main-post path."""
    return _historical_context_queue._process_due_historical_context_obligations(
        store=store,
        parent_post_id=parent_post_id,
        limit=limit,
        runtime_state=runtime_state,
        historical_context_receipt_reconciliation_only=historical_context_receipt_reconciliation_only,
        historical_context_outbox_reconciliation_only=historical_context_outbox_reconciliation_only,
        ApiError=ApiError,
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE=HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        _HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON=_HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON,
        _get_historical_context_outbox_unavailable_reason=_get_historical_context_outbox_unavailable_reason,
        _set_historical_context_outbox_unavailable_reason=_set_historical_context_outbox_unavailable_reason,
        api_error_is_reply_not_allowed=api_error_is_reply_not_allowed,
        historical_context_receipt_path_present_or_unsafe=historical_context_receipt_path_present_or_unsafe,
        in_api_cooldown=in_api_cooldown,
        inspect_transport_state=inspect_transport_state,
        journal_path_for_receipt=journal_path_for_receipt,
        lane_paused=lane_paused,
        log=log,
        log_event=log_event,
        maybe_post_historical_context_reply=maybe_post_historical_context_reply,
        now_epoch=now_epoch,
        record_ambiguous_remote_post=record_ambiguous_remote_post,
        record_api_error=record_api_error,
        recover_interrupted_historical_context_attempt=recover_interrupted_historical_context_attempt,
        save_state=save_state,
    )


def process_due_historical_context_obligations(
    *,
    parent_post_id: str | None = None,
    limit: int = 1,
    runtime_state: dict | None = None,
) -> list[dict]:
    """Serialise complete context attempts across claims and remote outcomes."""
    return _historical_context_delivery.process_due_historical_context_obligations(
        parent_post_id=parent_post_id,
        limit=limit,
        runtime_state=runtime_state,
        AmbiguousRemotePostOutcome=AmbiguousRemotePostOutcome,
        InvalidMemePostReceipt=InvalidMemePostReceipt,
        InvalidRegularPostReceipt=InvalidRegularPostReceipt,
        _process_due_historical_context_obligations=_process_due_historical_context_obligations,
        block_if_ambiguous_remote_post=block_if_ambiguous_remote_post,
        historical_context_outbox_remote_attempt_parent_for_local_reconciliation=historical_context_outbox_remote_attempt_parent_for_local_reconciliation,
        historical_context_outbox_store=historical_context_outbox_store,
        historical_context_receipt_parent_for_local_reconciliation=historical_context_receipt_parent_for_local_reconciliation,
        historical_context_receipt_path_present_or_unsafe=historical_context_receipt_path_present_or_unsafe,
        log=log,
    )


def safely_process_due_historical_context_obligations(
    *,
    parent_post_id: str | None = None,
    limit: int = 1,
    runtime_state: dict | None = None,
) -> list[dict]:
    """Isolate auxiliary context-worker faults from confirmed main-post lanes."""
    return _historical_context_queue.safely_process_due_historical_context_obligations(
        parent_post_id=parent_post_id,
        limit=limit,
        runtime_state=runtime_state,
        _set_historical_context_outbox_unavailable_reason=_set_historical_context_outbox_unavailable_reason,
        log=log,
        log_event=log_event,
        process_due_historical_context_obligations=process_due_historical_context_obligations,
    )


def ensure_reconciled_regular_receipt_schedule_is_future(
    receipt: dict,
    state: dict,
    current: int,
) -> bool:
    """Persist a future quote schedule before completing current-receipt replay."""
    return _transaction_recovery.ensure_reconciled_regular_receipt_schedule_is_future(
        receipt,
        state,
        current,
        log=log,
        schedule_next_quote_post=schedule_next_quote_post,
    )


def reconcile_regular_post_receipt(
    lines_used: set,
    images_used: set,
    state: dict,
    *,
    minimum_next_quote_epoch: int | None = None,
    process_auxiliary_context: bool = True,
) -> bool:
    """Reconcile a durable regular-post receipt without duplicating a remote post."""
    return _main_post_reconciliation.reconcile_regular_post_receipt(
        lines_used,
        images_used,
        state,
        minimum_next_quote_epoch=minimum_next_quote_epoch,
        process_auxiliary_context=process_auxiliary_context,
        InvalidRegularPostReceipt=InvalidRegularPostReceipt,
        REGULAR_POST_RECEIPT_FILE=REGULAR_POST_RECEIPT_FILE,
        apply_regular_post_receipt=apply_regular_post_receipt,
        emit_account_root_posted=emit_account_root_posted,
        enqueue_historical_context_obligation=enqueue_historical_context_obligation,
        ensure_reconciled_regular_receipt_schedule_is_future=ensure_reconciled_regular_receipt_schedule_is_future,
        finalize_confirmed_pending_schedule_receipt=finalize_confirmed_pending_schedule_receipt,
        load_regular_post_receipt=load_regular_post_receipt,
        log=log,
        remove_regular_post_receipt=remove_regular_post_receipt,
        retire_lane_transport_journal_if_present=retire_lane_transport_journal_if_present,
        safely_process_due_historical_context_obligations=safely_process_due_historical_context_obligations,
        save_regular_post_protected_state=save_regular_post_protected_state,
        verify_lane_transport_source_lineage_if_present=verify_lane_transport_source_lineage_if_present,
    )


def block_if_unresolved_regular_post_receipt() -> None:
    """Refuse a new regular post while a prior receipt is unresolved."""
    return _transaction_recovery.block_if_unresolved_regular_post_receipt(
        InvalidRegularPostReceipt=InvalidRegularPostReceipt,
        REGULAR_POST_RECEIPT_FILE=REGULAR_POST_RECEIPT_FILE,
        UnresolvedRegularPostReceipt=UnresolvedRegularPostReceipt,
        load_regular_post_receipt=load_regular_post_receipt,
    )


def both_main_post_receipts_exist() -> bool:
    """Return the both main post receipts exist."""
    return receipt_namespace_entry_exists(
        REGULAR_POST_RECEIPT_FILE
    ) and receipt_namespace_entry_exists(MEME_POST_RECEIPT_FILE)


def reconcile_main_post_receipts(
    lines_used: set,
    images_used: set,
    state: dict,
    *,
    minimum_next_quote_epoch: int | None = None,
    process_auxiliary_context: bool = True,
) -> dict[str, bool]:
    """Reconcile regular and meme receipts before any new main post."""
    return _main_post_reconciliation.reconcile_main_post_receipts(
        lines_used,
        images_used,
        state,
        minimum_next_quote_epoch=minimum_next_quote_epoch,
        process_auxiliary_context=process_auxiliary_context,
        InvalidRegularPostReceipt=InvalidRegularPostReceipt,
        MEME_POST_RECEIPT_FILE=MEME_POST_RECEIPT_FILE,
        REGULAR_POST_RECEIPT_FILE=REGULAR_POST_RECEIPT_FILE,
        both_main_post_receipts_exist=both_main_post_receipts_exist,
        log=log,
        reconcile_meme_post_receipt=reconcile_meme_post_receipt,
        reconcile_regular_post_receipt=reconcile_regular_post_receipt,
    )


def reconcile_startup_main_post_receipts(
    lines_used: set,
    images_used: set,
    state: dict,
    current: int,
) -> dict[str, bool]:
    """Reconcile main receipts unless a global maintenance pause is active."""
    return _transaction_recovery.reconcile_startup_main_post_receipts(
        lines_used,
        images_used,
        state,
        current,
        global_remote_writes_paused=global_remote_writes_paused,
        log=log,
        reconcile_main_post_receipts=reconcile_main_post_receipts,
    )


def reconcile_confirmed_transactions_before_global_barrier(
    lines_used: set,
    images_used: set,
    state: dict,
    current: int | None = None,
) -> dict[str, bool]:
    """Finish one exact locally recoverable transaction before global blocking.

    Transport journals are intentionally global barriers, but a confirmed
    journal plus its confirmed lane receipt is also enough local authority to
    finish state persistence without another remote request.  This narrow
    pre-barrier reconciler promotes a sending/attempting source only when its
    exact confirmed journal proves the remote identity.  It may also consume an
    exact historical-context definite-failure pair through the matching outbox
    attempt.  It never runs auxiliary remote work and refuses to choose between
    multiple lane receipts.
    """
    return _transaction_recovery.reconcile_confirmed_transactions_before_global_barrier(
        lines_used,
        images_used,
        state,
        current,
        CONFIRMED_REPLY_RECEIPT_FILE=CONFIRMED_REPLY_RECEIPT_FILE,
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE=HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        MEME_POST_RECEIPT_FILE=MEME_POST_RECEIPT_FILE,
        REGULAR_POST_RECEIPT_FILE=REGULAR_POST_RECEIPT_FILE,
        TRANSPORT_SOURCE_VALIDATOR_ID=TRANSPORT_SOURCE_VALIDATOR_ID,
        TransportJournalError=TransportJournalError,
        _legacy_conversational_transport_source_semantic_validator=_legacy_conversational_transport_source_semantic_validator,
        _promote_legacy_sending_reply_receipt_from_confirmed_transport=_promote_legacy_sending_reply_receipt_from_confirmed_transport,
        _reply_confirmation_epoch_after_remote_success=_reply_confirmation_epoch_after_remote_success,
        bind_confirmed_transport_source=bind_confirmed_transport_source,
        confirmation_epoch_for_main_attempt=confirmation_epoch_for_main_attempt,
        emit_historical_context_store_observation=emit_historical_context_store_observation,
        global_remote_writes_paused=global_remote_writes_paused,
        hashlib=hashlib,
        historical_context_outbox_remote_attempt_parent_for_local_reconciliation=historical_context_outbox_remote_attempt_parent_for_local_reconciliation,
        historical_context_outbox_store=historical_context_outbox_store,
        historical_context_reply_store=historical_context_reply_store,
        inspect_confirmed_transport_transaction=inspect_confirmed_transport_transaction,
        inspect_transport_state=inspect_transport_state,
        journal_path_for_receipt=journal_path_for_receipt,
        load_confirmed_reply_receipt=load_confirmed_reply_receipt,
        load_meme_post_receipt=load_meme_post_receipt,
        load_regular_post_receipt=load_regular_post_receipt,
        log_event=log_event,
        now_epoch=now_epoch,
        promote_main_post_attempt_to_confirmed_pending_schedule=promote_main_post_attempt_to_confirmed_pending_schedule,
        promote_sending_reply_receipt=promote_sending_reply_receipt,
        receipt_namespace_entry_exists=receipt_namespace_entry_exists,
        reconcile_confirmed_reply_receipt=reconcile_confirmed_reply_receipt,
        reconcile_main_post_receipts=reconcile_main_post_receipts,
        recover_interrupted_historical_context_attempt=recover_interrupted_historical_context_attempt,
        remote_write_safety_incident_is_latched=remote_write_safety_incident_is_latched,
        remote_write_safety_marker_path_present_or_unsafe=remote_write_safety_marker_path_present_or_unsafe,
        remote_write_safety_protocol_is_active=remote_write_safety_protocol_is_active,
        transport_source_semantic_validator=transport_source_semantic_validator,
    )


def image_used_history_has_legacy_indices(images_used: set) -> bool:
    """Return whether image used history has legacy indices."""
    return _used_history_owner().image_used_history_has_legacy_indices(images_used)


def image_corpus_verified_for_legacy_migration(images: list[str], image_analysis: dict | None) -> bool:
    """Return the image corpus verified for legacy migration."""
    return _used_history_owner().image_corpus_verified_for_legacy_migration(images, image_analysis)


def normalise_image_used_basenames(images_used: set, images: list[str], image_analysis: dict | None = None) -> tuple[set, bool]:
    """Normalise image used basenames."""
    return _used_history_owner().normalise_image_used_basenames(images_used, images, image_analysis)


def load_image_used_basenames(images: list[str]) -> set:
    """Load image used basenames."""
    return _used_history_owner().load_image_used_basenames(images)


# Keep the public helper API here; resolve runtime token and phrase policy
# at each call while fixed tag transformations use their owner directly.
normalise_tag = _image_scoring.normalise_tag


as_string_list = _image_scoring.as_string_list


def meaningful_tokens(value: object) -> set[str]:
    """Return the meaningful tokens."""
    return _image_scoring.meaningful_tokens(
        value,
        token_stopwords=TOKEN_STOPWORDS,
    )


def phrase_matches_text(phrase: str, text: str) -> bool:
    """Return whether phrase matches text."""
    return _image_scoring.phrase_matches_text(
        phrase, text, meaningful_tokens=meaningful_tokens,
    )


def hard_mismatch_tokens(value: object) -> set[str]:
    """Return the hard mismatch tokens."""
    return _image_scoring.hard_mismatch_tokens(
        value, meaningful_tokens=meaningful_tokens,
    )


def hard_mismatch_phrase_matches_text(phrase: str, text: str) -> bool:
    """Return whether hard mismatch phrase matches text."""
    return _image_scoring.hard_mismatch_phrase_matches_text(
        phrase, text, hard_mismatch_tokens=hard_mismatch_tokens,
    )


def image_text_corpus(image_analysis: dict) -> str:
    """Return the image text corpus."""
    return _image_scoring.image_text_corpus(image_analysis)


def build_image_topic_idf(image_analysis: dict | None) -> dict[str, float]:
    """Build image topic idf."""
    return _image_scoring.build_image_topic_idf(
        image_analysis,
    )


visual_energy_score = _image_scoring.visual_energy_score


def image_is_out_of_season(image_analysis: dict, today_mm_dd: str) -> bool:
    """Return whether image is out of season."""
    return _image_scoring.image_is_out_of_season(
        image_analysis, today_mm_dd,
    )


def score_image_for_quote(quote_analysis: dict | None, image_analysis: dict | None, idf: dict[str, float] | None = None) -> tuple[float, dict[str, float], bool]:
    """Calculate the production image score and component breakdown for a quotation."""
    return _image_scoring.score_image_for_quote(
        quote_analysis, image_analysis, idf,
        phrase_matches_text=phrase_matches_text,
        hard_mismatch_phrase_matches_text=hard_mismatch_phrase_matches_text,
        strong_mismatch_penalty=IMAGE_STRONG_MISMATCH_PENALTY,
    )


ORIGINAL_EDITORIAL_ANALYSIS_KIND = "original_editorial_experiment"
ORIGINAL_EDITORIAL_SCHEMA_VERSION = 1
ORIGINAL_EDITORIAL_DIMENSIONS = [
    "leadership",
    "conviction",
    "authority",
    "defiance",
    "warning",
    "optimism",
    "patriotism",
    "statesmanship",
    "economic_seriousness",
    "human_warmth",
    "ceremony_formality",
    "historical_iconicity",
]
ORIGINAL_EDITORIAL_AFFINITY_CONCEPTS = {
    "ceremony",
    "defiance",
    "duty",
    "economic",
    "enterprise",
    "family",
    "freedom",
    "humour",
    "national_identity",
    "patriotism",
    "public_service",
    "responsibility",
    "socialism",
    "victory",
    "warning",
}
ORIGINAL_EDITORIAL_SYNONYMS = {
    "ceremony": {"ceremony", "ceremonial", "ceremony_formality", "formal_ceremony"},
    "defiance": {"defiance", "defiant", "political_struggle", "resistance", "confrontation"},
    "duty": {"duty", "obligation", "responsibility"},
    "economic": {"economic", "economic_seriousness", "economy", "fiscal", "finance", "markets"},
    "enterprise": {"enterprise", "capitalism", "free_enterprise", "business"},
    "family": {"family", "home", "children", "domestic_life"},
    "freedom": {"freedom", "liberty", "individual_liberty"},
    "humour": {"humour", "humor", "light_humour", "wit"},
    "national_identity": {"national_identity", "nation", "britain", "britishness", "national_direction"},
    "patriotism": {"patriotism", "national_pride"},
    "public_service": {"public_service", "service", "civic_duty"},
    "socialism": {"socialism", "communism", "collectivism"},
    "victory": {"victory", "triumph", "winning"},
    "warning": {"warning", "danger", "caution", "grave_warning"},
}
_ORIGINAL_EDITORIAL_SYNONYM_TO_CONCEPT = {
    normalise_tag(value): concept
    for concept, values in ORIGINAL_EDITORIAL_SYNONYMS.items()
    for value in values | {concept}
}
_ORIGINAL_EDITORIAL_ANALYSIS_CACHE: dict[str, dict] = {}


original_editorial_numeric = _original_editorial.original_editorial_numeric


def _original_editorial_owner() -> _original_editorial.OriginalEditorial:
    """Bind current external boundaries without runtime work or caller state."""
    return _original_editorial.OriginalEditorial(
        normalise_tag=normalise_tag,
        synonym_to_concept=_ORIGINAL_EDITORIAL_SYNONYM_TO_CONCEPT,
        affinity_concepts=ORIGINAL_EDITORIAL_AFFINITY_CONCEPTS,
        as_string_list=as_string_list,
        dimensions=ORIGINAL_EDITORIAL_DIMENSIONS,
        generated_origin=generated_image_origin_quote_hash,
        image_sha256=current_image_sha256,
        analysis_file=ORIGINAL_EDITORIAL_ANALYSIS_FILE,
        analysis_cache=_ORIGINAL_EDITORIAL_ANALYSIS_CACHE,
        analysis_kind=ORIGINAL_EDITORIAL_ANALYSIS_KIND,
        schema_version=ORIGINAL_EDITORIAL_SCHEMA_VERSION,
        image_paths=current_image_paths,
        enabled=ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING,
        default_weight=ORIGINAL_EDITORIAL_SHADOW_WEIGHT,
        default_max_abs_adjustment=ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT,
        log=log,
    )


def original_editorial_concepts(value: object) -> set[str]:
    """Return the original editorial concepts."""
    return _original_editorial_owner().concepts(value)


def original_editorial_quote_concepts(quote_analysis: dict | None) -> set[str]:
    """Return the original editorial quote concepts."""
    return _original_editorial_owner().quote_concepts(quote_analysis)


def original_editorial_image_concepts(editorial: dict | None) -> set[str]:
    """Return the original editorial image concepts."""
    return _original_editorial_owner().image_concepts(editorial)


def original_editorial_avoid_concepts(editorial: dict | None) -> set[str]:
    """Return the original editorial avoid concepts."""
    return _original_editorial_owner().avoid_concepts(editorial)


def original_editorial_quote_dimension_profile(quote_analysis: dict | None) -> dict[str, float]:
    """Return the original editorial quote dimension profile."""
    return _original_editorial_owner().quote_profile(quote_analysis)


def validate_original_editorial_item(basename: str, entry: dict, image_by_name: dict[str, str]) -> dict:
    """Validate original editorial item."""
    return _original_editorial_owner().validate_item(basename, entry, image_by_name)


def load_original_editorial_analysis() -> dict[str, dict]:
    """Load original editorial analysis."""
    return _original_editorial_owner().load()


def validate_original_editorial_shadow_startup() -> None:
    """Validate original editorial shadow startup."""
    return _original_editorial_owner().validate_startup()


def original_editorial_shadow_score(
    quote_analysis: dict | None,
    editorial: dict | None,
    *,
    weight: float | None = None,
    max_abs_adjustment: float | None = None,
) -> tuple[float, dict]:
    """Calculate the observational editorial adjustment for one image."""
    return _original_editorial_owner().score(
        quote_analysis,
        editorial,
        weight=weight,
        max_abs_adjustment=max_abs_adjustment,
    )


def original_editorial_shadow_result(
    quote_choice: dict,
    production_choice: dict,
    scored_candidates: list[dict],
    *,
    selection_phase: str,
) -> tuple[dict | None, dict | None]:
    """Return the existing editorial comparison and its preferred original."""
    return _original_editorial_owner().compare(
        quote_choice,
        production_choice,
        scored_candidates,
        selection_phase=selection_phase,
    )


def log_original_editorial_shadow_result(
    quote_choice: dict,
    production_choice: dict,
    scored_candidates: list[dict],
    *,
    selection_phase: str,
    comparison: tuple[dict | None, dict | None] | None = None,
) -> None:
    """Log an original-editorial comparison without mutating candidates."""
    return _original_editorial_owner().log_comparison(
        quote_choice,
        production_choice,
        scored_candidates,
        selection_phase=selection_phase,
        comparison=comparison,
    )


def apply_original_editorial_selection(
    quote_choice: dict,
    baseline_choice: dict,
    scored_candidates: list[dict],
    *,
    selection_phase: str,
    comparison: tuple[dict | None, dict | None] | None = None,
) -> dict:
    """Replace an original baseline winner with the existing editorial winner."""
    return _original_editorial_owner().apply_selection(
        quote_choice,
        baseline_choice,
        scored_candidates,
        selection_phase=selection_phase,
        comparison=comparison,
    )


def concise_components(components: dict[str, float]) -> str:
    """Return the concise components."""
    return ", ".join(f"{key}={value:.1f}" for key, value in sorted(components.items()))


def build_quote_candidates(
    lines: list[str],
    available_lines: list[int],
    quote_analysis: dict | None,
    today_mm_dd: str,
    *,
    excluded_quote_hashes: set[str] | None = None,
) -> tuple[list[dict], int, int]:
    """Build analysed, research-eligible quotation candidates for a date."""
    return _quote_candidates_owner().build(
        lines,
        available_lines,
        quote_analysis,
        today_mm_dd,
        excluded_quote_hashes=excluded_quote_hashes,
    )


def load_quote_lines_and_analysis() -> tuple[list[str], dict | None, str]:
    """Load the active quotation source and validated analysis metadata."""
    return _quote_candidates_owner().load_source()


def load_completed_research_quote_hashes() -> set[str]:
    """Load the exact hash-bound ordinary-post eligibility partition."""
    return _quote_candidates.load_completed_research_quote_hashes(
        historical_context_research_dir=HISTORICAL_CONTEXT_RESEARCH_DIR,
        runtime_eligible_quote_manifest_file=RUNTIME_ELIGIBLE_QUOTE_MANIFEST_FILE,
        lines_file=LINES_FILE,
        completed_quote_research_file=COMPLETED_QUOTE_RESEARCH_FILE,
        load_json_object=load_json_object,
        file_sha256=file_sha256,
        quote_text_hash=quote_text_hash,
    )


def completed_research_quote_hashes() -> set[str]:
    """Return current attribution-eligible completed quotation hashes."""
    return _quote_candidates_owner().completed()


def quote_candidates_for_current_cycle(
    lines_used: set, *, excluded_quote_hashes: set[str] | None = None,
    allow_cycle_reset: bool = True,
) -> list[dict]:
    """Build unused candidates; disable cycle resets during image-pair retries."""
    return _quote_candidates_owner().for_cycle(
        lines_used,
        excluded_quote_hashes=excluded_quote_hashes,
        allow_cycle_reset=allow_cycle_reset,
    )


def select_quote_candidate(candidates: list[dict]) -> dict:
    """Select quote candidate."""
    return _quote_candidates_owner().select(candidates)


def choose_unused_line_candidate(
    lines_used: set, *, excluded_quote_hashes: set[str] | None = None,
    allow_cycle_reset: bool = True,
) -> dict:
    """Select an unused quotation, optionally preserving history during retries."""
    return _quote_candidates_owner().choose(
        lines_used,
        excluded_quote_hashes=excluded_quote_hashes,
        allow_cycle_reset=allow_cycle_reset,
    )


def _image_selection_owner() -> _image_selection.ImageSelection:
    """Bind current external boundaries without runtime work or caller state."""
    return _image_selection.ImageSelection(
        NoEligibleImageForQuote=NoEligibleImageForQuote,
        log=log,
        current_image_paths=current_image_paths,
        load_image_analysis=load_image_analysis,
        normalise_image_used_basenames=normalise_image_used_basenames,
        save_image_used_basenames=save_image_used_basenames,
        image_used_history_has_legacy_indices=image_used_history_has_legacy_indices,
        current_datetime=current_datetime,
        build_image_topic_idf=build_image_topic_idf,
        image_metadata_for_basename=image_metadata_for_basename,
        image_is_out_of_season=image_is_out_of_season,
        score_image_for_quote=score_image_for_quote,
        original_editorial_enabled=ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING,
        original_editorial_shadow_result=original_editorial_shadow_result,
        apply_original_editorial_selection=apply_original_editorial_selection,
        concise_components=concise_components,
        log_original_editorial_shadow_result=log_original_editorial_shadow_result,
        image_glob=IMAGE_GLOB,
        images_used_file=IMAGES_USED_FILE,
        UnsafeImageHistoryMigration=UnsafeImageHistoryMigration,
        GlobalImageUnavailable=GlobalImageUnavailable,
        StaleImageMetadata=StaleImageMetadata,
        QuoteSpecificImageMismatch=QuoteSpecificImageMismatch,
    )


def available_currently_eligible_image_basenames(
    eligible_basenames: set[str],
    images_used: set[str],
    state: dict | None = None,
) -> tuple[list[str], bool]:
    """Return whether available currently eligible image basenames."""
    return _image_selection_owner().available_basenames(eligible_basenames, images_used, state)


def current_image_sha256(path: str) -> str:
    """Return the current image SHA-256."""
    return file_sha256(Path(path))


def image_metadata_for_basename(image_analysis: dict | None, basename: str, path: str | None = None) -> tuple[str | None, dict | None]:
    """Return the image metadata for basename."""
    return _asset_metadata_owner().image_for_basename(image_analysis, basename, path)


generated_image_origin_quote_hash = _asset_metadata.generated_image_origin_quote_hash


def log_regular_image_selection(choice: dict) -> None:
    """Log regular image selection."""
    return _image_selection_owner().log_choice(choice)


def choose_matched_unused_image(
    images_used: set,
    quote_choice: dict,
    state: dict,
    *,
    force_cycle_reset: bool = False,
    avoid_last_image_at_cycle_boundary: bool = True,
    cycle_boundary_exclusions: set[str] | None = None,
    selection_phase: str = "normal",
) -> dict:
    """Select the highest-scoring eligible unused image for a quotation."""
    return _image_selection_owner().choose_matched(
        images_used,
        quote_choice,
        state,
        force_cycle_reset=force_cycle_reset,
        avoid_last_image_at_cycle_boundary=avoid_last_image_at_cycle_boundary,
        cycle_boundary_exclusions=cycle_boundary_exclusions,
        selection_phase=selection_phase,
    )


def choose_regular_quote_image_pair(
    lines_used: set,
    images_used: set,
    state: dict,
    *,
    force_image_cycle_reset: bool = False,
    avoid_last_image_at_cycle_boundary: bool = True,
    excluded_quote_hashes: set[str] | None = None,
) -> tuple[dict, dict, int]:
    """Select a production quotation-image pair under current cycle rules."""
    return _image_selection.choose_regular_quote_image_pair(
        lines_used,
        images_used,
        state,
        force_image_cycle_reset=force_image_cycle_reset,
        avoid_last_image_at_cycle_boundary=avoid_last_image_at_cycle_boundary,
        excluded_quote_hashes=excluded_quote_hashes,
        choose_unused_line_candidate=choose_unused_line_candidate,
        choose_matched_unused_image=choose_matched_unused_image,
        max_quote_image_pair_attempts=MAX_QUOTE_IMAGE_PAIR_ATTEMPTS,
        QuoteSpecificImageMismatch=QuoteSpecificImageMismatch,
        NoViableQuoteImagePair=NoViableQuoteImagePair,
        log=log,
    )


def post_random_quote(lines_used: set, images_used: set, state: dict) -> None:
    """Post a quotation pair through the owner with current root dependencies."""
    return _quote_posting.post_random_quote(
        lines_used, images_used, state,
        log=log,
        block_if_ambiguous_remote_post=block_if_ambiguous_remote_post,
        now_epoch=now_epoch,
        reconcile_main_post_receipts=reconcile_main_post_receipts,
        ConfirmedPostSigintDeferral=ConfirmedPostSigintDeferral,
        require_historical_context_outbox_writable=require_historical_context_outbox_writable,
        quote_used_history_has_legacy_indices=quote_used_history_has_legacy_indices,
        CorruptUsedHistoryError=CorruptUsedHistoryError,
        choose_regular_quote_image_pair=choose_regular_quote_image_pair,
        NoViableQuoteImagePair=NoViableQuoteImagePair,
        POST_SLEEP_MIN=POST_SLEEP_MIN,
        POST_SLEEP_MAX=POST_SLEEP_MAX,
        MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS=MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS,
        MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS=MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS,
        ENABLE_DAILY_MEME_POSTS=ENABLE_DAILY_MEME_POSTS,
        upload_media=upload_media,
        build_main_post_attempt=build_main_post_attempt,
        MEME_TRIGGER_AFTER_HOUR=MEME_TRIGGER_AFTER_HOUR,
        MEME_SCHEDULE_VERSION=MEME_SCHEDULE_VERSION,
        MAIN_POST_SCHEDULE_TIMEZONE=MAIN_POST_SCHEDULE_TIMEZONE,
        bound_meme_schedule_state=bound_meme_schedule_state,
        write_main_post_attempt=write_main_post_attempt,
        prepare_main_tweet_transport=prepare_main_tweet_transport,
        handoff_confirmed_media_upload_to_main_attempt=handoff_confirmed_media_upload_to_main_attempt,
        begin_confirmed_post_sigint_deferral=begin_confirmed_post_sigint_deferral,
        create_post=create_post,
        api_error_proves_remote_non_success=api_error_proves_remote_non_success,
        remove_main_post_attempt=remove_main_post_attempt,
        end_confirmed_post_sigint_deferral=end_confirmed_post_sigint_deferral,
        AmbiguousRemotePostOutcome=AmbiguousRemotePostOutcome,
        remote_write_safety_incident_is_latched=remote_write_safety_incident_is_latched,
        durable_remote_write_safety_barrier_exists=durable_remote_write_safety_barrier_exists,
        retain_sigint_deferral_without_durable_barrier=retain_sigint_deferral_without_durable_barrier,
        inspect_confirmed_transport_transaction=inspect_confirmed_transport_transaction,
        journal_path_for_receipt=journal_path_for_receipt,
        REGULAR_POST_RECEIPT_FILE=REGULAR_POST_RECEIPT_FILE,
        confirmation_epoch_for_main_attempt=confirmation_epoch_for_main_attempt,
        build_confirmed_pending_schedule_receipt=build_confirmed_pending_schedule_receipt,
        promote_main_post_attempt_to_confirmed_pending_schedule=promote_main_post_attempt_to_confirmed_pending_schedule,
        finalize_confirmed_pending_schedule_receipt=finalize_confirmed_pending_schedule_receipt,
        ConfirmedPendingScheduleDurabilityUncertain=ConfirmedPendingScheduleDurabilityUncertain,
        ConfirmedPostLocalPersistenceError=ConfirmedPostLocalPersistenceError,
        materialize_bound_regular_schedule_receipt=materialize_bound_regular_schedule_receipt,
        cache_tweet=cache_tweet,
        MY_USER_ID=MY_USER_ID,
        record_recent_own_post=record_recent_own_post,
        emergency_persist_confirmed_regular_post=emergency_persist_confirmed_regular_post,
        confirmed_regular_emergency_representation_is_complete=confirmed_regular_emergency_representation_is_complete,
        latch_confirmed_post_persistence_failure=latch_confirmed_post_persistence_failure,
        UnrecoverableConfirmedPostPersistenceError=UnrecoverableConfirmedPostPersistenceError,
        load_regular_post_receipt=load_regular_post_receipt,
        enqueue_historical_context_obligation=enqueue_historical_context_obligation,
        log_event=log_event,
        retire_lane_transport_journal_if_present=retire_lane_transport_journal_if_present,
        emit_account_root_posted=emit_account_root_posted,
        save_regular_post_protected_state=save_regular_post_protected_state,
        remove_regular_post_receipt=remove_regular_post_receipt,
        safely_process_due_historical_context_obligations=safely_process_due_historical_context_obligations,
    )


# ---------------------------------------------------------------------
# Daily meme posting
# ---------------------------------------------------------------------

def load_meme_analysis_index() -> dict[str, dict]:
    """Load meme analysis index."""
    return _asset_metadata_owner().load_meme_index()


original_meme_filename = _daily_meme.original_meme_filename


def _meme_catalog_owner() -> _daily_meme.MemeCatalog:
    """Bind current external boundaries without runtime work or caller state."""
    return _daily_meme.MemeCatalog(
        log=log,
        directory=MEME_DIR,
        reset_when_exhausted=RESET_MEME_CYCLE_WHEN_ALL_POSTED,
        save_state=save_state,
    )


def build_meme_cache_summary(shortlist_path: Path, analysis_index: dict[str, dict]) -> str:
    """Build the meme cache summary through current root helpers."""
    return _meme_catalog_owner().summary(shortlist_path, analysis_index)


def list_meme_candidates() -> list[Path]:
    """List the current meme catalog through the owner."""
    return _meme_catalog_owner().candidates()


def choose_next_meme(state: dict) -> Path | None:
    """Select a meme, saving any cycle reset through root authority."""
    return _meme_catalog_owner().choose(state)


def epoch_date_str(epoch: int | None = None) -> str:
    """Return the epoch date str."""
    return _receipt_dates_owner().local_date(epoch)


def reply_cap_date_str(epoch: int | None = None) -> str:
    """Return the conversational daily-cap date in Europe/London."""
    return _receipt_dates_owner().reply_cap_date(epoch)


def _meme_schedule_owner() -> _daily_meme.MemeSchedule:
    """Bind current calendar and persistence boundaries without runtime work."""
    return _daily_meme.MemeSchedule(
        bound_datetime=bound_schedule_datetime,
        timezone=MAIN_POST_SCHEDULE_TIMEZONE,
        now_epoch=now_epoch,
        fallback_hour=MEME_FALLBACK_HOUR,
        fallback_minute=MEME_FALLBACK_MINUTE,
        version=MEME_SCHEDULE_VERSION,
        modes=MEME_SCHEDULE_MODES,
        enabled=ENABLE_DAILY_MEME_POSTS,
        trigger_hour=MEME_TRIGGER_AFTER_HOUR,
        minimum_delay=MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS,
        maximum_delay=MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS,
        save_state=save_state,
        log=log,
    )


def meme_schedule_datetime(epoch: int) -> datetime:
    """Interpret a meme epoch through the current production-zone helper."""
    return _meme_schedule_owner().datetime(epoch)


def meme_schedule_date_str(epoch: int | None = None) -> str:
    """Return the meme calendar date through current root helpers."""
    return _meme_schedule_owner().date_str(epoch)


def meme_posted_on_date(state: dict, date_text: str) -> bool:
    """Check the daily meme guard through the current date helper."""
    return _meme_schedule_owner().posted_on_date(state, date_text)


def next_meme_fallback_epoch(state: dict, from_epoch: int | None = None) -> int:
    """Calculate the next fallback through current calendar settings."""
    return _meme_schedule_owner().next_fallback_epoch(state, from_epoch)


def next_meme_schedule_fields(state: dict, from_epoch: int | None = None, mode: str = "fallback") -> dict:
    """Build fallback schedule fields through current root helpers."""
    return _meme_schedule_owner().next_fields(state, from_epoch, mode)


def meme_delay_schedule_fields(epoch: int, mode: str) -> dict:
    """Build delayed schedule fields through current modes and helpers."""
    return _meme_schedule_owner().delay_fields(epoch, mode)


def set_meme_delay_schedule(state: dict, *, epoch: int, mode: str, save: bool = True) -> None:
    """Apply a meme delay and optionally save through root authority."""
    return _meme_schedule_owner().set_delay(state, epoch=epoch, mode=mode, save=save)


apply_state_fields = _runtime_state_helpers.apply_state_fields


def schedule_next_meme_post(state: dict, from_epoch: int | None = None, mode: str = "fallback", *, save: bool = True) -> None:
    """Schedule the fallback and optionally save through root authority."""
    return _meme_schedule_owner().schedule_next(state, from_epoch, mode, save=save)


def ensure_meme_schedule_initialized(state: dict) -> None:
    """Initialise or migrate the meme schedule through root authority."""
    return _meme_schedule_owner().ensure_initialized(state)


def meme_schedule_fields_after_quote_post(state: dict, quote_post_epoch: int | None = None, *, delay: int | None = None) -> dict:
    """Build quote-anchored meme fields through current root settings."""
    return _meme_schedule_owner().fields_after_quote(state, quote_post_epoch, delay=delay)


def maybe_schedule_meme_after_quote_post(state: dict, quote_post_epoch: int | None = None, *, save: bool = True) -> None:
    """Apply quote-anchored meme scheduling and optionally save state."""
    return _meme_schedule_owner().maybe_after_quote(state, quote_post_epoch, save=save)


def run_daily_meme_stage(stage: str, operation):
    """Run a meme stage with current logging and original exceptions."""
    return _daily_meme.run_daily_meme_stage(
        stage, operation,
        log=log,
        log_event=log_event,
    )


def require_valid_meme_post_id(posted_id: object) -> None:
    """Validate a meme post identity through the current root callback."""
    return _daily_meme.require_valid_meme_post_id(
        posted_id,
        valid_post_id=valid_post_id,
    )


def post_next_meme(state: dict) -> None:
    """Select and post a daily meme through the owner with root authority."""
    return _daily_meme.post_next_meme(
        state,
        log=log,
        run_daily_meme_stage=run_daily_meme_stage,
        block_if_ambiguous_remote_post=block_if_ambiguous_remote_post,
        both_main_post_receipts_exist=both_main_post_receipts_exist,
        REGULAR_POST_RECEIPT_FILE=REGULAR_POST_RECEIPT_FILE,
        MEME_POST_RECEIPT_FILE=MEME_POST_RECEIPT_FILE,
        InvalidMemePostReceipt=InvalidMemePostReceipt,
        reconcile_meme_post_receipt=reconcile_meme_post_receipt,
        now_epoch=now_epoch,
        meme_posted_on_date=meme_posted_on_date,
        meme_schedule_date_str=meme_schedule_date_str,
        schedule_next_meme_post=schedule_next_meme_post,
        block_if_unresolved_regular_post_receipt=block_if_unresolved_regular_post_receipt,
        choose_next_meme=choose_next_meme,
        load_meme_analysis_index=load_meme_analysis_index,
        build_meme_cache_summary=build_meme_cache_summary,
        upload_media=upload_media,
        build_main_post_attempt=build_main_post_attempt,
        MEME_POST_TEXT=MEME_POST_TEXT,
        MEME_SCHEDULE_VERSION=MEME_SCHEDULE_VERSION,
        MEME_FALLBACK_HOUR=MEME_FALLBACK_HOUR,
        MEME_FALLBACK_MINUTE=MEME_FALLBACK_MINUTE,
        MAIN_POST_SCHEDULE_TIMEZONE=MAIN_POST_SCHEDULE_TIMEZONE,
        write_main_post_attempt=write_main_post_attempt,
        prepare_main_tweet_transport=prepare_main_tweet_transport,
        handoff_confirmed_media_upload_to_main_attempt=handoff_confirmed_media_upload_to_main_attempt,
        begin_confirmed_post_sigint_deferral=begin_confirmed_post_sigint_deferral,
        create_post=create_post,
        require_valid_meme_post_id=require_valid_meme_post_id,
        api_error_proves_remote_non_success=api_error_proves_remote_non_success,
        remove_main_post_attempt=remove_main_post_attempt,
        end_confirmed_post_sigint_deferral=end_confirmed_post_sigint_deferral,
        AmbiguousRemotePostOutcome=AmbiguousRemotePostOutcome,
        remote_write_safety_incident_is_latched=remote_write_safety_incident_is_latched,
        durable_remote_write_safety_barrier_exists=durable_remote_write_safety_barrier_exists,
        retain_sigint_deferral_without_durable_barrier=retain_sigint_deferral_without_durable_barrier,
        inspect_confirmed_transport_transaction=inspect_confirmed_transport_transaction,
        journal_path_for_receipt=journal_path_for_receipt,
        confirmation_epoch_for_main_attempt=confirmation_epoch_for_main_attempt,
        build_confirmed_pending_schedule_receipt=build_confirmed_pending_schedule_receipt,
        promote_main_post_attempt_to_confirmed_pending_schedule=promote_main_post_attempt_to_confirmed_pending_schedule,
        finalize_confirmed_pending_schedule_receipt=finalize_confirmed_pending_schedule_receipt,
        log_event=log_event,
        ConfirmedPendingScheduleDurabilityUncertain=ConfirmedPendingScheduleDurabilityUncertain,
        ConfirmedPostLocalPersistenceError=ConfirmedPostLocalPersistenceError,
        materialize_bound_meme_schedule_receipt=materialize_bound_meme_schedule_receipt,
        apply_state_fields=apply_state_fields,
        cache_tweet=cache_tweet,
        MY_USER_ID=MY_USER_ID,
        record_recent_own_post=record_recent_own_post,
        save_state=save_state,
        confirmed_meme_emergency_representation_is_complete=confirmed_meme_emergency_representation_is_complete,
        StateBackupWriteError=StateBackupWriteError,
        json_file_matches=json_file_matches,
        STATE_FILE=STATE_FILE,
        latch_confirmed_post_persistence_failure=latch_confirmed_post_persistence_failure,
        UnrecoverableConfirmedPostPersistenceError=UnrecoverableConfirmedPostPersistenceError,
        load_meme_post_receipt=load_meme_post_receipt,
        retire_lane_transport_journal_if_present=retire_lane_transport_journal_if_present,
        remove_meme_post_receipt=remove_meme_post_receipt,
        emit_account_root_posted=emit_account_root_posted,
    )


# ---------------------------------------------------------------------
# Reply generation
# ---------------------------------------------------------------------

SPAMMY_PATTERNS = [
    r"\bcrypto\b",
    r"\bairdrop\b",
    r"\bforex\b",
    r"\bonlyfans\b",
    r"\bporn\b",
    r"\btelegram\b",
    r"\bt\.me\b",
    r"\bmutual follow\b",
    r"\bfollow back\b",
    r"\bglobal city navigation\b",
    r"\breliable broker\b",
    r"\bmanual vetting\b",
    r"\bmanual screening\b",
    r"\bdocument verification\b",
    r"\bmaterial verification\b",
    r"\brisk control\b",
    r"\bplatform model resources\b",
    r"\bscammers\b",
    r"包养",
    r"外围",
    r"资源",
    r"模特",
    r"全国外围",
    r"同城",
    r"\bwhatsapp\b",
    r"\bdiscount code\b",
    r"\bgiveaway\b",
    r"\binvestment opportunity\b",
    r"\bguaranteed profit\b",
]

def is_probably_spam_or_not_worth_replying(text: str) -> bool:
    """Delegate reply-lane policy with current root dependencies."""
    return _reply_lane_policy.is_probably_spam_or_not_worth_replying(
        text,
        SPAMMY_PATTERNS=SPAMMY_PATTERNS,
        log=log,
        re=re,
    )


def _reply_draft_owner() -> _reply_drafts.ReplyDrafts:
    """Bind current draft dependencies without acquiring evidence or caller state."""
    return _reply_drafts.ReplyDrafts(
        validate_persisted_draft=validate_single_call_persisted_draft,
        evidence_repository=reply_evidence_repository,
        record_result=_record_single_call_result,
        log_event=log_event,
        log=log,
        strategy_version=SINGLE_CALL_STRATEGY_VERSION,
        model=SINGLE_CALL_MODEL,
        result_type=PipelineResult,
        reply_type=ValidatedReply,
        evidence_unavailable=ReplyEvidenceUnavailable,
        validation_error=ReplyValidationError,
    )


pending_ai_reply_draft_key = _reply_drafts.pending_ai_reply_draft_key


def validate_current_ai_reply_draft(
    draft: object,
    *,
    context: dict[str, object],
    recent_replies: list[object] | None = None,
) -> dict:
    """Validate a current draft through its owner."""
    return _reply_draft_owner().validate(
        draft, context=context, recent_replies=recent_replies,
    )


def store_pending_ai_reply(
    state: dict,
    target_id: str,
    candidate_source: str,
    reply: str,
    *,
    context: dict[str, object],
) -> bool:
    """Store a validated pending draft through its owner."""
    return _reply_draft_owner().store(
        state, target_id, candidate_source, reply, context=context,
    )


def pending_ai_reply(
    state: dict,
    target_id: str,
    candidate_source: str,
    *,
    context: dict[str, object],
    recent_replies: list[object] | None = None,
    evaluation_outcome: dict[str, object] | None = None,
) -> str | None:
    """Compatibility prose API; production cycles consume recover_pending_ai_reply."""
    result = recover_pending_ai_reply(
        state, target_id, candidate_source, context=context, recent_replies=recent_replies,
    )
    if result is None:
        return None
    if evaluation_outcome is not None and result.status == "operational_failure":
        evaluation_outcome.update(
            status=result.status, reason=result.reason,
            error_category=result.error_category, model_call_count=result.model_call_count,
        )
    return result.reply


def recover_pending_ai_reply(
    state: dict,
    target_id: str,
    candidate_source: str,
    *,
    context: dict[str, object],
    recent_replies: list[object] | None = None,
) -> PipelineResult | None:
    """Recover a pending draft through its owner without a provider call."""
    return _reply_draft_owner().recover(
        state, target_id, candidate_source,
        context=context, recent_replies=recent_replies,
    )


def clear_pending_ai_reply(state: dict, target_id: str, candidate_source: str) -> None:
    """Clear a pending draft through its owner."""
    return _reply_draft_owner().clear(state, target_id, candidate_source)


def log_ai_reply_posting_outcome(
    *,
    reply: str,
    status: str,
    lane: str,
    target_id: str,
    failure_reason: str,
    reply_post_id: str = "",
) -> None:
    """Emit a bounded posting outcome without model inputs or reasoning."""
    return _reply_generation.log_ai_reply_posting_outcome(
        reply=reply,
        status=status,
        lane=lane,
        target_id=target_id,
        failure_reason=failure_reason,
        reply_post_id=reply_post_id,
        log_event=log_event,
    )


CONVERSATIONAL_REPLY_HISTORY_LANES = _reply_history.CONVERSATIONAL_REPLY_HISTORY_LANES
_DEFAULT_RECENT_ACCOUNT_REPLY_LIMIT = MAX_RECENT_ACCOUNT_REPLIES


def _reply_history_owner() -> _reply_history.ReplyHistory:
    """Bind current history dependencies without reading state or the clock."""
    return _reply_history.ReplyHistory(
        now_epoch=now_epoch,
        quoted_post_reference_id=quoted_post_reference_id,
        maximum_state_epoch=MAX_REASONABLE_STATE_EPOCH,
        maximum_recent_replies=MAX_RECENT_ACCOUNT_REPLIES,
        default_recent_reply_limit=_DEFAULT_RECENT_ACCOUNT_REPLY_LIMIT,
        maximum_same_author_interactions=MAX_SAME_AUTHOR_INTERACTIONS,
        maximum_age_seconds=AI_REPLY_HISTORY_MAX_AGE_SECONDS,
        maximum_records=AI_REPLY_HISTORY_MAX_RECORDS,
    )


def _confirmed_conversational_history_rows(state: dict) -> list[dict]:
    """Select confirmed rows through the history owner."""
    return _reply_history_owner().confirmed_rows(state)


_confirmed_history_sort_key = _reply_history._confirmed_history_sort_key


def recent_confirmed_account_replies(
    state: dict,
    limit: int = MAX_RECENT_ACCOUNT_REPLIES,
    *,
    before_epoch: int | None = None,
    excluded_post_ids: set[str] | None = None,
    excluded_reply_post_ids: set[str] | None = None,
) -> list[dict[str, str]]:
    """Select recent confirmed prose through the history owner."""
    return _reply_history_owner().recent_replies(
        state, limit,
        before_epoch=before_epoch,
        excluded_post_ids=excluded_post_ids,
        excluded_reply_post_ids=excluded_reply_post_ids,
    )


def _reply_context_history_excluded_post_ids(
    context: dict[str, object],
) -> set[str]:
    """Select current conversation identities through the history owner."""
    return _reply_history_owner().context_excluded_post_ids(context)


def recovery_comparison_account_replies(
    state: dict,
    *,
    context: dict[str, object],
) -> list[dict[str, str]]:
    """Select current draft comparisons through the history owner."""
    return _reply_history_owner().recovery_replies(state, context=context)


def _same_author_confirmed_history_rows(
    state: dict,
    *,
    author_id: object,
    current_thread_post_ids: set[str],
    target_id: object,
    before_epoch: int | None,
) -> list[dict]:
    """Select prior confirmed author rows through the history owner."""
    return _reply_history_owner().same_author_rows(
        state,
        author_id=author_id,
        current_thread_post_ids=current_thread_post_ids,
        target_id=target_id,
        before_epoch=before_epoch,
    )


def recent_same_author_account_interactions(
    state: dict,
    *,
    author_id: object,
    conversation_id: object,
    target_id: object,
    before_epoch: int | None = None,
    visible_post_ids: set[str] | None = None,
) -> list[dict[str, str]]:
    """Select prior contributor/reply pairs through the history owner."""
    return _reply_history_owner().recent_same_author_interactions(
        state,
        author_id=author_id,
        conversation_id=conversation_id,
        target_id=target_id,
        before_epoch=before_epoch,
        visible_post_ids=visible_post_ids,
    )


_reply_target_epoch = _reply_history._reply_target_epoch


_REPLY_IMAGE_MIME_TYPES = _reply_native_media._REPLY_IMAGE_MIME_TYPES


class ReplyMediaUnavailable(RuntimeError):
    """A material candidate image could not be collected safely."""


class ReplyMediaTransientUnavailable(ReplyMediaUnavailable):
    """A material candidate image could not be collected on this cycle."""


def _safe_reply_image_url(value: object) -> str:
    """Check the trusted image origin through the media owner."""
    return _reply_media_owner().safe_url(value)


def collect_reply_images(media_context: dict | None) -> list[dict[str, object]]:
    """Collect up to two already-identified native X images with hard bounds."""
    return _reply_media_owner().collect(media_context)


def _reply_model_transport_owner() -> _reply_model_transport.ReplyModelTransport:
    return _reply_model_transport.ReplyModelTransport(
        log=log,
        model=SINGLE_CALL_MODEL,
        reasoning_effort=SINGLE_CALL_REASONING_EFFORT,
        monotonic=monotonic,
        require_remote_operation_unpaused=require_remote_operation_unpaused,
        report_bot_health_progress=report_bot_health_progress,
        requests=requests,
        base_url=OPENAI_BASE,
        api_key=OPENAI_API_KEY,
        sleep=sleep,
        now_epoch=now_epoch,
        error_type=ApiError,
    )


def _definite_connection_failure_before_transmission(
    error: requests.RequestException,
) -> bool:
    """Use the reply model transport with current runtime dependencies."""
    return _reply_model_transport_owner().definite_connection_failure_before_transmission(error)


def _openai_api_error(
    message: str,
    *,
    category: str,
    status_code: int | None = None,
    reset_epoch: int | None = None,
    retry_after_seconds: int | None = None,
    request_attempt_count: int = 1,
) -> ApiError:
    """Use the reply model transport with current runtime dependencies."""
    return _reply_model_transport_owner().error(
        message,
        category=category,
        status_code=status_code,
        reset_epoch=reset_epoch,
        retry_after_seconds=retry_after_seconds,
        request_attempt_count=request_attempt_count,
    )


def _openai_retry_metadata(response: requests.Response) -> tuple[int | None, int | None]:
    """Return bounded Retry-After metadata for provider cooldown accounting."""
    return _reply_model_transport_owner().retry_metadata(response)


_OPENAI_PROVIDER_HEALTH_FAILURE_CATEGORIES = _reply_generation._OPENAI_PROVIDER_HEALTH_FAILURE_CATEGORIES
_TERMINAL_CANDIDATE_LOCAL_FAILURE_CATEGORIES = _reply_generation._TERMINAL_CANDIDATE_LOCAL_FAILURE_CATEGORIES


def _reply_generation_owner() -> _reply_generation.ReplyGeneration:
    """Bind current generation boundaries without collecting evidence or media."""
    return _reply_generation.ReplyGeneration(
        collect_reply_images=collect_reply_images,
        remote_operations_paused=RemoteOperationsPaused,
        media_unavailable=ReplyMediaUnavailable,
        media_transient_unavailable=ReplyMediaTransientUnavailable,
        result_type=PipelineResult,
        log=log,
        history_for_evaluation=_reply_history_owner().for_evaluation,
        require_remote_operation_unpaused=require_remote_operation_unpaused,
        run_pipeline=run_single_call_reply_pipeline,
        config=single_call_reply,
        evidence_repository=reply_evidence_repository,
        transport=openai_responses_reply_call,
        record_api_error=record_api_error,
        provider_error=_openai_api_error,
        reply_type=ValidatedReply,
        now_epoch=now_epoch,
        decision_telemetry=single_call_decision_telemetry,
        log_event=log_event,
        model=SINGLE_CALL_MODEL,
        strategy_version=SINGLE_CALL_STRATEGY_VERSION,
    )


_is_openai_provider_health_failure = _reply_generation._is_openai_provider_health_failure


_is_terminal_candidate_local_failure = _reply_generation._is_terminal_candidate_local_failure


def openai_responses_reply_call(
    *,
    request: dict[str, object],
    timeout_seconds: int,
    lane: str,
    target_id: str,
) -> dict[str, object]:
    """Send one executable Responses request, retrying only proved non-execution."""
    return _reply_model_transport_owner().call(
        request=request,
        timeout_seconds=timeout_seconds,
        lane=lane,
        target_id=target_id,
    )


def _record_single_call_result(
    result: PipelineResult,
    *,
    lane: str,
    target_id: str,
) -> None:
    """Delegate reply generation with current root dependencies."""
    return _reply_generation_owner().record_result(
        result, lane=lane, target_id=target_id,
    )


def generate_single_call_reply(
    context: dict[str, object],
    media_context: dict | None = None,
    *,
    state: dict,
    evaluation_outcome: dict | None = None,
) -> str | None:
    """Compatibility prose API; production cycles consume evaluate_single_call_reply."""
    result = evaluate_single_call_reply(context, media_context, state=state)
    if evaluation_outcome is not None:
        evaluation_outcome.update(
            status=result.status, reason=result.reason,
            error_category=result.error_category, model_call_count=result.model_call_count,
        )
        if result.reason != "material_image_unavailable":
            evaluation_outcome.update(reason_code=result.reason_code, reply_kind=result.reply_kind)
    return result.reply


def evaluate_single_call_reply(
    context: dict[str, object],
    media_context: dict | None = None,
    *,
    state: dict,
) -> PipelineResult:
    """Return the authoritative reply decision and its accounting metadata."""
    return _reply_generation_owner().evaluate(context, media_context, state=state)


terminal_reply_evaluation = _reply_evaluation_state.terminal_reply_evaluation


def record_terminal_reply_evaluation(
    state: dict,
    *,
    target_id: str,
    lane: str,
    reason: str,
    outcome: str = "no_reply",
    prune_records: bool = True,
    evidence_policy: str | None = None,
) -> None:
    """Record terminal reply evaluation."""
    return _reply_evaluation_owner().record(
        state,
        target_id=target_id,
        lane=lane,
        reason=reason,
        outcome=outcome,
        prune_records=prune_records,
        evidence_policy=evidence_policy,
    )


def ai_reply_receipt_draft_is_valid(data: dict, text: object) -> bool:
    """Validate the current draft carried by a receipt through its owner."""
    return _reply_draft_owner().receipt_draft_is_valid(data, text)


_LEGACY_TESTED_REPLY_STRATEGY_VERSION = _legacy_reply_validation._LEGACY_TESTED_REPLY_STRATEGY_VERSION
_LEGACY_AI_FIRST_REPLY_STRATEGY_VERSION = _legacy_reply_validation._LEGACY_AI_FIRST_REPLY_STRATEGY_VERSION
_LEGACY_SINGLE_SOL_REPLY_STRATEGY_VERSION = _legacy_reply_validation._LEGACY_SINGLE_SOL_REPLY_STRATEGY_VERSION
_LEGACY_SINGLE_SOL_PROMPT_SHA256 = _legacy_reply_validation._LEGACY_SINGLE_SOL_PROMPT_SHA256
_LEGACY_SINGLE_SOL_RESPONSE_SCHEMA_SHA256 = _legacy_reply_validation._LEGACY_SINGLE_SOL_RESPONSE_SCHEMA_SHA256
_LEGACY_SINGLE_SOL_SCHEMA4_PROMPT_SHA256 = _legacy_reply_validation._LEGACY_SINGLE_SOL_SCHEMA4_PROMPT_SHA256
_LEGACY_SINGLE_SOL_SCHEMA4_RESPONSE_SCHEMA_SHA256 = _legacy_reply_validation._LEGACY_SINGLE_SOL_SCHEMA4_RESPONSE_SCHEMA_SHA256
_LEGACY_MULTI_MODEL_REPLY_CONTEXT_FIELDS = _legacy_reply_validation._LEGACY_MULTI_MODEL_REPLY_CONTEXT_FIELDS
_LEGACY_TESTED_REPLY_DRAFT_FIELDS = _legacy_reply_validation._LEGACY_TESTED_REPLY_DRAFT_FIELDS
_LEGACY_TESTED_REPLY_DIRECT_REPAIR_FIELDS = _legacy_reply_validation._LEGACY_TESTED_REPLY_DIRECT_REPAIR_FIELDS
_LEGACY_AI_FIRST_REPLY_DRAFT_FIELDS = _legacy_reply_validation._LEGACY_AI_FIRST_REPLY_DRAFT_FIELDS
_LEGACY_SINGLE_SOL_REPLY_DRAFT_FIELDS = _legacy_reply_validation._LEGACY_SINGLE_SOL_REPLY_DRAFT_FIELDS
_LEGACY_AI_FIRST_REPLY_MODES = _legacy_reply_validation._LEGACY_AI_FIRST_REPLY_MODES
_LEGACY_AI_FIRST_REPLY_TONES = _legacy_reply_validation._LEGACY_AI_FIRST_REPLY_TONES
_LEGACY_AI_FIRST_ANSWER_TYPES = _legacy_reply_validation._LEGACY_AI_FIRST_ANSWER_TYPES
_LEGACY_AI_FIRST_WORLD_CLAIM_FIELDS = _legacy_reply_validation._LEGACY_AI_FIRST_WORLD_CLAIM_FIELDS
_LEGACY_SINGLE_SOL_REPLY_KINDS = _legacy_reply_validation._LEGACY_SINGLE_SOL_REPLY_KINDS
_LEGACY_SINGLE_SOL_REASON_CODES = _legacy_reply_validation._LEGACY_SINGLE_SOL_REASON_CODES
_LEGACY_SINGLE_SOL_IMAGE_MIME_TYPES = _legacy_reply_validation._LEGACY_SINGLE_SOL_IMAGE_MIME_TYPES


_legacy_reply_value_sha256 = _legacy_reply_validation._legacy_reply_value_sha256


_legacy_reply_sha256_is_valid = _legacy_reply_validation._legacy_reply_sha256_is_valid


_legacy_reply_utc_timestamp_is_valid = _legacy_reply_validation._legacy_reply_utc_timestamp_is_valid


_legacy_multi_model_context_post_is_valid = _legacy_reply_validation._legacy_multi_model_context_post_is_valid


_legacy_multi_model_reply_context_is_valid = _legacy_reply_validation._legacy_multi_model_reply_context_is_valid


def _legacy_tested_reply_draft_is_valid(
    draft: dict,
    *,
    context: dict,
    text: object,
) -> bool:
    """Delegate frozen validation with current root dependencies."""
    return _legacy_reply_validation._legacy_tested_reply_draft_is_valid(
        draft,
        context=context,
        text=text,
    )


_legacy_ai_first_claim_is_valid = _legacy_reply_validation._legacy_ai_first_claim_is_valid


def _legacy_ai_first_sentence_assessment_is_valid(value: object) -> bool:
    """Delegate frozen validation with current root dependencies."""
    return _legacy_reply_validation._legacy_ai_first_sentence_assessment_is_valid(
        value,
    )


def _legacy_ai_first_claim_audit_is_valid(value: object) -> bool:
    """Delegate frozen validation with current root dependencies."""
    return _legacy_reply_validation._legacy_ai_first_claim_audit_is_valid(
        value,
    )


def _legacy_ai_first_reply_draft_is_valid(
    draft: dict,
    *,
    context: dict,
    text: object,
) -> bool:
    """Delegate frozen validation with current root dependencies."""
    return _legacy_reply_validation._legacy_ai_first_reply_draft_is_valid(
        draft,
        context=context,
        text=text,
    )


def _legacy_single_sol_reply_draft_is_valid(
    data: dict,
    draft: dict,
    *,
    context: dict,
    text: object,
) -> bool:
    """Delegate frozen validation with current root dependencies."""
    return _legacy_reply_validation._legacy_single_sol_reply_draft_is_valid(
        data, draft,
        context=context,
        text=text,
        bound_visible_conversation=bound_visible_conversation,
        MAX_TRUSTED_FACTS=MAX_TRUSTED_FACTS,
        MAX_SUPPLIED_IMAGES=MAX_SUPPLIED_IMAGES,
        SINGLE_CALL_MAX_IMAGE_BYTES=SINGLE_CALL_MAX_IMAGE_BYTES,
    )


def _legacy_ai_reply_receipt_draft_is_valid(data: dict, text: object) -> bool:
    """Delegate frozen validation with current root dependencies."""
    return _legacy_reply_validation._legacy_ai_reply_receipt_draft_is_valid(
        data, text,
        _legacy_single_sol_reply_draft_is_valid=_legacy_single_sol_reply_draft_is_valid,
    )


def _reply_receipt_values_owner() -> _reply_receipt_values.ReplyReceiptValues:
    """Bind current receipt value boundaries without reading the clock or state."""
    return _reply_receipt_values.ReplyReceiptValues(
        valid_receipt_epoch=valid_receipt_epoch,
        safe_reply_cap_date_str=safe_reply_cap_date_str,
        legacy_draft_is_valid=_legacy_ai_reply_receipt_draft_is_valid,
        draft_is_valid=ai_reply_receipt_draft_is_valid,
        now_epoch=now_epoch,
        reply_cap_date_str=reply_cap_date_str,
        log=log,
        invalid_receipt=InvalidConfirmedReplyReceipt,
    )


mention_pagination_provenance_is_valid = _mention_authority.mention_pagination_provenance_is_valid


def _conversational_reply_receipt_is_semantically_valid(
    data: dict,
    *,
    lifecycle_state: str,
    legacy_recovery: bool = False,
) -> bool:
    """Validate one current receipt or a frozen lifecycle-recovery receipt."""
    return _reply_receipt_values_owner().validate(
        data,
        lifecycle_state=lifecycle_state,
        legacy_recovery=legacy_recovery,
    )


def conversational_sending_receipt_from_confirmed(
    confirmed_receipt: dict,
) -> dict:
    """Reconstruct the exact schema-v4 pre-transport reply receipt.

    Legacy confirmed receipts intentionally lack ``source_receipt_sha256`` and
    remain readable for local reconciliation, but cannot use this function to
    retire a current transport journal.
    """
    return _reply_receipt_values_owner().sending_from_confirmed(confirmed_receipt)


def confirmed_reply_receipt_is_semantically_valid(data: dict) -> bool:
    """Return whether a confirmed-reply receipt is internally consistent."""
    return _reply_receipt_values_owner().confirmed_is_valid(data)


def sending_reply_receipt_is_semantically_valid(data: dict) -> bool:
    """Return whether a pre-send conversational-reply receipt is complete."""
    return _reply_receipt_values_owner().sending_is_valid(data)


def _legacy_confirmed_reply_receipt_is_semantically_valid(data: dict) -> bool:
    """Accept a frozen draft only for local recovery after remote confirmation."""
    return _reply_receipt_values_owner().legacy_confirmed_is_valid(data)


def _legacy_sending_reply_receipt_is_semantically_valid(data: dict) -> bool:
    """Recognise a frozen sending receipt as a barrier, never send authority."""
    return _reply_receipt_values_owner().legacy_sending_is_valid(data)


def load_confirmed_reply_receipt() -> tuple[str, dict | None]:
    """Load confirmed reply receipt."""
    return _reply_delivery.load_confirmed_reply_receipt(
        load_receipt_json_no_follow=load_receipt_json_no_follow,
        CONFIRMED_REPLY_RECEIPT_FILE=CONFIRMED_REPLY_RECEIPT_FILE,
        log=log,
        receipt_values=_reply_receipt_values_owner(),
    )


def write_confirmed_reply_receipt(receipt: dict) -> None:
    """Write confirmed reply receipt."""
    return _reply_delivery.write_reply_receipt(
        receipt,
        confirmed=True,
        remote_receipt_retirement_is_blocking=remote_receipt_retirement_is_blocking,
        InvalidConfirmedReplyReceipt=InvalidConfirmedReplyReceipt,
        receipt_namespace_entry_exists=receipt_namespace_entry_exists,
        CONFIRMED_REPLY_RECEIPT_FILE=CONFIRMED_REPLY_RECEIPT_FILE,
        receipt_values=_reply_receipt_values_owner(),
        durable_create_receipt_json=durable_create_receipt_json,
        log=log,
    )


def write_sending_reply_receipt(receipt: dict) -> None:
    """Durably record a reply transaction before its remote create request."""
    return _reply_delivery.write_reply_receipt(
        receipt,
        confirmed=False,
        remote_receipt_retirement_is_blocking=remote_receipt_retirement_is_blocking,
        InvalidConfirmedReplyReceipt=InvalidConfirmedReplyReceipt,
        receipt_namespace_entry_exists=receipt_namespace_entry_exists,
        CONFIRMED_REPLY_RECEIPT_FILE=CONFIRMED_REPLY_RECEIPT_FILE,
        receipt_values=_reply_receipt_values_owner(),
        durable_create_receipt_json=durable_create_receipt_json,
        log=log,
    )


def bind_conversational_reply_attempt_time(receipt_template: dict) -> dict:
    """Bind a schema-v4 reply template to its immediately pre-send time."""
    return _reply_receipt_values_owner().bind_attempt(receipt_template)


def _confirmed_reply_receipt_from_sending(
    sending_receipt: dict,
    *,
    reply_post_id: str,
    confirmation_epoch: int,
) -> dict:
    """Build the confirmed form without mutating its durable sending input."""
    return _reply_receipt_values_owner().confirmed_from_sending(
        sending_receipt,
        reply_post_id=reply_post_id,
        confirmation_epoch=confirmation_epoch,
    )


def _reply_confirmation_epoch_after_remote_success(
    sending_receipt: dict,
    observed_epoch: int | None = None,
) -> int:
    """Return a conservative monotonic wall time after remote confirmation."""
    return _reply_receipt_values_owner().observed_confirmation_epoch(
        sending_receipt,
        observed_epoch,
    )


def promote_sending_reply_receipt(
    sending_receipt: dict,
    *,
    reply_post_id: str,
    confirmation_epoch: int,
) -> dict:
    """Atomically promote the exact prepared transaction to confirmed."""
    return _reply_delivery.promote_sending_reply_receipt(
        sending_receipt,
        reply_post_id=reply_post_id,
        confirmation_epoch=confirmation_epoch,
        load_confirmed_reply_receipt=load_confirmed_reply_receipt,
        UnresolvedSendingReplyReceipt=UnresolvedSendingReplyReceipt,
        bind_confirmed_transport_source=bind_confirmed_transport_source,
        journal_path_for_receipt=journal_path_for_receipt,
        CONFIRMED_REPLY_RECEIPT_FILE=CONFIRMED_REPLY_RECEIPT_FILE,
        TRANSPORT_SOURCE_VALIDATOR_ID=TRANSPORT_SOURCE_VALIDATOR_ID,
        transport_source_semantic_validator=transport_source_semantic_validator,
        TransportJournalError=TransportJournalError,
        receipt_values=_reply_receipt_values_owner(),
        legacy_recovery=False,
        replace_bound_source_receipt=replace_bound_source_receipt,
        transaction_mutation_authority=transaction_mutation_authority,
        log=log,
    )


def _promote_legacy_sending_reply_receipt_from_confirmed_transport(
    sending_receipt: dict,
    *,
    reply_post_id: str,
    confirmation_epoch: int,
) -> dict:
    """Promote a frozen source only when its exact journal proves success."""
    return _reply_delivery.promote_sending_reply_receipt(
        sending_receipt,
        reply_post_id=reply_post_id,
        confirmation_epoch=confirmation_epoch,
        load_confirmed_reply_receipt=load_confirmed_reply_receipt,
        UnresolvedSendingReplyReceipt=UnresolvedSendingReplyReceipt,
        bind_confirmed_transport_source=bind_confirmed_transport_source,
        journal_path_for_receipt=journal_path_for_receipt,
        CONFIRMED_REPLY_RECEIPT_FILE=CONFIRMED_REPLY_RECEIPT_FILE,
        TRANSPORT_SOURCE_VALIDATOR_ID=TRANSPORT_SOURCE_VALIDATOR_ID,
        transport_source_semantic_validator=_legacy_conversational_transport_source_semantic_validator,
        TransportJournalError=TransportJournalError,
        receipt_values=_reply_receipt_values_owner(),
        legacy_recovery=True,
        replace_bound_source_receipt=replace_bound_source_receipt,
        transaction_mutation_authority=transaction_mutation_authority,
        log=log,
    )


def remove_confirmed_reply_receipt(
    receipt: dict,
    *,
    sending_disposition: str | None = None,
    commit_proof=None,
) -> None:
    """Retire one exact conversational-reply source receipt."""
    if receipt.get("lifecycle_state") != "sending" or sending_disposition == "confirmed_state_fallback":
        from mrs_bot_state_generation import require_commit_proof
        require_commit_proof(commit_proof)
        commit_proof.require_receipt(receipt)
    return _reply_delivery.remove_confirmed_reply_receipt(
        receipt,
        sending_disposition=sending_disposition,
        CONFIRMED_REPLY_RECEIPT_FILE=CONFIRMED_REPLY_RECEIPT_FILE,
        load_receipt_json_no_follow=load_receipt_json_no_follow,
        InvalidConfirmedReplyReceipt=InvalidConfirmedReplyReceipt,
        retire_current_source_receipt=functools.partial(
            retire_current_source_receipt, commit_proof=commit_proof,
            **({"disposition": "definite_non_success"}
               if receipt.get("lifecycle_state") == "sending"
               and sending_disposition == "definite_non_success" else {}),
        ),
        log=log,
    )


def conversational_reply_confirmation_epoch(receipt: dict) -> int:
    """Return the best available confirmed time for a reply receipt."""
    return _reply_receipt_values_owner().confirmation_epoch(receipt)


def _valid_iso_date(value: object) -> bool:
    """Return whether a value is a canonical calendar date."""
    return _daily_reply_accounting_owner().valid_date(value)


def _advance_reply_counters_to_confirmation_date(
    state: dict,
    confirmation_date: str,
    *,
    include_quote_lane: bool,
) -> None:
    """Advance stale daily reply buckets without rolling newer state backward."""
    return _daily_reply_accounting_owner().advance(
        state,
        confirmation_date,
        include_quote_lane=include_quote_lane,
    )


def apply_confirmed_reply_receipt(state: dict, receipt: dict) -> None:
    """Apply confirmed reply receipt."""
    return _reply_reconciliation.apply_confirmed_reply_receipt(
        state, receipt,
        receipt_values=_reply_receipt_values_owner(),
        validate_pending_mention_candidate_authority=validate_pending_mention_candidate_authority,
        STATE_FILE=STATE_FILE,
        InvalidConfirmedReplyReceipt=InvalidConfirmedReplyReceipt,
        reply_cap_date_str=reply_cap_date_str,
        accounting=_daily_reply_accounting_owner(),
        mention_pagination_has_canonical_page_ownership=mention_pagination_has_canonical_page_ownership,
        _emit_mention_authority_recovery=_emit_mention_authority_recovery,
        log=log,
        clear_target_drafts=_reply_draft_owner().clear_target,
        remove_pending_mention_candidate=remove_pending_mention_candidate,
        update_last_seen_mention_id=update_last_seen_mention_id,
        cache_tweet=cache_tweet,
        MY_USER_ID=MY_USER_ID,
        datetime=datetime,
        record_reply_history=_reply_history_owner().record_confirmation,
        clarifications=_clarification_reply_owner(),
        log_event=log_event,
    )


def update_last_seen_mention_id(state: dict, mention_id: str) -> None:
    """Delegate to the mention queue with current root dependencies."""
    return _mention_queue_owner().advance_watermark(state, mention_id)


def mark_mention_seen_if_applicable(state: dict, candidate: dict) -> None:
    """Delegate to the mention queue with current root dependencies."""
    return _mention_queue_owner().mark_seen(state, candidate)


def _reply_completion_owner() -> _reply_reconciliation.ReplyCompletion:
    """Bind current completion authorities without reading state or receipt files."""
    return _reply_reconciliation.ReplyCompletion(
        receipt_path=CONFIRMED_REPLY_RECEIPT_FILE,
        persistence_error=ConfirmedReplyLocalPersistenceError,
        apply_state=apply_confirmed_reply_receipt,
        save_state=save_state,
        retire_journal=retire_lane_transport_journal_if_present,
        remove_receipt=remove_confirmed_reply_receipt,
        log=log,
        load_receipt=load_confirmed_reply_receipt,
        unresolved_sending_receipt=UnresolvedSendingReplyReceipt,
        invalid_receipt=InvalidConfirmedReplyReceipt,
        verify_lineage=verify_lane_transport_source_lineage_if_present,
    )


def reconcile_confirmed_reply_receipt(state: dict) -> bool:
    """Reconcile a confirmed reply without duplicating the remote post."""
    return _reply_completion_owner().reconcile(state)


def confirmed_reply_emergency_representation_is_complete(
    receipt: dict,
    state: dict,
) -> bool:
    """Return whether state alone durably suppresses a confirmed reply replay."""
    return _reply_reconciliation.confirmed_reply_emergency_representation_is_complete(
        receipt, state,
        receipt_values=_reply_receipt_values_owner(),
        InvalidConfirmedReplyReceipt=InvalidConfirmedReplyReceipt,
        receipt_int=receipt_int,
        has_target_draft=_reply_draft_owner().has_target,
    )


def retire_proved_rejected_conversational_reply_receipt(
    receipt: dict,
    error: ProvedRemotePostNonSuccess,
) -> None:
    """Retire the exact sending receipt after terminal state is durable."""
    return _reply_delivery.retire_proved_rejected_conversational_reply_receipt(
        receipt, error,
        ProvedRemotePostNonSuccess=ProvedRemotePostNonSuccess,
        api_error_is_reply_not_allowed=api_error_is_reply_not_allowed,
        sending_reply_receipt_is_semantically_valid=sending_reply_receipt_is_semantically_valid,
        reply_create_rejection_payload=reply_create_rejection_payload,
        claim_reply_create_rejection_for_receipt_retirement=claim_reply_create_rejection_for_receipt_retirement,
        CONFIRMED_REPLY_RECEIPT_FILE=CONFIRMED_REPLY_RECEIPT_FILE,
        remove_confirmed_reply_receipt=remove_confirmed_reply_receipt,
        record_ambiguous_remote_post=record_ambiguous_remote_post,
        ConfirmedReplyLocalPersistenceError=ConfirmedReplyLocalPersistenceError,
    )


def post_conversational_reply_with_durable_identity(
    *,
    state: dict,
    receipt_template: dict,
    reply_text: str,
    reply_to_id: str,
    made_with_ai: bool,
    lane: str,
) -> tuple[dict, dict]:
    """Create a conversational reply and durably bind its remote identity.

    A controlled SIGINT is deferred from the first remote-create instruction
    until either the confirmed-reply receipt, a complete canonical state
    fallback, or the global manual-reconciliation barrier is durable.
    """
    return _reply_delivery.post_conversational_reply_with_durable_identity(
        state=state,
        receipt_template=receipt_template,
        reply_text=reply_text,
        reply_to_id=reply_to_id,
        made_with_ai=made_with_ai,
        lane=lane,
        receipt_values=_reply_receipt_values_owner(),
        receipt_namespace_entry_exists=receipt_namespace_entry_exists,
        CONFIRMED_REPLY_RECEIPT_FILE=CONFIRMED_REPLY_RECEIPT_FILE,
        InvalidConfirmedReplyReceipt=InvalidConfirmedReplyReceipt,
        block_if_ambiguous_remote_post=block_if_ambiguous_remote_post,
        write_sending_reply_receipt=write_sending_reply_receipt,
        begin_confirmed_post_sigint_deferral=begin_confirmed_post_sigint_deferral,
        create_post=create_post,
        AmbiguousRemotePostOutcome=AmbiguousRemotePostOutcome,
        end_confirmed_post_sigint_deferral=end_confirmed_post_sigint_deferral,
        record_api_error=record_api_error,
        save_state=save_state,
        log=log,
        RemoteOperationsPaused=RemoteOperationsPaused,
        remove_confirmed_reply_receipt=remove_confirmed_reply_receipt,
        ConfirmedReplyLocalPersistenceError=ConfirmedReplyLocalPersistenceError,
        ProvedRemotePostNonSuccess=ProvedRemotePostNonSuccess,
        ApiError=ApiError,
        inspect_confirmed_transport_transaction=inspect_confirmed_transport_transaction,
        journal_path_for_receipt=journal_path_for_receipt,
        promote_sending_reply_receipt=promote_sending_reply_receipt,
        apply_confirmed_reply_receipt=apply_confirmed_reply_receipt,
        StateBackupWriteError=StateBackupWriteError,
        json_file_matches=json_file_matches,
        STATE_FILE=STATE_FILE,
        confirmed_reply_emergency_representation_is_complete=confirmed_reply_emergency_representation_is_complete,
        latch_confirmed_post_persistence_failure=latch_confirmed_post_persistence_failure,
        load_confirmed_reply_receipt=load_confirmed_reply_receipt,
        retain_sigint_deferral_without_durable_barrier=retain_sigint_deferral_without_durable_barrier,
        UnrecoverableConfirmedReplyPersistenceError=UnrecoverableConfirmedReplyPersistenceError,
        completion=_reply_completion_owner(),
    )


def finalise_confirmed_reply(
    state: dict, receipt: dict, *, target_id: str, quote_reply: bool,
) -> str:
    """Commit a reply confirmation before either cycle reports successful posting."""
    return _reply_completion_owner().finalise(
        state, receipt, target_id=target_id, quote_reply=quote_reply,
    )


def _reply_cycle_persistence() -> _reply_cycle_interfaces.ReplyCyclePersistence:
    """Bind one draft owner and the current durable save for a cycle invocation."""
    drafts = _reply_draft_owner()
    return _reply_cycle_interfaces.ReplyCyclePersistence(
        save=save_state, recover=drafts.recover,
        store=drafts.store, clear=drafts.clear,
        retire_ineligible=drafts.retire_ineligible,
    )


def _reply_cycle_delivery() -> _reply_cycle_interfaces.ReplyCycleDelivery:
    """Bind current receipt and delivery callbacks without retaining caller state."""
    return _reply_cycle_interfaces.ReplyCycleDelivery(
        load_receipt=load_confirmed_reply_receipt,
        reconcile_receipt=reconcile_confirmed_reply_receipt,
        block_ambiguous=block_if_ambiguous_remote_post,
        bind_attempt=bind_conversational_reply_attempt_time,
        target_available=reply_target_is_available_immediately_before_send,
        post=post_conversational_reply_with_durable_identity,
        retire_rejected=retire_proved_rejected_conversational_reply_receipt,
        finalise=finalise_confirmed_reply,
    )


def maybe_reply_to_mentions(
    state: dict,
    *,
    _fresh_mention_ai_evaluations: int = 0,
    _skip_hot_post_fetch: bool = False,
) -> str:
    """Process eligible mention and hot-post candidates under all reply limits."""
    return _normal_reply_cycle.maybe_reply_to_mentions(
        state,
        config=_reply_cycle_interfaces.NormalReplyConfig(
            enabled=ENABLE_AUTO_REPLIES,
            mark_as_ai=MARK_AI_REPLIES_AS_AI,
            maximum_daily_replies=MAX_AUTO_REPLIES_PER_DAY,
            maximum_daily_author_replies=MAX_REPLIES_PER_AUTHOR_PER_DAY,
            minimum_reply_spacing=MIN_SECONDS_BETWEEN_REPLIES,
            user_id=MY_USER_ID,
            maximum_fresh_evaluations=MAX_MENTIONS_PER_CHECK,
            incoming_max_chars=REPLY_INCOMING_MAX_CHARS,
        ),
        persistence=_reply_cycle_persistence(),
        delivery=_reply_cycle_delivery(),
        _fresh_mention_ai_evaluations=_fresh_mention_ai_evaluations,
        _skip_hot_post_fetch=_skip_hot_post_fetch,
        author_quarantines=_author_quarantine_owner(),
        AmbiguousRemotePostOutcome=AmbiguousRemotePostOutcome,
        ApiError=ApiError,
        ConfirmedReplyLocalPersistenceError=ConfirmedReplyLocalPersistenceError,
        PipelineResult=PipelineResult,
        ProvedRemotePostNonSuccess=ProvedRemotePostNonSuccess,
        RemoteOperationsPaused=RemoteOperationsPaused,
        ReplyEvidenceUnavailable=ReplyEvidenceUnavailable,
        SINGLE_CALL_STRATEGY_VERSION=SINGLE_CALL_STRATEGY_VERSION,
        UnrecoverableConfirmedReplyPersistenceError=UnrecoverableConfirmedReplyPersistenceError,
        ValidatedReply=ValidatedReply,
        _log_validated_single_call_reply=_log_validated_single_call_reply,
        _record_single_call_result=_record_single_call_result,
        api_error_is_reply_not_allowed=api_error_is_reply_not_allowed,
        build_context_for_reply_ai=build_context_for_reply_ai,
        cache_tweet=cache_tweet,
        clarifications=_clarification_reply_owner(),
        conversational_reply_pipeline_enabled=conversational_reply_pipeline_enabled,
        accounting=_daily_reply_accounting_owner(),
        dedupe_reply_candidates=dedupe_reply_candidates,
        evaluate_single_call_reply=evaluate_single_call_reply,
        get_hot_post_reply_candidates=get_hot_post_reply_candidates,
        get_mentions=get_mentions,
        in_api_cooldown=in_api_cooldown,
        is_probably_spam_or_not_worth_replying=is_probably_spam_or_not_worth_replying,
        lane_paused=lane_paused,
        log=log,
        log_ai_reply_posting_outcome=log_ai_reply_posting_outcome,
        log_event=log_event,
        mention_queue=_mention_queue_owner(),
        maybe_mark_hot_post_reply_skipped=maybe_mark_hot_post_reply_skipped,
        maybe_reply_to_mentions=maybe_reply_to_mentions,
        now_epoch=now_epoch,
        reply_evaluations=_reply_evaluation_owner(),
        record_api_error=record_api_error,
        recovery_comparison_account_replies=_reply_history_owner().recovery_replies,
        reply_evidence_repository=reply_evidence_repository,
        reply_target_is_directly_eligible=reply_target_is_directly_eligible,
        valid_tweets_sorted_by_id=valid_tweets_sorted_by_id,
    )


# ---------------------------------------------------------------------
# Quote-post replies
# ---------------------------------------------------------------------

def _quote_watch_posts_owner() -> _quote_discovery.QuoteWatchPosts:
    """Bind current watch selection settings without reading the watch file."""
    return _quote_discovery.QuoteWatchPosts(
        watch_file=EXTRA_QUOTE_WATCH_FILE,
        maximum_extra_posts=MAX_EXTRA_QUOTE_WATCH_POSTS,
        maximum_posts=MAX_QUOTE_POSTS_PER_CHECK,
        lookback_posts=QUOTE_POST_LOOKBACK_MAIN_POSTS,
        seed_recent=seed_recent_own_post_ids_from_cache,
        log=log,
    )


def load_extra_quote_watch_post_ids() -> list[str]:
    """Delegate to watched-post selection with current root dependencies."""
    return _quote_watch_posts_owner().load_extra()


def build_quote_lookup_post_ids(state: dict) -> list[str]:
    """Delegate to watched-post selection with current root dependencies."""
    return _quote_watch_posts_owner().lookup(state)


def get_recent_own_post_ids_for_quote_lookup(state: dict) -> list[str]:
    """Delegate to watched-post selection with current root dependencies."""
    return _quote_watch_posts_owner().recent(state)


def get_quote_tweets_for_post(post_id: str, state: dict | None = None) -> list[dict]:
    """Delegate to quote discovery with current root dependencies."""
    return _quote_discovery.get_quote_tweets_for_post(
        post_id,
        state,
        QUOTE_LOOKUP_API_MAX_RESULTS=QUOTE_LOOKUP_API_MAX_RESULTS,
        QUOTE_LOOKUP_MAX_PAGES_PER_POST=QUOTE_LOOKUP_MAX_PAGES_PER_POST,
        QUOTE_REPEATED_CURSOR_BACKOFF_SECONDS=QUOTE_REPEATED_CURSOR_BACKOFF_SECONDS,
        log=log,
        log_event=log_event,
        log_json_debug=log_json_debug,
        normalise_quote_repeated_cursor_suppressions=normalise_quote_repeated_cursor_suppressions,
        now_epoch=now_epoch,
        quote_repeated_cursor_suppression_record=quote_repeated_cursor_suppression_record,
        save_state=save_state,
        x_paginated_get=x_paginated_get,
        x_quote_lookup_request=x_quote_lookup_request,
    )


def get_quote_tweets_for_posts(post_ids: list[str], state: dict | None = None) -> dict[str, list[dict]]:
    """Search watched originals together using current root dependencies."""
    return _quote_discovery.get_quote_tweets_for_posts(
        post_ids,
        state,
        QUOTE_LOOKUP_API_MAX_RESULTS=QUOTE_LOOKUP_API_MAX_RESULTS,
        QUOTE_LOOKUP_MAX_PAGES_PER_POST=QUOTE_LOOKUP_MAX_PAGES_PER_POST,
        log=log,
        save_state=save_state,
        x_paginated_get=x_paginated_get,
        x_quote_lookup_request=x_quote_lookup_request,
    )


def quote_tweet_is_old_enough(quote_tweet: dict) -> bool:
    """Return whether quote tweet is old enough."""
    return _quote_reply_cycle.quote_tweet_is_old_enough(
        quote_tweet,
        QUOTE_REPLY_DELAY_SECONDS=QUOTE_REPLY_DELAY_SECONDS,
        log=log,
        now_epoch=now_epoch,
        parse_x_datetime_to_epoch=parse_x_datetime_to_epoch,
    )


def quote_tweet_directly_quotes_original(quote_tweet: dict, original_post_id: str) -> bool:
    """
    The /quote_tweets endpoint can surface reposts/retweets of someone else's
    quote-tweet. Those are not good reply targets. Only treat the item as
    replyable if X's structured referenced_tweets says it directly quoted the
    original post we are checking.
    """
    return _quote_reply_cycle.quote_tweet_directly_quotes_original(
        quote_tweet,
        original_post_id,
    )


quote_author_profile_text = _quote_reply_cycle.quote_author_profile_text


def build_quote_tweet_reply_context(
    original_tweet: dict,
    quote_tweet: dict,
) -> _reply_cycle_interfaces.PreparedReplyContext:
    """Build the canonical two-turn context for a direct quote-tweet."""
    return _reply_context_owner().build_quote(
        original_tweet,
        quote_tweet,
    )


def mark_quote_tweet_skipped(state: dict, quote_id: str) -> None:
    """Mark quote tweet skipped."""
    return _quote_reply_cycle.mark_quote_tweet_skipped(
        state,
        quote_id,
    )


def mark_quote_tweet_replied(state: dict, quote_id: str) -> None:
    """Mark quote tweet replied."""
    return _quote_reply_cycle.mark_quote_tweet_replied(
        state,
        quote_id,
    )


def mark_quote_spam_author(state: dict, author_id: str) -> None:
    """Mark quote spam author."""
    return _quote_reply_cycle.mark_quote_spam_author(
        state,
        author_id,
        log=log,
    )


def maybe_reply_to_quote_tweets(state: dict) -> str:
    """Process eligible quote-tweet candidates under all reply limits."""
    return _quote_reply_cycle.maybe_reply_to_quote_tweets(
        state,
        config=_reply_cycle_interfaces.QuoteReplyConfig(
            enabled=ENABLE_AUTO_REPLIES,
            mark_as_ai=MARK_AI_REPLIES_AS_AI,
            maximum_daily_replies=MAX_AUTO_REPLIES_PER_DAY,
            maximum_daily_author_replies=MAX_REPLIES_PER_AUTHOR_PER_DAY,
            minimum_reply_spacing=MIN_SECONDS_BETWEEN_REPLIES,
            user_id=MY_USER_ID,
            quote_checks_enabled=ENABLE_QUOTE_TWEET_CHECKS,
            maximum_candidates=MAX_QUOTE_POSTS_PER_CHECK,
            maximum_daily_quote_replies=MAX_QUOTE_REPLIES_PER_DAY,
        ),
        persistence=_reply_cycle_persistence(),
        delivery=_reply_cycle_delivery(),
        AmbiguousRemotePostOutcome=AmbiguousRemotePostOutcome,
        ApiError=ApiError,
        ConfirmedReplyLocalPersistenceError=ConfirmedReplyLocalPersistenceError,
        ContextValidationError=ContextValidationError,
        PipelineResult=PipelineResult,
        ProvedRemotePostNonSuccess=ProvedRemotePostNonSuccess,
        RemoteOperationsPaused=RemoteOperationsPaused,
        ReplyEvidenceUnavailable=ReplyEvidenceUnavailable,
        SINGLE_CALL_STRATEGY_VERSION=SINGLE_CALL_STRATEGY_VERSION,
        UnrecoverableConfirmedReplyPersistenceError=UnrecoverableConfirmedReplyPersistenceError,
        ValidatedReply=ValidatedReply,
        _log_validated_single_call_reply=_log_validated_single_call_reply,
        _record_single_call_result=_record_single_call_result,
        api_error_is_permanent_target_failure=api_error_is_permanent_target_failure,
        api_error_is_reply_not_allowed=api_error_is_reply_not_allowed,
        build_quote_lookup_post_ids=build_quote_lookup_post_ids,
        build_quote_tweet_reply_context=build_quote_tweet_reply_context,
        cache_tweet=cache_tweet,
        conversational_reply_pipeline_enabled=conversational_reply_pipeline_enabled,
        accounting=_daily_reply_accounting_owner(),
        evaluate_single_call_reply=evaluate_single_call_reply,
        get_quote_tweets_for_posts=get_quote_tweets_for_posts,
        get_tweet_by_id_cached=get_tweet_by_id_cached,
        in_api_cooldown=in_api_cooldown,
        is_probably_spam_or_not_worth_replying=is_probably_spam_or_not_worth_replying,
        lane_paused=lane_paused,
        log=log,
        log_ai_reply_posting_outcome=log_ai_reply_posting_outcome,
        log_event=log_event,
        mark_quote_spam_author=mark_quote_spam_author,
        now_epoch=now_epoch,
        quote_tweet_is_old_enough=quote_tweet_is_old_enough,
        record_api_error=record_api_error,
        reply_evaluations=_reply_evaluation_owner(),
        recovery_comparison_account_replies=_reply_history_owner().recovery_replies,
        reply_evidence_repository=reply_evidence_repository,
        valid_tweets_sorted_by_id=valid_tweets_sorted_by_id,
    )


# ---------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------

def _quote_schedule_owner() -> _runtime_state_helpers.QuoteSchedule:
    """Bind current external boundaries without runtime work or caller state."""
    return _runtime_state_helpers.QuoteSchedule(
        minimum_delay=POST_SLEEP_MIN,
        maximum_delay=POST_SLEEP_MAX,
        now_epoch=now_epoch,
        save_state=save_state,
        log=log,
    )


def next_quote_schedule_fields(from_epoch: int | None = None, *, delay: int | None = None) -> tuple[dict, int]:
    """Return the next quote schedule fields."""
    return _quote_schedule_owner().next_fields(from_epoch, delay=delay)


def schedule_next_quote_post(state: dict, from_epoch: int | None = None, *, save: bool = True) -> None:
    """Perform the schedule next quote post operation."""
    return _quote_schedule_owner().schedule(state, from_epoch, save=save)


def run_reply_lane_checks_for_tick(
    state: dict,
    current: int,
) -> tuple[int, int]:
    """Run one reply-lane tick using the check epochs in canonical state."""
    return _tick_coordination.run_reply_lane_checks_for_tick(
        state,
        current,
        AmbiguousRemotePostOutcome=AmbiguousRemotePostOutcome,
        ENABLE_AUTO_REPLIES=ENABLE_AUTO_REPLIES,
        ENABLE_QUOTE_TWEET_CHECKS=ENABLE_QUOTE_TWEET_CHECKS,
        MIN_SECONDS_BETWEEN_REPLIES=MIN_SECONDS_BETWEEN_REPLIES,
        QUOTE_CHECK_EVERY_SECONDS=QUOTE_CHECK_EVERY_SECONDS,
        QUOTE_CHECK_SPACING_RETRY_SECONDS=QUOTE_CHECK_SPACING_RETRY_SECONDS,
        REPLY_CHECK_EVERY_SECONDS=REPLY_CHECK_EVERY_SECONDS,
        UnrecoverableConfirmedReplyPersistenceError=UnrecoverableConfirmedReplyPersistenceError,
        ambiguous_remote_post_is_blocking=ambiguous_remote_post_is_blocking,
        log=log,
        log_event=log_event,
        maybe_reply_to_mentions=maybe_reply_to_mentions,
        maybe_reply_to_quote_tweets=maybe_reply_to_quote_tweets,
        save_state=save_state,
        scheduler_epoch_from_state=scheduler_epoch_from_state,
    )


def maintain_global_remote_write_barrier_tick(
    *,
    already_logged: bool,
) -> tuple[bool, bool]:
    """Maintain one fail-closed barrier tick and its one-shot logging state."""
    return _tick_coordination.maintain_global_remote_write_barrier_tick(
        already_logged=already_logged,
        ambiguous_remote_post_is_blocking=ambiguous_remote_post_is_blocking,
        durable_remote_write_safety_barrier_exists=durable_remote_write_safety_barrier_exists,
        log=log,
        remote_write_safety_protocol_is_active=remote_write_safety_protocol_is_active,
    )


def _run_due_quote_post_for_tick(
    lines_used: set,
    images_used: set,
    state: dict,
    current: int,
) -> None:
    """Handle quote timing and retries after the main loop's safety gates."""
    next_quote_epoch = int(state.get("next_quote_post_epoch", 0))
    if current >= next_quote_epoch:
        log.info("Due to post quote/image")

        if lane_paused("disable_quote_posts"):
            log.warning("Skipping quote/image post due to runtime control file; retrying in 5 minutes")
            state["next_quote_post_epoch"] = current + 300
            save_state(state)
        elif in_api_cooldown(state, scope="write"):
            log.warning("Skipping quote/image post due to X write API cooldown")
            schedule_next_quote_post(state, current)
        else:
            quote_posted = False
            report_bot_health_progress("quote_post")
            try:
                post_random_quote(lines_used, images_used, state)
                quote_posted = True
            except UnrecoverableConfirmedPostPersistenceError:
                quote_posted = True
                log.exception(
                    "Quote/image post was confirmed remotely but no complete durable "
                    "local representation survived; all remote writes are now blocked"
                )
            except ConfirmedPostLocalPersistenceError:
                quote_posted = True
                log.exception("Quote/image post was confirmed remotely but local recovery/persistence failed; not scheduling an error retry")
            except AmbiguousRemotePostOutcome:
                quote_posted = True
                log.exception(
                    "Quote/image remote outcome is ambiguous; the remote-write safety "
                    "barrier is active and no retry will be scheduled"
                )
            except ApiError as e:
                log.exception("Quote/image posting failed due to API error")
                record_api_error(state, e, "x", scope="write")
            except Exception:
                log.exception("Quote/image posting failed unexpectedly")
            report_bot_health_progress("main_loop")

            if not quote_posted:
                schedule_next_quote_post(state, current)
    else:
        log.debug(
            "Not due to post quote/image. seconds_until_next=%s",
            max(0, next_quote_epoch - current),
        )


def _run_due_meme_post_for_tick(state: dict, current: int) -> None:
    """Handle meme timing and retries after the main loop's safety gates."""
    if ENABLE_DAILY_MEME_POSTS:
        next_meme_epoch = int(state.get("next_meme_post_epoch", 0) or 0)

        if current >= next_meme_epoch:
            log.info("Due to post daily meme")

            seconds_since_quote = current - int(state.get("last_quote_post_epoch", 0) or 0)

            if lane_paused("disable_meme_posts"):
                log.warning("Skipping daily meme post due to runtime control file; retrying in 5 minutes")
                set_meme_delay_schedule(state, epoch=current + 300, mode="delayed_runtime_control")
            elif seconds_since_quote < MEME_MIN_SECONDS_AFTER_QUOTE_POST:
                log.info(
                    "Meme post due, but delaying because last quote post was %d seconds ago",
                    seconds_since_quote,
                )
                set_meme_delay_schedule(state, epoch=current + 1800, mode="delayed_recent_quote")
            elif in_api_cooldown(state, scope="write"):
                log.warning("Skipping daily meme post due to X write API cooldown")
                set_meme_delay_schedule(state, epoch=current + 3600, mode="delayed_write_api_cooldown")
            else:
                report_bot_health_progress("meme_post")
                try:
                    post_next_meme(state)
                except UnrecoverableConfirmedPostPersistenceError:
                    log.exception(
                        "Daily meme post was confirmed remotely but no complete durable "
                        "local representation survived; all remote writes are now blocked"
                    )
                except ConfirmedPostLocalPersistenceError:
                    log.exception("Daily meme post was confirmed remotely but local recovery/persistence failed; not scheduling an error retry")
                except AmbiguousRemotePostOutcome:
                    log.exception(
                        "Daily meme remote outcome is ambiguous; the remote-write safety "
                        "barrier is active and no retry will be scheduled"
                    )
                except ApiError as e:
                    log.exception("Daily meme posting failed due to API error")
                    record_api_error(state, e, "x", scope="write")
                    set_meme_delay_schedule(state, epoch=current + 3600, mode="delayed_api_error")
                except Exception:
                    log.exception("Daily meme posting failed unexpectedly")
                    set_meme_delay_schedule(state, epoch=current + 3600, mode="delayed_exception")
                report_bot_health_progress("main_loop")
        else:
            log.debug(
                "Not due to post daily meme. seconds_until_next=%s",
                max(0, next_meme_epoch - current),
            )


def _log_startup_configuration() -> None:
    """Log the current paths and settings after startup recovery."""
    log.info("Bot starting")
    log.info("Python executable=%s", sys.executable)
    log.info("Base dir=%s", BASE_DIR)
    log.info("Lines file=%s exists=%s", LINES_FILE, LINES_FILE.exists())
    log.info("Image glob=%s", IMAGE_GLOB)
    log.info("State file=%s", STATE_FILE)
    log.info("Log file=%s", LOG_FILE)
    log.info("Local config file=%s exists=%s", LOCAL_CONFIG_FILE, LOCAL_CONFIG_FILE.exists())
    log.info("Runtime control file=%s exists=%s", CONTROL_FILE, CONTROL_FILE.exists())

    log.info("Config: POST_SLEEP_MIN=%s POST_SLEEP_MAX=%s", POST_SLEEP_MIN, POST_SLEEP_MAX)
    log.info("Config: ENABLE_AUTO_REPLIES=%s", ENABLE_AUTO_REPLIES)
    log.info("Config: REPLY_CHECK_EVERY_SECONDS=%s", REPLY_CHECK_EVERY_SECONDS)
    log.info("Config: MAX_AUTO_REPLIES_PER_DAY=%s", MAX_AUTO_REPLIES_PER_DAY)
    log.info("Config: MAX_REPLIES_PER_AUTHOR_PER_DAY=%s", MAX_REPLIES_PER_AUTHOR_PER_DAY)
    log.info("Config: MAX_MENTIONS_PER_CHECK=%s", MAX_MENTIONS_PER_CHECK)
    log.info("Config: MENTIONS_MAX_PAGES_PER_CHECK=%s", MENTIONS_MAX_PAGES_PER_CHECK)
    log.info(
        "Config: author no-reply quarantine threshold=%s window_seconds=%s quarantine_seconds=%s",
        AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD,
        AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS,
        AUTHOR_NO_REPLY_QUARANTINE_SECONDS,
    )
    log.info("Config: MIN_SECONDS_BETWEEN_REPLIES=%s", MIN_SECONDS_BETWEEN_REPLIES)
    log.info("Config: ALWAYS_FETCH_PARENT_FOR_CONTEXT=%s", ALWAYS_FETCH_PARENT_FOR_CONTEXT)
    log.info("Config: SKIP_REPLIES_TO_OWN_AUTO_REPLIES=%s", SKIP_REPLIES_TO_OWN_AUTO_REPLIES)
    log.info("Config: THREAD_CONTEXT_MAX_DEPTH=%s", THREAD_CONTEXT_MAX_DEPTH)
    log.info(
        "Config: THREAD_CONTEXT_MAX_NETWORK_FETCHES=%s",
        THREAD_CONTEXT_MAX_NETWORK_FETCHES,
    )
    log.info("Config: THREAD_CONTEXT_MAX_CHARS_PER_POST=%s", THREAD_CONTEXT_MAX_CHARS_PER_POST)
    log.info("Config: THREAD_CONTEXT_MAX_TOTAL_CHARS=%s", THREAD_CONTEXT_MAX_TOTAL_CHARS)
    log.info("Config: TWEET_CACHE_MAX_AGE_SECONDS=%s", TWEET_CACHE_MAX_AGE_SECONDS)
    log.info("Config: TWEET_CACHE_MAX_ITEMS=%s", TWEET_CACHE_MAX_ITEMS)
    log.info("Config: STATE_BACKUP_COUNT=%s", STATE_BACKUP_COUNT)
    log.info(
        "Config: single_call_reply enabled=%s strategy_version=%s model=%s "
        "reasoning_effort=%s temperature=%s",
        single_call_reply.get("enabled"),
        SINGLE_CALL_STRATEGY_VERSION,
        SINGLE_CALL_MODEL,
        SINGLE_CALL_REASONING_EFFORT,
        SINGLE_CALL_TEMPERATURE,
    )

    log.info("Config: ENABLE_DAILY_MEME_POSTS=%s", ENABLE_DAILY_MEME_POSTS)
    log.info("Config: MEME_DIR=%s", MEME_DIR)
    log.info("Config: MEME_ANALYSIS_FILE=%s", MEME_ANALYSIS_FILE)
    log.info("Config: MEME_TRIGGER_AFTER_HOUR=%s", MEME_TRIGGER_AFTER_HOUR)
    log.info("Config: MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS=%s", MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS)
    log.info("Config: MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS=%s", MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS)
    log.info("Config: MEME_FALLBACK_HOUR=%s", MEME_FALLBACK_HOUR)
    log.info("Config: MEME_FALLBACK_MINUTE=%s", MEME_FALLBACK_MINUTE)
    log.info("Config: MEME_POST_TEXT=%r", MEME_POST_TEXT)
    log.info("Config: MEME_SCHEDULE_VERSION=%s", MEME_SCHEDULE_VERSION)
    log.info("Config: RESET_MEME_CYCLE_WHEN_ALL_POSTED=%s", RESET_MEME_CYCLE_WHEN_ALL_POSTED)

    log.info("Config: ENABLE_QUOTE_TWEET_CHECKS=%s", ENABLE_QUOTE_TWEET_CHECKS)
    log.info("Config: QUOTE_CHECK_EVERY_SECONDS=%s", QUOTE_CHECK_EVERY_SECONDS)
    log.info("Config: QUOTE_CHECK_SPACING_RETRY_SECONDS=%s", QUOTE_CHECK_SPACING_RETRY_SECONDS)
    log.info("Config: QUOTE_REPLY_DELAY_SECONDS=%s", QUOTE_REPLY_DELAY_SECONDS)
    log.info("Config: MAX_QUOTE_POSTS_PER_CHECK=%s", MAX_QUOTE_POSTS_PER_CHECK)
    log.info("Config: QUOTE_LOOKUP_API_MAX_RESULTS=%s", QUOTE_LOOKUP_API_MAX_RESULTS)
    log.info("Config: QUOTE_LOOKUP_MAX_PAGES_PER_POST=%s", QUOTE_LOOKUP_MAX_PAGES_PER_POST)
    log.info("Config: MAX_QUOTE_REPLIES_PER_DAY=%s", MAX_QUOTE_REPLIES_PER_DAY)
    log.info("Config: QUOTE_POST_LOOKBACK_MAIN_POSTS=%s", QUOTE_POST_LOOKBACK_MAIN_POSTS)
    log.info("Config: RECENT_OWN_POST_IDS_MAX=%s", RECENT_OWN_POST_IDS_MAX)
    log.info("Config: EXTRA_QUOTE_WATCH_FILE=%s", EXTRA_QUOTE_WATCH_FILE)
    log.info("Config: MAX_EXTRA_QUOTE_WATCH_POSTS=%s", MAX_EXTRA_QUOTE_WATCH_POSTS)
    log.info("Config: HOT_POST_REPLY_USE_SINCE_ID=%s", HOT_POST_REPLY_USE_SINCE_ID)
    log.info("Config: HOT_POST_REPLY_SEARCH_API_MAX_RESULTS=%s", HOT_POST_REPLY_SEARCH_API_MAX_RESULTS)
    log.info("Config: HOT_POST_REPLY_SEARCH_MAX_PAGES_PER_CHECK=%s", HOT_POST_REPLY_SEARCH_MAX_PAGES_PER_CHECK)
    log.info("Config: HOT_POST_REPLY_FULL_RESCAN_EVERY_CHECKS=%s", HOT_POST_REPLY_FULL_RESCAN_EVERY_CHECKS)
    log.info("Config: X_BEARER_TOKEN_SET=%s", bool(X_BEARER_TOKEN))


def main() -> None:
    """Recover local state and run the continuous posting and reply scheduler."""
    require_production_bootstrap()
    random.seed()
    acquire_instance_lock()
    report_bot_health_progress("startup")
    report_bot_health_progress("recovery")
    # The durable namespace must be proved only while this process owns the
    # installation lock.  Checking it before the lock leaves a stale-success
    # interval in which a cooperating maintenance process can change the very
    # files whose presence authorises startup.
    require_established_installation_after_ledger_recovery()
    if not global_remote_writes_paused():
        try:
            resume_interrupted_confirmed_media_retirement_if_present()
        except Exception:
            log.critical(
                "Interrupted confirmed-media retirement could not be resumed "
                "at startup; every remote lane remains blocked",
                exc_info=True,
            )
    reconcile_runtime_historical_context_state()

    _log_startup_configuration()

    images_at_start = glob(IMAGE_GLOB)
    log.info("Images found at startup=%d", len(images_at_start))

    if ENABLE_DAILY_MEME_POSTS:
        meme_candidates_at_start = list_meme_candidates()
        log.info("Meme candidates found at startup=%d", len(meme_candidates_at_start))

    validate_original_editorial_shadow_startup()

    with open(LINES_FILE, encoding="utf-8") as f:
        quote_lines_for_history = f.readlines()
    lines_used = load_quote_used_hashes(quote_lines_for_history)
    images_used = load_image_used_basenames(current_image_paths())
    state = load_runtime_state()
    active_backlog = state.get("mention_backlog", {})
    active_quarantines = state.get("author_evaluation_quarantines", {})
    log.info(
        "Mention backlog state loaded active=%s pages_completed=%s highest_mention_id=%s continuation_token_present=%s pending_candidates=%s",
        bool(active_backlog),
        active_backlog.get("pages_completed", 0) if isinstance(active_backlog, dict) else 0,
        active_backlog.get("highest_mention_id") if isinstance(active_backlog, dict) else None,
        bool(active_backlog.get("next_token")) if isinstance(active_backlog, dict) else False,
        len(state.get("mention_pending_candidates", {})) if isinstance(state.get("mention_pending_candidates"), dict) else 0,
    )
    log.info(
        "Author evaluation quarantine state loaded active=%s",
        sum(
            1
            for record in (
                active_quarantines.values()
                if isinstance(active_quarantines, dict)
                else []
            )
            if isinstance(record, dict)
            and int(record.get("quarantine_until_epoch", 0) or 0) > now_epoch()
        ),
    )
    startup_current = now_epoch()
    reconcile_startup_main_post_receipts(
        lines_used,
        images_used,
        state,
        startup_current,
    )
    if not global_remote_writes_paused():
        reconcile_confirmed_transactions_before_global_barrier(
            lines_used,
            images_used,
            state,
            startup_current,
        )


    seed_recent_own_post_ids_from_cache(state)
    save_state(state)

    current = now_epoch()
    _, reply_epoch_changed = scheduler_epoch_from_state(
        state,
        "last_reply_check_epoch",
        current=current,
    )
    _, quote_epoch_changed = scheduler_epoch_from_state(
        state,
        "last_quote_tweet_check_epoch",
        current=current,
    )
    if reply_epoch_changed or quote_epoch_changed:
        save_state(state)

    if not state.get("next_quote_post_epoch"):
        log.info("No next_quote_post_epoch found; first quote/image post will happen immediately")
        state["next_quote_post_epoch"] = 0
        save_state(state)
    else:
        log.info(
            "Existing next_quote_post_epoch=%s, human=%s",
            state.get("next_quote_post_epoch"),
            datetime.fromtimestamp(int(state["next_quote_post_epoch"])).strftime("%Y-%m-%d %H:%M:%S"),
        )

    if ENABLE_DAILY_MEME_POSTS:
        ensure_meme_schedule_initialized(state)

    log.info("Bot started successfully")
    report_bot_health_progress("main_loop")

    ambiguity_pause_logged = False
    maintenance_pause_logged = global_remote_writes_paused()
    while True:
        report_bot_health_progress("main_loop", loop_started=True)
        maintenance_paused = global_remote_writes_paused()
        if not maintenance_paused:
            try:
                resume_interrupted_confirmed_media_retirement_if_present()
            except Exception:
                log.critical(
                    "Interrupted confirmed-media retirement could not be "
                    "resumed; every remote lane remains blocked",
                    exc_info=True,
                )
        try:
            resume_source_receipt_retirement_for_control_snapshot(
                maintenance_paused=maintenance_paused,
            )
        except Exception:
            log.critical(
                "Interrupted source-receipt retirement could not be resumed; "
                "all remote lanes remain blocked",
                exc_info=True,
            )
        if not maintenance_paused:
            try:
                reconciled = reconcile_confirmed_transactions_before_global_barrier(
                    lines_used,
                    images_used,
                    state,
                )
            except Exception:
                log.critical(
                    "A locally confirmed remote transaction could not be "
                    "reconciled before the global barrier; all remote lanes "
                    "remain blocked",
                    exc_info=True,
                )
            else:
                if any(reconciled.values()):
                    log.warning(
                        "Completed local confirmed-transaction recovery before "
                        "remote scheduling: %s",
                        {key: value for key, value in reconciled.items() if value},
                    )


        ambiguity_blocked, ambiguity_pause_logged = (
            maintain_global_remote_write_barrier_tick(
                already_logged=ambiguity_pause_logged,
            )
        )

        if maintenance_paused:
            if not maintenance_pause_logged:
                log.warning(
                    "Global runtime control pause is active; all remote-write lanes "
                    "remain idle"
                )
            maintenance_pause_logged = True
            report_bot_health_progress(
                "paused",
                paused=True,
                remote_write_blocked=ambiguity_blocked,
                loop_completed=True,
            )
            sleep(60)
            continue
        if maintenance_pause_logged:
            log.info("Global runtime control pause cleared; resuming scheduled lanes")
        maintenance_pause_logged = False
        report_bot_health_progress("main_loop", paused=False)

        if ambiguity_blocked:
            report_bot_health_progress(
                "remote_write_blocked",
                remote_write_blocked=True,
                loop_completed=True,
            )
            sleep(60)
            continue
        report_bot_health_progress("main_loop", remote_write_blocked=False)

        current = now_epoch()
        log.debug("Main loop tick. epoch=%s", current)

        report_bot_health_progress("historical_context")
        safely_process_due_historical_context_obligations(
            limit=1,
            runtime_state=state,
        )
        report_bot_health_progress("main_loop")
        if ambiguous_remote_post_is_blocking():
            report_bot_health_progress(
                "remote_write_blocked",
                remote_write_blocked=True,
                loop_completed=True,
            )
            continue

        report_bot_health_progress("reply_checks")
        run_reply_lane_checks_for_tick(state, current)
        report_bot_health_progress("main_loop")
        if ambiguous_remote_post_is_blocking():
            report_bot_health_progress(
                "remote_write_blocked",
                remote_write_blocked=True,
                loop_completed=True,
            )
            continue

        _run_due_quote_post_for_tick(lines_used, images_used, state, current)

        if ambiguous_remote_post_is_blocking():
            report_bot_health_progress(
                "remote_write_blocked",
                remote_write_blocked=True,
                loop_completed=True,
            )
            continue

        _run_due_meme_post_for_tick(state, current)

        log.debug("Sleeping for 60 seconds")
        report_bot_health_progress("sleep", loop_completed=True)
        sleep(60)


# ---------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------

def _self_test_ok(label: str, ok: bool, detail: str = "") -> bool:
    return _self_test._self_test_ok(
        label,
        ok,
        detail,
        log=log,
    )


def _self_test_warn(label: str, ok: bool, detail: str = "") -> None:
    return _self_test._self_test_warn(
        label,
        ok,
        detail,
        log=log,
    )


def run_self_test() -> int:
    """Run local checks without posting or calling X or OpenAI."""
    return _self_test.run_self_test(
        ACCESS_SECRET=ACCESS_SECRET,
        ACCESS_TOKEN=ACCESS_TOKEN,
        BASE_DIR=BASE_DIR,
        CONSUMER_KEY=CONSUMER_KEY,
        CONSUMER_SECRET=CONSUMER_SECRET,
        CONTROL_FILE=CONTROL_FILE,
        ENABLE_AUTO_REPLIES=ENABLE_AUTO_REPLIES,
        ENABLE_DAILY_MEME_POSTS=ENABLE_DAILY_MEME_POSTS,
        EXTRA_QUOTE_WATCH_FILE=EXTRA_QUOTE_WATCH_FILE,
        IMAGE_GLOB=IMAGE_GLOB,
        LINES_FILE=LINES_FILE,
        LOCAL_CONFIG_ALLOWED_KEYS=LOCAL_CONFIG_ALLOWED_KEYS,
        LOCAL_CONFIG_FILE=LOCAL_CONFIG_FILE,
        MAX_AUTO_REPLIES_PER_DAY=MAX_AUTO_REPLIES_PER_DAY,
        MAX_QUOTE_REPLIES_PER_DAY=MAX_QUOTE_REPLIES_PER_DAY,
        MEME_ANALYSIS_FILE=MEME_ANALYSIS_FILE,
        MEME_DIR=MEME_DIR,
        MIN_SECONDS_BETWEEN_REPLIES=MIN_SECONDS_BETWEEN_REPLIES,
        MY_USER_ID=MY_USER_ID,
        OPENAI_API_KEY=OPENAI_API_KEY,
        QUOTE_CHECK_EVERY_SECONDS=QUOTE_CHECK_EVERY_SECONDS,
        REPLY_CHECK_EVERY_SECONDS=REPLY_CHECK_EVERY_SECONDS,
        STATE_FILE=STATE_FILE,
        X_BEARER_TOKEN=X_BEARER_TOKEN,
        _runtime_config_namespace=_runtime_config_namespace,
        _self_test_ok=_self_test_ok,
        _self_test_warn=_self_test_warn,
        glob=glob,
        json=json,
        list_meme_candidates=list_meme_candidates,
        load_control=load_control,
        load_extra_quote_watch_post_ids=load_extra_quote_watch_post_ids,
        load_validated_local_config_overrides=load_validated_local_config_overrides,
        log=log,
        require_production_bootstrap=require_production_bootstrap,
        single_call_reply=single_call_reply,
        validate_runtime_config_values=validate_runtime_config_values,
    )


def run_test_cycle() -> int:
    """Run one local integration-test pass without entering the posting loop."""
    return _cli_execution.run_test_cycle(
        AmbiguousRemotePostOutcome=AmbiguousRemotePostOutcome,
        BASE_DIR=BASE_DIR,
        LOG_FILE=LOG_FILE,
        OPENAI_BASE=OPENAI_BASE,
        STATE_FILE=STATE_FILE,
        UnrecoverableConfirmedReplyPersistenceError=UnrecoverableConfirmedReplyPersistenceError,
        X_BASE=X_BASE,
        X_UPLOAD_BASE=X_UPLOAD_BASE,
        acquire_instance_lock=acquire_instance_lock,
        ambiguous_remote_post_is_blocking=ambiguous_remote_post_is_blocking,
        block_if_ambiguous_remote_post=block_if_ambiguous_remote_post,
        load_runtime_state=load_runtime_state,
        log=log,
        log_event=log_event,
        maybe_reply_to_mentions=maybe_reply_to_mentions,
        maybe_reply_to_quote_tweets=maybe_reply_to_quote_tweets,
        reconcile_runtime_historical_context_state=reconcile_runtime_historical_context_state,
        require_established_installation_after_ledger_recovery=require_established_installation_after_ledger_recovery,
        require_production_bootstrap=require_production_bootstrap,
        require_test_mode=require_test_mode,
        save_state=save_state,
        wait_for_durable_barrier_before_one_shot_exit=wait_for_durable_barrier_before_one_shot_exit,
    )


def run_test_main_tick() -> int:
    """Run the production reply-lane tick once for local integration tests."""
    return _cli_execution.run_test_main_tick(
        acquire_instance_lock=acquire_instance_lock,
        ambiguous_remote_post_is_blocking=ambiguous_remote_post_is_blocking,
        block_if_ambiguous_remote_post=block_if_ambiguous_remote_post,
        load_runtime_state=load_runtime_state,
        log=log,
        now_epoch=now_epoch,
        reconcile_runtime_historical_context_state=reconcile_runtime_historical_context_state,
        report_bot_health_progress=report_bot_health_progress,
        require_established_installation_after_ledger_recovery=require_established_installation_after_ledger_recovery,
        require_production_bootstrap=require_production_bootstrap,
        require_test_mode=require_test_mode,
        run_reply_lane_checks_for_tick=run_reply_lane_checks_for_tick,
        save_state=save_state,
        scheduler_epoch_from_state=scheduler_epoch_from_state,
        wait_for_durable_barrier_before_one_shot_exit=wait_for_durable_barrier_before_one_shot_exit,
    )


def require_test_mode(command_name: str) -> bool:
    """Require the immutable import-time test-mode safety configuration."""
    return _cli_execution.require_test_mode(
        command_name,
        IMPORT_TIME_TEST_MODE=IMPORT_TIME_TEST_MODE,
        log=log,
    )


def prepare_test_main_post_state(state: dict) -> None:
    """Prepare test main post state."""
    return _runtime_state_helpers.prepare_test_main_post_state(
        state,
        ENABLE_DAILY_MEME_POSTS=ENABLE_DAILY_MEME_POSTS,
        ensure_meme_schedule_initialized=ensure_meme_schedule_initialized,
    )


def wait_for_durable_barrier_before_one_shot_exit(*, lane: str) -> None:
    """Keep a one-shot posting process alive while its only barrier is memory."""
    return _cli_execution.wait_for_durable_barrier_before_one_shot_exit(
        lane=lane,
        durable_remote_write_safety_barrier_exists=durable_remote_write_safety_barrier_exists,
        log=log,
        remote_write_safety_incident_is_latched=remote_write_safety_incident_is_latched,
        sleep=sleep,
    )


def run_test_post_quote() -> int:
    """Run one quote/image post cycle for local integration tests."""
    return _cli_execution.run_test_post_quote(
        AmbiguousRemotePostOutcome=AmbiguousRemotePostOutcome,
        ApiError=ApiError,
        ConfirmedPostLocalPersistenceError=ConfirmedPostLocalPersistenceError,
        LINES_FILE=LINES_FILE,
        UnrecoverableConfirmedPostPersistenceError=UnrecoverableConfirmedPostPersistenceError,
        acquire_instance_lock=acquire_instance_lock,
        block_if_ambiguous_remote_post=block_if_ambiguous_remote_post,
        current_image_paths=current_image_paths,
        lane_paused=lane_paused,
        load_image_used_basenames=load_image_used_basenames,
        load_quote_used_hashes=load_quote_used_hashes,
        load_runtime_state=load_runtime_state,
        log=log,
        post_random_quote=post_random_quote,
        prepare_test_main_post_state=prepare_test_main_post_state,
        reconcile_runtime_historical_context_state=reconcile_runtime_historical_context_state,
        record_api_error=record_api_error,
        require_established_installation_after_ledger_recovery=require_established_installation_after_ledger_recovery,
        require_production_bootstrap=require_production_bootstrap,
        require_test_mode=require_test_mode,
        save_state=save_state,
        wait_for_durable_barrier_before_one_shot_exit=wait_for_durable_barrier_before_one_shot_exit,
    )


def run_test_post_meme() -> int:
    """Run one daily meme post cycle for local integration tests."""
    return _cli_execution.run_test_post_meme(
        AmbiguousRemotePostOutcome=AmbiguousRemotePostOutcome,
        ApiError=ApiError,
        ConfirmedPostLocalPersistenceError=ConfirmedPostLocalPersistenceError,
        UnrecoverableConfirmedPostPersistenceError=UnrecoverableConfirmedPostPersistenceError,
        acquire_instance_lock=acquire_instance_lock,
        block_if_ambiguous_remote_post=block_if_ambiguous_remote_post,
        lane_paused=lane_paused,
        load_runtime_state=load_runtime_state,
        log=log,
        post_next_meme=post_next_meme,
        prepare_test_main_post_state=prepare_test_main_post_state,
        reconcile_runtime_historical_context_state=reconcile_runtime_historical_context_state,
        record_api_error=record_api_error,
        require_established_installation_after_ledger_recovery=require_established_installation_after_ledger_recovery,
        require_production_bootstrap=require_production_bootstrap,
        require_test_mode=require_test_mode,
        save_state=save_state,
        wait_for_durable_barrier_before_one_shot_exit=wait_for_durable_barrier_before_one_shot_exit,
    )


def run_cli(argv: list[str] | tuple[str, ...] | None = None) -> int | None:
    """Validate one complete command line, then bootstrap and dispatch it."""
    return _cli_execution.run_cli(
        argv,
        CLI_USAGE=CLI_USAGE,
        CliUsageError=CliUsageError,
        IMPORT_TIME_CLI_ARGUMENTS=IMPORT_TIME_CLI_ARGUMENTS,
        IMPORT_TIME_TEST_MODE=IMPORT_TIME_TEST_MODE,
        TEST_MODE_REQUIRED_CLI_FLAGS=TEST_MODE_REQUIRED_CLI_FLAGS,
        initialise_installation=initialise_installation,
        main=main,
        parse_cli_mode=parse_cli_mode,
        production_bootstrap=production_bootstrap,
        run_self_test=run_self_test,
        run_test_cycle=run_test_cycle,
        run_test_main_tick=run_test_main_tick,
        run_test_post_meme=run_test_post_meme,
        run_test_post_quote=run_test_post_quote,
        sys=sys,
    )


if __name__ == "__main__":
    try:
        cli_status = run_cli()
        if cli_status is not None:
            sys.exit(cli_status)
    except KeyboardInterrupt:
        report_bot_health_progress("shutdown")
        log.warning("Bot stopped by KeyboardInterrupt")
    except Exception:
        log.exception("Bot crashed with unhandled exception")
        raise
