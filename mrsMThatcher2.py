#!/usr/bin/env python3
"""Run the production MrsMThatcher posting and conversational-reply bot."""


from __future__ import annotations

import base64
import hashlib
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
    return urlunsplit((parsed.scheme.lower(), netloc, "", "", ""))


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
from datetime import datetime, timedelta
from glob import glob
from logging.handlers import RotatingFileHandler
from pathlib import Path
from time import monotonic, sleep
from urllib.parse import unquote
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import requests
from requests_oauthlib import OAuth1
from urllib3.util import Timeout

import engagement_question_experiment as engagement_question_trial
import mrs_bot_image_scoring as _image_scoring
import mrs_bot_original_editorial as _original_editorial
import mrs_bot_generated_identity as _generated_identity
import mrs_bot_asset_metadata as _asset_metadata
import mrs_bot_quote_candidates as _quote_candidates
import mrs_bot_image_selection as _image_selection
import mrs_bot_quote_posting as _quote_posting
import mrs_bot_daily_meme as _daily_meme
import mrs_bot_legacy_reply_validation as _legacy_reply_validation
import mrs_bot_reply_state as _reply_state
import mrs_bot_reply_generation as _reply_generation
import mrs_bot_reply_receipt_values as _reply_receipt_values
import mrs_bot_reply_reconciliation as _reply_reconciliation
import mrs_bot_reply_delivery as _reply_delivery
import mrs_bot_normal_reply_cycle as _normal_reply_cycle
import mrs_bot_quote_reply_cycle as _quote_reply_cycle
import mrs_bot_quote_discovery as _quote_discovery
import mrs_bot_hot_post_discovery as _hot_post_discovery
import mrs_bot_mention_discovery as _mention_discovery
import mrs_bot_mention_authority as _mention_authority
import mrs_bot_reply_evaluation_state as _reply_evaluation_state
import mrs_bot_tweet_lookup_cache as _tweet_lookup_cache
import mrs_bot_reply_context as _reply_context
import mrs_bot_reply_native_media as _reply_native_media
import mrs_bot_reply_lane_policy as _reply_lane_policy
import mrs_bot_runtime_control as _runtime_control
import mrs_bot_api_cooldowns as _api_cooldowns
import mrs_bot_x_pagination as _x_pagination
import mrs_bot_request_route_values as _request_route_values
import mrs_bot_x_response_diagnostics as _x_response_diagnostics
import mrs_bot_tick_coordination as _tick_coordination
import mrs_bot_durable_json_io as _durable_json_io
import mrs_bot_state_value_normalisation as _state_value_normalisation

from single_call_reply import (
    MAX_IMAGE_BYTES as SINGLE_CALL_MAX_IMAGE_BYTES,
    MAX_RECENT_ACCOUNT_REPLIES,
    MAX_REPLY_SENTENCES,
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

# X's quote_tweets endpoint commonly expects at least 10 max_results.
QUOTE_LOOKUP_API_MAX_RESULTS = 10
QUOTE_LOOKUP_MAX_PAGES_PER_POST = 3
QUOTE_REPEATED_CURSOR_BACKOFF_SECONDS = 21600
QUOTE_REPEATED_CURSOR_SUPPRESSION_MAX_ENTRIES = 64

QUOTE_CHECK_STATUS_CHECKED = "checked"
QUOTE_CHECK_STATUS_POSTED = "posted"
QUOTE_CHECK_STATUS_SKIPPED_SPACING = "skipped_spacing"
QUOTE_CHECK_STATUS_SKIPPED_CAP = "skipped_cap"
QUOTE_CHECK_STATUS_SKIPPED_COOLDOWN = "skipped_cooldown"
QUOTE_CHECK_STATUS_DISABLED = "disabled"

NORMAL_CHECK_STATUS_CHECKED = "checked"
NORMAL_CHECK_STATUS_POSTED = "posted"
NORMAL_CHECK_STATUS_SKIPPED_SPACING = "skipped_spacing"
NORMAL_CHECK_STATUS_SKIPPED_CAP = "skipped_cap"
NORMAL_CHECK_STATUS_SKIPPED_COOLDOWN = "skipped_cooldown"
NORMAL_CHECK_STATUS_DISABLED = "disabled"
NORMAL_CHECK_STATUS_API_ERROR = "api_error"

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
GENERATED_IMAGE_ORIGIN_QUOTE_BOOST = 4

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
ENABLE_GENERATED_IMAGE_POOL = False
GENERATED_IMAGE_DIR = str(BASE_DIR / "generated_review_approved_images")
GENERATED_IMAGE_GLOB = "*.png"
GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN = 2
QUOTE_ANALYSIS_FILE = BASE_DIR / "quote_analysis.json"
IMAGE_ANALYSIS_FILE = BASE_DIR / "image_analysis.json"
GENERATED_IMAGE_ANALYSIS_FILE = str(BASE_DIR / "generated_image_analysis.json")
ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING = False
ORIGINAL_EDITORIAL_ANALYSIS_FILE = str(BASE_DIR / "original_image_editorial_analysis_experiment_v1.json")
ORIGINAL_EDITORIAL_SHADOW_WEIGHT = 0.32
ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT = 4.0
ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING = False
ENABLE_GENERATED_IDENTITY_POLICY_SCORING = False
GENERATED_IDENTITY_AUDIT_FILE = str(BASE_DIR / "generated_image_identity_dependence_audit.json")
GENERATED_IDENTITY_SHADOW_SMALL_PENALTY = 6.0
GENERATED_IDENTITY_SHADOW_STRONG_PENALTY = 15.0
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
engagement_question_experiment_enabled = False
engagement_question_experiment_plan_path = (
    "engagement_question_experiment/active_plan.json"
)
engagement_question_notification_output_path = ""
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
MAX_REASONABLE_STATE_EPOCH = 4_102_531_200
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
STATE_READER_VERSION = 4
STATE_MINIMUM_READER_VERSION = 4
ENGAGEMENT_QUESTION_EXPERIMENT_STATE_MINIMUM_READER_VERSION = 3
STATE_READER_COMPATIBILITY_FENCE = {
    "__mrs_state_reader_compatibility_fence__": STATE_MINIMUM_READER_VERSION,
}
STATE_PREVIOUS_READER_COMPATIBILITY_FENCES = (
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
_MANAGED_LOG_HANDLER_ATTR = "_mrs_mthatcher_managed_handler"


def remove_managed_log_handlers(logger: logging.Logger) -> None:
    """Detach and close handlers installed by this module."""
    for handler in list(logger.handlers):
        if not getattr(handler, _MANAGED_LOG_HANDLER_ATTR, False):
            continue
        logger.removeHandler(handler)
        handler.close()


def mark_managed_log_handler(handler: logging.Handler, kind: str) -> logging.Handler:
    """Mark a logging handler as owned by this module."""
    setattr(handler, _MANAGED_LOG_HANDLER_ATTR, True)
    setattr(handler, "_mrs_mthatcher_handler_kind", kind)
    return handler


def setup_logging(
    *,
    log_path: Path | None = None,
    configure_file_logging: bool = True,
) -> logging.Logger:
    """Configure console and optional rotating-file logging."""
    target_log = Path(log_path).expanduser() if log_path is not None else LOG_FILE
    if configure_file_logging and os.getenv("PYTEST_CURRENT_TEST") and path_is_same_or_child(target_log, PRODUCTION_BASE_DIR):
        raise RuntimeError(f"Refusing to attach pytest process to production log: {target_log}")
    if configure_file_logging:
        target_log.parent.mkdir(parents=True, exist_ok=True)

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)

    logger = logging.getLogger("mrsMThatcher")
    logger.setLevel(level)
    logger.propagate = False
    remove_managed_log_handlers(logger)

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s %(funcName)s:%(lineno)d - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = mark_managed_log_handler(logging.StreamHandler(sys.stdout), "console")
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    if configure_file_logging:
        file_handler = mark_managed_log_handler(
            RotatingFileHandler(
                target_log,
                maxBytes=PRODUCTION_LOG_MAX_BYTES,
                backupCount=PRODUCTION_LOG_BACKUP_COUNT,
            ),
            "file",
        )
        setattr(file_handler, "_mrs_mthatcher_log_path", str(target_log.resolve()))
        file_handler.setLevel(level)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    logger.debug(
        "Logging initialised. LOG_LEVEL=%s LOG_FILE=%s",
        level_name,
        target_log if configure_file_logging else "<disabled>",
    )
    return logger


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
_OFD_LOCK_FORMAT = "hhqqi"
_BOT_HEALTH_REPORTER: BotHealthReporter | None = None
_BOT_HEALTH_LOGGING_OBSERVER: HealthLoggingObserver | None = None


def initialise_bot_health_reporting() -> None:
    """Initialise fail-open telemetry after production logging is ready."""

    global _BOT_HEALTH_LOGGING_OBSERVER, _BOT_HEALTH_REPORTER
    if _BOT_HEALTH_REPORTER is not None:
        return
    if SELF_TEST_REQUESTED or INITIALISE_REQUESTED:
        return
    try:
        health_path = health_file_path_from_environment(
            test_mode=TEST_MODE,
            test_base_dir=BASE_DIR if TEST_MODE else None,
        )
        if health_path is None:
            return
        reporter = BotHealthReporter(
            health_path,
            write_failure_callback=lambda message: log.warning("%s", message),
        )
        observer = HealthLoggingObserver(reporter)
        _BOT_HEALTH_REPORTER = reporter
        _BOT_HEALTH_LOGGING_OBSERVER = observer
        log.addHandler(observer)
    except Exception:
        if TEST_MODE:
            raise
        log.warning(
            "Bot health telemetry could not be initialised; bot operation continues",
            exc_info=True,
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

    reporter = _BOT_HEALTH_REPORTER
    if reporter is None:
        return
    try:
        reporter.progress(
            phase,
            paused=paused,
            remote_write_blocked=remote_write_blocked,
            loop_started=loop_started,
            loop_completed=loop_completed,
        )
    except Exception:
        pass


def instance_lock_abstract_socket_name(base_dir: Path | None = None) -> bytes:
    """Return one Linux abstract-socket name bound to the state directory."""
    root = os.path.abspath(os.fspath(base_dir or BASE_DIR))
    identity = os.stat(root, follow_symlinks=True)
    if not stat.S_ISDIR(identity.st_mode):
        raise RuntimeError("Instance-lock state root is not a directory")
    return instance_lock_abstract_socket_name_for_identity(
        int(identity.st_dev),
        int(identity.st_ino),
    )


def instance_lock_abstract_socket_name_for_identity(
    device: int,
    inode: int,
) -> bytes:
    """Return one supplementary singleton name for a directory identity."""

    identity_bytes = (
        f"dev={int(device)};ino={int(inode)}"
    ).encode("ascii")
    digest = hashlib.sha256(identity_bytes).hexdigest()[:40].encode("ascii")
    return b"\0mrsMThatcher-instance-" + digest


def ofd_lock_record(lock_type: int) -> bytes:
    """Return one one-byte-range Linux OFD lock request."""
    return struct.pack(
        _OFD_LOCK_FORMAT,
        lock_type,
        os.SEEK_SET,
        0,
        1,
        0,
    )


def descriptor_owns_exclusive_flock(
    descriptor: int,
    *,
    expected_device: int,
    expected_inode: int,
) -> bool:
    """Return whether Linux fdinfo binds an exclusive flock to this exact fd."""

    try:
        fdinfo = Path(f"/proc/self/fdinfo/{int(descriptor)}").read_text(
            encoding="ascii",
        )
    except (OSError, UnicodeError):
        return False
    expected_major = os.major(int(expected_device))
    expected_minor = os.minor(int(expected_device))
    for line in fdinfo.splitlines():
        fields = line.split()
        if (
            len(fields) != 9
            or fields[0] != "lock:"
            or fields[2:5] != ["FLOCK", "ADVISORY", "WRITE"]
            or fields[7:] != ["0", "EOF"]
        ):
            continue
        try:
            lock_pid = int(fields[5])
            major_text, minor_text, inode_text = fields[6].split(":", 2)
            lock_major = int(major_text, 16)
            lock_minor = int(minor_text, 16)
            lock_inode = int(inode_text)
        except (TypeError, ValueError):
            continue
        if (
            lock_pid == os.getpid()
            and lock_major == expected_major
            and lock_minor == expected_minor
            and lock_inode == int(expected_inode)
        ):
            return True
    return False


def test_mode_excludes_live_remote_writes() -> bool:
    """Return whether test mode uses only explicitly local fake endpoints."""
    endpoints = [X_BASE, X_UPLOAD_BASE]
    if single_call_reply.get("enabled") is True:
        endpoints.append(OPENAI_BASE)
    return (
        TEST_MODE
        and _configured_x_request_is_sealed_test_loopback()
        and os.getenv("MRS_ALLOW_LIVE_ENDPOINTS_IN_TEST")
        != LIVE_ENDPOINT_TEST_OVERRIDE_PHRASE
        and all(
            endpoint_is_loopback(value)
            for value in endpoints
        )
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
    if test_mode_excludes_live_remote_writes():
        return
    if (
        _LOCK_FH is None
        or _LOCK_ACQUISITION_IDENTITY is None
        or _STATE_DIR_LOCK_FD is None
        or _STATE_DIR_LOCK_IDENTITY is None
    ):
        raise RuntimeError(
            f"{operation} requires the established process-lifetime instance lock"
        )
    if _LOCK_SOCKET is None or _LOCK_SOCKET_NAME is None:
        raise RuntimeError(
            f"{operation} requires the non-replaceable process singleton"
        )
    try:
        socket_name = _LOCK_SOCKET.getsockname()
    except OSError as exc:
        raise RuntimeError(
            f"{operation} refused because the process singleton is unavailable"
        ) from exc
    if socket_name != _LOCK_SOCKET_NAME:
        raise RuntimeError(
            f"{operation} refused because the process singleton identity changed"
        )

    expected_directory_device, expected_directory_inode = (
        _STATE_DIR_LOCK_IDENTITY
    )
    directory_opened = os.fstat(_STATE_DIR_LOCK_FD)
    directory_current = os.stat(BASE_DIR, follow_symlinks=True)
    if (
        not stat.S_ISDIR(directory_opened.st_mode)
        or not stat.S_ISDIR(directory_current.st_mode)
        or directory_opened.st_dev != expected_directory_device
        or directory_opened.st_ino != expected_directory_inode
        or directory_current.st_dev != expected_directory_device
        or directory_current.st_ino != expected_directory_inode
    ):
        raise RuntimeError(
            f"{operation} refused because the state-directory lock identity changed"
        )
    if not descriptor_owns_exclusive_flock(
        _STATE_DIR_LOCK_FD,
        expected_device=expected_directory_device,
        expected_inode=expected_directory_inode,
    ):
        raise RuntimeError(
            f"{operation} refused because the designated state-directory "
            "descriptor does not own its exclusive lock"
        )
    directory_probe = os.open(
        BASE_DIR,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        probe_identity = os.fstat(directory_probe)
        if (
            probe_identity.st_dev != expected_directory_device
            or probe_identity.st_ino != expected_directory_inode
        ):
            raise RuntimeError(
                f"{operation} refused because the state-directory path was replaced"
            )
        try:
            fcntl.flock(
                directory_probe,
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError:
            pass
        else:
            fcntl.flock(directory_probe, fcntl.LOCK_UN)
            raise RuntimeError(
                f"{operation} refused because the process-lifetime "
                "state-directory lock is not held"
            )
    finally:
        os.close(directory_probe)

    descriptor = _LOCK_FH.fileno()
    expected_device, expected_inode, expected_pid = _LOCK_ACQUISITION_IDENTITY
    held = os.fstat(descriptor)
    current = os.lstat(LOCK_FILE)
    expected_contents = f"pid={expected_pid}\n".encode("ascii")
    contents = os.pread(descriptor, len(expected_contents) + 1, 0)
    if (
        expected_pid != os.getpid()
        or not stat.S_ISREG(held.st_mode)
        or not stat.S_ISREG(current.st_mode)
        or held.st_nlink != 1
        or current.st_nlink != 1
        or held.st_dev != expected_device
        or held.st_ino != expected_inode
        or current.st_dev != expected_device
        or current.st_ino != expected_inode
        or contents != expected_contents
    ):
        raise RuntimeError(
            f"{operation} refused because the instance-lock pathname or "
            "acquisition identity changed"
        )
    if not descriptor_owns_exclusive_flock(
        descriptor,
        expected_device=expected_device,
        expected_inode=expected_inode,
    ):
        raise RuntimeError(
            f"{operation} refused because the designated instance-lock "
            "descriptor does not own its exclusive flock"
        )

    own_query = fcntl.fcntl(
        descriptor,
        fcntl.F_OFD_GETLK,
        ofd_lock_record(fcntl.F_WRLCK),
    )
    own_conflict = struct.unpack(_OFD_LOCK_FORMAT, own_query)[0]
    if own_conflict != fcntl.F_UNLCK:
        raise RuntimeError(
            f"{operation} refused because another open file description owns "
            "the instance lock"
        )

    probe_flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise RuntimeError("O_NOFOLLOW is required for instance-lock verification")
    probe = os.open(LOCK_FILE, probe_flags | nofollow)
    try:
        probe_stat = os.fstat(probe)
        if (
            not stat.S_ISREG(probe_stat.st_mode)
            or probe_stat.st_nlink != 1
            or probe_stat.st_dev != expected_device
            or probe_stat.st_ino != expected_inode
        ):
            raise RuntimeError(
                f"{operation} refused because the instance-lock path was replaced"
            )
        for requested_type in (fcntl.F_WRLCK, fcntl.F_RDLCK):
            try:
                fcntl.fcntl(
                    probe,
                    fcntl.F_OFD_SETLK,
                    ofd_lock_record(requested_type),
                )
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                    raise
            else:
                fcntl.fcntl(
                    probe,
                    fcntl.F_OFD_SETLK,
                    ofd_lock_record(fcntl.F_UNLCK),
                )
                raise RuntimeError(
                    f"{operation} refused because the designated open file "
                    "description does not continuously own the exclusive "
                    "write lock"
                )
    finally:
        os.close(probe)

    after = os.lstat(LOCK_FILE)
    if (
        not stat.S_ISREG(after.st_mode)
        or after.st_nlink != 1
        or after.st_dev != expected_device
        or after.st_ino != expected_inode
    ):
        raise RuntimeError(
            f"{operation} refused because the instance-lock path changed during "
            "the ownership probe"
        )


def transaction_mutation_authority(
    operation: str,
) -> TransactionMutationAuthority:
    """Issue an authority which re-proves the live instance lock on use."""

    return issue_transaction_mutation_authority(
        require_instance_lock_for_remote_write,
        operation=operation,
    )


def acquire_instance_lock() -> None:
    """Prevent two bot processes from sharing one mutable state directory."""
    global _LOCK_FH
    global _LOCK_ACQUISITION_IDENTITY
    global _STATE_DIR_LOCK_FD
    global _STATE_DIR_LOCK_IDENTITY
    global _LOCK_SOCKET
    global _LOCK_SOCKET_NAME

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
    log.info("Acquired instance lock %s", LOCK_FILE)


def redact_secret(value: str, visible: int = 4) -> str:
    """Redact a secret value before it is logged."""
    if not value:
        return "<missing>"
    if len(value) <= visible * 2:
        return "<set-but-short>"
    return f"{value[:visible]}...{value[-visible:]}"


def log_json_debug(label: str, obj: object, max_chars: int = 4000) -> None:
    """Log bounded JSON with recursively redacted credential-like values."""

    sensitive_markers = (
        "secret",
        "token",
        "password",
        "authorization",
        "apikey",
        "bearer",
        "cookie",
        "oauth",
    )

    def sanitise(value: object, seen: set[int], depth: int = 0) -> object:
        if depth > 20:
            return "<maximum-depth>"
        if isinstance(value, dict):
            identity = id(value)
            if identity in seen:
                return "<circular-reference>"
            seen.add(identity)
            try:
                cleaned: dict[str, object] = {}
                for key, item in value.items():
                    key_text = str(key)
                    compact_key = re.sub(r"[^a-z0-9]", "", key_text.lower())
                    if any(marker in compact_key for marker in sensitive_markers):
                        cleaned[key_text] = "[REDACTED]"
                    else:
                        cleaned[key_text] = sanitise(item, seen, depth + 1)
                return cleaned
            finally:
                seen.remove(identity)
        if isinstance(value, (list, tuple)):
            identity = id(value)
            if identity in seen:
                return "<circular-reference>"
            seen.add(identity)
            try:
                return [sanitise(item, seen, depth + 1) for item in value]
            finally:
                seen.remove(identity)
        return value

    try:
        redacted = sanitise(obj, set())
        text = json.dumps(redacted, indent=2, sort_keys=True, default=str)
    except Exception:
        text = "<unserialisable-redacted-payload>"

    if len(text) > max_chars:
        text = text[:max_chars] + "...<truncated>"

    log.debug("%s: %s", label, text)


def state_debug_summary(state: object) -> dict[str, object]:
    """Return state keys and collection sizes without any state values."""
    if not isinstance(state, dict):
        return {"type": type(state).__name__}
    collection_counts = {
        str(key): len(value)
        for key, value in state.items()
        if isinstance(value, (dict, list, tuple, set))
    }
    return {
        "key_count": len(state),
        "keys": sorted(str(key) for key in state),
        "collection_counts": dict(sorted(collection_counts.items())),
    }


def log_event(event: str, **fields: object) -> None:
    """Emit a stable one-line structured event for digest scripts."""
    payload = {"event": event}
    payload.update(fields)
    try:
        text = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        text = repr(payload)
    log.info("EVENT %s", text)


def _log_descriptive_observability_failure(message: str) -> None:
    try:
        log.error(message, exc_info=True)
    except Exception:
        pass


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
    try:
        stable_post_id = str(post_id)
        public = str(public_text).strip() if public_text is not None else ""
        quote = str(quote_text).strip() if quote_text is not None else ""
        summary = str(image_summary).strip() if image_summary is not None else ""
        if public:
            visible_text = public
            visible_text_source = "public_text"
        elif quote:
            visible_text = quote
            visible_text_source = "image_quote_text"
        elif summary:
            visible_text = summary
            visible_text_source = "image_summary"
        else:
            visible_text = None
            visible_text_source = "unavailable"
        log_event(
            "account_root_posted",
            event_version=1,
            lane=str(lane),
            post_id=stable_post_id,
            root_post_id=stable_post_id,
            conversation_id=stable_post_id,
            public_text=public or None,
            visible_text=visible_text,
            visible_text_source=visible_text_source,
            quote_id=(str(quote_id) if quote_id is not None else None),
            quote_text=quote or None,
            image_summary=summary or None,
            post_created_at=(
                str(post_created_at) if post_created_at is not None else None
            ),
            publication_authority="confirmed_transport",
        )
    except Exception:
        # Observability is deliberately downstream of publication authority and
        # can never turn a confirmed post into a failed posting outcome.
        _log_descriptive_observability_failure(
            "Could not emit descriptive account_root_posted observability"
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
    try:
        parent_id = str(parent_post_id)
        log_event(
            "historical_context_reply_posted",
            event_version=1,
            lane="historical_context_reply",
            parent_post_id=parent_id,
            reply_post_id=str(reply_post_id),
            root_post_id=parent_id,
            conversation_id=parent_id,
            reply_text=(str(reply_text) if reply_text is not None else None),
            quote_id=str(quote_id),
            reply_created_at=(
                str(reply_created_at) if reply_created_at is not None else None
            ),
            publication_authority="confirmed_transport",
        )
    except Exception:
        _log_descriptive_observability_failure(
            "Could not emit descriptive historical_context_reply_posted observability"
        )


def emit_historical_context_history_observation(item: object) -> None:
    """Emit the v1 contract only for one validated completed history item."""
    if not isinstance(item, dict) or item.get("status") != "completed":
        return
    required = ("parent_post_id", "reply_post_id", "reply_text", "quote_id")
    if not all(item.get(field) is not None for field in required):
        return
    emit_historical_context_reply_posted(
        parent_post_id=item["parent_post_id"],
        reply_post_id=item["reply_post_id"],
        reply_text=item["reply_text"],
        quote_id=item["quote_id"],
    )


def emit_historical_context_store_observation(
    store: object,
    parent_post_id: object,
) -> None:
    """Read completed history for observability without affecting recovery."""
    try:
        history = store.history()
        items = history.get("items") if isinstance(history, dict) else None
        emit_historical_context_history_observation(
            items.get(str(parent_post_id)) if isinstance(items, dict) else None
        )
    except Exception:
        _log_descriptive_observability_failure(
            "Could not emit recovered historical-context observability"
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

    # Optional generated-image pool for regular quote/image posts
    "ENABLE_GENERATED_IMAGE_POOL",
    "GENERATED_IMAGE_DIR",
    "GENERATED_IMAGE_GLOB",
    "GENERATED_IMAGE_ANALYSIS_FILE",
    "GENERATED_IMAGE_ORIGIN_QUOTE_BOOST",
    "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN",
    "ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING",
    "ORIGINAL_EDITORIAL_ANALYSIS_FILE",
    "ORIGINAL_EDITORIAL_SHADOW_WEIGHT",
    "ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT",
    "ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING",
    "ENABLE_GENERATED_IDENTITY_POLICY_SCORING",
    "GENERATED_IDENTITY_AUDIT_FILE",
    "GENERATED_IDENTITY_SHADOW_SMALL_PENALTY",
    "GENERATED_IDENTITY_SHADOW_STRONG_PENALTY",

    # Operational hardening
    "STATE_BACKUP_COUNT",
    "historical_context_reply",
    "single_call_reply",
    "engagement_question_experiment_enabled",
    "engagement_question_experiment_plan_path",
    "engagement_question_notification_output_path",
}

LOCAL_CONFIG_NON_NEGATIVE_INT_KEYS = {
    "POST_SLEEP_MIN",
    "POST_SLEEP_MAX",
    "MEME_TRIGGER_AFTER_HOUR",
    "MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS",
    "MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS",
    "MEME_FALLBACK_HOUR",
    "MEME_FALLBACK_MINUTE",
    "MEME_MIN_SECONDS_AFTER_QUOTE_POST",
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
    "HOT_POST_REPLY_FULL_RESCAN_EVERY_CHECKS",
    "MAX_QUOTE_POSTS_PER_CHECK",
    "QUOTE_POST_LOOKBACK_MAIN_POSTS",
    "RECENT_OWN_POST_IDS_MAX",
    "QUOTE_LOOKUP_API_MAX_RESULTS",
    "QUOTE_LOOKUP_MAX_PAGES_PER_POST",
    "MENTIONS_MAX_PAGES_PER_CHECK",
    "HOT_POST_REPLY_SEARCH_MAX_PAGES_PER_CHECK",
    "TWEET_CACHE_MAX_AGE_SECONDS",
    "TWEET_CACHE_MAX_ITEMS",
    "ERROR_WINDOW_SECONDS",
    "MAX_X_ERRORS_PER_WINDOW",
    "MAX_OPENAI_ERRORS_PER_WINDOW",
    "COOLDOWN_AFTER_REPEATED_ERRORS_SECONDS",
    "COOLDOWN_AFTER_429_SECONDS",
    "GENERATED_IMAGE_ORIGIN_QUOTE_BOOST",
    "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN",
    "STATE_BACKUP_COUNT",
}

LOCAL_CONFIG_POSITIVE_INT_KEYS = {
    "MAX_AUTO_REPLIES_PER_DAY",
    "MAX_QUOTE_REPLIES_PER_DAY",
    "MAX_REPLIES_PER_AUTHOR_PER_DAY",
    "MAX_MENTIONS_PER_CHECK",
    "AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD",
    "AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS",
    "AUTHOR_NO_REPLY_QUARANTINE_SECONDS",
    "MAX_HOT_POST_REPLIES_PER_CHECK",
    "HOT_POST_REPLY_SEARCH_API_MAX_RESULTS",
    "MAX_QUOTE_POSTS_PER_CHECK",
    "QUOTE_POST_LOOKBACK_MAIN_POSTS",
    "RECENT_OWN_POST_IDS_MAX",
    "QUOTE_LOOKUP_API_MAX_RESULTS",
    "QUOTE_LOOKUP_MAX_PAGES_PER_POST",
    "MENTIONS_MAX_PAGES_PER_CHECK",
    "HOT_POST_REPLY_SEARCH_MAX_PAGES_PER_CHECK",
    "TWEET_CACHE_MAX_AGE_SECONDS",
    "TWEET_CACHE_MAX_ITEMS",
    "MAX_X_ERRORS_PER_WINDOW",
    "MAX_OPENAI_ERRORS_PER_WINDOW",
    "COOLDOWN_AFTER_REPEATED_ERRORS_SECONDS",
    "COOLDOWN_AFTER_429_SECONDS",
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

    def reject_duplicate_names(pairs: list[tuple[str, object]]) -> dict:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise ValueError(f"{label} contains a duplicate object name")
            value[key] = item
        return value

    def reject_nonfinite_constant(value: str) -> object:
        raise ValueError(f"{label} contains a non-finite JSON constant")

    def parse_finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError(f"{label} contains a non-finite JSON number")
        return parsed

    def parse_finite_decimal(value: str) -> Decimal:
        parsed = Decimal(value)
        if not parsed.is_finite():
            raise ValueError(f"{label} contains a non-finite JSON number")
        return parsed

    if isinstance(handle_or_document, (bytes, str)):
        document = handle_or_document
    else:
        document = handle_or_document.read()
    if isinstance(document, bytes):
        document = document.decode("utf-8", errors="strict")
    elif type(document) is not str:
        raise ValueError(f"{label} reader returned unsupported content")

    return json.loads(
        document,
        object_pairs_hook=reject_duplicate_names,
        parse_constant=reject_nonfinite_constant,
        parse_float=(
            parse_finite_decimal
            if parse_floats_as_decimal
            else parse_finite_float
        ),
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


def _coerce_local_config_value(key: str, value: object, current_value: object) -> object:
    if isinstance(current_value, bool):
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            low = value.strip().lower()
            if low in {"1", "true", "yes", "on"}:
                return True
            if low in {"0", "false", "no", "off"}:
                return False
        raise ValueError(f"{key} must be a boolean")

    if isinstance(current_value, int) and not isinstance(current_value, bool):
        if type(value) is not int:
            raise ValueError(f"{key} must be a JSON integer")
        coerced = value
        if key in LOCAL_CONFIG_NON_NEGATIVE_INT_KEYS and coerced < 0:
            raise ValueError(f"{key} must be non-negative")
        if key in LOCAL_CONFIG_POSITIVE_INT_KEYS and coerced <= 0:
            raise ValueError(f"{key} must be positive")
        return coerced

    if isinstance(current_value, float):
        if isinstance(value, bool):
            raise ValueError(f"{key} must be a number, not a boolean")
        coerced = float(value)
        if key in {
            "ORIGINAL_EDITORIAL_SHADOW_WEIGHT",
            "ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT",
            "GENERATED_IDENTITY_SHADOW_SMALL_PENALTY",
            "GENERATED_IDENTITY_SHADOW_STRONG_PENALTY",
        }:
            if not math.isfinite(coerced):
                raise ValueError(f"{key} must be finite")
            if coerced < 0:
                raise ValueError(f"{key} must be non-negative")
        return coerced

    if isinstance(current_value, str):
        if type(value) is not str:
            raise ValueError(f"{key} must be a JSON string")
        if any(ord(char) < 32 and char not in {"\n", "\t"} for char in value):
            raise ValueError(f"{key} contains unsafe control characters")
        if key not in {
            "MEME_POST_TEXT",
            "engagement_question_notification_output_path",
        }:
            if not value:
                raise ValueError(f"{key} must not be empty")
            if value != value.strip():
                raise ValueError(f"{key} must not have leading or trailing whitespace")
        return value

    return value


def validate_runtime_config_values(values: dict[str, object]) -> list[str]:
    """Return validation errors for runtime config values.

    This is intentionally conservative for local overrides. Script defaults are
    expected to pass, and invalid local override sets are rejected atomically.
    """
    errors: list[str] = []

    experiment_enabled = values.get(
        "engagement_question_experiment_enabled",
        globals().get("engagement_question_experiment_enabled"),
    )
    experiment_plan_path = values.get(
        "engagement_question_experiment_plan_path",
        globals().get("engagement_question_experiment_plan_path"),
    )
    notification_path = values.get(
        "engagement_question_notification_output_path",
        globals().get("engagement_question_notification_output_path"),
    )
    if type(experiment_enabled) is not bool:
        errors.append("engagement_question_experiment_enabled must be boolean")
    if (
        type(experiment_plan_path) is not str
        or not experiment_plan_path
        or experiment_plan_path != experiment_plan_path.strip()
        or any(ord(character) < 32 for character in experiment_plan_path)
    ):
        errors.append(
            "engagement_question_experiment_plan_path must be a non-empty clean path"
        )
    if (
        type(notification_path) is not str
        or notification_path != notification_path.strip()
        or any(ord(character) < 32 for character in notification_path)
    ):
        errors.append(
            "engagement_question_notification_output_path must be an empty or clean path"
        )
    elif experiment_enabled is True and not notification_path:
        errors.append(
            "engagement_question_notification_output_path is required when the experiment is enabled"
        )

    context_config = values.get("historical_context_reply", globals().get("historical_context_reply"))
    context_keys = {"enabled", "maximum_length", "include_meaning", "include_source", "include_verification"}
    if not isinstance(context_config, dict):
        errors.append("historical_context_reply must be an object")
    elif set(context_config) != context_keys:
        errors.append("historical_context_reply fields mismatch")
    else:
        for key in ("enabled", "include_meaning", "include_source", "include_verification"):
            if type(context_config.get(key)) is not bool:
                errors.append(f"historical_context_reply.{key} must be boolean")
        maximum = context_config.get("maximum_length")
        if type(maximum) is not int or not 120 <= maximum <= 25_000:
            errors.append("historical_context_reply.maximum_length must be an integer from 120 to 25000")

    reply_config = values.get(
        "single_call_reply",
        globals().get("single_call_reply"),
    )
    errors.extend(validate_single_call_reply_config(reply_config))

    def int_value(key: str) -> int:
        return int(values.get(key, globals().get(key, 0)))

    positive_keys = {
        "POST_SLEEP_MIN",
        "POST_SLEEP_MAX",
        "REPLY_CHECK_EVERY_SECONDS",
        "QUOTE_CHECK_EVERY_SECONDS",
        "MIN_SECONDS_BETWEEN_REPLIES",
        "MAX_AUTO_REPLIES_PER_DAY",
        "MAX_QUOTE_REPLIES_PER_DAY",
        "MAX_REPLIES_PER_AUTHOR_PER_DAY",
        "AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD",
        "AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS",
        "AUTHOR_NO_REPLY_QUARANTINE_SECONDS",
        "MAX_HOT_POST_REPLIES_PER_CHECK",
        "QUOTE_POST_LOOKBACK_MAIN_POSTS",
        "RECENT_OWN_POST_IDS_MAX",
        "QUOTE_LOOKUP_MAX_PAGES_PER_POST",
        "MENTIONS_MAX_PAGES_PER_CHECK",
        "HOT_POST_REPLY_SEARCH_MAX_PAGES_PER_CHECK",
        "TWEET_CACHE_MAX_AGE_SECONDS",
        "TWEET_CACHE_MAX_ITEMS",
        "ERROR_WINDOW_SECONDS",
        "MAX_X_ERRORS_PER_WINDOW",
        "COOLDOWN_AFTER_REPEATED_ERRORS_SECONDS",
        "COOLDOWN_AFTER_429_SECONDS",
    }

    for key in sorted(positive_keys):
        try:
            if int_value(key) <= 0:
                errors.append(f"{key} must be positive")
        except Exception:
            errors.append(f"{key} must be an integer")

    for key, low, high in (
        ("MAX_MENTIONS_PER_CHECK", 5, 100),
        ("QUOTE_LOOKUP_API_MAX_RESULTS", 10, 100),
        ("HOT_POST_REPLY_SEARCH_API_MAX_RESULTS", 10, 100),
    ):
        try:
            value = int_value(key)
            if value < low or value > high:
                errors.append(f"{key} must be between {low} and {high}")
        except Exception:
            errors.append(f"{key} must be an integer")

    raw_generated_spacing = values.get(
        "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN",
        globals().get("GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN", 0),
    )
    if type(raw_generated_spacing) is not int:
        errors.append("GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN must be an integer")
    elif raw_generated_spacing < 0:
        errors.append("GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN must be non-negative")

    for key in (
        "ORIGINAL_EDITORIAL_SHADOW_WEIGHT",
        "ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT",
        "GENERATED_IDENTITY_SHADOW_SMALL_PENALTY",
        "GENERATED_IDENTITY_SHADOW_STRONG_PENALTY",
    ):
        raw_value = values.get(key, globals().get(key, 0.0))
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            errors.append(f"{key} must be a number")
            continue
        if not math.isfinite(float(raw_value)):
            errors.append(f"{key} must be finite")
        elif float(raw_value) < 0:
            errors.append(f"{key} must be non-negative")

    for key in ("MEME_TRIGGER_AFTER_HOUR", "MEME_FALLBACK_HOUR"):
        try:
            value = int_value(key)
            if value < 0 or value > 23:
                errors.append(f"{key} must be between 0 and 23")
        except Exception:
            errors.append(f"{key} must be an integer")

    try:
        value = int_value("MEME_FALLBACK_MINUTE")
        if value < 0 or value > 59:
            errors.append("MEME_FALLBACK_MINUTE must be between 0 and 59")
    except Exception:
        errors.append("MEME_FALLBACK_MINUTE must be an integer")

    for min_key, max_key in (
        ("POST_SLEEP_MIN", "POST_SLEEP_MAX"),
        ("MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS", "MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS"),
    ):
        try:
            if int_value(min_key) > int_value(max_key):
                errors.append(f"{min_key} must be <= {max_key}")
        except Exception:
            errors.append(f"{min_key}/{max_key} must be integers")

    return errors


SOURCE_DEFAULT_CONFIG_VALUES = {
    name: copy.deepcopy(globals()[name])
    for name in LOCAL_CONFIG_ALLOWED_KEYS
    if name in globals()
}


def _local_config_stat_identity(file_stat: os.stat_result) -> tuple[int, ...]:
    """Return the file identity which must remain stable for one config read."""

    return (
        file_stat.st_dev,
        file_stat.st_ino,
        file_stat.st_mode,
        file_stat.st_nlink,
        file_stat.st_uid,
        file_stat.st_gid,
        file_stat.st_size,
        file_stat.st_mtime_ns,
        file_stat.st_ctime_ns,
    )


def _read_stable_local_config_bytes() -> bytes | None:
    """Read one optional regular local-config file without following links."""

    config_path = os.path.abspath(os.fspath(LOCAL_CONFIG_FILE))
    try:
        before_path = os.lstat(config_path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise LocalConfigError(
            f"Failed to inspect local config file {LOCAL_CONFIG_FILE}: {exc}"
        ) from exc
    if not stat.S_ISREG(before_path.st_mode):
        raise LocalConfigError(
            f"Local config file {LOCAL_CONFIG_FILE} must be a regular file"
        )

    nofollow = getattr(os, "O_NOFOLLOW", 0)
    nonblock = getattr(os, "O_NONBLOCK", 0)
    if not nofollow or not nonblock:
        raise LocalConfigError(
            "Local config requires O_NOFOLLOW and O_NONBLOCK support"
        )
    try:
        descriptor = os.open(
            config_path,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | nofollow | nonblock,
        )
    except OSError as exc:
        raise LocalConfigError(
            f"Local config file {LOCAL_CONFIG_FILE} changed before it was opened: {exc}"
        ) from exc
    try:
        try:
            before_fd = os.fstat(descriptor)
            if (
                not stat.S_ISREG(before_fd.st_mode)
                or _local_config_stat_identity(before_path)
                != _local_config_stat_identity(before_fd)
            ):
                raise LocalConfigError(
                    f"Local config file {LOCAL_CONFIG_FILE} changed while it was opened"
                )
            if before_fd.st_size > LOCAL_CONFIG_MAX_BYTES:
                raise LocalConfigError(
                    f"Local config file {LOCAL_CONFIG_FILE} exceeds "
                    f"{LOCAL_CONFIG_MAX_BYTES} bytes"
                )

            chunks: list[bytes] = []
            observed = 0
            while observed <= LOCAL_CONFIG_MAX_BYTES:
                chunk = os.read(
                    descriptor,
                    min(8192, LOCAL_CONFIG_MAX_BYTES + 1 - observed),
                )
                if not chunk:
                    break
                chunks.append(chunk)
                observed += len(chunk)
            document = b"".join(chunks)
            middle_fd = os.fstat(descriptor)
            repeated_document = os.pread(descriptor, before_fd.st_size + 1, 0)
            after_fd = os.fstat(descriptor)
            try:
                after_path = os.lstat(config_path)
            except OSError as exc:
                raise LocalConfigError(
                    f"Local config file {LOCAL_CONFIG_FILE} disappeared while it was read"
                ) from exc
        finally:
            os.close(descriptor)
    except LocalConfigError:
        raise
    except OSError as exc:
        raise LocalConfigError(
            f"Local config file {LOCAL_CONFIG_FILE} could not be read as a stable snapshot: {exc}"
        ) from exc

    if len(document) != before_fd.st_size or len(document) > LOCAL_CONFIG_MAX_BYTES:
        raise LocalConfigError(
            f"Local config file {LOCAL_CONFIG_FILE} length changed while it was read"
        )
    if document != repeated_document:
        raise LocalConfigError(
            f"Local config file {LOCAL_CONFIG_FILE} bytes changed while it was read"
        )
    expected_identity = _local_config_stat_identity(before_fd)
    if any(
        _local_config_stat_identity(observed_stat) != expected_identity
        for observed_stat in (middle_fd, after_fd, after_path)
    ):
        raise LocalConfigError(
            f"Local config file {LOCAL_CONFIG_FILE} identity changed while it was read"
        )
    return document


def load_validated_local_config_overrides() -> dict[str, object] | None:
    """Read and validate local overrides without mutating runtime globals."""

    document = _read_stable_local_config_bytes()
    if document is None:
        return None
    try:
        data = load_strict_runtime_json(document, label="local config")
    except Exception as exc:
        raise LocalConfigError(f"Failed to read local config file {LOCAL_CONFIG_FILE}: {exc}") from exc

    if not isinstance(data, dict):
        raise LocalConfigError(f"Local config file {LOCAL_CONFIG_FILE} must contain a JSON object")

    if "reply_strategy" in data:
        raise LocalConfigError(
            "Local config contains retired reply_strategy V1 settings; replace them with "
            "the single_call_reply configuration before activation"
        )

    # This was the global conversational-provider breaker setting immediately
    # before the single-Sol cut-over. Accept it only as an unambiguous upgrade
    # alias; runtime configuration and state use the accurately named OpenAI
    # setting exclusively.
    legacy_provider_limit = "MAX_XAI_ERRORS_PER_WINDOW"
    current_provider_limit = "MAX_OPENAI_ERRORS_PER_WINDOW"
    if legacy_provider_limit in data:
        if current_provider_limit in data:
            raise LocalConfigError(
                "Local config contains both the retired xAI and current "
                "OpenAI provider error limits"
            )
        data = dict(data)
        data[current_provider_limit] = data.pop(legacy_provider_limit)
        log.warning(
            "Migrating retired local config key %s to %s",
            legacy_provider_limit,
            current_provider_limit,
        )

    proposed: dict[str, object] = {}
    coercion_errors: list[str] = []

    for key, value in data.items():
        if key not in SOURCE_DEFAULT_CONFIG_VALUES:
            raise LocalConfigError(
                f"Unsupported local config key {key!r} in {LOCAL_CONFIG_FILE}; "
                "refusing to ignore a possible safety-setting typo"
            )

        try:
            coerced = _coerce_local_config_value(
                key,
                value,
                SOURCE_DEFAULT_CONFIG_VALUES[key],
            )
        except Exception as exc:
            log.error("Ignoring invalid local config override %s=%r: %s", key, value, exc)
            coercion_errors.append(f"{key}: {exc}")
            continue

        proposed[key] = coerced

    if coercion_errors:
        raise LocalConfigError(
            f"Invalid local config {LOCAL_CONFIG_FILE}: " + "; ".join(coercion_errors)
        )

    if proposed:
        candidate = copy.deepcopy(SOURCE_DEFAULT_CONFIG_VALUES)
        candidate.update(proposed)
        validation_errors = validate_runtime_config_values(candidate)
        if validation_errors:
            raise LocalConfigError(
                f"Invalid local config {LOCAL_CONFIG_FILE}: " + "; ".join(validation_errors)
            )

    return proposed


def apply_local_config() -> None:
    """Apply optional local JSON config overrides without editing the bot script."""
    proposed = load_validated_local_config_overrides()
    if proposed is None:
        log.info("Local config file not present; using script defaults. path=%s", LOCAL_CONFIG_FILE)
        return

    if proposed:
        for key, value in proposed.items():
            globals()[key] = value
        log.info("Applied %d local config override(s) from %s", len(proposed), LOCAL_CONFIG_FILE)
        log_json_debug("Local config overrides applied", proposed)
    else:
        log.info("Local config file present but no valid overrides applied: %s", LOCAL_CONFIG_FILE)


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
    global _REPLY_EVIDENCE_REPOSITORY, _REPLY_EVIDENCE_LOAD_ERROR
    if _REPLY_EVIDENCE_REPOSITORY is not None:
        return _REPLY_EVIDENCE_REPOSITORY
    if _REPLY_EVIDENCE_LOAD_ERROR is not None:
        raise ReplyEvidenceUnavailable(_REPLY_EVIDENCE_LOAD_ERROR)

    from reply_evidence import EvidenceRepository

    research_path = Path(SINGLE_CALL_REPLY_RESEARCH_CORPUS_PATH)
    if not research_path.is_absolute():
        research_path = BASE_DIR / research_path
    factual_evidence_path = BASE_DIR / "reply_factual_evidence.json"
    try:
        _REPLY_EVIDENCE_REPOSITORY = EvidenceRepository(
            research_path,
            factual_evidence_path=factual_evidence_path,
        )
    except Exception as exc:
        _REPLY_EVIDENCE_LOAD_ERROR = (
            f"reply evidence unavailable at {research_path}: "
            f"{type(exc).__name__}: {exc}"
        )
        log.critical(
            "%s; conversational replies are disabled until a controlled restart",
            _REPLY_EVIDENCE_LOAD_ERROR,
        )
        raise ReplyEvidenceUnavailable(_REPLY_EVIDENCE_LOAD_ERROR) from exc
    log.info(
        "Reply evidence loaded lazily. completed=%d unresolved=%d attribution_eligible=%d factual=%d passages=%d",
        _REPLY_EVIDENCE_REPOSITORY.completed_packet_count,
        _REPLY_EVIDENCE_REPOSITORY.unresolved_packet_count,
        _REPLY_EVIDENCE_REPOSITORY.attribution_eligible_packet_count,
        _REPLY_EVIDENCE_REPOSITORY.factual_evidence_count,
        len(_REPLY_EVIDENCE_REPOSITORY.passages),
    )
    return _REPLY_EVIDENCE_REPOSITORY


def initialise_historical_context_semantic_gate(
    packets: dict[str, dict],
) -> object:
    """Load the reviewed gate without failing the independent main-post lane."""
    global _HISTORICAL_CONTEXT_SEMANTIC_GATE
    if _HISTORICAL_CONTEXT_SEMANTIC_GATE is not None:
        return _HISTORICAL_CONTEXT_SEMANTIC_GATE

    from historical_context_formatter import (
        packet_is_attributed_to_margaret_thatcher,
    )
    from historical_context_reply_semantic_gate import (
        POLICY_VERSION,
        load_historical_context_semantic_gate,
    )

    eligible_quote_ids = {
        quote_id
        for quote_id, packet in packets.items()
        if packet_is_attributed_to_margaret_thatcher(packet)
    }
    gate = load_historical_context_semantic_gate(
        root=BASE_DIR,
        eligible_quote_ids=eligible_quote_ids,
        formatter_options={
            "maximum_length": int(historical_context_reply["maximum_length"]),
            "include_meaning": bool(historical_context_reply["include_meaning"]),
            "include_source": bool(historical_context_reply["include_source"]),
            "include_verification": bool(
                historical_context_reply["include_verification"]
            ),
        },
    )
    _HISTORICAL_CONTEXT_SEMANTIC_GATE = gate
    if gate.available:
        log.info(
            "Historical-context semantic gate loaded. policy=%s ledger_sha256=%s "
            "projection_sha256=%s blocked=%d regular_post_eligibility_unchanged=true",
            POLICY_VERSION,
            gate.ledger_sha256,
            gate.projection_sha256,
            len(gate.blocked_dispositions),
        )
        log_event(
            "historical_context_semantic_gate",
            status="loaded",
            policy_version=POLICY_VERSION,
            ledger_sha256=gate.ledger_sha256,
            projection_sha256=gate.projection_sha256,
            blocked_quote_count=len(gate.blocked_dispositions),
        )
    else:
        log.critical(
            "Historical-context semantic gate unavailable; only public context "
            "replies are fail-closed until a controlled restart. reason=%s",
            gate.reason,
        )
        log_event(
            "historical_context_semantic_gate",
            status="unavailable",
            policy_version=POLICY_VERSION,
            ledger_sha256=gate.ledger_sha256,
            reason=gate.reason,
        )
    return gate


def production_bootstrap(
    *,
    log_path: Path | None = None,
    configure_file_logging: bool = True,
) -> None:
    """Apply and validate deployment-local configuration exactly once."""
    global _HISTORICAL_CONTEXT_CORPUS_SNAPSHOT
    global _HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON
    global _PRODUCTION_BOOTSTRAPPED, log
    if _PRODUCTION_BOOTSTRAPPED:
        return
    log = setup_logging(log_path=log_path, configure_file_logging=configure_file_logging)
    initialise_bot_health_reporting()
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
    from historical_context_formatter import HistoricalContextReplyStore

    maintenance_paused = global_remote_writes_paused()
    resume_source_receipt_retirement_for_control_snapshot(
        maintenance_paused=maintenance_paused,
    )
    global _HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON
    context_store = historical_context_reply_store()
    outbox_store = historical_context_outbox_store()
    outbox_available = True
    try:
        outbox_store.snapshot()
    except Exception as exc:
        outbox_available = False
        _HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON = (
            f"{type(exc).__name__}: {exc}"
        )
        log.critical(
            "Historical-context outbox is invalid or unavailable; the quote "
            "lane will fail its pre-post check while unrelated lanes continue",
            exc_info=True,
        )
        log_event(
            "historical_context_outbox",
            status="unavailable",
            error_type=type(exc).__name__,
            reason=str(exc)[:500],
            unrelated_lanes_available=True,
        )
    defer_to_confirmed_transport_recovery = False
    defer_to_outbox_recovery = False
    reconciliation_parent_id: str | None = None
    preloaded_confirmed_context_receipt = None
    leave_receipt_untouched = False
    if maintenance_paused:
        leave_receipt_untouched = receipt_namespace_entry_exists(
            HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
        )
        if leave_receipt_untouched:
            log.warning(
                "Global maintenance pause is active; leaving the historical-"
                "context transaction untouched until an unpaused loop tick"
            )
    else:
        loaded_receipt = context_store._load_receipt_safely()
        loaded_receipt_document = (
            loaded_receipt[0] if loaded_receipt is not None else None
        )
        loaded_is_confirmed_receipt = bool(
            HistoricalContextReplyStore._valid_receipt(
                loaded_receipt_document
            )
        )
        if loaded_is_confirmed_receipt:
            preloaded_confirmed_context_receipt = loaded_receipt
        loaded_is_confirmed_lineage = bool(
            loaded_is_confirmed_receipt
            and loaded_receipt_document.get("lifecycle_state")
            == "confirmed"
            and "source_receipt_sha256" in loaded_receipt_document
        )
        loaded_requires_outbox_authority = bool(
            HistoricalContextReplyStore._valid_sending_receipt(
                loaded_receipt_document
            )
            or loaded_is_confirmed_receipt
        )
        loaded_has_source_lineage = bool(
            HistoricalContextReplyStore._valid_sending_receipt(
                loaded_receipt_document
            )
            or loaded_is_confirmed_lineage
        )
        loaded_journal_classification = (
            inspect_transport_state(
                journal_path_for_receipt(
                    HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
                )
            ).classification
            if loaded_has_source_lineage
            else "absent"
        )
        if (
            loaded_receipt is not None
            and not outbox_available
            and loaded_requires_outbox_authority
        ):
            leave_receipt_untouched = True
            log.critical(
                "Leaving the historical-context transaction receipt untouched "
                "because its outbox authority is unavailable"
            )
        elif (
            loaded_has_source_lineage
            and loaded_journal_classification == "confirmed_pair"
        ):
            defer_to_confirmed_transport_recovery = True
            log.warning(
                "Deferring a transport-confirmed historical-context receipt "
                "to the pre-barrier local recovery path"
            )
        elif loaded_is_confirmed_receipt:
            reconciliation_parent_id = str(
                loaded_receipt_document["parent_post_id"]
            )
            obligation = outbox_store.get(reconciliation_parent_id)
            context = (
                obligation.get("context_reply")
                if isinstance(obligation, dict)
                else None
            )
            if not isinstance(obligation, dict):
                raise RuntimeError(
                    "confirmed historical-context receipt has no matching "
                    "outbox obligation"
                )
            if (
                not isinstance(context, dict)
                or context.get("quote_id")
                != loaded_receipt_document["quote_id"]
            ):
                raise RuntimeError(
                    "confirmed historical-context receipt conflicts with "
                    "its outbox identity"
                )
            if context.get("state") == "context_reply_attempting":
                if loaded_is_confirmed_lineage:
                    defer_to_outbox_recovery = True
                    log.warning(
                        "Deferring a confirmed historical-context receipt to "
                        "its exact attempting outbox recovery"
                    )
                else:
                    if {
                        "source_receipt_sha256",
                        "source_receipt_attempt_number",
                    } & set(context):
                        raise RuntimeError(
                            "legacy confirmed historical-context receipt "
                            "conflicts with a source-bound attempting outbox"
                        )
                    if context.get("attempt_count") != (
                        loaded_receipt_document.get("attempt_number")
                    ):
                        raise RuntimeError(
                            "legacy confirmed historical-context receipt "
                            "conflicts with its outbox attempt"
                        )
                    outbox_store.record_confirmed(
                        reconciliation_parent_id,
                        attempt_number=int(context["attempt_count"]),
                        reply_post_id=loaded_receipt_document[
                            "reply_post_id"
                        ],
                        confirmed_epoch=int(
                            loaded_receipt_document["reply_epoch"]
                        ),
                    )
            elif not confirmed_context_outbox_matches_receipt(
                context,
                loaded_receipt_document,
            ):
                raise RuntimeError(
                    "confirmed historical-context receipt conflicts with "
                    "its durable outbox outcome"
                )
        elif (
            loaded_receipt is not None
            and HistoricalContextReplyStore._valid_sending_receipt(
                loaded_receipt[0]
            )
        ):
            sending_receipt = loaded_receipt[0]
            reconciliation_parent_id = str(
                sending_receipt["parent_post_id"]
            )
            obligation = outbox_store.get(reconciliation_parent_id)
            context = (
                obligation.get("context_reply")
                if isinstance(obligation, dict)
                else None
            )
            if not isinstance(obligation, dict):
                disposition = context_store.reconcile_receipt_disposition(
                    retain_definite_failure_receipt=True,
                )
                if disposition != "definite_failure":
                    raise RuntimeError(
                        "historical-context sending receipt had an "
                        f"unexpected local disposition: {disposition}"
                    )
                raise RuntimeError(
                    "historical-context sending receipt has no matching "
                    "outbox obligation"
                )
            if (
                not isinstance(context, dict)
                or context.get("quote_id") != sending_receipt["quote_id"]
            ):
                raise RuntimeError(
                    "historical-context sending receipt conflicts with "
                    "its outbox identity"
                )
            if context.get("state") == "context_reply_attempting":
                defer_to_outbox_recovery = True
                log.warning(
                    "Deferring a historical-context sending receipt to "
                    "its exact attempting outbox recovery"
                )
            elif context.get("state") in {
                "context_reply_failed_retryable",
                "context_reply_failed_terminal",
            }:
                context_store.ensure_proved_failure_history_from_outbox(
                    context
                )
            else:
                raise RuntimeError(
                    "historical-context failure receipt conflicts with "
                    "its outbox state"
                )
    if not (
        defer_to_confirmed_transport_recovery
        or defer_to_outbox_recovery
        or leave_receipt_untouched
    ):
        try:
            if preloaded_confirmed_context_receipt is not None:
                context_store.reconcile_receipt_disposition(
                    preloaded_receipt=preloaded_confirmed_context_receipt,
                )
            else:
                context_store.reconcile_receipt()
        except Exception:
            log.critical(
                "Historical-context durable receipt could not be reconciled; "
                "refusing production startup to preserve the ambiguity barrier",
                exc_info=True,
            )
            raise
    if defer_to_outbox_recovery:
        if reconciliation_parent_id is None:
            raise RuntimeError(
                "historical-context outbox recovery has no parent identity"
            )
        with outbox_store.worker_lock():
            obligation = outbox_store.get(reconciliation_parent_id)
            if not isinstance(obligation, dict):
                raise RuntimeError(
                    "historical-context attempting outbox record disappeared"
                )
            recovered = recover_interrupted_historical_context_attempt(
                outbox_store,
                obligation,
                recovered_epoch=now_epoch(),
                receipt_was_observed=True,
            )
        log_event(
            "historical_context_obligation",
            **recovered,
        )


def required_installation_files_missing() -> list[Path]:
    """Return durable files that cannot be recovered from local backups."""
    missing = [
        path
        for path in (
            LINES_USED_FILE,
            IMAGES_USED_FILE,
        )
        if not durable_state_namespace_is_owned_single_link_file(path)
    ]
    # The history and outbox stores apply their own schema-specific read
    # limits. Establishment checks only their shared namespace contract here;
    # imposing the smaller core-state limit would reject a valid store before
    # its authoritative loader could inspect it.
    missing.extend(
        path
        for path in (
            HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
            HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE,
        )
        if not durable_state_namespace_is_owned_single_link_file(
            path,
            maximum_bytes=None,
        )
    )
    for receipt_path in remote_source_receipt_paths():
        if retirement_ledger_is_blocking(receipt_path):
            ledger_path, _exchange_path = retirement_ledger_paths(receipt_path)
            missing.append(ledger_path)
    # Installations created before the explicit marker protocol remain valid
    # when all established durable files are present.  A new initializer
    # publishes this separate sentinel before its first data write, so any
    # interrupted new installation remains fail closed without rejecting the
    # already deployed legacy installation.
    try:
        os.lstat(INSTALLATION_IN_PROGRESS_FILE)
    except FileNotFoundError:
        pass
    except OSError:
        missing.append(INSTALLATION_IN_PROGRESS_FILE)
    else:
        missing.append(INSTALLATION_IN_PROGRESS_FILE)
    state_candidates = [STATE_FILE]
    state_candidates.extend(
        STATE_FILE.with_name(f"{STATE_FILE.name}.bak{i}")
        for i in range(1, STATE_BACKUP_COUNT + 1)
    )
    for candidate in state_candidates:
        try:
            os.lstat(candidate)
        except FileNotFoundError:
            continue
        except OSError:
            missing.append(candidate)
            continue
        if not durable_state_namespace_is_owned_single_link_file(candidate):
            missing.append(candidate)
    if not any(
        durable_state_namespace_is_owned_single_link_file(path)
        for path in state_candidates
    ):
        missing.append(STATE_FILE)
    return list(dict.fromkeys(missing))


def require_established_installation() -> None:
    """Refuse operational startup after unexpected durable-state loss."""
    missing = required_installation_files_missing()
    if missing:
        raise RuntimeError(
            "Required durable production state/history is missing: "
            + ", ".join(str(path) for path in missing)
            + ". Restore the files or use --initialise only for a genuinely new installation."
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

    inspections = {
        receipt_path: inspect_retirement_ledger(receipt_path)
        for receipt_path in remote_source_receipt_paths()
    }
    recoverable = tuple(
        receipt_path
        for receipt_path, inspection in inspections.items()
        if inspection.valid
        and inspection.blocking
        and inspection.state in {"exchange_staged", "exchange_committed"}
    )
    if not recoverable:
        return ()
    try:
        inspect_protocol_activation(
            REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE
        )
    except (ProtocolActivationError, OSError):
        # Installation validation below remains fail closed.  An absent or
        # invalid permission generation cannot authorise namespace mutation.
        return ()
    authority = transaction_mutation_authority(
        "startup retirement-ledger atomic exchange recovery"
    )
    recovered: list[Path] = []
    for receipt_path in recoverable:
        if recover_retirement_ledger_exchange_if_present(
            receipt_path,
            mutation_authority=authority,
        ):
            recovered.append(receipt_path)
    return tuple(recovered)


def require_established_installation_after_ledger_recovery() -> None:
    """Recover exact ledger exchanges, then require a complete installation."""

    recovered = recover_interrupted_retirement_ledger_exchanges_at_startup()
    if recovered:
        log.warning(
            "Recovered crash-left permanent retirement-ledger exchanges "
            "before installation validation: %s",
            [str(path) for path in recovered],
        )
    require_established_installation()


def initialise_installation() -> int:
    """Create a new state/history set without starting production."""
    from historical_context_formatter import HistoricalContextReplyStore

    require_production_bootstrap()
    # This one-shot command mutates the daemon's durable namespace.  Own the
    # ordinary process/state-directory lock before proving that namespace
    # empty so a running daemon or concurrent initializer cannot race the
    # inspection/write interval.  The command exits immediately afterwards
    # and deliberately retains the lock until process exit.
    acquire_instance_lock()
    candidates = [
        INSTALLATION_MARKER_FILE,
        INSTALLATION_IN_PROGRESS_FILE,
        STATE_FILE,
        STATE_FILE.with_suffix(".tmp"),
        LINES_USED_FILE,
        LINES_USED_FILE.with_suffix(f"{LINES_USED_FILE.suffix}.tmp"),
        IMAGES_USED_FILE,
        IMAGES_USED_FILE.with_suffix(f"{IMAGES_USED_FILE.suffix}.tmp"),
    ]
    candidates.extend(STATE_FILE.with_name(f"{STATE_FILE.name}.bak{i}") for i in range(1, STATE_BACKUP_COUNT + 1))
    candidates.extend(
        STATE_FILE.with_name(f"{STATE_FILE.name}.bak{i}.tmp")
        for i in range(1, STATE_BACKUP_COUNT + 1)
    )
    candidates.extend(
        (
            REGULAR_POST_RECEIPT_FILE,
            MEME_POST_RECEIPT_FILE,
            CONFIRMED_REPLY_RECEIPT_FILE,
            journal_path_for_receipt(REGULAR_POST_RECEIPT_FILE),
            fence_path_for_journal(
                journal_path_for_receipt(REGULAR_POST_RECEIPT_FILE)
            ),
            MEDIA_UPLOAD_RECEIPT_FILE,
            media_fence_path_for_receipt(MEDIA_UPLOAD_RECEIPT_FILE),
            AMBIGUOUS_POST_OUTCOME_FILE,
            AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE,
            REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE.with_name(
                REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_AUDIT_BASENAME
            ),
            REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE,
            HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
            HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
            HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE,
            HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.with_name(
                f"{HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.name}.lock"
            ),
            HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.with_name(
                f"{HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.name}.worker.lock"
            ),
        )
    )
    candidates.extend(
        auxiliary
        for receipt_path in remote_source_receipt_paths()
        for auxiliary in retirement_auxiliary_paths(receipt_path)
    )
    candidates.extend(
        ledger_path
        for receipt_path in remote_source_receipt_paths()
        for ledger_path in retirement_ledger_paths(receipt_path)
    )
    # Transition/retirement auxiliaries carry random identity suffixes. Inspect
    # their exact reserved lexical prefixes without following entries rather
    # than guessing names or treating an orphan as a fresh installation.
    try:
        with os.scandir(BASE_DIR) as entries:
            candidates.extend(
                BASE_DIR / entry.name
                for entry in entries
                if entry.name.startswith(
                    (
                        JOURNAL_STAGING_PREFIX,
                        JOURNAL_RETIREMENT_PREFIX,
                        MEDIA_TRANSITION_PREFIX,
                        MEDIA_RETIREMENT_GUARD_PREFIX,
                    )
                )
            )
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise RuntimeError(
            "Refusing to initialise because the state directory namespace "
            "cannot be inventoried"
        ) from exc
    existing: list[Path] = []
    for path in candidates:
        try:
            os.lstat(path)
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise RuntimeError(
                "Refusing to initialise because an existing-state namespace "
                f"entry cannot be inspected: {path}"
            ) from exc
        existing.append(path)
    if existing:
        raise RuntimeError(
            "Refusing to initialise over an existing or partially established installation: "
            + ", ".join(str(path) for path in existing)
        )

    BASE_DIR.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    try:
        current = now_epoch()
        created.append(INSTALLATION_IN_PROGRESS_FILE)
        atomic_write_json(
            INSTALLATION_IN_PROGRESS_FILE,
            {
                "schema_version": 1,
                "state": "initialising",
                "started_at_epoch": current,
            },
            durable=True,
        )
        # Register every state pathname before schedule initialisation: that
        # helper may persist state when daily memes are enabled.
        created.append(STATE_FILE)
        created.append(STATE_FILE.with_suffix(".tmp"))
        created.extend(
            STATE_FILE.with_name(f"{STATE_FILE.name}.bak{i}")
            for i in range(1, STATE_BACKUP_COUNT + 1)
        )
        created.extend(
            STATE_FILE.with_name(f"{STATE_FILE.name}.bak{i}.tmp")
            for i in range(1, STATE_BACKUP_COUNT + 1)
        )
        state = default_state()
        state["next_quote_post_epoch"] = current + POST_SLEEP_MIN
        if ENABLE_DAILY_MEME_POSTS:
            ensure_meme_schedule_initialized(state)
        save_state(state, durable=True)
        created.append(LINES_USED_FILE)
        created.append(
            LINES_USED_FILE.with_suffix(f"{LINES_USED_FILE.suffix}.tmp")
        )
        save_quote_used_hashes(LINES_USED_FILE, set(), durable=True)
        created.append(IMAGES_USED_FILE)
        created.append(
            IMAGES_USED_FILE.with_suffix(f"{IMAGES_USED_FILE.suffix}.tmp")
        )
        save_image_used_basenames(IMAGES_USED_FILE, set(), durable=True)
        context_store = historical_context_reply_store(
            allow_missing_history=True
        )
        created.append(context_store.history_path)
        context_store.initialise_empty_history()
        outbox = historical_context_outbox_store()
        created.extend((outbox.path, outbox.lock_path))
        outbox.initialise_empty()
        ledger_authority = transaction_mutation_authority(
            "new-install retirement-ledger initialisation"
        )
        for receipt_path in remote_source_receipt_paths():
            ledger_path, exchange_path = retirement_ledger_paths(receipt_path)
            created.extend((ledger_path, exchange_path))
            inspection = initialise_retirement_ledger(
                receipt_path,
                mutation_authority=ledger_authority,
            )
            if (
                not inspection.valid
                or inspection.blocking
                or inspection.state != "idle"
                or inspection.sequence != 0
            ):
                raise RuntimeError(
                    "New-install retirement ledger did not reach its exact "
                    f"genesis state: {receipt_path}"
                )
        created.append(INSTALLATION_MARKER_FILE)
        atomic_write_json(
            INSTALLATION_MARKER_FILE,
            {"schema_version": 1, "initialised_at_epoch": current},
            durable=True,
        )
        INSTALLATION_IN_PROGRESS_FILE.unlink()
        fsync_parent_dir(INSTALLATION_IN_PROGRESS_FILE, strict=True)
    except Exception as initialisation_error:
        cleanup_failures: list[str] = []
        for path in reversed(created):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as cleanup_error:
                # Never recurse through, replace, or follow an unexpected
                # namespace object.  Continue removing the remaining files,
                # preserve the initiating failure, and leave the installation
                # completeness checks to keep any residue non-operational.
                cleanup_failures.append(
                    f"{path}: {type(cleanup_error).__name__}: {cleanup_error}"
                )
        if cleanup_failures:
            # Python 3.10 has no BaseException.add_note().  Attach structured
            # diagnostics without replacing the initiating exception and emit
            # the same information to the local log.
            initialisation_error.initialisation_cleanup_failures = tuple(
                cleanup_failures
            )
            log.error(
                "Initialisation rollback left exact non-file or unremovable "
                "paths: %s",
                "; ".join(cleanup_failures),
            )
        raise
    print(
        f"Initialised durable MrsMThatcher state in {BASE_DIR}; production was "
        "not started. Remote writes remain disabled until the stopped "
        "external-attestation protocol activator succeeds."
    )
    return 0


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


def control_failure_result(reason: str, *, signature: object) -> dict:
    """Build the fail-closed result for an invalid runtime-control document."""
    return _runtime_control.control_failure_result(
        reason,
        signature=signature,
        CONTROL_FILE=CONTROL_FILE,
        _CONTROL_CACHE=_CONTROL_CACHE,
        log=log,
    )


def _runtime_control_stat_identity(file_stat: os.stat_result) -> tuple[int, ...]:
    """Return the fields which must remain stable for one control snapshot."""
    return _runtime_control._runtime_control_stat_identity(
        file_stat,
        stat=stat,
    )


def _read_stable_runtime_control() -> tuple[bytes, tuple[object, ...]]:
    """Read one regular, non-symlink control file as a stable byte snapshot."""
    return _runtime_control._read_stable_runtime_control(
        CONTROL_FILE=CONTROL_FILE,
        RUNTIME_CONTROL_MAX_BYTES=RUNTIME_CONTROL_MAX_BYTES,
        _RuntimeControlAbsent=_RuntimeControlAbsent,
        _runtime_control_stat_identity=_runtime_control_stat_identity,
        hashlib=hashlib,
        os=os,
        stat=stat,
    )


def load_control() -> dict:
    """Load and validate the optional fail-safe runtime-control document."""
    return _runtime_control.load_control(
        CONTROL_FILE=CONTROL_FILE,
        _CONTROL_CACHE=_CONTROL_CACHE,
        _RuntimeControlAbsent=_RuntimeControlAbsent,
        _read_stable_runtime_control=_read_stable_runtime_control,
        control_failure_result=control_failure_result,
        load_strict_runtime_json=load_strict_runtime_json,
        log=log,
        log_json_debug=log_json_debug,
        validate_control_document=validate_control_document,
    )


def control_bool(data: dict, key: str) -> bool:
    """Return a validated boolean runtime-control value."""
    return _runtime_control.control_bool(
        data,
        key,
        log=log,
    )


def control_pause_active(data: dict, *keys: str) -> tuple[bool, str, int]:
    """Return whether the runtime-control document currently pauses a lane."""
    return _runtime_control.control_pause_active(
        data,
        *keys,
        control_bool=control_bool,
        log=log,
        now_epoch=now_epoch,
        parse_control_time=parse_control_time,
    )


def lane_paused(*lane_keys: str) -> bool:
    """Return whether a named posting lane is paused."""
    return _runtime_control.lane_paused(
        *lane_keys,
        control_pause_active=control_pause_active,
        datetime=datetime,
        load_control=load_control,
        log=log,
        log_event=log_event,
    )


def global_remote_writes_paused() -> bool:
    """Return whether the runtime control pauses every remote-write lane."""
    return _runtime_control.global_remote_writes_paused(
        control_pause_active=control_pause_active,
        load_control=load_control,
    )


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
    if not all([CONSUMER_KEY, CONSUMER_SECRET, ACCESS_TOKEN, ACCESS_SECRET, MY_USER_ID]):
        raise RuntimeError(
            "Missing X credentials. Set X_CONSUMER_KEY, X_CONSUMER_SECRET, "
            "X_ACCESS_TOKEN, X_ACCESS_SECRET, X_MY_USER_ID"
        )
    if (
        ENABLE_AUTO_REPLIES
        and single_call_reply.get("enabled") is True
        and not OPENAI_API_KEY
    ):
        raise RuntimeError(
            "single_call_reply is enabled, but OPENAI_API_KEY is not set"
        )

AUTH = OAuth1(
    CONSUMER_KEY,
    client_secret=CONSUMER_SECRET,
    resource_owner_key=ACCESS_TOKEN,
    resource_owner_secret=ACCESS_SECRET,
)


def normalise_base_url(raw: str, *, require_origin: bool = False) -> str:
    """Return one validated API base or fail during configuration.

    Route classification is performed against paths which this module appends
    itself.  A configured path prefix, query, fragment or user-info component
    could make the literal route and the prepared on-wire route disagree, so
    the X request and upload bases must be origins.  The OpenAI provider retains
    its explicit ``/v1`` base because it does not participate in X route
    classification.
    """
    return _request_route_values.normalise_base_url(
        raw,
        require_origin=require_origin,
        _normalise_x_origin_before_runtime_configuration=_normalise_x_origin_before_runtime_configuration,
        urlsplit=urlsplit,
        urlunsplit=urlunsplit,
    )


def endpoint_host(url: str) -> str:
    """Return the normalised host from an API endpoint URL."""
    return _request_route_values.endpoint_host(
        url,
        urlsplit=urlsplit,
    )


def endpoint_is_loopback(url: str) -> bool:
    """Return whether one configured endpoint is an explicit loopback host."""
    return _request_route_values.endpoint_is_loopback(
        url,
        endpoint_host=endpoint_host,
        ipaddress=ipaddress,
    )


X_BASE = normalise_base_url(
    os.getenv("X_API_BASE_URL", "https://api.x.com"),
    require_origin=True,
)
X_UPLOAD_BASE = normalise_base_url(
    os.getenv("X_UPLOAD_BASE_URL", X_BASE),
    require_origin=True,
)
OPENAI_BASE = normalise_base_url(os.getenv("OPENAI_API_BASE_URL", "https://api.openai.com/v1"))
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

def coerce_used_set(value: object, *, path: Path) -> set:
    """Normalise persisted used-history data to a set."""
    if isinstance(value, set):
        return value
    if isinstance(value, list):
        return set(value)

    raise ValueError(f"Used-history file {path} must contain a JSON list")


def used_set_to_sorted_list(value: set) -> list:
    """Return deterministic JSON-safe used-history values."""
    def sort_key(item: object) -> tuple[int, int | str]:
        try:
            return (0, int(item))
        except Exception:
            return (1, str(item))

    return sorted(value, key=sort_key)


def load_used_set(path: Path, *, legacy_pickle_path: Path | None = None) -> set:
    """Load a fail-closed durable used-history set."""
    log.debug("Loading used-history set from %s", path)

    try:
        present, data = read_stable_owned_json_bytes_no_follow(path)
        if not present or data is None:
            raise FileNotFoundError(path)
        value = json.loads(data.decode("utf-8"))
        converted = coerce_used_set(value, path=path)
        if isinstance(value, list) and value != used_set_to_sorted_list(converted):
            save_used_set(path, converted)
            log.info("Normalized used-history JSON ordering in %s", path)
        log.debug("Loaded %d entries from %s", len(converted), path)
        return converted
    except FileNotFoundError:
        log.warning("Used-history JSON file does not exist yet: %s", path)
    except (OSError, UnsafeDurableStateNamespace):
        log.exception("OS error loading existing used-history JSON file %s; refusing stale legacy fallback", path)
        raise CorruptUsedHistoryError(f"Existing used-history JSON is unreadable: {path}")
    except Exception:
        log.exception("Failed loading existing used-history JSON file %s; refusing stale legacy fallback", path)
        raise CorruptUsedHistoryError(f"Existing used-history JSON is corrupt or invalid: {path}")

    if legacy_pickle_path is not None and legacy_pickle_path.exists():
        log.critical(
            "Used-history JSON %s is missing but legacy pickle %s exists; refusing unsafe pickle fallback. "
            "Restore the JSON history or migrate manually from a trusted backup.",
            path,
            legacy_pickle_path,
        )
        raise CorruptUsedHistoryError(f"Used-history JSON missing while legacy pickle exists: {path}")
    return set()


def save_used_set(path: Path, value: set, *, durable: bool = False) -> None:
    """Persist a used-history set atomically."""
    log.debug("Saving %d entries to used-history JSON %s", len(value), path)
    atomic_write_json(path, used_set_to_sorted_list(value), durable=durable)


def default_state() -> dict:
    """Build a new runtime-state document with safe defaults."""
    return {
        "minimum_reader_version": STATE_MINIMUM_READER_VERSION,
        "last_seen_mention_id": None,
        "mention_pagination": {},
        "mention_backlog": {},
        "mention_backlog_reset_guard": {},
        "mention_pending_candidates": {},
        "author_evaluation_quarantines": {},
        "replied_to_ids": [],
        "dry_run_seen_mention_ids": [],
        "skipped_hot_reply_ids": [],
        "skipped_hot_reply_records": {},
        "hot_post_reply_since_ids": {},
        "hot_post_reply_pagination_tokens": {},
        "hot_post_reply_check_counts": {},

        "daily_reply_date": None,
        "daily_reply_count": 0,
        "daily_replied_author_ids": [],
        "daily_replied_author_counts": {},

        "own_auto_reply_ids": [],
        "tweet_cache": {},
        "pending_ai_reply_drafts": {},
        "ai_reply_history": [],

        "posted_meme_filenames": [],
        "last_meme_post_epoch": 0,
        "next_meme_post_epoch": 0,
        "meme_schedule_version": 0,
        "next_meme_schedule_mode": "",
        "next_meme_schedule_date": "",
        "meme_anchor_quote_post_epoch": 0,

        "last_reply_epoch": 0,
        "last_reply_check_epoch": 0,
        "next_reply_lane_priority": "normal",
        "last_main_post_id": None,
        "last_regular_image_filename": None,
        "original_regular_posts_since_generated_image": GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN,
        "last_quote_post_epoch": 0,
        "next_quote_post_epoch": 0,

        "recent_own_post_ids": [],

        "seen_quote_post_ids": [],
        "replied_to_quote_post_ids": [],
        "skipped_quote_post_ids": [],
        "quote_lookup_pagination_tokens": {},
        "quote_lookup_repeated_cursor_suppressions": {},
        "quote_spam_author_ids": [],
        "daily_quote_reply_date": None,
        "daily_quote_reply_count": 0,
        "last_quote_tweet_check_epoch": 0,

        "x_error_epochs": [],
        "x_write_error_epochs": [],
        "openai_error_epochs": [],
        "api_cooldown_until_epoch": 0,
        "api_cooldown_reason": "",
        "x_write_api_cooldown_until_epoch": 0,
        "x_write_api_cooldown_reason": "",
        "openai_api_cooldown_until_epoch": 0,
        "openai_api_cooldown_reason": "",
        "quote_x_error_epochs": [],
        "quote_api_cooldown_until_epoch": 0,
        "quote_api_cooldown_reason": "",
    }


def append_unique_capped(values: object, item: object, max_items: int) -> list[str]:
    """Append unique capped."""
    item_text = str(item)
    existing = [str(value) for value in values] if isinstance(values, list) else []
    existing = [value for value in existing if value != item_text]
    existing.append(item_text)
    return existing[-max_items:]


def append_unique_durable(values: object, item: object) -> list[str]:
    """Append once without evicting an authoritative completed-target record."""
    item_text = str(item)
    existing = [str(value) for value in values] if isinstance(values, list) else []
    deduplicated = list(dict.fromkeys(existing))
    if item_text not in deduplicated:
        deduplicated.append(item_text)
    return deduplicated


def bounded_tweet_id_value(value: object, *, allow_empty: bool = False) -> int | None:
    """Parse one bounded string tweet ID without unbounded integer conversion."""
    return _state_value_normalisation.bounded_tweet_id_value(
        value,
        allow_empty=allow_empty,
        re=re,
    )


def normalise_state_int(value: object, *, key: str, path: Path) -> int | None:
    """Normalise state int."""
    return _state_value_normalisation.normalise_state_int(
        value,
        key=key,
        path=path,
        log=log,
        math=math,
    )


def normalise_state_epoch(value: object, *, key: str, path: Path) -> int | None:
    """Normalise state epoch."""
    return _state_value_normalisation.normalise_state_epoch(
        value,
        key=key,
        path=path,
        MAX_REASONABLE_STATE_EPOCH=MAX_REASONABLE_STATE_EPOCH,
        log=log,
        normalise_state_int=normalise_state_int,
    )


def normalise_string_list(value: object, *, key: str, path: Path) -> list[str] | None:
    """Normalise string list."""
    return _state_value_normalisation.normalise_string_list(
        value,
        key=key,
        path=path,
        log=log,
    )


def normalise_int_list(value: object, *, key: str, path: Path) -> list[int] | None:
    """Normalise int list."""
    return _state_value_normalisation.normalise_int_list(
        value,
        key=key,
        path=path,
        log=log,
        normalise_state_int=normalise_state_int,
    )


def normalise_epoch_list(value: object, *, key: str, path: Path) -> list[int] | None:
    """Normalise epoch list."""
    return _state_value_normalisation.normalise_epoch_list(
        value,
        key=key,
        path=path,
        MAX_REASONABLE_STATE_EPOCH=MAX_REASONABLE_STATE_EPOCH,
        log=log,
        normalise_int_list=normalise_int_list,
    )


def normalise_string_map(value: object, *, key: str, path: Path) -> dict[str, str] | None:
    """Normalise string map."""
    return _state_value_normalisation.normalise_string_map(
        value,
        key=key,
        path=path,
        log=log,
    )


def normalise_int_map(value: object, *, key: str, path: Path) -> dict[str, int] | None:
    """Normalise int map."""
    return _state_value_normalisation.normalise_int_map(
        value,
        key=key,
        path=path,
        log=log,
        normalise_state_int=normalise_state_int,
    )


def normalise_record_map(value: object, *, key: str, path: Path) -> dict[str, dict] | None:
    """Normalise record map."""
    return _state_value_normalisation.normalise_record_map(
        value,
        key=key,
        path=path,
        log=log,
    )


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
        bounded_tweet_id_value=bounded_tweet_id_value,
        re=re,
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


def prune_completed_mention_quarantine_evaluations(state: dict) -> int:
    """Delegate reply evaluation state with current root dependencies."""
    return _reply_evaluation_state.prune_completed_mention_quarantine_evaluations(
        state,
        AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY=AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY,
        completed_mention_watermark_covers_target=completed_mention_watermark_covers_target,
        log=log,
    )


def prune_reply_evaluation_records(
    state: dict,
    *,
    current_epoch: int | None = None,
) -> None:
    """Delegate reply evaluation state with current root dependencies."""
    return _reply_evaluation_state.prune_reply_evaluation_records(
        state,
        current_epoch=current_epoch,
        MAX_REASONABLE_STATE_EPOCH=MAX_REASONABLE_STATE_EPOCH,
        REPLY_EVALUATION_MAX_RECORDS=REPLY_EVALUATION_MAX_RECORDS,
        REPLY_EVALUATION_MIN_RETENTION_SECONDS=REPLY_EVALUATION_MIN_RETENTION_SECONDS,
        log=log,
        now_epoch=now_epoch,
        prune_completed_mention_quarantine_evaluations=prune_completed_mention_quarantine_evaluations,
    )


def author_no_reply_epoch_limit() -> int:
    """Delegate reply evaluation state with current root dependencies."""
    return _reply_evaluation_state.author_no_reply_epoch_limit(
        AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD=AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD,
    )


def prune_author_evaluation_quarantines(
    state: dict,
    *,
    current_epoch: int | None = None,
) -> bool:
    """Delegate reply evaluation state with current root dependencies."""
    return _reply_evaluation_state.prune_author_evaluation_quarantines(
        state,
        current_epoch=current_epoch,
        AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY=AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY,
        AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS=AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS,
        MAX_REASONABLE_STATE_EPOCH=MAX_REASONABLE_STATE_EPOCH,
        author_no_reply_epoch_limit=author_no_reply_epoch_limit,
        log_event=log_event,
        now_epoch=now_epoch,
    )


def active_author_evaluation_quarantine(
    state: dict,
    author_id: str,
    *,
    current_epoch: int | None = None,
) -> dict | None:
    """Delegate reply evaluation state with current root dependencies."""
    return _reply_evaluation_state.active_author_evaluation_quarantine(
        state, author_id,
        current_epoch=current_epoch,
        now_epoch=now_epoch,
        prune_author_evaluation_quarantines=prune_author_evaluation_quarantines,
    )


def record_qualifying_author_no_reply(
    state: dict,
    author_id: str,
    *,
    current_epoch: int | None = None,
    explicit_spam_or_abuse: bool = True,
) -> bool:
    """Delegate reply evaluation state with current root dependencies."""
    return _reply_evaluation_state.record_qualifying_author_no_reply(
        state, author_id,
        current_epoch=current_epoch,
        explicit_spam_or_abuse=explicit_spam_or_abuse,
        AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY=AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY,
        AUTHOR_NO_REPLY_QUARANTINE_SECONDS=AUTHOR_NO_REPLY_QUARANTINE_SECONDS,
        AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD=AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD,
        AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS=AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS,
        author_no_reply_epoch_limit=author_no_reply_epoch_limit,
        log_event=log_event,
        now_epoch=now_epoch,
        prune_author_evaluation_quarantines=prune_author_evaluation_quarantines,
    )


clear_author_evaluation_quarantine_history = _reply_evaluation_state.clear_author_evaluation_quarantine_history


def normalise_tweet_cache_entry(tweet_id: object, entry: dict, *, path: Path) -> dict[str, object] | None:
    """Delegate tweet lookup/cache work with current root dependencies."""
    return _tweet_lookup_cache.normalise_tweet_cache_entry(
        tweet_id,
        entry,
        path=path,
        log=log,
        normalise_state_epoch=normalise_state_epoch,
    )


def normalise_tweet_cache(value: object, *, path: Path) -> dict[str, dict] | None:
    """Delegate tweet lookup/cache work with current root dependencies."""
    return _tweet_lookup_cache.normalise_tweet_cache(
        value,
        path=path,
        log=log,
        normalise_tweet_cache_entry=normalise_tweet_cache_entry,
    )


def normalise_mention_pagination(value: object, *, path: Path) -> dict[str, str] | None:
    """Delegate to mention authority with current root dependencies."""
    return _mention_authority.normalise_mention_pagination(
        value,
        path=path,
        bounded_tweet_id_value=bounded_tweet_id_value,
        log=log,
        mention_pagination_provenance_is_valid=mention_pagination_provenance_is_valid,
    )


def normalise_mention_backlog_reset_guard(
    value: object,
    *,
    path: Path,
) -> dict[str, object] | None:
    """Delegate to mention authority with current root dependencies."""
    return _mention_authority.normalise_mention_backlog_reset_guard(
        value,
        path=path,
        bounded_tweet_id_value=bounded_tweet_id_value,
        log=log,
    )


active_mention_backlog_reset_guard = _mention_authority.active_mention_backlog_reset_guard


def normalise_mention_backlog(
    value: object,
    *,
    path: Path,
    reset_token_overflow: bool = False,
) -> dict | None:
    """Delegate to mention authority with current root dependencies."""
    return _mention_authority.normalise_mention_backlog(
        value,
        path=path,
        reset_token_overflow=reset_token_overflow,
        MAX_REASONABLE_STATE_EPOCH=MAX_REASONABLE_STATE_EPOCH,
        MENTION_BACKLOG_CONTINUATION_TOKEN_LIMIT=MENTION_BACKLOG_CONTINUATION_TOKEN_LIMIT,
        bounded_tweet_id_value=bounded_tweet_id_value,
        log=log,
    )


def canonical_mention_pending_candidates(
    value: object,
    *,
    path: Path,
) -> dict[str, dict] | None:
    """Delegate to mention authority with current root dependencies."""
    return _mention_authority.canonical_mention_pending_candidates(
        value,
        path=path,
        bounded_tweet_id_value=bounded_tweet_id_value,
        log=log,
    )


def _emit_mention_authority_recovery(
    recovery: dict[str, object],
    *,
    path: Path,
    recovery_events: list[dict[str, object]] | None,
) -> None:
    """Delegate to mention authority with current root dependencies."""
    return _mention_authority._emit_mention_authority_recovery(
        recovery,
        path=path,
        recovery_events=recovery_events,
        log=log,
        log_event=log_event,
    )


_reset_mention_candidate_authority = _mention_authority._reset_mention_candidate_authority


def validate_pending_mention_candidate_authority(
    state: dict,
    *,
    path: Path,
    recover_pending_identity: bool,
    recovery_events: list[dict[str, object]] | None = None,
) -> tuple[bool, bool]:
    """Delegate to mention authority with current root dependencies."""
    return _mention_authority.validate_pending_mention_candidate_authority(
        state,
        path=path,
        recover_pending_identity=recover_pending_identity,
        recovery_events=recovery_events,
        _emit_mention_authority_recovery=_emit_mention_authority_recovery,
        _reset_mention_candidate_authority=_reset_mention_candidate_authority,
        active_mention_backlog_reset_guard=active_mention_backlog_reset_guard,
        bounded_tweet_id_value=bounded_tweet_id_value,
        canonical_mention_pending_candidates=canonical_mention_pending_candidates,
        log=log,
        normalise_mention_backlog=normalise_mention_backlog,
        normalise_mention_backlog_reset_guard=normalise_mention_backlog_reset_guard,
        normalise_mention_pagination=normalise_mention_pagination,
        terminal_reply_evaluation=terminal_reply_evaluation,
    )


def mention_pagination_has_canonical_page_ownership(
    state: dict,
    pagination: object,
    *,
    target_id: str,
) -> bool:
    """Delegate to mention authority with current root dependencies."""
    return _mention_authority.mention_pagination_has_canonical_page_ownership(
        state,
        pagination,
        target_id=target_id,
        STATE_FILE=STATE_FILE,
        bounded_tweet_id_value=bounded_tweet_id_value,
        mention_pagination_provenance_is_valid=mention_pagination_provenance_is_valid,
        normalise_mention_backlog=normalise_mention_backlog,
    )


def normalise_author_evaluation_quarantines(value: object, *, path: Path) -> dict | None:
    """Delegate reply evaluation state with current root dependencies."""
    return _reply_evaluation_state.normalise_author_evaluation_quarantines(
        value,
        path=path,
        AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY=AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY,
        AUTHOR_EVALUATION_QUARANTINE_LEGACY_EVIDENCE_POLICY=AUTHOR_EVALUATION_QUARANTINE_LEGACY_EVIDENCE_POLICY,
        AUTHOR_EVALUATION_QUARANTINE_PREVIOUS_EVIDENCE_POLICY=AUTHOR_EVALUATION_QUARANTINE_PREVIOUS_EVIDENCE_POLICY,
        AUTHOR_EVALUATION_QUARANTINE_SEEDED_EVIDENCE_POLICY=AUTHOR_EVALUATION_QUARANTINE_SEEDED_EVIDENCE_POLICY,
        AUTHOR_EVALUATION_QUARANTINE_SINGLE_SOL_V1_EVIDENCE_POLICY=AUTHOR_EVALUATION_QUARANTINE_SINGLE_SOL_V1_EVIDENCE_POLICY,
        MAX_REASONABLE_STATE_EPOCH=MAX_REASONABLE_STATE_EPOCH,
        author_no_reply_epoch_limit=author_no_reply_epoch_limit,
        log=log,
    )


def normalise_optional_scalar(value: object, *, key: str, path: Path) -> str | None:
    """Normalise optional scalar."""
    return _state_value_normalisation.normalise_optional_scalar(
        value,
        key=key,
        path=path,
        log=log,
    )


def normalise_optional_numeric_id(value: object, *, key: str, path: Path) -> str | None:
    """Normalise optional numeric ID."""
    return _state_value_normalisation.normalise_optional_numeric_id(
        value,
        key=key,
        path=path,
        bounded_tweet_id_value=bounded_tweet_id_value,
        log=log,
    )


def validate_meme_schedule_state(state: dict, *, path: Path) -> bool:
    """Validate meme schedule state."""
    next_epoch = int(state.get("next_meme_post_epoch", 0) or 0)
    if not next_epoch:
        return True
    if not valid_receipt_epoch(next_epoch):
        log.error("State candidate %s has receipt-incompatible active meme target epoch %s; ignoring", path, next_epoch)
        return False

    mode = str(state.get("next_meme_schedule_mode", "") or "")
    schedule_date = str(state.get("next_meme_schedule_date", "") or "")
    anchor_epoch = int(state.get("meme_anchor_quote_post_epoch", 0) or 0)

    if mode not in MEME_SCHEDULE_MODES or not mode:
        log.error("State candidate %s has invalid meme schedule mode %r; ignoring", path, mode)
        return False

    if mode == "after_first_quote_after_midday":
        if anchor_epoch <= 0:
            log.error("State candidate %s has quote-anchored meme schedule without anchor; ignoring", path)
            return False
        if not valid_receipt_epoch(anchor_epoch):
            log.error("State candidate %s has receipt-incompatible meme anchor epoch %s; ignoring", path, anchor_epoch)
            return False
        if next_epoch <= anchor_epoch:
            log.error("State candidate %s has quote-anchored meme target not after anchor; ignoring", path)
            return False
        expected_date = safe_bound_schedule_date_str(
            anchor_epoch,
            MAIN_POST_SCHEDULE_TIMEZONE,
        )
        if not expected_date or schedule_date != expected_date:
            log.error(
                "State candidate %s has quote-anchored meme schedule_date=%r expected=%r; ignoring",
                path,
                schedule_date,
                expected_date,
            )
            return False
        return True

    if anchor_epoch:
        log.error("State candidate %s has non-quote meme schedule with stale quote anchor; ignoring", path)
        return False
    expected_date = safe_bound_schedule_date_str(
        next_epoch,
        MAIN_POST_SCHEDULE_TIMEZONE,
    )
    if not expected_date or schedule_date != expected_date:
        log.error(
            "State candidate %s has meme schedule_date=%r expected=%r for mode=%s; ignoring",
            path,
            schedule_date,
            expected_date,
            mode,
        )
        return False
    return True


def validate_meme_schedule_version_for_candidate(state: dict, *, path: Path) -> bool:
    """Validate meme schedule version for candidate."""
    version = int(state.get("meme_schedule_version", 0) or 0)
    if version > MEME_SCHEDULE_VERSION:
        log.error(
            "State candidate %s has future meme_schedule_version=%s > supported=%s; ignoring",
            path,
            version,
            MEME_SCHEDULE_VERSION,
        )
        return False
    if version < MEME_SCHEDULE_VERSION:
        log.info(
            "State candidate %s has old meme_schedule_version=%s; deferring schedule validation to migration",
            path,
            version,
        )
        return True
    return validate_meme_schedule_state(state, path=path)


class IncompatibleStateReaderError(RuntimeError):
    """Raised when durable state requires a newer executable."""


def require_compatible_state_reader(
    state: dict,
    *,
    path: Path,
    reader_version: int | None = None,
) -> int:
    """Return the declared minimum after rejecting an incompatible reader."""
    raw_minimum = state.get("minimum_reader_version", 1)
    if type(raw_minimum) is not int or raw_minimum < 1:
        raise IncompatibleStateReaderError(
            f"State candidate {path} has invalid minimum reader version "
            f"{raw_minimum!r}"
        )
    supported = STATE_READER_VERSION if reader_version is None else reader_version
    if type(supported) is not int or supported < 1:
        raise ValueError("reader_version must be a positive integer")
    if raw_minimum > supported:
        raise IncompatibleStateReaderError(
            f"State candidate {path} requires minimum reader version "
            f"{raw_minimum}, but this executable supports {supported}; refusing "
            "state mutation and backup fallback"
        )
    return raw_minimum


def state_document_for_persistence(state: dict) -> dict:
    """Return state with the reader declaration and pre-reader rollback fence."""
    minimum = require_compatible_state_reader(state, path=STATE_FILE)
    legacy_drafts = state.get("pending_reply_drafts")
    if legacy_drafts not in (
        None,
        {},
        STATE_READER_COMPATIBILITY_FENCE,
        *STATE_PREVIOUS_READER_COMPATIBILITY_FENCES,
    ):
        raise RuntimeError(
            "Legacy V1 reply drafts remain in runtime state; refusing to "
            "overwrite them with the reader compatibility fence"
        )
    document = dict(state)
    experiment_state = document.get("engagement_question_experiment")
    if experiment_state is not None:
        engagement_question_trial.validate_experiment_state(experiment_state)
    document["minimum_reader_version"] = max(
        minimum,
        STATE_MINIMUM_READER_VERSION,
        (
            ENGAGEMENT_QUESTION_EXPERIMENT_STATE_MINIMUM_READER_VERSION
            if experiment_state is not None
            else STATE_MINIMUM_READER_VERSION
        ),
    )
    document["pending_reply_drafts"] = copy.deepcopy(
        STATE_READER_COMPATIBILITY_FENCE
    )
    return document


def normalise_state_candidate(
    state: dict,
    *,
    path: Path,
    recovery_events: list[dict[str, object]] | None = None,
    recover_pending_identity: bool = False,
) -> dict | None:
    """Normalise state candidate."""
    minimum_reader_version = require_compatible_state_reader(state, path=path)
    list_keys = {
        "replied_to_ids",
        "dry_run_seen_mention_ids",
        "skipped_hot_reply_ids",
        "daily_replied_author_ids",
        "own_auto_reply_ids",
        "posted_meme_filenames",
        "recent_own_post_ids",
        "seen_quote_post_ids",
        "replied_to_quote_post_ids",
        "skipped_quote_post_ids",
        "quote_spam_author_ids",
    }
    epoch_list_keys = {
        "x_error_epochs",
        "x_write_error_epochs",
        "openai_error_epochs",
        "quote_x_error_epochs",
    }
    string_map_keys = {
        "hot_post_reply_since_ids",
        "hot_post_reply_pagination_tokens",
        "quote_lookup_pagination_tokens",
    }
    int_map_keys = {"hot_post_reply_check_counts", "daily_replied_author_counts"}
    record_map_keys = {
        "skipped_hot_reply_records",
        "pending_ai_reply_drafts",
        "reply_evaluation_records",
        "clarification_reply_records",
    }
    optional_scalar_keys = {
        "daily_reply_date",
        "daily_quote_reply_date",
        "last_regular_image_filename",
    }
    optional_numeric_id_keys = {
        "last_seen_mention_id",
        "last_main_post_id",
    }
    int_keys = {
        "daily_reply_count",
        "meme_schedule_version",
        "daily_quote_reply_count",
        "original_regular_posts_since_generated_image",
    }
    epoch_keys = {
        "last_meme_post_epoch",
        "next_meme_post_epoch",
        "meme_anchor_quote_post_epoch",
        "last_reply_epoch",
        "last_quote_post_epoch",
        "next_quote_post_epoch",
        "api_cooldown_until_epoch",
        "x_write_api_cooldown_until_epoch",
        "openai_api_cooldown_until_epoch",
        "quote_api_cooldown_until_epoch",
    }

    normalised = default_state()
    normalised.update(state)
    normalised["minimum_reader_version"] = max(
        minimum_reader_version,
        STATE_MINIMUM_READER_VERSION,
    )

    if "engagement_question_experiment" in state:
        try:
            normalised["engagement_question_experiment"] = (
                engagement_question_trial.validate_experiment_state(
                    state["engagement_question_experiment"]
                )
            )
        except engagement_question_trial.ExperimentValidationError:
            log.error(
                "State candidate %s has invalid engagement-question experiment state; ignoring",
                path,
                exc_info=True,
            )
            return None
        normalised["minimum_reader_version"] = max(
            normalised["minimum_reader_version"],
            ENGAGEMENT_QUESTION_EXPERIMENT_STATE_MINIMUM_READER_VERSION,
        )

    for key in list_keys:
        if key in state:
            value = normalise_string_list(state[key], key=key, path=path)
            if value is None:
                return None
            normalised[key] = value
    for key in epoch_list_keys:
        if key in state:
            value = normalise_epoch_list(state[key], key=key, path=path)
            if value is None:
                return None
            normalised[key] = value
    for key in string_map_keys:
        if key in state:
            value = normalise_string_map(state[key], key=key, path=path)
            if value is None:
                return None
            normalised[key] = value
    if "quote_lookup_repeated_cursor_suppressions" in state:
        value, discarded = normalise_quote_repeated_cursor_suppressions(
            state["quote_lookup_repeated_cursor_suppressions"]
        )
        normalised["quote_lookup_repeated_cursor_suppressions"] = value
        if discarded and recovery_events is not None:
            recovery_events.append({
                "kind": "quote_cursor_suppression_pruned",
                "discarded_entries": discarded,
            })
    for key in int_map_keys:
        if key in state:
            value = normalise_int_map(state[key], key=key, path=path)
            if value is None:
                return None
            normalised[key] = value
    for key in record_map_keys:
        if key in state:
            value = normalise_record_map(state[key], key=key, path=path)
            if value is None:
                return None
            normalised[key] = value
    if "mention_pending_candidates" in state:
        pending_value = canonical_mention_pending_candidates(
            state["mention_pending_candidates"],
            path=path,
        )
        if pending_value is None and not recover_pending_identity:
            return None
        if pending_value is not None:
            normalised["mention_pending_candidates"] = pending_value
    for key in optional_scalar_keys:
        if key in state:
            value = normalise_optional_scalar(state[key], key=key, path=path)
            if value is None:
                return None
            normalised[key] = value or None
    for key in optional_numeric_id_keys:
        if key in state:
            value = normalise_optional_numeric_id(state[key], key=key, path=path)
            if value is None:
                return None
            normalised[key] = value or None
    if "tweet_cache" in state:
        value = normalise_tweet_cache(state["tweet_cache"], path=path)
        if value is None:
            return None
        normalised["tweet_cache"] = value
    if "reply_strategy_history" in state:
        history = state["reply_strategy_history"]
        if not isinstance(history, list) or any(not isinstance(item, dict) for item in history):
            log.error("State candidate %s has invalid reply_strategy_history; ignoring", path)
            return None
        normalised["reply_strategy_history"] = history[-1000:]
    if "ai_reply_history" in state:
        history = state["ai_reply_history"]
        if not isinstance(history, list) or any(not isinstance(item, dict) for item in history):
            log.error("State candidate %s has invalid ai_reply_history; ignoring", path)
            return None
        normalised["ai_reply_history"] = history[-1000:]
    if "mention_pagination" in state:
        value = normalise_mention_pagination(state["mention_pagination"], path=path)
        if value is None:
            return None
        normalised["mention_pagination"] = value
    if "mention_backlog_reset_guard" in state:
        value = normalise_mention_backlog_reset_guard(
            state["mention_backlog_reset_guard"],
            path=path,
        )
        if value is None:
            return None
        normalised["mention_backlog_reset_guard"] = value
    if "mention_backlog" in state:
        raw_backlog = state["mention_backlog"]
        token_overflow = (
            isinstance(raw_backlog, dict)
            and isinstance(raw_backlog.get("seen_tokens"), list)
            and len(raw_backlog["seen_tokens"])
            > MENTION_BACKLOG_CONTINUATION_TOKEN_LIMIT
        )
        value = normalise_mention_backlog(
            raw_backlog,
            path=path,
            reset_token_overflow=token_overflow,
        )
        if value is None:
            return None
        normalised["mention_backlog"] = value
        if token_overflow:
            normalised["mention_pagination"] = {}
            normalised["mention_pending_candidates"] = {}
            normalised["mention_backlog_reset_guard"] = {
                "base_since_id": str(
                    normalised.get("last_seen_mention_id") or ""
                ),
                "head_traversal_started": False,
            }
            if recovery_events is not None:
                recovery_events.append({
                    "reason": "continuation_token_limit",
                    "since_id": str(raw_backlog.get("since_id") or "") or None,
                    "pages_completed": raw_backlog.get("pages_completed"),
                    "token_fingerprint": hashlib.sha256(
                        str(raw_backlog.get("next_token") or "").encode("utf-8")
                    ).hexdigest()[:16],
                })
    # Retire legacy quarantine-only terminal records before they can suppress
    # a completed-page pending candidate that the watermark will not refetch.
    prune_reply_evaluation_records(normalised)
    pending_authority_usable, _pending_authority_changed = (
        validate_pending_mention_candidate_authority(
            normalised,
            path=path,
            recover_pending_identity=recover_pending_identity,
            recovery_events=recovery_events,
        )
    )
    if not pending_authority_usable:
        return None
    if "author_evaluation_quarantines" in state:
        value = normalise_author_evaluation_quarantines(
            state["author_evaluation_quarantines"],
            path=path,
        )
        if value is None:
            return None
        normalised["author_evaluation_quarantines"] = value
    for key in int_keys:
        if key not in state:
            continue
        value = normalise_state_int(state[key], key=key, path=path)
        if value is None:
            return None
        normalised[key] = value
    if "original_regular_posts_since_generated_image" not in state:
        last_regular_image = str(normalised.get("last_regular_image_filename") or "")
        if generated_image_origin_quote_hash(last_regular_image):
            normalised["original_regular_posts_since_generated_image"] = 0
        else:
            normalised["original_regular_posts_since_generated_image"] = generated_image_spacing_required()
    for key in epoch_keys:
        if key not in state:
            continue
        value = normalise_state_epoch(state[key], key=key, path=path)
        if value is None:
            return None
        normalised[key] = value

    if not validate_meme_schedule_version_for_candidate(normalised, path=path):
        return None

    prune_author_evaluation_quarantines(normalised)

    return normalised


def load_state() -> dict:
    """Load, validate, and recover runtime state from durable storage."""
    log.debug("Loading state from %s", STATE_FILE)

    candidates = [STATE_FILE]
    candidates.extend(STATE_FILE.with_name(f"{STATE_FILE.name}.bak{i}") for i in range(1, STATE_BACKUP_COUNT + 1))

    existing_candidates = False
    candidate_recoveries: dict[Path, list[dict[str, object]]] = {}

    def emit_candidate_recoveries(candidate: Path) -> None:
        for recovery in candidate_recoveries.get(candidate, []):
            if recovery.get("kind") == "quote_cursor_suppression_pruned":
                log.info(
                    "Pruned %s malformed, expired, or excess quote cursor "
                    "suppression entry or entries while loading %s",
                    recovery.get("discarded_entries"),
                    candidate,
                )
                continue
            reason = recovery.get("reason")
            if reason == "orphaned_pending_candidates":
                log.warning(
                    "Discarding %s uncovered pending mention candidate(s) "
                    "without pagination provenance while loading %s; watermark "
                    "remains unchanged and a reset guard was installed",
                    recovery.get("discarded_candidates"),
                    candidate,
                )
            elif reason == "continuation_token_limit":
                log.warning(
                    "Resetting oversized mention backlog while loading %s; "
                    "watermark remains unchanged and pending candidates were discarded",
                    candidate,
                )
            else:
                log.warning(
                    "Resetting unsafe mention candidate authority while loading %s "
                    "reason=%s; watermark remains unchanged",
                    candidate,
                    reason,
                )
            log_event("mention_backlog_reset", **recovery)

    def persist_candidate_recoveries(candidate: Path, state: dict) -> None:
        """Commit safe state repairs before any post-load provider work."""
        if not candidate_recoveries.get(candidate):
            return
        save_state(state, durable=True)

    def load_candidate(
        candidate: Path,
        *,
        reject_legacy: bool,
        recover_pending_identity: bool = False,
    ) -> dict | None:
        nonlocal existing_candidates
        try:
            present, data = read_stable_owned_json_bytes_no_follow(candidate)
        except UnsafeDurableStateNamespace:
            existing_candidates = True
            log.critical(
                "State candidate namespace is unsafe; refusing backup fallback: %s",
                candidate,
                exc_info=True,
            )
            raise
        if not present or data is None:
            log.warning("State file candidate does not exist: %s", candidate)
            return None
        existing_candidates = True

        try:
            state = json.loads(data.decode("utf-8"))
        except Exception:
            log.exception("Failed loading state candidate %s", candidate)
            return None

        if not isinstance(state, dict):
            log.error("State file candidate %s is not a JSON object; ignoring", candidate)
            return None
        minimum_reader_version = require_compatible_state_reader(
            state,
            path=candidate,
        )
        legacy_drafts = state.get("pending_reply_drafts")
        if minimum_reader_version >= STATE_MINIMUM_READER_VERSION:
            if legacy_drafts != STATE_READER_COMPATIBILITY_FENCE:
                message = (
                    f"State candidate {candidate} declares minimum reader version "
                    f"{minimum_reader_version} without the exact compatibility "
                    "fence; refusing unsafe rollback state"
                )
                if reject_legacy:
                    log.critical(message)
                    raise RuntimeError(message)
                log.warning(
                    "%s; candidate is not needed because primary state is usable",
                    message,
                )
                return None
        elif (
            legacy_drafts not in (None, {})
            and legacy_drafts not in STATE_PREVIOUS_READER_COMPATIBILITY_FENCES
        ):
            message = (
                f"Legacy V1 reply drafts remain in {candidate}; refusing to interpret or post them "
                "through the AI-first strategy"
            )
            if reject_legacy:
                log.critical(message)
                raise RuntimeError(message)
            log.warning("%s; candidate is not needed because primary state is usable", message)
            return None
        state.pop("pending_reply_drafts", None)
        state["minimum_reader_version"] = max(
            minimum_reader_version,
            STATE_MINIMUM_READER_VERSION,
        )
        recovery_events: list[dict[str, object]] = []
        normalised = normalise_state_candidate(
            state,
            path=candidate,
            recovery_events=recovery_events,
            recover_pending_identity=recover_pending_identity,
        )
        if normalised is not None:
            candidate_recoveries[candidate] = recovery_events
        return normalised

    latest_backup_path = STATE_FILE.with_name(f"{STATE_FILE.name}.bak1")
    primary = load_candidate(STATE_FILE, reject_legacy=True)
    if primary is not None:
        latest_backup = (
            load_candidate(latest_backup_path, reject_legacy=False)
            if STATE_BACKUP_COUNT > 0
            else None
        )
        if latest_backup is not None and latest_backup != primary:
            message = (
                "Primary state and latest committed backup are both valid but "
                "diverge; refusing to guess which durable generation is newer"
            )
            log.critical(
                "%s primary=%s backup=%s",
                message,
                STATE_FILE,
                latest_backup_path,
            )
            raise RuntimeError(message)
        emit_candidate_recoveries(STATE_FILE)
        persist_candidate_recoveries(STATE_FILE, primary)
        log_json_debug("Loaded state summary", state_debug_summary(primary))
        return primary

    # Only inspect backups until the first usable generation is found.  Older
    # snapshots are recovery fallbacks, not vetoes over a newer usable state.
    for candidate in candidates[1:]:
        recovered = load_candidate(candidate, reject_legacy=True)
        if recovered is None:
            continue
        log.warning("Recovered state from backup %s", candidate)
        emit_candidate_recoveries(candidate)
        persist_candidate_recoveries(candidate, recovered)
        log_json_debug("Loaded state summary", state_debug_summary(recovered))
        return recovered

    if existing_candidates:
        # Pending identity corruption is recoverable only after every strict
        # candidate has failed, so a usable backup always remains authoritative.
        for candidate in candidates:
            recovered = load_candidate(
                candidate,
                reject_legacy=True,
                recover_pending_identity=True,
            )
            if recovered is None:
                continue
            log.warning(
                "Recovered state candidate %s by discarding corrupt pending "
                "mention identity and requiring a head refetch",
                candidate,
            )
            emit_candidate_recoveries(candidate)
            persist_candidate_recoveries(candidate, recovered)
            log_json_debug("Loaded state summary", state_debug_summary(recovered))
            return recovered
        message = "Existing state file(s) found but no usable state or backup; refusing to start with empty state"
        log.critical(message)
        raise RuntimeError(message)

    log.error("No state file or backup found; using default state")
    return default_state()


def scheduler_epoch_from_state(state: dict, key: str, *, current: int | None = None) -> tuple[int, bool]:
    """Return the scheduler epoch from state."""
    raw_value = state.get(key, 0)
    malformed = isinstance(raw_value, bool) or (
        isinstance(raw_value, float)
        and (not math.isfinite(raw_value) or not raw_value.is_integer())
    )
    try:
        value = 0 if malformed else int(raw_value or 0)
    except (TypeError, ValueError, OverflowError):
        malformed = True
        value = 0
    if malformed:
        log.warning("Ignoring malformed scheduler epoch %s=%r", key, raw_value)

    if value < 0:
        log.warning("Ignoring negative scheduler epoch %s=%r", key, raw_value)
        value = 0

    if current is not None and value > current:
        log.warning("Ignoring future scheduler epoch %s=%r current=%s", key, raw_value, current)
        value = 0

    if malformed or type(raw_value) is not int or raw_value != value:
        state[key] = value
        return value, True

    return value, False


def copy_state_backup(src: Path, dst: Path, *, durable: bool = False) -> None:
    """Copy one exact stable state generation without following links."""

    present, data = read_stable_owned_json_bytes_no_follow(src)
    if not present or data is None:
        raise UnsafeDurableStateNamespace(
            f"state backup source disappeared before copying: {src}"
        )
    dst.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{dst.name}.",
        dir=dst.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(data)
            if durable:
                handle.flush()
                os.fsync(handle.fileno())
        os.replace(temporary, dst)
        if durable:
            fsync_parent_dir(dst, strict=True)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def rotate_state_backups_before_commit(*, durable: bool = False) -> None:
    """Rotate state backups before commit."""
    if STATE_BACKUP_COUNT <= 1 or not STATE_FILE.exists():
        return

    try:
        for i in range(STATE_BACKUP_COUNT, 2, -1):
            older = STATE_FILE.with_name(f"{STATE_FILE.name}.bak{i - 1}")
            newer = STATE_FILE.with_name(f"{STATE_FILE.name}.bak{i}")
            if older.exists():
                older.replace(newer)

        bak2 = STATE_FILE.with_name(f"{STATE_FILE.name}.bak2")
        copy_state_backup(STATE_FILE, bak2, durable=durable)
        log.debug("Previous state backup written: %s", bak2)
    except Exception:
        log.exception("Failed rotating state backups; continuing with state save")


def write_latest_state_backup(*, durable: bool = False) -> None:
    """Write latest state backup."""
    if STATE_BACKUP_COUNT <= 0 or not STATE_FILE.exists():
        return
    bak1 = STATE_FILE.with_name(f"{STATE_FILE.name}.bak1")
    copy_state_backup(STATE_FILE, bak1, durable=durable)
    log.debug("Latest committed state backup written: %s", bak1)


class StateBackupWriteError(RuntimeError):
    """Raised after canonical state commits but its latest backup write fails."""
    pass


def save_state(state: dict, *, durable: bool = False) -> None:
    """Persist state atomically, logging only a value-free structural summary."""
    if test_process_production_state_write_blocked(STATE_FILE):
        raise RuntimeError(f"Refusing test-process write to production state: {STATE_FILE}")
    log.debug("Saving state to %s", STATE_FILE)
    log_json_debug("State summary being saved", state_debug_summary(state))
    persisted_state = state_document_for_persistence(state)

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{STATE_FILE.name}.",
        dir=STATE_FILE.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            json.dump(persisted_state, handle, indent=2, sort_keys=True)
            if durable:
                handle.flush()
                os.fsync(handle.fileno())

        rotate_state_backups_before_commit(durable=durable)
        os.replace(temporary, STATE_FILE)
        if durable:
            fsync_parent_dir(STATE_FILE, strict=durable)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise
    try:
        write_latest_state_backup(durable=durable)
    except Exception as exc:
        raise StateBackupWriteError(
            f"Canonical state committed but latest backup write failed: {STATE_FILE}"
        ) from exc


def reset_daily_reply_count_if_needed(state: dict) -> None:
    """Delegate reply-lane policy with current root dependencies."""
    return _reply_lane_policy.reset_daily_reply_count_if_needed(
        state,
        log=log,
        reply_cap_date_str=reply_cap_date_str,
    )


def reset_daily_quote_reply_count_if_needed(state: dict) -> None:
    """Delegate reply-lane policy with current root dependencies."""
    return _reply_lane_policy.reset_daily_quote_reply_count_if_needed(
        state,
        log=log,
        reply_cap_date_str=reply_cap_date_str,
    )


daily_author_reply_counts = _reply_lane_policy.daily_author_reply_counts


def daily_author_reply_count(state: dict, author_id: str) -> int:
    """Delegate reply-lane policy with current root dependencies."""
    return _reply_lane_policy.daily_author_reply_count(
        state,
        author_id,
        daily_author_reply_counts=daily_author_reply_counts,
    )


def mark_daily_author_replied(state: dict, author_id: str) -> None:
    """Delegate reply-lane policy with current root dependencies."""
    return _reply_lane_policy.mark_daily_author_replied(
        state,
        author_id,
        append_unique_capped=append_unique_capped,
        daily_author_reply_counts=daily_author_reply_counts,
    )


CLARIFICATION_CUE_RE = _reply_lane_policy.CLARIFICATION_CUE_RE
CLARIFICATION_TOKEN_RE = _reply_lane_policy.CLARIFICATION_TOKEN_RE
CLARIFICATION_TOKEN_STOPWORDS = _reply_lane_policy.CLARIFICATION_TOKEN_STOPWORDS


clarification_thread_id = _reply_lane_policy.clarification_thread_id


def clarification_thread_is_terminal(state: dict, candidate: dict) -> bool:
    """Delegate reply-lane policy with current root dependencies."""
    return _reply_lane_policy.clarification_thread_is_terminal(
        state,
        candidate,
        clarification_thread_id=clarification_thread_id,
    )


def author_used_clarification_recently(state: dict, author_id: str, *, current: int) -> bool:
    """Delegate reply-lane policy with current root dependencies."""
    return _reply_lane_policy.author_used_clarification_recently(
        state,
        author_id,
        current=current,
        CLARIFICATION_REPLY_WINDOW_SECONDS=CLARIFICATION_REPLY_WINDOW_SECONDS,
    )


def _clarification_tokens(text: object) -> set[str]:
    """Delegate reply-lane policy with current root dependencies."""
    return _reply_lane_policy._clarification_tokens(
        text,
        CLARIFICATION_TOKEN_RE=CLARIFICATION_TOKEN_RE,
        CLARIFICATION_TOKEN_STOPWORDS=CLARIFICATION_TOKEN_STOPWORDS,
        re=re,
    )


def clarification_reply_context(
    state: dict,
    candidate: dict,
    *,
    current: int,
) -> dict | None:
    """Delegate reply-lane policy with current root dependencies."""
    return _reply_lane_policy.clarification_reply_context(
        state,
        candidate,
        current=current,
        ApiError=ApiError,
        CLARIFICATION_CUE_RE=CLARIFICATION_CUE_RE,
        _clarification_tokens=_clarification_tokens,
        author_used_clarification_recently=author_used_clarification_recently,
        clarification_thread_id=clarification_thread_id,
        clarification_thread_is_terminal=clarification_thread_is_terminal,
        conversational_reply_pipeline_enabled=conversational_reply_pipeline_enabled,
        get_immediate_parent_id=get_immediate_parent_id,
        is_our_auto_reply=is_our_auto_reply,
    )


# ---------------------------------------------------------------------
# Time / cooldown / error handling
# ---------------------------------------------------------------------

def now_epoch() -> int:
    """Return the now epoch."""
    if TEST_MODE and os.getenv("MRS_FAKE_NOW_EPOCH"):
        return int(os.getenv("MRS_FAKE_NOW_EPOCH", "0"))
    return int(datetime.now().timestamp())


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


def in_api_cooldown(state: dict, *, scope: str = "api") -> bool:
    """Return the in API cooldown."""
    return _api_cooldowns.in_api_cooldown(
        state,
        scope=scope,
        datetime=datetime,
        log=log,
        now_epoch=now_epoch,
    )


def clear_expired_api_cooldowns(state: dict) -> bool:
    """Clear expired API cooldowns."""
    return _api_cooldowns.clear_expired_api_cooldowns(
        state,
        log=log,
        now_epoch=now_epoch,
    )


def sanitize_next_reply_lane_priority(state: dict) -> bool:
    """Sanitise next reply lane priority."""
    return _tick_coordination.sanitize_next_reply_lane_priority(
        state,
        log=log,
    )


def load_runtime_state() -> dict:
    """Load runtime state and apply daily maintenance safely."""
    state = load_state()
    clear_expired_api_cooldowns(state)
    sanitize_next_reply_lane_priority(state)
    return state


def prune_error_epochs(epochs: list[int]) -> list[int]:
    """Prune error epochs."""
    return _api_cooldowns.prune_error_epochs(
        epochs,
        ERROR_WINDOW_SECONDS=ERROR_WINDOW_SECONDS,
        log=log,
        now_epoch=now_epoch,
    )


def cooldown_until_for_rate_limit(current: int, reset_epoch: int | None) -> int:
    """Return the cooldown until for rate limit."""
    return _api_cooldowns.cooldown_until_for_rate_limit(
        current,
        reset_epoch,
        COOLDOWN_AFTER_429_SECONDS=COOLDOWN_AFTER_429_SECONDS,
    )


def record_api_error(state: dict, error: Exception, service: str, *, scope: str = "api") -> None:
    """Record API error."""
    return _api_cooldowns.record_api_error(
        state,
        error,
        service,
        scope=scope,
        COOLDOWN_AFTER_REPEATED_ERRORS_SECONDS=COOLDOWN_AFTER_REPEATED_ERRORS_SECONDS,
        MAX_OPENAI_ERRORS_PER_WINDOW=MAX_OPENAI_ERRORS_PER_WINDOW,
        MAX_X_ERRORS_PER_WINDOW=MAX_X_ERRORS_PER_WINDOW,
        api_error_is_reply_not_allowed=api_error_is_reply_not_allowed,
        cooldown_until_for_rate_limit=cooldown_until_for_rate_limit,
        datetime=datetime,
        log=log,
        now_epoch=now_epoch,
        prune_error_epochs=prune_error_epochs,
        save_state=save_state,
    )


# ---------------------------------------------------------------------
# X API helpers
# ---------------------------------------------------------------------

def x_request_base_url(method: str, path: str) -> str:
    """Select the configured origin for one literal X request.

    Only the exact authorised v2 media-upload write uses the optional upload
    origin.  Reads, tweet creation and every non-literal spelling stay on the
    primary X API origin.
    """
    return _request_route_values.x_request_base_url(
        method,
        path,
        X_BASE=X_BASE,
        X_UPLOAD_BASE=X_UPLOAD_BASE,
    )


def normalised_prepared_x_request_path(method: str, path: str) -> str:
    """Return the conservative path which Requests will place on the wire.

    ``requests`` normalises dot segments and some percent-encoded characters
    while preparing a request.  Security decisions made against the caller's
    unprepared string can therefore misclassify a tweet-create target.  Decode
    repeatedly as a conservative allowance for an upstream HTTP router doing
    another decoding pass, normalise separators/dot segments, and collapse
    repeated slashes before comparing protected endpoints.
    """
    return _request_route_values.normalised_prepared_x_request_path(
        method,
        path,
        AmbiguousRemotePostOutcome=AmbiguousRemotePostOutcome,
        posixpath=posixpath,
        re=re,
        requests=requests,
        unquote=unquote,
        urlsplit=urlsplit,
        x_request_base_url=x_request_base_url,
    )


def x_request_targets_tweet_create(method: str, path: str) -> bool:
    """Return whether one prepared X request targets the tweet-create route."""
    return _request_route_values.x_request_targets_tweet_create(
        method,
        path,
        prepared_x_create_route=prepared_x_create_route,
    )


def x_request_targets_media_upload(method: str, path: str) -> bool:
    """Return whether one prepared X request targets the v2 media-create route."""
    return _request_route_values.x_request_targets_media_upload(
        method,
        path,
        prepared_x_create_route=prepared_x_create_route,
    )


def prepared_x_create_route(method: str, path: str) -> str | None:
    """Classify the create route produced by Requests preparation."""
    return _request_route_values.prepared_x_create_route(
        method,
        path,
        normalised_prepared_x_request_path=normalised_prepared_x_request_path,
    )


exact_x_create_route = _request_route_values.exact_x_create_route


def frozen_strict_json_object(value: object, *, label: str) -> dict:
    """Return an isolated strict-JSON copy suitable for request transport."""
    return _request_route_values.frozen_strict_json_object(
        value,
        label=label,
        AmbiguousRemotePostOutcome=AmbiguousRemotePostOutcome,
        json=json,
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
    log.warning("Rate Limit: %s", response.headers.get("x-rate-limit-limit"))
    log.warning("Remaining: %s", response.headers.get("x-rate-limit-remaining"))

    reset_time = response.headers.get("x-rate-limit-reset")
    if not reset_time:
        return None

    try:
        reset_epoch = int(reset_time)
    except (TypeError, ValueError):
        log.warning("Rate Limit Resets At: %s", reset_time)
        return None

    try:
        reset_time_human = datetime.fromtimestamp(reset_epoch).strftime("%Y-%m-%d %H:%M:%S")
        log.warning("Rate Limit Resets At: %s", reset_time_human)
        return reset_epoch
    except Exception:
        log.warning("Rate Limit Resets At invalid epoch: %s", reset_time)
        return None


X_CREATE_RESPONSE_ANOMALY_EVENT = _x_response_diagnostics.X_CREATE_RESPONSE_ANOMALY_EVENT
X_CREATE_RESPONSE_ANOMALY_SCHEMA_VERSION = _x_response_diagnostics.X_CREATE_RESPONSE_ANOMALY_SCHEMA_VERSION
X_CREATE_RESPONSE_ANOMALY_BODY_MAX_BYTES = _x_response_diagnostics.X_CREATE_RESPONSE_ANOMALY_BODY_MAX_BYTES
X_CREATE_RESPONSE_SAFE_CORRELATION_HEADERS = _x_response_diagnostics.X_CREATE_RESPONSE_SAFE_CORRELATION_HEADERS
_X_CREATE_RESPONSE_HEADER_MAX_CHARACTERS = _x_response_diagnostics._X_CREATE_RESPONSE_HEADER_MAX_CHARACTERS
_X_CREATE_RESPONSE_ID_VALUE_MAX_CHARACTERS = _x_response_diagnostics._X_CREATE_RESPONSE_ID_VALUE_MAX_CHARACTERS
_X_CREATE_RESPONSE_TOP_LEVEL_KEY_LIMIT = _x_response_diagnostics._X_CREATE_RESPONSE_TOP_LEVEL_KEY_LIMIT
_X_CREATE_RESPONSE_TOP_LEVEL_KEY_MAX_CHARACTERS = _x_response_diagnostics._X_CREATE_RESPONSE_TOP_LEVEL_KEY_MAX_CHARACTERS


def x_create_response_anomaly_reason(decoded: object) -> str | None:
    """Return why one decoded tweet-create response cannot confirm success."""

    return _x_response_diagnostics.x_create_response_anomaly_reason(
        decoded,
        valid_post_id=valid_post_id,
    )


_x_create_diagnostic_json_type = _x_response_diagnostics._x_create_diagnostic_json_type


_bounded_x_create_diagnostic_text = _x_response_diagnostics._bounded_x_create_diagnostic_text


def _x_create_response_elapsed_ms(response: requests.Response) -> int | None:
    """Return a finite non-negative Requests elapsed duration in milliseconds."""

    return _x_response_diagnostics._x_create_response_elapsed_ms(
        response,
        math=math,
    )


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

    return _x_response_diagnostics.emit_x_create_response_anomaly(
        response=response,
        transport_authority=transport_authority,
        request_payload=request_payload,
        reason=reason,
        raw_body=raw_body,
        json_decode_succeeded=json_decode_succeeded,
        decoded=decoded,
        json_error=json_error,
        X_CREATE_RESPONSE_ANOMALY_BODY_MAX_BYTES=X_CREATE_RESPONSE_ANOMALY_BODY_MAX_BYTES,
        X_CREATE_RESPONSE_ANOMALY_EVENT=X_CREATE_RESPONSE_ANOMALY_EVENT,
        X_CREATE_RESPONSE_ANOMALY_SCHEMA_VERSION=X_CREATE_RESPONSE_ANOMALY_SCHEMA_VERSION,
        X_CREATE_RESPONSE_SAFE_CORRELATION_HEADERS=X_CREATE_RESPONSE_SAFE_CORRELATION_HEADERS,
        _X_CREATE_RESPONSE_HEADER_MAX_CHARACTERS=_X_CREATE_RESPONSE_HEADER_MAX_CHARACTERS,
        _X_CREATE_RESPONSE_ID_VALUE_MAX_CHARACTERS=_X_CREATE_RESPONSE_ID_VALUE_MAX_CHARACTERS,
        _X_CREATE_RESPONSE_TOP_LEVEL_KEY_LIMIT=_X_CREATE_RESPONSE_TOP_LEVEL_KEY_LIMIT,
        _X_CREATE_RESPONSE_TOP_LEVEL_KEY_MAX_CHARACTERS=_X_CREATE_RESPONSE_TOP_LEVEL_KEY_MAX_CHARACTERS,
        _bounded_x_create_diagnostic_text=_bounded_x_create_diagnostic_text,
        _x_create_diagnostic_json_type=_x_create_diagnostic_json_type,
        _x_create_response_elapsed_ms=_x_create_response_elapsed_ms,
        base64=base64,
        hashlib=hashlib,
        json=json,
        log=log,
        math=math,
        now_epoch=now_epoch,
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
    """Send an authenticated X API request with bounded retries."""
    url = f"{x_request_base_url(method, path)}{path}"
    prepared_create_route = prepared_x_create_route(method, path)
    exact_create_route = exact_x_create_route(method, path)
    if prepared_create_route != exact_create_route:
        raise AmbiguousRemotePostOutcome(
            "Prepared and literal X create-route classifications disagree",
            service="x",
            request_method=method,
            request_path=path,
        )
    is_post_create = prepared_create_route == "tweet"
    is_media_upload = prepared_create_route == "media"
    method_upper = str(method).upper()
    if (is_post_create or is_media_upload) and exact_create_route is None:
        raise AmbiguousRemotePostOutcome(
            "An X create route must use its exact literal method and path",
            service="x",
            request_method=method,
            request_path=path,
        )
    if method_upper not in {"GET", "HEAD", "OPTIONS"} and exact_create_route is None:
        raise AmbiguousRemotePostOutcome(
            "Generic or legacy X write routes have no durable transaction policy",
            service="x",
            request_method=method,
            request_path=path,
        )
    if ambiguous_write and exact_create_route is None:
        raise AmbiguousRemotePostOutcome(
            "Ambiguous-write handling is reserved for exact authorised create routes",
            service="x",
            request_method=method,
            request_path=path,
        )
    if isinstance(_remote_write_authorization, TransportAuthority) and not is_post_create:
        raise AmbiguousRemotePostOutcome(
            "A tweet-create transport authority cannot authorise another X endpoint",
            service="x",
            request_method=method,
            request_path=path,
        )
    if _remote_media_payload is not None and not is_media_upload:
        raise AmbiguousRemotePostOutcome(
            "A receipt-bound media body cannot authorise another X endpoint",
            service="x",
            request_method=method,
            request_path=path,
        )
    if _remote_media_payload_metadata is not None and not is_media_upload:
        raise AmbiguousRemotePostOutcome(
            "Media receipt metadata cannot authorise another X endpoint",
            service="x",
            request_method=method,
            request_path=path,
        )
    if isinstance(_remote_write_authorization, MediaUploadAuthority) and not is_media_upload:
        raise AmbiguousRemotePostOutcome(
            "A media-upload authority cannot authorise another X endpoint",
            service="x",
            request_method=method,
            request_path=path,
        )
    if _remote_write_authorization is not None and not isinstance(
        _remote_write_authorization,
        (TransportAuthority, MediaUploadAuthority),
    ):
        raise AmbiguousRemotePostOutcome(
            "X write authority has an unsupported type",
            service="x",
            request_method=method,
            request_path=path,
        )
    if is_post_create and (
        not isinstance(_remote_write_authorization, TransportAuthority)
        or not ambiguous_write
    ):
        raise AmbiguousRemotePostOutcome(
            "X post creation requires an exact durable transport-journal "
            "authorization and ambiguous-write handling",
            service="x",
            request_method=method,
            request_path=path,
        )

    expected_receipt_path: Path | None = None
    if is_post_create:
        if set(kwargs) != {"json"}:
            raise AmbiguousRemotePostOutcome(
                "X post creation accepts only one exact JSON body and no alternate "
                "body, query, header, file or redirect channel",
                service="x",
                request_method=method,
                request_path=path,
            )
        kwargs["json"] = frozen_strict_json_object(
            kwargs["json"],
            label="X post creation payload",
        )

    if is_media_upload and set(kwargs) != {"data", "files"}:
        raise AmbiguousRemotePostOutcome(
            "X media creation accepts only its exact form and media part",
            service="x",
            request_method=method,
            request_path=path,
        )

    log.debug("X request: %s %s", method, url)

    if "params" in kwargs:
        log_json_debug("X request params", kwargs["params"])

    if "json" in kwargs:
        log_json_debug("X request json", kwargs["json"])

    if "data" in kwargs:
        log_json_debug("X request form data", kwargs["data"])

    if "files" in kwargs:
        log.debug("X request includes files: %s", list(kwargs["files"].keys()))

    if method_upper not in {"GET", "HEAD", "OPTIONS"}:
        require_remote_operation_unpaused(
            f"X {method.upper()} {path}",
            transaction_authorization=_remote_write_authorization,
        )

    if ambiguous_write:
        # A redirect can turn one create into an untracked follow-up request.
        # Writes are therefore single-hop unless a future provider contract
        # explicitly proves a redirect safe.
        kwargs["allow_redirects"] = False

    if is_post_create:
        payload = kwargs.get("json")
        if not isinstance(payload, dict):
            raise AmbiguousRemotePostOutcome(
                "X post creation requires one exact JSON payload",
                service="x",
                request_method=method,
                request_path=path,
            )
        try:
            expected_receipt_path = canonical_transport_receipt_path_for_lane(
                _remote_write_authorization.lane
            )
            if expected_receipt_path is None:
                raise TransportJournalError(
                    "transport authority lane is not a canonical public-create lane"
                )
            block_if_unrelated_receipt_appeared_for_tweet_transport(
                expected_receipt_path
            )
        except TransportJournalError as exc:
            raise AmbiguousRemotePostOutcome(
                "X post creation lost its exact durable transport authority",
                service="x",
                request_method=method,
                request_path=path,
            ) from exc

    if is_media_upload and (
        not isinstance(_remote_write_authorization, MediaUploadAuthority)
        or not ambiguous_write
    ):
        raise AmbiguousRemotePostOutcome(
            "X media upload requires exact durable media authority and explicit "
            "ambiguous-write handling",
            service="x",
            request_method=method,
            request_path=path,
        )
    if is_media_upload:
        files = kwargs.get("files")
        form = kwargs.get("data")
        media_part = files.get("media") if isinstance(files, dict) else None
        if (
            not isinstance(media_part, tuple)
            or len(media_part) != 3
            or not isinstance(media_part[0], str)
            or type(media_part[1]) is not bytes
            or not isinstance(media_part[2], str)
            or not isinstance(form, dict)
            or not isinstance(_remote_media_payload, ReceiptBoundMediaPayload)
            or media_part
            != (
                _remote_media_payload.basename,
                _remote_media_payload.data,
                _remote_media_payload.mime_type,
            )
        ):
            raise AmbiguousRemotePostOutcome(
                "X media upload request is not an exact bound multipart payload",
                service="x",
                request_method=method,
                request_path=path,
            )
        try:
            receipt_payload_metadata = (
                media_upload_payload_metadata(form)
                if _remote_media_payload_metadata is None
                else validate_media_upload_payload_metadata(
                    _remote_media_payload_metadata,
                    form=form,
                )
            )
        except (TypeError, ValueError) as exc:
            raise AmbiguousRemotePostOutcome(
                "X media upload receipt metadata does not match its exact form",
                service="x",
                request_method=method,
                request_path=path,
            ) from exc
        try:
            block_if_unrelated_receipt_appeared_for_media_transport()
            consume_media_upload_authority(
                MEDIA_UPLOAD_RECEIPT_FILE,
                _remote_write_authorization,
                payload=_remote_media_payload,
                lane=_remote_write_authorization.lane,
                mime_type=media_part[2],
                payload_metadata=receipt_payload_metadata,
            )
        except MediaUploadReceiptError as exc:
            raise AmbiguousRemotePostOutcome(
                "X media upload lost its exact durable transport authority",
                service="x",
                request_method=method,
                request_path=path,
            ) from exc

    validated_error_response: ValidatedXErrorResponse | None = None
    rejection_proof: DeterministicReplyCreateRejectionProof | None = None
    read_only_request = method_upper in {"GET", "HEAD", "OPTIONS"}
    if read_only_request:
        report_bot_health_progress("x_read")
    try:
        if is_post_create:
            if expected_receipt_path is None:
                raise TransportJournalError(
                    "tweet transport has no canonical source receipt"
                )
            (
                response,
                coordinated_validated_error,
                coordinated_rejection_proof,
            ) = perform_consumed_x_request(
                Path(_remote_write_authorization.journal_path),
                _remote_write_authorization,
                request_authority=(
                    _bind_transport_authority_to_configured_x_request(
                        _remote_write_authorization,
                        payload=kwargs["json"],
                    )
                ),
                payload=kwargs["json"],
                expected_receipt_path=expected_receipt_path,
                request_kwargs=kwargs,
            )
            if isinstance(
                coordinated_validated_error,
                ValidatedXErrorResponse,
            ):
                validated_error_response = coordinated_validated_error
            if isinstance(
                coordinated_rejection_proof,
                DeterministicReplyCreateRejectionProof,
            ):
                rejection_proof = coordinated_rejection_proof
        else:
            response = requests.request(
                method,
                url,
                auth=AUTH,
                timeout=request_timeout(),
                **kwargs,
            )
    except TransportJournalError as e:
        log.exception("X request lost its exact transport authority")
        raise AmbiguousRemotePostOutcome(
            "X post creation lost its exact durable transport authority",
            service="x",
            status_code=getattr(e, "status_code", None),
            request_method=method,
            request_path=path,
        ) from e
    except requests.RequestException as e:
        log.exception("X request failed before receiving response")
        if ambiguous_write:
            raise AmbiguousRemotePostOutcome(
                str(e),
                service="x",
                request_method=method,
                request_path=path,
            ) from e
        raise ApiError(
            str(e),
            service="x",
            request_method=method,
            request_path=path,
        ) from e
    if read_only_request:
        report_bot_health_progress("x_read")

    def process_received_response() -> dict:
        nonlocal rejection_proof, validated_error_response

        log.debug("X response status: %s", response.status_code)
        log.debug(
            "X response headers: x-rate-limit-limit=%s remaining=%s reset=%s",
            response.headers.get("x-rate-limit-limit"),
            response.headers.get("x-rate-limit-remaining"),
            response.headers.get("x-rate-limit-reset"),
        )

        if not 200 <= response.status_code < 300:
            log.error("X API error %s: %s", response.status_code, response.text)
            reset_epoch = print_rate_limit_headers(response)
            if validated_error_response is None:
                try:
                    validated_error_response = parse_validated_x_error_response(
                        getattr(response, "content", None),
                        status_code=response.status_code,
                    )
                except XErrorResponseValidationError:
                    log.warning(
                        "X API non-success body was not one strict validated "
                        "error object; it cannot prove remote non-success",
                        exc_info=True,
                    )

            api_error = ApiError(
                f"X API error {response.status_code}: {response.text}",
                service="x",
                status_code=response.status_code,
                reset_epoch=reset_epoch,
                request_method=method,
                request_path=path,
                x_error_response=validated_error_response,
                x_error_message_fallback=not ambiguous_write,
            )

            if ambiguous_write:
                if rejection_proof is not None:
                    if not _activate_coordinator_reply_create_rejection_proof(
                        rejection_proof,
                        authority=_remote_write_authorization,
                    ):
                        invalidate_reply_create_rejection_proof(rejection_proof)
                        rejection_proof = None
                    else:
                        raise ProvedRemotePostNonSuccess(
                            str(api_error),
                            service="x",
                            status_code=response.status_code,
                            reset_epoch=reset_epoch,
                            request_method=method,
                            request_path=path,
                            x_error_response=validated_error_response,
                            x_error_message_fallback=False,
                            remote_non_success_proof=rejection_proof,
                        )
                raise AmbiguousRemotePostOutcome(
                    "X write outcome is not proved by HTTP status alone; "
                    f"received HTTP {response.status_code}: {response.text}",
                    service="x",
                    status_code=response.status_code,
                    reset_epoch=reset_epoch,
                    request_method=method,
                    request_path=path,
                    x_error_message_fallback=False,
                )

            raise api_error

        raw_create_body: bytes | None = None
        if is_post_create:
            # Requests has already populated its cached, decompressed content
            # for the non-streaming request.  This bytes() call cannot trigger
            # another network read.
            raw_create_body = bytes(response.content)

        def raise_create_response_anomaly(
            reason: str,
            *,
            json_decode_succeeded: bool,
            decoded: object,
            json_error: json.JSONDecodeError | None = None,
        ) -> None:
            if (
                raw_create_body is None
                or not isinstance(_remote_write_authorization, TransportAuthority)
            ):
                raise RuntimeError(
                    "tweet-create anomaly evidence requires its exact response "
                    "and transport authority"
                )
            diagnostic = emit_x_create_response_anomaly(
                response=response,
                transport_authority=_remote_write_authorization,
                request_payload=kwargs["json"],
                reason=reason,
                raw_body=raw_create_body,
                json_decode_succeeded=json_decode_succeeded,
                decoded=decoded,
                json_error=json_error,
            )
            if reason == "json_decode_error":
                outcome_summary = (
                    "X may have accepted the post but returned a non-JSON "
                    "successful response"
                )
            elif reason == "decoded_top_level_not_object":
                outcome_summary = (
                    "X may have accepted the post but its successful response "
                    f"was not a JSON object: {type(decoded).__name__}"
                )
            else:
                outcome_summary = (
                    "X may have accepted the post but its successful response "
                    "could not confirm a valid numeric data.id"
                )
            error = AmbiguousRemotePostOutcome(
                f"{outcome_summary}; "
                f"reason={reason} "
                f"diagnostic_event={X_CREATE_RESPONSE_ANOMALY_EVENT} "
                f"diagnostic_sha256={diagnostic['diagnostic_sha256']}",
                service="x",
                status_code=response.status_code,
                request_method=method,
                request_path=path,
                response_body_length=diagnostic["raw_body_length"],
                response_body_sha256=diagnostic["raw_body_sha256"],
                diagnostic_sha256=diagnostic["diagnostic_sha256"],
                diagnostic_event=X_CREATE_RESPONSE_ANOMALY_EVENT,
            )
            if json_error is not None:
                raise error from json_error
            raise error

        if is_post_create and not raw_create_body:
            log.debug("X tweet-create response has empty body")
            raise_create_response_anomaly(
                "empty_response_body",
                json_decode_succeeded=False,
                decoded=None,
            )
        if not is_post_create and not response.text:
            log.debug("X response has empty body")
            return {}

        try:
            data = response.json()
        except json.JSONDecodeError as e:
            if is_post_create:
                raise_create_response_anomaly(
                    "json_decode_error",
                    json_decode_succeeded=False,
                    decoded=None,
                    json_error=e,
                )
            log.error(
                "X API returned non-JSON response: %s",
                response.text[:1000],
            )
            if ambiguous_write:
                raise AmbiguousRemotePostOutcome(
                    "X may have accepted the write but returned a non-JSON "
                    f"response: {response.text[:500]}",
                    service="x",
                    request_method=method,
                    request_path=path,
                ) from e
            raise ApiError(
                f"X API returned non-JSON response: {response.text[:500]}",
                service="x",
                request_method=method,
                request_path=path,
            ) from e
        if not isinstance(data, dict):
            if is_post_create:
                raise_create_response_anomaly(
                    "decoded_top_level_not_object",
                    json_decode_succeeded=True,
                    decoded=data,
                )
            message = (
                "X API response must be a JSON object, got "
                f"{type(data).__name__}"
            )
            if ambiguous_write:
                raise AmbiguousRemotePostOutcome(
                    "X may have accepted the write but its response was not "
                    f"a JSON object: {type(data).__name__}",
                    service="x",
                    request_method=method,
                    request_path=path,
                )
            raise ApiError(
                message,
                service="x",
                request_method=method,
                request_path=path,
            )

        if is_post_create:
            anomaly_reason = x_create_response_anomaly_reason(data)
            if anomaly_reason is not None:
                raise_create_response_anomaly(
                    anomaly_reason,
                    json_decode_succeeded=True,
                    decoded=data,
                )

        log_json_debug("X response json", data)
        return data

    try:
        return process_received_response()
    finally:
        escaping_error = sys.exc_info()[1]
        if rejection_proof is not None:
            preserve_proof = False
            try:
                preserve_proof = bool(
                    isinstance(
                        escaping_error,
                        ProvedRemotePostNonSuccess,
                    )
                    and getattr(
                        escaping_error,
                        "remote_non_success_proof",
                        None,
                    )
                    is rejection_proof
                )
            except BaseException:
                preserve_proof = False
            if not preserve_proof:
                invalidate_reply_create_rejection_proof(rejection_proof)


def x_bearer_request(method: str, path: str, **kwargs) -> dict:
    """Send a bearer-authenticated X API request with bounded retries."""
    if str(method).upper() not in {"GET", "HEAD", "OPTIONS"}:
        raise AmbiguousRemotePostOutcome(
            "Bearer-authenticated X writes have no durable transaction authority",
            service="x",
            request_method=method,
            request_path=path,
        )
    if not X_BEARER_TOKEN:
        raise ApiError("X_BEARER_TOKEN is not set", service="x")

    url = f"{X_BASE}{path}"

    log.debug("X bearer request: %s %s", method, url)

    if "params" in kwargs:
        log_json_debug("X bearer request params", kwargs["params"])

    report_bot_health_progress("x_read")
    try:
        response = requests.request(
            method,
            url,
            headers={
                "Authorization": f"Bearer {X_BEARER_TOKEN}",
            },
            timeout=request_timeout(),
            **kwargs,
        )
    except requests.RequestException as e:
        log.exception("X bearer request failed before receiving response")
        raise ApiError(
            str(e),
            service="x",
            request_method=method,
            request_path=path,
        ) from e
    finally:
        report_bot_health_progress("x_read")

    log.debug("X bearer response status: %s", response.status_code)
    log.debug(
        "X bearer response headers: x-rate-limit-limit=%s remaining=%s reset=%s",
        response.headers.get("x-rate-limit-limit"),
        response.headers.get("x-rate-limit-remaining"),
        response.headers.get("x-rate-limit-reset"),
    )

    if response.status_code >= 400:
        log.error("X bearer API error %s: %s", response.status_code, response.text)
        reset_epoch = print_rate_limit_headers(response)

        raise ApiError(
            f"X bearer API error {response.status_code}: {response.text}",
            service="x",
            status_code=response.status_code,
            reset_epoch=reset_epoch,
            request_method=method,
            request_path=path,
        )

    if not response.text:
        return {}

    try:
        data = response.json()
    except json.JSONDecodeError as e:
        log.error("X bearer API returned non-JSON response: %s", response.text[:1000])
        raise ApiError(
            f"X bearer API returned non-JSON response: {response.text[:500]}",
            service="x",
            request_method=method,
            request_path=path,
        ) from e
    if not isinstance(data, dict):
        raise ApiError(
            f"X bearer API response must be a JSON object, got {type(data).__name__}",
            service="x",
            request_method=method,
            request_path=path,
        )

    log_json_debug("X bearer response json", data)
    return data


def x_quote_lookup_request(path: str, params: dict) -> dict:
    """
    The quote_tweets lookup is documented using Bearer auth. If X_BEARER_TOKEN
    is set, use it. Otherwise try the same OAuth1 path used by the rest of
    this bot.
    """
    if X_BEARER_TOKEN:
        return x_bearer_request("GET", path, params=params)

    log.warning("X_BEARER_TOKEN not set; trying quote lookup with OAuth1")
    return x_request("GET", path, params=params)


def api_error_is_invalid_pagination_cursor(error: BaseException) -> bool:
    """Recognise only X 400 responses which specifically reject a cursor."""
    return _x_pagination.api_error_is_invalid_pagination_cursor(
        error,
        ApiError=ApiError,
        json=json,
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

def prune_tweet_cache(state: dict) -> None:
    """Delegate tweet lookup/cache work with current root dependencies."""
    return _tweet_lookup_cache.prune_tweet_cache(
        state,
        TWEET_CACHE_MAX_AGE_SECONDS=TWEET_CACHE_MAX_AGE_SECONDS,
        TWEET_CACHE_MAX_ITEMS=TWEET_CACHE_MAX_ITEMS,
        log=log,
        now_epoch=now_epoch,
    )


def record_recent_own_post(state: dict, tweet_id: str) -> None:
    """Delegate tweet lookup/cache work with current root dependencies."""
    return _tweet_lookup_cache.record_recent_own_post(
        state,
        tweet_id,
        RECENT_OWN_POST_IDS_MAX=RECENT_OWN_POST_IDS_MAX,
        log=log,
    )


def seed_recent_own_post_ids_from_cache(state: dict) -> None:
    """Delegate tweet lookup/cache work with current root dependencies."""
    return _tweet_lookup_cache.seed_recent_own_post_ids_from_cache(
        state,
        MY_USER_ID=MY_USER_ID,
        RECENT_OWN_POST_IDS_MAX=RECENT_OWN_POST_IDS_MAX,
        log=log,
    )


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
    return _tweet_lookup_cache.cache_tweet(
        state,
        tweet_id=tweet_id,
        text=text,
        author_id=author_id,
        conversation_id=conversation_id,
        referenced_tweets=referenced_tweets,
        created_at=created_at,
        image_summary=image_summary,
        post_type=post_type,
        STATE_FILE=STATE_FILE,
        current_datetime=current_datetime,
        log=log,
        normalise_tweet_cache_entry=normalise_tweet_cache_entry,
        now_epoch=now_epoch,
        prune_tweet_cache=prune_tweet_cache,
    )


def get_immediate_parent_id(tweet: dict) -> str | None:
    """Delegate to the context owner with current root dependencies."""
    return _reply_context.get_immediate_parent_id(
        tweet,
        ApiError=ApiError,
        parse_tweet_id=parse_tweet_id,
    )


def _verified_tweet_lookup_row(
    tweet: object,
    *,
    requested_tweet_id: str,
) -> dict | None:
    """Delegate tweet lookup/cache work with current root dependencies."""
    return _tweet_lookup_cache._verified_tweet_lookup_row(
        tweet,
        requested_tweet_id=requested_tweet_id,
        ApiError=ApiError,
    )


def get_tweet_by_id(
    tweet_id: str,
    *,
    include_media: bool = False,
) -> dict | None:
    """Delegate tweet lookup/cache work with current root dependencies."""
    return _tweet_lookup_cache.get_tweet_by_id(
        tweet_id,
        include_media=include_media,
        _verified_tweet_lookup_row=_verified_tweet_lookup_row,
        attach_media_to_tweets=attach_media_to_tweets,
        log=log,
        log_json_debug=log_json_debug,
        x_request=x_request,
    )


def reply_target_is_available_immediately_before_send(target_id: str) -> bool:
    """Delegate tweet lookup/cache work with current root dependencies."""
    return _tweet_lookup_cache.reply_target_is_available_immediately_before_send(
        target_id,
        ApiError=ApiError,
        api_error_is_permanent_target_failure=api_error_is_permanent_target_failure,
        get_tweet_by_id=get_tweet_by_id,
        log=log,
    )


def get_tweet_by_id_cached(
    tweet_id: str,
    state: dict,
    *,
    include_media: bool = False,
) -> dict | None:
    """Delegate tweet lookup/cache work with current root dependencies."""
    return _tweet_lookup_cache.get_tweet_by_id_cached(
        tweet_id,
        state,
        include_media=include_media,
        _verified_tweet_lookup_row=_verified_tweet_lookup_row,
        cache_tweet=cache_tweet,
        copy=copy,
        get_tweet_by_id=get_tweet_by_id,
        log=log,
        prune_tweet_cache=prune_tweet_cache,
        save_state=save_state,
    )


def clean_text_for_reply_context(text: str) -> str:
    """Delegate to the context owner with current root dependencies."""
    return _reply_context.clean_text_for_reply_context(
        text,
        html=html,
        re=re,
    )


attach_media_to_tweets = _reply_native_media.attach_media_to_tweets


def candidate_native_photo_media(candidate: dict) -> tuple[list[dict], int]:
    """Delegate photo selection with the current root cap."""
    return _reply_native_media.candidate_native_photo_media(
        candidate,
        MAX_REPLY_CONTEXT_PHOTOS=MAX_REPLY_CONTEXT_PHOTOS,
    )


def reply_media_context_for_candidate(
    candidate: dict,
    *,
    lane: str,
    target_id: str,
    quoted_candidate: dict | None = None,
) -> dict:
    """Delegate media context with the current root cap, callback and logger."""
    return _reply_native_media.reply_media_context_for_candidate(
        candidate,
        lane=lane,
        target_id=target_id,
        quoted_candidate=quoted_candidate,
        MAX_REPLY_CONTEXT_PHOTOS=MAX_REPLY_CONTEXT_PHOTOS,
        candidate_native_photo_media=candidate_native_photo_media,
        log=log,
    )


def tweet_context_text(tweet: dict) -> str:
    """Delegate to the context owner with current root dependencies."""
    return _reply_context.tweet_context_text(
        tweet,
        clean_text_for_reply_context=clean_text_for_reply_context,
    )


def trim_context_text(text: str, max_chars: int) -> str:
    """Delegate to the context owner with current root dependencies."""
    return _reply_context.trim_context_text(
        text,
        max_chars,
        clean_text_for_reply_context=clean_text_for_reply_context,
    )


def build_parent_chain(mention: dict, state: dict) -> list[dict]:
    """Delegate to the context owner with current root dependencies."""
    return _reply_context.build_parent_chain(
        mention,
        state,
        ApiError=ApiError,
        THREAD_CONTEXT_MAX_DEPTH=THREAD_CONTEXT_MAX_DEPTH,
        THREAD_CONTEXT_MAX_NETWORK_FETCHES=THREAD_CONTEXT_MAX_NETWORK_FETCHES,
        api_error_is_permanent_target_failure=api_error_is_permanent_target_failure,
        get_immediate_parent_id=get_immediate_parent_id,
        get_tweet_by_id_cached=get_tweet_by_id_cached,
        log=log,
        log_json_debug=log_json_debug,
        prune_tweet_cache=prune_tweet_cache,
    )


def is_our_auto_reply(tweet: dict | None, state: dict) -> bool:
    """Delegate to the context owner with current root dependencies."""
    return _reply_context.is_our_auto_reply(
        tweet,
        state,
        MY_USER_ID=MY_USER_ID,
    )


def _reply_context_post(
    tweet: dict,
    *,
    principal_author_id: str,
    maximum_chars: int = MAX_VISIBLE_TEXT_CHARACTERS,
) -> dict[str, str]:
    """Delegate to the context owner with current root dependencies."""
    return _reply_context._reply_context_post(
        tweet,
        principal_author_id=principal_author_id,
        maximum_chars=maximum_chars,
        MY_USER_ID=MY_USER_ID,
        trim_context_text=trim_context_text,
        tweet_context_text=tweet_context_text,
    )


def _log_single_call_context_summary(label: str, context: dict[str, object]) -> None:
    """Delegate to the context owner with current root dependencies."""
    return _reply_context._log_single_call_context_summary(
        label,
        context,
        hashlib=hashlib,
        json=json,
        log=log,
    )


def _log_validated_single_call_reply(
    *,
    target_description: str,
    target_id: str,
    reply: object,
) -> None:
    """Log validated output metadata without retaining exact public prose."""

    text = str(reply)
    encoded = text.encode("utf-8", errors="strict")
    log.info(
        "Generated validated reply to %s %s character_count=%d "
        "utf8_byte_count=%d sha256=%s",
        target_description,
        str(target_id),
        len(text),
        len(encoded),
        hashlib.sha256(encoded).hexdigest(),
    )


def _directly_quoted_tweet_for_reply_context(
    candidate: dict,
    state: dict,
    *,
    include_media: bool = True,
) -> dict | None:
    """Delegate to the context owner with current root dependencies."""
    return _reply_context._directly_quoted_tweet_for_reply_context(
        candidate,
        state,
        include_media=include_media,
        ApiError=ApiError,
        api_error_is_permanent_target_failure=api_error_is_permanent_target_failure,
        get_tweet_by_id_cached=get_tweet_by_id_cached,
    )


_direct_quote_id = _reply_context._direct_quote_id


def _quoted_post_for_reply_context(
    candidate: dict,
    state: dict,
    *,
    principal_author_id: str,
) -> dict[str, str] | None:
    """Delegate to the context owner with current root dependencies."""
    return _reply_context._quoted_post_for_reply_context(
        candidate,
        state,
        principal_author_id=principal_author_id,
        _directly_quoted_tweet_for_reply_context=_directly_quoted_tweet_for_reply_context,
        _reply_context_post=_reply_context_post,
    )


def _parent_path_is_contiguous(path: list[dict], target: dict) -> bool:
    """Delegate to the context owner with current root dependencies."""
    return _reply_context._parent_path_is_contiguous(
        path,
        target,
        get_immediate_parent_id=get_immediate_parent_id,
    )


def _parent_path_is_chronological(path: list[dict], target: dict) -> bool:
    """Delegate to the context owner with current root dependencies."""
    return _reply_context._parent_path_is_chronological(
        path,
        target,
        parse_x_datetime_to_epoch=parse_x_datetime_to_epoch,
    )


def build_context_for_reply_ai(
    mention: dict,
    state: dict,
) -> tuple[dict[str, object], bool]:
    """Delegate to the context owner with current root dependencies."""
    return _reply_context.build_context_for_reply_ai(
        mention,
        state,
        ALWAYS_FETCH_PARENT_FOR_CONTEXT=ALWAYS_FETCH_PARENT_FOR_CONTEXT,
        ContextValidationError=ContextValidationError,
        REPLY_INCOMING_MAX_CHARS=REPLY_INCOMING_MAX_CHARS,
        SKIP_REPLIES_TO_OWN_AUTO_REPLIES=SKIP_REPLIES_TO_OWN_AUTO_REPLIES,
        THREAD_CONTEXT_MAX_DEPTH=THREAD_CONTEXT_MAX_DEPTH,
        _direct_quote_id=_direct_quote_id,
        _directly_quoted_tweet_for_reply_context=_directly_quoted_tweet_for_reply_context,
        _log_single_call_context_summary=_log_single_call_context_summary,
        _parent_path_is_chronological=_parent_path_is_chronological,
        _parent_path_is_contiguous=_parent_path_is_contiguous,
        _reply_context_post=_reply_context_post,
        bound_visible_conversation=bound_visible_conversation,
        build_parent_chain=build_parent_chain,
        copy=copy,
        current_datetime=current_datetime,
        get_immediate_parent_id=get_immediate_parent_id,
        is_our_auto_reply=is_our_auto_reply,
        log=log,
        reply_media_context_for_candidate=reply_media_context_for_candidate,
        trim_context_text=trim_context_text,
    )
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


def pending_mention_candidates(state: dict) -> list[dict]:
    """Delegate to the mention owner with current root dependencies."""
    return _mention_discovery.pending_mention_candidates(
        state,
        STATE_FILE=STATE_FILE,
        save_state=save_state,
        valid_tweets_sorted_by_id=valid_tweets_sorted_by_id,
        validate_pending_mention_candidate_authority=validate_pending_mention_candidate_authority,
    )


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
        active_mention_backlog_reset_guard=active_mention_backlog_reset_guard,
        api_error_is_invalid_pagination_cursor=api_error_is_invalid_pagination_cursor,
        attach_media_to_tweets=attach_media_to_tweets,
        cache_tweet=cache_tweet,
        copy=copy,
        hashlib=hashlib,
        log=log,
        log_event=log_event,
        log_json_debug=log_json_debug,
        now_epoch=now_epoch,
        pending_mention_candidates=pending_mention_candidates,
        prune_completed_mention_quarantine_evaluations=prune_completed_mention_quarantine_evaluations,
        save_state=save_state,
        terminal_reply_evaluation=terminal_reply_evaluation,
        update_last_seen_mention_id=update_last_seen_mention_id,
        valid_tweets_sorted_by_id=valid_tweets_sorted_by_id,
        x_paginated_get=x_paginated_get,
        x_request=x_request,
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
        attach_media_to_tweets=attach_media_to_tweets,
        cache_tweet=cache_tweet,
        clear_pending_ai_reply=clear_pending_ai_reply,
        in_api_cooldown=in_api_cooldown,
        lane_paused=lane_paused,
        load_extra_quote_watch_post_ids=load_extra_quote_watch_post_ids,
        log=log,
        log_event=log_event,
        log_json_debug=log_json_debug,
        mark_hot_post_reply_skipped=mark_hot_post_reply_skipped,
        pending_ai_reply_draft_key=pending_ai_reply_draft_key,
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
        append_unique_capped=append_unique_capped,
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
        copy=copy,
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

    base: dict[str, object] = {
        "request_method": "POST",
        "request_path": "/2/media/upload",
        "form": dict(form),
    }
    if not isinstance(value, dict):
        raise TypeError("media receipt payload metadata is not an object")
    allowed_fields = {*base, "engagement_question_experiment"}
    observed_fields = set(value)
    if observed_fields != set(base) and observed_fields != allowed_fields:
        raise ValueError("media receipt payload metadata fields are invalid")
    if any(value.get(field) != expected for field, expected in base.items()):
        raise ValueError("media receipt payload metadata changed its remote form")
    envelope = value.get("engagement_question_experiment")
    if envelope is not None:
        if not isinstance(envelope, dict):
            raise ValueError("media receipt experiment authority is invalid")
        binding = envelope.get("binding")
        canonical_quote_text = envelope.get("canonical_quote_text")
        question_body = envelope.get("approved_question_body")
        if (
            not isinstance(binding, dict)
            or type(canonical_quote_text) is not str
            or type(question_body) is not str
        ):
            raise ValueError("media receipt experiment authority is incomplete")
        public_text = (
            engagement_question_trial.complete_treatment_text(
                canonical_quote_text,
                question_body,
            )
            if binding.get("arm") == "treatment"
            else canonical_quote_text
        )
        if not engagement_experiment_attempt_envelope_is_valid(
            envelope,
            public_text=public_text,
            quote_hash=binding.get("canonical_quote_sha256"),
        ):
            raise ValueError("media receipt experiment authority is inconsistent")
    return copy.deepcopy(value)


def media_upload_payload_metadata(
    form: dict[str, object],
    *,
    engagement_experiment: dict | None = None,
) -> dict[str, object]:
    """Bind the durable media receipt to its remote form and optional trial."""

    metadata: dict[str, object] = {
        "request_method": "POST",
        "request_path": "/2/media/upload",
        "form": dict(form),
    }
    if engagement_experiment is not None:
        metadata["engagement_question_experiment"] = copy.deepcopy(
            engagement_experiment
        )
    return validate_media_upload_payload_metadata(metadata, form=form)


def upload_media_v2(
    *,
    authority: MediaUploadAuthority,
    payload: ReceiptBoundMediaPayload,
    payload_metadata: dict[str, object] | None = None,
) -> str:
    """Upload once through v2 and return its confirmed media identity."""
    log.info("Uploading receipt-bound media via X API v2: %s", payload.basename)
    files = {
        "media": (payload.basename, payload.data, payload.mime_type),
    }
    data = {
        "media_category": "tweet_image",
        "media_type": payload.mime_type,
    }
    if payload_metadata is None:
        result = x_request(
            "POST",
            "/2/media/upload",
            files=files,
            data=data,
            ambiguous_write=True,
            _remote_write_authorization=authority,
            _remote_media_payload=payload,
        )
    else:
        result = x_request(
            "POST",
            "/2/media/upload",
            files=files,
            data=data,
            ambiguous_write=True,
            _remote_write_authorization=authority,
            _remote_media_payload=payload,
            _remote_media_payload_metadata=validate_media_upload_payload_metadata(
                payload_metadata,
                form=data,
            ),
        )

    response_data = result.get("data") if isinstance(result, dict) else None
    raw_media_id = (
        response_data.get("id") if isinstance(response_data, dict) else None
    )
    if (
        isinstance(raw_media_id, bool)
        or not isinstance(raw_media_id, (str, int))
        or not str(raw_media_id).strip()
    ):
        raise AmbiguousRemotePostOutcome(
            "X may have accepted the v2 media upload but its response did not "
            "include a valid data.id",
            service="x",
            request_method="POST",
            request_path="/2/media/upload",
        )
    media_id = str(raw_media_id).strip()
    log.info("Uploaded media via v2. media_id=%s", media_id)
    return media_id


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
    engagement_experiment: dict | None = None,
    pre_transport_validation: Callable[[], None] | None = None,
) -> str:
    """Upload once under a restart-visible, image-bound sending receipt."""
    if (engagement_experiment is None) != (pre_transport_validation is None):
        raise ValueError(
            "experimental media authority and pre-transport validation must "
            "be supplied together"
        )
    require_remote_operation_unpaused("X media upload")
    block_if_ambiguous_remote_post()
    mime_type, _ = mimetypes.guess_type(image_path)
    if not mime_type:
        mime_type = "image/jpeg"
    form: dict[str, object] = {
        "media_category": "tweet_image",
        "media_type": mime_type,
    }
    payload_metadata = media_upload_payload_metadata(
        form,
        engagement_experiment=engagement_experiment,
    )
    try:
        authority = begin_media_upload(
            receipt_path=MEDIA_UPLOAD_RECEIPT_FILE,
            image_path=Path(image_path),
            lane=lane,
            mime_type=mime_type,
            payload_metadata=payload_metadata,
        )
        bound_payload = bind_media_upload_payload(
            MEDIA_UPLOAD_RECEIPT_FILE,
            authority,
            image_path=Path(image_path),
            lane=lane,
            mime_type=mime_type,
            payload_metadata=payload_metadata,
        )
    except MediaUploadReceiptError as exc:
        raise AmbiguousRemotePostOutcome(
            "Could not establish the restart-persistent media-upload receipt",
            service="x",
            request_method="POST",
            request_path="/2/media/upload",
        ) from exc
    if pre_transport_validation is not None:
        try:
            pre_transport_validation()
        except BaseException:
            try:
                abort_untransmitted_media_upload(
                    MEDIA_UPLOAD_RECEIPT_FILE,
                    authority,
                    mutation_authority=transaction_mutation_authority(
                        "invalid untransmitted experimental media abort"
                    ),
                )
            except Exception as abort_exc:
                record_ambiguous_remote_post(
                    {"text": "", "media": {"media_ids": []}}
                )
                raise AmbiguousRemotePostOutcome(
                    "Experimental pre-transport validation failed and left an "
                    "unresolved media-upload barrier",
                    service="x",
                    request_method="POST",
                    request_path="/2/media/upload",
                ) from abort_exc
            raise
    media_sigint_guard = begin_confirmed_post_sigint_deferral()
    try:
        try:
            if engagement_experiment is None:
                media_id = upload_media_v2(
                    authority=authority,
                    payload=bound_payload,
                )
            else:
                media_id = upload_media_v2(
                    authority=authority,
                    payload=bound_payload,
                    payload_metadata=payload_metadata,
                )
            confirm_media_upload(
                MEDIA_UPLOAD_RECEIPT_FILE,
                authority,
                mutation_authority=transaction_mutation_authority(
                    "media upload confirmation"
                ),
                media_id=media_id,
            )
            return media_id
        except RemoteOperationsPaused:
            # The final transport preflight runs before media authority is
            # consumed.  Only that exact local non-transmission proof may
            # retire this process-issued sending pair.
            try:
                abort_untransmitted_media_upload(
                    MEDIA_UPLOAD_RECEIPT_FILE,
                    authority,
                    mutation_authority=transaction_mutation_authority(
                        "untransmitted media-upload abort"
                    ),
                )
            except Exception as abort_exc:
                record_ambiguous_remote_post(
                    {"text": "", "media": {"media_ids": []}}
                )
                raise AmbiguousRemotePostOutcome(
                    "A locally paused media upload left an unresolved "
                    "media-upload barrier",
                    service="x",
                    request_method="POST",
                    request_path="/2/media/upload",
                ) from abort_exc
            raise
        except AmbiguousRemotePostOutcome:
            # The upload precedes the public-post sending receipt.  If its
            # response is lost, its media receipt and the ordinary incident
            # marker both suppress every later upload or public post.
            log.critical(
                "X media upload outcome is ambiguous; blocking every subsequent "
                "remote write pending manual reconciliation. image=%s",
                Path(image_path).name,
            )
            record_ambiguous_remote_post({"text": "", "media": {"media_ids": []}})
            raise
        except MediaUploadReceiptError as exc:
            record_ambiguous_remote_post({"text": "", "media": {"media_ids": []}})
            raise AmbiguousRemotePostOutcome(
                "X confirmed a media upload but its durable receipt could not be confirmed",
                service="x",
                request_method="POST",
                request_path="/2/media/upload",
            ) from exc
    finally:
        # The receipt is durable before this guard begins and remains the
        # restart barrier until confirmed media is handed to a tweet journal.
        end_confirmed_post_sigint_deferral(media_sigint_guard)


def unresolved_conversational_reply_receipt_is_blocking() -> bool:
    """Return whether a reply receipt forbids another remote write."""
    status, _receipt = load_confirmed_reply_receipt()
    return status != "absent"


def unresolved_main_post_attempt_is_blocking() -> bool:
    """Return whether a main-post attempt forbids another remote write."""
    regular_status, _regular = load_regular_post_receipt()
    meme_status, _meme = load_meme_post_receipt()
    return regular_status != "absent" or meme_status != "absent"


def remote_write_safety_incident_is_latched() -> bool:
    """Return whether process memory requires every remote operation to stop."""
    return (
        _AMBIGUOUS_REMOTE_POST_SEEN
        or _AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN
    )


def remote_write_safety_protocol_is_active() -> bool:
    """Return whether the exact restart-persistent protocol is activated.

    Absence, malformed bytes, unsafe metadata and inspection failure all mean
    that no remote-write lane may open.  This deliberately durable negative
    condition survives arbitrary process loss without relying on Python
    globals or an ambiguity marker which another process could remove.
    """
    try:
        inspect_protocol_activation(
            REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE
        )
    except (ProtocolActivationError, OSError):
        return False
    return not remote_receipt_retirement_is_blocking()


def historical_context_receipt_path_present_or_unsafe() -> bool:
    """Treat any historical-context receipt namespace entry as blocking."""

    try:
        os.lstat(HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE)
    except FileNotFoundError:
        return False
    except Exception:
        log.critical(
            "The historical-context receipt namespace cannot be inspected; "
            "treating every remote write as blocked",
            exc_info=True,
        )
        return True
    return True


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

    try:
        snapshot = historical_context_outbox_store().snapshot()
        obligations = snapshot.get("obligations")
        if not isinstance(obligations, dict):
            raise RuntimeError("historical-context outbox has no obligations map")
        exact_prepared_identity: dict[str, object] | None = None
        exact_prepared_attempt_required = bool(
            prepared_transport_authority is not None
            and prepared_transport_authority.lane == "historical_context_reply"
            and prepared_transport_authority.lifecycle_state == "attempting"
        )
        exact_prepared_attempt_observed = False
        if (
            prepared_transport_authority is not None
            and prepared_transport_authority.lane == "historical_context_reply"
            and prepared_transport_authority.lifecycle_state == "attempting"
            and Path(prepared_transport_authority.journal_path).absolute()
            == journal_path_for_receipt(
                HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
            ).absolute()
        ):
            from historical_context_formatter import HistoricalContextReplyStore

            state = inspect_transport_state(
                Path(prepared_transport_authority.journal_path)
            )
            loaded = historical_context_reply_store()._load_receipt_safely()
            if (
                not state.errors
                and state.classification == "attempting_pair"
                and state.journal is not None
                and state.fence is not None
                and state.journal.sha256
                == prepared_transport_authority.journal_sha256
                and state.fence.sha256
                == prepared_transport_authority.fence_sha256
                and state.journal.document.get("transaction_id")
                == prepared_transport_authority.transaction_id
                and state.fence.document.get("transaction_id")
                == prepared_transport_authority.transaction_id
                and state.journal.document.get("source_receipt")
                == state.fence.document.get("source_receipt")
                and isinstance(loaded, tuple)
                and len(loaded) == 2
            ):
                receipt, receipt_bytes = loaded
                source = state.journal.document.get("source_receipt")
                receipt_sha256 = hashlib.sha256(receipt_bytes).hexdigest()
                if (
                    isinstance(receipt, dict)
                    and type(receipt_bytes) is bytes
                    and isinstance(source, dict)
                    and HistoricalContextReplyStore._valid_sending_receipt(
                        receipt
                    )
                    and (
                        prepared_receipt is None
                        or receipt == prepared_receipt
                    )
                    and source.get("basename")
                    == HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.name
                    == prepared_transport_authority.source_receipt_basename
                    and source.get("sha256") == receipt_sha256
                ):
                    exact_prepared_identity = {
                        "parent_post_id": receipt.get("parent_post_id"),
                        "quote_id": receipt.get("quote_id"),
                        "source_receipt_sha256": receipt_sha256,
                        "source_receipt_attempt_number": receipt.get(
                            "attempt_number"
                        ),
                    }
        for obligation in obligations.values():
            context_reply = (
                obligation.get("context_reply")
                if isinstance(obligation, dict)
                else None
            )
            if (
                isinstance(context_reply, dict)
                and context_reply.get("state") == "context_reply_attempting"
                and context_reply.get("remote_transaction_started") is not False
            ):
                if (
                    context_reply.get("remote_transaction_started") is True
                    and exact_prepared_identity is not None
                    and obligation.get("parent_post_id")
                    == exact_prepared_identity["parent_post_id"]
                    and context_reply.get("quote_id")
                    == exact_prepared_identity["quote_id"]
                    and context_reply.get("source_receipt_sha256")
                    == exact_prepared_identity["source_receipt_sha256"]
                    and context_reply.get("source_receipt_attempt_number")
                    == exact_prepared_identity[
                        "source_receipt_attempt_number"
                    ]
                ):
                    # The ordinary checks below still prove the exact source
                    # receipt and armed journal pair. This exception is only
                    # for that one currently executing context attempt.
                    exact_prepared_attempt_observed = True
                    continue
                if (
                    allow_local_reconciliation_parent_id is not None
                    and obligation.get("parent_post_id")
                    == str(allow_local_reconciliation_parent_id)
                ):
                    # The outbox worker may inspect and reconcile this one
                    # already-attempting parent while every remote boundary
                    # remains barred. Any second risky row still blocks entry.
                    continue
                return True
        return bool(
            exact_prepared_attempt_required
            and not exact_prepared_attempt_observed
        )
    except Exception:
        log.critical(
            "The historical-context outbox cannot prove that no remote-started "
            "attempt remains; treating every remote write as blocked",
            exc_info=True,
        )
        return True


def historical_context_outbox_remote_attempt_parent_for_local_reconciliation(
) -> str | None:
    """Return the sole risky outbox parent eligible for local-only recovery."""

    try:
        snapshot = historical_context_outbox_store().snapshot()
        obligations = snapshot.get("obligations")
        if not isinstance(obligations, dict):
            return None
        parents = [
            str(obligation.get("parent_post_id"))
            for obligation in obligations.values()
            if isinstance(obligation, dict)
            and isinstance(obligation.get("context_reply"), dict)
            and obligation["context_reply"].get("state")
            == "context_reply_attempting"
            and obligation["context_reply"].get("remote_transaction_started")
            is not False
            and str(obligation.get("parent_post_id") or "")
        ]
    except Exception:
        return None
    return parents[0] if len(parents) == 1 else None


def historical_context_receipt_parent_for_local_reconciliation() -> str | None:
    """Return the parent bound by a stable no-follow transaction receipt."""

    from historical_context_formatter import HistoricalContextReplyStore

    return HistoricalContextReplyStore.receipt_parent_for_safe_local_reconciliation(
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
    )


def exact_historical_context_sending_receipt_matches(
    receipt: dict | None,
) -> bool:
    """Match the owning sending receipt by schema, identity and exact bytes."""

    if receipt is None:
        return False
    from historical_context_formatter import HistoricalContextReplyStore

    if not HistoricalContextReplyStore._valid_sending_receipt(receipt):
        return False
    expected = (
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    path = HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
    try:
        before = os.lstat(path)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_nlink != 1
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size != len(expected)
        ):
            return False
        nofollow = getattr(os, "O_NOFOLLOW", 0)
        if not nofollow:
            return False
        descriptor = os.open(
            path,
            os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            opened = os.fstat(descriptor)
            chunks: list[bytes] = []
            total = 0
            while total <= len(expected):
                chunk = os.read(descriptor, len(expected) + 1 - total)
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
            after_read = os.fstat(descriptor)
            after_path = os.lstat(path)
        finally:
            os.close(descriptor)
    except Exception:
        return False
    return bool(
        stat.S_ISREG(opened.st_mode)
        and opened.st_nlink == 1
        and opened.st_uid == os.geteuid()
        and stat.S_IMODE(opened.st_mode) == 0o600
        and opened.st_dev == before.st_dev == after_read.st_dev == after_path.st_dev
        and opened.st_ino == before.st_ino == after_read.st_ino == after_path.st_ino
        and opened.st_size == before.st_size == after_read.st_size == after_path.st_size
        and opened.st_ctime_ns
        == before.st_ctime_ns
        == after_read.st_ctime_ns
        == after_path.st_ctime_ns
        and opened.st_mtime_ns
        == before.st_mtime_ns
        == after_read.st_mtime_ns
        == after_path.st_mtime_ns
        and after_path.st_uid == os.geteuid()
        and stat.S_IMODE(after_path.st_mode) == 0o600
        and b"".join(chunks) == expected
    )


def block_if_remote_write_safety_incident_latched() -> None:
    """Fail before remote work when a marker or either process latch exists."""
    if not remote_write_safety_protocol_is_active():
        raise AmbiguousRemotePostOutcome(
            "Remote-write safety protocol is not durably activated; every "
            "remote operation remains blocked: "
            f"{REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE}",
            service="x",
        )
    marker_exists = remote_write_safety_marker_path_present_or_unsafe()
    # Re-read after the namespace probe: observing or failing to inspect the
    # marker seeds both process latches, and a concurrent signal-path latch
    # must not be lost to a stale pre-probe snapshot.
    incident_latched = remote_write_safety_incident_is_latched()
    if not incident_latched and not marker_exists:
        return
    if marker_exists:
        raise AmbiguousRemotePostOutcome(
            "Unreconciled ambiguous/confirmed-persistence remote-write safety "
            "barrier blocks further posting: "
            f"{AMBIGUOUS_POST_OUTCOME_FILE} or "
            f"{AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE}",
            service="x",
        )
    raise AmbiguousRemotePostOutcome(
        "Unreconciled in-process remote-write safety latch or marker-"
        "durability uncertainty blocks further posting",
        service="x",
    )


def remote_write_transport_journal_paths() -> tuple[Path, ...]:
    """Return every distinct transaction-journal path used by active lanes."""

    return tuple(
        sorted(
            {
                journal_path_for_receipt(path)
                for path in (
                    REGULAR_POST_RECEIPT_FILE,
                    MEME_POST_RECEIPT_FILE,
                    CONFIRMED_REPLY_RECEIPT_FILE,
                    HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
                )
            },
            key=str,
        )
    )


def remote_source_receipt_paths() -> tuple[Path, ...]:
    """Return the four current public-create source receipt paths."""

    return (
        REGULAR_POST_RECEIPT_FILE,
        MEME_POST_RECEIPT_FILE,
        CONFIRMED_REPLY_RECEIPT_FILE,
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
    )


def canonical_transport_receipt_path_for_lane(lane: str) -> Path | None:
    """Return the only receipt pathname allowed to authorise one public lane."""

    return {
        "quote_image": REGULAR_POST_RECEIPT_FILE,
        "daily_meme": MEME_POST_RECEIPT_FILE,
        "conversational_reply": CONFIRMED_REPLY_RECEIPT_FILE,
        "historical_context_reply": HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
    }.get(str(lane))


TRANSPORT_SOURCE_VALIDATOR_ID = LANE_SOURCE_VALIDATOR_ID


def transport_source_semantic_validator(
    lane: str,
    receipt: dict,
    payload: dict,
) -> bool:
    """Prove that one lane-owned source receipt authorises one tweet body."""

    if lane in {"quote_image", "daily_meme"}:
        return bool(
            receipt.get("lane") == lane
            and receipt.get("lifecycle_state") == "attempting"
            and main_post_attempt_binds_payload(receipt, payload)
        )
    if lane == "conversational_reply":
        expected_keys = {"text", "reply"}
        if payload.get("made_with_ai") is True:
            expected_keys.add("made_with_ai")
        return bool(
            sending_reply_receipt_is_semantically_valid(receipt)
            and set(payload) == expected_keys
            and payload.get("text") == receipt.get("reply_text")
            and payload.get("reply")
            == {"in_reply_to_tweet_id": str(receipt.get("target_id"))}
        )
    if lane == "historical_context_reply":
        from historical_context_formatter import HistoricalContextReplyStore

        return bool(
            HistoricalContextReplyStore._valid_sending_receipt(receipt)
            and set(payload) == {"text", "reply"}
            and payload.get("text") == receipt.get("reply_text")
            and payload.get("reply")
            == {"in_reply_to_tweet_id": str(receipt.get("parent_post_id"))}
        )
    return False


def _legacy_conversational_transport_source_semantic_validator(
    lane: str,
    receipt: dict,
    payload: dict,
) -> bool:
    """Validate a frozen reply source solely for confirmed-journal recovery."""

    if lane != "conversational_reply":
        return False
    expected_keys = {"text", "reply"}
    if payload.get("made_with_ai") is True:
        expected_keys.add("made_with_ai")
    return bool(
        _legacy_sending_reply_receipt_is_semantically_valid(receipt)
        and set(payload) == expected_keys
        and payload.get("text") == receipt.get("reply_text")
        and payload.get("reply")
        == {"in_reply_to_tweet_id": str(receipt.get("target_id"))}
    )


def bind_lane_transport_source(
    *,
    receipt_path: Path,
    receipt: dict,
    lane: str,
    payload: dict,
) -> SourceReceiptBinding:
    """Create the only accepted semantic source binding for a public tweet."""

    if lane == "historical_context_reply":
        from historical_context_formatter import (
            canonical_json_bytes as canonical_context_receipt_bytes,
        )

        expected_receipt_bytes = canonical_context_receipt_bytes(receipt)
    else:
        expected_receipt_bytes = canonical_atomic_json_bytes(receipt)

    return bind_transport_source(
        receipt_path=receipt_path,
        expected_receipt=receipt,
        expected_receipt_bytes=expected_receipt_bytes,
        lane=lane,
        payload=payload,
        validator_id=TRANSPORT_SOURCE_VALIDATOR_ID,
        validator=transport_source_semantic_validator,
    )


def block_if_unrelated_receipt_appeared_for_tweet_transport(
    expected_receipt_path: Path,
) -> None:
    """Reject a lane which appeared after the transaction's initial preflight."""

    if remote_receipt_retirement_is_blocking():
        raise TransportJournalError(
            "a source-receipt retirement appeared before tweet transport"
        )

    for path in (
        REGULAR_POST_RECEIPT_FILE,
        MEME_POST_RECEIPT_FILE,
        CONFIRMED_REPLY_RECEIPT_FILE,
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
    ):
        if path == expected_receipt_path:
            continue
        try:
            receipt_present = receipt_namespace_entry_exists(path)
        except OSError as exc:
            raise TransportJournalError(
                "an unrelated receipt namespace could not be inspected "
                "before tweet transport"
            ) from exc
        if receipt_present:
            raise TransportJournalError(
                "an unrelated durable receipt appeared before tweet transport"
            )
    if media_upload_receipt_is_blocking(MEDIA_UPLOAD_RECEIPT_FILE):
        raise TransportJournalError(
            "an unresolved media receipt appeared before tweet transport"
        )


def block_if_unrelated_receipt_appeared_for_media_transport() -> None:
    """Reject media transport if any other transaction owns remote writes."""

    if remote_receipt_retirement_is_blocking():
        raise MediaUploadReceiptError(
            "a source-receipt retirement appeared before media transport"
        )

    if remote_write_transport_journal_is_blocking():
        raise MediaUploadReceiptError(
            "a public-create transport journal appeared before media upload"
        )
    for path in (
        REGULAR_POST_RECEIPT_FILE,
        MEME_POST_RECEIPT_FILE,
        CONFIRMED_REPLY_RECEIPT_FILE,
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
    ):
        try:
            receipt_present = receipt_namespace_entry_exists(path)
        except OSError as exc:
            raise MediaUploadReceiptError(
                "an unrelated receipt namespace could not be inspected "
                "before media upload"
            ) from exc
        if receipt_present:
            raise MediaUploadReceiptError(
                "an unrelated durable receipt appeared before media upload"
            )


def remote_write_transport_journal_is_blocking() -> bool:
    """Return whether any valid, invalid, or torn transport journal exists."""

    return any(
        transport_journal_is_blocking(path)
        for path in remote_write_transport_journal_paths()
    )


def remote_receipt_retirement_is_blocking() -> bool:
    """Return whether any source-receipt retirement is incomplete or unsafe."""

    return any(
        retirement_auxiliary_barrier_exists(path)
        or retirement_ledger_is_blocking(path)
        for path in remote_source_receipt_paths()
    )


def confirmed_context_outbox_matches_receipt(
    context_reply: dict,
    receipt: dict,
) -> bool:
    """Match one confirmed outbox outcome to the receipt's exact lineage.

    Legacy confirmed receipts and legacy terminal outbox rows both predate the
    source-receipt hash.  They may be paired only with each other.  A current
    lineage-bearing receipt must never be reconciled through a legacy terminal
    row, because parent/quote/reply identity alone cannot distinguish a stale
    or substituted receipt generation.
    """

    if (
        not isinstance(context_reply, dict)
        or not isinstance(receipt, dict)
        or context_reply.get("state") != "context_reply_confirmed"
        or context_reply.get("quote_id") != receipt.get("quote_id")
        or context_reply.get("reply_post_id") != receipt.get("reply_post_id")
    ):
        return False
    outbox_source_fields = {
        "source_receipt_sha256",
        "source_receipt_attempt_number",
    }
    outbox_has_source = outbox_source_fields.issubset(context_reply)
    receipt_has_source = "source_receipt_sha256" in receipt
    if outbox_has_source != receipt_has_source:
        return False
    if not receipt_has_source:
        # The oldest accepted confirmed receipt predates attempt ordinals as
        # well as source hashes.  Preserve that deliberately weaker legacy
        # replay, while requiring exact ordinals for the later legacy shape
        # which does carry one.
        if "attempt_number" not in receipt:
            return True
        return context_reply.get("attempt_count") == receipt.get("attempt_number")
    return bool(
        context_reply.get("source_receipt_sha256")
        == receipt.get("source_receipt_sha256")
        and context_reply.get("source_receipt_attempt_number")
        == receipt.get("attempt_number")
    )


def require_historical_context_retirement_outbox_authority() -> None:
    """Bind an interrupted context retirement to its durable outbox outcome.

    A confirmed reply or a proved remote non-success is written to history and
    outbox before exact source retirement starts.  No retirement namespace may
    be resumed until its marker hash matches exactly one such durable outcome.
    """

    if not retirement_auxiliary_barrier_exists(
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
    ):
        return
    from historical_context_formatter import (
        HistoricalContextReplyStore,
        canonical_json_bytes,
    )

    context_store = historical_context_reply_store()
    retirement = inspect_interrupted_receipt_retirement(
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
    )
    if not retirement.valid or not re.fullmatch(
        r"[0-9a-f]{64}", retirement.expected_sha256
    ):
        raise ExactReceiptRetirementError(
            "historical-context retirement has no valid marker-bound source"
        )

    authorities: list[tuple[str, dict]] = []
    for item in context_store.history()["items"].values():
        if not isinstance(item, dict):
            continue
        if item.get("status") == "completed":
            receipt = {
                key: value for key, value in item.items() if key != "status"
            }
            receipt_bytes = canonical_json_bytes(receipt)
            inspection = inspect_exact_receipt_retirement(
                HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
                receipt_bytes,
            )
            if inspection.valid:
                authorities.append(("completed", receipt))
        elif (
            item.get("status") == "failed"
            and item.get("remote_outcome") == "proved_non_success"
            and item.get("source_receipt_sha256")
            == retirement.expected_sha256
            and item.get("source_receipt_attempt_number")
            == item.get("attempt_count")
        ):
            authorities.append(("failed", item))
    if len(authorities) != 1:
        raise ExactReceiptRetirementError(
            "historical-context retirement has no unique terminal-history "
            "authority"
        )
    authority_kind, receipt = authorities[0]
    parent_id = str(receipt["parent_post_id"])
    outbox_store = historical_context_outbox_store()
    obligation = outbox_store.get(parent_id)
    context = (
        obligation.get("context_reply")
        if isinstance(obligation, dict)
        else None
    )
    if not isinstance(obligation, dict):
        raise ExactReceiptRetirementError(
            "historical-context retirement has no matching outbox obligation"
        )
    if (
        not isinstance(context, dict)
        or context.get("quote_id") != receipt["quote_id"]
    ):
        raise ExactReceiptRetirementError(
            "historical-context retirement conflicts with its outbox identity"
        )
    if authority_kind == "failed":
        failure = context.get("failure")
        if (
            context.get("state")
            not in {
                "context_reply_failed_retryable",
                "context_reply_failed_terminal",
            }
            or not isinstance(failure, dict)
            or failure.get("remote_outcome") != "proved_non_success"
            or failure.get("source_receipt_sha256")
            != retirement.expected_sha256
            or failure.get("source_receipt_attempt_number")
            != receipt.get("source_receipt_attempt_number")
            or context.get("attempt_count") != failure.get("attempt_number")
            or transport_journal_is_blocking(
                journal_path_for_receipt(
                    HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
                )
            )
        ):
            raise ExactReceiptRetirementError(
                "historical-context retirement conflicts with its exact "
                "proved-failure outbox authority"
            )
        return

    if context.get("state") == "context_reply_attempting":
        source_sha256 = receipt.get("source_receipt_sha256")
        source_attempt = receipt.get("attempt_number")
        if source_sha256 is not None:
            if (
                context.get("remote_transaction_started") is not True
                or context.get("source_receipt_sha256") != source_sha256
                or context.get("source_receipt_attempt_number")
                != source_attempt
            ):
                raise ExactReceiptRetirementError(
                    "historical-context retirement conflicts with its exact "
                    "attempting outbox source"
                )
        elif {
            "source_receipt_sha256",
            "source_receipt_attempt_number",
        } & set(context):
            raise ExactReceiptRetirementError(
                "legacy historical-context retirement conflicts with a "
                "source-bound attempting outbox"
            )
        elif (
            "attempt_number" in receipt
            and context.get("attempt_count") != receipt.get("attempt_number")
        ):
            raise ExactReceiptRetirementError(
                "legacy historical-context retirement conflicts with its "
                "outbox attempt"
            )
        outbox_store.record_confirmed(
            parent_id,
            attempt_number=int(context["attempt_count"]),
            reply_post_id=receipt["reply_post_id"],
            # Legacy confirmed_at values were not canonical timestamps.  The
            # outbox records the local recovery observation and clamps it to
            # its already durable timeline, just like the ordinary
            # already_completed path.
            confirmed_epoch=now_epoch(),
        )
    elif not confirmed_context_outbox_matches_receipt(context, receipt):
        raise ExactReceiptRetirementError(
            "historical-context retirement conflicts with its durable outbox "
            "outcome"
        )


def resume_interrupted_source_receipt_retirement_if_present() -> bool:
    """Finish one journal-free source retirement under the process lock.

    A matching transport journal must retain the source receipt until its own
    retirement has completed.  Multiple lane auxiliaries are never selected
    automatically, and every namespace inspection includes broken symlinks.
    """

    receipt_paths = (
        REGULAR_POST_RECEIPT_FILE,
        MEME_POST_RECEIPT_FILE,
        CONFIRMED_REPLY_RECEIPT_FILE,
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
    )
    active = [
        path
        for path in receipt_paths
        if retirement_auxiliary_barrier_exists(path)
    ]
    if not active:
        return False
    if len(active) != 1:
        raise ExactReceiptRetirementError(
            "multiple source-receipt retirement lanes require manual inspection"
        )
    source_path = active[0]
    if source_path == HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE:
        require_historical_context_retirement_outbox_authority()
    blocking_journals = [
        path
        for path in remote_write_transport_journal_paths()
        if transport_journal_is_blocking(path)
    ]
    if blocking_journals:
        owning_journal = journal_path_for_receipt(source_path)
        if len(blocking_journals) != 1 or (
            blocking_journals[0].absolute() != owning_journal.absolute()
        ):
            raise ExactReceiptRetirementError(
                "source retirement overlaps an unrelated transport journal"
            )
        journal_state = inspect_transport_state(owning_journal)
        if journal_state.journal is None:
            raise ExactReceiptRetirementError(
                "source retirement cannot identify an incomplete owning journal"
            )
        journal_document = journal_state.journal.document
        lane = str(journal_document["lane"])
        post_id = str(journal_document.get("remote_post_id") or "")
        if not post_id:
            raise ExactReceiptRetirementError(
                "source retirement owning journal is not confirmed"
            )
        if source_path == REGULAR_POST_RECEIPT_FILE:
            _status, receipt = load_regular_post_receipt()
            receipt_bytes = (
                canonical_atomic_json_bytes(receipt)
                if isinstance(receipt, dict)
                else b""
            )
        elif source_path == MEME_POST_RECEIPT_FILE:
            _status, receipt = load_meme_post_receipt()
            receipt_bytes = (
                canonical_atomic_json_bytes(receipt)
                if isinstance(receipt, dict)
                else b""
            )
        elif source_path == CONFIRMED_REPLY_RECEIPT_FILE:
            _status, receipt = load_confirmed_reply_receipt()
            receipt_bytes = (
                canonical_atomic_json_bytes(receipt)
                if isinstance(receipt, dict)
                else b""
            )
        else:
            from historical_context_formatter import HistoricalContextReplyStore

            loaded = historical_context_reply_store()._load_receipt_safely()
            receipt = loaded[0] if loaded is not None else None
            receipt_bytes = loaded[1] if loaded is not None else b""
        if not isinstance(receipt, dict) or not receipt_bytes:
            raise ExactReceiptRetirementError(
                "prepared source receipt is unavailable or invalid"
            )
        retire_lane_transport_journal_if_present(
            receipt_path=source_path,
            receipt=receipt,
            lane=lane,
            post_id=post_id,
            current_receipt_bytes=receipt_bytes,
        )
    result = resume_interrupted_receipt_retirement(
        source_path,
        mutation_authority=transaction_mutation_authority(
            "interrupted source receipt retirement resume"
        ),
    )
    log.warning(
        "Resumed interrupted exact source-receipt retirement path=%s phase=%s",
        source_path,
        result.initial_phase,
    )
    return True


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


def retire_current_source_receipt(
    receipt_path: Path,
    expected_receipt_bytes: bytes,
) -> None:
    """Resume a prepared removal, or start retirement when no journal existed."""

    retire_or_resume_exact_receipt(
        receipt_path,
        expected_receipt_bytes,
        mutation_authority=transaction_mutation_authority(
            "source receipt exact retirement"
        ),
        on_retirement_uncertainty=latch_source_receipt_retirement_uncertainty,
    )


def block_if_remote_receipt_retirement_exists() -> None:
    """Fail closed while a receipt-removal transaction remains unfinished."""

    if remote_receipt_retirement_is_blocking():
        raise AmbiguousRemotePostOutcome(
            "An incomplete source-receipt retirement blocks every remote-write lane",
            service="x",
        )


def block_if_remote_write_transport_journal_exists() -> None:
    """Fail closed before unrelated remote work while a journal is unresolved."""

    if remote_write_transport_journal_is_blocking():
        raise AmbiguousRemotePostOutcome(
            "An unresolved payload-bound transport journal blocks every other "
            "remote-write lane",
            service="x",
        )


def confirmed_main_receipt_is_sole_local_recovery_barrier() -> bool:
    """Return whether one main receipt may finish under its confirmed journal.

    This is intentionally narrower than ignoring the global journal barrier.
    It exists so the regular and meme entry points can perform local recovery
    when called directly, just as the daemon's pre-barrier reconciler does.
    """

    source_paths = (
        REGULAR_POST_RECEIPT_FILE,
        MEME_POST_RECEIPT_FILE,
        CONFIRMED_REPLY_RECEIPT_FILE,
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
    )
    present = [
        path for path in source_paths if receipt_namespace_entry_exists(path)
    ]
    if len(present) != 1 or present[0] not in {
        REGULAR_POST_RECEIPT_FILE,
        MEME_POST_RECEIPT_FILE,
    }:
        return False
    owner = present[0]
    status, receipt = (
        load_regular_post_receipt()
        if owner == REGULAR_POST_RECEIPT_FILE
        else load_meme_post_receipt()
    )
    if status not in {"pending_schedule", "valid"} or receipt is None:
        return False
    blocking_states = [
        (path, inspect_transport_state(path))
        for path in remote_write_transport_journal_paths()
        if transport_journal_is_blocking(path)
    ]
    if not blocking_states:
        # Only legacy full receipts can legitimately predate a journal.
        return status == "valid"
    if len(blocking_states) != 1:
        return False
    path, state = blocking_states[0]
    if not (
        path.absolute() == journal_path_for_receipt(owner).absolute()
        and state.classification == "confirmed_pair"
    ):
        return False
    lane = "quote_image" if owner == REGULAR_POST_RECEIPT_FILE else "daily_meme"
    try:
        return verify_lane_transport_source_lineage_if_present(
            receipt_path=owner,
            receipt=receipt,
            lane=lane,
            post_id=str(receipt.get("post_id") or ""),
        )
    except Exception:
        log.critical(
            "A confirmed main-post recovery receipt failed source-lineage "
            "validation before local reconciliation",
            exc_info=True,
        )
        return False


def remote_media_upload_receipt_is_blocking() -> bool:
    """Return whether a valid, invalid, or torn media transaction exists."""

    return media_upload_receipt_is_blocking(MEDIA_UPLOAD_RECEIPT_FILE)


def block_if_remote_media_upload_receipt_exists() -> None:
    """Fail closed before unrelated work while a media upload is unresolved."""

    if remote_media_upload_receipt_is_blocking():
        raise AmbiguousRemotePostOutcome(
            "An unresolved media-upload receipt blocks every remote-write lane",
            service="x",
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

    require_instance_lock_for_remote_write(
        "Interrupted confirmed-media retirement recovery"
    )

    def entry_exists(path: Path) -> bool:
        try:
            os.lstat(path)
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise MediaUploadReceiptError(
                "confirmed media retirement namespace cannot be inspected"
            ) from exc
        return True

    media_receipt_path = Path(MEDIA_UPLOAD_RECEIPT_FILE)
    media_fence_path = media_fence_path_for_receipt(media_receipt_path)
    if entry_exists(media_receipt_path):
        return False
    if not entry_exists(media_fence_path):
        return False

    source_by_lane = {
        "quote_image": Path(REGULAR_POST_RECEIPT_FILE),
        "daily_meme": Path(MEME_POST_RECEIPT_FILE),
    }
    journal_paths = {
        journal_path_for_receipt(source_path).absolute()
        for source_path in source_by_lane.values()
    }
    if len(journal_paths) != 1:
        raise MediaUploadReceiptError(
            "main-lane transport journals do not share one canonical owner path"
        )
    journal_path = next(iter(journal_paths))
    owner_state = inspect_transport_state(journal_path)
    if (
        not owner_state.blocking
        or owner_state.classification != "prepared_pair"
        or owner_state.journal is None
        or owner_state.fence is None
    ):
        raise MediaUploadReceiptError(
            "orphaned media fence owner is not one intact prepared pair"
        )
    owner_document = owner_state.journal.document
    lane = str(owner_document.get("lane") or "")
    source_path = source_by_lane.get(lane)
    if (
        source_path is None
        or journal_path.absolute()
        != journal_path_for_receipt(source_path).absolute()
        or owner_document.get("source_receipt", {}).get("basename")
        != source_path.name
    ):
        raise MediaUploadReceiptError(
            "orphaned media fence owner does not bind a canonical main lane"
        )

    result = resume_interrupted_confirmed_media_retirement(
        media_receipt_path,
        mutation_authority=transaction_mutation_authority(
            "interrupted media retirement resume"
        ),
        transport_journal_path=journal_path,
        transport_fence_path=fence_path_for_journal(journal_path),
        source_receipt_path=source_path,
    )
    if result is None or result.state != "retired":
        raise MediaUploadReceiptError(
            "interrupted confirmed media retirement did not complete"
        )
    log.warning(
        "Resumed interrupted confirmed-media fence retirement lane=%s "
        "media_transaction_id=%s media_id=%s; prepared tweet transaction "
        "remains blocked",
        result.lane,
        result.media_transaction_id,
        result.media_id,
    )
    return True


def expected_lane_transport_source_receipt_bytes(
    *,
    receipt: dict,
    lane: str,
    current_receipt_bytes: bytes,
) -> bytes:
    """Reconstruct the exact pre-transport receipt for one public lane."""

    if type(current_receipt_bytes) is not bytes or not current_receipt_bytes:
        raise TransportJournalError("current lane receipt bytes are invalid")
    if lane in {"quote_image", "daily_meme"}:
        if (
            current_main_post_attempt_is_semantically_valid(receipt)
            and receipt.get("lifecycle_state") == "attempting"
        ):
            return current_receipt_bytes
        if (
            receipt.get("receipt_type") == "confirmed_pending_schedule"
            and confirmed_pending_schedule_receipt_is_semantically_valid(
                receipt,
                expected_lane=lane,
            )
        ):
            return canonical_atomic_json_bytes(receipt["source_attempt"])
        source_attempt = receipt.get("source_attempt")
        source_sha256 = receipt.get("source_attempt_sha256")
        if (
            not isinstance(source_attempt, dict)
            or not re.fullmatch(r"[0-9a-f]{64}", str(source_sha256 or ""))
        ):
            raise TransportJournalError(
                "current main-post journal cannot be reconciled by a receipt "
                "without exact source-attempt lineage"
            )
        source_bytes = canonical_atomic_json_bytes(source_attempt)
        if hashlib.sha256(source_bytes).hexdigest() != source_sha256:
            raise TransportJournalError(
                "main-post confirmed source-attempt lineage changed"
            )
        return source_bytes
    if lane == "conversational_reply":
        if sending_reply_receipt_is_semantically_valid(receipt):
            return current_receipt_bytes
        source = conversational_sending_receipt_from_confirmed(receipt)
        source_bytes = canonical_atomic_json_bytes(source)
        if (
            hashlib.sha256(source_bytes).hexdigest()
            != receipt.get("source_receipt_sha256")
        ):
            raise TransportJournalError(
                "conversational confirmed source-receipt lineage changed"
            )
        return source_bytes
    if lane == "historical_context_reply":
        from historical_context_formatter import HistoricalContextReplyStore

        if HistoricalContextReplyStore._valid_sending_receipt(receipt):
            return current_receipt_bytes
        try:
            return HistoricalContextReplyStore.source_receipt_bytes_from_confirmed(
                receipt
            )
        except (TypeError, ValueError) as exc:
            raise TransportJournalError(
                "historical-context confirmed source-receipt lineage changed"
            ) from exc
    raise TransportJournalError(
        "confirmed transport journal has no supported source-lineage lane"
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

    journal_path = journal_path_for_receipt(receipt_path)
    if not transport_journal_is_blocking(journal_path):
        return False
    if current_receipt_bytes is None:
        if lane == "historical_context_reply":
            from historical_context_formatter import canonical_json_bytes

            current_receipt_bytes = canonical_json_bytes(receipt)
        else:
            current_receipt_bytes = canonical_atomic_json_bytes(receipt)
    source_bytes = expected_lane_transport_source_receipt_bytes(
        receipt=receipt,
        lane=lane,
        current_receipt_bytes=current_receipt_bytes,
    )
    details = verify_confirmed_transport_source_lineage(
        receipt_path=receipt_path,
        expected_source_receipt_bytes=source_bytes,
        lane=lane,
        post_id=post_id,
        validator_id=TRANSPORT_SOURCE_VALIDATOR_ID,
    )
    if lane == "quote_image":
        derived_epoch = receipt_int(
            receipt.get("confirmation_epoch")
            if receipt.get("receipt_type") == "confirmed_pending_schedule"
            else receipt.get("quote_post_epoch")
        )
    elif lane == "daily_meme":
        derived_epoch = receipt_int(
            receipt.get("confirmation_epoch")
            if receipt.get("receipt_type") == "confirmed_pending_schedule"
            else receipt.get("meme_post_epoch")
        )
    elif lane == "conversational_reply":
        derived_epoch = receipt_int(receipt.get("confirmation_epoch"))
    else:
        derived_epoch = None
    if lane != "historical_context_reply" and (
        derived_epoch is None or derived_epoch != details.confirmation_epoch
    ):
        raise TransportJournalError(
            "confirmed receipt time differs from its transport journal"
        )
    return True


def retire_lane_transport_journal_if_present(
    *,
    receipt_path: Path,
    receipt: dict,
    lane: str,
    post_id: str,
    current_receipt_bytes: bytes | None = None,
) -> bool:
    """Retire a confirmed journal while an exact source-removal guard overlaps."""

    journal_path = journal_path_for_receipt(receipt_path)
    if not transport_journal_is_blocking(journal_path):
        return False
    if current_receipt_bytes is None:
        if lane == "historical_context_reply":
            from historical_context_formatter import canonical_json_bytes

            current_receipt_bytes = canonical_json_bytes(receipt)
        else:
            current_receipt_bytes = canonical_atomic_json_bytes(receipt)
    expected_source_receipt_bytes = expected_lane_transport_source_receipt_bytes(
        receipt=receipt,
        lane=lane,
        current_receipt_bytes=current_receipt_bytes,
    )
    prepare_exact_receipt_retirement(
        receipt_path,
        current_receipt_bytes,
        mutation_authority=transaction_mutation_authority(
            "source receipt retirement preparation"
        ),
    )
    retire_confirmed_transport_transaction(
        mutation_authority=transaction_mutation_authority(
            "confirmed transport journal retirement"
        ),
        receipt_path=receipt_path,
        expected_confirmed_receipt=receipt,
        expected_source_receipt_bytes=expected_source_receipt_bytes,
        expected_current_receipt_bytes=current_receipt_bytes,
        source_retirement_prepared=True,
        lane=lane,
        post_id=post_id,
    )
    return True


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
    prepared_receipt_count = sum(
        item is not None
        for item in (
            prepared_conversational_reply_receipt,
            prepared_historical_context_reply_receipt,
            prepared_main_post_attempt,
        )
    )
    if prepared_receipt_count > 1:
        raise AmbiguousRemotePostOutcome(
            "One remote write cannot be authorised by multiple transaction "
            "receipts or attempt records",
            service="x",
        )
    local_main_reconciliation_authorised = bool(
        allow_confirmed_pending_schedule_reconciliation
        and confirmed_main_receipt_is_sole_local_recovery_barrier()
    )
    block_if_remote_write_safety_incident_latched()
    if historical_context_outbox_remote_attempt_is_blocking(
        prepared_receipt=prepared_historical_context_reply_receipt,
        prepared_transport_authority=prepared_transport_authority,
        allow_local_reconciliation_parent_id=(
            allow_historical_context_outbox_reconciliation_parent_id
        ),
    ):
        raise AmbiguousRemotePostOutcome(
            "A historical-context outbox attempt may have reached remote "
            "transport and blocks every remote-write lane",
            service="x",
        )
    block_if_remote_receipt_retirement_exists()
    if prepared_transport_authority is None:
        if not local_main_reconciliation_authorised:
            block_if_remote_write_transport_journal_exists()
    else:
        expected_journal = Path(prepared_transport_authority.journal_path)
        state = inspect_transport_state(expected_journal)
        unrelated = [
            path
            for path in remote_write_transport_journal_paths()
            if path.absolute() != expected_journal.absolute()
            and transport_journal_is_blocking(path)
        ]
        if (
            unrelated
            or prepared_transport_authority.lifecycle_state != "prepared"
            or state.classification != "prepared_pair"
            or state.journal is None
            or state.fence is None
            or state.journal.document.get("transaction_id")
            != prepared_transport_authority.transaction_id
            or state.journal.sha256
            != prepared_transport_authority.journal_sha256
            or state.fence.sha256 != prepared_transport_authority.fence_sha256
        ):
            raise AmbiguousRemotePostOutcome(
                "Prepared tweet authority is not the sole exact transport barrier",
                service="x",
            )
    block_if_remote_media_upload_receipt_exists()

    regular_status, regular_receipt = load_regular_post_receipt()
    meme_status, meme_receipt = load_meme_post_receipt()
    prepared_main_authorized = prepared_main_post_attempt is None
    blocking_main_receipts = [
        ("regular", regular_status, regular_receipt),
        ("meme", meme_status, meme_receipt),
    ]
    for lane_name, status, receipt in blocking_main_receipts:
        if status == "absent":
            continue
        if (
            status in {"pending_schedule", "valid"}
            and local_main_reconciliation_authorised
        ):
            # Main-lane entry points may pass this narrow exception solely to
            # reach their local receipt reconciler.  Every remote-create
            # preflight, including the later preflight in those same lanes,
            # retains the default fail-closed behaviour.
            continue
        if status == "invalid":
            if lane_name == "regular":
                raise InvalidRegularPostReceipt(
                    f"Invalid regular-post receipt blocks posting: "
                    f"{REGULAR_POST_RECEIPT_FILE}"
                )
            raise InvalidMemePostReceipt(
                f"Invalid meme-post receipt blocks posting: {MEME_POST_RECEIPT_FILE}"
            )
        if (
            status == "sending"
            and prepared_main_post_attempt is not None
            and receipt == prepared_main_post_attempt
            and current_main_post_attempt_is_semantically_valid(
                prepared_main_post_attempt
            )
            and prepared_main_post_attempt.get("lifecycle_state")
            == (
                "attempting"
                if prepared_transport_authority is not None
                else "sending"
            )
        ):
            prepared_main_authorized = True
            continue
        raise AmbiguousRemotePostOutcome(
            "An unresolved main-post sending, confirmed pending-schedule, or "
            "invalid receipt blocks further posting",
            service="x",
        )
    if not prepared_main_authorized:
        raise AmbiguousRemotePostOutcome(
            "Prepared main-post attempt is not the exact durable sending receipt",
            service="x",
        )

    status, receipt = load_confirmed_reply_receipt()
    if prepared_conversational_reply_receipt is not None:
        if not (
            status == "sending"
            and receipt == prepared_conversational_reply_receipt
        ):
            raise AmbiguousRemotePostOutcome(
                "Prepared conversational-reply receipt is not the exact "
                "durable sending receipt",
                service="x",
            )
    elif status != "absent":
        raise AmbiguousRemotePostOutcome(
            "An unresolved conversational-reply source receipt blocks "
            f"further posting: {CONFIRMED_REPLY_RECEIPT_FILE}",
            service="x",
        )

    if not historical_context_receipt_path_present_or_unsafe():
        if prepared_historical_context_reply_receipt is not None:
            raise AmbiguousRemotePostOutcome(
                "Prepared historical-context receipt is not durably present",
                service="x",
            )
        return
    if (
        allow_historical_context_receipt_reconciliation
        and historical_context_receipt_parent_for_local_reconciliation()
        is not None
    ):
        # This exception reaches only the outbox worker's local receipt
        # reconciler.  The worker must re-run the ordinary fail-closed check
        # before claiming or transmitting any other context attempt.
        return
    if exact_historical_context_sending_receipt_matches(
        prepared_historical_context_reply_receipt
    ):
        return
    raise AmbiguousRemotePostOutcome(
        "An unresolved or unsafe historical-context reply receipt blocks "
        f"further posting: {HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE}",
        service="x",
    )


def ambiguous_remote_post_is_blocking() -> bool:
    """Return the global write barrier state without starting any remote work."""
    if not remote_write_safety_protocol_is_active():
        return True
    if remote_write_safety_incident_is_latched():
        return True
    if remote_write_safety_marker_path_present_or_unsafe():
        return True
    if historical_context_outbox_remote_attempt_is_blocking():
        return True
    if remote_write_transport_journal_is_blocking():
        return True
    if remote_media_upload_receipt_is_blocking():
        return True
    if remote_receipt_retirement_is_blocking():
        return True
    if historical_context_receipt_path_present_or_unsafe():
        return True
    try:
        return (
            unresolved_main_post_attempt_is_blocking()
            or unresolved_conversational_reply_receipt_is_blocking()
        )
    except Exception:
        log.critical(
            "A remote-write receipt cannot be inspected; treating all remote "
            "writes as blocked",
            exc_info=True,
        )
        return True


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
    found = False
    for path in (
        AMBIGUOUS_POST_OUTCOME_FILE,
        AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE,
    ):
        try:
            os.lstat(path)
        except FileNotFoundError:
            continue
        except Exception:
            latch_remote_write_safety_marker_observation()
            log.critical(
                "A remote-write safety barrier namespace cannot be inspected; "
                "treating all remote writes as blocked path=%s",
                path,
                exc_info=True,
            )
            return True
        latch_remote_write_safety_marker_observation()
        found = True
    return found


def require_remote_write_marker_removal_protocol() -> None:
    """Require the process-lifetime instance lock for marker acknowledgement.

    Production marker removal is supported only while the bot is stopped and a
    reconciler holds ``mrsMThatcher.lock`` exclusively.  The running daemon
    holds that lock for its lifetime, so a cooperating reconciler cannot remove
    the marker after the acknowledgement recheck.  Tests use isolated paths and
    exercise the same byte/identity checks without a production lock.
    """
    require_instance_lock_for_remote_write(
        "Remote-write safety marker acknowledgement"
    )


def read_remote_write_safety_marker_snapshot(
    path: Path | None = None,
    *,
    accepted_link_counts: frozenset[int] = frozenset({1}),
) -> tuple[int, int, int, int, bytes]:
    """Read one bounded, no-follow marker snapshot with stable file identity."""
    path = AMBIGUOUS_POST_OUTCOME_FILE if path is None else Path(path)
    before = os.lstat(path)
    # A marker pathname is the surviving restart barrier.  Seed both
    # process-local barriers before any later open, read, fsync or revalidation
    # can fail or race with a cooperating filesystem actor.
    latch_remote_write_safety_marker_observation()
    if not stat.S_ISREG(before.st_mode):
        raise RuntimeError("Remote-write safety marker is not a regular file")
    if before.st_nlink not in accepted_link_counts:
        raise RuntimeError(
            "Remote-write safety marker has an unsupported filesystem-link count"
        )
    if before.st_size > REMOTE_WRITE_SAFETY_MARKER_MAX_BYTES:
        raise RuntimeError("Remote-write safety marker exceeds the size limit")

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise RuntimeError("O_NOFOLLOW is required for safety-marker inspection")
    fd = os.open(path, flags | nofollow)
    try:
        opened = os.fstat(fd)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != before.st_nlink
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
        ):
            raise RuntimeError(
                "Remote-write safety marker changed while it was opened"
            )
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(fd, min(8192, REMOTE_WRITE_SAFETY_MARKER_MAX_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > REMOTE_WRITE_SAFETY_MARKER_MAX_BYTES:
                raise RuntimeError("Remote-write safety marker exceeds the size limit")
        after_read = os.fstat(fd)
        if (
            after_read.st_dev != opened.st_dev
            or after_read.st_ino != opened.st_ino
            or after_read.st_nlink != opened.st_nlink
            or after_read.st_size != opened.st_size
            or after_read.st_ctime_ns != opened.st_ctime_ns
            or after_read.st_mtime_ns != opened.st_mtime_ns
        ):
            raise RuntimeError(
                "Remote-write safety marker changed while it was read"
            )
        os.fsync(fd)
        after_sync = os.fstat(fd)
        after_path = os.lstat(path)
        if (
            not stat.S_ISREG(after_path.st_mode)
            or after_sync.st_nlink != opened.st_nlink
            or after_path.st_nlink != opened.st_nlink
            or after_sync.st_dev != opened.st_dev
            or after_sync.st_ino != opened.st_ino
            or after_sync.st_size != opened.st_size
            or after_sync.st_ctime_ns != opened.st_ctime_ns
            or after_sync.st_mtime_ns != opened.st_mtime_ns
            or after_path.st_dev != opened.st_dev
            or after_path.st_ino != opened.st_ino
            or stat.S_IFMT(after_path.st_mode) != stat.S_IFMT(opened.st_mode)
            or after_path.st_ctime_ns != opened.st_ctime_ns
        ):
            raise RuntimeError(
                "Remote-write safety marker changed while it was synchronised"
            )
    finally:
        os.close(fd)

    data = b"".join(chunks)
    if len(data) != opened.st_size:
        raise RuntimeError("Remote-write safety marker read was incomplete")
    return (
        opened.st_dev,
        opened.st_ino,
        stat.S_IFMT(opened.st_mode),
        opened.st_ctime_ns,
        data,
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

    entries: dict[Path, os.stat_result] = {}
    for path in (
        AMBIGUOUS_POST_OUTCOME_FILE,
        AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE,
    ):
        try:
            entries[path] = os.lstat(path)
        except FileNotFoundError:
            continue
        except Exception:
            latch_remote_write_safety_marker_observation()
            raise

    if not entries:
        raise FileNotFoundError("No remote-write safety barrier exists")
    latch_remote_write_safety_marker_observation()

    original = entries.get(AMBIGUOUS_POST_OUTCOME_FILE)
    successor = entries.get(AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE)

    if original is not None and successor is None:
        if not stat.S_ISREG(original.st_mode) or original.st_nlink != 1:
            raise RuntimeError(
                "A sole remote-write safety marker must be one ordinary "
                "single-link file"
            )
        # A running process must never migrate this legacy-only state.  The
        # pathname could disappear before the successor link is committed,
        # leaving no restart-persistent barrier after a hard process loss.
        # Only the stopped, lock-bound reconciler may first establish the
        # successor; activation is permitted only after all markers are gone.
        raise RuntimeError(
            "A legacy-only remote-write safety marker requires stopped "
            "offline reconciliation before protocol activation"
        )

    if original is None and successor is not None:
        if not stat.S_ISREG(successor.st_mode) or successor.st_nlink != 1:
            raise RuntimeError(
                "A sole remote-write safety successor must be one ordinary "
                "single-link file"
            )
        return (
            AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE,
            read_remote_write_safety_marker_snapshot(
                AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE,
            ),
        )

    assert original is not None and successor is not None
    if (
        not stat.S_ISREG(original.st_mode)
        or not stat.S_ISREG(successor.st_mode)
        or original.st_nlink != 2
        or successor.st_nlink != 2
        or original.st_dev != successor.st_dev
        or original.st_ino != successor.st_ino
    ):
        raise RuntimeError(
            "Remote-write safety marker and successor are not one exact "
            "two-link ordinary-file pair"
        )
    original_snapshot = read_remote_write_safety_marker_snapshot(
        AMBIGUOUS_POST_OUTCOME_FILE,
        accepted_link_counts=frozenset({2}),
    )
    successor_snapshot = read_remote_write_safety_marker_snapshot(
        AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE,
        accepted_link_counts=frozenset({2}),
    )
    if original_snapshot != successor_snapshot:
        raise RuntimeError(
            "Remote-write safety marker and successor snapshots differ"
        )
    return AMBIGUOUS_POST_OUTCOME_FILE, original_snapshot


def acknowledge_durable_remote_write_safety_marker(
    *,
    expected_bytes: bytes | None = None,
) -> bool:
    """Synchronise and revalidate one unchanged marker namespace entry."""
    require_remote_write_marker_removal_protocol()
    active_path, before = read_remote_write_safety_barrier_snapshot()
    if expected_bytes is not None and before[-1] != expected_bytes:
        raise RuntimeError(
            "Remote-write safety marker does not match the expected incident"
        )

    # File contents are synchronised by the snapshot helper.  The directory
    # fsync makes the name-to-inode binding durable; the second no-follow read
    # proves that the name still identifies the same ordinary file afterwards.
    fsync_parent_dir(active_path, strict=True)
    after_path, after = read_remote_write_safety_barrier_snapshot()
    # Removing the legacy hard-link name legitimately changes inode ctime.
    # The separately synchronised successor remains a complete restart
    # barrier when its device, inode, type and exact bytes are unchanged.
    unchanged_successor_survivor = (
        after_path == AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE
        and before[:3] == after[:3]
        and before[-1] == after[-1]
    )
    if before != after and not unchanged_successor_survivor:
        raise RuntimeError(
            "Remote-write safety marker disappeared, changed or was replaced "
            "during durability acknowledgement"
        )
    if expected_bytes is not None and after[-1] != expected_bytes:
        raise RuntimeError(
            "Remote-write safety marker changed from the expected incident"
        )
    # The supported offline reconciler must still be excluded by the exact
    # process-lifetime lock after the final marker identity/content check.
    require_remote_write_marker_removal_protocol()
    return True


def ensure_durable_remote_write_safety_marker(marker: dict) -> bool:
    """Write or acknowledge a marker without trusting atomic-write return alone."""
    if not remote_write_safety_protocol_is_active():
        raise RuntimeError(
            "Cannot record a remote-write incident while the restart-persistent "
            "safety protocol is inactive"
        )
    expected_bytes = canonical_atomic_json_bytes(marker)
    original_exists = False
    successor_exists = False
    try:
        os.lstat(AMBIGUOUS_POST_OUTCOME_FILE)
    except FileNotFoundError:
        pass
    else:
        original_exists = True
    try:
        os.lstat(AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE)
    except FileNotFoundError:
        pass
    else:
        successor_exists = True
    if not original_exists and not successor_exists:
        try:
            atomic_write_json(
                AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE,
                marker,
                durable=True,
            )
        except Exception:
            # Replacement can succeed before the final directory fsync raises.
            # The central acknowledgement below decides whether exact durable
            # bytes now exist; the writer's return status is not authoritative.
            pass
        # Prove the successor's exact bytes and parent-directory durability
        # before exposing the optional legacy name to any later fallible step.
        # A hard exit from this point onward therefore leaves a complete
        # restart barrier even if the legacy hard-link is never created.
        acknowledge_durable_remote_write_safety_marker(
            expected_bytes=expected_bytes,
        )
        try:
            os.link(
                AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE,
                AMBIGUOUS_POST_OUTCOME_FILE,
                follow_symlinks=False,
            )
        except FileExistsError:
            # The exact namespace is revalidated below.  Never replace an
            # entry which appeared after the initial absence check.
            pass
        except Exception:
            # The successor is the restart barrier.  A legacy display name is
            # useful for compatibility, but inability to add it must not
            # discard an otherwise exact durable successor.
            log.warning(
                "Could not add the legacy remote-write safety marker name; "
                "retaining the successor-only barrier",
                exc_info=True,
            )
    return acknowledge_durable_remote_write_safety_marker(
        expected_bytes=expected_bytes,
    )


def durable_remote_write_safety_marker_exists() -> bool:
    """Return whether restart safety survives loss of the in-process latch."""
    global _AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN
    if not remote_write_safety_protocol_is_active():
        return False
    if not remote_write_safety_marker_path_present_or_unsafe():
        return False

    # Once a namespace entry is observed, only an unchanged, ordinary,
    # no-follow marker may clear uncertainty or release a retained SIGINT.
    latch_remote_write_safety_marker_observation()
    try:
        acknowledge_durable_remote_write_safety_marker()
    except Exception:
        log.critical(
            "The remote-write safety marker cannot be durably acknowledged; "
            "the process-local latch and deferred SIGINT remain active",
            exc_info=True,
        )
        return False
    _AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN = False
    release_retained_sigint_deferral_after_durable_barrier()
    return True


def durable_remote_write_safety_barrier_exists() -> bool:
    """Return whether restart safety survives loss of the process latch."""
    def confirmed() -> bool:
        # Any independently validated incident-specific durable authority—not
        # only the legacy marker—makes controlled process loss restart-safe.
        # Release a retained post-confirmation SIGINT exactly at that point.
        release_retained_sigint_deferral_after_durable_barrier()
        return True

    # Protocol inactivity blocks every compatible process, but it is not
    # incident-specific durable evidence and must not by itself acknowledge an
    # in-flight transaction or release a retained confirmed-post signal guard.
    # Supported clean activation can occur only after every receipt and marker
    # has been reconciled.
    if (
        remote_write_safety_protocol_is_active()
        and durable_remote_write_safety_marker_exists()
    ):
        return True
    # A conservative blocker and proved restart-persistent authority are
    # different claims.  Directory inspection errors, unsafe namespace entries
    # and malformed objects must keep remote writes blocked, but cannot release
    # a retained SIGINT.  Require at least one strict transaction object.
    for journal_path in remote_write_transport_journal_paths():
        try:
            if transport_journal_has_valid_restart_barrier(journal_path):
                return confirmed()
        except Exception:
            continue
    try:
        if media_upload_has_valid_restart_barrier(MEDIA_UPLOAD_RECEIPT_FILE):
            return confirmed()
    except Exception:
        pass
    # Retirement auxiliaries are blockers, but an invalid or uninspectable
    # auxiliary alone is not sufficient durability evidence.  The normal
    # per-tick resumer will either complete a valid retirement or leave the
    # signal guard retained.
    try:
        from historical_context_formatter import HistoricalContextReplyStore

        historical_loaded = (
            historical_context_reply_store()._load_receipt_safely()
        )
        if historical_loaded is not None and (
            HistoricalContextReplyStore._valid_sending_receipt(
                historical_loaded[0]
            )
            or HistoricalContextReplyStore._valid_receipt(historical_loaded[0])
        ):
            return confirmed()
    except Exception:
        pass
    try:
        regular_status, _regular = load_regular_post_receipt()
        meme_status, _meme = load_meme_post_receipt()
        if regular_status in {"sending", "pending_schedule", "valid"}:
            return confirmed()
        if meme_status in {"sending", "pending_schedule", "valid"}:
            return confirmed()
    except Exception:
        pass
    try:
        status, _receipt = load_confirmed_reply_receipt()
    except Exception:
        return False
    return confirmed() if status in {"sending", "legacy_sending", "valid"} else False


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
    global _AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN
    global _AMBIGUOUS_REMOTE_POST_SEEN
    _AMBIGUOUS_REMOTE_POST_SEEN = True
    _AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN = True
    text = str(payload.get("text") or "")
    try:
        marker = {
            "schema_version": 1,
            "recorded_at_epoch": now_epoch(),
            "outcome": "ambiguous_remote_post",
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "reply_to_id": str((payload.get("reply") or {}).get("in_reply_to_tweet_id") or ""),
            "media_ids": list((payload.get("media") or {}).get("media_ids") or []),
            "made_with_ai": bool(payload.get("made_with_ai")),
        }
        ensure_durable_remote_write_safety_marker(marker)
    except Exception:
        log.critical(
            "AMBIGUOUS REMOTE X POST OUTCOME: the durable safety marker could not be "
            "written. The process-local latch remains active; do not restart this "
            "process before manual reconciliation.",
            exc_info=True,
        )
        return
    _AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN = False
    log.critical(
        "AMBIGUOUS REMOTE X POST OUTCOME: X may have accepted the write, but a usable confirmation was not received. "
        "Automatic posting is blocked pending manual reconciliation: %s",
        AMBIGUOUS_POST_OUTCOME_FILE,
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
    global _AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN
    global _AMBIGUOUS_REMOTE_POST_SEEN
    _AMBIGUOUS_REMOTE_POST_SEEN = True
    _AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN = True
    failures = sorted({str(item) for item in failure_components if str(item)})
    incident_identity = json.dumps(
        {
            "failure_components": failures,
            "lane": str(lane),
            "post_id": str(post_id),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    try:
        recorded_at_epoch: int | None = now_epoch()
    except Exception:
        recorded_at_epoch = None
    marker = {
        "schema_version": 1,
        "outcome": "confirmed_remote_post_local_persistence_failed",
        "lane": str(lane),
        "post_id": str(post_id),
        "failure_components": failures,
        "incident_sha256": hashlib.sha256(incident_identity.encode("utf-8")).hexdigest(),
    }
    if recorded_at_epoch is None:
        marker["recorded_at_unavailable"] = True
    else:
        marker["recorded_at_epoch"] = recorded_at_epoch
    try:
        ensure_durable_remote_write_safety_marker(marker)
    except Exception:
        log.critical(
            "CONFIRMED REMOTE POST LOST COMPLETE LOCAL RECOVERY: post_id=%s lane=%s "
            "failures=%s. The durable safety marker could not be written. The "
            "process-local latch remains active; do not restart this process before "
            "manual reconciliation.",
            post_id,
            lane,
            failures,
            exc_info=True,
        )
        return False
    _AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN = False
    log.critical(
        "CONFIRMED REMOTE POST LOST COMPLETE LOCAL RECOVERY: post_id=%s lane=%s "
        "failures=%s. All remote writes are blocked pending manual reconciliation: %s",
        post_id,
        lane,
        failures,
        AMBIGUOUS_POST_OUTCOME_FILE,
    )
    return True


def confirmation_epoch_after_remote_success(source_receipt: dict) -> int:
    """Return a usable confirmation time without losing a known post ID.

    Reading the wall clock is deliberately best-effort *after* X has returned
    a valid post identity.  A clock failure at that point must not strand the
    transport journal in ``attempting`` state and throw away the one piece of
    information which makes automatic restart recovery possible.  Every
    supported sending receipt already contains a durable pre-request epoch;
    that is a conservative lower-bound fallback.
    """

    fallback = receipt_int(source_receipt.get("attempt_epoch"))
    if fallback is None:
        fallback = receipt_int(source_receipt.get("reply_epoch"))
    if fallback is None or not valid_receipt_epoch(fallback):
        raise TransportJournalError(
            "transport source has no durable confirmation-time fallback"
        )
    try:
        observed = int(now_epoch())
    except Exception:
        log.critical(
            "Wall-clock observation failed after X returned a confirmed post "
            "identity; using the durable pre-request epoch so the confirmed "
            "transport identity remains restart-recoverable",
            exc_info=True,
        )
        return fallback
    if not valid_receipt_epoch(observed):
        log.critical(
            "Wall-clock observation was outside the supported receipt range "
            "after X returned a confirmed post identity; using the durable "
            "pre-request epoch"
        )
        return fallback
    return max(fallback, observed)


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
    prepared_receipt_count = sum(
        item is not None
        for item in (
            prepared_conversational_reply_receipt,
            prepared_historical_context_reply_receipt,
            prepared_main_post_attempt,
        )
    )
    if prepared_receipt_count != 1:
        # Preserve the strongest existing failure reason.  An unresolved
        # transaction or process-wide incident must remain visible as the
        # reason this unbound call cannot proceed; only a genuinely clean
        # process reports the missing transaction authority below.
        block_if_ambiguous_remote_post()
        raise AmbiguousRemotePostOutcome(
            "X post creation requires exactly one prepared durable transaction "
            "receipt",
            service="x",
        )
    if on_remote_transaction_started is not None and (
        prepared_historical_context_reply_receipt is None
        or not callable(on_remote_transaction_started)
    ):
        raise AmbiguousRemotePostOutcome(
            "Only a historical-context transaction may publish its remote "
            "phase through this callback",
            service="x",
        )
    if prepared_conversational_reply_receipt is not None:
        prepared = prepared_conversational_reply_receipt
        if (
            not sending_reply_receipt_is_semantically_valid(prepared)
            or str(prepared.get("target_id") or "") != str(reply_to_id or "")
            or str(prepared.get("reply_text") or "") != str(text)
            or bool(media_ids)
        ):
            raise AmbiguousRemotePostOutcome(
                "Prepared conversational-reply receipt does not exactly bind the "
                "requested remote write",
                service="x",
            )
    if prepared_main_post_attempt is not None:
        expected_lifecycle = (
            "attempting"
            if prepared_transport_authority is not None
            else "sending"
        )
        if (
            not current_main_post_attempt_is_semantically_valid(
                prepared_main_post_attempt
            )
            or prepared_main_post_attempt.get("lifecycle_state")
            != expected_lifecycle
            or reply_to_id is not None
            or not media_ids
        ):
            raise AmbiguousRemotePostOutcome(
                "Prepared main-post attempt is invalid for the requested remote "
                "write",
                service="x",
            )
    if prepared_historical_context_reply_receipt is not None:
        prepared = prepared_historical_context_reply_receipt
        from historical_context_formatter import HistoricalContextReplyStore

        if (
            not HistoricalContextReplyStore._valid_sending_receipt(prepared)
            or str(prepared.get("parent_post_id") or "") != str(reply_to_id or "")
            or str(prepared.get("reply_text") or "") != str(text)
            or bool(media_ids)
            or made_with_ai
        ):
            raise AmbiguousRemotePostOutcome(
                "Prepared historical-context receipt does not exactly bind the "
                "requested remote write",
                service="x",
            )
    block_if_ambiguous_remote_post(
        prepared_conversational_reply_receipt=(
            prepared_conversational_reply_receipt
        ),
        prepared_historical_context_reply_receipt=(
            prepared_historical_context_reply_receipt
        ),
        prepared_main_post_attempt=prepared_main_post_attempt,
        prepared_transport_authority=prepared_transport_authority,
    )
    payload: dict = {}

    if text:
        payload["text"] = text

    if media_ids:
        payload["media"] = {
            "media_ids": [str(x) for x in media_ids],
        }

    if reply_to_id:
        payload["reply"] = {
            "in_reply_to_tweet_id": str(reply_to_id),
        }

    if made_with_ai:
        payload["made_with_ai"] = True

    if not payload.get("text") and not payload.get("media"):
        raise ValueError("Cannot create X post without text or media")
    try:
        payload = freeze_tweet_request(
            method="POST",
            request_path="/2/tweets",
            payload=payload,
        ).payload()
    except TransportJournalError as exc:
        raise AmbiguousRemotePostOutcome(
            "X post creation payload is not an authorised immutable request",
            service="x",
            request_method="POST",
            request_path="/2/tweets",
        ) from exc
    if (
        prepared_main_post_attempt is not None
        and not main_post_attempt_binds_payload(
            prepared_main_post_attempt,
            payload,
        )
    ):
        raise AmbiguousRemotePostOutcome(
            "Prepared main-post attempt does not exactly bind the requested "
            "remote payload",
            service="x",
        )

    # The final transport helper repeats the pause and instance-lock checks.
    # Main-image lanes may already have published a ``prepared`` transport pair
    # so confirmed media can be handed off before this call.  That exact pair
    # is still provably untransmitted and must be retired before a local pause
    # is allowed to escape; otherwise a temporary maintenance pause strands the
    # source receipt and journal for manual recovery.
    require_instance_lock_for_remote_write("X post creation")
    block_if_remote_write_safety_incident_latched()
    if global_remote_writes_paused():
        if prepared_transport_authority is not None:
            try:
                if prepared_transport_source is None:
                    raise TransportJournalError(
                        "prepared transport authority has no semantic source"
                    )
                abort_untransmitted_transport_transaction(
                    source_binding=prepared_transport_source,
                    authority=prepared_transport_authority,
                    mutation_authority=transaction_mutation_authority(
                        "initially paused transport journal abort"
                    ),
                )
            except Exception as abort_exc:
                record_ambiguous_remote_post(payload)
                raise AmbiguousRemotePostOutcome(
                    "An initially paused tweet left an unresolved transport "
                    "barrier",
                    service="x",
                    request_method="POST",
                    request_path="/2/tweets",
                ) from abort_exc
        raise RemoteOperationsPaused(
            "Global runtime control pause blocks operation: X post creation"
        )

    if prepared_main_post_attempt is not None and prepared_transport_authority is None:
        attempting = mark_main_post_attempt_attempting(
            prepared_main_post_attempt
        )
        prepared_main_post_attempt.clear()
        prepared_main_post_attempt.update(attempting)

    if prepared_conversational_reply_receipt is not None:
        transaction_receipt_path = CONFIRMED_REPLY_RECEIPT_FILE
        transaction_receipt = prepared_conversational_reply_receipt
        transaction_lane = "conversational_reply"
    elif prepared_historical_context_reply_receipt is not None:
        transaction_receipt_path = HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
        transaction_receipt = prepared_historical_context_reply_receipt
        transaction_lane = "historical_context_reply"
    else:
        assert prepared_main_post_attempt is not None
        transaction_receipt_path = main_post_attempt_path(
            prepared_main_post_attempt
        )
        transaction_receipt = prepared_main_post_attempt
        transaction_lane = str(prepared_main_post_attempt["lane"])

    try:
        if (prepared_transport_authority is None) != (
            prepared_transport_source is None
        ):
            raise TransportJournalError(
                "prepared transport authority and semantic source must be supplied together"
            )
        if prepared_transport_authority is None:
            transport_source = bind_lane_transport_source(
                receipt_path=transaction_receipt_path,
                receipt=transaction_receipt,
                lane=transaction_lane,
                payload=payload,
            )
            transport_authority = begin_transport_transaction(
                receipt_path=transaction_receipt_path,
                source_binding=transport_source,
            )
        else:
            transport_authority = prepared_transport_authority
            transport_source = prepared_transport_source
            assert transport_source is not None
            if (
                transport_authority.lifecycle_state != "prepared"
                or Path(transport_authority.journal_path)
                != journal_path_for_receipt(transaction_receipt_path).absolute()
                or transport_source.receipt_document != transaction_receipt
                or transport_source.request.payload() != payload
                or transport_source.lane != transaction_lane
            ):
                raise TransportJournalError(
                    "prepublished transport pair does not bind this transaction"
                )
        transport_authority = arm_transport_transaction(
            Path(transport_authority.journal_path),
            transport_authority,
            mutation_authority=transaction_mutation_authority(
                "transport journal arming"
            ),
        )
    except TransportJournalError as exc:
        record_ambiguous_remote_post(payload)
        raise AmbiguousRemotePostOutcome(
            "Could not establish the restart-persistent transport journal",
            service="x",
            request_method="POST",
            request_path="/2/tweets",
        ) from exc

    log.info(
        "Creating X post with durable transport journal. "
        "lane=%s transaction_id=%s reply_to_id=%s media_count=%d "
        "made_with_ai=%s text=%r",
        transaction_lane,
        transport_authority.transaction_id,
        reply_to_id,
        len(media_ids or []),
        made_with_ai,
        text,
    )

    def validate_created_post_response(result: dict) -> str:
        response_data = result.get("data") if isinstance(result, dict) else None
        post_id = response_data.get("id") if isinstance(response_data, dict) else None
        if not valid_post_id(post_id):
            raise AmbiguousRemotePostOutcome(
                f"X may have accepted the post but its response did not include a valid numeric data.id: {result}",
                service="x",
            )
        return str(post_id)

    try:
        if on_remote_transaction_started is not None:
            # The outbox phase is published only after the restart-persistent
            # transport pair is armed, and directly before the sole remote
            # request boundary.  If this durable callback fails, the armed
            # journal remains a global barrier and no request is attempted.
            on_remote_transaction_started()
        result = x_request(
            "POST",
            "/2/tweets",
            json=payload,
            ambiguous_write=True,
            _remote_write_authorization=transport_authority,
        )
        post_id = validate_created_post_response(result)
        confirm_transport_transaction(
            Path(transport_authority.journal_path),
            transport_authority,
            mutation_authority=transaction_mutation_authority(
                "transport journal confirmation"
            ),
            post_id=post_id,
            confirmation_epoch=confirmation_epoch_after_remote_success(
                transaction_receipt
            ),
        )
        log.info("Created X post successfully. response=%s", result)
        return result
    except ProvedRemotePostNonSuccess as remote_rejection:
        try:
            retire_consumed_transport_transaction_after_proved_remote_non_success(
                source_binding=transport_source,
                authority=transport_authority,
                remote_non_success_proof=(
                    remote_rejection.remote_non_success_proof
                ),
                mutation_authority=transaction_mutation_authority(
                    "proved remote non-success transport retirement"
                ),
            )
        except BaseException as retirement_error:
            # A rejected remote create is useful only if its exact consumed
            # transaction can also be retired without uncertainty.  Preserve
            # the source receipt and latch a durable global barrier whenever
            # either journal generation cannot be retired exactly.
            record_ambiguous_remote_post(payload)
            if not isinstance(retirement_error, Exception):
                raise
            raise AmbiguousRemotePostOutcome(
                "X rejected the reply create, but its consumed transport "
                "transaction could not be retired safely",
                service="x",
                request_method="POST",
                request_path="/2/tweets",
            ) from retirement_error
        raise
    except TransportJournalError as exc:
        record_ambiguous_remote_post(payload)
        raise AmbiguousRemotePostOutcome(
            "X returned a post identity but its durable transport journal "
            "could not be confirmed",
            service="x",
            request_method="POST",
            request_path="/2/tweets",
        ) from exc
    except RemoteOperationsPaused:
        try:
            abort_untransmitted_transport_transaction(
                source_binding=transport_source,
                authority=transport_authority,
                mutation_authority=transaction_mutation_authority(
                    "untransmitted transport journal abort"
                ),
            )
        except Exception as abort_exc:
            record_ambiguous_remote_post(payload)
            raise AmbiguousRemotePostOutcome(
                "A locally paused tweet left an unresolved transport barrier",
                service="x",
                request_method="POST",
                request_path="/2/tweets",
            ) from abort_exc
        raise
    except AmbiguousRemotePostOutcome:
        record_ambiguous_remote_post(payload)
        raise


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


def load_json_object(path: Path, *, label: str) -> dict | None:
    """Load JSON object."""
    return _asset_metadata.load_json_object(
        path,
        label=label,
        log=log,
    )


collapse_quote_whitespace = _asset_metadata.collapse_quote_whitespace


def quote_text_hash(text: str) -> str:
    """Return whether quote text hash."""
    return _asset_metadata.quote_text_hash(text, collapse_quote_whitespace=collapse_quote_whitespace)


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
        deep_merge_dict=deep_merge_dict,
    )


def apply_quote_analysis_overrides(raw_analysis: dict, overrides: dict | None) -> dict:
    """Apply quote analysis overrides."""
    return _asset_metadata.apply_quote_analysis_overrides(
        raw_analysis,
        overrides,
        deep_merge_dict=deep_merge_dict,
        log=log,
    )


def load_quote_analysis() -> dict | None:
    """Load validated quotation-analysis metadata and local overrides."""
    return _asset_metadata.load_quote_analysis(
        quote_analysis_file=QUOTE_ANALYSIS_FILE,
        quote_analysis_overrides_file=QUOTE_ANALYSIS_OVERRIDES_FILE,
        load_json_object=load_json_object,
        apply_quote_analysis_overrides=apply_quote_analysis_overrides,
        log=log,
    )


def load_image_analysis_file(path: Path, *, label: str) -> dict | None:
    """Load image analysis file."""
    return _asset_metadata.load_image_analysis_file(
        path,
        label=label,
        load_json_object=load_json_object,
        log=log,
    )


def merge_image_analysis(primary: dict, generated: dict | None) -> dict:
    """Merge image analysis."""
    return _asset_metadata.merge_image_analysis(
        primary,
        generated,
        log=log,
    )


def load_image_analysis() -> dict | None:
    """Load original and, when enabled, generated image metadata."""
    return _asset_metadata.load_image_analysis(
        image_analysis_file=IMAGE_ANALYSIS_FILE,
        pool_enabled=ENABLE_GENERATED_IMAGE_POOL,
        generated_image_analysis_file=GENERATED_IMAGE_ANALYSIS_FILE,
        load_image_analysis_file=load_image_analysis_file,
        merge_image_analysis=merge_image_analysis,
        log=log,
    )


mm_dd_in_window = _quote_candidates.mm_dd_in_window


def any_window_matches_today(windows: object, today_mm_dd: str) -> bool:
    """Return whether any window matches today."""
    return _quote_candidates.any_window_matches_today(
        windows,
        today_mm_dd,
        mm_dd_in_window=mm_dd_in_window,
    )


def quote_season_status(analysis: dict | None, *, today_mm_dd: str) -> dict:
    """Return the quote season status."""
    return _quote_candidates.quote_season_status(
        analysis,
        today_mm_dd=today_mm_dd,
        any_window_matches_today=any_window_matches_today,
    )


def quote_candidate_weight(analysis: dict | None, *, today_mm_dd: str) -> tuple[float, dict]:
    """Return the quote candidate weight."""
    return _quote_candidates.quote_candidate_weight(
        analysis,
        today_mm_dd=today_mm_dd,
        quote_season_status=quote_season_status,
        season_date_specific_weight=QUOTE_SEASON_DATE_SPECIFIC_WEIGHT,
        season_strong_weight=QUOTE_SEASON_STRONG_WEIGHT,
        season_soft_weight=QUOTE_SEASON_SOFT_WEIGHT,
        quality_weight_max_multiplier=QUOTE_QUALITY_WEIGHT_MAX_MULTIPLIER,
    )


weighted_random_choice = _quote_candidates.weighted_random_choice


def quote_metadata_for_hash(quote_analysis: dict | None, quote_hash: str, text: str = "") -> dict | None:
    """Return whether quote metadata for hash."""
    return _asset_metadata.quote_metadata_for_hash(
        quote_analysis,
        quote_hash,
        text,
        collapse_quote_whitespace=collapse_quote_whitespace,
        quote_text_hash=quote_text_hash,
        log=log,
    )


def current_quote_hashes_by_line(lines: list[str]) -> dict[int, str]:
    """Return whether current quote hashes by line."""
    return _quote_candidates.current_quote_hashes_by_line(
        lines,
        quote_text_hash=quote_text_hash,
    )


def quote_used_history_has_legacy_indices(value: set) -> bool:
    """Return whether quote used history has legacy indices."""
    return any(re.fullmatch(r"-?\d+", str(item)) for item in value)


def quote_source_matches_analysis(quote_analysis: dict | None, lines: list[str]) -> bool:
    """Return whether quote source matches analysis."""
    if not isinstance(quote_analysis, dict):
        return False
    source = quote_analysis.get("source", {}) if isinstance(quote_analysis.get("source"), dict) else {}
    expected = source.get("source_sha256")
    if not expected:
        return False
    current = hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()
    return str(expected) == current


def normalise_quote_used_hashes(raw_used: set, lines: list[str], quote_analysis: dict | None = None) -> tuple[set, bool]:
    """Return whether normalise quote used hashes."""
    hashes_by_line = current_quote_hashes_by_line(lines)
    normalised: set[str] = set()
    changed = False
    can_migrate_indices = quote_source_matches_analysis(quote_analysis, lines)

    for item in raw_used:
        item_text = str(item)
        if re.fullmatch(r"[0-9a-fA-F]{64}", item_text):
            normalised.add(item_text.lower())
            if item_text != item_text.lower():
                changed = True
            continue
        try:
            line_no = int(item)
        except Exception:
            log.warning("Dropping unrecognised quote used-history entry: %r", item)
            changed = True
            continue
        if not can_migrate_indices:
            normalised.add(item)
            continue
        if line_no in hashes_by_line:
            normalised.add(hashes_by_line[line_no])
        else:
            log.warning("Dropping out-of-range quote line used-history entry: %r", item)
        changed = True

    return normalised, changed or normalised != {str(item) for item in raw_used}


def load_quote_used_hashes(lines: list[str]) -> set[str]:
    """Return whether load quote used hashes."""
    raw = load_used_set(LINES_USED_FILE, legacy_pickle_path=PICKLE_FILE)
    quote_analysis = load_quote_analysis()
    normalised, changed = normalise_quote_used_hashes(raw, lines, quote_analysis)
    if quote_used_history_has_legacy_indices(normalised):
        log.critical(
            "Quote used-history contains legacy integer entries but current quote source does not match analysed source; refusing destructive migration"
        )
        return normalised
    if changed or LINES_USED_FILE.exists():
        save_used_set(LINES_USED_FILE, normalised)
        log.info("Quote used-history normalised to %d quote hash(es)", len(normalised))
    return normalised


def save_quote_used_hashes(path: Path, value: set[str], *, durable: bool = False) -> None:
    """Return whether save quote used hashes."""
    save_used_set(path, {str(item) for item in value}, durable=durable)


def validate_quote_analysis_against_lines(quote_analysis: dict, lines: list[str]) -> None:
    """Validate quote analysis against lines."""
    return _asset_metadata.validate_quote_analysis_against_lines(
        quote_analysis,
        lines,
        log=log,
    )


def current_image_paths() -> list[str]:
    """Return the current image paths."""
    return _asset_metadata.current_image_paths(
        image_glob=IMAGE_GLOB,
        pool_enabled=ENABLE_GENERATED_IMAGE_POOL,
        generated_image_dir=GENERATED_IMAGE_DIR,
        generated_image_glob=GENERATED_IMAGE_GLOB,
        glob=glob,
        configured_generated_image_paths=configured_generated_image_paths,
        log=log,
    )


def save_image_used_basenames(path: Path, value: set[str], *, durable: bool = False) -> None:
    """Save image used basenames."""
    atomic_write_json(
        path,
        sorted(str(item) for item in value),
        durable=durable,
    )


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
        fsync_parent_dir=fsync_parent_dir,
        json=json,
        os=os,
        tempfile=tempfile,
    )


def canonical_atomic_json_bytes(value: object) -> bytes:
    """Return the exact byte representation used by ``atomic_write_json``."""
    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


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
        json=json,
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
        canonical_atomic_json_bytes=canonical_atomic_json_bytes,
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
        canonical_atomic_json_bytes=canonical_atomic_json_bytes,
        fsync_parent_dir=fsync_parent_dir,
        os=os,
        stat=stat,
    )


def atomic_json_file_exactly_matches(path: Path, value: object) -> bool:
    """Compare a receipt with its expected canonical bytes without JSON parsing."""
    try:
        return path.read_bytes() == canonical_atomic_json_bytes(value)
    except Exception:
        return False


def valid_post_id(value: object) -> bool:
    """Return whether valid post ID."""
    return bool(re.fullmatch(r"\d{1,30}", str(value or "")))


def valid_string_post_id(value: object) -> bool:
    """Return whether a durable receipt stores an exact string post ID."""
    return type(value) is str and valid_post_id(value)


def valid_receipt_epoch(value: object) -> bool:
    """Return whether valid receipt epoch."""
    if type(value) is not int:
        return False
    epoch = value
    return MIN_CONFIRMATION_EPOCH <= epoch <= MAX_CONFIRMATION_EPOCH


def receipt_int(value: object, default: int | None = None) -> int | None:
    """Return the receipt int."""
    if value in (None, "") and default is not None:
        return default
    return value if type(value) is int else None


def receipt_bool(value: object) -> bool | None:
    """Return the receipt bool."""
    if isinstance(value, bool):
        return value
    return None


def safe_epoch_date_str(epoch: int) -> str | None:
    """Return the safe epoch date str."""
    try:
        return epoch_date_str(epoch)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def safe_reply_cap_date_str(epoch: int) -> str | None:
    """Return a safe Europe/London conversational daily-cap date."""
    try:
        return reply_cap_date_str(epoch)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def main_post_schedule_zone(timezone_name: object) -> ZoneInfo:
    """Return the sole calendar zone accepted by current main-post receipts."""

    if (
        type(timezone_name) is not str
        or timezone_name != MAIN_POST_SCHEDULE_TIMEZONE
    ):
        raise ValueError("unsupported main-post schedule timezone")
    try:
        return ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise RuntimeError(
            "the bound main-post schedule timezone is unavailable"
        ) from exc


def bound_schedule_datetime(epoch: int, timezone_name: object) -> datetime:
    """Interpret one durable epoch in its exact receipt-bound calendar zone."""

    if type(epoch) is not int:
        raise TypeError("bound schedule epoch must be an integer")
    return datetime.fromtimestamp(epoch, tz=main_post_schedule_zone(timezone_name))


def safe_bound_schedule_date_str(
    epoch: int,
    timezone_name: object,
) -> str | None:
    """Return a bound calendar date, or ``None`` for invalid receipt input."""

    try:
        return bound_schedule_datetime(epoch, timezone_name).strftime("%Y-%m-%d")
    except (TypeError, ValueError, RuntimeError, OverflowError, OSError):
        return None


def valid_receipt_basename(value: object) -> bool:
    """Return whether valid receipt basename."""
    if type(value) is not str:
        return False
    basename = value
    return bool(basename) and Path(basename).name == basename and basename not in {".", ".."}


def canonical_remote_post_payload_sha256(payload: dict) -> str:
    """Return the stable identity of one exact X create payload."""
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def main_post_attempt_payload(attempt: dict) -> dict:
    """Reconstruct the exact remote payload bound by a main-post attempt."""
    payload: dict = {}
    text = str(attempt.get("text") or "")
    media_ids = attempt.get("media_ids")
    reply_to_id = str(attempt.get("reply_to_id") or "")
    if text:
        payload["text"] = text
    if isinstance(media_ids, list) and media_ids:
        payload["media"] = {"media_ids": [str(value) for value in media_ids]}
    if reply_to_id:
        payload["reply"] = {"in_reply_to_tweet_id": reply_to_id}
    if attempt.get("made_with_ai") is True:
        payload["made_with_ai"] = True
    return payload


BOUND_MEME_SCHEDULE_STATE_KEYS = {
    "last_meme_post_epoch",
    "next_meme_post_epoch",
    "meme_schedule_version",
    "next_meme_schedule_mode",
    "next_meme_schedule_date",
    "meme_anchor_quote_post_epoch",
}

# Schema-v4/v5 recovery delays are data, not current configuration.  These
# immutable format bounds keep old receipts readable across configuration
# changes while rejecting corrupt plans that could suppress a lane for years.
MAIN_POST_ATTEMPT_MAX_BOUND_DELAY_SECONDS = 31 * 24 * 60 * 60


def bound_meme_schedule_state(
    state: dict,
    *,
    schedule_timezone: str | None = None,
) -> dict:
    """Capture the exact meme-schedule inputs bound before a regular X write."""
    next_epoch = int(state.get("next_meme_post_epoch", 0) or 0)
    next_mode = str(state.get("next_meme_schedule_mode", "") or "")
    next_date = str(state.get("next_meme_schedule_date", "") or "")
    schedule_version = int(state.get("meme_schedule_version", 0) or 0)
    if next_epoch:
        # Older state can predate the descriptive schedule fields.  Bind its
        # effective fallback interpretation explicitly rather than leaving
        # reconciliation dependent on later defaults.
        next_mode = next_mode or "fallback"
        derived_date = safe_bound_schedule_date_str(
            next_epoch,
            MAIN_POST_SCHEDULE_TIMEZONE
            if schedule_timezone is None
            else schedule_timezone,
        )
        if not next_date and derived_date is None:
            raise ValueError("meme schedule epoch has no valid calendar date")
        next_date = next_date or str(derived_date)
        schedule_version = schedule_version or MEME_SCHEDULE_VERSION
    return {
        "last_meme_post_epoch": int(state.get("last_meme_post_epoch", 0) or 0),
        "next_meme_post_epoch": next_epoch,
        "meme_schedule_version": schedule_version,
        "next_meme_schedule_mode": next_mode,
        "next_meme_schedule_date": next_date,
        "meme_anchor_quote_post_epoch": int(
            state.get("meme_anchor_quote_post_epoch", 0) or 0
        ),
    }


def bound_meme_schedule_state_is_valid(
    value: object,
    *,
    schedule_timezone: str | None = None,
) -> bool:
    """Return whether a pre-send meme-schedule snapshot is self-consistent."""
    if not isinstance(value, dict) or set(value) != BOUND_MEME_SCHEDULE_STATE_KEYS:
        return False
    integer_keys = {
        "last_meme_post_epoch",
        "next_meme_post_epoch",
        "meme_schedule_version",
        "meme_anchor_quote_post_epoch",
    }
    if any(type(value.get(key)) is not int or int(value[key]) < 0 for key in integer_keys):
        return False
    if int(value["meme_schedule_version"]) > MEME_SCHEDULE_VERSION:
        return False
    next_epoch = int(value["next_meme_post_epoch"])
    last_epoch = int(value["last_meme_post_epoch"])
    anchor_epoch = int(value["meme_anchor_quote_post_epoch"])
    if any(
        epoch and not valid_receipt_epoch(epoch)
        for epoch in (next_epoch, last_epoch, anchor_epoch)
    ):
        return False
    mode = value["next_meme_schedule_mode"]
    schedule_date = value["next_meme_schedule_date"]
    if type(mode) is not str or type(schedule_date) is not str:
        return False
    effective_timezone = (
        MAIN_POST_SCHEDULE_TIMEZONE
        if schedule_timezone is None
        else schedule_timezone
    )

    def date_for_epoch(epoch: int) -> str | None:
        return safe_bound_schedule_date_str(epoch, effective_timezone)
    if next_epoch:
        if (
            int(value["meme_schedule_version"]) < 1
            or mode not in MEME_SCHEDULE_MODES
            or not mode
        ):
            return False
        if mode == "after_first_quote_after_midday":
            if (
                anchor_epoch <= 0
                or next_epoch <= anchor_epoch
                or schedule_date != date_for_epoch(anchor_epoch)
            ):
                return False
        elif (
            anchor_epoch
            or schedule_date != date_for_epoch(next_epoch)
        ):
            return False
    elif mode or schedule_date or anchor_epoch:
        return False
    return True


ENGAGEMENT_EXPERIMENT_ATTEMPT_FIELDS = {
    "binding",
    "canonical_quote_text",
    "approved_question_body",
    "complete_treatment_sha256",
    "complete_treatment_weighted_length",
}


def engagement_experiment_attempt_envelope_is_valid(
    value: object,
    *,
    public_text: object,
    quote_hash: object,
    plan: dict | None = None,
) -> bool:
    """Validate the self-contained experiment authority bound before X."""

    if (
        not isinstance(value, dict)
        or set(value) != ENGAGEMENT_EXPERIMENT_ATTEMPT_FIELDS
        or type(public_text) is not str
        or type(quote_hash) is not str
    ):
        return False
    canonical_quote_text = value.get("canonical_quote_text")
    question_body = value.get("approved_question_body")
    treatment_sha256 = value.get("complete_treatment_sha256")
    treatment_length = value.get("complete_treatment_weighted_length")
    binding = value.get("binding")
    if (
        type(canonical_quote_text) is not str
        or not canonical_quote_text
        or type(question_body) is not str
        or type(treatment_sha256) is not str
        or re.fullmatch(r"[0-9a-f]{64}", treatment_sha256) is None
        or type(treatment_length) is not int
    ):
        return False
    try:
        engagement_question_trial.validate_attempt_binding(
            binding,
            plan=plan,
            exact_quote_text=canonical_quote_text,
            public_text=public_text,
        )
        treatment_text = engagement_question_trial.complete_treatment_text(
            canonical_quote_text,
            question_body,
        )
        if (
            engagement_question_trial.sha256_text(canonical_quote_text)
            != quote_hash
            or quote_text_hash(canonical_quote_text) != quote_hash
            or engagement_question_trial.sha256_text(question_body)
            != binding["approved_question_sha256"]
            or engagement_question_trial.sha256_text(treatment_text)
            != treatment_sha256
            or engagement_question_trial.x_weighted_length(treatment_text)
            != treatment_length
            or treatment_length > engagement_question_trial.MAX_ROOT_WEIGHTED_LENGTH
            or public_text
            != (
                treatment_text
                if binding["arm"] == "treatment"
                else canonical_quote_text
            )
        ):
            return False
    except (KeyError, TypeError, engagement_question_trial.ExperimentValidationError):
        return False
    return True


def engagement_experiment_envelope_from_attempt(
    attempt: object,
) -> dict | None:
    """Return one validated-looking envelope only for schema-v6 quote attempts."""

    if (
        isinstance(attempt, dict)
        and attempt.get("lane") == "quote_image"
        and attempt.get("schema_version") == 6
        and isinstance(attempt.get("engagement_question_experiment"), dict)
    ):
        return attempt["engagement_question_experiment"]
    return None


def main_post_attempt_is_semantically_valid(data: object) -> bool:
    """Return whether a pre-send regular or meme attempt is self-consistent."""
    if not isinstance(data, dict):
        return False
    lane = data.get("lane")
    if type(lane) is not str:
        return False
    supported_schemas = {
        "quote_image": {3, 4, 5, 6},
        "daily_meme": {2, 3, 4, 5},
    }.get(lane)
    schema_version = data.get("schema_version")
    if (
        supported_schemas is None
        or type(schema_version) is not int
        or schema_version not in supported_schemas
    ):
        return False
    if data.get("lifecycle_state") not in {"sending", "attempting"}:
        return False
    if (
        type(data.get("attempt_id")) is not str
        or re.fullmatch(r"[0-9a-f]{64}", data["attempt_id"]) is None
    ):
        return False
    attempt_epoch = receipt_int(data.get("attempt_epoch"))
    if attempt_epoch is None or not valid_receipt_epoch(attempt_epoch):
        return False
    revision = receipt_int(data.get("payload_revision"))
    if revision is None or revision < 1 or revision > 10:
        return False
    text = data.get("text")
    if not isinstance(text, str):
        return False
    if (
        type(data.get("text_sha256")) is not str
        or hashlib.sha256(text.encode("utf-8")).hexdigest()
        != data["text_sha256"]
    ):
        return False
    media_ids = data.get("media_ids")
    if (
        not isinstance(media_ids, list)
        or not media_ids
        or len(media_ids) > 4
        or any(not isinstance(value, str) or not value for value in media_ids)
        or len(set(media_ids)) != len(media_ids)
    ):
        return False
    if type(data.get("reply_to_id")) is not str or data["reply_to_id"]:
        return False
    if not isinstance(data.get("made_with_ai"), bool):
        return False
    selected = data.get("selected_identity")
    recovery_plan = data.get("recovery_plan")
    if not isinstance(selected, dict):
        return False
    if not isinstance(recovery_plan, dict):
        return False
    if lane == "quote_image":
        experiment_envelope = data.get("engagement_question_experiment")
        if (schema_version == 6) != (experiment_envelope is not None):
            return False
        if set(selected) != {
            "quote_hash",
            "line_no",
            "source_line_number",
            "image_basename",
            "image_no",
        }:
            return False
        quote_hash = selected.get("quote_hash")
        canonical_identity_text = (
            experiment_envelope.get("canonical_quote_text")
            if isinstance(experiment_envelope, dict)
            else text
        )
        if (
            type(quote_hash) is not str
            or re.fullmatch(r"[0-9a-f]{64}", quote_hash) is None
            or quote_text_hash(canonical_identity_text) != quote_hash
            or (
                schema_version == 6
                and not engagement_experiment_attempt_envelope_is_valid(
                    experiment_envelope,
                    public_text=text,
                    quote_hash=quote_hash,
                )
            )
            or not valid_receipt_basename(selected.get("image_basename"))
            or type(selected.get("line_no")) is not int
            or int(selected["line_no"]) < 0
            or type(selected.get("source_line_number")) is not int
            or selected.get("source_line_number") != int(selected["line_no"]) + 1
            or type(selected.get("image_no")) is not int
            or int(selected["image_no"]) < 0
        ):
            return False
        expected_recovery_keys = {
            "quote_delay_seconds",
            "meme_delay_seconds",
            "quote_history_after",
            "image_history_after",
        }
        if schema_version in {4, 5, 6}:
            expected_recovery_keys |= {
                "meme_scheduling_enabled",
                "meme_trigger_after_hour",
                "meme_schedule_version",
                "meme_schedule_before",
            }
        if schema_version in {5, 6}:
            expected_recovery_keys.add("schedule_timezone")
        if set(recovery_plan) != expected_recovery_keys:
            return False
        quote_delay = recovery_plan.get("quote_delay_seconds")
        meme_delay = recovery_plan.get("meme_delay_seconds")
        quote_history = recovery_plan.get("quote_history_after")
        image_history = recovery_plan.get("image_history_after")
        if (
            type(quote_delay) is not int
            or not 0 < quote_delay <= MAIN_POST_ATTEMPT_MAX_BOUND_DELAY_SECONDS
            or (
                meme_delay is not None
                and (
                    type(meme_delay) is not int
                    or not 0
                    < meme_delay
                    <= MAIN_POST_ATTEMPT_MAX_BOUND_DELAY_SECONDS
                )
            )
            or not isinstance(quote_history, list)
            or len(quote_history) > 10_000
            or len(set(quote_history)) != len(quote_history)
            or quote_history != sorted(quote_history)
            or quote_hash not in quote_history
            or any(
                not isinstance(value, str)
                or re.fullmatch(r"[0-9a-f]{64}", value) is None
                for value in quote_history
            )
            or not isinstance(image_history, list)
            or len(image_history) > 10_000
            or len(set(image_history)) != len(image_history)
            or image_history != sorted(image_history)
            or selected["image_basename"] not in image_history
            or any(
                not isinstance(value, str)
                or not valid_receipt_basename(value)
                for value in image_history
            )
        ):
            return False
        if schema_version in {4, 5, 6} and (
            type(recovery_plan.get("meme_scheduling_enabled")) is not bool
            or type(recovery_plan.get("meme_trigger_after_hour")) is not int
            or not 0 <= int(recovery_plan["meme_trigger_after_hour"]) <= 23
            or type(recovery_plan.get("meme_schedule_version")) is not int
            or not 1
            <= int(recovery_plan["meme_schedule_version"])
            <= MEME_SCHEDULE_VERSION
            or not bound_meme_schedule_state_is_valid(
                recovery_plan.get("meme_schedule_before"),
                schedule_timezone=(
                    recovery_plan.get("schedule_timezone")
                    if schema_version in {5, 6}
                    else None
                ),
            )
            or (
                schema_version in {5, 6}
                and (
                    type(recovery_plan.get("schedule_timezone")) is not str
                    or recovery_plan["schedule_timezone"]
                    != MAIN_POST_SCHEDULE_TIMEZONE
                    or safe_bound_schedule_date_str(
                        attempt_epoch,
                        recovery_plan.get("schedule_timezone"),
                    )
                    is None
                )
            )
            or (
                recovery_plan["meme_scheduling_enabled"]
                and meme_delay is None
            )
            or (
                not recovery_plan["meme_scheduling_enabled"]
                and meme_delay is not None
            )
        ):
            return False
    else:
        expected_meme_keys = {"next_schedule_mode"}
        if schema_version in {3, 4, 5}:
            expected_meme_keys |= {
                "meme_schedule_version",
                "fallback_hour",
                "fallback_minute",
            }
        if schema_version in {4, 5}:
            expected_meme_keys.add("image_summary")
        if schema_version == 5:
            expected_meme_keys.add("schedule_timezone")
        if (
            set(selected) != {"meme_basename"}
            or not valid_receipt_basename(selected.get("meme_basename"))
            or set(recovery_plan) != expected_meme_keys
            or recovery_plan.get("next_schedule_mode") != "fallback"
            or (
                schema_version in {3, 4, 5}
                and (
                    type(recovery_plan.get("meme_schedule_version")) is not int
                    or not 1
                    <= int(recovery_plan["meme_schedule_version"])
                    <= MEME_SCHEDULE_VERSION
                    or type(recovery_plan.get("fallback_hour")) is not int
                    or not 0 <= int(recovery_plan["fallback_hour"]) <= 23
                    or type(recovery_plan.get("fallback_minute")) is not int
                    or not 0 <= int(recovery_plan["fallback_minute"]) <= 59
                    or (
                        schema_version in {4, 5}
                        and (
                            not isinstance(recovery_plan.get("image_summary"), str)
                            or len(recovery_plan["image_summary"]) > 16_000
                        )
                    )
                    or (
                        schema_version == 5
                        and (
                            type(recovery_plan.get("schedule_timezone")) is not str
                            or recovery_plan["schedule_timezone"]
                            != MAIN_POST_SCHEDULE_TIMEZONE
                            or safe_bound_schedule_date_str(
                                attempt_epoch,
                                recovery_plan.get("schedule_timezone"),
                            )
                            is None
                        )
                    )
                )
            )
        ):
            return False
    payload = main_post_attempt_payload(data)
    return (
        bool(payload.get("text") or payload.get("media"))
        and type(data.get("payload_sha256")) is str
        and canonical_remote_post_payload_sha256(payload)
        == data["payload_sha256"]
    )


def main_post_attempt_binds_payload(attempt: dict, payload: dict) -> bool:
    """Return whether an attempt authorises exactly one remote payload."""
    return bool(
        current_main_post_attempt_is_semantically_valid(attempt)
        and main_post_attempt_payload(attempt) == payload
        and type(attempt.get("payload_sha256")) is str
        and canonical_remote_post_payload_sha256(payload)
        == attempt["payload_sha256"]
    )


def current_main_post_attempt_is_semantically_valid(data: object) -> bool:
    """Return whether an attempt belongs to the current writable generation."""

    return bool(
        main_post_attempt_is_semantically_valid(data)
        and isinstance(data, dict)
        and data.get("schema_version") in {5, 6}
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
    engagement_experiment: dict | None = None,
) -> dict:
    """Build a durable pre-send identity for one main-post transaction."""
    if lane not in {"quote_image", "daily_meme"}:
        raise ValueError(f"Unsupported main-post lane: {lane}")
    payload: dict = {}
    if text:
        payload["text"] = str(text)
    payload["media"] = {"media_ids": [str(value) for value in media_ids]}
    if made_with_ai:
        payload["made_with_ai"] = True
    if (
        type(recovery_plan.get("schedule_timezone")) is not str
        or recovery_plan["schedule_timezone"] != MAIN_POST_SCHEDULE_TIMEZONE
    ):
        raise ValueError(
            "new main-post attempts must bind the production schedule timezone"
        )
    if engagement_experiment is not None and lane != "quote_image":
        raise ValueError("experiment metadata is valid only for quote/image posts")
    attempt = {
        "schema_version": 6 if engagement_experiment is not None else 5,
        "lifecycle_state": "sending",
        "lane": lane,
        "attempt_id": hashlib.sha256(os.urandom(32)).hexdigest(),
        "attempt_epoch": now_epoch() if attempt_epoch is None else int(attempt_epoch),
        "payload_revision": 1,
        "payload_sha256": canonical_remote_post_payload_sha256(payload),
        "text": str(text),
        "text_sha256": hashlib.sha256(str(text).encode("utf-8")).hexdigest(),
        "media_ids": [str(value) for value in media_ids],
        "reply_to_id": "",
        "made_with_ai": bool(made_with_ai),
        "selected_identity": copy.deepcopy(selected_identity),
        "recovery_plan": copy.deepcopy(recovery_plan),
    }
    if engagement_experiment is not None:
        attempt["engagement_question_experiment"] = copy.deepcopy(
            engagement_experiment
        )
    if not current_main_post_attempt_is_semantically_valid(attempt):
        raise RuntimeError("Internal error: generated main-post attempt is invalid")
    return attempt


def main_post_attempt_path(attempt: dict) -> Path:
    """Return the receipt path which owns one main-post attempt."""
    lane = str(attempt.get("lane") or "")
    if lane == "quote_image":
        return REGULAR_POST_RECEIPT_FILE
    if lane == "daily_meme":
        return MEME_POST_RECEIPT_FILE
    raise ValueError(f"Unsupported main-post attempt lane: {lane}")


def write_main_post_attempt(attempt: dict) -> None:
    """Durably record a main-post transaction before its X create request."""
    if (
        not current_main_post_attempt_is_semantically_valid(attempt)
        or attempt.get("lifecycle_state") != "sending"
    ):
        raise RuntimeError(
            "Only a current-schema sending main-post attempt may enter the "
            "live write path"
        )
    if remote_receipt_retirement_is_blocking():
        raise UnresolvedRegularPostReceipt(
            "Refusing a main-post attempt while source-receipt retirement is incomplete"
        )
    if receipt_namespace_entry_exists(
        REGULAR_POST_RECEIPT_FILE
    ) or receipt_namespace_entry_exists(MEME_POST_RECEIPT_FILE):
        raise UnresolvedRegularPostReceipt(
            "Refusing to overwrite an unresolved regular or meme transaction"
        )
    if receipt_namespace_entry_exists(CONFIRMED_REPLY_RECEIPT_FILE):
        raise InvalidConfirmedReplyReceipt(
            "Refusing a main-post attempt while a conversational-reply receipt exists"
        )
    path = main_post_attempt_path(attempt)
    try:
        durable_create_receipt_json(path, attempt)
    except FileExistsError as exc:
        raise UnresolvedRegularPostReceipt(
            "Refusing to overwrite a receipt namespace entry which appeared "
            f"during main-post publication: {path}"
        ) from exc
    log.warning(
        "Wrote main-post sending receipt lane=%s attempt_id=%s path=%s",
        attempt["lane"],
        attempt["attempt_id"],
        path,
    )


def prepare_main_tweet_transport(
    attempt: dict,
) -> tuple[dict, SourceReceiptBinding, TransportAuthority]:
    """Publish a prepared tweet owner before retiring confirmed media state."""

    if (
        not current_main_post_attempt_is_semantically_valid(attempt)
        or attempt.get("lifecycle_state") != "sending"
    ):
        raise TransportJournalError(
            "only a current-schema sending main-post attempt may prepare transport"
        )
    attempting = mark_main_post_attempt_attempting(attempt)
    if (
        attempting.get("lifecycle_state") != "attempting"
        or not current_main_post_attempt_is_semantically_valid(attempting)
    ):
        raise TransportJournalError("main post attempt is not transport-ready")
    path = main_post_attempt_path(attempting)
    payload = main_post_attempt_payload(attempting)
    source = bind_lane_transport_source(
        receipt_path=path,
        receipt=attempting,
        lane=str(attempting["lane"]),
        payload=payload,
    )
    authority = begin_transport_transaction(
        receipt_path=path,
        source_binding=source,
    )
    attempt.clear()
    attempt.update(attempting)
    return attempt, source, authority


def confirmed_media_upload_experiment_envelope(
    confirmation: ConfirmedMediaUpload,
) -> dict | None:
    """Return trial authority from the exact confirmed media generation."""

    if not isinstance(confirmation, ConfirmedMediaUpload):
        raise MediaUploadReceiptError("confirmed media identity is invalid")
    snapshot = inspect_media_upload_receipt(Path(confirmation.receipt_path))
    if snapshot is None or (
        snapshot.device != confirmation.receipt_device
        or snapshot.inode != confirmation.receipt_inode
        or snapshot.ctime_ns != confirmation.receipt_ctime_ns
        or snapshot.sha256 != confirmation.receipt_sha256
        or snapshot.document.get("transaction_id")
        != confirmation.transaction_id
        or snapshot.document.get("lifecycle_state") != "confirmed"
        or snapshot.document.get("remote_media_id") != confirmation.media_id
    ):
        raise MediaUploadReceiptError(
            "confirmed media receipt changed before main-post handoff"
        )
    metadata = snapshot.document.get("payload_metadata")
    form = metadata.get("form") if isinstance(metadata, dict) else None
    if not isinstance(form, dict):
        raise MediaUploadReceiptError("confirmed media receipt form is invalid")
    try:
        validated = validate_media_upload_payload_metadata(metadata, form=form)
    except (TypeError, ValueError) as exc:
        raise MediaUploadReceiptError(
            "confirmed media receipt payload authority is invalid"
        ) from exc
    envelope = validated.get("engagement_question_experiment")
    return copy.deepcopy(envelope) if isinstance(envelope, dict) else None


def handoff_confirmed_media_upload_to_main_attempt(
    attempt: dict,
    transport_authority: TransportAuthority,
) -> None:
    """Retire media state only beneath an independent prepared tweet pair."""

    confirmation = load_confirmed_media_upload(MEDIA_UPLOAD_RECEIPT_FILE)
    if confirmation is None:
        raise MediaUploadReceiptError(
            "main-post attempt has no confirmed media-upload receipt"
        )
    media_experiment = confirmed_media_upload_experiment_envelope(confirmation)
    attempt_experiment = engagement_experiment_envelope_from_attempt(attempt)
    if media_experiment != attempt_experiment:
        raise MediaUploadReceiptError(
            "confirmed media experiment authority does not match main-post attempt"
        )
    path = main_post_attempt_path(attempt)
    handoff = bind_media_handoff_to_transport(
        MEDIA_UPLOAD_RECEIPT_FILE,
        confirmation,
        transport_journal_path=Path(transport_authority.journal_path),
        transport_fence_path=Path(transport_authority.fence_path),
        source_receipt_path=path,
    )
    retire_confirmed_media_upload(
        MEDIA_UPLOAD_RECEIPT_FILE,
        handoff,
        mutation_authority=transaction_mutation_authority(
            "confirmed media upload retirement"
        ),
    )
    log.warning(
        "Handed confirmed media upload to durable main-post attempt "
        "lane=%s attempt_id=%s media_id=%s",
        attempt["lane"],
        attempt["attempt_id"],
        confirmation.media_id,
    )


def mark_main_post_attempt_attempting(attempt: dict) -> dict:
    """Atomically consume one sending authorisation before remote transmission."""
    if (
        not current_main_post_attempt_is_semantically_valid(attempt)
        or attempt.get("lifecycle_state") != "sending"
    ):
        raise AmbiguousRemotePostOutcome(
            "Only a current-schema main-post attempt may transmit once from "
            "sending state",
            service="x",
        )
    path = main_post_attempt_path(attempt)
    status, current = (
        load_regular_post_receipt()
        if path == REGULAR_POST_RECEIPT_FILE
        else load_meme_post_receipt()
    )
    if status != "sending" or current != attempt:
        raise AmbiguousRemotePostOutcome(
            "Main-post sending receipt changed before transmission",
            service="x",
        )
    attempting = {**attempt, "lifecycle_state": "attempting"}
    if not current_main_post_attempt_is_semantically_valid(attempting):
        raise RuntimeError("Attempting main-post receipt failed validation")
    replace_exact_source_receipt_document(
        path,
        expected_bytes=canonical_atomic_json_bytes(attempt),
        replacement_bytes=canonical_atomic_json_bytes(attempting),
        mutation_authority=transaction_mutation_authority(
            "main-post sending-to-attempting receipt promotion"
        ),
    )
    log.warning(
        "Promoted main-post receipt to attempting lane=%s attempt_id=%s path=%s",
        attempting["lane"],
        attempting["attempt_id"],
        path,
    )
    return attempting


def remove_main_post_attempt(
    attempt: dict,
    *,
    sending_disposition: str,
) -> None:
    """Retire an exact sending attempt after one proved-safe disposition."""
    if sending_disposition not in {
        "definite_non_success",
        "confirmed_state_fallback",
    }:
        raise ValueError("A main-post sending receipt requires an explicit disposition")
    if not current_main_post_attempt_is_semantically_valid(attempt):
        raise AmbiguousRemotePostOutcome(
            "Refusing to mutate a legacy or invalid main-post attempt",
            service="x",
        )
    path = main_post_attempt_path(attempt)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            current = json.load(handle)
        if (
            current != attempt
            or not current_main_post_attempt_is_semantically_valid(current)
        ):
            raise AmbiguousRemotePostOutcome(
                "Refusing to remove a changed main-post sending receipt",
                service="x",
            )
        retire_current_source_receipt(
            path,
            canonical_atomic_json_bytes(attempt),
        )
        log.info(
            "Removed main-post sending receipt disposition=%s "
            "lane=%s attempt_id=%s path=%s",
            sending_disposition,
            attempt["lane"],
            attempt["attempt_id"],
            path,
        )
    except FileNotFoundError as exc:
        raise AmbiguousRemotePostOutcome(
            "Main-post sending receipt disappeared before definite-failure retirement",
            service="x",
        ) from exc


def confirmed_receipt_matches_main_attempt(receipt: dict, attempt: dict) -> bool:
    """Return whether a confirmed receipt atomically promotes one attempt."""
    if (
        not main_post_attempt_is_semantically_valid(attempt)
        or str(receipt.get("attempt_id") or "") != str(attempt["attempt_id"])
        or str(receipt.get("attempt_payload_sha256") or "")
        != str(attempt["payload_sha256"])
    ):
        return False
    selected = attempt["selected_identity"]
    if attempt["lane"] == "quote_image":
        return bool(
            type(receipt.get("line_no")) is int
            and type(receipt.get("source_line_number")) is int
            and type(receipt.get("image_no")) is int
            and str(receipt.get("quote_hash") or "") == selected["quote_hash"]
            and receipt.get("line_no") == selected["line_no"]
            and receipt.get("source_line_number")
            == selected["source_line_number"]
            and str(receipt.get("image_basename") or "")
            == selected["image_basename"]
            and receipt.get("image_no") == selected["image_no"]
            and str(receipt.get("text") or "") == str(attempt["text"])
            and receipt.get("quote_history_after")
            == attempt["recovery_plan"]["quote_history_after"]
            and receipt.get("image_history_after")
            == attempt["recovery_plan"]["image_history_after"]
        )
    return bool(
        str(receipt.get("meme_basename") or "") == selected["meme_basename"]
        and str(receipt.get("text") or "") == str(attempt["text"])
    )


def confirmed_pending_schedule_receipt_is_semantically_valid(
    data: object,
    *,
    expected_lane: str | None = None,
) -> bool:
    """Validate a remote-confirmed receipt awaiting local schedule materialisation."""
    if not isinstance(data, dict) or set(data) != {
        "schema_version",
        "receipt_type",
        "post_id",
        "confirmation_epoch",
        "source_attempt",
        "image_summary",
    }:
        return False
    if (
        type(data.get("schema_version")) is not int
        or data.get("schema_version") != 1
        or data.get("receipt_type") != "confirmed_pending_schedule"
        or not valid_string_post_id(data.get("post_id"))
        or not isinstance(data.get("image_summary"), str)
    ):
        return False
    confirmation_epoch = receipt_int(data.get("confirmation_epoch"))
    if confirmation_epoch is None or not valid_receipt_epoch(confirmation_epoch):
        return False
    attempt = data.get("source_attempt")
    if (
        not main_post_attempt_is_semantically_valid(attempt)
        or attempt.get("lifecycle_state") != "attempting"
        # Older attempts remain readable as conservative restart barriers,
        # but their bytes did not bind a calendar zone.  They therefore cannot
        # authorise post-confirmation schedule materialisation.
        or attempt.get("schema_version") not in {5, 6}
        or confirmation_epoch < int(attempt["attempt_epoch"])
    ):
        return False
    lane = str(attempt.get("lane") or "")
    if expected_lane is not None and lane != expected_lane:
        return False
    if lane == "quote_image" and data["image_summary"]:
        return False
    if (
        lane == "daily_meme"
        and data["image_summary"] != attempt["recovery_plan"]["image_summary"]
    ):
        return False
    return lane in {"quote_image", "daily_meme"}


def build_confirmed_pending_schedule_receipt(
    attempt: dict,
    *,
    post_id: str,
    confirmation_epoch: int,
    image_summary: str = "",
) -> dict:
    """Build a versioned confirmed receipt without deriving local schedules."""
    if (
        not main_post_attempt_is_semantically_valid(attempt)
        or attempt.get("lifecycle_state") != "attempting"
        or not valid_post_id(post_id)
        or not valid_receipt_epoch(int(confirmation_epoch))
        or int(confirmation_epoch) < int(attempt["attempt_epoch"])
    ):
        raise RuntimeError(
            "Refusing an invalid main-post attempt or confirmation"
        )
    pending = {
        "schema_version": 1,
        "receipt_type": "confirmed_pending_schedule",
        "post_id": str(post_id),
        "confirmation_epoch": int(confirmation_epoch),
        "source_attempt": copy.deepcopy(attempt),
        "image_summary": str(image_summary),
    }
    if not confirmed_pending_schedule_receipt_is_semantically_valid(
        pending,
        expected_lane=str(attempt["lane"]),
    ):
        raise RuntimeError(
            "Internal error: confirmed pending-schedule receipt is invalid"
        )
    return pending


def confirmation_epoch_for_main_attempt(attempt: dict, observed_epoch: int) -> int:
    """Return a confirmation epoch which cannot precede its durable attempt."""
    attempt_epoch = int(attempt["attempt_epoch"])
    observed_epoch = int(observed_epoch)
    if observed_epoch < attempt_epoch:
        log.warning(
            "Wall clock moved backwards after X confirmation; clamping "
            "confirmation epoch lane=%s attempt_id=%s observed=%s attempt=%s",
            attempt.get("lane"),
            attempt.get("attempt_id"),
            observed_epoch,
            attempt_epoch,
        )
        return attempt_epoch
    return observed_epoch


def promote_main_post_attempt_to_confirmed_pending_schedule(
    attempt: dict,
    *,
    post_id: str,
    confirmation_epoch: int,
    image_summary: str = "",
) -> dict:
    """Atomically bind a confirmed remote identity before fallible local work."""
    pending = build_confirmed_pending_schedule_receipt(
        attempt,
        post_id=post_id,
        confirmation_epoch=confirmation_epoch,
        image_summary=image_summary,
    )
    path = main_post_attempt_path(attempt)
    status, current = (
        load_regular_post_receipt()
        if path == REGULAR_POST_RECEIPT_FILE
        else load_meme_post_receipt()
    )
    if status != "sending" or current != attempt:
        raise AmbiguousRemotePostOutcome(
            "Main-post attempt changed before remote-confirmation promotion",
            service="x",
        )
    recovery = bind_confirmed_transport_source(
        journal_path=journal_path_for_receipt(path),
        receipt_path=path,
        validator_id=TRANSPORT_SOURCE_VALIDATOR_ID,
        validator=transport_source_semantic_validator,
    )
    if (
        recovery.details.lane != str(attempt["lane"])
        or recovery.details.post_id != str(post_id)
        or recovery.details.confirmation_epoch != int(confirmation_epoch)
        or recovery.source_binding.receipt_document != attempt
        or recovery.source_binding.receipt_bytes
        != canonical_atomic_json_bytes(attempt)
    ):
        raise TransportJournalError(
            "confirmed main-post transport/source lineage changed"
        )
    try:
        replace_bound_source_receipt(
            recovery.source_binding,
            canonical_atomic_json_bytes(pending),
            mutation_authority=transaction_mutation_authority(
                "confirmed main-post source receipt promotion"
            ),
        )
    except BaseException as write_error:
        # ``atomic_write_json`` replaces the receipt before synchronising its
        # parent directory.  A failure at that final boundary can therefore
        # leave the exact pending receipt visible even though the writer did
        # not return.  Latch first: every inspection and recovery operation
        # below is fallible, and no unrelated remote lane may proceed while
        # durability is uncertain.
        global _AMBIGUOUS_REMOTE_POST_SEEN
        latch_was_already_set = remote_write_safety_incident_is_latched()
        _AMBIGUOUS_REMOTE_POST_SEEN = True

        if isinstance(write_error, BoundSourceReceiptTransitionError):
            raise ConfirmedPendingScheduleDurabilityUncertain(
                "Confirmed main-post source receipt changed or its exact "
                "promotion did not complete; the transport journal remains "
                "a durable global barrier",
                durable_barrier=True,
            ) from write_error

        if atomic_json_file_exactly_matches(path, pending):
            try:
                fsync_parent_dir(path, strict=True)
                if not atomic_json_file_exactly_matches(path, pending):
                    raise RuntimeError(
                        "Pending-schedule receipt changed during durability recheck"
                    )
            except BaseException as durability_error:
                durable_barrier = latch_confirmed_post_persistence_failure(
                    lane=str(attempt["lane"]),
                    post_id=str(post_id),
                    failure_components=[
                        "pending_schedule_parent_fsync",
                        type(durability_error).__name__,
                    ],
                )
                raise ConfirmedPendingScheduleDurabilityUncertain(
                    "Confirmed main-post pending-schedule receipt is visible but "
                    "its parent-directory durability could not be re-established",
                    durable_barrier=durable_barrier,
                ) from write_error

            if not latch_was_already_set:
                _AMBIGUOUS_REMOTE_POST_SEEN = False
            log.warning(
                "Re-established confirmed pending-schedule receipt durability "
                "after its initial parent-directory fsync failed lane=%s "
                "attempt_id=%s post_id=%s path=%s",
                attempt["lane"],
                attempt["attempt_id"],
                post_id,
                path,
            )
        elif atomic_json_file_exactly_matches(path, attempt):
            # The replace did not occur.  The previously durable sending
            # attempt remains the restart-safe barrier, so the caller's
            # existing confirmed-state fallback may proceed.
            if not latch_was_already_set:
                _AMBIGUOUS_REMOTE_POST_SEEN = False
            raise
        else:
            durable_barrier = latch_confirmed_post_persistence_failure(
                lane=str(attempt["lane"]),
                post_id=str(post_id),
                failure_components=[
                    "pending_schedule_receipt_identity",
                    type(write_error).__name__,
                ],
            )
            raise ConfirmedPendingScheduleDurabilityUncertain(
                "Confirmed main-post receipt identity changed or could not be "
                "verified after pending-schedule promotion failed",
                durable_barrier=durable_barrier,
            ) from write_error
    log.warning(
        "Promoted main-post attempt to confirmed pending-schedule receipt "
        "lane=%s attempt_id=%s post_id=%s path=%s",
        attempt["lane"],
        attempt["attempt_id"],
        post_id,
        path,
    )
    return pending


def materialize_bound_regular_schedule_receipt(
    pending: dict,
    *,
    _validate_result: bool = True,
) -> dict:
    """Build a full regular receipt solely from its durable bound plan."""
    if not confirmed_pending_schedule_receipt_is_semantically_valid(
        pending,
        expected_lane="quote_image",
    ):
        raise InvalidRegularPostReceipt(
            "Invalid confirmed regular pending-schedule receipt"
        )
    attempt = pending["source_attempt"]
    selected = attempt["selected_identity"]
    plan = attempt["recovery_plan"]
    quote_post_epoch = int(pending["confirmation_epoch"])
    schedule_timezone = str(plan["schedule_timezone"])
    next_quote_post_epoch = quote_post_epoch + int(plan["quote_delay_seconds"])
    snapshot = copy.deepcopy(plan["meme_schedule_before"])
    next_meme_post_epoch = int(snapshot["next_meme_post_epoch"])
    next_meme_schedule_mode = str(snapshot["next_meme_schedule_mode"])
    next_meme_schedule_date = str(snapshot["next_meme_schedule_date"])
    meme_anchor_quote_post_epoch = int(snapshot["meme_anchor_quote_post_epoch"])
    meme_schedule_version = int(snapshot["meme_schedule_version"])
    meme_schedule_changed_by_quote = False

    if bool(plan["meme_scheduling_enabled"]):
        quote_dt = bound_schedule_datetime(
            quote_post_epoch,
            schedule_timezone,
        )
        quote_date = quote_dt.strftime("%Y-%m-%d")
        last_meme_epoch = int(snapshot["last_meme_post_epoch"])
        meme_already_posted = bool(
            last_meme_epoch
            and safe_bound_schedule_date_str(
                last_meme_epoch,
                schedule_timezone,
            )
            == quote_date
        )
        already_anchored = bool(
            next_meme_post_epoch
            and next_meme_schedule_date == quote_date
            and next_meme_schedule_mode == "after_first_quote_after_midday"
        )
        if (
            quote_dt.hour >= int(plan["meme_trigger_after_hour"])
            and not meme_already_posted
            and not already_anchored
        ):
            next_meme_post_epoch = (
                quote_post_epoch + int(plan["meme_delay_seconds"])
            )
            next_meme_schedule_mode = "after_first_quote_after_midday"
            next_meme_schedule_date = quote_date
            meme_anchor_quote_post_epoch = quote_post_epoch
            meme_schedule_version = int(plan["meme_schedule_version"])
            meme_schedule_changed_by_quote = True

    experiment_envelope = engagement_experiment_envelope_from_attempt(attempt)
    receipt = {
        "schema_version": 4 if experiment_envelope is not None else 3,
        "post_id": str(pending["post_id"]),
        "quote_hash": str(selected["quote_hash"]),
        "line_no": int(selected["line_no"]),
        "source_line_number": int(selected["source_line_number"]),
        "text": str(attempt["text"]),
        "image_basename": str(selected["image_basename"]),
        "image_no": int(selected["image_no"]),
        "quote_post_epoch": quote_post_epoch,
        "next_quote_post_epoch": next_quote_post_epoch,
        "next_meme_post_epoch": next_meme_post_epoch,
        "meme_schedule_version": meme_schedule_version,
        "next_meme_schedule_mode": next_meme_schedule_mode,
        "next_meme_schedule_date": next_meme_schedule_date,
        "meme_anchor_quote_post_epoch": meme_anchor_quote_post_epoch,
        "meme_schedule_changed_by_quote": meme_schedule_changed_by_quote,
        "quote_history_after": list(plan["quote_history_after"]),
        "image_history_after": list(plan["image_history_after"]),
        "attempt_id": str(attempt["attempt_id"]),
        "attempt_payload_sha256": str(attempt["payload_sha256"]),
        # Preserve the complete pre-transport authority.  The nested recovery
        # plan is the only authoritative input for every derived schedule
        # field, and its exact canonical bytes are independently anchored by
        # the transport journal until reconciliation completes.
        "source_attempt": copy.deepcopy(attempt),
        "source_attempt_sha256": hashlib.sha256(
            canonical_atomic_json_bytes(attempt)
        ).hexdigest(),
    }
    if experiment_envelope is not None:
        receipt["quote_text"] = str(
            experiment_envelope["canonical_quote_text"]
        )
        receipt["engagement_question_experiment"] = copy.deepcopy(
            experiment_envelope
        )
    if _validate_result and not regular_post_receipt_is_semantically_valid(receipt):
        raise InvalidRegularPostReceipt(
            "Bound regular schedule produced an invalid confirmed receipt"
        )
    return receipt


def materialize_bound_meme_schedule_receipt(
    pending: dict,
    *,
    _validate_result: bool = True,
) -> dict:
    """Build a full meme receipt solely from its durable bound plan."""
    if not confirmed_pending_schedule_receipt_is_semantically_valid(
        pending,
        expected_lane="daily_meme",
    ):
        raise InvalidMemePostReceipt(
            "Invalid confirmed meme pending-schedule receipt"
        )
    attempt = pending["source_attempt"]
    selected = attempt["selected_identity"]
    plan = attempt["recovery_plan"]
    meme_post_epoch = int(pending["confirmation_epoch"])
    schedule_timezone = str(plan["schedule_timezone"])
    confirmation_dt = bound_schedule_datetime(
        meme_post_epoch,
        schedule_timezone,
    )
    next_dt = (confirmation_dt + timedelta(days=1)).replace(
        hour=int(plan["fallback_hour"]),
        minute=int(plan["fallback_minute"]),
        second=0,
        microsecond=0,
    )
    next_meme_post_epoch = int(next_dt.timestamp())
    receipt = {
        "schema_version": 2,
        "post_id": str(pending["post_id"]),
        "meme_basename": str(selected["meme_basename"]),
        "meme_post_epoch": meme_post_epoch,
        "next_meme_post_epoch": next_meme_post_epoch,
        "next_meme_schedule_date": next_dt.strftime("%Y-%m-%d"),
        "meme_schedule_version": int(plan["meme_schedule_version"]),
        "next_meme_schedule_mode": str(plan["next_schedule_mode"]),
        "text": str(attempt["text"]),
        "image_summary": str(pending["image_summary"]),
        "attempt_id": str(attempt["attempt_id"]),
        "attempt_payload_sha256": str(attempt["payload_sha256"]),
        "source_attempt": copy.deepcopy(attempt),
        "source_attempt_sha256": hashlib.sha256(
            canonical_atomic_json_bytes(attempt)
        ).hexdigest(),
    }
    if _validate_result and not meme_post_receipt_is_semantically_valid(receipt):
        raise InvalidMemePostReceipt(
            "Bound meme schedule produced an invalid confirmed receipt"
        )
    return receipt


def finalize_confirmed_pending_schedule_receipt(
    pending: dict,
) -> dict:
    """Atomically replace one pending schedule with its complete local receipt."""
    if not confirmed_pending_schedule_receipt_is_semantically_valid(pending):
        raise RuntimeError("Refusing to finalise an invalid pending receipt")
    attempt = pending["source_attempt"]
    path = main_post_attempt_path(attempt)
    status, current = (
        load_regular_post_receipt()
        if path == REGULAR_POST_RECEIPT_FILE
        else load_meme_post_receipt()
    )
    if status != "pending_schedule" or current != pending:
        raise RuntimeError(
            "Confirmed pending-schedule receipt changed before finalisation"
        )
    if attempt["lane"] == "quote_image":
        receipt = materialize_bound_regular_schedule_receipt(pending)
    else:
        receipt = materialize_bound_meme_schedule_receipt(pending)
    if attempt["lane"] == "quote_image":
        write_regular_post_receipt(receipt)
    else:
        write_meme_post_receipt(receipt)
    log.warning(
        "Finalised confirmed pending-schedule receipt lane=%s post_id=%s path=%s",
        attempt["lane"],
        pending["post_id"],
        path,
    )
    return receipt


def write_regular_post_receipt(receipt: dict) -> None:
    """Write regular post receipt."""
    if remote_receipt_retirement_is_blocking():
        raise UnresolvedRegularPostReceipt(
            "Refusing regular receipt publication during source-receipt retirement"
        )
    if receipt_namespace_entry_exists(MEME_POST_RECEIPT_FILE):
        raise UnresolvedMemePostReceipt(f"Refusing regular post while unresolved meme-post receipt exists: {MEME_POST_RECEIPT_FILE}")
    if confirmed_pending_schedule_receipt_is_semantically_valid(
        receipt,
        expected_lane="quote_image",
    ):
        status, attempt = load_regular_post_receipt()
        if status != "sending" or attempt != receipt["source_attempt"]:
            raise UnresolvedRegularPostReceipt(
                "Refusing to promote a changed regular main-post attempt"
            )
        atomic_write_json(REGULAR_POST_RECEIPT_FILE, receipt, durable=True)
        log.warning(
            "Wrote confirmed regular pending-schedule receipt post_id=%s path=%s",
            receipt.get("post_id"),
            REGULAR_POST_RECEIPT_FILE,
        )
        return
    if not regular_post_receipt_is_semantically_valid(receipt):
        raise RuntimeError("Internal error: generated regular-post receipt failed semantic validation")
    if receipt_namespace_entry_exists(REGULAR_POST_RECEIPT_FILE):
        status, current = load_regular_post_receipt()
        if status == "pending_schedule" and current is not None:
            if materialize_bound_regular_schedule_receipt(current) != receipt:
                raise UnresolvedRegularPostReceipt(
                    "Refusing a schedule result which does not match the durable "
                    "confirmed regular plan"
                )
            atomic_write_json(REGULAR_POST_RECEIPT_FILE, receipt, durable=True)
            log.warning(
                "Finalised regular-post pending schedule post_id=%s path=%s",
                receipt.get("post_id"),
                REGULAR_POST_RECEIPT_FILE,
            )
            return
        attempt = current
        if (
            status == "sending"
            and isinstance(attempt, dict)
            and attempt.get("schema_version") in {4, 5, 6}
        ):
            raise UnresolvedRegularPostReceipt(
                "Current-schema regular attempts must be promoted through the "
                "durable confirmed pending-schedule receipt"
            )
        if (
            status != "sending"
            or attempt is None
            or not confirmed_receipt_matches_main_attempt(receipt, attempt)
        ):
            raise UnresolvedRegularPostReceipt(
                "Refusing to overwrite an unresolved regular-post receipt: "
                f"{REGULAR_POST_RECEIPT_FILE}"
            )
        atomic_write_json(REGULAR_POST_RECEIPT_FILE, receipt, durable=True)
        log.warning(
            "Promoted regular-post sending receipt to confirmed attempt_id=%s "
            "post_id=%s path=%s",
            receipt.get("attempt_id"),
            receipt.get("post_id"),
            REGULAR_POST_RECEIPT_FILE,
        )
        return
    try:
        durable_create_receipt_json(REGULAR_POST_RECEIPT_FILE, receipt)
    except FileExistsError as exc:
        raise UnresolvedRegularPostReceipt(
            "Refusing to overwrite a regular-post receipt namespace entry "
            "which appeared during publication"
        ) from exc
    log.warning("Wrote confirmed regular-post receipt pending local reconciliation: %s", REGULAR_POST_RECEIPT_FILE)


def regular_post_receipt_is_semantically_valid(data: dict) -> bool:
    """Return whether a regular-post receipt is internally consistent."""
    schema_version = data.get("schema_version")
    if type(schema_version) is not int or schema_version not in {1, 2, 3, 4}:
        return False
    post_id = data.get("post_id")
    quote_hash = data.get("quote_hash")
    image_basename = data.get("image_basename")
    text = data.get("text")
    quote_post_epoch = receipt_int(data.get("quote_post_epoch"))
    next_quote_post_epoch = receipt_int(data.get("next_quote_post_epoch"))
    lineage_attempt = data.get("source_attempt")
    bound_timezone = MAIN_POST_SCHEDULE_TIMEZONE
    if (
        isinstance(lineage_attempt, dict)
        and lineage_attempt.get("schema_version") in {5, 6}
        and isinstance(lineage_attempt.get("recovery_plan"), dict)
    ):
        bound_timezone = lineage_attempt["recovery_plan"].get(
            "schedule_timezone"
        )

    def schedule_date_for_epoch(epoch: int) -> str | None:
        return safe_bound_schedule_date_str(epoch, bound_timezone)

    if not valid_string_post_id(post_id):
        return False
    if type(quote_hash) is not str or not re.fullmatch(r"[0-9a-f]{64}", quote_hash):
        return False
    if quote_post_epoch is None or not valid_receipt_epoch(quote_post_epoch):
        return False
    if next_quote_post_epoch is None or not valid_receipt_epoch(next_quote_post_epoch):
        return False
    if next_quote_post_epoch <= quote_post_epoch:
        return False
    if not valid_receipt_basename(image_basename):
        return False
    if not isinstance(text, str) or not text.strip():
        return False
    experiment_envelope = data.get("engagement_question_experiment")
    quote_text = data.get("quote_text") if schema_version == 4 else text
    if (
        (schema_version == 4)
        != (
            "engagement_question_experiment" in data
            and "quote_text" in data
        )
        or type(quote_text) is not str
        or quote_text_hash(quote_text) != quote_hash
        or (
            schema_version == 4
            and not engagement_experiment_attempt_envelope_is_valid(
                experiment_envelope,
                public_text=text,
                quote_hash=quote_hash,
            )
        )
        or (
            schema_version == 4
            and quote_text != experiment_envelope.get("canonical_quote_text")
        )
    ):
        return False
    if schema_version in {2, 3, 4}:
        quote_history = data.get("quote_history_after")
        image_history = data.get("image_history_after")
        if (
            not isinstance(quote_history, list)
            or len(quote_history) > 10_000
            or any(not isinstance(value, str) for value in quote_history)
            or len(set(quote_history)) != len(quote_history)
            or any(
                re.fullmatch(r"[0-9a-f]{64}", value) is None
                for value in quote_history
            )
            or quote_hash not in quote_history
        ):
            return False
        if (
            not isinstance(image_history, list)
            or len(image_history) > 10_000
            or any(not isinstance(value, str) for value in image_history)
            or len(set(image_history)) != len(image_history)
            or any(not valid_receipt_basename(value) for value in image_history)
            or image_basename not in image_history
        ):
            return False
    next_meme_epoch = receipt_int(data.get("next_meme_post_epoch", 0), default=0)
    if next_meme_epoch is None:
        return False
    schedule_version = data.get("meme_schedule_version")
    if schema_version in {3, 4} and (
        type(schedule_version) is not int
        or schedule_version < 0
        or schedule_version > MEME_SCHEDULE_VERSION
    ):
        return False
    if schema_version < 3 and schedule_version is not None and (
        type(schedule_version) is not int
        or schedule_version < 0
        or schedule_version > MEME_SCHEDULE_VERSION
    ):
        return False
    if next_meme_epoch:
        if schema_version in {3, 4} and schedule_version < 1:
            return False
        if not valid_receipt_epoch(next_meme_epoch):
            return False
        mode = data.get("next_meme_schedule_mode")
        if type(mode) is not str:
            return False
        if mode not in MEME_SCHEDULE_MODES or not mode:
            return False
        anchor_int = receipt_int(data.get("meme_anchor_quote_post_epoch", 0), default=0)
        if anchor_int is None:
            return False
        changed_by_quote = receipt_bool(data.get("meme_schedule_changed_by_quote"))
        if changed_by_quote is None:
            return False
        schedule_date = data.get("next_meme_schedule_date")
        if type(schedule_date) is not str:
            return False
        if mode == "after_first_quote_after_midday":
            if changed_by_quote:
                if anchor_int != quote_post_epoch:
                    return False
                if next_meme_epoch <= quote_post_epoch:
                    return False
                if schedule_date != schedule_date_for_epoch(quote_post_epoch):
                    return False
            else:
                if anchor_int <= 0:
                    return False
                if schedule_date != schedule_date_for_epoch(anchor_int):
                    return False
                if next_meme_epoch <= anchor_int:
                    return False
        else:
            if changed_by_quote:
                return False
            if anchor_int:
                return False
            if schedule_date != schedule_date_for_epoch(next_meme_epoch):
                return False
    elif data.get("meme_schedule_changed_by_quote") not in (None, False):
        return False

    lineage_fields = {"source_attempt", "source_attempt_sha256"}
    present_lineage_fields = lineage_fields.intersection(data)
    if present_lineage_fields:
        source_attempt = data.get("source_attempt")
        source_sha256 = data.get("source_attempt_sha256")
        if (
            present_lineage_fields != lineage_fields
            or schema_version not in {3, 4}
            or type(data.get("line_no")) is not int
            or type(data.get("source_line_number")) is not int
            or type(data.get("image_no")) is not int
            or not isinstance(source_attempt, dict)
            or source_attempt.get("schema_version")
            != (6 if schema_version == 4 else 5)
            or source_attempt.get("lifecycle_state") != "attempting"
            or source_attempt.get("lane") != "quote_image"
            or not main_post_attempt_is_semantically_valid(source_attempt)
            or type(source_sha256) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", source_sha256)
            or hashlib.sha256(
                canonical_atomic_json_bytes(source_attempt)
            ).hexdigest()
            != source_sha256
        ):
            return False
        pending = {
            "schema_version": 1,
            "receipt_type": "confirmed_pending_schedule",
            "post_id": post_id,
            "confirmation_epoch": quote_post_epoch,
            "source_attempt": copy.deepcopy(source_attempt),
            "image_summary": "",
        }
        if (
            not confirmed_pending_schedule_receipt_is_semantically_valid(
                pending,
                expected_lane="quote_image",
            )
            or materialize_bound_regular_schedule_receipt(
                pending,
                _validate_result=False,
            )
            != data
        ):
            return False
    return True


def load_regular_post_receipt() -> tuple[str, dict | None]:
    """Load regular post receipt."""
    try:
        present, data = load_receipt_json_no_follow(REGULAR_POST_RECEIPT_FILE)
    except Exception:
        log.exception("Malformed or unsafe regular-post receipt blocks main posting until repaired: %s", REGULAR_POST_RECEIPT_FILE)
        return "invalid", None
    if not present:
        return "absent", None
    if isinstance(data, dict) and main_post_attempt_is_semantically_valid(data):
        if data.get("lane") == "quote_image":
            return "sending", data
        log.critical(
            "A meme attempt was stored in the regular-post receipt path: %s",
            REGULAR_POST_RECEIPT_FILE,
        )
        return "invalid", data
    if confirmed_pending_schedule_receipt_is_semantically_valid(
        data,
        expected_lane="quote_image",
    ):
        return "pending_schedule", data
    if (
        not isinstance(data, dict)
        or type(data.get("schema_version")) is not int
        or data.get("schema_version") not in {1, 2, 3, 4}
    ):
        log.critical("Invalid regular-post receipt blocks main posting until repaired: %s", REGULAR_POST_RECEIPT_FILE)
        return "invalid", None
    required = ("post_id", "quote_hash", "image_basename", "quote_post_epoch", "next_quote_post_epoch")
    if not all(data.get(key) for key in required):
        log.critical("Incomplete regular-post receipt blocks main posting until repaired: %s", REGULAR_POST_RECEIPT_FILE)
        return "invalid", None
    if not regular_post_receipt_is_semantically_valid(data):
        log.critical(
            "Semantically invalid or unsupported-version regular-post receipt blocks main posting until repaired; "
            "do not continue an upgrade while a confirmed-post receipt exists: %s",
            REGULAR_POST_RECEIPT_FILE,
        )
        return "invalid", None
    return "valid", data


def remove_regular_post_receipt(receipt: dict) -> None:
    """Retire one exact reconciled regular-post receipt."""

    retire_current_source_receipt(
        REGULAR_POST_RECEIPT_FILE,
        canonical_atomic_json_bytes(receipt),
    )
    log.info("Removed reconciled regular-post receipt: %s", REGULAR_POST_RECEIPT_FILE)


def write_meme_post_receipt(receipt: dict) -> None:
    """Write meme post receipt."""
    if remote_receipt_retirement_is_blocking():
        raise UnresolvedMemePostReceipt(
            "Refusing meme receipt publication during source-receipt retirement"
        )
    if receipt_namespace_entry_exists(REGULAR_POST_RECEIPT_FILE):
        raise UnresolvedRegularPostReceipt(f"Refusing meme post while unresolved regular-post receipt exists: {REGULAR_POST_RECEIPT_FILE}")
    if confirmed_pending_schedule_receipt_is_semantically_valid(
        receipt,
        expected_lane="daily_meme",
    ):
        status, attempt = load_meme_post_receipt()
        if status != "sending" or attempt != receipt["source_attempt"]:
            raise UnresolvedMemePostReceipt(
                "Refusing to promote a changed meme main-post attempt"
            )
        atomic_write_json(MEME_POST_RECEIPT_FILE, receipt, durable=True)
        log.warning(
            "Wrote confirmed meme pending-schedule receipt post_id=%s path=%s",
            receipt.get("post_id"),
            MEME_POST_RECEIPT_FILE,
        )
        return
    if not meme_post_receipt_is_semantically_valid(receipt):
        raise RuntimeError("Internal error: generated meme-post receipt failed semantic validation")
    if receipt_namespace_entry_exists(MEME_POST_RECEIPT_FILE):
        status, current = load_meme_post_receipt()
        if status == "pending_schedule" and current is not None:
            if materialize_bound_meme_schedule_receipt(current) != receipt:
                raise UnresolvedMemePostReceipt(
                    "Refusing a schedule result which does not match the durable "
                    "confirmed meme plan"
                )
            atomic_write_json(MEME_POST_RECEIPT_FILE, receipt, durable=True)
            log.warning(
                "Finalised meme-post pending schedule post_id=%s path=%s",
                receipt.get("post_id"),
                MEME_POST_RECEIPT_FILE,
            )
            return
        attempt = current
        if (
            status == "sending"
            and isinstance(attempt, dict)
            and attempt.get("schema_version") in {3, 4, 5}
        ):
            raise UnresolvedMemePostReceipt(
                "Current-schema meme attempts must be promoted through the "
                "durable confirmed pending-schedule receipt"
            )
        if (
            status != "sending"
            or attempt is None
            or not confirmed_receipt_matches_main_attempt(receipt, attempt)
        ):
            raise UnresolvedMemePostReceipt(
                "Refusing to overwrite an unresolved meme-post receipt: "
                f"{MEME_POST_RECEIPT_FILE}"
            )
        atomic_write_json(MEME_POST_RECEIPT_FILE, receipt, durable=True)
        log.warning(
            "Promoted meme-post sending receipt to confirmed attempt_id=%s "
            "post_id=%s path=%s",
            receipt.get("attempt_id"),
            receipt.get("post_id"),
            MEME_POST_RECEIPT_FILE,
        )
        return
    try:
        durable_create_receipt_json(MEME_POST_RECEIPT_FILE, receipt)
    except FileExistsError as exc:
        raise UnresolvedMemePostReceipt(
            "Refusing to overwrite a meme-post receipt namespace entry which "
            "appeared during publication"
        ) from exc
    log.warning("Wrote confirmed meme-post receipt pending local reconciliation: %s", MEME_POST_RECEIPT_FILE)


def meme_post_receipt_is_semantically_valid(data: dict) -> bool:
    """Return whether a meme-post receipt is internally consistent."""
    schema_version = data.get("schema_version")
    if type(schema_version) is not int or schema_version not in {1, 2}:
        return False
    if not valid_string_post_id(data.get("post_id")):
        return False
    if not valid_receipt_basename(data.get("meme_basename")):
        return False
    meme_post_epoch = receipt_int(data.get("meme_post_epoch"))
    next_meme_post_epoch = receipt_int(data.get("next_meme_post_epoch"))
    if meme_post_epoch is None or not valid_receipt_epoch(meme_post_epoch):
        return False
    if next_meme_post_epoch is None or not valid_receipt_epoch(next_meme_post_epoch):
        return False
    if next_meme_post_epoch <= meme_post_epoch:
        return False
    schedule_version = data.get("meme_schedule_version")
    if schema_version == 2 and (
        type(schedule_version) is not int
        or schedule_version < 1
        or schedule_version > MEME_SCHEDULE_VERSION
    ):
        return False
    if schema_version == 1 and schedule_version is not None and (
        type(schedule_version) is not int
        or schedule_version < 1
        or schedule_version > MEME_SCHEDULE_VERSION
    ):
        return False
    mode = data.get("next_meme_schedule_mode", "fallback")
    if type(mode) is not str:
        return False
    if mode not in MEME_SCHEDULE_MODES or mode == "after_first_quote_after_midday":
        return False

    lineage_fields = {"source_attempt", "source_attempt_sha256"}
    present_lineage_fields = lineage_fields.intersection(data)
    if present_lineage_fields:
        source_attempt = data.get("source_attempt")
        source_sha256 = data.get("source_attempt_sha256")
        if (
            present_lineage_fields != lineage_fields
            or schema_version != 2
            or not isinstance(source_attempt, dict)
            or source_attempt.get("schema_version") != 5
            or source_attempt.get("lifecycle_state") != "attempting"
            or source_attempt.get("lane") != "daily_meme"
            or not main_post_attempt_is_semantically_valid(source_attempt)
            or type(source_sha256) is not str
            or not re.fullmatch(r"[0-9a-f]{64}", source_sha256)
            or hashlib.sha256(
                canonical_atomic_json_bytes(source_attempt)
            ).hexdigest()
            != source_sha256
        ):
            return False
        pending = {
            "schema_version": 1,
            "receipt_type": "confirmed_pending_schedule",
            "post_id": str(data["post_id"]),
            "confirmation_epoch": meme_post_epoch,
            "source_attempt": copy.deepcopy(source_attempt),
            "image_summary": str(data.get("image_summary") or ""),
        }
        if (
            not confirmed_pending_schedule_receipt_is_semantically_valid(
                pending,
                expected_lane="daily_meme",
            )
            or materialize_bound_meme_schedule_receipt(
                pending,
                _validate_result=False,
            )
            != data
        ):
            return False
    return True


def load_meme_post_receipt() -> tuple[str, dict | None]:
    """Load meme post receipt."""
    try:
        present, data = load_receipt_json_no_follow(MEME_POST_RECEIPT_FILE)
    except Exception:
        log.exception("Malformed or unsafe meme-post receipt blocks the bot until repaired: %s", MEME_POST_RECEIPT_FILE)
        return "invalid", None
    if not present:
        return "absent", None
    if isinstance(data, dict) and main_post_attempt_is_semantically_valid(data):
        if data.get("lane") == "daily_meme":
            return "sending", data
        log.critical(
            "A regular-post attempt was stored in the meme receipt path: %s",
            MEME_POST_RECEIPT_FILE,
        )
        return "invalid", data
    if confirmed_pending_schedule_receipt_is_semantically_valid(
        data,
        expected_lane="daily_meme",
    ):
        return "pending_schedule", data
    if (
        not isinstance(data, dict)
        or data.get("schema_version") not in {1, 2}
    ):
        log.critical("Invalid meme-post receipt blocks the bot until repaired: %s", MEME_POST_RECEIPT_FILE)
        return "invalid", None
    required = ("post_id", "meme_basename", "meme_post_epoch", "next_meme_post_epoch")
    if not all(data.get(key) for key in required):
        log.critical("Incomplete meme-post receipt blocks the bot until repaired: %s", MEME_POST_RECEIPT_FILE)
        return "invalid", None
    if not meme_post_receipt_is_semantically_valid(data):
        log.critical(
            "Semantically invalid or unsupported-version meme-post receipt blocks the bot until repaired; "
            "do not continue an upgrade while a confirmed-post receipt exists: %s",
            MEME_POST_RECEIPT_FILE,
        )
        return "invalid", None
    return "valid", data


def remove_meme_post_receipt(receipt: dict) -> None:
    """Retire one exact reconciled meme-post receipt."""

    retire_current_source_receipt(
        MEME_POST_RECEIPT_FILE,
        canonical_atomic_json_bytes(receipt),
    )
    log.info("Removed reconciled meme-post receipt: %s", MEME_POST_RECEIPT_FILE)


def apply_meme_post_receipt(receipt: dict, state: dict) -> None:
    """Apply meme post receipt."""
    post_id = str(receipt["post_id"])
    meme_basename = str(receipt["meme_basename"])
    meme_post_epoch = int(receipt["meme_post_epoch"])
    next_meme_post_epoch = int(receipt["next_meme_post_epoch"])
    text = str(receipt.get("text") or MEME_POST_TEXT)
    image_summary = str(receipt.get("image_summary") or "")

    last_quote_epoch = int(state.get("last_quote_post_epoch", 0) or 0)
    last_meme_epoch = int(state.get("last_meme_post_epoch", 0) or 0)
    last_main_post_id = str(state.get("last_main_post_id") or "")
    newest_known_main_epoch = max(last_quote_epoch, last_meme_epoch)
    receipt_is_newest_main = bool(
        meme_post_epoch > newest_known_main_epoch
        or (
            meme_post_epoch == newest_known_main_epoch
            and last_main_post_id in {"", post_id}
        )
    )
    if receipt_is_newest_main:
        state["last_main_post_id"] = post_id
    elif last_main_post_id != post_id:
        log.warning(
            "Meme receipt post_id=%s epoch=%s is older than known main-post "
            "state quote=%s meme=%s; preserving last_main_post_id=%s",
            post_id,
            meme_post_epoch,
            last_quote_epoch,
            last_meme_epoch,
            last_main_post_id,
        )
    posted = set(str(x) for x in state.get("posted_meme_filenames", []))
    posted.add(meme_basename)
    state["posted_meme_filenames"] = sorted(posted)
    current_next_meme_epoch = int(state.get("next_meme_post_epoch", 0) or 0)
    apply_bound_schedule = bool(
        meme_post_epoch > last_meme_epoch
        or (
            meme_post_epoch == last_meme_epoch
            and last_main_post_id in {"", post_id}
            and next_meme_post_epoch > current_next_meme_epoch
        )
    )
    if meme_post_epoch >= last_meme_epoch:
        state["last_meme_post_epoch"] = meme_post_epoch
    if apply_bound_schedule:
        state["next_meme_post_epoch"] = next_meme_post_epoch
        state["meme_schedule_version"] = (
            int(receipt["meme_schedule_version"])
            if receipt.get("schema_version") == 2
            else int(
                receipt.get("meme_schedule_version") or MEME_SCHEDULE_VERSION
            )
        )
        state["next_meme_schedule_mode"] = str(
            receipt.get("next_meme_schedule_mode") or "fallback"
        )
        source_attempt = receipt.get("source_attempt")
        if (
            isinstance(source_attempt, dict)
            and source_attempt.get("schema_version") == 5
        ):
            # Current receipts carry the date derived under their bound zone;
            # never reinterpret the epoch through this process's ambient TZ.
            state["next_meme_schedule_date"] = str(
                receipt["next_meme_schedule_date"]
            )
        else:
            state["next_meme_schedule_date"] = meme_schedule_date_str(
                next_meme_post_epoch
            )
        state["meme_anchor_quote_post_epoch"] = 0
    elif meme_post_epoch <= last_meme_epoch:
        log.warning(
            "Meme receipt post_id=%s epoch=%s is already reflected by newer "
            "meme state epoch=%s next=%s; preserving the current schedule",
            post_id,
            meme_post_epoch,
            last_meme_epoch,
            current_next_meme_epoch,
        )
    if receipt_is_newest_main:
        cache_tweet(
            state,
            tweet_id=post_id,
            text=text,
            author_id=str(MY_USER_ID),
            conversation_id=post_id,
            referenced_tweets=[],
            image_summary=image_summary,
            post_type="daily_meme",
        )
        record_recent_own_post(state, post_id)


def reconcile_meme_post_receipt(state: dict) -> bool:
    """Reconcile a durable meme receipt without duplicating a remote post."""
    status, receipt = load_meme_post_receipt()
    if status == "absent":
        return False
    if status == "sending":
        log.critical(
            "A meme post was interrupted with an uncertain remote outcome; "
            "leaving its sending receipt as a global manual-reconciliation barrier"
        )
        return False
    if receipt is not None and status in {"pending_schedule", "valid"}:
        verify_lane_transport_source_lineage_if_present(
            receipt_path=MEME_POST_RECEIPT_FILE,
            receipt=receipt,
            lane="daily_meme",
            post_id=str(receipt.get("post_id") or ""),
        )
    if status == "pending_schedule" and receipt is not None:
        log.warning(
            "Finalising local schedule for already-confirmed meme post_id=%s",
            receipt.get("post_id"),
        )
        receipt = finalize_confirmed_pending_schedule_receipt(receipt)
        status = "valid"
    if status == "invalid" or receipt is None:
        raise InvalidMemePostReceipt(f"Invalid meme-post receipt blocks the bot: {MEME_POST_RECEIPT_FILE}")
    log.warning(
        "Reconciling confirmed meme post receipt post_id=%s meme=%s",
        receipt.get("post_id"),
        receipt.get("meme_basename"),
    )
    apply_meme_post_receipt(receipt, state)
    save_state(state, durable=True)
    retire_lane_transport_journal_if_present(
        receipt_path=MEME_POST_RECEIPT_FILE,
        receipt=receipt,
        lane="daily_meme",
        post_id=str(receipt["post_id"]),
    )
    remove_meme_post_receipt(receipt)
    emit_account_root_posted(
        lane="daily_meme",
        post_id=receipt["post_id"],
        public_text=receipt.get("text") or MEME_POST_TEXT,
        image_summary=receipt.get("image_summary"),
    )
    return True


def engagement_experiment_envelope_from_receipt(
    receipt: object,
) -> dict | None:
    """Return the experiment envelope from one validated regular receipt."""

    if not isinstance(receipt, dict) or receipt.get("schema_version") != 4:
        return None
    value = receipt.get("engagement_question_experiment")
    return value if isinstance(value, dict) else None


def engagement_experiment_event_fields(receipt: dict) -> dict[str, object]:
    """Return the optional confirmed structured-event experiment fields."""

    envelope = engagement_experiment_envelope_from_receipt(receipt)
    if envelope is None:
        return {}
    binding = envelope["binding"]
    return {
        "engagement_experiment_id": binding["experiment_id"],
        "engagement_experiment_plan_sha256": binding["plan_sha256"],
        "engagement_experiment_pair_id": binding["pair_id"],
        "engagement_experiment_arm": binding["arm"],
        "engagement_experiment_member_position": binding["member_position"],
        "engagement_experiment_publication_order": binding["publication_order"],
        "engagement_experiment_sequence": binding["publication_sequence"],
        "engagement_question_present": binding["question_present"],
        "engagement_approved_question_sha256": binding[
            "approved_question_sha256"
        ],
        "engagement_public_text_sha256": binding["public_text_sha256"],
    }


def apply_confirmed_engagement_experiment_receipt(
    receipt: dict,
    state: dict,
) -> bool:
    """Apply a receipt-bound experiment transition exactly once in memory."""

    envelope = engagement_experiment_envelope_from_receipt(receipt)
    if envelope is None:
        return False
    experiment_state = state.get("engagement_question_experiment")
    if not isinstance(experiment_state, dict):
        raise RuntimeError(
            "confirmed experimental post has no protected experiment state"
        )
    plan = None
    try:
        plan, _catalogue, _quote_text_by_id = (
            load_engagement_question_runtime_plan()
        )
        if plan["plan_sha256"] != envelope["binding"]["plan_sha256"]:
            plan = None
    except Exception:
        # The immutable pre-write receipt remains sufficient authority for an
        # already-confirmed X post.  Future publication is invalidated later by
        # normal plan/state initialisation if the configured plan is absent or
        # changed.
        plan = None
    changed = engagement_question_trial.apply_confirmed_publication(
        experiment_state,
        binding=envelope["binding"],
        plan=plan,
        post_id=str(receipt["post_id"]),
        published_epoch=int(receipt["quote_post_epoch"]),
        exact_quote_text=str(envelope["canonical_quote_text"]),
        public_text=str(receipt["text"]),
        approved_question_body=str(envelope["approved_question_body"]),
    )
    state["engagement_question_experiment"] = experiment_state
    return changed


def log_confirmed_engagement_experiment_receipt(receipt: dict) -> None:
    """Emit bounded progress events after protected persistence is durable."""

    envelope = engagement_experiment_envelope_from_receipt(receipt)
    if envelope is None:
        return
    binding = envelope["binding"]
    log_event(
        "engagement_question_experimental_member_confirmed",
        post_id=str(receipt["post_id"]),
        pair_id=binding["pair_id"],
        arm=binding["arm"],
        member_position=binding["member_position"],
        publication_sequence=binding["publication_sequence"],
        plan_sha256=binding["plan_sha256"],
    )
    if binding["expected_transition"]["status_after"] == "completed":
        log_event(
            "engagement_question_experiment_completed",
            experiment_id=binding["experiment_id"],
            plan_sha256=binding["plan_sha256"],
            completed_pairs=engagement_question_trial.TARGET_COMPLETED_PAIRS,
        )


def apply_regular_post_receipt(receipt: dict, lines_used: set, images_used: set, state: dict) -> None:
    """Apply regular post receipt."""
    post_id = str(receipt["post_id"])
    quote_hash = str(receipt["quote_hash"])
    image_basename = str(receipt["image_basename"])
    quote_post_epoch = int(receipt["quote_post_epoch"])
    next_quote_post_epoch = int(receipt["next_quote_post_epoch"])
    text = str(receipt.get("text") or "")

    last_quote_epoch = int(state.get("last_quote_post_epoch", 0) or 0)
    if receipt.get("schema_version") in {2, 3, 4} and quote_post_epoch > last_quote_epoch:
        # Only a strictly newer receipt may install its exact post-cycle
        # snapshot.  Replaying an older snapshot after newer local state would
        # erase duplicate-suppression identities and could permit reuse.
        lines_used.clear()
        lines_used.update(str(value) for value in receipt["quote_history_after"])
        images_used.clear()
        images_used.update(str(value) for value in receipt["image_history_after"])
    elif receipt.get("schema_version") in {2, 3, 4}:
        # Equal or stale replay is monotonic.  Unioning the receipt's identities
        # can conservatively delay reuse, but can never discard newer evidence.
        lines_used.update(str(value) for value in receipt["quote_history_after"])
        images_used.update(str(value) for value in receipt["image_history_after"])
    else:
        # Schema v1 did not preserve cycle-boundary resets.  Retain its
        # historical additive interpretation for backward compatibility.
        lines_used.add(quote_hash)
        images_used.add(image_basename)
    last_meme_epoch = int(state.get("last_meme_post_epoch", 0) or 0)
    newest_known_main_epoch = max(last_quote_epoch, last_meme_epoch)
    last_main_post_id = str(state.get("last_main_post_id") or "")
    receipt_is_newest_main = bool(
        quote_post_epoch > newest_known_main_epoch
        or (
            quote_post_epoch == newest_known_main_epoch
            and last_main_post_id in {"", post_id}
        )
    )
    if receipt_is_newest_main:
        state["last_main_post_id"] = post_id
    else:
        log.warning(
            "Receipt post_id=%s epoch=%s is older than known main-post state quote=%s meme=%s; not moving last_main_post_id backwards",
            post_id,
            quote_post_epoch,
            last_quote_epoch,
            last_meme_epoch,
        )
    if quote_post_epoch >= last_quote_epoch:
        spacing_already_reflected = (
            quote_post_epoch == last_quote_epoch
            and str(state.get("last_regular_image_filename") or "") == image_basename
            and regular_generated_image_spacing_already_reflected(state, image_basename)
        )
        state["last_quote_post_epoch"] = quote_post_epoch
        state["last_regular_image_filename"] = image_basename
        current_next_quote_post_epoch = int(state.get("next_quote_post_epoch", 0) or 0)
        if (
            quote_post_epoch == last_quote_epoch
            and current_next_quote_post_epoch > next_quote_post_epoch
        ):
            log.warning(
                "Receipt post_id=%s is already reflected with a newer quote schedule current=%s receipt=%s; preserving current schedule",
                post_id,
                current_next_quote_post_epoch,
                next_quote_post_epoch,
            )
        else:
            state["next_quote_post_epoch"] = next_quote_post_epoch
        if not spacing_already_reflected:
            update_regular_generated_image_spacing_state(state, image_basename)
    if receipt_is_newest_main:
        if receipt.get("schema_version") in {2, 3, 4}:
            # Current schema-v3 receipts carry the exact bound schedule and
            # its policy version. Schema v2 retains the earlier exact-time
            # interpretation with a backward-compatible version fallback.
            # Apply even an intentionally empty schedule verbatim; invoking
            # the legacy random helper here would make restart recovery differ
            # from the bound transaction.
            state["next_meme_post_epoch"] = int(
                receipt.get("next_meme_post_epoch", 0) or 0
            )
            state["meme_schedule_version"] = (
                int(receipt["meme_schedule_version"])
                if receipt.get("schema_version") in {3, 4}
                else int(
                    receipt.get("meme_schedule_version")
                    or MEME_SCHEDULE_VERSION
                )
            )
            state["next_meme_schedule_mode"] = str(
                receipt.get("next_meme_schedule_mode") or ""
            )
            state["next_meme_schedule_date"] = str(
                receipt.get("next_meme_schedule_date") or ""
            )
            state["meme_anchor_quote_post_epoch"] = int(
                receipt.get("meme_anchor_quote_post_epoch") or 0
            )
        elif receipt.get("next_meme_post_epoch"):
            state["next_meme_post_epoch"] = int(receipt["next_meme_post_epoch"])
            state["meme_schedule_version"] = int(
                receipt.get("meme_schedule_version") or MEME_SCHEDULE_VERSION
            )
            state["next_meme_schedule_mode"] = str(receipt.get("next_meme_schedule_mode") or state.get("next_meme_schedule_mode") or "")
            state["next_meme_schedule_date"] = str(
                receipt.get("next_meme_schedule_date")
                or meme_schedule_date_str(int(receipt["next_meme_post_epoch"]))
            )
            state["meme_anchor_quote_post_epoch"] = int(receipt.get("meme_anchor_quote_post_epoch") or 0)
        else:
            maybe_schedule_meme_after_quote_post(state, quote_post_epoch, save=False)
    if text and receipt_is_newest_main:
        cache_tweet(
            state,
            tweet_id=post_id,
            text=text,
            author_id=str(MY_USER_ID),
            conversation_id=post_id,
            referenced_tweets=[],
            post_type="quote",
        )
    if receipt_is_newest_main:
        record_recent_own_post(state, post_id)
    apply_confirmed_engagement_experiment_receipt(receipt, state)


def save_regular_post_protected_state(lines_used: set, images_used: set, state: dict, *, durable: bool) -> None:
    """Save regular post protected state."""
    save_quote_used_hashes(LINES_USED_FILE, lines_used, durable=durable)
    save_image_used_basenames(IMAGES_USED_FILE, {str(item) for item in images_used}, durable=durable)
    save_state(state, durable=durable)


def json_file_matches(path: Path, expected: object) -> bool:
    """Return whether a JSON file contains exactly the expected value."""
    try:
        comparison = (
            state_document_for_persistence(expected)
            if path == STATE_FILE and isinstance(expected, dict)
            else expected
        )
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle) == comparison
    except Exception:
        return False


def emergency_persist_confirmed_regular_post(lines_used: set, images_used: set, state: dict) -> list[str]:
    """Return the emergency persist confirmed regular post."""
    failures: list[str] = []
    for name, func in (
        ("quote_history", lambda: save_quote_used_hashes(LINES_USED_FILE, lines_used, durable=True)),
        ("image_history", lambda: save_image_used_basenames(IMAGES_USED_FILE, {str(item) for item in images_used}, durable=True)),
        ("state", lambda: save_state(state, durable=True)),
    ):
        try:
            func()
        except Exception as exc:
            if (
                name == "state"
                and isinstance(exc, StateBackupWriteError)
                and json_file_matches(STATE_FILE, state)
            ):
                log.warning(
                    "Emergency canonical state was committed after confirmed regular "
                    "post, but a later backup/finalisation step failed; treating the "
                    "canonical durable state as the recovery representation",
                    exc_info=True,
                )
                continue
            failures.append(name)
            log.critical("Emergency persistence component failed after confirmed regular post: %s", name, exc_info=True)
    return failures


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
    state_post_epoch = receipt_int(state.get("last_quote_post_epoch"))
    try:
        pending = build_confirmed_pending_schedule_receipt(
            main_post_attempt,
            post_id=str(post_id),
            confirmation_epoch=int(post_epoch or 0),
        )
        expected = materialize_bound_regular_schedule_receipt(pending)
    except Exception:
        return False
    expected_meme_epoch = int(expected.get("next_meme_post_epoch", 0) or 0)
    experiment_envelope = engagement_experiment_envelope_from_attempt(
        main_post_attempt
    )
    experiment_complete = True
    if experiment_envelope is not None:
        experiment_state = state.get("engagement_question_experiment")
        experiment_complete = bool(
            isinstance(experiment_state, dict)
            and any(
                row.get("post_id") == str(post_id)
                and row.get("pair_id")
                == experiment_envelope["binding"]["pair_id"]
                and row.get("arm") == experiment_envelope["binding"]["arm"]
                and row.get("public_text_sha256")
                == experiment_envelope["binding"]["public_text_sha256"]
                for row in experiment_state.get("confirmed_publications", [])
                if isinstance(row, dict)
            )
        )
    return bool(
        valid_post_id(post_id)
        and post_epoch is not None
        and valid_receipt_epoch(post_epoch)
        and str(state.get("last_main_post_id") or "") == str(post_id)
        and lines_used == set(expected["quote_history_after"])
        and images_used == set(expected["image_history_after"])
        and quote_hash in lines_used
        and image_basename in images_used
        and experiment_complete
        and state_post_epoch == post_epoch
        and receipt_int(state.get("next_quote_post_epoch"))
        == int(expected["next_quote_post_epoch"])
        and int(state.get("next_meme_post_epoch", 0) or 0)
        == expected_meme_epoch
        and int(state.get("meme_schedule_version", 0) or 0)
        == int(expected["meme_schedule_version"])
        and (
            not expected_meme_epoch
            or (
                str(state.get("next_meme_schedule_mode") or "")
                == str(expected["next_meme_schedule_mode"])
                and str(state.get("next_meme_schedule_date") or "")
                == str(expected["next_meme_schedule_date"])
                and int(state.get("meme_anchor_quote_post_epoch", 0) or 0)
                == int(expected["meme_anchor_quote_post_epoch"])
            )
        )
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
    state_post_epoch = receipt_int(state.get("last_meme_post_epoch"))
    posted = {str(item) for item in state.get("posted_meme_filenames", [])}
    image_summary = str(
        main_post_attempt.get("recovery_plan", {}).get("image_summary") or ""
    )
    try:
        pending = build_confirmed_pending_schedule_receipt(
            main_post_attempt,
            post_id=str(post_id),
            confirmation_epoch=int(post_epoch or 0),
            image_summary=image_summary,
        )
        expected = materialize_bound_meme_schedule_receipt(pending)
    except Exception:
        return False
    expected_next_epoch = int(expected["next_meme_post_epoch"])
    schedule_timezone = main_post_attempt.get("recovery_plan", {}).get(
        "schedule_timezone"
    )
    expected_post_date = safe_bound_schedule_date_str(
        int(post_epoch or 0),
        schedule_timezone,
    )
    expected_next_date = safe_bound_schedule_date_str(
        expected_next_epoch,
        schedule_timezone,
    )
    cached = state.get("tweet_cache", {}).get(str(post_id))
    return bool(
        valid_post_id(post_id)
        and post_epoch is not None
        and valid_receipt_epoch(post_epoch)
        and str(state.get("last_main_post_id") or "") == str(post_id)
        and meme_basename in posted
        and state_post_epoch == post_epoch
        and receipt_int(state.get("next_meme_post_epoch"))
        == expected_next_epoch
        and int(state.get("meme_schedule_version", 0) or 0)
        == int(expected["meme_schedule_version"])
        and expected_post_date is not None
        and expected_next_date is not None
        and expected_next_date > expected_post_date
        and str(state.get("next_meme_schedule_mode") or "")
        == str(expected["next_meme_schedule_mode"])
        and str(state.get("next_meme_schedule_date") or "")
        == expected_next_date
        and int(state.get("meme_anchor_quote_post_epoch", 0) or 0) == 0
        and isinstance(cached, dict)
        and str(cached.get("text") or "") == str(main_post_attempt["text"])
        and str(cached.get("post_type") or "") == "daily_meme"
        and str(cached.get("image_summary") or "") == image_summary
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
    if not dry_run:
        block_if_ambiguous_remote_post()
    try:
        from historical_context_formatter import (
            HistoricalContextReplyStore,
            format_context_reply_public,
            load_and_validate_corpus,
            packet_for_posted_quote,
            x_weighted_length,
        )

        store = historical_context_reply_store()
        if not dry_run:
            # Reconcile before every policy/configuration exit. A durable
            # receipt describes an earlier remote attempt and must not be
            # hidden merely because the lane is now disabled, the packet is
            # unavailable, or the current quote is blocked by policy.
            store.reconcile_receipt()
        if _HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON and not dry_run:
            return {
                "status": "failed_terminal",
                "reason": _HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON,
            }
        if not historical_context_reply.get("enabled") and not dry_run:
            return {"status": "disabled"}

        if _HISTORICAL_CONTEXT_CORPUS_SNAPSHOT is None:
            packets, unresolved = load_and_validate_corpus(
                HISTORICAL_CONTEXT_RESEARCH_DIR,
                require_source_role_audit=True,
            )
        else:
            packets, unresolved = _HISTORICAL_CONTEXT_CORPUS_SNAPSHOT
        packet = packet_for_posted_quote(packets, unresolved, quote_hash, quote_text)
        if packet is None:
            log.warning("No completed canonical research packet for quote_hash=%s; context reply skipped", quote_hash)
            log_event(
                "historical_context_reply", status="skipped_no_completed_packet",
                parent_post_id=str(parent_post_id), quote_id=str(quote_hash),
                reason="no_completed_canonical_packet",
            )
            return {"status": "skipped_no_completed_packet"}

        gate = _HISTORICAL_CONTEXT_SEMANTIC_GATE
        if gate is None:
            gate = initialise_historical_context_semantic_gate(packets)

        gate_disposition = gate.disposition(packet["quote_id"])
        reviewed_gate_disposition = gate.reviewed_disposition(packet["quote_id"])
        if not gate.available or gate_disposition is not None:
            reason = (
                "semantic_review_gate_unavailable"
                if not gate.available
                else "open_semantic_review"
            )
            log.warning(
                "Historical context reply blocked by reviewed semantic gate. "
                "quote_id=%s reason=%s disposition=%s ledger_sha256=%s",
                packet["quote_id"],
                reason,
                gate_disposition or "unavailable",
                gate.ledger_sha256 or "unavailable",
            )
            log_event(
                "historical_context_reply",
                status="skipped_future_policy",
                parent_post_id=str(parent_post_id),
                quote_id=str(packet["quote_id"]),
                reason=reason,
                semantic_review_disposition=gate_disposition,
                semantic_review_ledger_sha256=gate.ledger_sha256,
                semantic_review_projection_sha256=gate.projection_sha256,
            )
            return {
                "status": "skipped_future_policy",
                "quote_id": str(packet["quote_id"]),
                "reason": reason,
                "semantic_review_disposition": gate_disposition,
                "semantic_review_ledger_sha256": gate.ledger_sha256,
                "semantic_review_projection_sha256": gate.projection_sha256,
            }
        formatted = format_context_reply_public(
            packet,
            maximum_length=int(historical_context_reply["maximum_length"]),
            include_meaning=bool(historical_context_reply["include_meaning"]),
            include_source=bool(historical_context_reply["include_source"]),
            include_verification=bool(historical_context_reply["include_verification"]),
        )
        if formatted is None:
            log.warning("Canonical packet could not produce a safe context reply quote_id=%s", packet["quote_id"])
            log_event(
                "historical_context_reply", status="skipped_unformattable_packet",
                parent_post_id=str(parent_post_id), quote_id=str(packet["quote_id"]),
                reason="unformattable_canonical_packet",
            )
            return {"status": "skipped_unformattable_packet"}
        if dry_run:
            print(formatted["text"])
            print(
                f"Character count: raw={formatted['raw_character_count']} "
                f"x_weighted={formatted['character_count']}/{formatted['maximum_length']}"
            )
        formatter_metadata = {
            "formatter_version": formatted["formatter_version"],
            "template_variant": formatted["template_variant"],
            "meaning_included": formatted["meaning_included"],
            "meaning_decision_reason": formatted["meaning_decision_reason"],
            "raw_character_count": formatted["raw_character_count"],
            "weighted_character_count": formatted["weighted_character_count"],
            "verification_label": formatted["verification_label"],
            "source_class": formatted["source_class"],
            "historical_confidence": formatted["historical_confidence"],
            "confidence_dimensions": formatted["confidence_dimensions"],
            "source_role_audit_version": formatted["source_role_audit_version"],
            "rendering_mode": formatted["rendering_mode"],
            "shortening_applied": formatted["shortening_applied"],
        }
        historical_sigint_guard = (
            None if dry_run else begin_confirmed_post_sigint_deferral()
        )
        try:
            result = store.post(
                parent_post_id=str(parent_post_id),
                quote_id=str(packet["quote_id"]),
                reply_text=str(formatted["text"]),
                create_post=create_post,
                now_epoch=now_epoch,
                dry_run=dry_run,
                formatter_metadata=formatter_metadata,
                # The store retires its journal only after completed history is
                # durable, so the prepared source-removal guard is always safe
                # to resume in a later process.
                on_confirmed_receipt=(
                    None if dry_run else on_confirmed_receipt
                ),
                remote_failure_is_definite_non_success=(
                    lambda error: isinstance(error, RemoteOperationsPaused)
                ),
                require_confirmed_transport=not dry_run,
                on_source_receipt_published=(
                    None if dry_run else on_source_receipt_published
                ),
                on_remote_transaction_started=(
                    None if dry_run else on_remote_transaction_started
                ),
                on_definite_non_success=(
                    None if dry_run else on_definite_non_success
                ),
            )
        finally:
            # Every post-start exit retains either the sending receipt, its
            # transport journal, an exact retirement guard, or completed
            # history.  A local pre-transport pause is also definite.
            end_confirmed_post_sigint_deferral(historical_sigint_guard)
        event_text = str(result.get("reply_text") or formatted["text"])
        event_metadata = formatter_metadata
        if result.get("status") == "already_completed":
            stored_metadata = result.get("formatter_metadata")
            if isinstance(stored_metadata, dict):
                event_metadata = stored_metadata
            else:
                # Receipts created before formatter metadata was introduced are v1. The
                # existing reply remains authoritative and must not be relabelled as v2.
                event_metadata = {
                    "formatter_version": "historical_context_reply_schema_v1",
                    "template_variant": "legacy_v1",
                    "meaning_included": "\n\nMeaning:" in event_text,
                    "meaning_decision_reason": "Legacy formatter metadata unavailable.",
                    "raw_character_count": len(event_text),
                    "weighted_character_count": x_weighted_length(event_text),
                    "verification_label": formatted["verification_label"],
                    "source_class": formatted["source_class"],
                    "historical_confidence": formatted["historical_confidence"],
                    "shortening_applied": False,
                }
        log_event(
            "historical_context_reply",
            status=result.get("status"),
            parent_post_id=str(parent_post_id),
            quote_id=str(packet["quote_id"]),
            character_count=event_metadata["weighted_character_count"],
            raw_character_count=event_metadata["raw_character_count"],
            verification_label=event_metadata["verification_label"],
            source_class=event_metadata["source_class"],
            historical_confidence=event_metadata["historical_confidence"],
            confidence_dimensions=event_metadata.get("confidence_dimensions"),
            source_role_audit_version=event_metadata.get("source_role_audit_version"),
            rendering_mode=event_metadata.get("rendering_mode"),
            shortening_applied=event_metadata["shortening_applied"],
            meaning_omitted=not event_metadata["meaning_included"],
            meaning_included=event_metadata["meaning_included"],
            meaning_decision_reason=event_metadata["meaning_decision_reason"],
            source_omitted="Source:" not in event_text and "Source —" not in event_text,
            verification_omitted="Verification:" not in event_text and "Verification —" not in event_text,
            formatter_version=event_metadata["formatter_version"],
            template_variant=event_metadata["template_variant"],
            semantic_review_disposition=(
                reviewed_gate_disposition or "unavailable"
            ),
            semantic_review_ledger_sha256=gate.ledger_sha256,
            semantic_review_projection_sha256=gate.projection_sha256,
            reply_preview=event_text[:160],
            reason="post_failed" if result.get("status") == "failed" else "",
        )
        if (
            result.get("status") in {"completed", "already_completed"}
            and re.fullmatch(r"\d{1,30}", str(result.get("reply_post_id") or ""))
        ):
            emit_historical_context_reply_posted(
                parent_post_id=parent_post_id,
                reply_post_id=result["reply_post_id"],
                reply_text=event_text,
                quote_id=packet["quote_id"],
            )
        return {**result, "formatted": formatted}
    except Exception as exc:
        if type(exc).__name__ == "AmbiguousContextReplyOutcome":
            log_event(
                "historical_context_reply", status="failed",
                parent_post_id=str(parent_post_id), quote_id=str(quote_hash),
                reason="ambiguous_outcome",
            )
            raise
        # The caller owns an independent durable outbox obligation. Propagate so it can
        # record a retryable or terminal context-only outcome without relabelling the
        # already-confirmed main post.
        log.error(
            "Historical context reply failed independently for parent_post_id=%s quote_hash=%s: %s",
            parent_post_id,
            quote_hash,
            exc,
            exc_info=True,
        )
        log_event(
            "historical_context_reply", status="failed",
            parent_post_id=str(parent_post_id), quote_id=str(quote_hash),
            reason=type(exc).__name__,
        )
        raise


def historical_context_reply_store(*, allow_missing_history: bool = False):
    """Return the reply store with production history-loss protection."""
    from historical_context_formatter import HistoricalContextReplyStore

    return HistoricalContextReplyStore(
        HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        mutation_authority_provider=transaction_mutation_authority,
        retirement_uncertainty_callback=(
            latch_source_receipt_retirement_uncertainty
        ),
        require_existing_history=(
            not TEST_MODE and not allow_missing_history
        ),
    )


def historical_context_outbox_store():
    """Return the durable store for auxiliary context-reply obligations."""
    from historical_context_outbox import HistoricalContextOutbox

    return HistoricalContextOutbox(
        HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE,
        # Production installation creates this authority explicitly.  Focused
        # test fixtures may still construct isolated stores lazily; production
        # must never reinterpret later disappearance as a fresh empty outbox.
        require_existing=not TEST_MODE,
    )


def require_historical_context_outbox_writable() -> None:
    """Fail before a main X post when its context state cannot be persisted."""
    global _HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON
    if (
        not historical_context_reply.get("enabled")
        or _HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON
    ):
        return
    if _HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON:
        raise RuntimeError(
            "historical-context outbox is unavailable: "
            f"{_HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON}"
        )
    try:
        historical_context_outbox_store().verify_writable()
    except Exception as exc:
        _HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON = (
            f"{type(exc).__name__}: {exc}"
        )
        raise RuntimeError(
            "historical-context outbox failed the pre-post durability check"
        ) from exc


def canonical_context_obligation_quote_id(quote_hash: str, quote_text: str) -> str:
    """Resolve a stable canonical packet identity without fuzzy matching."""
    if _HISTORICAL_CONTEXT_CORPUS_SNAPSHOT is None:
        return str(quote_hash)
    from historical_context_formatter import packet_for_posted_quote

    packets, unresolved = _HISTORICAL_CONTEXT_CORPUS_SNAPSHOT
    packet = packet_for_posted_quote(
        packets,
        unresolved,
        str(quote_hash),
        str(quote_text),
    )
    return str(packet["quote_id"]) if packet is not None else str(quote_hash)


def enqueue_historical_context_obligation(receipt: dict) -> dict:
    """Persist an optional context obligation before releasing a main receipt."""
    global _HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON
    store = historical_context_outbox_store()
    parent_post_id = str(receipt["post_id"])
    confirmed_epoch = int(receipt["quote_post_epoch"])
    if (
        not historical_context_reply.get("enabled")
        or _HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON
    ):
        reason = (
            "historical_context_runtime_unavailable"
            if _HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON
            else "historical_context_reply_disabled"
        )
        try:
            obligation = store.enqueue(
                parent_post_id,
                main_post_confirmed_epoch=confirmed_epoch,
                not_required_reason=reason,
            )
        except Exception as exc:
            _HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON = (
                f"{type(exc).__name__}: {exc}"
            )
            log.error(
                "Could not persist a not-required historical-context state; "
                "the confirmed main post remains independent. parent_post_id=%s "
                "reason=%s",
                parent_post_id,
                reason,
                exc_info=True,
            )
            log_event(
                "posting_transaction_state",
                parent_post_id=parent_post_id,
                main_post_state="main_post_confirmed",
                context_reply_state="context_reply_not_required",
                context_state_persisted=False,
                reason=reason,
            )
            return {
                "parent_post_id": parent_post_id,
                "main_post": {
                    "state": "main_post_confirmed",
                    "confirmed_epoch": confirmed_epoch,
                },
                "context_reply": {
                    "state": "context_reply_not_required",
                    "reason": reason,
                    "updated_epoch": confirmed_epoch,
                },
            }
    else:
        quote_text = str(receipt.get("quote_text", receipt.get("text") or ""))
        obligation = store.enqueue(
            parent_post_id,
            main_post_confirmed_epoch=confirmed_epoch,
            quote_id=canonical_context_obligation_quote_id(
                str(receipt["quote_hash"]),
                quote_text,
            ),
            quote_text=quote_text,
        )
    log_event(
        "posting_transaction_state",
        parent_post_id=parent_post_id,
        main_post_state=obligation["main_post"]["state"],
        context_reply_state=obligation["context_reply"]["state"],
    )
    return obligation


def _record_context_outbox_failure(
    store,
    *,
    parent_post_id: str,
    attempt_number: int,
    error: BaseException | str,
    failed_epoch: int,
    force_terminal: bool = False,
    proved_remote_non_success: bool = False,
) -> str:
    """Record one bounded context failure and return its durable state."""
    if force_terminal or attempt_number >= store.max_attempts:
        obligation = store.record_terminal_failure(
            parent_post_id,
            attempt_number=attempt_number,
            error=error,
            failed_epoch=failed_epoch,
            proved_remote_non_success=proved_remote_non_success,
        )
    else:
        obligation = store.record_retryable_failure(
            parent_post_id,
            attempt_number=attempt_number,
            error=error,
            failed_epoch=failed_epoch,
            proved_remote_non_success=proved_remote_non_success,
        )
    return str(obligation["context_reply"]["state"])


def _record_or_verify_proved_context_failure(
    store,
    *,
    parent_post_id: str,
    attempt_number: int,
    error,
    failed_epoch: int,
) -> str:
    """Preserve or verify one exact proved-non-success outbox outcome."""

    source_sha256 = getattr(error, "source_receipt_sha256", None)
    source_attempt = getattr(error, "source_receipt_attempt_number", None)
    remote_error = getattr(error, "remote_error", None)
    if (
        type(source_sha256) is not str
        or not re.fullmatch(r"[0-9a-f]{64}", source_sha256)
        or type(source_attempt) is not int
        or source_attempt < 1
        or not isinstance(remote_error, BaseException)
    ):
        raise RuntimeError(
            "definite historical-context persistence error lacks exact source proof"
        )
    obligation = store.get(parent_post_id)
    context = (
        obligation.get("context_reply") if isinstance(obligation, dict) else None
    )
    if not isinstance(context, dict) or context.get("attempt_count") != attempt_number:
        raise RuntimeError("historical-context outbox attempt changed after failure")
    if context.get("state") in {
        "context_reply_failed_retryable",
        "context_reply_failed_terminal",
    }:
        failure = context.get("failure")
        if not (
            isinstance(failure, dict)
            and failure.get("remote_outcome") == "proved_non_success"
            and failure.get("source_receipt_sha256") == source_sha256
            and failure.get("source_receipt_attempt_number") == source_attempt
        ):
            raise RuntimeError(
                "durable historical-context failure does not match source proof"
            )
        return str(context["state"])
    if (
        context.get("state") != "context_reply_attempting"
        or context.get("source_receipt_sha256") != source_sha256
        or context.get("source_receipt_attempt_number") != source_attempt
    ):
        raise RuntimeError(
            "historical-context source binding changed before failure persistence"
        )
    return _record_context_outbox_failure(
        store,
        parent_post_id=parent_post_id,
        attempt_number=attempt_number,
        error=remote_error,
        failed_epoch=failed_epoch,
        force_terminal=attempt_number >= store.max_attempts,
        proved_remote_non_success=True,
    )


def recover_interrupted_historical_context_attempt(
    store,
    obligation: dict,
    *,
    recovered_epoch: int,
    receipt_was_observed: bool = False,
) -> dict:
    """Resolve a durable interrupted claim without repeating its remote work."""
    from historical_context_formatter import (
        AmbiguousContextReplyOutcome,
        HistoricalContextReplyStore,
    )

    parent_id = str(obligation["parent_post_id"])
    context = obligation["context_reply"]
    if context.get("state") != "context_reply_attempting":
        raise ValueError("only an interrupted attempting state can be recovered")
    attempt_number = int(context["attempt_count"])
    context_store = historical_context_reply_store()
    transport_journal_path = journal_path_for_receipt(
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
    )
    journal_was_observed = transport_journal_is_blocking(
        transport_journal_path
    )
    bound_source_sha256 = context.get("source_receipt_sha256")
    bound_source_attempt = context.get("source_receipt_attempt_number")
    source_binding_present = bool(
        type(bound_source_sha256) is str
        and re.fullmatch(r"[0-9a-f]{64}", bound_source_sha256)
        and type(bound_source_attempt) is int
        and bound_source_attempt >= 1
    )
    loaded_before_reconciliation = context_store._load_receipt_safely()
    exact_current_sending_receipt = False
    if loaded_before_reconciliation is not None:
        source_receipt, source_receipt_bytes = loaded_before_reconciliation
        if HistoricalContextReplyStore._valid_sending_receipt(source_receipt):
            observed_source_sha256 = hashlib.sha256(
                source_receipt_bytes
            ).hexdigest()
            observed_source_attempt = source_receipt["attempt_number"]
            if (
                not source_binding_present
                and context.get("remote_transaction_started") is False
                and not journal_was_observed
                and not transport_journal_is_blocking(
                    transport_journal_path
                )
            ):
                history = context_store.history()
                previous = history["items"].get(parent_id)
                expected_source_attempt = (
                    int(previous.get("attempt_count", 0)) + 1
                    if isinstance(previous, dict)
                    else 1
                )
                if (
                    (
                        isinstance(previous, dict)
                        and previous.get("status") != "failed"
                    )
                    or
                    source_receipt.get("parent_post_id") != parent_id
                    or source_receipt.get("quote_id")
                    != context.get("quote_id")
                    or source_receipt.get("attempt_number")
                    != expected_source_attempt
                ):
                    raise AmbiguousContextReplyOutcome(
                        "unbound historical-context source receipt does not "
                        "match the exact pre-transport attempt",
                        parent_post_id=parent_id,
                        reply_text=str(source_receipt.get("reply_text") or ""),
                    )
                rebound = store.bind_attempt_source_receipt(
                    parent_id,
                    attempt_number=attempt_number,
                    source_receipt_sha256=observed_source_sha256,
                    source_receipt_attempt_number=observed_source_attempt,
                )
                context = rebound["context_reply"]
                bound_source_sha256 = observed_source_sha256
                bound_source_attempt = observed_source_attempt
                source_binding_present = True
            exact_current_sending_receipt = bool(
                source_binding_present
                and bound_source_sha256 == observed_source_sha256
                and bound_source_attempt == observed_source_attempt
            )
        elif HistoricalContextReplyStore._valid_receipt(source_receipt):
            observed_source_sha256 = source_receipt.get(
                "source_receipt_sha256"
            )
            observed_source_attempt = source_receipt.get("attempt_number")
        else:
            raise RuntimeError("invalid historical context reply receipt")
        if (
            not source_binding_present
            or bound_source_sha256 != observed_source_sha256
            or bound_source_attempt != observed_source_attempt
        ):
            raise AmbiguousContextReplyOutcome(
                "historical-context receipt does not match the exact current "
                "outbox source binding",
                parent_post_id=parent_id,
                reply_text=str(source_receipt.get("reply_text") or ""),
            )
    journal_is_blocking = transport_journal_is_blocking(
        transport_journal_path
    )
    if (
        context.get("remote_transaction_started") is False
        and exact_current_sending_receipt
        and not journal_was_observed
        and not journal_is_blocking
    ):
        # The exact source receipt exists, but the durable outbox still proves
        # that journal arming and transport never began.  Persist that proof
        # before writing failed history and retiring only this receipt.  Calling
        # generic receipt reconciliation first would incorrectly classify the
        # safely pre-transport crash as an ambiguous remote outcome.
        state_name = _record_context_outbox_failure(
            store,
            parent_post_id=parent_id,
            attempt_number=attempt_number,
            error="context attempt was interrupted before remote transaction start",
            failed_epoch=recovered_epoch,
            force_terminal=attempt_number >= store.max_attempts,
            proved_remote_non_success=True,
        )
        updated = store.get(parent_id)
        if updated is None or updated["context_reply"]["state"] != state_name:
            raise RuntimeError("pre-remote context recovery was not durable")
        context_store.ensure_proved_failure_history_from_outbox(
            updated["context_reply"]
        )
        if context_store.reconcile_receipt_disposition() != "definite_failure":
            raise RuntimeError(
                "exact pre-remote context receipt changed before retirement"
            )
        return {
            "parent_post_id": parent_id,
            "status": "recovered_pre_remote_interruption",
            "context_reply_state": state_name,
            "attempt_number": attempt_number,
            "remote_work_repeated": False,
        }
    # The sending receipt is the authoritative ambiguity barrier.  It must be
    # reconciled (confirmed, definitely failed, or left ambiguous) before an
    # interrupted outbox claim can be downgraded or retried.
    receipt_disposition = context_store.reconcile_receipt_disposition(
        retain_definite_failure_receipt=True,
        preloaded_receipt=loaded_before_reconciliation,
    )
    journal_is_blocking = transport_journal_is_blocking(
        transport_journal_path
    )
    history = context_store.history()
    previous = history["items"].get(parent_id)
    previous_source_matches = bool(
        source_binding_present
        and isinstance(previous, dict)
        and previous.get("source_receipt_sha256") == bound_source_sha256
        and (
            previous.get("source_receipt_attempt_number")
            if previous.get("status") == "failed"
            else previous.get("attempt_number")
        )
        == bound_source_attempt
    )
    if (
        receipt_was_observed
        and not (
            isinstance(previous, dict)
            and previous.get("quote_id") == context["quote_id"]
            and previous.get("status") in {"completed", "failed"}
            and previous_source_matches
        )
    ):
        raise AmbiguousContextReplyOutcome(
            "an observed historical-context receipt disappeared before its "
            "outcome could be reconciled",
            parent_post_id=parent_id,
            reply_text=str(context.get("reply_text") or ""),
        )
    if isinstance(previous, dict) and previous.get("quote_id") != context["quote_id"]:
        raise RuntimeError(
            "context history identity conflicts with interrupted outbox attempt"
        )
    if (
        context.get("remote_transaction_started") is False
        and receipt_disposition == "absent"
        and not source_binding_present
        and not receipt_was_observed
        and not journal_was_observed
        and not journal_is_blocking
        and isinstance(previous, dict)
        and previous.get("status") == "completed"
        and previous.get("quote_id") == context["quote_id"]
    ):
        # store.post() returns ``already_completed`` before publishing a new
        # source receipt.  A crash before the worker copies that durable result
        # into its newly claimed outbox must recover the existing confirmed
        # identity, not recast it as a terminal pre-transport failure.
        updated = store.record_confirmed(
            parent_id,
            attempt_number=attempt_number,
            reply_post_id=str(previous["reply_post_id"]),
            confirmed_epoch=recovered_epoch,
        )
        emit_historical_context_history_observation(previous)
        return {
            "parent_post_id": parent_id,
            "status": "recovered_confirmed_history",
            "context_reply_state": str(updated["context_reply"]["state"]),
            "attempt_number": attempt_number,
            "remote_work_repeated": False,
        }
    proved_pre_remote = bool(
        context.get("remote_transaction_started") is False
        and receipt_disposition == "absent"
        and not previous_source_matches
        and not journal_was_observed
        and not journal_is_blocking
    )
    if proved_pre_remote:
        state_name = _record_context_outbox_failure(
            store,
            parent_post_id=parent_id,
            attempt_number=attempt_number,
            error="context attempt was interrupted before remote transaction start",
            failed_epoch=recovered_epoch,
            force_terminal=attempt_number >= store.max_attempts,
        )
        updated = store.get(parent_id)
        if updated is None or updated["context_reply"]["state"] != state_name:
            raise RuntimeError("pre-remote context recovery was not durable")
        return {
            "parent_post_id": parent_id,
            "status": "recovered_pre_remote_interruption",
            "context_reply_state": state_name,
            "attempt_number": attempt_number,
            "remote_work_repeated": False,
        }
    exact_current_failure = bool(
        receipt_disposition == "definite_failure"
        and previous_source_matches
        and not journal_was_observed
        and not journal_is_blocking
    )
    if (
        isinstance(previous, dict)
        and not previous_source_matches
        and not proved_pre_remote
    ):
        # A failed history row is keyed only by parent/quote identity.  The
        # reply store and outbox deliberately have independent attempt
        # ordinals, so an older definite failure cannot prove the outcome of a
        # later attempt which may have reached transport.  Only an explicit
        # current-schema pre-remote phase or the exact matching sending receipt
        # reconciled above may consume the row safely.
        raise AmbiguousContextReplyOutcome(
            "an interrupted historical-context attempt may have reached its "
            "remote transaction but only an older failed history outcome is "
            "available",
            parent_post_id=parent_id,
            reply_text=str(context.get("reply_text") or ""),
        )
    if not isinstance(previous, dict):
        # Missing phase metadata denotes a legacy record whose remote boundary
        # is unknown. True denotes the current schema's explicit remote phase.
        # A journal object or unsafe journal namespace likewise remains an
        # independent fail-closed barrier even if the source receipt vanished.
        raise AmbiguousContextReplyOutcome(
            "an interrupted historical-context attempt may have reached its "
            "remote transaction but has neither a durable transaction receipt "
            "nor a recorded terminal outcome",
            parent_post_id=parent_id,
            reply_text=str(context.get("reply_text") or ""),
        )
    if isinstance(previous, dict) and previous.get("status") == "completed":
        if not previous_source_matches:
            raise AmbiguousContextReplyOutcome(
                "completed historical-context history does not match the exact "
                "current outbox source binding",
                parent_post_id=parent_id,
                reply_text=str(context.get("quote_text") or ""),
            )
        # HistoricalContextReplyStore counts only attempts which reached its
        # remote-write transaction.  The outbox also counts formatter and
        # policy failures, so the two ordinals are deliberately not compared.
        # Matching parent and canonical quote identity plus a validated
        # completed history record is the durable proof required here.
        updated = store.record_confirmed(
            parent_id,
            attempt_number=attempt_number,
            reply_post_id=str(previous["reply_post_id"]),
            confirmed_epoch=recovered_epoch,
        )
        status = "recovered_confirmed_history"
    else:
        failure = "context attempt was interrupted before a durable outcome"
        if isinstance(previous, dict) and previous.get("status") == "failed":
            if not previous_source_matches:
                raise AmbiguousContextReplyOutcome(
                    "failed historical-context history does not match the exact "
                    "current outbox source binding",
                    parent_post_id=parent_id,
                    reply_text=str(context.get("quote_text") or ""),
                )
            # The failed history may describe this remote attempt or an older
            # one because pre-transaction failures are counted only by the
            # outbox.  Either way, no confirmed reply exists; consuming the
            # claimed outbox attempt without repeating remote work is the
            # conservative recovery action.
            failure = str(previous["failure"])
        state_name = _record_context_outbox_failure(
            store,
            parent_post_id=parent_id,
            attempt_number=attempt_number,
            error=failure,
            failed_epoch=recovered_epoch,
            force_terminal=attempt_number >= store.max_attempts,
            proved_remote_non_success=exact_current_failure,
        )
        updated = store.get(parent_id)
        status = "recovered_interrupted_attempt"
        if updated is None or updated["context_reply"]["state"] != state_name:
            raise RuntimeError("interrupted context recovery was not durable")
        if exact_current_failure:
            if (
                context_store.reconcile_receipt_disposition()
                != "definite_failure"
            ):
                raise RuntimeError(
                    "exact context failure receipt changed before retirement"
                )
    if status == "recovered_confirmed_history":
        emit_historical_context_history_observation(previous)
    return {
        "parent_post_id": parent_id,
        "status": status,
        "context_reply_state": str(updated["context_reply"]["state"]),
        "attempt_number": attempt_number,
        "remote_work_repeated": False,
    }


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
    global _HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON
    from historical_context_outbox import DUE_STATES

    if _HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON:
        log.debug(
            "Historical-context outbox processing deferred until a controlled "
            "restart because this process disabled the context runtime"
        )
        return []
    if _HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON:
        log.debug(
            "Historical-context outbox processing is latched unavailable until "
            "a controlled restart. reason=%s",
            _HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON,
        )
        return []
    if lane_paused("disable_replies"):
        log.info(
            "Historical-context outbox processing deferred by reply runtime control"
        )
        return []
    if runtime_state is not None and in_api_cooldown(
        runtime_state,
        scope="write",
    ):
        log.info(
            "Historical-context outbox processing deferred by X write cooldown"
        )
        return []

    current = now_epoch()
    try:
        if parent_post_id is None:
            due = store.due(current, limit=max(1, int(limit)))
        else:
            requested = store.get(str(parent_post_id))
            requested_context = (
                requested.get("context_reply")
                if isinstance(requested, dict)
                else None
            )
            due = (
                [requested]
                if isinstance(requested_context, dict)
                and requested_context.get("state") in DUE_STATES
                and (
                    requested_context.get("state")
                    == "context_reply_attempting"
                    or int(requested_context.get("next_attempt_epoch") or 0)
                    <= current
                )
                else []
            )
    except Exception as exc:
        _HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON = (
            f"{type(exc).__name__}: {exc}"
        )
        log.critical(
            "Historical-context outbox unavailable; main-post lanes remain independent. "
            "error_type=%s error=%s",
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        log_event(
            "historical_context_outbox",
            status="unavailable",
            error_type=type(exc).__name__,
            reason=str(exc)[:500],
        )
        return []

    results: list[dict] = []
    for obligation in due:
        parent_id = str(obligation["parent_post_id"])
        context = obligation["context_reply"]
        if context.get("state") not in DUE_STATES:
            continue
        if context.get("state") == "context_reply_attempting":
            try:
                recovered = recover_interrupted_historical_context_attempt(
                    store,
                    obligation,
                    recovered_epoch=now_epoch(),
                    receipt_was_observed=(
                        historical_context_receipt_reconciliation_only
                    ),
                )
            except Exception as exc:
                if type(exc).__name__ == "AmbiguousContextReplyOutcome":
                    record_ambiguous_remote_post(
                        {
                            "text": str(getattr(exc, "reply_text", "") or ""),
                            "reply": {
                                "in_reply_to_tweet_id": str(
                                    getattr(exc, "parent_post_id", "")
                                    or parent_id
                                )
                            },
                        }
                    )
                _HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON = (
                    f"{type(exc).__name__}: {exc}"
                )
                log.critical(
                    "Could not reconcile an interrupted historical-context "
                    "attempt; no remote work was repeated. parent_post_id=%s",
                    parent_id,
                    exc_info=True,
                )
                results.append(
                    {
                        "parent_post_id": parent_id,
                        "status": "interrupted_attempt_recovery_failed",
                        "error_type": type(exc).__name__,
                    }
                )
                break
            log_event(
                "historical_context_obligation",
                **recovered,
            )
            results.append(recovered)
            if (
                historical_context_receipt_reconciliation_only
                or historical_context_outbox_reconciliation_only
            ):
                break
            continue
        if (
            historical_context_receipt_reconciliation_only
            or historical_context_outbox_reconciliation_only
            or historical_context_receipt_path_present_or_unsafe()
        ):
            log.critical(
                "A historical-context receipt may be reconciled only against "
                "its already-attempting outbox record; no new context attempt "
                "was claimed. parent_post_id=%s",
                parent_id,
            )
            results.append(
                {
                    "parent_post_id": parent_id,
                    "status": "blocked_by_unresolved_context_receipt",
                    "context_reply_state": str(context.get("state") or ""),
                }
            )
            break
        try:
            claimed = store.claim_attempt(
                parent_id,
                # The obligation was selected as due at ``current``.  A
                # subsequent CLOCK_REALTIME rollback must not invalidate that
                # already observed eligibility or strand the lane until a
                # restart; preserve the later of the two observations.
                started_epoch=max(current, now_epoch()),
            )
            context = claimed["context_reply"]
            attempt_number = int(context["attempt_count"])
        except Exception as exc:
            _HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON = (
                f"{type(exc).__name__}: {exc}"
            )
            log.critical(
                "Could not durably claim historical-context attempt; no context "
                "work was performed. parent_post_id=%s",
                parent_id,
                exc_info=True,
            )
            log_event(
                "historical_context_outbox",
                status="claim_failed",
                parent_post_id=parent_id,
                error_type=type(exc).__name__,
                reason=str(exc)[:500],
            )
            results.append(
                {
                    "parent_post_id": parent_id,
                    "status": "outbox_claim_failed",
                    "context_reply_state": str(context.get("state") or ""),
                }
            )
            break
        try:
            result = maybe_post_historical_context_reply(
                quote_hash=str(context["quote_id"]),
                quote_text=str(context["quote_text"]),
                parent_post_id=parent_id,
                on_source_receipt_published=(
                    lambda source_sha256,
                    source_attempt_number,
                    parent_id=parent_id,
                    attempt_number=attempt_number: (
                        store.bind_attempt_source_receipt(
                            parent_id,
                            attempt_number=attempt_number,
                            source_receipt_sha256=source_sha256,
                            source_receipt_attempt_number=(
                                source_attempt_number
                            ),
                        )
                    )
                ),
                on_remote_transaction_started=(
                    lambda parent_id=parent_id, attempt_number=attempt_number: (
                        store.mark_remote_transaction_started(
                            parent_id,
                            attempt_number=attempt_number,
                        )
                    )
                ),
                on_definite_non_success=(
                    lambda error,
                    parent_id=parent_id,
                    attempt_number=attempt_number: (
                        _record_context_outbox_failure(
                            store,
                            parent_post_id=parent_id,
                            attempt_number=attempt_number,
                            error=error,
                            failed_epoch=now_epoch(),
                            force_terminal=attempt_number >= store.max_attempts,
                            proved_remote_non_success=True,
                        )
                    )
                ),
                on_confirmed_receipt=(
                    lambda confirmed_receipt,
                    confirmation_epoch,
                    parent_id=parent_id,
                    attempt_number=attempt_number: (
                        store.record_confirmed(
                            parent_id,
                            attempt_number=attempt_number,
                            reply_post_id=confirmed_receipt["reply_post_id"],
                            confirmed_epoch=confirmation_epoch,
                        )
                    )
                ),
            )
        except Exception as exc:
            if type(exc).__name__ == "AmbiguousContextReplyOutcome":
                record_ambiguous_remote_post(
                    {
                        "text": str(getattr(exc, "reply_text", "") or ""),
                        "reply": {
                            "in_reply_to_tweet_id": str(
                                getattr(exc, "parent_post_id", "") or parent_id
                            )
                        },
                    }
                )
                state_name = "ambiguous_remote_outcome"
            elif type(exc).__name__ == "DefiniteContextReplyLocalPersistenceError":
                try:
                    state_name = _record_or_verify_proved_context_failure(
                        store,
                        parent_post_id=parent_id,
                        attempt_number=attempt_number,
                        error=exc,
                        failed_epoch=now_epoch(),
                    )
                except Exception:
                    _HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON = (
                        "proved context outcome persistence failed"
                    )
                    log.critical(
                        "Could not preserve the exact proved historical-context "
                        "failure parent_post_id=%s",
                        parent_id,
                        exc_info=True,
                    )
                    state_name = "outbox_persistence_failed"
            else:
                try:
                    current_obligation = store.get(parent_id)
                    current_context = (
                        current_obligation.get("context_reply")
                        if isinstance(current_obligation, dict)
                        else None
                    )
                    remote_phase_is_unproved = bool(
                        isinstance(current_context, dict)
                        and current_context.get("state")
                        == "context_reply_attempting"
                        and current_context.get("attempt_count")
                        == attempt_number
                        and current_context.get(
                            "remote_transaction_started"
                        )
                        is not False
                    )
                    confirmed_outbox_outcome = bool(
                        isinstance(current_context, dict)
                        and current_context.get("state")
                        == "context_reply_confirmed"
                    )
                    if remote_phase_is_unproved or confirmed_outbox_outcome:
                        # Once the durable phase says transport may have
                        # started, an arbitrary local exception cannot prove a
                        # remote non-success.  The same applies after the
                        # outbox already holds the confirmed identity. Preserve
                        # that exact outcome and the independent receipt/journal
                        # barriers for local or manual reconciliation.
                        state_name = (
                            "confirmed_local_reconciliation_pending"
                            if confirmed_outbox_outcome
                            or inspect_transport_state(
                                journal_path_for_receipt(
                                    HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
                                )
                            ).classification == "confirmed_pair"
                            else "remote_outcome_reconciliation_pending"
                        )
                    else:
                        state_name = _record_context_outbox_failure(
                            store,
                            parent_post_id=parent_id,
                            attempt_number=attempt_number,
                            error=exc,
                            failed_epoch=now_epoch(),
                        )
                except Exception:
                    _HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON = (
                        "outbox outcome persistence failed"
                    )
                    log.critical(
                        "Could not persist historical-context outbox failure "
                        "parent_post_id=%s; main post remains confirmed",
                        parent_id,
                        exc_info=True,
                    )
                    state_name = "outbox_persistence_failed"
            log.error(
                "Confirmed main post remains successful; auxiliary historical-context "
                "reply failed. parent_post_id=%s state=%s error_type=%s error=%s",
                parent_id,
                state_name,
                type(exc).__name__,
                exc,
            )
            log_event(
                "historical_context_obligation",
                status="failed",
                parent_post_id=parent_id,
                attempt_number=attempt_number,
                context_reply_state=state_name,
                error_type=type(exc).__name__,
                reason=str(exc)[:500],
            )
            results.append(
                {
                    "parent_post_id": parent_id,
                    "status": "failed",
                    "context_reply_state": state_name,
                }
            )
            continue

        status = str(result.get("status") or "")
        error_status_code = result.get("error_status_code")
        error_request_method = result.get("error_request_method")
        error_request_path = result.get("error_request_path")
        failed_api_error = None
        if (
            status == "failed"
            and result.get("error_service") == "x"
            and type(error_status_code) is int
        ):
            failed_api_error = ApiError(
                str(result.get("error") or "historical-context X reply failed"),
                service="x",
                status_code=error_status_code,
                reset_epoch=(
                    result.get("error_reset_epoch")
                    if type(result.get("error_reset_epoch")) is int
                    else None
                ),
                # This worker's only X boundary is create_post(reply_to_id=...).
                # Older result schemas do not preserve request metadata, so
                # bind them to that known endpoint rather than guessing from
                # status alone.
                request_method=(
                    error_request_method
                    if isinstance(error_request_method, str)
                    else "POST"
                ),
                request_path=(
                    error_request_path
                    if isinstance(error_request_path, str)
                    else "/2/tweets"
                ),
            )
        if failed_api_error is not None and runtime_state is not None:
            try:
                record_api_error(
                    runtime_state,
                    failed_api_error,
                    "x",
                    scope="write",
                )
                save_state(runtime_state)
            except Exception:
                log.critical(
                    "Could not persist X write-cooldown metadata after a "
                    "historical-context reply failure",
                    exc_info=True,
                )
        try:
            durable_outbox_failure_state = result.get(
                "durable_outbox_failure_state"
            )
            if status == "failed" and type(durable_outbox_failure_state) is str:
                updated = store.get(parent_id)
                updated_context = (
                    updated.get("context_reply")
                    if isinstance(updated, dict)
                    else None
                )
                if (
                    not isinstance(updated_context, dict)
                    or updated_context.get("state")
                    != durable_outbox_failure_state
                ):
                    raise RuntimeError(
                        "durable context outbox failure state changed after "
                        "source-receipt retirement"
                    )
            elif status in {"completed", "already_completed"}:
                reply_post_id = str(result.get("reply_post_id") or "")
                current_after_post = store.get(parent_id)
                current_context_after_post = (
                    current_after_post.get("context_reply")
                    if isinstance(current_after_post, dict)
                    else None
                )
                if (
                    isinstance(current_context_after_post, dict)
                    and current_context_after_post.get("state")
                    == "context_reply_confirmed"
                    and current_context_after_post.get("reply_post_id")
                    == reply_post_id
                    and (
                        status == "already_completed"
                        or confirmed_context_outbox_matches_receipt(
                            current_context_after_post,
                            result,
                        )
                    )
                ):
                    updated = current_after_post
                elif status == "already_completed":
                    updated = store.record_confirmed(
                        parent_id,
                        attempt_number=attempt_number,
                        reply_post_id=reply_post_id,
                        confirmed_epoch=now_epoch(),
                    )
                else:
                    raise RuntimeError(
                        "completed historical-context reply lacks its durable "
                        "outbox confirmation"
                    )
            elif status in {
                "disabled",
                "skipped_no_completed_packet",
                "skipped_future_policy",
                "skipped_unformattable_packet",
            }:
                if (
                    int(context.get("attempt_count") or 0) == 1
                    and "previous_failure" not in context
                ):
                    updated = store.mark_not_required(
                        parent_id,
                        reason=status,
                        decided_epoch=now_epoch(),
                    )
                else:
                    state_name = _record_context_outbox_failure(
                        store,
                        parent_post_id=parent_id,
                        attempt_number=attempt_number,
                        error=f"{status} after a prior retryable context attempt",
                        failed_epoch=now_epoch(),
                        force_terminal=True,
                    )
                    updated = store.get(parent_id)
            elif status == "failed_terminal":
                state_name = _record_context_outbox_failure(
                    store,
                    parent_post_id=parent_id,
                    attempt_number=attempt_number,
                    error=str(result.get("reason") or status),
                    failed_epoch=now_epoch(),
                    force_terminal=True,
                )
                updated = store.get(parent_id)
            else:
                state_name = _record_context_outbox_failure(
                    store,
                    parent_post_id=parent_id,
                    attempt_number=attempt_number,
                    error=str(result.get("error") or f"unexpected context status: {status}"),
                    failed_epoch=now_epoch(),
                    force_terminal=(
                        failed_api_error is not None
                        and api_error_is_reply_not_allowed(failed_api_error)
                    ),
                )
                updated = store.get(parent_id)
        except Exception as exc:
            _HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON = (
                f"{type(exc).__name__}: {exc}"
            )
            log.critical(
                "Could not persist historical-context outbox outcome "
                "parent_post_id=%s; main post remains confirmed",
                parent_id,
                exc_info=True,
            )
            results.append(
                {
                    "parent_post_id": parent_id,
                    "status": "outbox_persistence_failed",
                    "error_type": type(exc).__name__,
                }
            )
            continue
        state_name = str(updated["context_reply"]["state"])
        log_event(
            "historical_context_obligation",
            status=status,
            parent_post_id=parent_id,
            attempt_number=attempt_number,
            context_reply_state=state_name,
        )
        results.append(
            {
                "parent_post_id": parent_id,
                "status": status,
                "context_reply_state": state_name,
            }
        )
    return results


def process_due_historical_context_obligations(
    *,
    parent_post_id: str | None = None,
    limit: int = 1,
    runtime_state: dict | None = None,
) -> list[dict]:
    """Serialise complete context attempts across claims and remote outcomes."""
    from historical_context_outbox import OutboxWorkerBusy

    receipt_reconciliation_only = (
        historical_context_receipt_path_present_or_unsafe()
    )
    reconciliation_parent_id = (
        historical_context_receipt_parent_for_local_reconciliation()
        if receipt_reconciliation_only
        else None
    )
    outbox_reconciliation_parent_id = (
        historical_context_outbox_remote_attempt_parent_for_local_reconciliation()
    )
    outbox_reconciliation_only = outbox_reconciliation_parent_id is not None
    if (
        receipt_reconciliation_only
        and reconciliation_parent_id is not None
        and outbox_reconciliation_parent_id is not None
        and reconciliation_parent_id != outbox_reconciliation_parent_id
    ):
        log.critical(
            "Historical-context receipt and risky outbox rows identify "
            "different parents; no local reconciliation was attempted"
        )
        return []
    if reconciliation_parent_id is None:
        reconciliation_parent_id = outbox_reconciliation_parent_id
    try:
        block_if_ambiguous_remote_post(
            allow_historical_context_receipt_reconciliation=(
                receipt_reconciliation_only
            ),
            allow_historical_context_outbox_reconciliation_parent_id=(
                outbox_reconciliation_parent_id
            ),
        )
    except (
        AmbiguousRemotePostOutcome,
        InvalidRegularPostReceipt,
        InvalidMemePostReceipt,
    ):
        log.critical(
            "Historical-context auxiliary worker deferred by the unresolved "
            "global remote-write ambiguity barrier"
        )
        return []
    if receipt_reconciliation_only or outbox_reconciliation_only:
        if reconciliation_parent_id is None:
            return []
        if (
            parent_post_id is not None
            and str(parent_post_id) != reconciliation_parent_id
        ):
            log.critical(
                "Historical-context local reconciliation was requested for "
                "a different parent; no outbox work was performed. "
                "reconciliation_parent_id=%s requested_parent_id=%s",
                reconciliation_parent_id,
                parent_post_id,
            )
            return []
        parent_post_id = reconciliation_parent_id
    store = historical_context_outbox_store()
    try:
        with store.worker_lock():
            return _process_due_historical_context_obligations(
                store=store,
                parent_post_id=parent_post_id,
                limit=limit,
                runtime_state=runtime_state,
                historical_context_receipt_reconciliation_only=(
                    receipt_reconciliation_only
                ),
                historical_context_outbox_reconciliation_only=(
                    outbox_reconciliation_only
                ),
            )
    except OutboxWorkerBusy:
        log.info(
            "Historical-context auxiliary worker already active; this tick "
            "will not inspect or repeat its claimed work"
        )
        return []


def safely_process_due_historical_context_obligations(
    *,
    parent_post_id: str | None = None,
    limit: int = 1,
    runtime_state: dict | None = None,
) -> list[dict]:
    """Isolate auxiliary context-worker faults from confirmed main-post lanes."""
    global _HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON
    try:
        return process_due_historical_context_obligations(
            parent_post_id=parent_post_id,
            limit=limit,
            runtime_state=runtime_state,
        )
    except Exception as exc:
        _HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON = (
            f"{type(exc).__name__}: {exc}"
        )
        log.critical(
            "Historical-context auxiliary worker failed at its isolation "
            "boundary; confirmed main posts remain successful and unrelated "
            "lanes remain available. parent_post_id=%s error_type=%s error=%s",
            parent_post_id or "",
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        try:
            log_event(
                "historical_context_outbox",
                status="worker_failed_isolated",
                parent_post_id=str(parent_post_id or ""),
                error_type=type(exc).__name__,
                reason=str(exc)[:500],
                main_post_success_preserved=True,
            )
        except Exception:
            log.critical(
                "Could not emit the isolated historical-context worker event",
                exc_info=True,
            )
        return [
            {
                "parent_post_id": str(parent_post_id or ""),
                "status": "worker_failed_isolated",
                "error_type": type(exc).__name__,
            }
        ]


def ensure_reconciled_regular_receipt_schedule_is_future(
    receipt: dict,
    state: dict,
    current: int,
) -> bool:
    """Persist a future quote schedule before completing current-receipt replay."""
    if int(state.get("last_quote_post_epoch", 0) or 0) != int(
        receipt["quote_post_epoch"]
    ):
        return False
    next_quote_epoch = int(state.get("next_quote_post_epoch", 0) or 0)
    if next_quote_epoch > current:
        return False
    log.warning(
        "Reconciled regular receipt has a due quote schedule; deferring the next "
        "regular post before completing receipt replay"
    )
    schedule_next_quote_post(state, current, save=False)
    return True


def reconcile_regular_post_receipt(
    lines_used: set,
    images_used: set,
    state: dict,
    *,
    minimum_next_quote_epoch: int | None = None,
    process_auxiliary_context: bool = True,
) -> bool:
    """Reconcile a durable regular-post receipt without duplicating a remote post."""
    status, receipt = load_regular_post_receipt()
    if status == "absent":
        return False
    if status == "sending":
        log.critical(
            "A regular post was interrupted with an uncertain remote outcome; "
            "leaving its sending receipt as a global manual-reconciliation barrier"
        )
        return False
    if receipt is not None and status in {"pending_schedule", "valid"}:
        verify_lane_transport_source_lineage_if_present(
            receipt_path=REGULAR_POST_RECEIPT_FILE,
            receipt=receipt,
            lane="quote_image",
            post_id=str(receipt.get("post_id") or ""),
        )
    if status == "pending_schedule" and receipt is not None:
        log.warning(
            "Finalising local schedule for already-confirmed regular post_id=%s",
            receipt.get("post_id"),
        )
        receipt = finalize_confirmed_pending_schedule_receipt(receipt)
        status = "valid"
    if status == "invalid" or receipt is None:
        raise InvalidRegularPostReceipt(f"Invalid regular-post receipt blocks main posting: {REGULAR_POST_RECEIPT_FILE}")
    log.warning(
        "Reconciling confirmed regular quote/image post receipt post_id=%s quote_hash=%s image=%s",
        receipt.get("post_id"),
        receipt.get("quote_hash"),
        receipt.get("image_basename"),
    )
    apply_regular_post_receipt(receipt, lines_used, images_used, state)
    if minimum_next_quote_epoch is not None:
        ensure_reconciled_regular_receipt_schedule_is_future(
            receipt,
            state,
            minimum_next_quote_epoch,
        )
    save_regular_post_protected_state(lines_used, images_used, state, durable=True)
    log_confirmed_engagement_experiment_receipt(receipt)
    if engagement_experiment_envelope_from_receipt(receipt) is not None:
        # Emit while the confirmed receipt still exists.  A crash afterwards
        # can safely replay the same structured evidence during reconciliation,
        # whereas removing the receipt first could lose the analytics label.
        log_event(
            "main_post_posted",
            lane="quote_image",
            post_id=receipt["post_id"],
            line_no=receipt.get("line_no"),
            image_no=receipt.get("image_no"),
            image_basename=receipt.get("image_basename"),
            quote_hash=receipt.get("quote_hash"),
            **engagement_experiment_event_fields(receipt),
        )
    enqueue_historical_context_obligation(receipt)
    retire_lane_transport_journal_if_present(
        receipt_path=REGULAR_POST_RECEIPT_FILE,
        receipt=receipt,
        lane="quote_image",
        post_id=str(receipt["post_id"]),
    )
    remove_regular_post_receipt(receipt)
    publish_pending_engagement_question_notification(state)
    emit_account_root_posted(
        lane="quote_image",
        post_id=receipt["post_id"],
        public_text=receipt.get("text"),
        quote_id=receipt.get("quote_hash"),
        quote_text=receipt.get("quote_text", receipt.get("text")),
    )
    log.info(
        "Confirmed main post reconciliation is complete; auxiliary context "
        "obligation is independent. post_id=%s",
        receipt["post_id"],
    )
    if process_auxiliary_context:
        safely_process_due_historical_context_obligations(
            parent_post_id=str(receipt["post_id"]),
            runtime_state=state,
        )
    return True


def block_if_unresolved_regular_post_receipt() -> None:
    """Refuse a new regular post while a prior receipt is unresolved."""
    status, _receipt = load_regular_post_receipt()
    if status == "absent":
        return
    if status == "invalid":
        raise InvalidRegularPostReceipt(f"Invalid regular-post receipt blocks main posting: {REGULAR_POST_RECEIPT_FILE}")
    raise UnresolvedRegularPostReceipt(f"Unresolved regular-post receipt must be reconciled before another main post: {REGULAR_POST_RECEIPT_FILE}")


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
    if both_main_post_receipts_exist():
        log.critical(
            "Both regular and meme confirmed-post receipts exist; refusing automatic reconciliation until manually inspected: %s %s",
            REGULAR_POST_RECEIPT_FILE,
            MEME_POST_RECEIPT_FILE,
        )
        raise InvalidRegularPostReceipt("Both main-post receipts exist; manual recovery required")
    return {
        "regular": reconcile_regular_post_receipt(
            lines_used,
            images_used,
            state,
            minimum_next_quote_epoch=minimum_next_quote_epoch,
            process_auxiliary_context=process_auxiliary_context,
        ),
        "meme": reconcile_meme_post_receipt(state),
    }


def reconcile_startup_main_post_receipts(
    lines_used: set,
    images_used: set,
    state: dict,
    current: int,
) -> dict[str, bool]:
    """Reconcile main receipts unless a global maintenance pause is active."""
    if global_remote_writes_paused():
        log.warning(
            "Global runtime control pause is active; leaving main-post receipts "
            "untouched during startup"
        )
        return {"regular": False, "meme": False}
    return reconcile_main_post_receipts(
        lines_used,
        images_used,
        state,
        minimum_next_quote_epoch=current,
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

    result = {
        "historical_context": False,
        "conversational_reply": False,
        "regular": False,
        "meme": False,
    }
    if global_remote_writes_paused():
        return result
    if (
        not remote_write_safety_protocol_is_active()
        or remote_write_safety_incident_is_latched()
        or remote_write_safety_marker_path_present_or_unsafe()
    ):
        return result

    receipt_paths = (
        REGULAR_POST_RECEIPT_FILE,
        MEME_POST_RECEIPT_FILE,
        CONFIRMED_REPLY_RECEIPT_FILE,
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
    )
    present = [
        path for path in receipt_paths if receipt_namespace_entry_exists(path)
    ]
    if not present:
        # A confirmed historical-context receipt can have completed exact
        # history and retired its source/journal immediately before a crash,
        # while the independently durable outbox still says that the remote
        # transaction was attempting.  That outbox row is correctly a global
        # barrier, so the ordinary worker below the barrier is unreachable.
        # Permit only the sole risky parent to run the existing local-only
        # reconciler here.  It requires exact source-bound completed/failed
        # history and repeats no remote work; missing, stale, multiple or
        # conflicting evidence raises and leaves the global barrier intact.
        parent_id = (
            historical_context_outbox_remote_attempt_parent_for_local_reconciliation()
        )
        if parent_id is not None:
            outbox_store = historical_context_outbox_store()
            with outbox_store.worker_lock():
                obligation = outbox_store.get(parent_id)
                if not isinstance(obligation, dict):
                    raise RuntimeError(
                        "risky historical-context outbox parent disappeared "
                        "before local reconciliation"
                    )
                recovered = recover_interrupted_historical_context_attempt(
                    outbox_store,
                    obligation,
                    recovered_epoch=now_epoch(),
                )
            log_event("historical_context_obligation", **recovered)
            result["historical_context"] = True
            return result
    if len(present) != 1:
        return result

    owning_path = present[0]
    journal_path = journal_path_for_receipt(owning_path)
    journal_state = inspect_transport_state(journal_path)
    needs_transport_promotion = False
    legacy_conversational_transport_promotion = False
    historical_store = None
    historical_sending_receipt = None
    historical_sending_receipt_bytes = None
    historical_confirmed_receipt = None
    historical_legacy_confirmed_receipt = None
    historical_outbox_store = None
    historical_outbox_obligation = None
    historical_outbox_context = None
    historical_loaded_receipt = None
    if owning_path == REGULAR_POST_RECEIPT_FILE:
        status, source = load_regular_post_receipt()
        needs_transport_promotion = bool(
            status == "sending"
            and isinstance(source, dict)
            and source.get("lifecycle_state") == "attempting"
        )
    elif owning_path == MEME_POST_RECEIPT_FILE:
        status, source = load_meme_post_receipt()
        needs_transport_promotion = bool(
            status == "sending"
            and isinstance(source, dict)
            and source.get("lifecycle_state") == "attempting"
        )
    elif owning_path == CONFIRMED_REPLY_RECEIPT_FILE:
        status, _source = load_confirmed_reply_receipt()
        needs_transport_promotion = status in {"sending", "legacy_sending"}
        legacy_conversational_transport_promotion = status == "legacy_sending"
    elif owning_path == HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE:
        from historical_context_formatter import HistoricalContextReplyStore

        historical_store = historical_context_reply_store()
        loaded = historical_store._load_receipt_safely()
        historical_loaded_receipt = loaded
        needs_transport_promotion = bool(
            loaded is not None
            and HistoricalContextReplyStore._valid_sending_receipt(loaded[0])
        )
        if needs_transport_promotion:
            historical_sending_receipt = loaded[0]
            historical_sending_receipt_bytes = loaded[1]
        elif (
            loaded is not None
            and HistoricalContextReplyStore._valid_receipt(loaded[0])
            and loaded[0].get("lifecycle_state") == "confirmed"
            and "source_receipt_sha256" in loaded[0]
        ):
            historical_confirmed_receipt = loaded[0]
            historical_sending_receipt = (
                HistoricalContextReplyStore.sending_receipt_from_confirmed(
                    loaded[0]
                )
            )
            historical_sending_receipt_bytes = (
                HistoricalContextReplyStore.source_receipt_bytes_from_confirmed(
                    loaded[0]
                )
            )
        elif (
            loaded is not None
            and HistoricalContextReplyStore._valid_receipt(loaded[0])
        ):
            historical_legacy_confirmed_receipt = loaded[0]
    if historical_legacy_confirmed_receipt is not None:
        if journal_state.classification != "clear":
            raise RuntimeError(
                "legacy confirmed historical-context receipt conflicts with "
                "an independent transport journal"
            )
        parent_id = str(
            historical_legacy_confirmed_receipt["parent_post_id"]
        )
        historical_outbox_store = historical_context_outbox_store()
        historical_outbox_obligation = historical_outbox_store.get(parent_id)
        historical_outbox_context = (
            historical_outbox_obligation.get("context_reply")
            if isinstance(historical_outbox_obligation, dict)
            else None
        )
        if not isinstance(historical_outbox_obligation, dict):
            raise RuntimeError(
                "confirmed historical-context receipt has no matching "
                "outbox obligation"
            )
        if (
            not isinstance(historical_outbox_context, dict)
            or historical_outbox_context.get("quote_id")
            != historical_legacy_confirmed_receipt["quote_id"]
        ):
            raise RuntimeError(
                "confirmed historical-context receipt conflicts with its "
                "outbox identity"
            )
        if historical_outbox_context.get("state") == (
            "context_reply_attempting"
        ):
            if {
                "source_receipt_sha256",
                "source_receipt_attempt_number",
            } & set(historical_outbox_context):
                raise RuntimeError(
                    "legacy confirmed historical-context receipt conflicts "
                    "with a source-bound attempting outbox"
                )
            if historical_outbox_context.get("attempt_count") != (
                historical_legacy_confirmed_receipt.get("attempt_number")
            ):
                raise RuntimeError(
                    "legacy confirmed historical-context receipt conflicts "
                    "with its outbox attempt"
                )
            historical_outbox_store.record_confirmed(
                parent_id,
                attempt_number=int(
                    historical_outbox_context["attempt_count"]
                ),
                reply_post_id=historical_legacy_confirmed_receipt[
                    "reply_post_id"
                ],
                confirmed_epoch=int(
                    historical_legacy_confirmed_receipt["reply_epoch"]
                ),
            )
        elif not confirmed_context_outbox_matches_receipt(
            historical_outbox_context,
            historical_legacy_confirmed_receipt,
        ):
            raise RuntimeError(
                "confirmed historical-context receipt conflicts with its "
                "durable outbox outcome"
            )
    if (
        owning_path == HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
        and historical_sending_receipt is not None
    ):
        if (
            historical_store is None
            or historical_sending_receipt is None
            or historical_sending_receipt_bytes is None
        ):
            raise RuntimeError(
                "historical-context local recovery lost its source receipt"
            )
        parent_id = str(historical_sending_receipt["parent_post_id"])
        historical_outbox_store = historical_context_outbox_store()
        historical_outbox_obligation = historical_outbox_store.get(parent_id)
        historical_outbox_context = (
            historical_outbox_obligation.get("context_reply")
            if isinstance(historical_outbox_obligation, dict)
            else None
        )
        if not isinstance(historical_outbox_obligation, dict):
            # A confirmed receipt is durable proof that the remote operation
            # succeeded.  Without its matching outbox obligation there is no
            # authority to complete local history or retire either transport
            # barrier.  In particular, ``reconcile_receipt_disposition`` is a
            # mutating operation for confirmed receipts, so fail before
            # invoking it and preserve every byte for operator reconciliation.
            if historical_confirmed_receipt is not None:
                raise RuntimeError(
                    "confirmed historical-context receipt has no matching "
                    "outbox obligation"
                )
            disposition = historical_store.reconcile_receipt_disposition(
                retain_definite_failure_receipt=True,
            )
            if disposition != "definite_failure":
                raise RuntimeError(
                    "historical-context sending receipt had an unexpected "
                    f"local disposition: {disposition}"
                )
            raise RuntimeError(
                "historical-context sending receipt has no matching outbox "
                "obligation"
            )
        if (
            not isinstance(historical_outbox_context, dict)
            or historical_outbox_context.get("quote_id")
            != historical_sending_receipt["quote_id"]
        ):
            raise RuntimeError(
                "historical-context sending receipt conflicts with its "
                "outbox identity"
            )
        if journal_state.classification == "confirmed_pair":
            confirmed_details = inspect_confirmed_transport_transaction(
                journal_path
            )
            if (
                confirmed_details.lane != "historical_context_reply"
                or confirmed_details.source_receipt_sha256
                != hashlib.sha256(historical_sending_receipt_bytes).hexdigest()
                or (
                    historical_confirmed_receipt is not None
                    and confirmed_details.post_id
                    != historical_confirmed_receipt["reply_post_id"]
                )
            ):
                raise RuntimeError(
                    "confirmed historical-context transport conflicts with "
                    "its exact receipt lineage"
                )
            if historical_outbox_context.get("state") == (
                "context_reply_attempting"
            ):
                if (
                    historical_outbox_context.get(
                        "remote_transaction_started"
                    )
                    is not True
                    or historical_outbox_context.get(
                        "source_receipt_sha256"
                    )
                    != hashlib.sha256(
                        historical_sending_receipt_bytes
                    ).hexdigest()
                    or historical_outbox_context.get(
                        "source_receipt_attempt_number"
                    )
                    != historical_sending_receipt["attempt_number"]
                ):
                    raise RuntimeError(
                        "confirmed historical-context transport conflicts "
                        "with its exact attempting outbox source"
                    )
                if historical_confirmed_receipt is not None:
                    historical_outbox_store.record_confirmed(
                        str(historical_sending_receipt["parent_post_id"]),
                        attempt_number=int(
                            historical_outbox_context["attempt_count"]
                        ),
                        reply_post_id=confirmed_details.post_id,
                        confirmed_epoch=confirmed_details.confirmation_epoch,
                    )
                    historical_outbox_context = (
                        historical_outbox_store.get(parent_id)[
                            "context_reply"
                        ]
                    )
            elif not confirmed_context_outbox_matches_receipt(
                historical_outbox_context,
                (
                    historical_confirmed_receipt
                    if historical_confirmed_receipt is not None
                    else {
                        "quote_id": historical_sending_receipt["quote_id"],
                        "reply_post_id": confirmed_details.post_id,
                        "source_receipt_sha256": hashlib.sha256(
                            historical_sending_receipt_bytes
                        ).hexdigest(),
                        "attempt_number": historical_sending_receipt[
                            "attempt_number"
                        ],
                    }
                ),
            ):
                raise RuntimeError(
                    "confirmed historical-context transport conflicts with "
                    "its durable outbox outcome"
                )
        elif historical_confirmed_receipt is not None:
            if not confirmed_context_outbox_matches_receipt(
                historical_outbox_context,
                historical_confirmed_receipt,
            ):
                raise RuntimeError(
                    "confirmed historical-context receipt conflicts with its "
                    "durable outbox outcome"
                )
            # The journal may already have been retired after durable remote
            # confirmation.  The exact lineage-bearing receipt and terminal
            # outbox row jointly authorise the ordinary history/receipt
            # reconciliation below; neither parent/reply identity alone does.
        elif historical_outbox_context.get("state") == "context_reply_attempting":
            with historical_outbox_store.worker_lock():
                current_obligation = historical_outbox_store.get(parent_id)
                if current_obligation != historical_outbox_obligation:
                    raise RuntimeError(
                        "historical-context outbox changed before local "
                        "recovery"
                    )
                recovered = recover_interrupted_historical_context_attempt(
                    historical_outbox_store,
                    current_obligation,
                    recovered_epoch=now_epoch(),
                    receipt_was_observed=True,
                )
            log_event("historical_context_obligation", **recovered)
            result["historical_context"] = True
            return result
        elif historical_outbox_context.get("state") not in {
            "context_reply_failed_retryable",
            "context_reply_failed_terminal",
        }:
            raise RuntimeError(
                "historical-context failure receipt conflicts with its "
                "outbox state"
            )
        else:
            historical_store.ensure_proved_failure_history_from_outbox(
                historical_outbox_context
            )
            disposition = historical_store.reconcile_receipt_disposition()
            if disposition != "definite_failure":
                raise RuntimeError(
                    "historical-context sending receipt has no local recovery "
                    "disposition"
                )
            result["historical_context"] = True
            return result
    if journal_state.classification == "confirmed_pair" and needs_transport_promotion:
        source_validator = (
            _legacy_conversational_transport_source_semantic_validator
            if legacy_conversational_transport_promotion
            else transport_source_semantic_validator
        )
        recovery = bind_confirmed_transport_source(
            journal_path=journal_path,
            receipt_path=owning_path,
            validator_id=TRANSPORT_SOURCE_VALIDATOR_ID,
            validator=source_validator,
        )
        source_receipt = recovery.source_binding.receipt_document
        details = recovery.details
        if details.lane in {"quote_image", "daily_meme"}:
            image_summary = (
                str(source_receipt["recovery_plan"].get("image_summary") or "")
                if details.lane == "daily_meme"
                else ""
            )
            promote_main_post_attempt_to_confirmed_pending_schedule(
                source_receipt,
                post_id=details.post_id,
                confirmation_epoch=confirmation_epoch_for_main_attempt(
                    source_receipt,
                    details.confirmation_epoch,
                ),
                image_summary=image_summary,
            )
        elif details.lane == "conversational_reply":
            confirmation_epoch = _reply_confirmation_epoch_after_remote_success(
                source_receipt,
                details.confirmation_epoch,
            )
            if legacy_conversational_transport_promotion:
                _promote_legacy_sending_reply_receipt_from_confirmed_transport(
                    source_receipt,
                    reply_post_id=details.post_id,
                    confirmation_epoch=confirmation_epoch,
                )
            else:
                promote_sending_reply_receipt(
                    source_receipt,
                    reply_post_id=details.post_id,
                    confirmation_epoch=confirmation_epoch,
                )
        elif details.lane == "historical_context_reply":
            from historical_context_formatter import HistoricalContextReplyStore

            if (
                historical_outbox_store is None
                or not isinstance(historical_outbox_context, dict)
            ):
                raise TransportJournalError(
                    "confirmed historical-context transport has no exact "
                    "outbox authority"
                )
            historical_context_reply_store().promote_sending_receipt_from_confirmed_transport(
                source_receipt,
                reply_post_id=details.post_id,
                confirmation_epoch=details.confirmation_epoch,
                require_confirmed_transport=True,
            )
            historical_outbox_store.record_confirmed(
                str(source_receipt["parent_post_id"]),
                attempt_number=int(
                    historical_outbox_context["attempt_count"]
                ),
                reply_post_id=details.post_id,
                confirmed_epoch=details.confirmation_epoch,
            )
        else:
            raise TransportJournalError(
                "confirmed journal has no supported recovery lane"
            )

    if owning_path == HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE:
        from historical_context_formatter import HistoricalContextReplyStore

        store = historical_context_reply_store()
        if (
            historical_loaded_receipt is not None
            and (
                historical_confirmed_receipt is not None
                or historical_legacy_confirmed_receipt is not None
            )
        ):
            result["historical_context"] = (
                store.reconcile_receipt_disposition(
                    preloaded_receipt=historical_loaded_receipt,
                )
                == "confirmed"
            )
        else:
            result["historical_context"] = (
                store.reconcile_confirmed_receipt_if_present()
            )
        if result["historical_context"]:
            emit_historical_context_store_observation(store, parent_id)
        return result

    if owning_path == CONFIRMED_REPLY_RECEIPT_FILE:
        status, _receipt = load_confirmed_reply_receipt()
        if status == "valid":
            result["conversational_reply"] = reconcile_confirmed_reply_receipt(
                state
            )
        return result

    regular_status, _regular = load_regular_post_receipt()
    meme_status, _meme = load_meme_post_receipt()
    if regular_status not in {"pending_schedule", "valid"} and meme_status not in {
        "pending_schedule",
        "valid",
    }:
        return result
    main_result = reconcile_main_post_receipts(
        lines_used,
        images_used,
        state,
        minimum_next_quote_epoch=(now_epoch() if current is None else current),
        process_auxiliary_context=False,
    )
    result["regular"] = bool(main_result["regular"])
    result["meme"] = bool(main_result["meme"])
    return result


def image_used_history_has_legacy_indices(images_used: set) -> bool:
    """Return whether image used history has legacy indices."""
    return any(re.fullmatch(r"-?\d+", str(item)) for item in images_used)


def image_corpus_verified_for_legacy_migration(images: list[str], image_analysis: dict | None) -> bool:
    """Return the image corpus verified for legacy migration."""
    if ENABLE_GENERATED_IMAGE_POOL:
        return False
    if not isinstance(image_analysis, dict):
        return False
    expected = set(str(name) for name in (image_analysis.get("path_index") or {}).keys())
    visible = {Path(path).name for path in images}
    return bool(expected) and visible == expected


def normalise_image_used_basenames(images_used: set, images: list[str], image_analysis: dict | None = None) -> tuple[set, bool]:
    """Normalise image used basenames."""
    basenames = [Path(path).name for path in images]
    migrated: set = set()
    changed = False
    can_migrate_indices = image_corpus_verified_for_legacy_migration(images, image_analysis)

    for item in images_used:
        item_text = str(item)
        if not re.fullmatch(r"-?\d+", item_text):
            migrated.add(item_text)
            continue
        if not can_migrate_indices:
            migrated.add(item)
            continue
        try:
            index = int(item)
        except Exception:
            migrated.add(item)
            continue
        if 0 <= index < len(basenames):
            migrated.add(basenames[index])
            changed = True
        else:
            migrated.add(item)

    if {str(item) for item in migrated} != {str(item) for item in images_used}:
        changed = True
    return migrated, changed


def load_image_used_basenames(images: list[str]) -> set:
    """Load image used basenames."""
    raw = load_used_set(IMAGES_USED_FILE, legacy_pickle_path=IMAGE_PICKLE_FILE)
    image_analysis = load_image_analysis()
    normalised, changed = normalise_image_used_basenames(raw, images, image_analysis)
    if image_used_history_has_legacy_indices(normalised) and images:
        log.critical(
            "Image used-history contains legacy integer entries but current image corpus is not verified complete; refusing destructive migration"
        )
        return normalised
    if images and (changed or IMAGES_USED_FILE.exists()):
        save_image_used_basenames(IMAGES_USED_FILE, normalised)
        log.info("Image used-history normalised to %d basename(s)", len(normalised))
    elif not images and changed:
        log.warning("Image scan is empty; preserving image used-history without rewriting %s", IMAGES_USED_FILE)
    return normalised


# Keep the public helper API here; resolve configuration and sibling helpers
# at each call so runtime overrides and root monkeypatches remain effective.
def normalise_tag(value: object) -> str:
    """Normalise tag."""
    return _image_scoring.normalise_tag(
        value, re_sub=re.sub,
    )


as_string_list = _image_scoring.as_string_list


def meaningful_tokens(value: object) -> set[str]:
    """Return the meaningful tokens."""
    return _image_scoring.meaningful_tokens(
        value,
        re_findall=re.findall,
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
    return _image_scoring.image_text_corpus(
        image_analysis, as_string_list=as_string_list,
    )


def build_image_topic_idf(image_analysis: dict | None) -> dict[str, float]:
    """Build image topic idf."""
    return _image_scoring.build_image_topic_idf(
        image_analysis,
        as_string_list=as_string_list,
        normalise_tag=normalise_tag,
    )


visual_energy_score = _image_scoring.visual_energy_score


def image_is_out_of_season(image_analysis: dict, today_mm_dd: str) -> bool:
    """Return whether image is out of season."""
    return _image_scoring.image_is_out_of_season(
        image_analysis, today_mm_dd,
        normalise_tag=normalise_tag,
        as_string_list=as_string_list,
        mm_dd_in_window=mm_dd_in_window,
    )


def score_image_for_quote(quote_analysis: dict | None, image_analysis: dict | None, idf: dict[str, float] | None = None) -> tuple[float, dict[str, float], bool]:
    """Calculate the production image score and component breakdown for a quotation."""
    return _image_scoring.score_image_for_quote(
        quote_analysis, image_analysis, idf,
        normalise_tag=normalise_tag,
        as_string_list=as_string_list,
        visual_energy_score=visual_energy_score,
        image_text_corpus=image_text_corpus,
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
GENERATED_IDENTITY_AUDIT_KIND = "generated_image_identity_dependence_audit"
GENERATED_IDENTITY_AUDIT_SCHEMA_VERSION = 1
GENERATED_IDENTITY_POLICIES = {"unrestricted", "small_penalty", "strong_penalty", "origin_quote_only"}
GENERATED_IDENTITY_DEPENDENCE_VALUES = {"none", "low", "medium", "high", "essential"}
_GENERATED_IDENTITY_AUDIT_CACHE: dict[str, dict] = {}


original_editorial_numeric = _original_editorial.original_editorial_numeric


def original_editorial_concepts(value: object) -> set[str]:
    """Return the original editorial concepts."""
    return _original_editorial.original_editorial_concepts(
        value,
        normalise_tag=normalise_tag,
        original_editorial_concepts=original_editorial_concepts,
        synonym_to_concept=_ORIGINAL_EDITORIAL_SYNONYM_TO_CONCEPT,
        affinity_concepts=ORIGINAL_EDITORIAL_AFFINITY_CONCEPTS,
    )


def original_editorial_quote_concepts(quote_analysis: dict | None) -> set[str]:
    """Return the original editorial quote concepts."""
    return _original_editorial.original_editorial_quote_concepts(
        quote_analysis,
        as_string_list=as_string_list,
        original_editorial_concepts=original_editorial_concepts,
    )


def original_editorial_image_concepts(editorial: dict | None) -> set[str]:
    """Return the original editorial image concepts."""
    return _original_editorial.original_editorial_image_concepts(
        editorial,
        as_string_list=as_string_list,
        original_editorial_concepts=original_editorial_concepts,
    )


def original_editorial_avoid_concepts(editorial: dict | None) -> set[str]:
    """Return the original editorial avoid concepts."""
    return _original_editorial.original_editorial_avoid_concepts(
        editorial,
        as_string_list=as_string_list,
        original_editorial_concepts=original_editorial_concepts,
    )


def original_editorial_quote_dimension_profile(quote_analysis: dict | None) -> dict[str, float]:
    """Return the original editorial quote dimension profile."""
    return _original_editorial.original_editorial_quote_dimension_profile(
        quote_analysis,
        dimensions=ORIGINAL_EDITORIAL_DIMENSIONS,
        normalise_tag=normalise_tag,
        as_string_list=as_string_list,
        original_editorial_quote_concepts=original_editorial_quote_concepts,
    )


def validate_original_editorial_item(basename: str, entry: dict, image_by_name: dict[str, str]) -> dict:
    """Validate original editorial item."""
    return _original_editorial.validate_original_editorial_item(
        basename, entry, image_by_name,
        dimensions=ORIGINAL_EDITORIAL_DIMENSIONS,
        generated_image_origin_quote_hash=generated_image_origin_quote_hash,
        current_image_sha256=current_image_sha256,
        original_editorial_numeric=original_editorial_numeric,
    )


def load_original_editorial_analysis() -> dict[str, dict]:
    """Load original editorial analysis."""
    return _original_editorial.load_original_editorial_analysis(
        analysis_file=ORIGINAL_EDITORIAL_ANALYSIS_FILE,
        analysis_cache=_ORIGINAL_EDITORIAL_ANALYSIS_CACHE,
        analysis_kind=ORIGINAL_EDITORIAL_ANALYSIS_KIND,
        schema_version=ORIGINAL_EDITORIAL_SCHEMA_VERSION,
        current_image_paths=current_image_paths,
        validate_original_editorial_item=validate_original_editorial_item,
        generated_image_origin_quote_hash=generated_image_origin_quote_hash,
    )


def validate_original_editorial_shadow_startup() -> None:
    """Validate original editorial shadow startup."""
    return _original_editorial.validate_original_editorial_shadow_startup(
        enabled=ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING,
        load_original_editorial_analysis=load_original_editorial_analysis,
        analysis_file=ORIGINAL_EDITORIAL_ANALYSIS_FILE,
        default_weight=ORIGINAL_EDITORIAL_SHADOW_WEIGHT,
        default_max_abs_adjustment=ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT,
        log=log,
    )


generated_identity_numeric = _generated_identity.generated_identity_numeric


def configured_generated_image_paths() -> dict[str, Path]:
    """Return the configured generated image paths."""
    return _asset_metadata.configured_generated_image_paths(
        generated_image_dir=GENERATED_IMAGE_DIR,
        generated_image_glob=GENERATED_IMAGE_GLOB,
        glob=glob,
        path_is_same_or_child=path_is_same_or_child,
        generated_image_origin_quote_hash=generated_image_origin_quote_hash,
    )


def validate_generated_identity_audit_item(basename: str, item: object, image_by_name: dict[str, Path]) -> dict:
    """Validate generated identity audit item."""
    return _generated_identity.validate_generated_identity_audit_item(
        basename, item, image_by_name,
        generated_image_origin_quote_hash=generated_image_origin_quote_hash,
        file_sha256=file_sha256,
        policies=GENERATED_IDENTITY_POLICIES,
        dependence_values=GENERATED_IDENTITY_DEPENDENCE_VALUES,
        generated_identity_numeric=generated_identity_numeric,
    )


def load_generated_identity_audit() -> dict[str, dict]:
    """Load generated identity audit."""
    return _generated_identity.load_generated_identity_audit(
        audit_file=GENERATED_IDENTITY_AUDIT_FILE,
        audit_cache=_GENERATED_IDENTITY_AUDIT_CACHE,
        schema_version=GENERATED_IDENTITY_AUDIT_SCHEMA_VERSION,
        audit_kind=GENERATED_IDENTITY_AUDIT_KIND,
        configured_generated_image_paths=configured_generated_image_paths,
        validate_generated_identity_audit_item=validate_generated_identity_audit_item,
    )


def validate_generated_identity_shadow_startup() -> None:
    """Validate generated identity shadow startup."""
    return _generated_identity.validate_generated_identity_shadow_startup(
        pool_enabled=ENABLE_GENERATED_IMAGE_POOL,
        shadow_enabled=ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING,
        scoring_enabled=ENABLE_GENERATED_IDENTITY_POLICY_SCORING,
        load_generated_identity_audit=load_generated_identity_audit,
        audit_file=GENERATED_IDENTITY_AUDIT_FILE,
        small_penalty=GENERATED_IDENTITY_SHADOW_SMALL_PENALTY,
        strong_penalty=GENERATED_IDENTITY_SHADOW_STRONG_PENALTY,
        log=log,
    )


def generated_identity_policy_scoring_active() -> bool:
    """Return whether generated candidates can receive production policy scoring."""
    return bool(ENABLE_GENERATED_IMAGE_POOL and ENABLE_GENERATED_IDENTITY_POLICY_SCORING)


def generated_identity_policy_shadow_active() -> bool:
    """Return whether generated candidates can receive observational policy scoring."""
    return bool(
        ENABLE_GENERATED_IMAGE_POOL
        and ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING
    )


def generated_identity_candidate_shadow_row(candidate: dict, audit_by_basename: dict[str, dict]) -> dict:
    """Return the generated identity candidate shadow row."""
    return _generated_identity.generated_identity_candidate_shadow_row(
        candidate, audit_by_basename,
        small_penalty=GENERATED_IDENTITY_SHADOW_SMALL_PENALTY,
        strong_penalty=GENERATED_IDENTITY_SHADOW_STRONG_PENALTY,
    )


def generated_identity_policy_shadow_result(
    quote_choice: dict,
    production_choice: dict,
    scored_candidates: list[dict],
    *,
    selection_phase: str,
    audit_by_basename: dict[str, dict] | None = None,
    selection_rng_state: object | None = None,
) -> dict:
    """Evaluate generated-image identity policy without changing selection."""
    return _generated_identity.generated_identity_policy_shadow_result(
        quote_choice, production_choice, scored_candidates,
        selection_phase=selection_phase,
        audit_by_basename=audit_by_basename,
        selection_rng_state=selection_rng_state,
        load_generated_identity_audit=load_generated_identity_audit,
        generated_identity_candidate_shadow_row=generated_identity_candidate_shadow_row,
        _choice_with_random_state=_choice_with_random_state,
        small_penalty=GENERATED_IDENTITY_SHADOW_SMALL_PENALTY,
        strong_penalty=GENERATED_IDENTITY_SHADOW_STRONG_PENALTY,
    )


def generated_identity_policy_selection(
    scored_candidates: list[dict],
    *,
    audit_by_basename: dict[str, dict] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Return policy rows and eligible candidates without mutating input rows."""
    return _generated_identity.generated_identity_policy_selection(
        scored_candidates,
        audit_by_basename=audit_by_basename,
        load_generated_identity_audit=load_generated_identity_audit,
        generated_identity_candidate_shadow_row=generated_identity_candidate_shadow_row,
    )


_choice_with_random_state = _generated_identity._choice_with_random_state


def generated_identity_policy_applied_result(
    quote_choice: dict,
    production_winner: dict,
    baseline_candidates: list[dict],
    policy_rows: list[dict],
    policy_tie_count: int,
    *,
    selection_phase: str,
    selection_rng_state: object,
) -> dict:
    """Apply the enabled generated-image identity policy to scored candidates."""
    return _generated_identity.generated_identity_policy_applied_result(
        quote_choice, production_winner, baseline_candidates, policy_rows, policy_tie_count,
        selection_phase=selection_phase,
        selection_rng_state=selection_rng_state,
        _choice_with_random_state=_choice_with_random_state,
    )


def log_generated_identity_policy_applied_result(payload: dict) -> None:
    """Log generated identity policy applied result."""
    return _generated_identity.log_generated_identity_policy_applied_result(
        payload,
        log=log,
    )


def log_generated_identity_policy_shadow_result(
    quote_choice: dict,
    production_choice: dict,
    scored_candidates: list[dict],
    *,
    selection_phase: str,
    selection_rng_state: object | None = None,
) -> None:
    """Log generated identity policy shadow result."""
    return _generated_identity.log_generated_identity_policy_shadow_result(
        quote_choice, production_choice, scored_candidates,
        selection_phase=selection_phase,
        selection_rng_state=selection_rng_state,
        generated_identity_policy_shadow_active=generated_identity_policy_shadow_active,
        generated_identity_policy_shadow_result=generated_identity_policy_shadow_result,
        log=log,
    )


def original_editorial_shadow_score(
    quote_analysis: dict | None,
    editorial: dict | None,
    *,
    weight: float | None = None,
    max_abs_adjustment: float | None = None,
) -> tuple[float, dict]:
    """Calculate the observational editorial adjustment for one image."""
    return _original_editorial.original_editorial_shadow_score(
        quote_analysis, editorial,
        weight=weight,
        max_abs_adjustment=max_abs_adjustment,
        default_weight=ORIGINAL_EDITORIAL_SHADOW_WEIGHT,
        default_max_abs_adjustment=ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT,
        original_editorial_quote_dimension_profile=original_editorial_quote_dimension_profile,
        original_editorial_numeric=original_editorial_numeric,
        original_editorial_quote_concepts=original_editorial_quote_concepts,
        original_editorial_image_concepts=original_editorial_image_concepts,
        original_editorial_avoid_concepts=original_editorial_avoid_concepts,
    )


def original_editorial_shadow_result(
    quote_choice: dict,
    production_choice: dict,
    scored_candidates: list[dict],
    *,
    selection_phase: str,
) -> tuple[dict | None, dict | None]:
    """Return the existing editorial comparison and its preferred original."""
    return _original_editorial.original_editorial_shadow_result(
        quote_choice, production_choice, scored_candidates,
        selection_phase=selection_phase,
        load_original_editorial_analysis=load_original_editorial_analysis,
        original_editorial_shadow_score=original_editorial_shadow_score,
        default_weight=ORIGINAL_EDITORIAL_SHADOW_WEIGHT,
        default_max_abs_adjustment=ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT,
    )


def log_original_editorial_shadow_result(
    quote_choice: dict,
    production_choice: dict,
    scored_candidates: list[dict],
    *,
    selection_phase: str,
) -> None:
    """Log an original-editorial comparison without mutating candidates."""
    return _original_editorial.log_original_editorial_shadow_result(
        quote_choice, production_choice, scored_candidates,
        selection_phase=selection_phase,
        enabled=ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING,
        original_editorial_shadow_result=original_editorial_shadow_result,
        log=log,
    )


def apply_original_editorial_selection(
    quote_choice: dict,
    baseline_choice: dict,
    scored_candidates: list[dict],
    *,
    selection_phase: str,
) -> dict:
    """Replace an original baseline winner with the existing editorial winner."""
    return _original_editorial.apply_original_editorial_selection(
        quote_choice, baseline_choice, scored_candidates,
        selection_phase=selection_phase,
        enabled=ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING,
        original_editorial_shadow_result=original_editorial_shadow_result,
        log=log,
    )


def concise_components(components: dict[str, float]) -> str:
    """Return the concise components."""
    return ", ".join(f"{key}={value:.1f}" for key, value in sorted(components.items()))


_ENGAGEMENT_QUESTION_LAST_LOADED_PLAN_SHA256: str | None = None
_ENGAGEMENT_QUESTION_LAST_NOTIFICATION_FAILURE_POST_ID: str | None = None
ENGAGEMENT_QUESTION_NOTIFICATION_REPLACEMENT_MIN_AGE_SECONDS = 60


def configured_engagement_question_path(raw_path: str) -> Path:
    """Resolve one deployment-local experiment path without writing it."""

    path = Path(raw_path)
    return path if path.is_absolute() else BASE_DIR / path


def current_exact_quote_text_by_sha256() -> dict[str, str]:
    """Load exact quotation bodies without production-text normalisation."""

    source = LINES_FILE.read_bytes().decode("utf-8", errors="strict")
    if "\r" in source:
        raise RuntimeError(
            "engagement experiment requires an LF-only canonical quotation source"
        )
    result: dict[str, str] = {}
    for exact_text in source.split("\n"):
        if not exact_text:
            continue
        quote_id = engagement_question_trial.sha256_text(exact_text)
        if quote_id in result and result[quote_id] != exact_text:
            raise RuntimeError("canonical quotation SHA-256 collision")
        result[quote_id] = exact_text
    return result


def load_engagement_question_runtime_plan() -> tuple[dict, dict, dict[str, str]]:
    """Load and fully validate the immutable mode-0600 live plan."""

    global _ENGAGEMENT_QUESTION_LAST_LOADED_PLAN_SHA256
    plan_path = configured_engagement_question_path(
        engagement_question_experiment_plan_path
    )
    present, plan_document = load_receipt_json_no_follow(plan_path)
    if not present or not isinstance(plan_document, dict):
        raise RuntimeError(f"engagement experiment plan unavailable: {plan_path}")
    quote_text_by_id = current_exact_quote_text_by_sha256()
    catalogue_path = (
        BASE_DIR
        / "engagement_question_experiment"
        / "approved_question_catalogue.json"
    )
    catalogue, catalogue_sha256 = engagement_question_trial.load_approved_catalogue(
        catalogue_path,
        quote_text_by_id,
    )
    plan = engagement_question_trial.validate_plan_document(
        plan_document,
        catalogue=catalogue,
        catalogue_sha256=catalogue_sha256,
        quote_text_by_id=quote_text_by_id,
        require_plan_kind="live",
    )
    if _ENGAGEMENT_QUESTION_LAST_LOADED_PLAN_SHA256 != plan["plan_sha256"]:
        log_event(
            "engagement_question_experiment_plan_loaded",
            experiment_id=plan["experiment_id"],
            plan_sha256=plan["plan_sha256"],
            pair_count=plan["pair_count"],
        )
        _ENGAGEMENT_QUESTION_LAST_LOADED_PLAN_SHA256 = plan["plan_sha256"]
    return plan, catalogue, quote_text_by_id


def invalidate_engagement_question_experiment(
    state: dict,
    *,
    code: str,
    recorded_epoch: int,
    exception_class: str | None = None,
    authority_component: str | None = None,
) -> None:
    """Durably invalidate a started trial while ordinary posting continues."""

    experiment_state = state.get("engagement_question_experiment")
    if not isinstance(experiment_state, dict):
        log_event(
            "engagement_question_experiment_invalid",
            experiment_id=engagement_question_trial.EXPERIMENT_ID,
            reason=code,
            started=False,
        )
        return
    if experiment_state.get("status") in {"invalid", "completed"}:
        return
    engagement_question_trial.mark_experiment_invalid(
        experiment_state,
        code=code,
        recorded_epoch=recorded_epoch,
    )
    state["engagement_question_experiment"] = experiment_state
    save_state(state, durable=True)
    event_fields = {
        "experiment_id": engagement_question_trial.EXPERIMENT_ID,
        "plan_sha256": experiment_state["active_plan_sha256"],
        "reason": experiment_state["current_deferral_reason"]["code"],
        "started": True,
    }
    if exception_class is not None:
        event_fields["exception_class"] = exception_class
    if authority_component is not None:
        event_fields["authority_component"] = authority_component
    log_event("engagement_question_experiment_invalid", **event_fields)


def initialise_engagement_question_experiment(
    state: dict,
    *,
    current_epoch: int,
) -> tuple[dict | None, dict | None]:
    """Load/bind a configured plan or pause an already-started experiment."""

    raw_state = state.get("engagement_question_experiment")
    if not engagement_question_experiment_enabled and raw_state is None:
        # Source-default parity: no catalogue/plan read and no state creation.
        return None, None
    if isinstance(raw_state, dict) and raw_state.get("status") in {
        "completed",
        "invalid",
    }:
        return None, raw_state
    try:
        plan, _catalogue, _quote_text_by_id = load_engagement_question_runtime_plan()
    except Exception as exc:
        log.error(
            "Engagement-question plan is unavailable or invalid: %s",
            exc,
            exc_info=True,
        )
        invalidate_engagement_question_experiment(
            state,
            code="configured_plan_unavailable_or_invalid",
            recorded_epoch=current_epoch,
        )
        return None, state.get("engagement_question_experiment")

    experiment_state = raw_state if isinstance(raw_state, dict) else None
    if experiment_state is None:
        # The state and reservation begin only when the first pair is actually
        # started on a normal quote opportunity.
        return plan, None
    try:
        engagement_question_trial.validate_experiment_state(
            experiment_state,
            plan=plan,
        )
    except engagement_question_trial.ExperimentValidationError:
        invalidate_engagement_question_experiment(
            state,
            code="active_plan_binding_changed",
            recorded_epoch=current_epoch,
        )
        return None, state.get("engagement_question_experiment")

    if not engagement_question_experiment_enabled:
        changed = engagement_question_trial.set_experiment_paused(
            experiment_state,
            paused=True,
            plan=plan,
        )
        if changed:
            state["engagement_question_experiment"] = experiment_state
            save_state(state, durable=True)
            log_event(
                "engagement_question_experiment_paused",
                experiment_id=experiment_state["experiment_id"],
                plan_sha256=experiment_state["active_plan_sha256"],
                completed_pairs=experiment_state["completed_pair_count"],
            )
        return plan, experiment_state
    if experiment_state.get("status") == "paused":
        engagement_question_trial.set_experiment_paused(
            experiment_state,
            paused=False,
            plan=plan,
        )
        state["engagement_question_experiment"] = experiment_state
        save_state(state, durable=True)
    return plan, experiment_state


def engagement_question_opportunity(
    state: dict,
    *,
    current_epoch: int,
) -> tuple[dict | None, dict | None, set[str]]:
    """Return the exact planned member and reservations for this opportunity."""

    plan, experiment_state = initialise_engagement_question_experiment(
        state,
        current_epoch=current_epoch,
    )
    if plan is None:
        return None, None, set()
    if not engagement_question_experiment_enabled:
        reserved = engagement_question_trial.reserved_quote_ids(
            plan,
            experiment_state,
        )
        return plan, None, reserved
    if experiment_state is None:
        experiment_state = engagement_question_trial.new_experiment_state(plan)
        engagement_question_trial.start_next_pair(experiment_state, plan)
        state["engagement_question_experiment"] = experiment_state
        save_state(state, durable=True)
        log_event(
            "engagement_question_experiment_pair_started",
            experiment_id=experiment_state["experiment_id"],
            plan_sha256=experiment_state["active_plan_sha256"],
            pair_id=experiment_state["active_pair_id"],
            pair_index=experiment_state["current_pair_index"],
        )
    member, reason = engagement_question_trial.member_for_current_opportunity(
        plan,
        experiment_state,
        current_epoch=current_epoch,
    )
    if reason == "pair_start_required":
        engagement_question_trial.start_next_pair(experiment_state, plan)
        state["engagement_question_experiment"] = experiment_state
        save_state(state, durable=True)
        log_event(
            "engagement_question_experiment_pair_started",
            experiment_id=experiment_state["experiment_id"],
            plan_sha256=experiment_state["active_plan_sha256"],
            pair_id=experiment_state["active_pair_id"],
            pair_index=experiment_state["current_pair_index"],
        )
        member, reason = engagement_question_trial.member_for_current_opportunity(
            plan,
            experiment_state,
            current_epoch=current_epoch,
        )
    if reason is not None:
        member = None
    reserved = engagement_question_trial.reserved_quote_ids(
        plan,
        experiment_state,
    )
    return plan, member, reserved


def resolve_engagement_question_quote_choice(
    member: dict,
    *,
    catalogue: dict,
) -> tuple[dict, str]:
    """Re-resolve and validate one exact planned canonical quotation."""

    lines, quote_analysis, today_mm_dd = load_quote_lines_and_analysis()
    quote_id = str(member["quote_id"])
    found: tuple[int, str] | None = None
    for line_no, source_line in enumerate(lines):
        exact_text = source_line[:-1] if source_line.endswith("\n") else source_line
        if exact_text.endswith("\r"):
            exact_text = exact_text[:-1]
        if engagement_question_trial.sha256_text(exact_text) == quote_id:
            found = (line_no, exact_text)
            break
    if found is None:
        raise engagement_question_trial.ExperimentValidationError(
            "planned canonical quotation is no longer present"
        )
    line_no, exact_text = found
    if quote_text_hash(exact_text) != quote_id:
        raise engagement_question_trial.ExperimentValidationError(
            "planned canonical identity no longer matches production"
        )
    if quote_id not in completed_research_quote_hashes():
        raise engagement_question_trial.ExperimentValidationError(
            "planned quotation is no longer runtime attribution eligible"
        )
    from historical_context_formatter import (
        format_context_reply_public,
        load_and_validate_corpus,
        packet_for_posted_quote,
        packet_is_attributed_to_margaret_thatcher,
    )

    if _HISTORICAL_CONTEXT_CORPUS_SNAPSHOT is None:
        packets, unresolved = load_and_validate_corpus(
            HISTORICAL_CONTEXT_RESEARCH_DIR,
            require_source_role_audit=True,
        )
    else:
        packets, unresolved = _HISTORICAL_CONTEXT_CORPUS_SNAPSHOT
    packet = packet_for_posted_quote(
        packets,
        unresolved,
        quote_id,
        exact_text,
    )
    formatted = (
        format_context_reply_public(
            packet,
            maximum_length=int(historical_context_reply["maximum_length"]),
            include_meaning=bool(historical_context_reply["include_meaning"]),
            include_source=bool(historical_context_reply["include_source"]),
            include_verification=bool(
                historical_context_reply["include_verification"]
            ),
        )
        if packet is not None
        and packet_is_attributed_to_margaret_thatcher(packet)
        else None
    )
    if (
        not isinstance(formatted, dict)
        or formatted.get("rendering_mode") != "public"
        or formatted.get("verification_label") != member["verification_label"]
        or formatted.get("source_class") != member["source_class"]
    ):
        raise engagement_question_trial.ExperimentValidationError(
            "planned historical context is no longer publicly renderable"
        )
    analysis = quote_metadata_for_hash(quote_analysis, quote_id, exact_text)
    if analysis is None:
        raise engagement_question_trial.ExperimentValidationError(
            "planned quotation analysis is unavailable"
        )
    topics = analysis.get("primary_topics")
    if (
        not isinstance(topics, list)
        or not topics
        or topics[0] != member["topic"]
        or engagement_question_trial.quotation_length_band(exact_text)
        != member["quotation_length_band"]
    ):
        raise engagement_question_trial.ExperimentValidationError(
            "planned quotation matching metadata changed"
        )
    weight, season_status = quote_candidate_weight(
        analysis,
        today_mm_dd=today_mm_dd,
    )
    if weight <= 0:
        raise engagement_question_trial.ExperimentValidationError(
            "planned quotation is currently hard-seasonally excluded"
        )
    entry = catalogue["entries"].get(quote_id)
    if not isinstance(entry, dict):
        raise engagement_question_trial.ExperimentValidationError(
            "planned quotation is outside the approved catalogue"
        )
    public_text = engagement_question_trial.validate_complete_public_text(
        exact_quote_text=exact_text,
        catalogue_entry=entry,
        arm=str(member["arm"]),
    )
    if (
        entry["question_body"] != member["approved_question_body"]
        or entry["question_sha256"] != member["approved_question_sha256"]
        or entry["complete_treatment_sha256"]
        != member["complete_treatment_sha256"]
        or entry["complete_treatment_weighted_length"]
        != member["complete_treatment_weighted_length"]
    ):
        raise engagement_question_trial.ExperimentValidationError(
            "planned approved question metadata changed"
        )
    return (
        {
            "line_no": line_no,
            "text": exact_text,
            "quote_hash": quote_id,
            "analysis": analysis,
            "weight": weight,
            "season_status": season_status,
        },
        public_text,
    )


def revalidate_engagement_question_publication_authority(
    *,
    state: dict,
    lines_used: set,
    envelope: dict,
    quote_choice: dict,
    public_text: str,
) -> None:
    """Re-resolve one prepared trial member at the remote-write handoff."""

    plan, catalogue, _quote_text_by_id = load_engagement_question_runtime_plan()
    experiment_state = state.get("engagement_question_experiment")
    if not isinstance(experiment_state, dict):
        raise engagement_question_trial.ExperimentValidationError(
            "prepared experimental publication lost protected state"
        )
    engagement_question_trial.validate_experiment_state(
        experiment_state,
        plan=plan,
    )
    binding = envelope.get("binding")
    if not isinstance(binding, dict):
        raise engagement_question_trial.ExperimentValidationError(
            "prepared experimental publication lost its binding"
        )
    pair_index = binding.get("pair_index")
    member_position = binding.get("member_position")
    if (
        type(pair_index) is not int
        or member_position not in {1, 2}
        or experiment_state.get("status") != "active"
        or experiment_state.get("active_pair_id") != binding.get("pair_id")
        or experiment_state.get("current_pair_index") != pair_index
        or experiment_state.get("next_pair_member_position") != member_position
    ):
        raise engagement_question_trial.ExperimentValidationError(
            "prepared experimental publication state authority changed"
        )
    try:
        pair = plan["pairs"][pair_index]
        plan_member = pair["members"][member_position - 1]
        member = {
            **dict(plan_member),
            "pair_index": pair_index,
            "pair_id": str(pair["pair_id"]),
            "topic": str(pair["topic"]),
            "quotation_length_band": str(pair["quotation_length_band"]),
            "publication_order": str(pair["planned_publication_order"]),
        }
    except (IndexError, KeyError, TypeError) as exc:
        raise engagement_question_trial.ExperimentValidationError(
            "prepared experimental publication member is unavailable"
        ) from exc
    quote_id = str(member.get("quote_id") or "")
    if quote_id in lines_used:
        raise engagement_question_trial.ExperimentValidationError(
            "prepared experimental quotation entered used history"
        )
    current_choice, current_public_text = resolve_engagement_question_quote_choice(
        member,
        catalogue=catalogue,
    )
    if (
        current_public_text != public_text
        or current_choice.get("quote_hash") != quote_choice.get("quote_hash")
        or current_choice.get("line_no") != quote_choice.get("line_no")
        or current_choice.get("text") != quote_choice.get("text")
    ):
        raise engagement_question_trial.ExperimentValidationError(
            "prepared experimental quotation or payload changed"
        )
    rebuilt_binding = engagement_question_trial.build_attempt_binding(
        plan=plan,
        state=experiment_state,
        member=member,
        exact_quote_text=str(current_choice["text"]),
        public_text=current_public_text,
    )
    rebuilt_envelope = {
        "binding": rebuilt_binding,
        "canonical_quote_text": str(current_choice["text"]),
        "approved_question_body": str(member["approved_question_body"]),
        "complete_treatment_sha256": str(
            member["complete_treatment_sha256"]
        ),
        "complete_treatment_weighted_length": int(
            member["complete_treatment_weighted_length"]
        ),
    }
    if rebuilt_envelope != envelope or not engagement_experiment_attempt_envelope_is_valid(
        envelope,
        public_text=public_text,
        quote_hash=quote_id,
        plan=plan,
    ):
        raise engagement_question_trial.ExperimentValidationError(
            "prepared experimental publication authority changed"
        )


ENGAGEMENT_QUESTION_MEMBER_AUTHORITY_KEYS = frozenset(
    set(engagement_question_trial.MEMBER_FIELDS)
    | {
        "pair_index",
        "pair_id",
        "topic",
        "quotation_length_band",
        "publication_order",
        "planned_publication_order",
    }
)


def engagement_question_authority_failure_diagnostic(
    exc: BaseException,
) -> tuple[str, str]:
    """Return bounded, non-sensitive structured handoff failure detail."""

    raw_class = type(exc).__name__
    exception_class = (
        raw_class
        if len(raw_class) <= 80 and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", raw_class)
        else "Exception"
    )
    message_arg = exc.args[0] if exc.args and type(exc.args[0]) is str else ""
    message = message_arg[:240].casefold()
    if isinstance(exc, KeyError):
        missing_key = (
            exc.args[0]
            if len(exc.args) == 1 and type(exc.args[0]) is str
            else None
        )
        component = (
            "member_metadata"
            if missing_key in ENGAGEMENT_QUESTION_MEMBER_AUTHORITY_KEYS
            else "authority_revalidation"
        )
    elif "used histor" in message:
        component = "used_history"
    elif "catalogue" in message:
        component = "catalogue"
    elif "plan" in message:
        component = "plan"
    elif "state" in message:
        component = "protected_state"
    elif "binding" in message:
        component = "publication_binding"
    elif "payload" in message or "public text" in message:
        component = "public_payload"
    elif "quotation" in message or "member" in message:
        component = "publication_member"
    elif isinstance(exc, (OSError, UnicodeError, json.JSONDecodeError)):
        component = "authority_input"
    else:
        component = "authority_revalidation"
    return exception_class, component


def revalidate_or_invalidate_engagement_question_publication(
    *,
    state: dict,
    lines_used: set,
    envelope: dict,
    quote_choice: dict,
    public_text: str,
) -> None:
    """Fail closed and invalidate a trial whose prepared authority changed."""

    try:
        revalidate_engagement_question_publication_authority(
            state=state,
            lines_used=lines_used,
            envelope=envelope,
            quote_choice=quote_choice,
            public_text=public_text,
        )
    except Exception as exc:
        log.error(
            "Experimental publication authority changed at remote-write handoff",
            exc_info=True,
        )
        exception_class, authority_component = (
            engagement_question_authority_failure_diagnostic(exc)
        )
        invalidate_engagement_question_experiment(
            state,
            code="pre_write_authority_changed",
            recorded_epoch=now_epoch(),
            exception_class=exception_class,
            authority_component=authority_component,
        )
        raise engagement_question_trial.ExperimentValidationError(
            "experimental publication authority changed before remote root posting"
        ) from exc


def defer_engagement_question_member(
    state: dict,
    *,
    code: str,
    recorded_epoch: int,
) -> None:
    """Durably record a bounded member deferral without advancing it."""

    experiment_state = state.get("engagement_question_experiment")
    if not isinstance(experiment_state, dict):
        raise RuntimeError("cannot defer an experiment before it starts")
    engagement_question_trial.record_deferral(
        experiment_state,
        code=code,
        recorded_epoch=recorded_epoch,
    )
    state["engagement_question_experiment"] = experiment_state
    save_state(state, durable=True)
    reason = experiment_state["current_deferral_reason"]
    log_event(
        "engagement_question_experimental_member_deferred",
        experiment_id=experiment_state["experiment_id"],
        plan_sha256=experiment_state["active_plan_sha256"],
        pair_id=reason["pair_id"],
        member_position=reason["member_position"],
        reason=reason["code"],
    )


def publish_pending_engagement_question_notification(state: dict) -> bool:
    """Atomically publish the oldest pending treatment observation."""

    global _ENGAGEMENT_QUESTION_LAST_NOTIFICATION_FAILURE_POST_ID
    experiment_state = state.get("engagement_question_experiment")
    if not isinstance(experiment_state, dict):
        return False
    output_setting = engagement_question_notification_output_path
    if not output_setting:
        return False
    post_id = ""
    try:
        identity = engagement_question_trial.pending_treatment_notification(
            experiment_state
        )
        if identity is None:
            return False
        post_id = str(identity.get("post_id") or "")
        document = engagement_question_trial.validate_notification_document(
            copy.deepcopy(identity["document"])
        )
        if (
            engagement_question_trial.canonical_sha256(document)
            != identity["document_sha256"]
        ):
            raise RuntimeError("pending treatment notification identity changed")
        output_path = configured_engagement_question_path(output_setting)
        output_already_matches = json_file_matches(output_path, document)
        if not output_already_matches:
            try:
                output_metadata = os.lstat(output_path)
            except FileNotFoundError:
                output_metadata = None
            if output_metadata is not None:
                if not stat.S_ISREG(output_metadata.st_mode):
                    raise RuntimeError(
                        "treatment notification output is not a regular file"
                    )
                output_age_seconds = now_epoch() - output_metadata.st_mtime
                if (
                    output_age_seconds
                    < ENGAGEMENT_QUESTION_NOTIFICATION_REPLACEMENT_MIN_AGE_SECONDS
                ):
                    # Home Assistant polls this single-document file every 30
                    # seconds.  Preserve each queued post for two complete poll
                    # intervals before replacing it with the next identity.
                    return False
            atomic_write_json(output_path, document, durable=True)
        if not json_file_matches(output_path, document):
            raise RuntimeError("treatment notification output verification failed")
        state_before_delivery = copy.deepcopy(experiment_state)
        try:
            if not engagement_question_trial.mark_notification_delivered(
                experiment_state,
                post_id,
            ):
                raise RuntimeError(
                    "pending treatment notification was not marked delivered"
                )
            state["engagement_question_experiment"] = experiment_state
            save_state(state, durable=True)
        except Exception:
            experiment_state.clear()
            experiment_state.update(state_before_delivery)
            raise
        _ENGAGEMENT_QUESTION_LAST_NOTIFICATION_FAILURE_POST_ID = None
        return True
    except Exception:
        log.error(
            "Confirmed treatment notification write failed for post_id=%s",
            post_id,
            exc_info=True,
        )
        if _ENGAGEMENT_QUESTION_LAST_NOTIFICATION_FAILURE_POST_ID != post_id:
            log_event(
                "engagement_question_treatment_notification_write_failed",
                experiment_id=engagement_question_trial.EXPERIMENT_ID,
                post_id=post_id or None,
            )
            _ENGAGEMENT_QUESTION_LAST_NOTIFICATION_FAILURE_POST_ID = post_id
        return False


def build_quote_candidates(
    lines: list[str],
    available_lines: list[int],
    quote_analysis: dict | None,
    today_mm_dd: str,
    *,
    excluded_quote_hashes: set[str] | None = None,
) -> tuple[list[dict], int, int]:
    """Build analysed, research-eligible quotation candidates for a date."""
    return _quote_candidates.build_quote_candidates(
        lines,
        available_lines,
        quote_analysis,
        today_mm_dd,
        excluded_quote_hashes=excluded_quote_hashes,
        quote_text_hash=quote_text_hash,
        quote_metadata_for_hash=quote_metadata_for_hash,
        quote_candidate_weight=quote_candidate_weight,
        log=log,
    )


def load_quote_lines_and_analysis() -> tuple[list[str], dict | None, str]:
    """Load the active quotation source and validated analysis metadata."""
    return _quote_candidates.load_quote_lines_and_analysis(
        lines_file=LINES_FILE,
        load_quote_analysis=load_quote_analysis,
        validate_quote_analysis_against_lines=validate_quote_analysis_against_lines,
        current_datetime=current_datetime,
        log=log,
    )


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
    return _quote_candidates.completed_research_quote_hashes(
        load_completed_research_quote_hashes=load_completed_research_quote_hashes,
    )


def quote_candidates_for_current_cycle(lines_used: set, *, excluded_quote_hashes: set[str] | None = None) -> list[dict]:
    """Build the unused runtime-eligible quotation pool for the current cycle."""
    return _quote_candidates.quote_candidates_for_current_cycle(
        lines_used,
        excluded_quote_hashes=excluded_quote_hashes,
        load_quote_lines_and_analysis=load_quote_lines_and_analysis,
        current_quote_hashes_by_line=current_quote_hashes_by_line,
        completed_research_quote_hashes=completed_research_quote_hashes,
        build_quote_candidates=build_quote_candidates,
        lines_file=LINES_FILE,
        log=log,
    )


def select_quote_candidate(candidates: list[dict]) -> dict:
    """Select quote candidate."""
    return _quote_candidates.select_quote_candidate(
        candidates,
        weighted_random_choice=weighted_random_choice,
        log=log,
    )


def choose_unused_line_candidate(lines_used: set, *, excluded_quote_hashes: set[str] | None = None) -> dict:
    """Select unused line candidate."""
    return _quote_candidates.choose_unused_line_candidate(
        lines_used,
        excluded_quote_hashes=excluded_quote_hashes,
        quote_candidates_for_current_cycle=quote_candidates_for_current_cycle,
        select_quote_candidate=select_quote_candidate,
    )


def available_currently_eligible_image_basenames(
    eligible_basenames: set[str],
    images_used: set[str],
    state: dict | None = None,
) -> tuple[list[str], bool]:
    """Return whether available currently eligible image basenames."""
    return _image_selection.available_currently_eligible_image_basenames(
        eligible_basenames,
        images_used,
        state,
        NoEligibleImageForQuote=NoEligibleImageForQuote,
        log=log,
    )


def current_image_sha256(path: str) -> str:
    """Return the current image SHA-256."""
    return file_sha256(Path(path))


def image_metadata_for_basename(image_analysis: dict | None, basename: str, path: str | None = None) -> tuple[str | None, dict | None]:
    """Return the image metadata for basename."""
    return _asset_metadata.image_metadata_for_basename(
        image_analysis,
        basename,
        path,
        current_image_sha256=current_image_sha256,
        StaleImageMetadata=StaleImageMetadata,
        log=log,
    )


generated_image_origin_quote_hash = _asset_metadata.generated_image_origin_quote_hash


def image_selection_observability(basename: str, quote_hash: object = None, origin_quote_boost: float = 0.0) -> dict:
    """Return the image selection observability."""
    return _image_selection.image_selection_observability(
        basename,
        quote_hash,
        origin_quote_boost,
        generated_image_origin_quote_hash=generated_image_origin_quote_hash,
    )


def generated_image_spacing_required() -> int:
    """Return the generated image spacing required."""
    return _image_selection.generated_image_spacing_required(
        min_original_posts_between=GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN,
    )


def original_posts_since_generated_image(state: dict | None) -> int:
    """Return the original posts since generated image."""
    return _image_selection.original_posts_since_generated_image(
        state,
        generated_image_spacing_required=generated_image_spacing_required,
    )


def generated_images_allowed_by_spacing(state: dict | None) -> bool:
    """Return whether generated images allowed by spacing."""
    return _image_selection.generated_images_allowed_by_spacing(
        state,
        generated_image_spacing_required=generated_image_spacing_required,
        original_posts_since_generated_image=original_posts_since_generated_image,
    )


def log_generated_image_spacing_status(state: dict | None) -> bool:
    """Log generated image spacing status."""
    return _image_selection.log_generated_image_spacing_status(
        state,
        generated_image_spacing_required=generated_image_spacing_required,
        original_posts_since_generated_image=original_posts_since_generated_image,
        generated_images_allowed_by_spacing=generated_images_allowed_by_spacing,
        enable_generated_image_pool=ENABLE_GENERATED_IMAGE_POOL,
        log=log,
    )


def log_generated_image_spacing_state_updated(state: dict | None, image_basename: str) -> None:
    """Log generated image spacing state updated."""
    return _image_selection.log_generated_image_spacing_state_updated(
        state,
        image_basename,
        generated_image_spacing_required=generated_image_spacing_required,
        original_posts_since_generated_image=original_posts_since_generated_image,
        generated_images_allowed_by_spacing=generated_images_allowed_by_spacing,
        generated_image_origin_quote_hash=generated_image_origin_quote_hash,
        enable_generated_image_pool=ENABLE_GENERATED_IMAGE_POOL,
        log=log,
    )


def filter_generated_images_by_spacing(eligible_basenames: set[str], state: dict | None) -> set[str]:
    """Filter generated images by spacing."""
    return _image_selection.filter_generated_images_by_spacing(
        eligible_basenames,
        state,
        generated_images_allowed_by_spacing=generated_images_allowed_by_spacing,
        generated_image_origin_quote_hash=generated_image_origin_quote_hash,
        enable_generated_image_pool=ENABLE_GENERATED_IMAGE_POOL,
    )


def update_regular_generated_image_spacing_state(state: dict, image_basename: str) -> None:
    """Update regular generated image spacing state."""
    return _image_selection.update_regular_generated_image_spacing_state(
        state,
        image_basename,
        generated_image_spacing_required=generated_image_spacing_required,
        generated_image_origin_quote_hash=generated_image_origin_quote_hash,
        original_posts_since_generated_image=original_posts_since_generated_image,
        log_generated_image_spacing_state_updated=log_generated_image_spacing_state_updated,
    )


def regular_generated_image_spacing_already_reflected(state: dict, image_basename: str) -> bool:
    """Return the regular generated image spacing already reflected."""
    return _image_selection.regular_generated_image_spacing_already_reflected(
        state,
        image_basename,
        generated_image_origin_quote_hash=generated_image_origin_quote_hash,
        original_posts_since_generated_image=original_posts_since_generated_image,
    )


def log_regular_image_selection(choice: dict) -> None:
    """Log regular image selection."""
    return _image_selection.log_regular_image_selection(
        choice,
        log=log,
    )


def choose_matched_unused_image(
    images_used: set,
    quote_choice: dict,
    state: dict,
    *,
    force_cycle_reset: bool = False,
    avoid_last_image_at_cycle_boundary: bool = True,
    cycle_boundary_exclusions: set[str] | None = None,
    generated_images_allowed: bool | None = None,
    selection_phase: str = "normal",
) -> dict:
    """Select the highest-scoring eligible unused image for a quotation."""
    return _image_selection.choose_matched_unused_image(
        images_used,
        quote_choice,
        state,
        force_cycle_reset=force_cycle_reset,
        avoid_last_image_at_cycle_boundary=avoid_last_image_at_cycle_boundary,
        cycle_boundary_exclusions=cycle_boundary_exclusions,
        generated_images_allowed=generated_images_allowed,
        selection_phase=selection_phase,
        current_image_paths=current_image_paths,
        load_image_analysis=load_image_analysis,
        normalise_image_used_basenames=normalise_image_used_basenames,
        save_image_used_basenames=save_image_used_basenames,
        image_used_history_has_legacy_indices=image_used_history_has_legacy_indices,
        current_datetime=current_datetime,
        build_image_topic_idf=build_image_topic_idf,
        image_metadata_for_basename=image_metadata_for_basename,
        image_is_out_of_season=image_is_out_of_season,
        generated_images_allowed_by_spacing=generated_images_allowed_by_spacing,
        filter_generated_images_by_spacing=filter_generated_images_by_spacing,
        available_currently_eligible_image_basenames=available_currently_eligible_image_basenames,
        score_image_for_quote=score_image_for_quote,
        generated_image_origin_quote_hash=generated_image_origin_quote_hash,
        image_selection_observability=image_selection_observability,
        generated_identity_policy_scoring_active=generated_identity_policy_scoring_active,
        generated_identity_policy_selection=generated_identity_policy_selection,
        apply_original_editorial_selection=apply_original_editorial_selection,
        concise_components=concise_components,
        log_regular_image_selection=log_regular_image_selection,
        log_original_editorial_shadow_result=log_original_editorial_shadow_result,
        generated_identity_policy_applied_result=generated_identity_policy_applied_result,
        log_generated_identity_policy_applied_result=log_generated_identity_policy_applied_result,
        log_generated_identity_policy_shadow_result=log_generated_identity_policy_shadow_result,
        image_glob=IMAGE_GLOB,
        images_used_file=IMAGES_USED_FILE,
        generated_image_origin_quote_boost=GENERATED_IMAGE_ORIGIN_QUOTE_BOOST,
        UnsafeImageHistoryMigration=UnsafeImageHistoryMigration,
        GlobalImageUnavailable=GlobalImageUnavailable,
        StaleImageMetadata=StaleImageMetadata,
        QuoteSpecificImageMismatch=QuoteSpecificImageMismatch,
        log=log,
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
        log_generated_image_spacing_status=log_generated_image_spacing_status,
        choose_unused_line_candidate=choose_unused_line_candidate,
        choose_matched_unused_image=choose_matched_unused_image,
        max_quote_image_pair_attempts=MAX_QUOTE_IMAGE_PAIR_ATTEMPTS,
        QuoteSpecificImageMismatch=QuoteSpecificImageMismatch,
        NoViableQuoteImagePair=NoViableQuoteImagePair,
        log=log,
    )


def choose_engagement_question_image(
    images_used: set,
    quote_choice: dict,
    state: dict,
) -> dict:
    """Apply the existing image policy to one fixed experimental quotation."""
    return _image_selection.choose_engagement_question_image(
        images_used,
        quote_choice,
        state,
        log_generated_image_spacing_status=log_generated_image_spacing_status,
        choose_matched_unused_image=choose_matched_unused_image,
        QuoteSpecificImageMismatch=QuoteSpecificImageMismatch,
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
        engagement_question_opportunity=engagement_question_opportunity,
        load_engagement_question_runtime_plan=load_engagement_question_runtime_plan,
        invalidate_engagement_question_experiment=invalidate_engagement_question_experiment,
        engagement_question_trial=engagement_question_trial,
        resolve_engagement_question_quote_choice=resolve_engagement_question_quote_choice,
        engagement_experiment_attempt_envelope_is_valid=engagement_experiment_attempt_envelope_is_valid,
        choose_engagement_question_image=choose_engagement_question_image,
        QuoteSpecificImageMismatch=QuoteSpecificImageMismatch,
        defer_engagement_question_member=defer_engagement_question_member,
        choose_regular_quote_image_pair=choose_regular_quote_image_pair,
        NoViableQuoteImagePair=NoViableQuoteImagePair,
        POST_SLEEP_MIN=POST_SLEEP_MIN,
        POST_SLEEP_MAX=POST_SLEEP_MAX,
        MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS=MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS,
        MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS=MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS,
        ENABLE_DAILY_MEME_POSTS=ENABLE_DAILY_MEME_POSTS,
        upload_media=upload_media,
        revalidate_or_invalidate_engagement_question_publication=revalidate_or_invalidate_engagement_question_publication,
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
        valid_post_id=valid_post_id,
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
        apply_confirmed_engagement_experiment_receipt=apply_confirmed_engagement_experiment_receipt,
        update_regular_generated_image_spacing_state=update_regular_generated_image_spacing_state,
        apply_state_fields=apply_state_fields,
        cache_tweet=cache_tweet,
        MY_USER_ID=MY_USER_ID,
        record_recent_own_post=record_recent_own_post,
        emergency_persist_confirmed_regular_post=emergency_persist_confirmed_regular_post,
        confirmed_regular_emergency_representation_is_complete=confirmed_regular_emergency_representation_is_complete,
        latch_confirmed_post_persistence_failure=latch_confirmed_post_persistence_failure,
        UnrecoverableConfirmedPostPersistenceError=UnrecoverableConfirmedPostPersistenceError,
        load_regular_post_receipt=load_regular_post_receipt,
        enqueue_historical_context_obligation=enqueue_historical_context_obligation,
        engagement_experiment_envelope_from_receipt=engagement_experiment_envelope_from_receipt,
        log_confirmed_engagement_experiment_receipt=log_confirmed_engagement_experiment_receipt,
        log_event=log_event,
        engagement_experiment_event_fields=engagement_experiment_event_fields,
        retire_lane_transport_journal_if_present=retire_lane_transport_journal_if_present,
        publish_pending_engagement_question_notification=publish_pending_engagement_question_notification,
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
    return _asset_metadata.load_meme_analysis_index(meme_analysis_file=MEME_ANALYSIS_FILE, log=log)


original_meme_filename = _daily_meme.original_meme_filename


def build_meme_cache_summary(shortlist_path: Path, analysis_index: dict[str, dict]) -> str:
    """Build the meme cache summary through current root helpers."""
    return _daily_meme.build_meme_cache_summary(
        shortlist_path, analysis_index,
        original_meme_filename=original_meme_filename,
        log=log,
    )


def list_meme_candidates() -> list[Path]:
    """List the current meme catalog through the owner."""
    return _daily_meme.list_meme_candidates(
        MEME_DIR=MEME_DIR,
        log=log,
    )


def choose_next_meme(state: dict) -> Path | None:
    """Select a meme, saving any cycle reset through root authority."""
    return _daily_meme.choose_next_meme(
        state,
        list_meme_candidates=list_meme_candidates,
        log=log,
        RESET_MEME_CYCLE_WHEN_ALL_POSTED=RESET_MEME_CYCLE_WHEN_ALL_POSTED,
        save_state=save_state,
    )


def epoch_date_str(epoch: int | None = None) -> str:
    """Return the epoch date str."""
    if epoch is None:
        epoch = now_epoch()
    return datetime.fromtimestamp(int(epoch)).strftime("%Y-%m-%d")


def reply_cap_date_str(epoch: int | None = None) -> str:
    """Return the conversational daily-cap date in Europe/London."""
    if epoch is None:
        epoch = now_epoch()
    return datetime.fromtimestamp(
        int(epoch),
        tz=ZoneInfo(MAIN_POST_SCHEDULE_TIMEZONE),
    ).strftime("%Y-%m-%d")


def meme_schedule_datetime(epoch: int) -> datetime:
    """Interpret a meme epoch through the current production-zone helper."""
    return _daily_meme.meme_schedule_datetime(
        epoch,
        bound_schedule_datetime=bound_schedule_datetime,
        MAIN_POST_SCHEDULE_TIMEZONE=MAIN_POST_SCHEDULE_TIMEZONE,
    )


def meme_schedule_date_str(epoch: int | None = None) -> str:
    """Return the meme calendar date through current root helpers."""
    return _daily_meme.meme_schedule_date_str(
        epoch,
        now_epoch=now_epoch,
        meme_schedule_datetime=meme_schedule_datetime,
    )


def meme_posted_on_date(state: dict, date_text: str) -> bool:
    """Check the daily meme guard through the current date helper."""
    return _daily_meme.meme_posted_on_date(
        state, date_text,
        meme_schedule_date_str=meme_schedule_date_str,
    )


def next_meme_fallback_epoch(state: dict, from_epoch: int | None = None) -> int:
    """Calculate the next fallback through current calendar settings."""
    return _daily_meme.next_meme_fallback_epoch(
        state, from_epoch,
        now_epoch=now_epoch,
        meme_schedule_datetime=meme_schedule_datetime,
        MEME_FALLBACK_HOUR=MEME_FALLBACK_HOUR,
        MEME_FALLBACK_MINUTE=MEME_FALLBACK_MINUTE,
        meme_posted_on_date=meme_posted_on_date,
    )


def next_meme_schedule_fields(state: dict, from_epoch: int | None = None, mode: str = "fallback") -> dict:
    """Build fallback schedule fields through current root helpers."""
    return _daily_meme.next_meme_schedule_fields(
        state, from_epoch, mode,
        next_meme_fallback_epoch=next_meme_fallback_epoch,
        MEME_SCHEDULE_VERSION=MEME_SCHEDULE_VERSION,
        meme_schedule_date_str=meme_schedule_date_str,
    )


def meme_delay_schedule_fields(epoch: int, mode: str) -> dict:
    """Build delayed schedule fields through current modes and helpers."""
    return _daily_meme.meme_delay_schedule_fields(
        epoch, mode,
        MEME_SCHEDULE_MODES=MEME_SCHEDULE_MODES,
        MEME_SCHEDULE_VERSION=MEME_SCHEDULE_VERSION,
        meme_schedule_date_str=meme_schedule_date_str,
    )


def set_meme_delay_schedule(state: dict, *, epoch: int, mode: str, save: bool = True) -> None:
    """Apply a meme delay and optionally save through root authority."""
    return _daily_meme.set_meme_delay_schedule(
        state,
        epoch=epoch,
        mode=mode,
        save=save,
        apply_state_fields=apply_state_fields,
        meme_delay_schedule_fields=meme_delay_schedule_fields,
        save_state=save_state,
    )


def apply_state_fields(state: dict, fields: dict) -> None:
    """Apply state fields."""
    for key, value in fields.items():
        state[key] = value


def schedule_next_meme_post(state: dict, from_epoch: int | None = None, mode: str = "fallback", *, save: bool = True) -> None:
    """Schedule the fallback and optionally save through root authority."""
    return _daily_meme.schedule_next_meme_post(
        state, from_epoch, mode,
        save=save,
        next_meme_schedule_fields=next_meme_schedule_fields,
        apply_state_fields=apply_state_fields,
        save_state=save_state,
        log=log,
    )


def ensure_meme_schedule_initialized(state: dict) -> None:
    """Initialise or migrate the meme schedule through root authority."""
    return _daily_meme.ensure_meme_schedule_initialized(
        state,
        ENABLE_DAILY_MEME_POSTS=ENABLE_DAILY_MEME_POSTS,
        MEME_SCHEDULE_VERSION=MEME_SCHEDULE_VERSION,
        log=log,
        MEME_TRIGGER_AFTER_HOUR=MEME_TRIGGER_AFTER_HOUR,
        MEME_FALLBACK_HOUR=MEME_FALLBACK_HOUR,
        MEME_FALLBACK_MINUTE=MEME_FALLBACK_MINUTE,
        schedule_next_meme_post=schedule_next_meme_post,
        now_epoch=now_epoch,
    )


def meme_schedule_fields_after_quote_post(state: dict, quote_post_epoch: int | None = None, *, delay: int | None = None) -> dict:
    """Build quote-anchored meme fields through current root settings."""
    return _daily_meme.meme_schedule_fields_after_quote_post(
        state, quote_post_epoch,
        delay=delay,
        ENABLE_DAILY_MEME_POSTS=ENABLE_DAILY_MEME_POSTS,
        now_epoch=now_epoch,
        meme_schedule_datetime=meme_schedule_datetime,
        MEME_TRIGGER_AFTER_HOUR=MEME_TRIGGER_AFTER_HOUR,
        log=log,
        meme_posted_on_date=meme_posted_on_date,
        MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS=MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS,
        MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS=MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS,
        MEME_SCHEDULE_VERSION=MEME_SCHEDULE_VERSION,
    )


def maybe_schedule_meme_after_quote_post(state: dict, quote_post_epoch: int | None = None, *, save: bool = True) -> None:
    """Apply quote-anchored meme scheduling and optionally save state."""
    return _daily_meme.maybe_schedule_meme_after_quote_post(
        state, quote_post_epoch,
        save=save,
        meme_schedule_fields_after_quote_post=meme_schedule_fields_after_quote_post,
        apply_state_fields=apply_state_fields,
        save_state=save_state,
        now_epoch=now_epoch,
        log=log,
        MEME_TRIGGER_AFTER_HOUR=MEME_TRIGGER_AFTER_HOUR,
    )


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


pending_ai_reply_draft_key = _reply_state.pending_ai_reply_draft_key


def validate_current_ai_reply_draft(
    draft: object,
    *,
    context: dict[str, object],
    recent_replies: list[object] | None = None,
) -> dict:
    """Delegate reply state with current root dependencies."""
    return _reply_state.validate_current_ai_reply_draft(
        draft,
        context=context,
        recent_replies=recent_replies,
        validate_single_call_persisted_draft=validate_single_call_persisted_draft,
        reply_evidence_repository=reply_evidence_repository,
    )


def store_pending_ai_reply(
    state: dict,
    target_id: str,
    candidate_source: str,
    reply: str,
    *,
    context: dict[str, object],
) -> bool:
    """Delegate reply state with current root dependencies."""
    return _reply_state.store_pending_ai_reply(
        state, target_id, candidate_source, reply,
        context=context,
        ValidatedReply=ValidatedReply,
        validate_current_ai_reply_draft=validate_current_ai_reply_draft,
        log=log,
        pending_ai_reply_draft_key=pending_ai_reply_draft_key,
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
    """Delegate reply state with current root dependencies."""
    return _reply_state.pending_ai_reply(
        state, target_id, candidate_source,
        context=context,
        recent_replies=recent_replies,
        evaluation_outcome=evaluation_outcome,
        pending_ai_reply_draft_key=pending_ai_reply_draft_key,
        validate_current_ai_reply_draft=validate_current_ai_reply_draft,
        ReplyEvidenceUnavailable=ReplyEvidenceUnavailable,
        ReplyValidationError=ReplyValidationError,
        log=log,
        _record_single_call_result=_record_single_call_result,
        PipelineResult=PipelineResult,
        log_event=log_event,
        SINGLE_CALL_STRATEGY_VERSION=SINGLE_CALL_STRATEGY_VERSION,
        SINGLE_CALL_MODEL=SINGLE_CALL_MODEL,
        ValidatedReply=ValidatedReply,
    )


def clear_pending_ai_reply(state: dict, target_id: str, candidate_source: str) -> None:
    """Delegate reply state with current root dependencies."""
    return _reply_state.clear_pending_ai_reply(
        state, target_id, candidate_source,
        pending_ai_reply_draft_key=pending_ai_reply_draft_key,
    )


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


CONVERSATIONAL_REPLY_HISTORY_LANES = _reply_state.CONVERSATIONAL_REPLY_HISTORY_LANES


def _confirmed_conversational_history_rows(state: dict) -> list[dict]:
    """Delegate reply state with current root dependencies."""
    return _reply_state._confirmed_conversational_history_rows(
        state,
        CONVERSATIONAL_REPLY_HISTORY_LANES=CONVERSATIONAL_REPLY_HISTORY_LANES,
        valid_string_post_id=valid_string_post_id,
        MAX_REASONABLE_STATE_EPOCH=MAX_REASONABLE_STATE_EPOCH,
    )


_confirmed_history_sort_key = _reply_state._confirmed_history_sort_key


def recent_confirmed_account_replies(
    state: dict,
    limit: int = MAX_RECENT_ACCOUNT_REPLIES,
    *,
    before_epoch: int | None = None,
    excluded_post_ids: set[str] | None = None,
    excluded_reply_post_ids: set[str] | None = None,
) -> list[dict[str, str]]:
    """Delegate reply state with current root dependencies."""
    return _reply_state.recent_confirmed_account_replies(
        state, limit,
        before_epoch=before_epoch,
        excluded_post_ids=excluded_post_ids,
        excluded_reply_post_ids=excluded_reply_post_ids,
        _confirmed_conversational_history_rows=_confirmed_conversational_history_rows,
        _confirmed_history_sort_key=_confirmed_history_sort_key,
        MAX_RECENT_ACCOUNT_REPLIES=MAX_RECENT_ACCOUNT_REPLIES,
    )


def _reply_context_history_excluded_post_ids(
    context: dict[str, object],
) -> set[str]:
    """Delegate reply state with current root dependencies."""
    return _reply_state._reply_context_history_excluded_post_ids(
        context,
        quoted_post_reference_id=quoted_post_reference_id,
    )


def recovery_comparison_account_replies(
    state: dict,
    *,
    context: dict[str, object],
) -> list[dict[str, str]]:
    """Delegate reply state with current root dependencies."""
    return _reply_state.recovery_comparison_account_replies(
        state,
        context=context,
        _reply_context_history_excluded_post_ids=_reply_context_history_excluded_post_ids,
        now_epoch=now_epoch,
        MAX_REASONABLE_STATE_EPOCH=MAX_REASONABLE_STATE_EPOCH,
        recent_confirmed_account_replies=recent_confirmed_account_replies,
        _same_author_confirmed_history_rows=_same_author_confirmed_history_rows,
        _confirmed_conversational_history_rows=_confirmed_conversational_history_rows,
    )


def _same_author_confirmed_history_rows(
    state: dict,
    *,
    author_id: object,
    current_thread_post_ids: set[str],
    target_id: object,
    before_epoch: int | None,
) -> list[dict]:
    """Delegate reply state with current root dependencies."""
    return _reply_state._same_author_confirmed_history_rows(
        state,
        author_id=author_id,
        current_thread_post_ids=current_thread_post_ids,
        target_id=target_id,
        before_epoch=before_epoch,
        AI_REPLY_HISTORY_MAX_AGE_SECONDS=AI_REPLY_HISTORY_MAX_AGE_SECONDS,
        _confirmed_conversational_history_rows=_confirmed_conversational_history_rows,
        _confirmed_history_sort_key=_confirmed_history_sort_key,
        MAX_SAME_AUTHOR_INTERACTIONS=MAX_SAME_AUTHOR_INTERACTIONS,
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
    """Delegate reply state with current root dependencies."""
    return _reply_state.recent_same_author_account_interactions(
        state,
        author_id=author_id,
        conversation_id=conversation_id,
        target_id=target_id,
        before_epoch=before_epoch,
        visible_post_ids=visible_post_ids,
        _same_author_confirmed_history_rows=_same_author_confirmed_history_rows,
        MAX_SAME_AUTHOR_INTERACTIONS=MAX_SAME_AUTHOR_INTERACTIONS,
    )


_reply_target_epoch = _reply_state._reply_target_epoch


_REPLY_IMAGE_MIME_TYPES = _reply_generation._REPLY_IMAGE_MIME_TYPES


class ReplyMediaUnavailable(RuntimeError):
    """A material candidate image could not be collected safely."""


class ReplyMediaTransientUnavailable(ReplyMediaUnavailable):
    """A material candidate image could not be collected on this cycle."""


def _safe_reply_image_url(value: object) -> str:
    """Delegate reply generation with current root dependencies."""
    return _reply_generation._safe_reply_image_url(
        value,
        urlsplit=urlsplit,
        ReplyMediaUnavailable=ReplyMediaUnavailable,
        TEST_MODE=TEST_MODE,
        endpoint_is_loopback=endpoint_is_loopback,
    )


def collect_reply_images(media_context: dict | None) -> list[dict[str, object]]:
    """Collect up to two already-identified native X images with hard bounds."""
    return _reply_generation.collect_reply_images(
        media_context,
        MAX_SUPPLIED_IMAGES=MAX_SUPPLIED_IMAGES,
        ReplyMediaUnavailable=ReplyMediaUnavailable,
        _safe_reply_image_url=_safe_reply_image_url,
        require_remote_operation_unpaused=require_remote_operation_unpaused,
        requests=requests,
        request_timeout=request_timeout,
        ReplyMediaTransientUnavailable=ReplyMediaTransientUnavailable,
        _REPLY_IMAGE_MIME_TYPES=_REPLY_IMAGE_MIME_TYPES,
        SINGLE_CALL_MAX_IMAGE_BYTES=SINGLE_CALL_MAX_IMAGE_BYTES,
        validate_supplied_images=validate_supplied_images,
    )


def _definite_connection_failure_before_transmission(
    error: requests.RequestException,
) -> bool:
    """Delegate reply generation with current root dependencies."""
    return _reply_generation._definite_connection_failure_before_transmission(
        error,
        requests=requests,
    )


def _openai_api_error(
    message: str,
    *,
    category: str,
    status_code: int | None = None,
    reset_epoch: int | None = None,
    retry_after_seconds: int | None = None,
    request_attempt_count: int = 1,
) -> ApiError:
    """Delegate reply generation with current root dependencies."""
    return _reply_generation._openai_api_error(
        message,
        category=category,
        status_code=status_code,
        reset_epoch=reset_epoch,
        retry_after_seconds=retry_after_seconds,
        request_attempt_count=request_attempt_count,
        ApiError=ApiError,
    )


def _openai_retry_metadata(response: requests.Response) -> tuple[int | None, int | None]:
    """Return bounded Retry-After metadata for provider cooldown accounting."""
    return _reply_generation._openai_retry_metadata(
        response,
        now_epoch=now_epoch,
        parsedate_to_datetime=parsedate_to_datetime,
    )


_OPENAI_PROVIDER_HEALTH_FAILURE_CATEGORIES = _reply_generation._OPENAI_PROVIDER_HEALTH_FAILURE_CATEGORIES
_TERMINAL_CANDIDATE_LOCAL_FAILURE_CATEGORIES = _reply_generation._TERMINAL_CANDIDATE_LOCAL_FAILURE_CATEGORIES


def _is_openai_provider_health_failure(category: object) -> bool:
    """Return whether a failure is evidence about OpenAI service health."""
    return _reply_generation._is_openai_provider_health_failure(
        category,
        _OPENAI_PROVIDER_HEALTH_FAILURE_CATEGORIES=_OPENAI_PROVIDER_HEALTH_FAILURE_CATEGORIES,
    )


def _is_terminal_candidate_local_failure(outcome: dict[str, object]) -> bool:
    """Return whether one permanent local failure should retire its candidate."""
    return _reply_generation._is_terminal_candidate_local_failure(
        outcome,
        _TERMINAL_CANDIDATE_LOCAL_FAILURE_CATEGORIES=_TERMINAL_CANDIDATE_LOCAL_FAILURE_CATEGORIES,
    )


def openai_responses_reply_call(
    *,
    request: dict[str, object],
    timeout_seconds: int,
    lane: str,
    target_id: str,
) -> dict[str, object]:
    """Send one executable Responses request, retrying only proved non-execution."""
    return _reply_generation.openai_responses_reply_call(
        request=request,
        timeout_seconds=timeout_seconds,
        lane=lane,
        target_id=target_id,
        log=log,
        SINGLE_CALL_MODEL=SINGLE_CALL_MODEL,
        SINGLE_CALL_REASONING_EFFORT=SINGLE_CALL_REASONING_EFFORT,
        monotonic=monotonic,
        require_remote_operation_unpaused=require_remote_operation_unpaused,
        report_bot_health_progress=report_bot_health_progress,
        requests=requests,
        OPENAI_BASE=OPENAI_BASE,
        OPENAI_API_KEY=OPENAI_API_KEY,
        _definite_connection_failure_before_transmission=_definite_connection_failure_before_transmission,
        _openai_api_error=_openai_api_error,
        _openai_retry_metadata=_openai_retry_metadata,
        sleep=sleep,
    )


def _record_single_call_result(
    result: PipelineResult,
    *,
    lane: str,
    target_id: str,
) -> None:
    """Delegate reply generation with current root dependencies."""
    return _reply_generation._record_single_call_result(
        result,
        lane=lane,
        target_id=target_id,
        single_call_decision_telemetry=single_call_decision_telemetry,
        log=log,
        log_event=log_event,
        SINGLE_CALL_MODEL=SINGLE_CALL_MODEL,
        SINGLE_CALL_STRATEGY_VERSION=SINGLE_CALL_STRATEGY_VERSION,
    )


def generate_single_call_reply(
    context: dict[str, object],
    media_context: dict | None = None,
    *,
    state: dict,
    evaluation_outcome: dict | None = None,
) -> str | None:
    """Make one authoritative Sol decision and return only validated prose."""
    return _reply_generation.generate_single_call_reply(
        context, media_context,
        state=state,
        evaluation_outcome=evaluation_outcome,
        collect_reply_images=collect_reply_images,
        RemoteOperationsPaused=RemoteOperationsPaused,
        ReplyMediaUnavailable=ReplyMediaUnavailable,
        ReplyMediaTransientUnavailable=ReplyMediaTransientUnavailable,
        PipelineResult=PipelineResult,
        _record_single_call_result=_record_single_call_result,
        log=log,
        _reply_target_epoch=_reply_target_epoch,
        _reply_context_history_excluded_post_ids=_reply_context_history_excluded_post_ids,
        _same_author_confirmed_history_rows=_same_author_confirmed_history_rows,
        recent_confirmed_account_replies=recent_confirmed_account_replies,
        require_remote_operation_unpaused=require_remote_operation_unpaused,
        run_single_call_reply_pipeline=run_single_call_reply_pipeline,
        single_call_reply=single_call_reply,
        reply_evidence_repository=reply_evidence_repository,
        openai_responses_reply_call=openai_responses_reply_call,
        _is_openai_provider_health_failure=_is_openai_provider_health_failure,
        record_api_error=record_api_error,
        _openai_api_error=_openai_api_error,
        ValidatedReply=ValidatedReply,
    )


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
    """Delegate reply evaluation state with current root dependencies."""
    return _reply_evaluation_state.record_terminal_reply_evaluation(
        state,
        target_id=target_id,
        lane=lane,
        reason=reason,
        outcome=outcome,
        prune_records=prune_records,
        evidence_policy=evidence_policy,
        now_epoch=now_epoch,
        prune_reply_evaluation_records=prune_reply_evaluation_records,
    )


def ai_reply_receipt_draft_is_valid(data: dict, text: object) -> bool:
    """Delegate reply state with current root dependencies."""
    return _reply_state.ai_reply_receipt_draft_is_valid(
        data, text,
        validate_current_ai_reply_draft=validate_current_ai_reply_draft,
    )


_LEGACY_TESTED_REPLY_STRATEGY_VERSION = _legacy_reply_validation._LEGACY_TESTED_REPLY_STRATEGY_VERSION
_LEGACY_AI_FIRST_REPLY_STRATEGY_VERSION = _legacy_reply_validation._LEGACY_AI_FIRST_REPLY_STRATEGY_VERSION
_LEGACY_SINGLE_SOL_REPLY_STRATEGY_VERSION = _legacy_reply_validation._LEGACY_SINGLE_SOL_REPLY_STRATEGY_VERSION
_LEGACY_SINGLE_SOL_PROMPT_SHA256 = _legacy_reply_validation._LEGACY_SINGLE_SOL_PROMPT_SHA256
_LEGACY_SINGLE_SOL_RESPONSE_SCHEMA_SHA256 = _legacy_reply_validation._LEGACY_SINGLE_SOL_RESPONSE_SCHEMA_SHA256
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


def _legacy_multi_model_context_post_is_valid(value: object) -> bool:
    """Delegate frozen validation with current root dependencies."""
    return _legacy_reply_validation._legacy_multi_model_context_post_is_valid(
        value,
        valid_string_post_id=valid_string_post_id,
    )


def _legacy_multi_model_reply_context_is_valid(context: object) -> bool:
    """Delegate frozen validation with current root dependencies."""
    return _legacy_reply_validation._legacy_multi_model_reply_context_is_valid(
        context,
        _LEGACY_MULTI_MODEL_REPLY_CONTEXT_FIELDS=_LEGACY_MULTI_MODEL_REPLY_CONTEXT_FIELDS,
        valid_string_post_id=valid_string_post_id,
        _legacy_multi_model_context_post_is_valid=_legacy_multi_model_context_post_is_valid,
    )


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
        _LEGACY_TESTED_REPLY_DRAFT_FIELDS=_LEGACY_TESTED_REPLY_DRAFT_FIELDS,
        _LEGACY_TESTED_REPLY_DIRECT_REPAIR_FIELDS=_LEGACY_TESTED_REPLY_DIRECT_REPAIR_FIELDS,
        _LEGACY_TESTED_REPLY_STRATEGY_VERSION=_LEGACY_TESTED_REPLY_STRATEGY_VERSION,
        _legacy_reply_utc_timestamp_is_valid=_legacy_reply_utc_timestamp_is_valid,
        _legacy_reply_value_sha256=_legacy_reply_value_sha256,
        _legacy_reply_sha256_is_valid=_legacy_reply_sha256_is_valid,
    )


_legacy_ai_first_claim_is_valid = _legacy_reply_validation._legacy_ai_first_claim_is_valid


def _legacy_ai_first_sentence_assessment_is_valid(value: object) -> bool:
    """Delegate frozen validation with current root dependencies."""
    return _legacy_reply_validation._legacy_ai_first_sentence_assessment_is_valid(
        value,
        _LEGACY_AI_FIRST_WORLD_CLAIM_FIELDS=_LEGACY_AI_FIRST_WORLD_CLAIM_FIELDS,
    )


def _legacy_ai_first_claim_audit_is_valid(value: object) -> bool:
    """Delegate frozen validation with current root dependencies."""
    return _legacy_reply_validation._legacy_ai_first_claim_audit_is_valid(
        value,
        _LEGACY_AI_FIRST_WORLD_CLAIM_FIELDS=_LEGACY_AI_FIRST_WORLD_CLAIM_FIELDS,
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
        _LEGACY_AI_FIRST_REPLY_DRAFT_FIELDS=_LEGACY_AI_FIRST_REPLY_DRAFT_FIELDS,
        _LEGACY_AI_FIRST_REPLY_STRATEGY_VERSION=_LEGACY_AI_FIRST_REPLY_STRATEGY_VERSION,
        _LEGACY_AI_FIRST_REPLY_MODES=_LEGACY_AI_FIRST_REPLY_MODES,
        _LEGACY_AI_FIRST_REPLY_TONES=_LEGACY_AI_FIRST_REPLY_TONES,
        _legacy_reply_utc_timestamp_is_valid=_legacy_reply_utc_timestamp_is_valid,
        _legacy_reply_value_sha256=_legacy_reply_value_sha256,
        _LEGACY_AI_FIRST_ANSWER_TYPES=_LEGACY_AI_FIRST_ANSWER_TYPES,
        _legacy_ai_first_claim_is_valid=_legacy_ai_first_claim_is_valid,
        _legacy_reply_sha256_is_valid=_legacy_reply_sha256_is_valid,
        _legacy_ai_first_sentence_assessment_is_valid=_legacy_ai_first_sentence_assessment_is_valid,
        _legacy_ai_first_claim_audit_is_valid=_legacy_ai_first_claim_audit_is_valid,
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
        _LEGACY_SINGLE_SOL_REPLY_DRAFT_FIELDS=_LEGACY_SINGLE_SOL_REPLY_DRAFT_FIELDS,
        _LEGACY_SINGLE_SOL_REPLY_STRATEGY_VERSION=_LEGACY_SINGLE_SOL_REPLY_STRATEGY_VERSION,
        _LEGACY_SINGLE_SOL_PROMPT_SHA256=_LEGACY_SINGLE_SOL_PROMPT_SHA256,
        _LEGACY_SINGLE_SOL_RESPONSE_SCHEMA_SHA256=_LEGACY_SINGLE_SOL_RESPONSE_SCHEMA_SHA256,
        _LEGACY_SINGLE_SOL_REPLY_KINDS=_LEGACY_SINGLE_SOL_REPLY_KINDS,
        _LEGACY_SINGLE_SOL_REASON_CODES=_LEGACY_SINGLE_SOL_REASON_CODES,
        _legacy_reply_utc_timestamp_is_valid=_legacy_reply_utc_timestamp_is_valid,
        _legacy_reply_sha256_is_valid=_legacy_reply_sha256_is_valid,
        _legacy_reply_value_sha256=_legacy_reply_value_sha256,
        valid_string_post_id=valid_string_post_id,
        bound_visible_conversation=bound_visible_conversation,
        MAX_TRUSTED_FACTS=MAX_TRUSTED_FACTS,
        MAX_SUPPLIED_IMAGES=MAX_SUPPLIED_IMAGES,
        _LEGACY_SINGLE_SOL_IMAGE_MIME_TYPES=_LEGACY_SINGLE_SOL_IMAGE_MIME_TYPES,
        SINGLE_CALL_MAX_IMAGE_BYTES=SINGLE_CALL_MAX_IMAGE_BYTES,
    )


def _legacy_ai_reply_receipt_draft_is_valid(data: dict, text: object) -> bool:
    """Delegate frozen validation with current root dependencies."""
    return _legacy_reply_validation._legacy_ai_reply_receipt_draft_is_valid(
        data, text,
        _LEGACY_TESTED_REPLY_STRATEGY_VERSION=_LEGACY_TESTED_REPLY_STRATEGY_VERSION,
        _legacy_multi_model_reply_context_is_valid=_legacy_multi_model_reply_context_is_valid,
        _legacy_tested_reply_draft_is_valid=_legacy_tested_reply_draft_is_valid,
        _LEGACY_AI_FIRST_REPLY_STRATEGY_VERSION=_LEGACY_AI_FIRST_REPLY_STRATEGY_VERSION,
        _legacy_ai_first_reply_draft_is_valid=_legacy_ai_first_reply_draft_is_valid,
        _LEGACY_SINGLE_SOL_REPLY_STRATEGY_VERSION=_LEGACY_SINGLE_SOL_REPLY_STRATEGY_VERSION,
        _legacy_single_sol_reply_draft_is_valid=_legacy_single_sol_reply_draft_is_valid,
    )


def mention_pagination_provenance_is_valid(value: object) -> bool:
    """Validate the exact mention continuation bound to a reply receipt."""
    return _reply_receipt_values.mention_pagination_provenance_is_valid(
        value,
        bounded_tweet_id_value=bounded_tweet_id_value,
    )


def _conversational_reply_receipt_is_semantically_valid(
    data: dict,
    *,
    lifecycle_state: str,
    legacy_recovery: bool = False,
) -> bool:
    """Validate one current receipt or a frozen lifecycle-recovery receipt."""
    return _reply_receipt_values._conversational_reply_receipt_is_semantically_valid(
        data,
        lifecycle_state=lifecycle_state,
        legacy_recovery=legacy_recovery,
        valid_string_post_id=valid_string_post_id,
        receipt_int=receipt_int,
        valid_receipt_epoch=valid_receipt_epoch,
        safe_reply_cap_date_str=safe_reply_cap_date_str,
        mention_pagination_provenance_is_valid=mention_pagination_provenance_is_valid,
        _legacy_ai_reply_receipt_draft_is_valid=_legacy_ai_reply_receipt_draft_is_valid,
        ai_reply_receipt_draft_is_valid=ai_reply_receipt_draft_is_valid,
        _LEGACY_TESTED_REPLY_STRATEGY_VERSION=_LEGACY_TESTED_REPLY_STRATEGY_VERSION,
        _LEGACY_AI_FIRST_REPLY_STRATEGY_VERSION=_LEGACY_AI_FIRST_REPLY_STRATEGY_VERSION,
        conversational_sending_receipt_from_confirmed=conversational_sending_receipt_from_confirmed,
        _legacy_sending_reply_receipt_is_semantically_valid=_legacy_sending_reply_receipt_is_semantically_valid,
        sending_reply_receipt_is_semantically_valid=sending_reply_receipt_is_semantically_valid,
        canonical_atomic_json_bytes=canonical_atomic_json_bytes,
    )


def conversational_sending_receipt_from_confirmed(
    confirmed_receipt: dict,
) -> dict:
    """Reconstruct the exact schema-v4 pre-transport reply receipt.

    Legacy confirmed receipts intentionally lack ``source_receipt_sha256`` and
    remain readable for local reconciliation, but cannot use this function to
    retire a current transport journal.
    """
    return _reply_receipt_values.conversational_sending_receipt_from_confirmed(
        confirmed_receipt,
        receipt_int=receipt_int,
        safe_reply_cap_date_str=safe_reply_cap_date_str,
    )


def confirmed_reply_receipt_is_semantically_valid(data: dict) -> bool:
    """Return whether a confirmed-reply receipt is internally consistent."""
    return _reply_receipt_values.confirmed_reply_receipt_is_semantically_valid(
        data,
        _conversational_reply_receipt_is_semantically_valid=_conversational_reply_receipt_is_semantically_valid,
    )


def sending_reply_receipt_is_semantically_valid(data: dict) -> bool:
    """Return whether a pre-send conversational-reply receipt is complete."""
    return _reply_receipt_values.sending_reply_receipt_is_semantically_valid(
        data,
        _conversational_reply_receipt_is_semantically_valid=_conversational_reply_receipt_is_semantically_valid,
    )


def _legacy_confirmed_reply_receipt_is_semantically_valid(data: dict) -> bool:
    """Accept a frozen draft only for local recovery after remote confirmation."""
    return _reply_receipt_values._legacy_confirmed_reply_receipt_is_semantically_valid(
        data,
        _conversational_reply_receipt_is_semantically_valid=_conversational_reply_receipt_is_semantically_valid,
    )


def _legacy_sending_reply_receipt_is_semantically_valid(data: dict) -> bool:
    """Recognise a frozen sending receipt as a barrier, never send authority."""
    return _reply_receipt_values._legacy_sending_reply_receipt_is_semantically_valid(
        data,
        _conversational_reply_receipt_is_semantically_valid=_conversational_reply_receipt_is_semantically_valid,
    )


def load_confirmed_reply_receipt() -> tuple[str, dict | None]:
    """Load confirmed reply receipt."""
    return _reply_delivery.load_confirmed_reply_receipt(
        load_receipt_json_no_follow=load_receipt_json_no_follow,
        CONFIRMED_REPLY_RECEIPT_FILE=CONFIRMED_REPLY_RECEIPT_FILE,
        log=log,
        sending_reply_receipt_is_semantically_valid=sending_reply_receipt_is_semantically_valid,
        _legacy_sending_reply_receipt_is_semantically_valid=_legacy_sending_reply_receipt_is_semantically_valid,
        confirmed_reply_receipt_is_semantically_valid=confirmed_reply_receipt_is_semantically_valid,
        _legacy_confirmed_reply_receipt_is_semantically_valid=_legacy_confirmed_reply_receipt_is_semantically_valid,
    )


def write_confirmed_reply_receipt(receipt: dict) -> None:
    """Write confirmed reply receipt."""
    return _reply_delivery.write_confirmed_reply_receipt(
        receipt,
        remote_receipt_retirement_is_blocking=remote_receipt_retirement_is_blocking,
        InvalidConfirmedReplyReceipt=InvalidConfirmedReplyReceipt,
        receipt_namespace_entry_exists=receipt_namespace_entry_exists,
        CONFIRMED_REPLY_RECEIPT_FILE=CONFIRMED_REPLY_RECEIPT_FILE,
        confirmed_reply_receipt_is_semantically_valid=confirmed_reply_receipt_is_semantically_valid,
        durable_create_receipt_json=durable_create_receipt_json,
        log=log,
    )


def write_sending_reply_receipt(receipt: dict) -> None:
    """Durably record a reply transaction before its remote create request."""
    return _reply_delivery.write_sending_reply_receipt(
        receipt,
        remote_receipt_retirement_is_blocking=remote_receipt_retirement_is_blocking,
        InvalidConfirmedReplyReceipt=InvalidConfirmedReplyReceipt,
        receipt_namespace_entry_exists=receipt_namespace_entry_exists,
        CONFIRMED_REPLY_RECEIPT_FILE=CONFIRMED_REPLY_RECEIPT_FILE,
        sending_reply_receipt_is_semantically_valid=sending_reply_receipt_is_semantically_valid,
        durable_create_receipt_json=durable_create_receipt_json,
        log=log,
    )


def bind_conversational_reply_attempt_time(receipt_template: dict) -> dict:
    """Bind a schema-v4 reply template to its immediately pre-send time."""
    return _reply_receipt_values.bind_conversational_reply_attempt_time(
        receipt_template,
        now_epoch=now_epoch,
        reply_cap_date_str=reply_cap_date_str,
        sending_reply_receipt_is_semantically_valid=sending_reply_receipt_is_semantically_valid,
    )


def _confirmed_reply_receipt_from_sending(
    sending_receipt: dict,
    *,
    reply_post_id: str,
    confirmation_epoch: int,
) -> dict:
    """Build the confirmed form without mutating its durable sending input."""
    return _reply_receipt_values._confirmed_reply_receipt_from_sending(
        sending_receipt,
        reply_post_id=reply_post_id,
        confirmation_epoch=confirmation_epoch,
        reply_cap_date_str=reply_cap_date_str,
        canonical_atomic_json_bytes=canonical_atomic_json_bytes,
    )


def _reply_confirmation_epoch_after_remote_success(
    sending_receipt: dict,
    observed_epoch: int | None = None,
) -> int:
    """Return a conservative monotonic wall time after remote confirmation."""
    return _reply_receipt_values._reply_confirmation_epoch_after_remote_success(
        sending_receipt, observed_epoch,
        now_epoch=now_epoch,
        receipt_int=receipt_int,
        log=log,
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
        canonical_atomic_json_bytes=canonical_atomic_json_bytes,
        TransportJournalError=TransportJournalError,
        _confirmed_reply_receipt_from_sending=_confirmed_reply_receipt_from_sending,
        confirmed_reply_receipt_is_semantically_valid=confirmed_reply_receipt_is_semantically_valid,
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
    return _reply_delivery._promote_legacy_sending_reply_receipt_from_confirmed_transport(
        sending_receipt,
        reply_post_id=reply_post_id,
        confirmation_epoch=confirmation_epoch,
        load_confirmed_reply_receipt=load_confirmed_reply_receipt,
        UnresolvedSendingReplyReceipt=UnresolvedSendingReplyReceipt,
        bind_confirmed_transport_source=bind_confirmed_transport_source,
        journal_path_for_receipt=journal_path_for_receipt,
        CONFIRMED_REPLY_RECEIPT_FILE=CONFIRMED_REPLY_RECEIPT_FILE,
        TRANSPORT_SOURCE_VALIDATOR_ID=TRANSPORT_SOURCE_VALIDATOR_ID,
        _legacy_conversational_transport_source_semantic_validator=_legacy_conversational_transport_source_semantic_validator,
        canonical_atomic_json_bytes=canonical_atomic_json_bytes,
        TransportJournalError=TransportJournalError,
        _confirmed_reply_receipt_from_sending=_confirmed_reply_receipt_from_sending,
        _legacy_confirmed_reply_receipt_is_semantically_valid=_legacy_confirmed_reply_receipt_is_semantically_valid,
        replace_bound_source_receipt=replace_bound_source_receipt,
        transaction_mutation_authority=transaction_mutation_authority,
        log=log,
    )


def remove_confirmed_reply_receipt(
    receipt: dict,
    *,
    sending_disposition: str | None = None,
) -> None:
    """Retire one exact conversational-reply source receipt."""
    return _reply_delivery.remove_confirmed_reply_receipt(
        receipt,
        sending_disposition=sending_disposition,
        CONFIRMED_REPLY_RECEIPT_FILE=CONFIRMED_REPLY_RECEIPT_FILE,
        json=json,
        InvalidConfirmedReplyReceipt=InvalidConfirmedReplyReceipt,
        retire_current_source_receipt=retire_current_source_receipt,
        canonical_atomic_json_bytes=canonical_atomic_json_bytes,
        log=log,
    )


def conversational_reply_confirmation_epoch(receipt: dict) -> int:
    """Return the best available confirmed time for a reply receipt."""
    return _reply_receipt_values.conversational_reply_confirmation_epoch(
        receipt,
        receipt_int=receipt_int,
        InvalidConfirmedReplyReceipt=InvalidConfirmedReplyReceipt,
    )


def _valid_iso_date(value: object) -> bool:
    """Return whether a value is a canonical calendar date."""
    return _reply_reconciliation._valid_iso_date(
        value,
        datetime=datetime,
    )


def _advance_reply_counters_to_confirmation_date(
    state: dict,
    confirmation_date: str,
    *,
    include_quote_lane: bool,
) -> None:
    """Advance stale daily reply buckets without rolling newer state backward."""
    return _reply_reconciliation._advance_reply_counters_to_confirmation_date(
        state, confirmation_date,
        include_quote_lane=include_quote_lane,
        _valid_iso_date=_valid_iso_date,
        log=log,
    )


def apply_confirmed_reply_receipt(state: dict, receipt: dict) -> None:
    """Apply confirmed reply receipt."""
    return _reply_reconciliation.apply_confirmed_reply_receipt(
        state, receipt,
        conversational_reply_confirmation_epoch=conversational_reply_confirmation_epoch,
        validate_pending_mention_candidate_authority=validate_pending_mention_candidate_authority,
        STATE_FILE=STATE_FILE,
        InvalidConfirmedReplyReceipt=InvalidConfirmedReplyReceipt,
        reply_cap_date_str=reply_cap_date_str,
        _advance_reply_counters_to_confirmation_date=_advance_reply_counters_to_confirmation_date,
        mention_pagination_provenance_is_valid=mention_pagination_provenance_is_valid,
        mention_pagination_has_canonical_page_ownership=mention_pagination_has_canonical_page_ownership,
        _reset_mention_candidate_authority=_reset_mention_candidate_authority,
        _emit_mention_authority_recovery=_emit_mention_authority_recovery,
        log=log,
        clear_pending_ai_reply=clear_pending_ai_reply,
        mark_quote_tweet_replied=mark_quote_tweet_replied,
        append_unique_durable=append_unique_durable,
        append_unique_capped=append_unique_capped,
        mark_daily_author_replied=mark_daily_author_replied,
        remove_pending_mention_candidate=remove_pending_mention_candidate,
        active_mention_backlog_reset_guard=active_mention_backlog_reset_guard,
        update_last_seen_mention_id=update_last_seen_mention_id,
        cache_tweet=cache_tweet,
        MY_USER_ID=MY_USER_ID,
        datetime=datetime,
        now_epoch=now_epoch,
        AI_REPLY_HISTORY_MAX_AGE_SECONDS=AI_REPLY_HISTORY_MAX_AGE_SECONDS,
        valid_string_post_id=valid_string_post_id,
        AI_REPLY_HISTORY_MAX_RECORDS=AI_REPLY_HISTORY_MAX_RECORDS,
        log_event=log_event,
    )


def update_last_seen_mention_id(state: dict, mention_id: str) -> None:
    """Delegate to the mention owner with current root dependencies."""
    return _mention_discovery.update_last_seen_mention_id(
        state,
        mention_id,
        log=log,
    )


def mark_mention_seen_if_applicable(state: dict, candidate: dict) -> None:
    """Delegate to the mention owner with current root dependencies."""
    return _mention_discovery.mark_mention_seen_if_applicable(
        state,
        candidate,
        log=log,
        remove_pending_mention_candidate=remove_pending_mention_candidate,
        update_last_seen_mention_id=update_last_seen_mention_id,
    )


def reconcile_confirmed_reply_receipt(state: dict) -> bool:
    """Reconcile a confirmed reply without duplicating the remote post."""
    return _reply_reconciliation.reconcile_confirmed_reply_receipt(
        state,
        load_confirmed_reply_receipt=load_confirmed_reply_receipt,
        UnresolvedSendingReplyReceipt=UnresolvedSendingReplyReceipt,
        InvalidConfirmedReplyReceipt=InvalidConfirmedReplyReceipt,
        CONFIRMED_REPLY_RECEIPT_FILE=CONFIRMED_REPLY_RECEIPT_FILE,
        verify_lane_transport_source_lineage_if_present=verify_lane_transport_source_lineage_if_present,
        log=log,
        apply_confirmed_reply_receipt=apply_confirmed_reply_receipt,
        save_state=save_state,
        ConfirmedReplyLocalPersistenceError=ConfirmedReplyLocalPersistenceError,
        retire_lane_transport_journal_if_present=retire_lane_transport_journal_if_present,
        remove_confirmed_reply_receipt=remove_confirmed_reply_receipt,
    )


def confirmed_reply_emergency_representation_is_complete(
    receipt: dict,
    state: dict,
) -> bool:
    """Return whether state alone durably suppresses a confirmed reply replay."""
    return _reply_reconciliation.confirmed_reply_emergency_representation_is_complete(
        receipt, state,
        confirmed_reply_receipt_is_semantically_valid=confirmed_reply_receipt_is_semantically_valid,
        conversational_reply_confirmation_epoch=conversational_reply_confirmation_epoch,
        InvalidConfirmedReplyReceipt=InvalidConfirmedReplyReceipt,
        receipt_int=receipt_int,
        pending_ai_reply_draft_key=pending_ai_reply_draft_key,
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
        sending_reply_receipt_is_semantically_valid=sending_reply_receipt_is_semantically_valid,
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
        _reply_confirmation_epoch_after_remote_success=_reply_confirmation_epoch_after_remote_success,
        _confirmed_reply_receipt_from_sending=_confirmed_reply_receipt_from_sending,
        confirmed_reply_receipt_is_semantically_valid=confirmed_reply_receipt_is_semantically_valid,
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
        retire_lane_transport_journal_if_present=retire_lane_transport_journal_if_present,
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
        _fresh_mention_ai_evaluations=_fresh_mention_ai_evaluations,
        _skip_hot_post_fetch=_skip_hot_post_fetch,
        AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY=AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY,
        AmbiguousRemotePostOutcome=AmbiguousRemotePostOutcome,
        ApiError=ApiError,
        CONFIRMED_REPLY_RECEIPT_FILE=CONFIRMED_REPLY_RECEIPT_FILE,
        ConfirmedReplyLocalPersistenceError=ConfirmedReplyLocalPersistenceError,
        ENABLE_AUTO_REPLIES=ENABLE_AUTO_REPLIES,
        MARK_AI_REPLIES_AS_AI=MARK_AI_REPLIES_AS_AI,
        MAX_AUTO_REPLIES_PER_DAY=MAX_AUTO_REPLIES_PER_DAY,
        MAX_MENTIONS_PER_CHECK=MAX_MENTIONS_PER_CHECK,
        MAX_REPLIES_PER_AUTHOR_PER_DAY=MAX_REPLIES_PER_AUTHOR_PER_DAY,
        MIN_SECONDS_BETWEEN_REPLIES=MIN_SECONDS_BETWEEN_REPLIES,
        MY_USER_ID=MY_USER_ID,
        NORMAL_CHECK_STATUS_API_ERROR=NORMAL_CHECK_STATUS_API_ERROR,
        NORMAL_CHECK_STATUS_CHECKED=NORMAL_CHECK_STATUS_CHECKED,
        NORMAL_CHECK_STATUS_DISABLED=NORMAL_CHECK_STATUS_DISABLED,
        NORMAL_CHECK_STATUS_POSTED=NORMAL_CHECK_STATUS_POSTED,
        NORMAL_CHECK_STATUS_SKIPPED_CAP=NORMAL_CHECK_STATUS_SKIPPED_CAP,
        NORMAL_CHECK_STATUS_SKIPPED_COOLDOWN=NORMAL_CHECK_STATUS_SKIPPED_COOLDOWN,
        NORMAL_CHECK_STATUS_SKIPPED_SPACING=NORMAL_CHECK_STATUS_SKIPPED_SPACING,
        PipelineResult=PipelineResult,
        ProvedRemotePostNonSuccess=ProvedRemotePostNonSuccess,
        REPLY_INCOMING_MAX_CHARS=REPLY_INCOMING_MAX_CHARS,
        RemoteOperationsPaused=RemoteOperationsPaused,
        ReplyEvidenceUnavailable=ReplyEvidenceUnavailable,
        SINGLE_CALL_STRATEGY_VERSION=SINGLE_CALL_STRATEGY_VERSION,
        UnrecoverableConfirmedReplyPersistenceError=UnrecoverableConfirmedReplyPersistenceError,
        ValidatedReply=ValidatedReply,
        _is_terminal_candidate_local_failure=_is_terminal_candidate_local_failure,
        _log_validated_single_call_reply=_log_validated_single_call_reply,
        _record_single_call_result=_record_single_call_result,
        active_author_evaluation_quarantine=active_author_evaluation_quarantine,
        api_error_is_reply_not_allowed=api_error_is_reply_not_allowed,
        append_unique_durable=append_unique_durable,
        apply_confirmed_reply_receipt=apply_confirmed_reply_receipt,
        bind_conversational_reply_attempt_time=bind_conversational_reply_attempt_time,
        block_if_ambiguous_remote_post=block_if_ambiguous_remote_post,
        build_context_for_reply_ai=build_context_for_reply_ai,
        cache_tweet=cache_tweet,
        clarification_reply_context=clarification_reply_context,
        clarification_thread_is_terminal=clarification_thread_is_terminal,
        clear_author_evaluation_quarantine_history=clear_author_evaluation_quarantine_history,
        clear_pending_ai_reply=clear_pending_ai_reply,
        completed_mention_watermark_covers_target=completed_mention_watermark_covers_target,
        conversational_reply_pipeline_enabled=conversational_reply_pipeline_enabled,
        copy=copy,
        daily_author_reply_count=daily_author_reply_count,
        daily_author_reply_counts=daily_author_reply_counts,
        dedupe_reply_candidates=dedupe_reply_candidates,
        generate_single_call_reply=generate_single_call_reply,
        get_hot_post_reply_candidates=get_hot_post_reply_candidates,
        get_mentions=get_mentions,
        in_api_cooldown=in_api_cooldown,
        is_probably_spam_or_not_worth_replying=is_probably_spam_or_not_worth_replying,
        lane_paused=lane_paused,
        load_confirmed_reply_receipt=load_confirmed_reply_receipt,
        log=log,
        log_ai_reply_posting_outcome=log_ai_reply_posting_outcome,
        log_event=log_event,
        mark_mention_seen_if_applicable=mark_mention_seen_if_applicable,
        maybe_mark_hot_post_reply_skipped=maybe_mark_hot_post_reply_skipped,
        maybe_reply_to_mentions=maybe_reply_to_mentions,
        mention_pagination_provenance_is_valid=mention_pagination_provenance_is_valid,
        now_epoch=now_epoch,
        pending_ai_reply=pending_ai_reply,
        pending_ai_reply_draft_key=pending_ai_reply_draft_key,
        pending_mention_candidates=pending_mention_candidates,
        post_conversational_reply_with_durable_identity=post_conversational_reply_with_durable_identity,
        prune_author_evaluation_quarantines=prune_author_evaluation_quarantines,
        prune_completed_mention_quarantine_evaluations=prune_completed_mention_quarantine_evaluations,
        prune_reply_evaluation_records=prune_reply_evaluation_records,
        reconcile_confirmed_reply_receipt=reconcile_confirmed_reply_receipt,
        record_api_error=record_api_error,
        record_qualifying_author_no_reply=record_qualifying_author_no_reply,
        record_terminal_reply_evaluation=record_terminal_reply_evaluation,
        recovery_comparison_account_replies=recovery_comparison_account_replies,
        remove_confirmed_reply_receipt=remove_confirmed_reply_receipt,
        reply_evidence_repository=reply_evidence_repository,
        reply_media_context_for_candidate=reply_media_context_for_candidate,
        reply_target_is_available_immediately_before_send=reply_target_is_available_immediately_before_send,
        reply_target_is_directly_eligible=reply_target_is_directly_eligible,
        reset_daily_reply_count_if_needed=reset_daily_reply_count_if_needed,
        retire_lane_transport_journal_if_present=retire_lane_transport_journal_if_present,
        retire_proved_rejected_conversational_reply_receipt=retire_proved_rejected_conversational_reply_receipt,
        save_state=save_state,
        store_pending_ai_reply=store_pending_ai_reply,
        terminal_reply_evaluation=terminal_reply_evaluation,
        trim_context_text=trim_context_text,
        valid_tweets_sorted_by_id=valid_tweets_sorted_by_id,
    )


# ---------------------------------------------------------------------
# Quote-post replies
# ---------------------------------------------------------------------

def load_extra_quote_watch_post_ids() -> list[str]:
    """Delegate to quote discovery with current root dependencies."""
    return _quote_discovery.load_extra_quote_watch_post_ids(
        EXTRA_QUOTE_WATCH_FILE=EXTRA_QUOTE_WATCH_FILE,
        MAX_EXTRA_QUOTE_WATCH_POSTS=MAX_EXTRA_QUOTE_WATCH_POSTS,
        log=log,
    )


def build_quote_lookup_post_ids(state: dict) -> list[str]:
    """Delegate to quote discovery with current root dependencies."""
    return _quote_discovery.build_quote_lookup_post_ids(
        state,
        EXTRA_QUOTE_WATCH_FILE=EXTRA_QUOTE_WATCH_FILE,
        MAX_QUOTE_POSTS_PER_CHECK=MAX_QUOTE_POSTS_PER_CHECK,
        get_recent_own_post_ids_for_quote_lookup=get_recent_own_post_ids_for_quote_lookup,
        load_extra_quote_watch_post_ids=load_extra_quote_watch_post_ids,
        log=log,
    )


def get_recent_own_post_ids_for_quote_lookup(state: dict) -> list[str]:
    """Delegate to quote discovery with current root dependencies."""
    return _quote_discovery.get_recent_own_post_ids_for_quote_lookup(
        state,
        QUOTE_POST_LOOKBACK_MAIN_POSTS=QUOTE_POST_LOOKBACK_MAIN_POSTS,
        seed_recent_own_post_ids_from_cache=seed_recent_own_post_ids_from_cache,
    )


def get_quote_tweets_for_post(post_id: str, state: dict | None = None) -> list[dict]:
    """Delegate to quote discovery with current root dependencies."""
    return _quote_discovery.get_quote_tweets_for_post(
        post_id,
        state,
        QUOTE_LOOKUP_API_MAX_RESULTS=QUOTE_LOOKUP_API_MAX_RESULTS,
        QUOTE_LOOKUP_MAX_PAGES_PER_POST=QUOTE_LOOKUP_MAX_PAGES_PER_POST,
        QUOTE_REPEATED_CURSOR_BACKOFF_SECONDS=QUOTE_REPEATED_CURSOR_BACKOFF_SECONDS,
        attach_media_to_tweets=attach_media_to_tweets,
        hashlib=hashlib,
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
        clean_text_for_reply_context=clean_text_for_reply_context,
        re=re,
    )


quote_author_profile_text = _quote_reply_cycle.quote_author_profile_text


def build_quote_tweet_reply_context(
    original_tweet: dict,
    quote_tweet: dict,
) -> dict[str, object]:
    """Build the canonical two-turn context for a direct quote-tweet."""
    return _quote_reply_cycle.build_quote_tweet_reply_context(
        original_tweet,
        quote_tweet,
        MAX_VISIBLE_TEXT_CHARACTERS=MAX_VISIBLE_TEXT_CHARACTERS,
        REPLY_INCOMING_MAX_CHARS=REPLY_INCOMING_MAX_CHARS,
        _log_single_call_context_summary=_log_single_call_context_summary,
        _reply_context_post=_reply_context_post,
        bound_visible_conversation=bound_visible_conversation,
        copy=copy,
        current_datetime=current_datetime,
        reply_media_context_for_candidate=reply_media_context_for_candidate,
        trim_context_text=trim_context_text,
        tweet_context_text=tweet_context_text,
    )


def mark_quote_tweet_skipped(state: dict, quote_id: str) -> None:
    """Mark quote tweet skipped."""
    return _quote_reply_cycle.mark_quote_tweet_skipped(
        state,
        quote_id,
        append_unique_capped=append_unique_capped,
    )


def mark_quote_tweet_replied(state: dict, quote_id: str) -> None:
    """Mark quote tweet replied."""
    return _quote_reply_cycle.mark_quote_tweet_replied(
        state,
        quote_id,
        append_unique_capped=append_unique_capped,
        append_unique_durable=append_unique_durable,
    )


def mark_quote_spam_author(state: dict, author_id: str) -> None:
    """Mark quote spam author."""
    return _quote_reply_cycle.mark_quote_spam_author(
        state,
        author_id,
        append_unique_capped=append_unique_capped,
        log=log,
    )


def maybe_reply_to_quote_tweets(state: dict) -> str:
    """Process eligible quote-tweet candidates under all reply limits."""
    return _quote_reply_cycle.maybe_reply_to_quote_tweets(
        state,
        AmbiguousRemotePostOutcome=AmbiguousRemotePostOutcome,
        ApiError=ApiError,
        CONFIRMED_REPLY_RECEIPT_FILE=CONFIRMED_REPLY_RECEIPT_FILE,
        ConfirmedReplyLocalPersistenceError=ConfirmedReplyLocalPersistenceError,
        ContextValidationError=ContextValidationError,
        ENABLE_AUTO_REPLIES=ENABLE_AUTO_REPLIES,
        ENABLE_QUOTE_TWEET_CHECKS=ENABLE_QUOTE_TWEET_CHECKS,
        MARK_AI_REPLIES_AS_AI=MARK_AI_REPLIES_AS_AI,
        MAX_AUTO_REPLIES_PER_DAY=MAX_AUTO_REPLIES_PER_DAY,
        MAX_QUOTE_POSTS_PER_CHECK=MAX_QUOTE_POSTS_PER_CHECK,
        MAX_QUOTE_REPLIES_PER_DAY=MAX_QUOTE_REPLIES_PER_DAY,
        MAX_REPLIES_PER_AUTHOR_PER_DAY=MAX_REPLIES_PER_AUTHOR_PER_DAY,
        MIN_SECONDS_BETWEEN_REPLIES=MIN_SECONDS_BETWEEN_REPLIES,
        MY_USER_ID=MY_USER_ID,
        PipelineResult=PipelineResult,
        ProvedRemotePostNonSuccess=ProvedRemotePostNonSuccess,
        QUOTE_CHECK_STATUS_CHECKED=QUOTE_CHECK_STATUS_CHECKED,
        QUOTE_CHECK_STATUS_DISABLED=QUOTE_CHECK_STATUS_DISABLED,
        QUOTE_CHECK_STATUS_POSTED=QUOTE_CHECK_STATUS_POSTED,
        QUOTE_CHECK_STATUS_SKIPPED_CAP=QUOTE_CHECK_STATUS_SKIPPED_CAP,
        QUOTE_CHECK_STATUS_SKIPPED_COOLDOWN=QUOTE_CHECK_STATUS_SKIPPED_COOLDOWN,
        QUOTE_CHECK_STATUS_SKIPPED_SPACING=QUOTE_CHECK_STATUS_SKIPPED_SPACING,
        RemoteOperationsPaused=RemoteOperationsPaused,
        ReplyEvidenceUnavailable=ReplyEvidenceUnavailable,
        SINGLE_CALL_STRATEGY_VERSION=SINGLE_CALL_STRATEGY_VERSION,
        UnrecoverableConfirmedReplyPersistenceError=UnrecoverableConfirmedReplyPersistenceError,
        ValidatedReply=ValidatedReply,
        _is_terminal_candidate_local_failure=_is_terminal_candidate_local_failure,
        _log_validated_single_call_reply=_log_validated_single_call_reply,
        _record_single_call_result=_record_single_call_result,
        api_error_is_permanent_target_failure=api_error_is_permanent_target_failure,
        api_error_is_reply_not_allowed=api_error_is_reply_not_allowed,
        apply_confirmed_reply_receipt=apply_confirmed_reply_receipt,
        bind_conversational_reply_attempt_time=bind_conversational_reply_attempt_time,
        block_if_ambiguous_remote_post=block_if_ambiguous_remote_post,
        build_quote_lookup_post_ids=build_quote_lookup_post_ids,
        build_quote_tweet_reply_context=build_quote_tweet_reply_context,
        cache_tweet=cache_tweet,
        clean_text_for_reply_context=clean_text_for_reply_context,
        clear_pending_ai_reply=clear_pending_ai_reply,
        conversational_reply_pipeline_enabled=conversational_reply_pipeline_enabled,
        copy=copy,
        daily_author_reply_count=daily_author_reply_count,
        daily_author_reply_counts=daily_author_reply_counts,
        generate_single_call_reply=generate_single_call_reply,
        get_quote_tweets_for_post=get_quote_tweets_for_post,
        get_tweet_by_id_cached=get_tweet_by_id_cached,
        in_api_cooldown=in_api_cooldown,
        is_probably_spam_or_not_worth_replying=is_probably_spam_or_not_worth_replying,
        lane_paused=lane_paused,
        load_confirmed_reply_receipt=load_confirmed_reply_receipt,
        log=log,
        log_ai_reply_posting_outcome=log_ai_reply_posting_outcome,
        log_event=log_event,
        mark_quote_spam_author=mark_quote_spam_author,
        mark_quote_tweet_skipped=mark_quote_tweet_skipped,
        now_epoch=now_epoch,
        pending_ai_reply=pending_ai_reply,
        post_conversational_reply_with_durable_identity=post_conversational_reply_with_durable_identity,
        quote_author_profile_text=quote_author_profile_text,
        quote_tweet_directly_quotes_original=quote_tweet_directly_quotes_original,
        quote_tweet_is_old_enough=quote_tweet_is_old_enough,
        reconcile_confirmed_reply_receipt=reconcile_confirmed_reply_receipt,
        record_api_error=record_api_error,
        record_terminal_reply_evaluation=record_terminal_reply_evaluation,
        recovery_comparison_account_replies=recovery_comparison_account_replies,
        remove_confirmed_reply_receipt=remove_confirmed_reply_receipt,
        reply_evidence_repository=reply_evidence_repository,
        reply_media_context_for_candidate=reply_media_context_for_candidate,
        reply_target_is_available_immediately_before_send=reply_target_is_available_immediately_before_send,
        reset_daily_quote_reply_count_if_needed=reset_daily_quote_reply_count_if_needed,
        reset_daily_reply_count_if_needed=reset_daily_reply_count_if_needed,
        retire_lane_transport_journal_if_present=retire_lane_transport_journal_if_present,
        retire_proved_rejected_conversational_reply_receipt=retire_proved_rejected_conversational_reply_receipt,
        save_state=save_state,
        store_pending_ai_reply=store_pending_ai_reply,
        terminal_reply_evaluation=terminal_reply_evaluation,
        valid_tweets_sorted_by_id=valid_tweets_sorted_by_id,
    )


# ---------------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------------

def next_quote_schedule_fields(from_epoch: int | None = None, *, delay: int | None = None) -> tuple[dict, int]:
    """Return the next quote schedule fields."""
    if from_epoch is None:
        from_epoch = now_epoch()

    if delay is None:
        delay = random.randint(POST_SLEEP_MIN, POST_SLEEP_MAX)
    return {"next_quote_post_epoch": int(from_epoch) + delay}, delay


def schedule_next_quote_post(state: dict, from_epoch: int | None = None, *, save: bool = True) -> None:
    """Perform the schedule next quote post operation."""
    fields, delay = next_quote_schedule_fields(from_epoch)
    apply_state_fields(state, fields)
    if save:
        save_state(state)

    log.info(
        "Next quote/image post in %d seconds at %s",
        delay,
        datetime.fromtimestamp(state["next_quote_post_epoch"]).strftime("%Y-%m-%d %H:%M:%S"),
    )


def run_reply_lane_checks_for_tick(
    state: dict,
    current: int,
    last_reply_check_epoch: int,
    last_quote_tweet_check_epoch: int,
) -> tuple[int, int]:
    """Run one scheduled reply-lane arbitration tick."""
    return _tick_coordination.run_reply_lane_checks_for_tick(
        state,
        current,
        last_reply_check_epoch,
        last_quote_tweet_check_epoch,
        AmbiguousRemotePostOutcome=AmbiguousRemotePostOutcome,
        ENABLE_AUTO_REPLIES=ENABLE_AUTO_REPLIES,
        ENABLE_QUOTE_TWEET_CHECKS=ENABLE_QUOTE_TWEET_CHECKS,
        MIN_SECONDS_BETWEEN_REPLIES=MIN_SECONDS_BETWEEN_REPLIES,
        NORMAL_CHECK_STATUS_POSTED=NORMAL_CHECK_STATUS_POSTED,
        NORMAL_CHECK_STATUS_SKIPPED_SPACING=NORMAL_CHECK_STATUS_SKIPPED_SPACING,
        QUOTE_CHECK_EVERY_SECONDS=QUOTE_CHECK_EVERY_SECONDS,
        QUOTE_CHECK_SPACING_RETRY_SECONDS=QUOTE_CHECK_SPACING_RETRY_SECONDS,
        QUOTE_CHECK_STATUS_POSTED=QUOTE_CHECK_STATUS_POSTED,
        QUOTE_CHECK_STATUS_SKIPPED_SPACING=QUOTE_CHECK_STATUS_SKIPPED_SPACING,
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


def main() -> None:
    """Run the command-line entry point."""
    require_production_bootstrap()
    report_bot_health_progress("startup")
    random.seed()
    acquire_instance_lock()
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

    images_at_start = glob(IMAGE_GLOB)
    log.info("Images found at startup=%d", len(images_at_start))

    if ENABLE_DAILY_MEME_POSTS:
        meme_candidates_at_start = list_meme_candidates()
        log.info("Meme candidates found at startup=%d", len(meme_candidates_at_start))

    validate_original_editorial_shadow_startup()
    validate_generated_identity_shadow_startup()

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

    initialise_engagement_question_experiment(
        state,
        current_epoch=startup_current,
    )
    publish_pending_engagement_question_notification(state)

    seed_recent_own_post_ids_from_cache(state)
    save_state(state)

    current = now_epoch()
    last_reply_check_epoch, reply_epoch_changed = scheduler_epoch_from_state(
        state,
        "last_reply_check_epoch",
        current=current,
    )
    last_quote_tweet_check_epoch, quote_epoch_changed = scheduler_epoch_from_state(
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

        publish_pending_engagement_question_notification(state)

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
        last_reply_check_epoch, last_quote_tweet_check_epoch = run_reply_lane_checks_for_tick(
            state,
            current,
            last_reply_check_epoch,
            last_quote_tweet_check_epoch,
        )
        report_bot_health_progress("main_loop")
        if ambiguous_remote_post_is_blocking():
            report_bot_health_progress(
                "remote_write_blocked",
                remote_write_blocked=True,
                loop_completed=True,
            )
            continue

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

        if ambiguous_remote_post_is_blocking():
            report_bot_health_progress(
                "remote_write_blocked",
                remote_write_blocked=True,
                loop_completed=True,
            )
            continue

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

        log.debug("Sleeping for 60 seconds")
        report_bot_health_progress("sleep", loop_completed=True)
        sleep(60)


# ---------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------

def _self_test_ok(label: str, ok: bool, detail: str = "") -> bool:
    status = "OK" if ok else "FAIL"
    message = f"SELFTEST {status}: {label}"
    if detail:
        message += f" - {detail}"
    if ok:
        log.info(message)
    else:
        log.error(message)
    return ok


def _self_test_warn(label: str, ok: bool, detail: str = "") -> None:
    status = "OK" if ok else "WARN"
    message = f"SELFTEST {status}: {label}"
    if detail:
        message += f" - {detail}"
    if ok:
        log.info(message)
    else:
        log.warning(message)


def run_self_test() -> int:
    """Run local checks without posting or calling X or OpenAI."""
    require_production_bootstrap()
    log.info("Running self-test only; no X or OpenAI API calls will be made")
    failures = 0

    def require(label: str, ok: bool, detail: str = "") -> None:
        nonlocal failures
        if not _self_test_ok(label, ok, detail):
            failures += 1

    require("base directory exists", BASE_DIR.exists(), str(BASE_DIR))
    require("lines file exists", LINES_FILE.exists(), str(LINES_FILE))
    if LINES_FILE.exists():
        try:
            with open(LINES_FILE, "r") as f:
                non_empty_lines = sum(1 for line in f if line.strip())
            require("lines file has non-empty lines", non_empty_lines > 0, f"non_empty_lines={non_empty_lines}")
        except Exception as exc:
            require("lines file readable", False, str(exc))

    images = glob(IMAGE_GLOB)
    require("quote/image image glob has files", len(images) > 0, f"count={len(images)} glob={IMAGE_GLOB}")

    try:
        overrides = load_validated_local_config_overrides()
        _self_test_warn(
            "local config file present",
            overrides is not None,
            str(LOCAL_CONFIG_FILE),
        )
        if overrides is not None:
            require(
                "local config validates",
                True,
                f"overrides={len(overrides)}",
            )
    except Exception as exc:
        _self_test_warn("local config file present", True, str(LOCAL_CONFIG_FILE))
        require("local config validates", False, str(exc))

    _self_test_warn("runtime control file absent", not CONTROL_FILE.exists(), str(CONTROL_FILE))
    ctrl = load_control()
    require(
        "runtime control validates",
        not bool(ctrl.get("_control_fail_closed", False)),
        str(CONTROL_FILE),
    )

    if STATE_FILE.exists():
        try:
            with open(STATE_FILE, "r") as f:
                state = json.load(f)
            require("state file parses", isinstance(state, dict), str(STATE_FILE))
            _self_test_warn("state has next_reply_lane_priority", "next_reply_lane_priority" in state)
            _self_test_warn("state has hot-post since_id map", isinstance(state.get("hot_post_reply_since_ids", {}), dict))
        except Exception as exc:
            require("state file parses", False, str(exc))
    else:
        _self_test_warn("state file present", False, str(STATE_FILE))

    if ENABLE_DAILY_MEME_POSTS:
        _self_test_warn("meme directory present", MEME_DIR.exists(), str(MEME_DIR))
        if MEME_DIR.exists():
            meme_count = len(list_meme_candidates())
            _self_test_warn("meme candidates available", meme_count > 0, f"count={meme_count}")
        _self_test_warn("meme analysis file present", MEME_ANALYSIS_FILE.exists(), str(MEME_ANALYSIS_FILE))

    _self_test_warn("extra quote watch file present", EXTRA_QUOTE_WATCH_FILE.exists(), str(EXTRA_QUOTE_WATCH_FILE))
    if EXTRA_QUOTE_WATCH_FILE.exists():
        try:
            watch_ids = load_extra_quote_watch_post_ids()
            _self_test_warn("extra quote watch IDs loaded", True, f"count={len(watch_ids)}")
        except Exception as exc:
            require("extra quote watch file readable", False, str(exc))

    require("X_CONSUMER_KEY set", bool(CONSUMER_KEY))
    require("X_CONSUMER_SECRET set", bool(CONSUMER_SECRET))
    require("X_ACCESS_TOKEN set", bool(ACCESS_TOKEN))
    require("X_ACCESS_SECRET set", bool(ACCESS_SECRET))
    require("X_MY_USER_ID set", bool(MY_USER_ID))
    if ENABLE_AUTO_REPLIES and single_call_reply.get("enabled") is True:
        require(
            "OPENAI_API_KEY set when single-call replies enabled",
            bool(OPENAI_API_KEY),
        )
    _self_test_warn("X_BEARER_TOKEN set", bool(X_BEARER_TOKEN), "needed/preferred for quote/hot search")

    require("MAX_AUTO_REPLIES_PER_DAY positive", int(MAX_AUTO_REPLIES_PER_DAY) > 0, str(MAX_AUTO_REPLIES_PER_DAY))
    require("MAX_QUOTE_REPLIES_PER_DAY positive", int(MAX_QUOTE_REPLIES_PER_DAY) > 0, str(MAX_QUOTE_REPLIES_PER_DAY))
    require("MIN_SECONDS_BETWEEN_REPLIES positive", int(MIN_SECONDS_BETWEEN_REPLIES) > 0, str(MIN_SECONDS_BETWEEN_REPLIES))
    require("REPLY_CHECK_EVERY_SECONDS positive", int(REPLY_CHECK_EVERY_SECONDS) > 0, str(REPLY_CHECK_EVERY_SECONDS))
    require("QUOTE_CHECK_EVERY_SECONDS positive", int(QUOTE_CHECK_EVERY_SECONDS) > 0, str(QUOTE_CHECK_EVERY_SECONDS))
    runtime_config_errors = validate_runtime_config_values(
        {name: globals()[name] for name in LOCAL_CONFIG_ALLOWED_KEYS if name in globals()}
    )
    require("runtime config validates", not runtime_config_errors, "; ".join(runtime_config_errors))

    if failures:
        log.error("Self-test finished with %d failure(s)", failures)
        return 1

    log.info("Self-test finished successfully")
    return 0


def run_test_cycle() -> int:
    """Run one local integration-test pass without entering the posting loop."""
    if not require_test_mode("--test-cycle"):
        return 2
    require_production_bootstrap()

    acquire_instance_lock()
    require_established_installation_after_ledger_recovery()
    reconcile_runtime_historical_context_state()
    block_if_ambiguous_remote_post()

    log.info("Running one test cycle")
    log.info("Base dir=%s", BASE_DIR)
    log.info("State file=%s", STATE_FILE)
    log.info("Log file=%s", LOG_FILE)
    log.info("X base=%s", X_BASE)
    log.info("X upload base=%s", X_UPLOAD_BASE)
    log.info("OpenAI base=%s", OPENAI_BASE)

    state = load_runtime_state()

    reply_lane_priority = str(state.get("next_reply_lane_priority", "normal") or "normal")

    quote_status = None
    before_reply_epoch = int(state.get("last_reply_epoch", 0) or 0)

    def finish_if_ambiguity_blocked() -> bool:
        if not ambiguous_remote_post_is_blocking():
            return False
        log.critical("Test cycle stopped after an ambiguous remote post; no later lane will run")
        save_state(state)
        return True

    def run_test_reply_action(lane: str, action) -> tuple[object | None, bool]:
        try:
            return action(state), False
        except (
            AmbiguousRemotePostOutcome,
            UnrecoverableConfirmedReplyPersistenceError,
        ):
            log.critical(
                "Test-cycle %s reply lane stopped by the global remote-write "
                "safety barrier",
                lane,
                exc_info=True,
            )
            wait_for_durable_barrier_before_one_shot_exit(
                lane=f"{lane}_reply",
            )
            return None, True

    if reply_lane_priority == "quote":
        quote_status, safety_stopped = run_test_reply_action(
            "quote_tweet",
            maybe_reply_to_quote_tweets,
        )
        if safety_stopped:
            return 0
        if finish_if_ambiguity_blocked():
            return 0
        log.info("Test-cycle quote-tweet check status=%s", quote_status)
        log_event("quote_check_status", status=quote_status, priority="test_cycle")
        after_quote_epoch = int(state.get("last_reply_epoch", 0) or 0)

        if after_quote_epoch != before_reply_epoch:
            state["next_reply_lane_priority"] = "normal"
            save_state(state)
            log.info("Test-cycle quote-tweet lane posted; next reply-lane priority=normal")
        else:
            _normal_status, safety_stopped = run_test_reply_action(
                "normal",
                maybe_reply_to_mentions,
            )
            if safety_stopped:
                return 0
            if finish_if_ambiguity_blocked():
                return 0
            after_reply_epoch = int(state.get("last_reply_epoch", 0) or 0)
            if after_reply_epoch != after_quote_epoch:
                state["next_reply_lane_priority"] = "quote"
                save_state(state)
                log.info("Test-cycle normal/hot-post lane posted; next reply-lane priority=quote")
    else:
        _normal_status, safety_stopped = run_test_reply_action(
            "normal",
            maybe_reply_to_mentions,
        )
        if safety_stopped:
            return 0
        if finish_if_ambiguity_blocked():
            return 0
        after_reply_epoch = int(state.get("last_reply_epoch", 0) or 0)

        if after_reply_epoch != before_reply_epoch:
            state["next_reply_lane_priority"] = "quote"
            save_state(state)
            log.info("Test-cycle normal/hot-post lane posted; next reply-lane priority=quote")
        else:
            quote_status, safety_stopped = run_test_reply_action(
                "quote_tweet",
                maybe_reply_to_quote_tweets,
            )
            if safety_stopped:
                return 0
            if finish_if_ambiguity_blocked():
                return 0
            log.info("Test-cycle quote-tweet check status=%s", quote_status)
            log_event("quote_check_status", status=quote_status, priority="test_cycle")
            after_quote_epoch = int(state.get("last_reply_epoch", 0) or 0)
            if after_quote_epoch != after_reply_epoch:
                state["next_reply_lane_priority"] = "normal"
                save_state(state)
                log.info("Test-cycle quote-tweet lane posted; next reply-lane priority=normal")

    save_state(state)
    log.info("Test cycle finished")
    return 0


def run_test_main_tick() -> int:
    """Run the production reply-lane tick once for local integration tests."""
    if not require_test_mode("--test-main-tick"):
        return 2
    require_production_bootstrap()
    report_bot_health_progress("startup")

    acquire_instance_lock()
    report_bot_health_progress("recovery")
    require_established_installation_after_ledger_recovery()
    reconcile_runtime_historical_context_state()
    block_if_ambiguous_remote_post()

    log.info("Running one test production reply-lane tick")
    state = load_runtime_state()
    current = now_epoch()
    last_reply_check_epoch, reply_epoch_changed = scheduler_epoch_from_state(
        state,
        "last_reply_check_epoch",
        current=current,
    )
    last_quote_tweet_check_epoch, quote_epoch_changed = scheduler_epoch_from_state(
        state,
        "last_quote_tweet_check_epoch",
        current=current,
    )
    if reply_epoch_changed or quote_epoch_changed:
        save_state(state)

    report_bot_health_progress("main_loop", loop_started=True)
    report_bot_health_progress("reply_checks")
    run_reply_lane_checks_for_tick(
        state,
        current,
        last_reply_check_epoch=last_reply_check_epoch,
        last_quote_tweet_check_epoch=last_quote_tweet_check_epoch,
    )
    report_bot_health_progress("main_loop")
    if ambiguous_remote_post_is_blocking():
        wait_for_durable_barrier_before_one_shot_exit(
            lane="production_reply_tick",
        )
        return 0

    save_state(state)
    log.info("Test production reply-lane tick finished")
    report_bot_health_progress("shutdown", loop_completed=True)
    return 0


def require_test_mode(command_name: str) -> bool:
    """Require the immutable import-time test-mode safety configuration."""
    if not IMPORT_TIME_TEST_MODE:
        log.error("%s requires MRS_TEST_MODE=1 before bot import", command_name)
        return False
    return True


def prepare_test_main_post_state(state: dict) -> None:
    """Prepare test main post state."""
    if ENABLE_DAILY_MEME_POSTS:
        ensure_meme_schedule_initialized(state)


def wait_for_durable_barrier_before_one_shot_exit(*, lane: str) -> None:
    """Keep a one-shot posting process alive while its only barrier is memory."""
    if (
        not remote_write_safety_incident_is_latched()
        or durable_remote_write_safety_barrier_exists()
    ):
        return
    log.critical(
        "The one-shot %s command cannot exit because its only remote-write safety "
        "barrier is process-local. Create and verify a durable reconciliation "
        "marker before terminating this process.",
        lane,
    )
    while not durable_remote_write_safety_barrier_exists():
        sleep(60)
    log.critical(
        "A durable remote-write safety marker is now present for one-shot lane=%s; "
        "process exit is restart-safe",
        lane,
    )


def run_test_post_quote() -> int:
    """Run one quote/image post cycle for local integration tests."""
    if not require_test_mode("--test-post-quote"):
        return 2
    require_production_bootstrap()

    acquire_instance_lock()
    require_established_installation_after_ledger_recovery()
    reconcile_runtime_historical_context_state()
    block_if_ambiguous_remote_post(
        allow_confirmed_pending_schedule_reconciliation=True
    )

    log.info("Running one test quote/image post cycle")
    state = load_runtime_state()
    prepare_test_main_post_state(state)

    if lane_paused("disable_quote_posts"):
        log.warning("Skipping test quote/image post due to runtime control file")
        save_state(state)
        return 0

    with open(LINES_FILE, encoding="utf-8") as f:
        quote_lines_for_history = f.readlines()
    lines_used = load_quote_used_hashes(quote_lines_for_history)
    images_used = load_image_used_basenames(current_image_paths())

    try:
        post_random_quote(lines_used, images_used, state)
    except UnrecoverableConfirmedPostPersistenceError:
        log.critical(
            "REMOTE X POST WAS CONFIRMED WITHOUT A COMPLETE DURABLE LOCAL "
            "REPRESENTATION. The one-shot quote process must not exit while only "
            "its in-memory safety latch survives.",
            exc_info=True,
        )
        wait_for_durable_barrier_before_one_shot_exit(lane="quote_image")
        return 3
    except ConfirmedPostLocalPersistenceError:
        log.critical(
            "REMOTE X POST WAS CONFIRMED; DO NOT RETRY MANUALLY. "
            "Test quote/image local persistence/recovery needs attention.",
            exc_info=True,
        )
        save_state(state)
        return 3
    except AmbiguousRemotePostOutcome as exc:
        log.critical(
            "The one-shot quote remote outcome is ambiguous; refusing normal exit "
            "while only an in-memory safety latch survives.",
            exc_info=True,
        )
        wait_for_durable_barrier_before_one_shot_exit(lane="quote_image")
        record_api_error(state, exc, "x", scope="write")
        save_state(state)
        return 1
    except ApiError as exc:
        log.exception("Test quote/image post failed due to API error")
        record_api_error(state, exc, "x", scope="write")
        save_state(state)
        return 1
    except Exception:
        log.exception("Test quote/image post failed unexpectedly")
        save_state(state)
        return 1

    log.info("Test quote/image post cycle finished")
    return 0


def run_test_post_meme() -> int:
    """Run one daily meme post cycle for local integration tests."""
    if not require_test_mode("--test-post-meme"):
        return 2
    require_production_bootstrap()

    acquire_instance_lock()
    require_established_installation_after_ledger_recovery()
    reconcile_runtime_historical_context_state()
    block_if_ambiguous_remote_post()

    log.info("Running one test daily meme post cycle")
    state = load_runtime_state()
    prepare_test_main_post_state(state)

    if lane_paused("disable_meme_posts"):
        log.warning("Skipping test daily meme post due to runtime control file")
        save_state(state)
        return 0

    try:
        post_next_meme(state)
    except UnrecoverableConfirmedPostPersistenceError:
        log.critical(
            "REMOTE X POST WAS CONFIRMED WITHOUT A COMPLETE DURABLE LOCAL "
            "REPRESENTATION. The one-shot meme process must not exit while only "
            "its in-memory safety latch survives.",
            exc_info=True,
        )
        wait_for_durable_barrier_before_one_shot_exit(lane="daily_meme")
        return 3
    except ConfirmedPostLocalPersistenceError:
        log.critical(
            "REMOTE X POST WAS CONFIRMED; DO NOT RETRY MANUALLY. "
            "Test daily meme local persistence/recovery needs attention.",
            exc_info=True,
        )
        save_state(state)
        return 3
    except AmbiguousRemotePostOutcome as exc:
        log.critical(
            "The one-shot meme remote outcome is ambiguous; refusing normal exit "
            "while only an in-memory safety latch survives.",
            exc_info=True,
        )
        wait_for_durable_barrier_before_one_shot_exit(lane="daily_meme")
        record_api_error(state, exc, "x", scope="write")
        save_state(state)
        return 1
    except ApiError as exc:
        log.exception("Test daily meme post failed due to API error")
        record_api_error(state, exc, "x", scope="write")
        save_state(state)
        return 1
    except Exception:
        log.exception("Test daily meme post failed unexpectedly")
        save_state(state)
        return 1

    log.info("Test daily meme post cycle finished")
    return 0


def run_cli(argv: list[str] | tuple[str, ...] | None = None) -> int | None:
    """Validate one complete command line, then bootstrap and dispatch it."""

    process_arguments = tuple(sys.argv[1:])
    arguments = process_arguments if argv is None else tuple(argv)
    try:
        if process_arguments != IMPORT_TIME_CLI_ARGUMENTS:
            raise CliUsageError("process argv changed after module import")
        if argv is not None and arguments != IMPORT_TIME_CLI_ARGUMENTS:
            raise CliUsageError(
                "explicit argv must exactly match the import-time command line"
            )
        mode = parse_cli_mode(IMPORT_TIME_CLI_ARGUMENTS)
        if mode in TEST_MODE_REQUIRED_CLI_FLAGS and not IMPORT_TIME_TEST_MODE:
            raise CliUsageError(
                f"{mode} requires MRS_TEST_MODE=1 before bot import"
            )
    except CliUsageError as exc:
        print(f"{CLI_USAGE}\nmrsMThatcher2.py: error: {exc}", file=sys.stderr)
        return 2

    # Argument validation is deliberately complete before this call.  No
    # invalid or ambiguous argv may reach configuration loading, the instance
    # lock, durable state, or any remote-operation boundary.
    production_bootstrap()
    if mode == "--initialise":
        return initialise_installation()
    if mode == "--self-test":
        return run_self_test()
    if mode == "--test-cycle":
        return run_test_cycle()
    if mode == "--test-main-tick":
        return run_test_main_tick()
    if mode == "--test-post-quote":
        return run_test_post_quote()
    if mode == "--test-post-meme":
        return run_test_post_meme()
    main()
    return None


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
