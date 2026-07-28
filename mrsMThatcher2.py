#!/usr/bin/env python3
"""Run the production MrsMThatcher posting and conversational-reply bot."""


from __future__ import annotations

import html
import copy
import fcntl
import hashlib
import json
import logging
import math
import mimetypes
import os
import random
import re
import signal
import shutil
import sys
import threading
from collections import Counter
from datetime import datetime, timedelta
from glob import glob
from logging.handlers import RotatingFileHandler
from pathlib import Path
from time import sleep
from urllib.parse import urlsplit, urlunsplit

import requests
from requests_oauthlib import OAuth1
from urllib3.util import Timeout


SELF_TEST_REQUESTED = "--self-test" in sys.argv
TEST_CYCLE_REQUESTED = "--test-cycle" in sys.argv
TEST_MAIN_TICK_REQUESTED = "--test-main-tick" in sys.argv
TEST_POST_QUOTE_REQUESTED = "--test-post-quote" in sys.argv
TEST_POST_MEME_REQUESTED = "--test-post-meme" in sys.argv
INITIALISE_REQUESTED = "--initialise" in sys.argv
TEST_MODE = os.getenv("MRS_TEST_MODE") == "1"


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

# ---------------------------------------------------------------------
# Reply automation
# ---------------------------------------------------------------------

ENABLE_AUTO_REPLIES = True

REPLY_CHECK_EVERY_SECONDS = 900
MAX_AUTO_REPLIES_PER_DAY = 24
MAX_REPLIES_PER_AUTHOR_PER_DAY = 1
CLARIFICATION_REPLY_WINDOW_SECONDS = 24 * 60 * 60
MAX_MENTIONS_PER_CHECK = 5
MENTIONS_MAX_PAGES_PER_CHECK = 3

# Optional hot-post reply lane. This reuses the same watched post ID file
# as the quote-tweet lane, but looks for ordinary replies in that post
# conversation via recent search and feeds them through the normal reply logic.
ENABLE_HOT_POST_REPLY_CHECKS = True
MAX_HOT_POST_REPLIES_PER_CHECK = 10
HOT_POST_REPLY_SEARCH_API_MAX_RESULTS = 10
HOT_POST_REPLY_SEARCH_MAX_PAGES_PER_CHECK = 3
HOT_POST_REPLY_USE_SINCE_ID = True
HOT_POST_REPLY_FULL_RESCAN_EVERY_CHECKS = 12

MIN_SECONDS_BETWEEN_REPLIES = 1800

DRY_RUN_REPLIES = False
MARK_AI_REPLIES_AS_AI = False

ALWAYS_FETCH_PARENT_FOR_CONTEXT = True
SKIP_REPLIES_TO_OWN_AUTO_REPLIES = False

THREAD_CONTEXT_MAX_DEPTH = 3
THREAD_CONTEXT_MAX_CHARS_PER_POST = 500
THREAD_CONTEXT_MAX_TOTAL_CHARS = 1500
MAX_REPLY_CONTEXT_PHOTOS = 2
GENERATED_IMAGE_ORIGIN_QUOTE_BOOST = 4

TWEET_CACHE_MAX_AGE_SECONDS = 7 * 24 * 3600
TWEET_CACHE_MAX_ITEMS = 500

# ---------------------------------------------------------------------
# Safety / circuit breaker
# ---------------------------------------------------------------------

ERROR_WINDOW_SECONDS = 3600
MAX_X_ERRORS_PER_WINDOW = 3
MAX_XAI_ERRORS_PER_WINDOW = 3
COOLDOWN_AFTER_REPEATED_ERRORS_SECONDS = 3600
COOLDOWN_AFTER_429_SECONDS = 3600

# ---------------------------------------------------------------------
# xAI/Grok
# ---------------------------------------------------------------------

XAI_MODEL = os.getenv("XAI_MODEL", "grok-4.3")
MAX_REPLY_CHARS = 270
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
ai_first_reply_strategy = {
    "enabled": False,
    "strategy_version": "ai-first-reply-v3",
    "proposer_model": XAI_MODEL,
    "reviewer_model": XAI_MODEL,
    "evidence_model": XAI_MODEL,
    "research_corpus_path": "semantic_alignment_research/quote_research_full_001",
    "maximum_model_calls": 6,
    "proposer_timeout_seconds": 60,
    "evidence_timeout_seconds": 60,
    "reviewer_timeout_seconds": 60,
    "proposer_max_output_tokens": 900,
    "evidence_max_output_tokens": 1800,
    "reviewer_max_output_tokens": 900,
    "maximum_revisions": 1,
    "maximum_invalid_response_retries": 1,
    "maximum_claims": 6,
    "maximum_evidence_packets_per_claim": 6,
    "maximum_evidence_passages_per_claim": 24,
    "maximum_reply_sentences": 2,
    "fail_closed": True,
}
quote_image_semantic_veto = {
    "enabled": False,
    "mode": "shadow",
    "manifest_path": (
        "semantic_alignment_research/quote_image_semantic_veto_001/shadow/"
        "material_veto_v2_shadow_manifest.json"
    ),
    "fail_open": True,
    "record_best_allowed_alternative": True,
    "maximum_shadow_history": 10_000,
}

LINES_USED_FILE = BASE_DIR / "lines_used.json"
IMAGES_USED_FILE = BASE_DIR / "images_used.json"
REGULAR_POST_RECEIPT_FILE = BASE_DIR / "regular_post_receipt.json"
MEME_POST_RECEIPT_FILE = BASE_DIR / "meme_post_receipt.json"
CONFIRMED_REPLY_RECEIPT_FILE = BASE_DIR / "confirmed_reply_receipt.json"
AMBIGUOUS_POST_OUTCOME_FILE = BASE_DIR / "ambiguous_post_outcome.json"
_AMBIGUOUS_REMOTE_POST_SEEN = False
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
CONTROL_FILE = BASE_DIR / "mrsMThatcher.control.json"
LOCK_FILE = BASE_DIR / "mrsMThatcher.lock"
STATE_BACKUP_COUNT = 5

# Re-read before each quote-tweet check; edit this file while the bot is running.
EXTRA_QUOTE_WATCH_FILE = BASE_DIR / "extra_quote_watch_post_ids.txt"
MAX_EXTRA_QUOTE_WATCH_POSTS = 5

MEME_DIR = BASE_DIR / "final_posting_queue_top90_as_is" / "images"
MEME_ANALYSIS_FILE = BASE_DIR / "final_posting_queue_top90_as_is" / "renamed_png_v3_top90_posting_queue.json"


# ---------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------

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

    level_name = os.getenv("LOG_LEVEL", "DEBUG").upper()
    level = getattr(logging, level_name, logging.DEBUG)

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
            RotatingFileHandler(target_log, maxBytes=2_000_000, backupCount=5),
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
_IMPORT_LOG_LEVEL = getattr(logging, os.getenv("LOG_LEVEL", "DEBUG").upper(), logging.DEBUG)
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


def acquire_instance_lock() -> None:
    """Prevent two bot processes from sharing one mutable state directory."""
    global _LOCK_FH

    if _LOCK_FH is not None:
        return

    BASE_DIR.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK_FILE, "a+")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        log.critical("Another MrsMThatcher instance already holds lock %s", LOCK_FILE)
        fh.close()
        sys.exit(2)

    fh.seek(0)
    fh.truncate()
    fh.write(f"pid={os.getpid()}\n")
    fh.flush()
    _LOCK_FH = fh
    log.info("Acquired instance lock %s", LOCK_FILE)


def redact_secret(value: str, visible: int = 4) -> str:
    """Redact a secret value before it is logged."""
    if not value:
        return "<missing>"
    if len(value) <= visible * 2:
        return "<set-but-short>"
    return f"{value[:visible]}...{value[-visible:]}"


def log_json_debug(label: str, obj: object, max_chars: int = 4000) -> None:
    """Log a JSON payload at debug level with sensitive fields redacted."""
    try:
        text = json.dumps(obj, indent=2, sort_keys=True, default=str)
    except Exception:
        text = repr(obj)

    if len(text) > max_chars:
        text = text[:max_chars] + "...<truncated>"

    log.debug("%s: %s", label, text)


def log_event(event: str, **fields: object) -> None:
    """Emit a stable one-line structured event for digest scripts."""
    payload = {"event": event}
    payload.update(fields)
    try:
        text = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        text = repr(payload)
    log.info("EVENT %s", text)


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
    "DRY_RUN_REPLIES",
    "MARK_AI_REPLIES_AS_AI",
    "ALWAYS_FETCH_PARENT_FOR_CONTEXT",
    "SKIP_REPLIES_TO_OWN_AUTO_REPLIES",
    "THREAD_CONTEXT_MAX_DEPTH",
    "THREAD_CONTEXT_MAX_CHARS_PER_POST",
    "THREAD_CONTEXT_MAX_TOTAL_CHARS",
    "TWEET_CACHE_MAX_AGE_SECONDS",
    "TWEET_CACHE_MAX_ITEMS",
    "ERROR_WINDOW_SECONDS",
    "MAX_X_ERRORS_PER_WINDOW",
    "MAX_XAI_ERRORS_PER_WINDOW",
    "COOLDOWN_AFTER_REPEATED_ERRORS_SECONDS",
    "COOLDOWN_AFTER_429_SECONDS",
    "XAI_MODEL",
    "MAX_REPLY_CHARS",

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
    "ai_first_reply_strategy",
    "quote_image_semantic_veto",
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
    "THREAD_CONTEXT_MAX_DEPTH",
    "THREAD_CONTEXT_MAX_CHARS_PER_POST",
    "THREAD_CONTEXT_MAX_TOTAL_CHARS",
    "TWEET_CACHE_MAX_AGE_SECONDS",
    "TWEET_CACHE_MAX_ITEMS",
    "ERROR_WINDOW_SECONDS",
    "MAX_X_ERRORS_PER_WINDOW",
    "MAX_XAI_ERRORS_PER_WINDOW",
    "COOLDOWN_AFTER_REPEATED_ERRORS_SECONDS",
    "COOLDOWN_AFTER_429_SECONDS",
    "MAX_REPLY_CHARS",
    "GENERATED_IMAGE_ORIGIN_QUOTE_BOOST",
    "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN",
    "STATE_BACKUP_COUNT",
}

LOCAL_CONFIG_POSITIVE_INT_KEYS = {
    "MAX_AUTO_REPLIES_PER_DAY",
    "MAX_QUOTE_REPLIES_PER_DAY",
    "MAX_REPLIES_PER_AUTHOR_PER_DAY",
    "MAX_MENTIONS_PER_CHECK",
    "MAX_HOT_POST_REPLIES_PER_CHECK",
    "HOT_POST_REPLY_SEARCH_API_MAX_RESULTS",
    "MAX_QUOTE_POSTS_PER_CHECK",
    "QUOTE_POST_LOOKBACK_MAIN_POSTS",
    "RECENT_OWN_POST_IDS_MAX",
    "QUOTE_LOOKUP_API_MAX_RESULTS",
    "QUOTE_LOOKUP_MAX_PAGES_PER_POST",
    "MENTIONS_MAX_PAGES_PER_CHECK",
    "HOT_POST_REPLY_SEARCH_MAX_PAGES_PER_CHECK",
    "THREAD_CONTEXT_MAX_DEPTH",
    "THREAD_CONTEXT_MAX_CHARS_PER_POST",
    "THREAD_CONTEXT_MAX_TOTAL_CHARS",
    "TWEET_CACHE_MAX_AGE_SECONDS",
    "TWEET_CACHE_MAX_ITEMS",
    "MAX_X_ERRORS_PER_WINDOW",
    "MAX_XAI_ERRORS_PER_WINDOW",
    "COOLDOWN_AFTER_REPEATED_ERRORS_SECONDS",
    "COOLDOWN_AFTER_429_SECONDS",
    "MAX_REPLY_CHARS",
}


class LocalConfigError(RuntimeError):
    """An existing production local-config file is unsafe to apply."""


class ReplyEvidenceUnavailable(RuntimeError):
    """The local reply-evidence corpus could not be loaded safely."""


_PRODUCTION_BOOTSTRAPPED = False
_QUOTE_IMAGE_SEMANTIC_VETO_SHADOW: object | None = None
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
        if key != "MEME_POST_TEXT":
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

    from reply_strategy import validate_strategy_config

    strategy_config = values.get(
        "ai_first_reply_strategy",
        globals().get("ai_first_reply_strategy"),
    )
    errors.extend(validate_strategy_config(strategy_config))

    veto_config = values.get("quote_image_semantic_veto", globals().get("quote_image_semantic_veto"))
    veto_keys = {
        "enabled", "mode", "manifest_path", "fail_open",
        "record_best_allowed_alternative", "maximum_shadow_history",
    }
    if not isinstance(veto_config, dict) or set(veto_config) != veto_keys:
        errors.append("quote_image_semantic_veto fields mismatch")
    else:
        if type(veto_config.get("enabled")) is not bool:
            errors.append("quote_image_semantic_veto.enabled must be boolean")
        if veto_config.get("mode") not in {"disabled", "shadow"}:
            errors.append("quote_image_semantic_veto.mode must be disabled or shadow")
        if veto_config.get("enabled") and veto_config.get("mode") != "shadow":
            errors.append("enabled quote_image_semantic_veto must use shadow mode")
        if not isinstance(veto_config.get("manifest_path"), str) or not veto_config["manifest_path"].strip():
            errors.append("quote_image_semantic_veto.manifest_path must be non-empty")
        if veto_config.get("fail_open") is not True:
            errors.append("quote_image_semantic_veto.fail_open must remain true")
        if type(veto_config.get("record_best_allowed_alternative")) is not bool:
            errors.append("quote_image_semantic_veto.record_best_allowed_alternative must be boolean")
        maximum_history = veto_config.get("maximum_shadow_history")
        if type(maximum_history) is not int or not 100 <= maximum_history <= 100_000:
            errors.append("quote_image_semantic_veto.maximum_shadow_history must be 100..100000")

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
        "MAX_HOT_POST_REPLIES_PER_CHECK",
        "QUOTE_POST_LOOKBACK_MAIN_POSTS",
        "RECENT_OWN_POST_IDS_MAX",
        "QUOTE_LOOKUP_MAX_PAGES_PER_POST",
        "MENTIONS_MAX_PAGES_PER_CHECK",
        "HOT_POST_REPLY_SEARCH_MAX_PAGES_PER_CHECK",
        "THREAD_CONTEXT_MAX_DEPTH",
        "THREAD_CONTEXT_MAX_CHARS_PER_POST",
        "THREAD_CONTEXT_MAX_TOTAL_CHARS",
        "TWEET_CACHE_MAX_AGE_SECONDS",
        "TWEET_CACHE_MAX_ITEMS",
        "ERROR_WINDOW_SECONDS",
        "MAX_X_ERRORS_PER_WINDOW",
        "MAX_XAI_ERRORS_PER_WINDOW",
        "COOLDOWN_AFTER_REPEATED_ERRORS_SECONDS",
        "COOLDOWN_AFTER_429_SECONDS",
    }

    for key in sorted(positive_keys):
        try:
            if int_value(key) <= 0:
                errors.append(f"{key} must be positive")
        except Exception:
            errors.append(f"{key} must be an integer")

    raw_reply_chars = values.get("MAX_REPLY_CHARS", globals().get("MAX_REPLY_CHARS"))
    if type(raw_reply_chars) is not int:
        errors.append("MAX_REPLY_CHARS must be an integer")
    elif raw_reply_chars <= 0:
        errors.append("MAX_REPLY_CHARS must be positive")
    elif raw_reply_chars > 280:
        errors.append("MAX_REPLY_CHARS must not exceed 280")

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


def apply_local_config() -> None:
    """Apply optional local JSON config overrides without editing the bot script."""
    if not LOCAL_CONFIG_FILE.exists():
        log.info("Local config file not present; using script defaults. path=%s", LOCAL_CONFIG_FILE)
        return

    try:
        with open(LOCAL_CONFIG_FILE, "r") as f:
            data = json.load(f)
    except Exception as exc:
        raise LocalConfigError(f"Failed to read local config file {LOCAL_CONFIG_FILE}: {exc}") from exc

    if not isinstance(data, dict):
        raise LocalConfigError(f"Local config file {LOCAL_CONFIG_FILE} must contain a JSON object")

    if "reply_strategy" in data:
        raise LocalConfigError(
            "Local config contains retired reply_strategy V1 settings; replace them with "
            "the reviewed ai_first_reply_strategy configuration before activation"
        )

    proposed: dict[str, object] = {}
    coercion_errors: list[str] = []

    for key, value in data.items():
        if key not in LOCAL_CONFIG_ALLOWED_KEYS or key not in globals():
            raise LocalConfigError(
                f"Unsupported local config key {key!r} in {LOCAL_CONFIG_FILE}; "
                "refusing to ignore a possible safety-setting typo"
            )

        try:
            coerced = _coerce_local_config_value(key, value, globals()[key])
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
        original_values = {
            name: globals()[name]
            for name in LOCAL_CONFIG_ALLOWED_KEYS
            if name in globals()
        }
        candidate = dict(original_values)
        candidate.update(proposed)
        validation_errors = validate_runtime_config_values(candidate)
        if validation_errors:
            raise LocalConfigError(
                f"Invalid local config {LOCAL_CONFIG_FILE}: " + "; ".join(validation_errors)
            )

        for key, value in proposed.items():
            globals()[key] = value
        log.info("Applied %d local config override(s) from %s", len(proposed), LOCAL_CONFIG_FILE)
        log_json_debug("Local config overrides applied", proposed)
    else:
        log.info("Local config file present but no valid overrides applied: %s", LOCAL_CONFIG_FILE)


SOURCE_DEFAULT_CONFIG_ERRORS = validate_runtime_config_values(
    {name: globals()[name] for name in LOCAL_CONFIG_ALLOWED_KEYS if name in globals()}
)
if SOURCE_DEFAULT_CONFIG_ERRORS:
    raise RuntimeError("Invalid source default config: " + "; ".join(SOURCE_DEFAULT_CONFIG_ERRORS))


def reply_evidence_repository():
    """Load claim evidence on first use and cache a fail-closed load failure."""
    global _REPLY_EVIDENCE_REPOSITORY, _REPLY_EVIDENCE_LOAD_ERROR
    if _REPLY_EVIDENCE_REPOSITORY is not None:
        return _REPLY_EVIDENCE_REPOSITORY
    if _REPLY_EVIDENCE_LOAD_ERROR is not None:
        raise ReplyEvidenceUnavailable(_REPLY_EVIDENCE_LOAD_ERROR)

    from reply_evidence import EvidenceRepository

    research_path = Path(str(ai_first_reply_strategy["research_corpus_path"]))
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


def initialise_quote_image_semantic_veto_shadow() -> None:
    """Load the corrected local manifest once; failure disables only observation."""
    global _QUOTE_IMAGE_SEMANTIC_VETO_SHADOW
    if not quote_image_semantic_veto.get("enabled"):
        _QUOTE_IMAGE_SEMANTIC_VETO_SHADOW = None
        return
    if _QUOTE_IMAGE_SEMANTIC_VETO_SHADOW is not None:
        return
    from semantic_alignment.quote_image_semantic_veto import ShadowRuntime

    runtime = ShadowRuntime.load(
        BASE_DIR,
        quote_image_semantic_veto,
        verify_source_hashes=True,
        expected_runtime_quote_ids=completed_research_quote_hashes(),
    )
    _QUOTE_IMAGE_SEMANTIC_VETO_SHADOW = runtime
    if runtime.available:
        log.info(
            "Quote/image semantic-veto shadow manifest loaded. policy=%s sha256=%s pairs=%d "
            "load_ms=%.3f memory_bytes=%d active_enforcement=false",
            runtime.policy_version,
            runtime.manifest_sha256,
            len(runtime.pairs or {}),
            runtime.load_time_ms,
            runtime.memory_bytes,
        )
    else:
        log.warning(
            "Quote/image semantic-veto shadow unavailable; production selection remains unchanged. "
            "status=%s reason=%s",
            runtime.status,
            runtime.reason,
        )


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
        from historical_context_formatter import HistoricalContextReplyStore

        context_store = HistoricalContextReplyStore(
            HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
            HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        )
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
    if load_runtime_resources:
        initialise_quote_image_semantic_veto_shadow()
    else:
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
    global _HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON
    from historical_context_formatter import HistoricalContextReplyStore

    context_store = HistoricalContextReplyStore(
        HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
    )
    try:
        context_store.reconcile_receipt()
    except Exception:
        log.critical(
            "Historical-context durable receipt could not be reconciled; "
            "refusing production startup to preserve the ambiguity barrier",
            exc_info=True,
        )
        raise
    try:
        historical_context_outbox_store().snapshot()
    except Exception as exc:
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


def required_installation_files_missing() -> list[Path]:
    """Return durable files that cannot be recovered from local backups."""
    missing = [path for path in (LINES_USED_FILE, IMAGES_USED_FILE) if not path.is_file()]
    state_candidates = [STATE_FILE]
    state_candidates.extend(
        STATE_FILE.with_name(f"{STATE_FILE.name}.bak{i}")
        for i in range(1, STATE_BACKUP_COUNT + 1)
    )
    if not any(path.is_file() for path in state_candidates):
        missing.append(STATE_FILE)
    return missing


def require_established_installation() -> None:
    """Refuse operational startup after unexpected durable-state loss."""
    missing = required_installation_files_missing()
    if missing:
        raise RuntimeError(
            "Required durable production state/history is missing: "
            + ", ".join(str(path) for path in missing)
            + ". Restore the files or use --initialise only for a genuinely new installation."
        )


def initialise_installation() -> int:
    """Create a new state/history set without starting production."""
    require_production_bootstrap()
    candidates = [INSTALLATION_MARKER_FILE, STATE_FILE, LINES_USED_FILE, IMAGES_USED_FILE]
    candidates.extend(STATE_FILE.with_name(f"{STATE_FILE.name}.bak{i}") for i in range(1, STATE_BACKUP_COUNT + 1))
    candidates.extend(
        (
            REGULAR_POST_RECEIPT_FILE,
            MEME_POST_RECEIPT_FILE,
            CONFIRMED_REPLY_RECEIPT_FILE,
            AMBIGUOUS_POST_OUTCOME_FILE,
            HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
            HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
            HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE,
            HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.with_name(
                f"{HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.name}.lock"
            ),
        )
    )
    existing = [path for path in candidates if path.exists()]
    if existing:
        raise RuntimeError(
            "Refusing to initialise over an existing or partially established installation: "
            + ", ".join(str(path) for path in existing)
        )

    BASE_DIR.mkdir(parents=True, exist_ok=True)
    created: list[Path] = []
    try:
        state = default_state()
        current = now_epoch()
        state["next_quote_post_epoch"] = current + POST_SLEEP_MIN
        if ENABLE_DAILY_MEME_POSTS:
            ensure_meme_schedule_initialized(state)
        save_state(state, durable=True)
        created.append(STATE_FILE)
        created.extend(
            path
            for path in STATE_FILE.parent.glob(f"{STATE_FILE.name}.bak*")
            if path.is_file()
        )
        save_quote_used_hashes(LINES_USED_FILE, set(), durable=True)
        created.append(LINES_USED_FILE)
        save_image_used_basenames(IMAGES_USED_FILE, set(), durable=True)
        created.append(IMAGES_USED_FILE)
        atomic_write_json(
            INSTALLATION_MARKER_FILE,
            {"schema_version": 1, "initialised_at_epoch": current},
            durable=True,
        )
        created.append(INSTALLATION_MARKER_FILE)
    except Exception:
        for path in reversed(created):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
        raise
    print(f"Initialised durable MrsMThatcher state in {BASE_DIR}; production was not started")
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

CONTROL_BOOLEAN_KEYS = frozenset({
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
})
CONTROL_TIME_KEYS = frozenset(
    f"{key}_until" for key in CONTROL_BOOLEAN_KEYS
)
CONTROL_METADATA_KEYS = frozenset({"generation"})
CONTROL_ALLOWED_KEYS = (
    CONTROL_BOOLEAN_KEYS | CONTROL_TIME_KEYS | CONTROL_METADATA_KEYS
)


def parse_control_time(value: object) -> int:
    """Parse a runtime-control timestamp into an epoch value."""
    if isinstance(value, bool) or value is None:
        raise ValueError("control timestamp must not be a boolean or null")
    if type(value) is int:
        epoch = value
    elif type(value) is float:
        if not math.isfinite(value) or not value.is_integer():
            raise ValueError("control timestamp float must be finite and integral")
        epoch = int(value)
    else:
        if type(value) is not str:
            raise ValueError("control timestamp must be an integer epoch or documented date string")
        text = value.strip()
        if not text or text.isdigit():
            raise ValueError("control timestamp string must use a documented date format")
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
            try:
                epoch = int(datetime.strptime(text, fmt).timestamp())
                break
            except ValueError:
                pass
        else:
            try:
                epoch = int(datetime.fromisoformat(text).timestamp())
            except ValueError as exc:
                raise ValueError(f"Cannot parse control time {value!r}") from exc
    if epoch < 0 or epoch > MAX_REASONABLE_STATE_EPOCH:
        raise ValueError(f"control timestamp is outside the supported epoch range: {epoch}")
    return epoch


def validate_control_document(data: object) -> dict:
    """Validate control document."""
    if not isinstance(data, dict):
        raise ValueError("control document must be a JSON object")
    validated = dict(data)
    for key, value in data.items():
        if type(key) is not str or key not in CONTROL_ALLOWED_KEYS:
            raise ValueError(
                f"unsupported runtime-control key {key!r}; "
                "refusing to ignore a possible safety-setting typo"
            )
        key_text = key
        if key_text in CONTROL_TIME_KEYS:
            parse_control_time(value)
        elif key_text in CONTROL_BOOLEAN_KEYS:
            if isinstance(value, bool):
                continue
            if isinstance(value, str) and value.strip().lower() in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
                continue
            raise ValueError(f"{key_text} must be a boolean")
        elif key_text == "generation":
            if type(value) is not int or value < 0:
                raise ValueError("generation must be a non-negative integer")
    return validated


def control_failure_result(reason: str, *, signature: object) -> dict:
    """Build the fail-closed result for an invalid runtime-control document."""
    if _CONTROL_CACHE.get("failure_signature") != signature:
        log.error("Runtime control file %s is unavailable or invalid; failing safe: %s", CONTROL_FILE, reason)
        _CONTROL_CACHE["failure_signature"] = signature
    if _CONTROL_CACHE.get("has_valid"):
        cached = _CONTROL_CACHE.get("data", {})
        log.debug("Continuing with last valid runtime control document")
        fail_closed = dict(cached) if isinstance(cached, dict) else {}
        fail_closed["disable_all"] = True
        fail_closed["_control_fail_closed"] = True
        return fail_closed
    return {"disable_all": True, "_control_fail_closed": True}


def load_control() -> dict:
    """Load and validate the optional fail-safe runtime-control document."""
    try:
        stat = CONTROL_FILE.stat()
    except FileNotFoundError:
        _CONTROL_CACHE["signature"] = None
        _CONTROL_CACHE["data"] = {}
        _CONTROL_CACHE["has_valid"] = False
        _CONTROL_CACHE["failure_signature"] = None
        return {}
    except OSError as exc:
        return control_failure_result(str(exc), signature=("stat", type(exc).__name__, str(exc)))

    signature = (
        str(CONTROL_FILE.resolve()), stat.st_dev, stat.st_ino,
        stat.st_size, stat.st_mtime_ns,
    )
    if _CONTROL_CACHE.get("signature") == signature and _CONTROL_CACHE.get("has_valid"):
        data = _CONTROL_CACHE.get("data", {})
        return data if isinstance(data, dict) else {}

    try:
        with open(CONTROL_FILE, "r") as f:
            data = json.load(f)
        data = validate_control_document(data)
    except Exception as exc:
        return control_failure_result(str(exc), signature=("content", signature, type(exc).__name__, str(exc)))

    _CONTROL_CACHE["signature"] = signature
    _CONTROL_CACHE["data"] = dict(data)
    _CONTROL_CACHE["has_valid"] = True
    _CONTROL_CACHE["failure_signature"] = None
    log.info("Loaded runtime control file %s", CONTROL_FILE)
    log_json_debug("Runtime control", data)
    return data


def control_bool(data: dict, key: str) -> bool:
    """Return a validated boolean runtime-control value."""
    value = data.get(key)
    if value in (None, ""):
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if not normalized:
            return False
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    log.warning("Ignoring invalid runtime control boolean %s=%r; failing open", key, value)
    return False


def control_pause_active(data: dict, *keys: str) -> tuple[bool, str, int]:
    """Return whether the runtime-control document currently pauses a lane."""
    current = now_epoch()

    for key in keys:
        if control_bool(data, key):
            return True, key, 0

    for key in [f"{key}_until" for key in keys]:
        if key in data:
            try:
                until_epoch = parse_control_time(data.get(key))
            except ValueError as exc:
                log.error("Ignoring invalid control pause %s=%r: %s", key, data.get(key), exc)
                continue
            if until_epoch > current:
                return True, key, until_epoch

    return False, "", 0


def lane_paused(*lane_keys: str) -> bool:
    """Return whether a named posting lane is paused."""
    data = load_control()
    if not data:
        return False

    expanded_keys = ["disable_all", "pause_all"]
    expanded_keys.extend(lane_keys)

    if any("replies" in key for key in lane_keys):
        expanded_keys.append("pause_replies")
    if "disable_normal_replies" in lane_keys:
        expanded_keys.append("pause_normal_replies")
    if "disable_quote_replies" in lane_keys:
        expanded_keys.append("pause_quote_replies")
    if "disable_hot_post_replies" in lane_keys:
        expanded_keys.append("pause_hot_post_replies")
    if "disable_quote_posts" in lane_keys:
        expanded_keys.append("pause_quote_posts")
    if "disable_meme_posts" in lane_keys:
        expanded_keys.append("pause_meme_posts")

    # Preserve order while de-duplicating aliases.
    expanded_keys = list(dict.fromkeys(expanded_keys))

    active, key, until_epoch = control_pause_active(data, *expanded_keys)
    if active:
        human_until = datetime.fromtimestamp(until_epoch).strftime("%Y-%m-%d %H:%M:%S") if until_epoch else "until cleared"
        log.warning("Runtime control active: %s pauses %s (%s)", key, ",".join(lane_keys), human_until)
        log_event("runtime_control_pause", key=key, lanes=list(lane_keys), until_epoch=until_epoch)
        return True

    return False


def global_remote_writes_paused() -> bool:
    """Return whether the runtime control pauses every remote-write lane."""
    data = load_control()
    if not data:
        return False
    active, _key, _until_epoch = control_pause_active(
        data,
        "disable_all",
        "pause_all",
    )
    return active


# ---------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------

CONSUMER_KEY = os.getenv("X_CONSUMER_KEY", "")
CONSUMER_SECRET = os.getenv("X_CONSUMER_SECRET", "")
ACCESS_TOKEN = os.getenv("X_ACCESS_TOKEN", "")
ACCESS_SECRET = os.getenv("X_ACCESS_SECRET", "")
MY_USER_ID = os.getenv("X_MY_USER_ID", "")
MY_USERNAME = os.getenv("X_MY_USERNAME", "MrsMThatcher").strip().lstrip("@")
XAI_API_KEY = os.getenv("XAI_API_KEY", "")

# Optional. If set, quote lookup uses Bearer auth. If not set, the script
# falls back to OAuth1, as used by the other X v2 calls.
X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN", "")

log.debug("Credential presence:")
log.debug("  X_CONSUMER_KEY=%s", redact_secret(CONSUMER_KEY))
log.debug("  X_CONSUMER_SECRET=%s", redact_secret(CONSUMER_SECRET))
log.debug("  X_ACCESS_TOKEN=%s", redact_secret(ACCESS_TOKEN))
log.debug("  X_ACCESS_SECRET=%s", redact_secret(ACCESS_SECRET))
log.debug("  X_MY_USER_ID=%s", MY_USER_ID or "<missing>")
log.debug("  XAI_API_KEY=%s", redact_secret(XAI_API_KEY))
log.debug("  X_BEARER_TOKEN=%s", redact_secret(X_BEARER_TOKEN))

def validate_production_credentials() -> None:
    """Validate required credentials without logging their values."""
    if not all([CONSUMER_KEY, CONSUMER_SECRET, ACCESS_TOKEN, ACCESS_SECRET, MY_USER_ID]):
        raise RuntimeError(
            "Missing X credentials. Set X_CONSUMER_KEY, X_CONSUMER_SECRET, "
            "X_ACCESS_TOKEN, X_ACCESS_SECRET, X_MY_USER_ID"
        )
    if ENABLE_AUTO_REPLIES and not XAI_API_KEY:
        raise RuntimeError("ENABLE_AUTO_REPLIES is True, but XAI_API_KEY is not set")

AUTH = OAuth1(
    CONSUMER_KEY,
    client_secret=CONSUMER_SECRET,
    resource_owner_key=ACCESS_TOKEN,
    resource_owner_secret=ACCESS_SECRET,
)


def normalise_base_url(raw: str, *, strip_trailing_segments: tuple[str, ...] = ()) -> str:
    """Return a stable API root URL for endpoint overrides.

    The production code appends endpoint paths such as /2/users/... and
    /1.1/media/upload.json itself. For convenience in tests, tolerate values
    such as http://127.0.0.1:8765/2 or http://127.0.0.1:8765/1.1 by
    stripping those terminal version segments.
    """
    value = str(raw or "").rstrip("/")
    parsed = urlsplit(value)
    path = parsed.path.rstrip("/")

    for segment in strip_trailing_segments:
        suffix = "/" + segment.strip("/")
        if path == suffix or path.endswith(suffix):
            path = path[: -len(suffix)].rstrip("/")
            value = urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, parsed.fragment)).rstrip("/")
            break

    return value


def endpoint_host(url: str) -> str:
    """Return the normalised host from an API endpoint URL."""
    try:
        return (urlsplit(url).hostname or "").lower()
    except Exception:
        return ""


X_BASE = normalise_base_url(os.getenv("X_API_BASE_URL", "https://api.x.com"), strip_trailing_segments=("2",))
X_UPLOAD_BASE = normalise_base_url(
    os.getenv("X_UPLOAD_BASE_URL", "https://upload.twitter.com"),
    strip_trailing_segments=("1.1",),
)
XAI_BASE = normalise_base_url(os.getenv("XAI_API_BASE_URL", "https://api.x.ai/v1"))
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
    live_endpoints = []
    if endpoint_host(X_BASE) == "api.x.com":
        live_endpoints.append(f"X_API_BASE_URL={X_BASE}")
    if endpoint_host(X_UPLOAD_BASE) == "upload.twitter.com":
        live_endpoints.append(f"X_UPLOAD_BASE_URL={X_UPLOAD_BASE}")
    if endpoint_host(XAI_BASE) == "api.x.ai":
        live_endpoints.append(f"XAI_API_BASE_URL={XAI_BASE}")

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
    """Raised when an X or xAI API operation fails."""
    def __init__(
        self,
        message: str,
        *,
        service: str,
        status_code: int | None = None,
        reset_epoch: int | None = None,
        request_method: str | None = None,
        request_path: str | None = None,
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


class AmbiguousRemotePostOutcome(ApiError):
    """X may have accepted a write although no response reached this process."""


class PaginationCursorProtocolError(ApiError):
    """An X collection returned a pagination-token cycle."""


class RemoteOperationsPaused(RuntimeError):
    """A global runtime-control pause blocked a remote operation."""


def require_remote_operation_unpaused(operation: str) -> None:
    """Fail before a remote boundary while a global pause is active."""
    if not global_remote_writes_paused():
        return
    log.warning(
        "Global runtime control pause blocked remote operation: %s",
        operation,
    )
    raise RemoteOperationsPaused(
        f"Global runtime control pause blocks remote operation: {operation}"
    )


X_API_ERROR_REPLY_TARGET_RESTRICTED = "reply_target_restricted"
X_API_ERROR_REPLY_TARGET_UNAVAILABLE = "reply_target_missing_or_inaccessible"
X_API_ERROR_LOOKUP_TARGET_UNAVAILABLE = "lookup_target_missing_or_inaccessible"
X_API_ERROR_GLOBAL_DENIAL = "global_authentication_or_application_denial"
X_API_ERROR_ENDPOINT_NOT_FOUND = "endpoint_or_unclassified_not_found"
X_API_ERROR_OTHER = "other"


def _x_error_request_path(error: Exception) -> str:
    """Return the request path without query data or credentials."""
    raw = str(getattr(error, "request_path", "") or "")
    if not raw:
        return ""
    parsed = urlsplit(raw)
    return parsed.path or raw.split("?", 1)[0]


def _x_error_is_tweet_lookup(error: Exception) -> bool:
    """Return whether the failed request was one exact post lookup."""
    return (
        str(getattr(error, "request_method", "") or "").upper() == "GET"
        and re.fullmatch(r"/2/tweets/\d+", _x_error_request_path(error)) is not None
    )


def _x_error_is_post_create(error: Exception) -> bool:
    """Return whether the failed request was the X post-create endpoint."""
    return (
        str(getattr(error, "request_method", "") or "").upper() == "POST"
        and _x_error_request_path(error) == "/2/tweets"
    )


def classify_x_api_error(error: Exception) -> str:
    """Classify X failures using request context, status and bounded messages.

    Status alone is deliberately insufficient: a 403 may be a target-specific
    reply restriction or an application-wide denial, and a 404 may identify a
    missing post or a missing/misconfigured endpoint.
    """
    if getattr(error, "service", None) != "x":
        return X_API_ERROR_OTHER

    status_code = getattr(error, "status_code", None)
    message = str(error).casefold()
    is_lookup = _x_error_is_tweet_lookup(error)
    is_post_create = _x_error_is_post_create(error)

    global_denial_markers = (
        "invalid or expired token",
        "could not authenticate you",
        "authentication credentials",
        "client forbidden",
        "this application is not permitted",
        "application is not permitted",
        "unsupported authentication",
    )
    if status_code in {401, 403} and any(
        marker in message for marker in global_denial_markers
    ):
        return X_API_ERROR_GLOBAL_DENIAL

    reply_restriction_markers = (
        "reply to this conversation is not allowed",
        "not been mentioned or otherwise engaged by the author",
        "not allowed to reply",
        "only reply to or quote posts where you are mentioned or are the author",
        "author has restricted who can reply",
    )
    target_unavailable_markers = (
        "tweet that is deleted or not visible to you",
        "post that is deleted or not visible to you",
        "tweet is deleted or not visible",
        "post is deleted or not visible",
        "tweet is unavailable",
        "post is unavailable",
        "could not find tweet",
        "could not find post",
        "tweet not found",
        "post not found",
    )

    if status_code == 403 and any(
        marker in message for marker in reply_restriction_markers
    ):
        # Strong reply-specific messages remain classifiable for legacy errors
        # which pre-date explicit request metadata.
        if is_post_create or not getattr(error, "request_path", None):
            return X_API_ERROR_REPLY_TARGET_RESTRICTED

    if status_code in {403, 404} and any(
        marker in message for marker in target_unavailable_markers
    ):
        if is_post_create or not getattr(error, "request_path", None):
            return X_API_ERROR_REPLY_TARGET_UNAVAILABLE
        if is_lookup:
            return X_API_ERROR_LOOKUP_TARGET_UNAVAILABLE

    if status_code == 404:
        if is_lookup:
            return X_API_ERROR_LOOKUP_TARGET_UNAVAILABLE
        return X_API_ERROR_ENDPOINT_NOT_FOUND

    if status_code == 403:
        return X_API_ERROR_GLOBAL_DENIAL
    return X_API_ERROR_OTHER


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
        with open(path, "r", encoding="utf-8") as f:
            value = json.load(f)
        converted = coerce_used_set(value, path=path)
        if isinstance(value, list) and value != used_set_to_sorted_list(converted):
            save_used_set(path, converted)
            log.info("Normalized used-history JSON ordering in %s", path)
        log.debug("Loaded %d entries from %s", len(converted), path)
        return converted
    except FileNotFoundError:
        log.warning("Used-history JSON file does not exist yet: %s", path)
    except OSError:
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

    path.parent.mkdir(parents=True, exist_ok=True)

    serializable = used_set_to_sorted_list(value)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=2)
        f.write("\n")
        if durable:
            f.flush()
            os.fsync(f.fileno())
    os.replace(tmp, path)
    if durable:
        fsync_parent_dir(path, strict=durable)


def default_state() -> dict:
    """Build a new runtime-state document with safe defaults."""
    return {
        "last_seen_mention_id": None,
        "mention_pagination": {},
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
        "quote_spam_author_ids": [],
        "daily_quote_reply_date": None,
        "daily_quote_reply_count": 0,
        "last_quote_tweet_check_epoch": 0,

        "x_error_epochs": [],
        "x_write_error_epochs": [],
        "xai_error_epochs": [],
        "api_cooldown_until_epoch": 0,
        "api_cooldown_reason": "",
        "x_write_api_cooldown_until_epoch": 0,
        "x_write_api_cooldown_reason": "",
        "xai_api_cooldown_until_epoch": 0,
        "xai_api_cooldown_reason": "",
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


def normalise_state_int(value: object, *, key: str, path: Path) -> int | None:
    """Normalise state int."""
    if isinstance(value, bool):
        log.error("State candidate %s has invalid %s boolean value %r; ignoring", path, key, value)
        return None
    if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
        log.error("State candidate %s has invalid %s numeric value %r; ignoring", path, key, value)
        return None
    try:
        number = int(value or 0)
    except (TypeError, ValueError, OverflowError):
        log.error("State candidate %s has invalid %s value %r; ignoring", path, key, value)
        return None
    if number < 0:
        log.error("State candidate %s has negative %s value %r; ignoring", path, key, value)
        return None
    return number


def normalise_state_epoch(value: object, *, key: str, path: Path) -> int | None:
    """Normalise state epoch."""
    number = normalise_state_int(value, key=key, path=path)
    if number is None:
        return None
    if number > MAX_REASONABLE_STATE_EPOCH:
        log.error("State candidate %s has impossible epoch %s=%r; ignoring", path, key, value)
        return None
    return number


def normalise_string_list(value: object, *, key: str, path: Path) -> list[str] | None:
    """Normalise string list."""
    if not isinstance(value, list):
        log.error("State candidate %s has invalid %s type %s; ignoring", path, key, type(value).__name__)
        return None
    return [str(item) for item in value if item is not None]


def normalise_int_list(value: object, *, key: str, path: Path) -> list[int] | None:
    """Normalise int list."""
    if not isinstance(value, list):
        log.error("State candidate %s has invalid %s type %s; ignoring", path, key, type(value).__name__)
        return None
    out: list[int] = []
    for item in value:
        number = normalise_state_int(item, key=key, path=path)
        if number is None:
            return None
        out.append(number)
    return out


def normalise_epoch_list(value: object, *, key: str, path: Path) -> list[int] | None:
    """Normalise epoch list."""
    out = normalise_int_list(value, key=key, path=path)
    if out is None:
        return None
    for number in out:
        if number > MAX_REASONABLE_STATE_EPOCH:
            log.error("State candidate %s has impossible %s epoch item %r; ignoring", path, key, number)
            return None
    return out


def normalise_string_map(value: object, *, key: str, path: Path) -> dict[str, str] | None:
    """Normalise string map."""
    if not isinstance(value, dict):
        log.error("State candidate %s has invalid %s type %s; ignoring", path, key, type(value).__name__)
        return None
    return {str(k): str(v) for k, v in value.items() if v is not None}


def normalise_int_map(value: object, *, key: str, path: Path) -> dict[str, int] | None:
    """Normalise int map."""
    if not isinstance(value, dict):
        log.error("State candidate %s has invalid %s type %s; ignoring", path, key, type(value).__name__)
        return None
    out: dict[str, int] = {}
    for item_key, item_value in value.items():
        number = normalise_state_int(item_value, key=f"{key}.{item_key}", path=path)
        if number is None:
            return None
        out[str(item_key)] = number
    return out


def normalise_record_map(value: object, *, key: str, path: Path) -> dict[str, dict] | None:
    """Normalise record map."""
    if not isinstance(value, dict):
        log.error("State candidate %s has invalid %s type %s; ignoring", path, key, type(value).__name__)
        return None
    out: dict[str, dict] = {}
    for item_key, item_value in value.items():
        if not isinstance(item_value, dict):
            log.error(
                "State candidate %s has invalid %s.%s type %s; ignoring",
                path,
                key,
                item_key,
                type(item_value).__name__,
            )
            return None
        out[str(item_key)] = dict(item_value)
    return out


def normalise_tweet_cache_entry(tweet_id: object, entry: dict, *, path: Path) -> dict[str, object] | None:
    """Normalise tweet cache entry."""
    cached_epoch = normalise_state_epoch(entry.get("cached_epoch", 0), key=f"tweet_cache.{tweet_id}.cached_epoch", path=path)
    if cached_epoch is None:
        return None

    referenced_tweets = entry.get("referenced_tweets", [])
    if not isinstance(referenced_tweets, list):
        log.error(
            "State candidate %s has invalid tweet_cache.%s.referenced_tweets type %s; ignoring",
            path,
            tweet_id,
            type(referenced_tweets).__name__,
        )
        return None
    normalized_refs: list[dict[str, str]] = []
    for index, ref in enumerate(referenced_tweets):
        if not isinstance(ref, dict):
            log.error(
                "State candidate %s has invalid tweet_cache.%s.referenced_tweets[%d] type %s; ignoring",
                path,
                tweet_id,
                index,
                type(ref).__name__,
            )
            return None
        normalized_ref: dict[str, str] = {}
        if ref.get("type") is not None:
            normalized_ref["type"] = str(ref.get("type"))
        if ref.get("id") is not None:
            normalized_ref["id"] = str(ref.get("id"))
        for key, ref_value in ref.items():
            if key in {"type", "id"} or ref_value is None:
                continue
            normalized_ref[str(key)] = str(ref_value)
        normalized_refs.append(normalized_ref)

    entry_id = entry.get("id")
    normalized_id = str(entry_id if entry_id is not None else tweet_id)
    conversation_id = entry.get("conversation_id")
    normalized_entry: dict[str, object] = {
        "id": normalized_id,
        "author_id": str(entry.get("author_id") or ""),
        "conversation_id": str(conversation_id if conversation_id is not None else normalized_id),
        "created_at": str(entry.get("created_at") or ""),
        "referenced_tweets": normalized_refs,
        "text": str(entry.get("text") or ""),
        "cached_epoch": cached_epoch,
    }
    if entry.get("image_summary") is not None:
        normalized_entry["image_summary"] = str(entry.get("image_summary"))
    if entry.get("post_type") is not None:
        normalized_entry["post_type"] = str(entry.get("post_type"))
    return normalized_entry


def normalise_tweet_cache(value: object, *, path: Path) -> dict[str, dict] | None:
    """Normalise tweet cache."""
    if not isinstance(value, dict):
        log.error("State candidate %s has invalid tweet_cache type %s; ignoring", path, type(value).__name__)
        return None
    out: dict[str, dict] = {}
    for tweet_id, entry in value.items():
        if not isinstance(entry, dict):
            log.error(
                "State candidate %s has invalid tweet_cache.%s type %s; ignoring",
                path,
                tweet_id,
                type(entry).__name__,
            )
            return None
        normalized_entry = normalise_tweet_cache_entry(tweet_id, entry, path=path)
        if normalized_entry is None:
            return None
        out[str(tweet_id)] = normalized_entry
    return out


def normalise_mention_pagination(value: object, *, path: Path) -> dict[str, str] | None:
    """Normalise mention pagination."""
    if not isinstance(value, dict):
        log.error("State candidate %s has invalid mention_pagination type %s; ignoring", path, type(value).__name__)
        return None
    if not value:
        return {}
    if not set(value).issubset({"base_since_id", "next_token"}):
        log.error(
            "State candidate %s has unknown mention_pagination fields; ignoring",
            path,
        )
        return None
    base_since_id = value.get("base_since_id", "")
    next_token = value.get("next_token")
    if not isinstance(base_since_id, str) or (
        base_since_id and not base_since_id.isdigit()
    ):
        log.error(
            "State candidate %s has invalid mention pagination base; ignoring",
            path,
        )
        return None
    candidate = {
        "base_since_id": base_since_id,
        "next_token": next_token,
    }
    if not mention_pagination_provenance_is_valid(candidate):
        log.error(
            "State candidate %s has invalid mention pagination token; ignoring",
            path,
        )
        return None
    return candidate


def normalise_optional_scalar(value: object, *, key: str, path: Path) -> str | None:
    """Normalise optional scalar."""
    if value is None:
        return ""
    if isinstance(value, (str, int)):
        return str(value)
    log.error("State candidate %s has invalid %s type %s; ignoring", path, key, type(value).__name__)
    return None


def normalise_optional_numeric_id(value: object, *, key: str, path: Path) -> str | None:
    """Normalise optional numeric ID."""
    if value in (None, ""):
        return ""
    text = str(value)
    if text.isdigit():
        return text
    log.error("State candidate %s has invalid %s value %r; ignoring", path, key, value)
    return None


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
        expected_date = safe_epoch_date_str(anchor_epoch)
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
    expected_date = safe_epoch_date_str(next_epoch)
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


def normalise_state_candidate(state: dict, *, path: Path) -> dict | None:
    """Normalise state candidate."""
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
    epoch_list_keys = {"x_error_epochs", "x_write_error_epochs", "xai_error_epochs", "quote_x_error_epochs"}
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
        "xai_api_cooldown_until_epoch",
        "quote_api_cooldown_until_epoch",
    }

    normalised = default_state()
    normalised.update(state)

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

    return normalised


def load_state() -> dict:
    """Load, validate, and recover runtime state from durable storage."""
    log.debug("Loading state from %s", STATE_FILE)

    candidates = [STATE_FILE]
    candidates.extend(STATE_FILE.with_name(f"{STATE_FILE.name}.bak{i}") for i in range(1, STATE_BACKUP_COUNT + 1))

    existing_candidates = False
    for candidate in candidates:
        if not candidate.exists():
            log.warning("State file candidate does not exist: %s", candidate)
            continue
        existing_candidates = True

        try:
            with open(candidate, "r") as f:
                state = json.load(f)
        except Exception:
            log.exception("Failed loading state candidate %s", candidate)
            continue

        if not isinstance(state, dict):
            log.error("State file candidate %s is not a JSON object; ignoring", candidate)
            continue
        legacy_drafts = state.get("pending_reply_drafts")
        if legacy_drafts not in (None, {}):
            message = (
                f"Legacy V1 reply drafts remain in {candidate}; refusing to interpret or post them "
                "through the AI-first strategy"
            )
            log.critical(message)
            raise RuntimeError(message)
        state.pop("pending_reply_drafts", None)
        normalised = normalise_state_candidate(state, path=candidate)
        if normalised is None:
            continue

        if candidate != STATE_FILE:
            log.warning("Recovered state from backup %s", candidate)
        log_json_debug("Loaded state", normalised)
        return normalised

    if existing_candidates:
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


def fsync_file(path: Path) -> None:
    """Synchronise file."""
    with open(path, "rb") as f:
        os.fsync(f.fileno())


def copy_state_backup(src: Path, dst: Path, *, durable: bool = False) -> None:
    """Copy state backup."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(f"{dst.name}.tmp")
    shutil.copyfile(src, tmp)
    shutil.copystat(src, tmp)
    if durable:
        fsync_file(tmp)
    os.replace(tmp, dst)
    if durable:
        fsync_parent_dir(dst, strict=True)


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
    """Persist runtime state atomically with bounded backups."""
    if test_process_production_state_write_blocked(STATE_FILE):
        raise RuntimeError(f"Refusing test-process write to production state: {STATE_FILE}")
    log.debug("Saving state to %s", STATE_FILE)
    log_json_debug("State being saved", state)

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    tmp = STATE_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        if durable:
            f.flush()
            os.fsync(f.fileno())

    rotate_state_backups_before_commit(durable=durable)
    os.replace(tmp, STATE_FILE)
    if durable:
        fsync_parent_dir(STATE_FILE, strict=durable)
    try:
        write_latest_state_backup(durable=durable)
    except Exception as exc:
        raise StateBackupWriteError(
            f"Canonical state committed but latest backup write failed: {STATE_FILE}"
        ) from exc


def reset_daily_reply_count_if_needed(state: dict) -> None:
    """Reset daily reply count if needed."""
    today = current_datetime().strftime("%Y-%m-%d")

    if state.get("daily_reply_date") != today:
        log.info(
            "Resetting daily reply count. Previous date=%s new date=%s previous count=%s",
            state.get("daily_reply_date"),
            today,
            state.get("daily_reply_count"),
        )
        state["daily_reply_date"] = today
        state["daily_reply_count"] = 0
        state["daily_replied_author_ids"] = []
        state["daily_replied_author_counts"] = {}


def reset_daily_quote_reply_count_if_needed(state: dict) -> None:
    """Reset daily quote reply count if needed."""
    today = current_datetime().strftime("%Y-%m-%d")

    if state.get("daily_quote_reply_date") != today:
        log.info(
            "Resetting daily quote-reply count. Previous date=%s new date=%s previous count=%s",
            state.get("daily_quote_reply_date"),
            today,
            state.get("daily_quote_reply_count"),
        )
        state["daily_quote_reply_date"] = today
        state["daily_quote_reply_count"] = 0


def daily_author_reply_counts(state: dict) -> dict[str, int]:
    """Return the daily author reply counts."""
    counts = state.get("daily_replied_author_counts", {})
    if isinstance(counts, dict):
        cleaned: dict[str, int] = {}
        for author_id, count in counts.items():
            try:
                cleaned[str(author_id)] = max(0, int(count))
            except Exception:
                continue
        if not cleaned:
            legacy_authors = set(str(x) for x in state.get("daily_replied_author_ids", []))
            cleaned = {author_id: 1 for author_id in legacy_authors}
        state["daily_replied_author_counts"] = cleaned
        return cleaned

    legacy_authors = set(str(x) for x in state.get("daily_replied_author_ids", []))
    cleaned = {author_id: 1 for author_id in legacy_authors}
    state["daily_replied_author_counts"] = cleaned
    return cleaned


def daily_author_reply_count(state: dict, author_id: str) -> int:
    """Return the daily author reply count."""
    return daily_author_reply_counts(state).get(str(author_id), 0)


def mark_daily_author_replied(state: dict, author_id: str) -> None:
    """Mark daily author replied."""
    author_id = str(author_id)
    counts = daily_author_reply_counts(state)
    counts[author_id] = counts.get(author_id, 0) + 1
    state["daily_replied_author_counts"] = counts

    state["daily_replied_author_ids"] = append_unique_capped(
        state.get("daily_replied_author_ids", []),
        author_id,
        1000,
    )


CLARIFICATION_CUE_RE = re.compile(
    r"\b(?:you\s+)?(?:did(?:n't|\s+not)|does(?:n't|\s+not)|have(?:n't|\s+not))\s+answer(?:ed)?\b"
    r"|\b(?:your|that|the)\s+(?:reply|answer)\s+(?:did(?:n't|\s+not)|does(?:n't|\s+not))\s+answer\b"
    r"|\b(?:that(?:'s|\s+is|\s+was)\s+)?not\s+(?:what|the\s+question)\s+(?:i\s+)?asked\b"
    r"|\banswer\s+(?:my|the)\s+question\b"
    r"|\b(?:you\s+)?(?:avoided|evaded)\s+(?:my|the)\s+question\b",
    re.IGNORECASE,
)
CLARIFICATION_TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9'-]{2,}")
CLARIFICATION_TOKEN_STOPWORDS = {
    "answer", "asked", "did", "does", "from", "have", "people", "question",
    "that", "the", "their", "then", "they", "this", "towards", "what", "when",
    "where", "which", "who", "with", "you", "your",
}


def clarification_thread_id(candidate: dict) -> str:
    """Return the clarification thread ID."""
    return str(candidate.get("conversation_id") or candidate.get("id") or "")


def clarification_thread_is_terminal(state: dict, candidate: dict) -> bool:
    """Return whether clarification thread is terminal."""
    records = state.get("clarification_reply_records", {})
    return isinstance(records, dict) and clarification_thread_id(candidate) in records


def author_used_clarification_recently(state: dict, author_id: str, *, current: int) -> bool:
    """Return the author used clarification recently."""
    records = state.get("clarification_reply_records", {})
    if not isinstance(records, dict):
        return False
    cutoff = int(current) - CLARIFICATION_REPLY_WINDOW_SECONDS
    for record in records.values():
        if not isinstance(record, dict) or str(record.get("author_id") or "") != str(author_id):
            continue
        try:
            if int(record.get("completed_epoch", 0) or 0) > cutoff:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _clarification_tokens(text: object) -> set[str]:
    without_handles = re.sub(r"(?<![A-Za-z0-9_])@[A-Za-z0-9_]+", " ", str(text or ""))
    return {
        token.lower() for token in CLARIFICATION_TOKEN_RE.findall(without_handles)
        if token.lower() not in CLARIFICATION_TOKEN_STOPWORDS
    }


def clarification_reply_context(
    state: dict,
    candidate: dict,
    *,
    current: int,
) -> dict | None:
    """Return bounded repair metadata only for a direct follow-up to our confirmed reply."""
    if not ai_first_reply_strategy.get("enabled") or clarification_thread_is_terminal(state, candidate):
        return None
    author_id = str(candidate.get("author_id") or "")
    if not author_id or author_used_clarification_recently(state, author_id, current=current):
        return None

    try:
        prior_bot_reply_id = get_immediate_parent_id(candidate)
    except ApiError:
        return None
    if not prior_bot_reply_id or prior_bot_reply_id not in {
        str(item) for item in state.get("own_auto_reply_ids", [])
    }:
        return None

    cache = state.get("tweet_cache", {})
    if not isinstance(cache, dict):
        return None
    prior_bot_reply = cache.get(prior_bot_reply_id)
    if not is_our_auto_reply(prior_bot_reply, state):
        return None
    try:
        original_question_id = get_immediate_parent_id(prior_bot_reply)
    except ApiError:
        return None
    original_question = cache.get(str(original_question_id or ""))
    if not isinstance(original_question, dict):
        return None
    if str(original_question.get("author_id") or "") != author_id:
        return None

    thread_id = clarification_thread_id(candidate)
    if not thread_id or str(original_question.get("conversation_id") or original_question_id) != thread_id:
        return None
    question_text = str(original_question.get("text") or "")
    incoming_text = str(candidate.get("text") or "")
    if "?" not in question_text:
        return None

    explicit_correction = bool(CLARIFICATION_CUE_RE.search(incoming_text))
    restated_question = "?" in incoming_text
    if restated_question:
        restated_question = bool(
            _clarification_tokens(question_text) & _clarification_tokens(incoming_text)
        )
    if not explicit_correction and not restated_question:
        return None

    return {
        "thread_id": thread_id,
        "prior_bot_reply_id": prior_bot_reply_id,
        "original_question_id": str(original_question_id),
        "question_text": question_text,
        "trigger": "explicit_correction" if explicit_correction else "restated_question",
    }


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
    if scope == "quote":
        until = int(state.get("quote_api_cooldown_until_epoch", 0) or 0)
        reason = state.get("quote_api_cooldown_reason", "Quote API cooldown")
        label = "Quote API cooldown"
    elif scope == "xai":
        until = int(state.get("xai_api_cooldown_until_epoch", 0) or 0)
        reason = state.get("xai_api_cooldown_reason", "xAI API cooldown")
        label = "xAI API cooldown"
    elif scope == "write":
        until = int(state.get("x_write_api_cooldown_until_epoch", 0) or 0)
        reason = state.get("x_write_api_cooldown_reason", "X write API cooldown")
        label = "X write API cooldown"
    else:
        until = int(state.get("api_cooldown_until_epoch", 0) or 0)
        reason = state.get("api_cooldown_reason", "X read API cooldown")
        label = "X read API cooldown"

    if until <= now_epoch():
        return False

    until_human = datetime.fromtimestamp(until).strftime("%Y-%m-%d %H:%M:%S")
    log.warning("%s active until %s: %s", label, until_human, reason)
    return True


def clear_expired_api_cooldowns(state: dict) -> bool:
    """Clear expired API cooldowns."""
    changed = False
    current = now_epoch()

    for until_key, reason_key, label in (
        ("api_cooldown_until_epoch", "api_cooldown_reason", "X read API cooldown"),
        ("x_write_api_cooldown_until_epoch", "x_write_api_cooldown_reason", "X write API cooldown"),
        ("xai_api_cooldown_until_epoch", "xai_api_cooldown_reason", "xAI API cooldown"),
        ("quote_api_cooldown_until_epoch", "quote_api_cooldown_reason", "Quote API cooldown"),
    ):
        until = int(state.get(until_key, 0) or 0)
        if until <= 0 or until > current:
            continue

        log.info(
            "Clearing expired %s. until_epoch=%s reason=%s",
            label,
            until,
            state.get(reason_key, ""),
        )
        state[until_key] = 0
        state[reason_key] = ""
        changed = True

    return changed


def sanitize_next_reply_lane_priority(state: dict) -> bool:
    """Sanitise next reply lane priority."""
    priority = str(state.get("next_reply_lane_priority", "normal") or "normal")
    if priority in {"normal", "quote"}:
        if state.get("next_reply_lane_priority") != priority:
            state["next_reply_lane_priority"] = priority
            return True
        return False

    log.warning("Invalid next_reply_lane_priority=%r; using normal", state.get("next_reply_lane_priority"))
    state["next_reply_lane_priority"] = "normal"
    return True


def load_runtime_state() -> dict:
    """Load runtime state and apply daily maintenance safely."""
    state = load_state()
    clear_expired_api_cooldowns(state)
    sanitize_next_reply_lane_priority(state)
    return state


def prune_error_epochs(epochs: list[int]) -> list[int]:
    """Prune error epochs."""
    cutoff = now_epoch() - ERROR_WINDOW_SECONDS
    pruned = [int(e) for e in epochs if int(e) >= cutoff]
    log.debug("Pruned error epochs from %d to %d", len(epochs), len(pruned))
    return pruned


def cooldown_until_for_rate_limit(current: int, reset_epoch: int | None) -> int:
    """Return the cooldown until for rate limit."""
    if reset_epoch and reset_epoch > current:
        return reset_epoch + 60
    return current + COOLDOWN_AFTER_429_SECONDS


def record_api_error(state: dict, error: Exception, service: str, *, scope: str = "api") -> None:
    """Record API error."""
    if service == "x" and scope == "write" and api_error_is_reply_not_allowed(error):
        log.warning(
            "Not recording terminal target-specific X reply restriction in the transient write-error window: %s",
            error,
        )
        return

    current = now_epoch()

    if service == "x" and scope == "quote":
        key = "quote_x_error_epochs"
        max_errors = MAX_X_ERRORS_PER_WINDOW
        cooldown_until_key = "quote_api_cooldown_until_epoch"
        cooldown_reason_key = "quote_api_cooldown_reason"
    elif service == "x" and scope == "write":
        key = "x_write_error_epochs"
        max_errors = MAX_X_ERRORS_PER_WINDOW
        cooldown_until_key = "x_write_api_cooldown_until_epoch"
        cooldown_reason_key = "x_write_api_cooldown_reason"
    elif service == "x":
        key = "x_error_epochs"
        max_errors = MAX_X_ERRORS_PER_WINDOW
        cooldown_until_key = "api_cooldown_until_epoch"
        cooldown_reason_key = "api_cooldown_reason"
    else:
        key = "xai_error_epochs"
        max_errors = MAX_XAI_ERRORS_PER_WINDOW
        cooldown_until_key = "xai_api_cooldown_until_epoch"
        cooldown_reason_key = "xai_api_cooldown_reason"

    epochs = prune_error_epochs(state.get(key, []))
    epochs.append(current)
    state[key] = epochs

    status_code = getattr(error, "status_code", None)
    reset_epoch = getattr(error, "reset_epoch", None)

    log.warning(
        "Recorded %s API error. status_code=%s errors_in_window=%d/%d reset_epoch=%s error=%s",
        f"{scope}/{service}" if scope != "api" else service,
        status_code,
        len(epochs),
        max_errors,
        reset_epoch,
        error,
    )

    if status_code == 429:
        state[cooldown_until_key] = cooldown_until_for_rate_limit(current, reset_epoch)
        state[cooldown_reason_key] = f"{scope}/{service} returned 429/rate limit" if scope != "api" else f"{service} returned 429/rate limit"
        log.error(
            "Entering API cooldown after 429 until %s",
            datetime.fromtimestamp(state[cooldown_until_key]).strftime("%Y-%m-%d %H:%M:%S"),
        )
        save_state(state)
        return

    if len(epochs) >= max_errors:
        state[cooldown_until_key] = current + COOLDOWN_AFTER_REPEATED_ERRORS_SECONDS
        state[cooldown_reason_key] = (
            f"too many {scope}/{service} API errors in the last hour"
            if scope != "api"
            else f"too many {service} API errors in the last hour"
        )
        log.error(
            "Entering API cooldown after repeated errors until %s",
            datetime.fromtimestamp(state[cooldown_until_key]).strftime("%Y-%m-%d %H:%M:%S"),
        )
        save_state(state)


# ---------------------------------------------------------------------
# X API helpers
# ---------------------------------------------------------------------

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


def x_request(method: str, path: str, *, ambiguous_write: bool = False, **kwargs) -> dict:
    """Send an authenticated X API request with bounded retries."""
    url = f"{X_BASE}{path}"

    log.debug("X request: %s %s", method, url)

    if "params" in kwargs:
        log_json_debug("X request params", kwargs["params"])

    if "json" in kwargs:
        log_json_debug("X request json", kwargs["json"])

    if "data" in kwargs:
        log_json_debug("X request form data", kwargs["data"])

    if "files" in kwargs:
        log.debug("X request includes files: %s", list(kwargs["files"].keys()))

    if method.upper() not in {"GET", "HEAD", "OPTIONS"}:
        require_remote_operation_unpaused(f"X {method.upper()} {path}")

    try:
        response = requests.request(
            method,
            url,
            auth=AUTH,
            timeout=request_timeout(),
            **kwargs,
        )
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

    log.debug("X response status: %s", response.status_code)
    log.debug(
        "X response headers: x-rate-limit-limit=%s remaining=%s reset=%s",
        response.headers.get("x-rate-limit-limit"),
        response.headers.get("x-rate-limit-remaining"),
        response.headers.get("x-rate-limit-reset"),
    )

    if response.status_code >= 400:
        log.error("X API error %s: %s", response.status_code, response.text)
        reset_epoch = print_rate_limit_headers(response)

        if ambiguous_write and 500 <= response.status_code < 600:
            raise AmbiguousRemotePostOutcome(
                f"X may have accepted the write before returning HTTP {response.status_code}: {response.text}",
                service="x",
                status_code=response.status_code,
                reset_epoch=reset_epoch,
                request_method=method,
                request_path=path,
            )

        raise ApiError(
            f"X API error {response.status_code}: {response.text}",
            service="x",
            status_code=response.status_code,
            reset_epoch=reset_epoch,
            request_method=method,
            request_path=path,
        )

    if not response.text:
        log.debug("X response has empty body")
        return {}

    try:
        data = response.json()
    except json.JSONDecodeError as e:
        log.error("X API returned non-JSON response: %s", response.text[:1000])
        if ambiguous_write:
            raise AmbiguousRemotePostOutcome(
                f"X may have accepted the write but returned a non-JSON response: {response.text[:500]}",
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
        message = f"X API response must be a JSON object, got {type(data).__name__}"
        if ambiguous_write:
            raise AmbiguousRemotePostOutcome(
                f"X may have accepted the write but its response was not a JSON object: {type(data).__name__}",
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

    log_json_debug("X response json", data)
    return data


def x_bearer_request(method: str, path: str, **kwargs) -> dict:
    """Send a bearer-authenticated X API request with bounded retries."""
    if not X_BEARER_TOKEN:
        raise ApiError("X_BEARER_TOKEN is not set", service="x")

    url = f"{X_BASE}{path}"

    log.debug("X bearer request: %s %s", method, url)

    if "params" in kwargs:
        log_json_debug("X bearer request params", kwargs["params"])

    if method.upper() not in {"GET", "HEAD", "OPTIONS"}:
        require_remote_operation_unpaused(f"X bearer {method.upper()} {path}")

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
    if not isinstance(error, ApiError):
        return False
    if error.service != "x" or error.status_code != 400:
        return False
    message = str(error)
    candidate_messages: list[str] = []
    json_start = message.find("{")
    if json_start >= 0:
        try:
            payload = json.loads(message[json_start:])
        except (json.JSONDecodeError, TypeError, ValueError):
            payload = None
        if isinstance(payload, dict):
            errors = payload.get("errors")
            if isinstance(errors, dict):
                errors = [errors]
            if isinstance(errors, list):
                for item in errors:
                    if not isinstance(item, dict):
                        continue
                    for key in ("message", "detail", "reason"):
                        value = item.get(key)
                        if isinstance(value, str):
                            candidate_messages.append(value)
            for key in ("message", "detail", "reason"):
                value = payload.get(key)
                if isinstance(value, str):
                    candidate_messages.append(value)
            if not candidate_messages:
                return False
    if not candidate_messages:
        candidate_messages = [message]

    def explicitly_rejects_cursor(candidate: str) -> bool:
        candidate = candidate.casefold()
        names_cursor = any(
            marker in candidate
            for marker in (
                "pagination_token",
                "pagination token",
                "next_token",
                "next token",
                "pagination cursor",
            )
        )
        rejects_cursor = any(
            marker in candidate
            for marker in (
                "invalid",
                "expired",
                "malformed",
                "not valid",
                "not recognised",
                "not recognized",
            )
        )
        return names_cursor and rejects_cursor

    return any(explicitly_rejects_cursor(item) for item in candidate_messages)


def x_paginated_get(
    request_func,
    path: str,
    params: dict,
    *,
    max_pages: int,
    label: str,
    on_invalid_cursor=None,
) -> dict:
    """
    Read bounded pages from an X API collection endpoint.

    A cursor-specific HTTP 400 gets one recovery from the original collection
    head. The caller clears its durable saved cursor before that retry. Other
    client errors remain fail-closed. A repeated token is rejected before it
    can be requested twice or persisted as a continuation.
    """
    base_params = dict(params)
    recovered_invalid_cursor = False
    cursor_state_invalidated = False
    requested_tokens: set[str] = set()

    def invalidate_cursor_state() -> None:
        nonlocal cursor_state_invalidated
        if cursor_state_invalidated:
            return
        if on_invalid_cursor is not None:
            on_invalid_cursor()
        cursor_state_invalidated = True

    while True:
        combined: dict[str, object] = {"data": []}
        users_by_id: dict[str, dict] = {}
        media_by_key: dict[str, dict] = {}
        next_token = ""
        pages_fetched = 0
        restart_from_head = False

        for page in range(1, max(1, int(max_pages)) + 1):
            pages_fetched = page
            page_params = dict(base_params)
            if next_token:
                page_params["pagination_token"] = next_token
            request_token = str(page_params.get("pagination_token") or "")
            if request_token:
                if request_token in requested_tokens:
                    invalidate_cursor_state()
                    raise PaginationCursorProtocolError(
                        f"X {label} repeated pagination token before request",
                        service="x",
                    )
                requested_tokens.add(request_token)

            try:
                result = request_func(path, page_params)
            except ApiError as exc:
                if (
                    request_token
                    and not recovered_invalid_cursor
                    and api_error_is_invalid_pagination_cursor(exc)
                ):
                    invalidate_cursor_state()
                    recovered_invalid_cursor = True
                    base_params.pop("pagination_token", None)
                    restart_from_head = True
                    log.warning(
                        "X %s rejected a pagination cursor; cleared the saved "
                        "cursor and retrying once from the collection head",
                        label,
                    )
                    break
                raise

            if not isinstance(result, dict):
                raise ApiError(f"X {label} returned a malformed paginated response object", service="x")
            page_data = result.get("data", [])
            includes = result.get("includes", {})
            meta = result.get("meta", {})
            if not isinstance(page_data, list) or any(not isinstance(item, dict) for item in page_data):
                raise ApiError(f"X {label} returned malformed paginated response data", service="x")
            if not isinstance(includes, dict):
                raise ApiError(f"X {label} returned malformed paginated response includes", service="x")
            users = includes.get("users", [])
            media_items = includes.get("media", [])
            if not isinstance(users, list) or any(not isinstance(user, dict) for user in users):
                raise ApiError(f"X {label} returned malformed paginated response users", service="x")
            if not isinstance(media_items, list) or any(not isinstance(media, dict) for media in media_items):
                raise ApiError(f"X {label} returned malformed paginated response media", service="x")
            if not isinstance(meta, dict):
                raise ApiError(f"X {label} returned malformed paginated response meta", service="x")
            combined["data"].extend(page_data)

            for user in users:
                user_id = str(user.get("id", ""))
                if user_id:
                    users_by_id[user_id] = user

            for media in media_items:
                media_key = str(media.get("media_key", ""))
                if media_key:
                    media_by_key[media_key] = media

            next_token = str(meta.get("next_token", "") or "")
            log.info(
                "Fetched %s page %d/%d items=%d next_token=%s",
                label,
                page,
                max_pages,
                len(page_data) if isinstance(page_data, list) else 0,
                bool(next_token),
            )
            if next_token and next_token in requested_tokens:
                invalidate_cursor_state()
                raise PaginationCursorProtocolError(
                    f"X {label} returned a repeated pagination token",
                    service="x",
                )
            if not next_token:
                break

        if restart_from_head:
            continue
        break

    includes: dict[str, list[dict]] = {}
    if users_by_id:
        includes["users"] = list(users_by_id.values())
    if media_by_key:
        includes["media"] = list(media_by_key.values())
    if includes:
        combined["includes"] = includes
    combined["_pagination"] = {
        "pages_fetched": pages_fetched,
        "truncated": bool(next_token),
        "next_token": next_token or None,
        "invalid_cursor_recovered": recovered_invalid_cursor,
    }
    if next_token:
        log.warning(
            "Pagination truncated for %s after %d page(s); more results remain",
            label,
            pages_fetched,
        )

    return combined


# ---------------------------------------------------------------------
# Tweet cache / thread context
# ---------------------------------------------------------------------

def prune_tweet_cache(state: dict) -> None:
    """Prune tweet cache."""
    cache = state.setdefault("tweet_cache", {})
    cutoff = now_epoch() - TWEET_CACHE_MAX_AGE_SECONDS

    pruned = {
        str(tweet_id): tweet
        for tweet_id, tweet in cache.items()
        if int(tweet.get("cached_epoch", 0) or 0) >= cutoff
    }

    if len(pruned) > TWEET_CACHE_MAX_ITEMS:
        items = sorted(
            pruned.items(),
            key=lambda kv: int(kv[1].get("cached_epoch", 0) or 0),
            reverse=True,
        )
        pruned = dict(items[:TWEET_CACHE_MAX_ITEMS])

    if len(pruned) != len(cache):
        log.info("Pruned tweet cache from %d to %d items", len(cache), len(pruned))

    state["tweet_cache"] = pruned


def record_recent_own_post(state: dict, tweet_id: str) -> None:
    """Record recent own post."""
    tweet_id = str(tweet_id)

    ids = [str(x) for x in state.get("recent_own_post_ids", []) if str(x) != tweet_id]
    ids.insert(0, tweet_id)
    state["recent_own_post_ids"] = ids[:RECENT_OWN_POST_IDS_MAX]

    log.info("Recorded recent own post id=%s recent_count=%d", tweet_id, len(state["recent_own_post_ids"]))


def seed_recent_own_post_ids_from_cache(state: dict) -> None:
    """Seed recent own post IDs from cache."""
    if state.get("recent_own_post_ids"):
        return

    cache = state.get("tweet_cache", {})
    own_posts: list[tuple[int, str]] = []

    for tweet_id, tweet in cache.items():
        if str(tweet.get("author_id")) != str(MY_USER_ID):
            continue

        post_type = tweet.get("post_type")
        if post_type not in {"quote", "daily_meme"}:
            continue

        own_posts.append((int(tweet.get("cached_epoch", 0) or 0), str(tweet_id)))

    own_posts.sort(reverse=True)
    seeded = [tweet_id for _, tweet_id in own_posts[:RECENT_OWN_POST_IDS_MAX]]

    if not seeded and state.get("last_main_post_id"):
        seeded = [str(state["last_main_post_id"])]

    state["recent_own_post_ids"] = seeded
    log.info("Seeded recent_own_post_ids from cache count=%d", len(seeded))


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
    """Return the cache tweet."""
    prune_tweet_cache(state)

    tweet_id = str(tweet_id)
    cache = state.setdefault("tweet_cache", {})

    cached_tweet = {
        "id": tweet_id,
        "author_id": str(author_id),
        "conversation_id": str(conversation_id or tweet_id),
        "created_at": created_at or current_datetime().isoformat(),
        "referenced_tweets": referenced_tweets or [],
        "text": text or "",
        "cached_epoch": now_epoch(),
    }

    if image_summary:
        cached_tweet["image_summary"] = image_summary

    if post_type:
        cached_tweet["post_type"] = post_type

    normalised_tweet = normalise_tweet_cache_entry(tweet_id, cached_tweet, path=STATE_FILE)
    if normalised_tweet is None:
        raise ValueError(f"Refusing to cache malformed tweet entry id={tweet_id}")

    cached_tweet = normalised_tweet
    cache[tweet_id] = cached_tweet
    state["tweet_cache"] = cache

    log.info(
        "Cached tweet id=%s author_id=%s cache_size=%d post_type=%s image_summary=%s",
        tweet_id,
        author_id,
        len(cache),
        post_type or "",
        bool(image_summary),
    )

    return cached_tweet


def get_immediate_parent_id(tweet: dict) -> str | None:
    """Return immediate parent ID."""
    referenced_tweets = tweet.get("referenced_tweets", [])
    if referenced_tweets is None:
        return None
    if not isinstance(referenced_tweets, list):
        raise ApiError("X tweet returned malformed referenced_tweets", service="x")

    for ref in referenced_tweets:
        if not isinstance(ref, dict):
            raise ApiError("X tweet returned malformed referenced_tweets", service="x")
        if ref.get("type") == "replied_to":
            parent_id = parse_tweet_id(ref.get("id"), context="parent reference")
            if parent_id is None:
                raise ApiError("X tweet returned malformed referenced_tweets", service="x")
            return str(parent_id)

    return None


def get_tweet_by_id(tweet_id: str) -> dict | None:
    """Fetch one post from X by ID."""
    log.info("Fetching tweet by id. tweet_id=%s", tweet_id)

    params = {
        "tweet.fields": "author_id,created_at,conversation_id,referenced_tweets",
    }

    result = x_request(
        "GET",
        f"/2/tweets/{tweet_id}",
        params=params,
    )

    tweet = result.get("data")
    if tweet is not None and not isinstance(tweet, dict):
        raise ApiError("X tweet lookup returned malformed tweet data", service="x")
    log_json_debug("Fetched tweet", tweet)

    return tweet


def get_tweet_by_id_cached(tweet_id: str, state: dict) -> dict | None:
    """Return a cached post or fetch it from X by ID."""
    prune_tweet_cache(state)

    tweet_id = str(tweet_id)
    cache = state.setdefault("tweet_cache", {})
    cached = cache.get(tweet_id)

    if cached:
        log.info("Using cached tweet for context. tweet_id=%s", tweet_id)
        return cached

    tweet = get_tweet_by_id(tweet_id)

    if tweet:
        cached_tweet = cache_tweet(
            state,
            tweet_id=str(tweet.get("id", tweet_id)),
            text=tweet.get("text", ""),
            author_id=str(tweet.get("author_id", "")),
            conversation_id=str(tweet.get("conversation_id", tweet_id)),
            referenced_tweets=tweet.get("referenced_tweets", []),
            created_at=tweet.get("created_at"),
        )
        save_state(state)
        return cached_tweet

    return None


def clean_text_for_reply_context(text: str) -> str:
    """Return normalised text for a bounded AI reply context."""
    text = html.unescape(text or "")
    text = re.sub(r"https?://\S+", "", text)
    text = " ".join(text.split())
    return text.strip()


def attach_media_to_tweets(tweets: list[dict], includes: dict | None) -> None:
    """Attach media to tweets."""
    media_items = (includes or {}).get("media", [])
    if not isinstance(media_items, list):
        return

    media_by_key = {
        str(media.get("media_key")): media
        for media in media_items
        if isinstance(media, dict) and media.get("media_key")
    }
    if not media_by_key:
        return

    for tweet in tweets:
        attachments = tweet.get("attachments", {})
        if not isinstance(attachments, dict):
            continue
        media_keys = attachments.get("media_keys", [])
        if not isinstance(media_keys, list):
            continue
        attached = [
            media_by_key[str(media_key)]
            for media_key in media_keys
            if str(media_key) in media_by_key
        ]
        if attached:
            tweet["_attached_media"] = attached


def candidate_native_photo_media(candidate: dict) -> tuple[list[dict], int]:
    """Return the candidate native photo media."""
    media_items = candidate.get("_attached_media", [])
    if not isinstance(media_items, list):
        media_items = []

    photo_records = [
        media
        for media in media_items
        if isinstance(media, dict) and str(media.get("type", "")).lower() == "photo"
    ]
    usable: list[dict] = []
    for media in photo_records:
        url = str(media.get("url") or "").strip()
        if not url:
            continue
        usable.append(
            {
                "media_key": str(media.get("media_key", "")),
                "url": url,
            }
        )
        if len(usable) >= MAX_REPLY_CONTEXT_PHOTOS:
            break

    return usable, len(photo_records)


def reply_media_context_for_candidate(candidate: dict, *, lane: str, target_id: str) -> dict:
    """Return the reply media context for candidate."""
    photos, expected_photo_count = candidate_native_photo_media(candidate)
    if photos:
        log.info(
            "Reply media context lane=%s target_id=%s photos=%d mode=multimodal status=supplied",
            lane,
            target_id,
            len(photos),
        )
        return {
            "lane": lane,
            "target_id": str(target_id),
            "mode": "multimodal",
            "status": "supplied",
            "photos_expected": expected_photo_count,
            "photos": photos,
        }

    if expected_photo_count:
        log.warning(
            "Reply media context unavailable lane=%s target_id=%s photos_expected=%d mode=multimodal status=unavailable",
            lane,
            target_id,
            expected_photo_count,
        )
        return {
            "lane": lane,
            "target_id": str(target_id),
            "mode": "multimodal",
            "status": "unavailable",
            "photos_expected": expected_photo_count,
            "photos": [],
        }

    return {
        "lane": lane,
        "target_id": str(target_id),
        "mode": "none",
        "status": "none",
        "photos_expected": 0,
        "photos": [],
    }


def redact_xai_payload_for_log(payload: dict) -> dict:
    """Redact xAI payload for log."""
    redacted = copy.deepcopy(payload)
    for message in redacted.get("messages", []) or []:
        content = message.get("content")
        if not isinstance(content, list):
            continue
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "image_url":
                continue
            image_url = part.get("image_url")
            if isinstance(image_url, dict) and image_url.get("url"):
                image_url["url"] = "[redacted native X image URL]"
    return redacted


def tweet_context_text(tweet: dict) -> str:
    """Return the tweet context text."""
    cleaned = clean_text_for_reply_context(tweet.get("text", ""))

    if cleaned:
        return cleaned

    image_summary = clean_text_for_reply_context(tweet.get("image_summary", ""))
    if image_summary:
        return f"[Image/meme summary: {image_summary}]"

    return ""


def trim_context_text(text: str, max_chars: int) -> str:
    """Trim context text."""
    text = clean_text_for_reply_context(text)

    if max_chars <= 0:
        return ""
    if len(text) <= max_chars:
        return text
    if max_chars <= 3:
        return text[:max_chars]
    prefix = text[:max_chars - 3].rsplit(" ", 1)[0].rstrip(".,;:")
    if not prefix:
        prefix = text[:max_chars - 3]
    return prefix + "..."


def build_parent_chain(mention: dict, state: dict) -> list[dict]:
    """Build bounded earlier-thread context for a reply candidate."""
    chain: list[dict] = []
    seen_ids: set[str] = set()

    parent_id = get_immediate_parent_id(mention)

    while parent_id and len(chain) < THREAD_CONTEXT_MAX_DEPTH:
        if parent_id in seen_ids:
            log.warning("Detected parent-chain loop at tweet_id=%s", parent_id)
            break

        seen_ids.add(parent_id)

        try:
            parent = get_tweet_by_id_cached(parent_id, state)
        except ApiError as exc:
            if api_error_is_permanent_target_failure(exc):
                log.warning(
                    "Parent tweet_id=%s is permanently unavailable with status=%s; continuing without it",
                    parent_id,
                    getattr(exc, "status_code", None),
                )
                break
            raise
        if not parent:
            log.info("Could not fetch/cache parent tweet_id=%s", parent_id)
            break

        chain.append(parent)
        parent_id = get_immediate_parent_id(parent)

    chain.reverse()
    log.info("Built parent chain with %d item(s)", len(chain))
    log_json_debug("Parent chain", chain)

    return chain


def is_our_auto_reply(tweet: dict | None, state: dict) -> bool:
    """Return whether is our auto reply."""
    if not tweet:
        return False

    if str(tweet.get("author_id")) != str(MY_USER_ID):
        return False

    own_auto_reply_ids = set(str(x) for x in state.get("own_auto_reply_ids", []))
    return str(tweet.get("id")) in own_auto_reply_ids


def _reply_context_post(tweet: dict, *, maximum_chars: int) -> dict[str, str]:
    """Return one bounded post with an explicit role label."""
    author_id = str(tweet.get("author_id") or "")
    if author_id == str(MY_USER_ID):
        author_role = "account"
    elif author_id:
        author_role = "user"
    else:
        author_role = "unknown"
    return {
        "post_id": str(tweet.get("id") or "unknown"),
        "author_role": author_role,
        "text": trim_context_text(tweet_context_text(tweet), maximum_chars),
    }


def _quoted_post_for_reply_context(candidate: dict, state: dict) -> dict[str, str] | None:
    """Return one explicitly quoted post, including an unavailable marker when known."""
    references = candidate.get("referenced_tweets", []) or []
    for reference in references:
        if not isinstance(reference, dict) or reference.get("type") != "quoted":
            continue
        quoted_id = str(reference.get("id") or "")
        if not quoted_id:
            continue
        try:
            quoted = get_tweet_by_id_cached(quoted_id, state)
        except ApiError as exc:
            if not api_error_is_permanent_target_failure(exc):
                raise
            quoted = None
        if quoted is None:
            return {
                "post_id": quoted_id,
                "author_role": "unknown",
                "text": "[Quoted post unavailable.]",
            }
        return _reply_context_post(
            quoted,
            maximum_chars=THREAD_CONTEXT_MAX_CHARS_PER_POST,
        )
    return None


def build_context_for_reply_ai(mention: dict, state: dict) -> tuple[dict[str, object], bool]:
    """Build structured incoming, parent-thread and quoted-post context."""
    mention_id = str(mention.get("id"))
    mention_text = mention.get("text", "").strip()

    if not mention_text:
        log.info("Mention %s has no text; skipping", mention_id)
        return {}, False

    chain: list[dict] = []

    if ALWAYS_FETCH_PARENT_FOR_CONTEXT:
        try:
            chain = build_parent_chain(mention, state)
        except ApiError as e:
            log.warning("Could not build parent chain for mention %s: %s", mention_id, e)
            raise
        except Exception as e:
            log.warning("Unexpected failure building parent chain for mention %s: %s", mention_id, e)
            return {}, False

    immediate_parent = chain[-1] if chain else None

    if SKIP_REPLIES_TO_OWN_AUTO_REPLIES and is_our_auto_reply(immediate_parent, state):
        log.info(
            "Skipping mention %s: immediate parent %s is one of our own auto-replies",
            mention_id,
            immediate_parent.get("id") if immediate_parent else None,
        )
        return {}, False

    # The AI-first context contract permits at most three inherited posts. Prefer the nearest
    # context and enforce the independently configurable aggregate text budget.
    remaining_parent_chars = min(max(int(THREAD_CONTEXT_MAX_TOTAL_CHARS), 0), 6_000)
    maximum_parent_chars = min(max(int(THREAD_CONTEXT_MAX_CHARS_PER_POST), 0), 2_000)
    parent_thread: list[dict[str, str]] = []
    for tweet in reversed(chain[-3:]):
        post = _reply_context_post(
            tweet,
            maximum_chars=min(maximum_parent_chars, remaining_parent_chars),
        )
        remaining_parent_chars -= len(post["text"])
        parent_thread.insert(0, post)
    context: dict[str, object] = {
        "target_id": mention_id,
        "thread_id": str(mention.get("conversation_id") or mention_id),
        "lane": str(mention.get("_source") or "mention"),
        "incoming_contribution": trim_context_text(mention_text, REPLY_INCOMING_MAX_CHARS),
        "quoted_post": _quoted_post_for_reply_context(mention, state),
        "parent_thread": parent_thread,
        "clarification_request": None,
        "current_date": current_datetime().strftime("%Y-%m-%d"),
    }

    log.info(
        "Built AI reply context for mention %s. chain_items=%d immediate_parent=%s quoted=%s",
        mention_id,
        len(chain),
        immediate_parent.get("id") if immediate_parent else None,
        bool(context["quoted_post"]),
    )
    log_json_debug("Context sent to AI reply pipeline", context)

    return context, True


# ---------------------------------------------------------------------
# Mentions
# ---------------------------------------------------------------------

def reply_target_is_directly_eligible(tweet: dict) -> bool:
    """Check only the target post itself for X reply eligibility evidence."""
    if str(tweet.get("author_id") or "") == str(MY_USER_ID):
        return True

    entities = tweet.get("entities")
    if isinstance(entities, dict):
        mentions = entities.get("mentions")
        if isinstance(mentions, list):
            for mention in mentions:
                if not isinstance(mention, dict):
                    continue
                if str(mention.get("id") or "") == str(MY_USER_ID):
                    return True
                username = str(mention.get("username") or "").lstrip("@")
                if MY_USERNAME and username.casefold() == MY_USERNAME.casefold():
                    return True
        return False

    text = str(tweet.get("text") or "")
    if MY_USERNAME and re.search(
        rf"(?<![A-Za-z0-9_])@{re.escape(MY_USERNAME)}(?![A-Za-z0-9_])",
        text,
        flags=re.IGNORECASE,
    ):
        return True
    return False


def get_mentions(state: dict) -> list[dict]:
    """Fetch a bounded page set of direct mention candidates."""
    base_since_id = str(state.get("last_seen_mention_id") or "")
    log.info(
        "Fetching mentions. last_seen_mention_id=%s max_results=%s",
        base_since_id or None,
        MAX_MENTIONS_PER_CHECK,
    )

    params = {
        "max_results": MAX_MENTIONS_PER_CHECK,
        "tweet.fields": "author_id,created_at,conversation_id,referenced_tweets,attachments,entities",
        "expansions": "author_id,attachments.media_keys",
        "media.fields": "media_key,type,url,preview_image_url",
    }

    if base_since_id:
        params["since_id"] = base_since_id

    mention_pagination = state.get("mention_pagination", {})
    if not isinstance(mention_pagination, dict):
        mention_pagination = {}
    resume_token = ""
    if str(mention_pagination.get("base_since_id", "")) == base_since_id:
        resume_token = str(mention_pagination.get("next_token", "") or "")
    if resume_token:
        params["pagination_token"] = resume_token
        log.info("Resuming mention pagination from saved cursor")

    def clear_invalid_mention_cursor() -> None:
        state["mention_pagination"] = {}
        save_state(state, durable=True)

    result = x_paginated_get(
        lambda path, page_params: x_request("GET", path, params=page_params),
        f"/2/users/{MY_USER_ID}/mentions",
        params,
        max_pages=MENTIONS_MAX_PAGES_PER_CHECK,
        label="mentions",
        on_invalid_cursor=clear_invalid_mention_cursor,
    )

    mentions = result.get("data", [])
    attach_media_to_tweets(mentions, result.get("includes", {}))
    pagination = result.get("_pagination", {}) if isinstance(result.get("_pagination", {}), dict) else {}
    truncated = bool(pagination.get("truncated"))
    log.info("Fetched %d mentions", len(mentions))
    log_json_debug("Mentions returned", mentions)
    if truncated:
        log.warning("Mention pagination was truncated; mention watermark will not advance this cycle")
        next_token = str(pagination.get("next_token") or "")
        if not next_token:
            raise ApiError(
                "X mentions marked pagination truncated without a continuation token",
                service="x",
            )
        continuation = {
            "base_since_id": base_since_id,
            "next_token": next_token,
        }
        state["mention_pagination"] = continuation
        save_state(state)
        for mention in mentions:
            mention["_pagination_truncated"] = True
            mention["_mention_pagination"] = copy.deepcopy(continuation)
    elif mention_pagination:
        state["mention_pagination"] = {}
        save_state(state)

    if mentions:
        for mention in mentions:
            mention_id = mention.get("id")
            if parse_tweet_id(mention_id, context="mention") is None:
                continue

            cache_tweet(
                state,
                tweet_id=str(mention_id),
                text=mention.get("text", ""),
                author_id=str(mention.get("author_id", "")),
                conversation_id=str(mention.get("conversation_id", mention_id)),
                referenced_tweets=mention.get("referenced_tweets", []),
                created_at=mention.get("created_at"),
            )

        save_state(state)

    return mentions


def get_hot_post_reply_candidates(state: dict) -> list[dict]:
    """
    Fetch directly reply-eligible contributions in conversations for watched hot posts.

    This deliberately uses the same extra_quote_watch_post_ids.txt file as the
    quote-tweet watch lane, so adding one hot post ID makes both lanes watch it.
    X permits a reply only when this account authored or is directly mentioned
    in the target post. Organic sub-thread chatter is therefore terminally
    skipped before applying the candidate cap, while eligible candidates are
    marked with _source=hot_post_reply and passed through the normal reply path.

    A soft per-hot-post since_id watermark is used only after a search returns
    no usable candidates. That avoids repeatedly downloading the same dead
    results, while preserving response ability when there is anything actionable.
    A full rescan is forced every HOT_POST_REPLY_FULL_RESCAN_EVERY_CHECKS passes.
    """
    if not ENABLE_HOT_POST_REPLY_CHECKS:
        return []

    if lane_paused("disable_replies", "disable_hot_post_replies"):
        log.info("Skipping hot-post reply search due to runtime control file")
        return []

    if in_api_cooldown(state, scope="quote"):
        log.info("Skipping hot-post reply search due to quote API cooldown")
        return []

    try:
        watched_post_ids = load_extra_quote_watch_post_ids()
    except Exception as exc:
        log.warning("Could not load hot-post reply watch IDs: %s", exc)
        return []

    if not watched_post_ids:
        return []

    skipped_hot_reply_ids = set(str(x) for x in state.get("skipped_hot_reply_ids", []))
    replied_to_ids = set(str(x) for x in state.get("replied_to_ids", []))

    since_ids = state.get("hot_post_reply_since_ids", {})
    if not isinstance(since_ids, dict):
        since_ids = {}

    check_counts = state.get("hot_post_reply_check_counts", {})
    if not isinstance(check_counts, dict):
        check_counts = {}

    pagination_tokens = state.get("hot_post_reply_pagination_tokens", {})
    if not isinstance(pagination_tokens, dict):
        pagination_tokens = {}

    candidates: list[dict] = []
    state_changed = False

    watched_post_id_set = {str(post_id) for post_id in watched_post_ids}
    pruned_since_ids = {str(post_id): value for post_id, value in since_ids.items() if str(post_id) in watched_post_id_set}
    pruned_check_counts = {
        str(post_id): value
        for post_id, value in check_counts.items()
        if str(post_id) in watched_post_id_set
    }
    pruned_pagination_tokens = {
        str(post_id): value
        for post_id, value in pagination_tokens.items()
        if str(post_id) in watched_post_id_set
    }
    if (
        pruned_since_ids != since_ids
        or pruned_check_counts != check_counts
        or pruned_pagination_tokens != pagination_tokens
    ):
        log.info(
            "Pruned hot-post reply tracking maps. since_ids=%d->%d check_counts=%d->%d pagination_tokens=%d->%d",
            len(since_ids),
            len(pruned_since_ids),
            len(check_counts),
            len(pruned_check_counts),
            len(pagination_tokens),
            len(pruned_pagination_tokens),
        )
        state_changed = True
    since_ids = pruned_since_ids
    check_counts = pruned_check_counts
    pagination_tokens = pruned_pagination_tokens

    log.info(
        "Hot-post reply check loaded %d watched post(s) from %s: %s",
        len(watched_post_ids),
        EXTRA_QUOTE_WATCH_FILE,
        ", ".join(watched_post_ids),
    )

    for original_post_id in watched_post_ids:
        if len(candidates) >= MAX_HOT_POST_REPLIES_PER_CHECK:
            break

        original_post_id = str(original_post_id)
        previous_count = int(check_counts.get(original_post_id, 0) or 0)
        if HOT_POST_REPLY_FULL_RESCAN_EVERY_CHECKS > 0:
            current_count = (previous_count % HOT_POST_REPLY_FULL_RESCAN_EVERY_CHECKS) + 1
        else:
            current_count = previous_count + 1
        check_counts[original_post_id] = current_count
        state_changed = True

        full_rescan = False
        if HOT_POST_REPLY_FULL_RESCAN_EVERY_CHECKS > 0:
            full_rescan = current_count == HOT_POST_REPLY_FULL_RESCAN_EVERY_CHECKS
        if full_rescan and original_post_id in pagination_tokens:
            pagination_tokens.pop(original_post_id, None)
            state_changed = True

        # conversation_id finds replies in the original post's conversation.
        # We exclude this account and retweets; later filtering keeps only actual replies.
        query = f"conversation_id:{original_post_id} -from:{MY_USER_ID} -is:retweet"

        params = {
            "query": query,
            "max_results": HOT_POST_REPLY_SEARCH_API_MAX_RESULTS,
            "tweet.fields": "author_id,created_at,conversation_id,referenced_tweets,attachments,entities",
            "expansions": "author_id,attachments.media_keys",
            "media.fields": "media_key,type,url,preview_image_url",
        }

        since_id_used = ""
        if HOT_POST_REPLY_USE_SINCE_ID and not full_rescan:
            candidate_since_id = str(since_ids.get(original_post_id, "") or "")
            if candidate_since_id:
                params["since_id"] = candidate_since_id
                since_id_used = candidate_since_id
                log.info(
                    "Hot-post reply search for post_id=%s using since_id=%s",
                    original_post_id,
                    since_id_used,
                )

        resume_token = str(pagination_tokens.get(original_post_id, "") or "")
        if resume_token and not full_rescan:
            params["pagination_token"] = resume_token
            log.info("Resuming hot-post reply pagination for post_id=%s", original_post_id)

        def clear_invalid_hot_post_cursor() -> None:
            pagination_tokens.pop(original_post_id, None)
            state["hot_post_reply_pagination_tokens"] = dict(pagination_tokens)
            save_state(state, durable=True)

        try:
            result = x_paginated_get(
                x_quote_lookup_request,
                "/2/tweets/search/recent",
                params,
                max_pages=HOT_POST_REPLY_SEARCH_MAX_PAGES_PER_CHECK,
                label=f"hot-post replies for {original_post_id}",
                on_invalid_cursor=clear_invalid_hot_post_cursor,
            )
        except ApiError:
            log.exception("Failed to fetch hot-post replies for post %s", original_post_id)
            raise
        except Exception:
            log.exception("Unexpected failure fetching hot-post replies for post %s", original_post_id)
            raise

        replies = result.get("data", [])
        attach_media_to_tweets(replies, result.get("includes", {}))
        pagination = result.get("_pagination", {}) if isinstance(result.get("_pagination", {}), dict) else {}
        pagination_truncated = bool(pagination.get("truncated"))
        next_pagination_token = str(pagination.get("next_token") or "")
        log.info("Fetched %d hot-post conversation candidate(s) for post_id=%s", len(replies), original_post_id)
        log_json_debug("Hot-post reply candidates returned", replies)

        valid_replies = valid_tweets_sorted_by_id(replies, context="hot-post reply candidate")
        raw_ids = [int(str(reply["id"])) for reply in valid_replies]
        raw_highest_id = str(max(raw_ids)) if raw_ids else ""
        candidates_for_this_post = 0

        for reply in valid_replies:
            reply_id = str(reply.get("id", ""))
            author_id = str(reply.get("author_id", ""))

            if not reply_id or reply_id == str(original_post_id):
                continue

            if reply_id in replied_to_ids or reply_id in skipped_hot_reply_ids:
                log.info("Skipping hot-post reply %s: already replied/skipped", reply_id)
                continue

            if author_id == str(MY_USER_ID):
                log.info("Skipping hot-post reply %s: authored by own account", reply_id)
                mark_hot_post_reply_skipped(
                    state,
                    reply_id,
                    reason="own_account",
                    original_post_id=str(original_post_id),
                    retryable=False,
                )
                state_changed = True
                continue

            refs = reply.get("referenced_tweets", []) or []
            if not any(ref.get("type") == "replied_to" for ref in refs):
                log.info(
                    "Skipping hot-post candidate %s: not an ordinary reply. referenced_tweets=%s",
                    reply_id,
                    refs,
                )
                mark_hot_post_reply_skipped(
                    state,
                    reply_id,
                    reason="not_ordinary_reply",
                    original_post_id=str(original_post_id),
                    retryable=False,
                )
                state_changed = True
                continue

            if not reply_target_is_directly_eligible(reply):
                reason = "target_does_not_directly_mention_account"
                log.info(
                    "Skipping hot-post reply %s before candidate limiting: "
                    "target is not directly reply-eligible",
                    reply_id,
                )
                drafts = state.get("pending_ai_reply_drafts", {})
                pending_key = pending_ai_reply_draft_key(reply_id, "hot_post_reply")
                pending_record = drafts.get(pending_key) if isinstance(drafts, dict) else None
                if isinstance(pending_record, dict):
                    log_event(
                        "ai_reply_pipeline_outcome",
                        status="posting_failed_terminal",
                        lane="hot_post_reply",
                        target_id=reply_id,
                        reply_post_id="",
                        strategy_version=pending_record.get("strategy_version"),
                        mode=pending_record.get("mode"),
                        reviewer_verdict=pending_record.get("reviewer_verdict"),
                        failure_reason="reply_not_permitted_preflight",
                    )
                    clear_pending_ai_reply(state, reply_id, "hot_post_reply")
                record_terminal_reply_evaluation(
                    state,
                    target_id=reply_id,
                    lane="hot_post_reply",
                    reason=reason,
                    outcome="reply_not_permitted",
                )
                mark_hot_post_reply_skipped(
                    state,
                    reply_id,
                    reason=reason,
                    original_post_id=original_post_id,
                    retryable=False,
                )
                log_event(
                    "reply_target_terminal",
                    lane="hot_post_reply",
                    target_id=reply_id,
                    outcome="reply_not_permitted",
                    reason=reason,
                )
                state_changed = True
                continue

            reply["_source"] = "hot_post_reply"
            reply["_hot_original_post_id"] = str(original_post_id)

            cache_tweet(
                state,
                tweet_id=reply_id,
                text=reply.get("text", ""),
                author_id=author_id,
                conversation_id=str(reply.get("conversation_id", reply_id)),
                referenced_tweets=reply.get("referenced_tweets", []),
                created_at=reply.get("created_at"),
                post_type="hot_post_reply",
            )

            candidates.append(reply)
            candidates_for_this_post += 1
            state_changed = True

            if len(candidates) >= MAX_HOT_POST_REPLIES_PER_CHECK:
                break

        watermark_updated = False
        if HOT_POST_REPLY_USE_SINCE_ID and raw_highest_id and candidates_for_this_post == 0 and not pagination_truncated:
            old_since = str(since_ids.get(original_post_id, "") or "")
            if not old_since or int(raw_highest_id) > int(old_since):
                since_ids[original_post_id] = raw_highest_id
                watermark_updated = True
                state_changed = True
                log.info(
                    "Updated hot-post reply since_id for post_id=%s to %s after no usable candidates",
                    original_post_id,
                    raw_highest_id,
                )
        if pagination_truncated:
            log.warning(
                "Not updating hot-post reply since_id for post_id=%s because pagination was truncated",
                original_post_id,
            )
            if next_pagination_token:
                pagination_tokens[original_post_id] = next_pagination_token
                state_changed = True
        elif original_post_id in pagination_tokens:
            pagination_tokens.pop(original_post_id, None)
            state_changed = True

        log_event(
            "hot_search",
            lane="hot_post",
            original_post_id=original_post_id,
            raw_count=len(replies),
            usable_count=candidates_for_this_post,
            since_id_used=since_id_used or None,
            full_rescan=full_rescan,
            watermark_updated=watermark_updated,
            watermark=since_ids.get(original_post_id),
        )

    state["hot_post_reply_since_ids"] = since_ids
    state["hot_post_reply_check_counts"] = check_counts
    state["hot_post_reply_pagination_tokens"] = pagination_tokens

    if state_changed:
        save_state(state)

    log.info("Hot-post reply check returning %d candidate(s)", len(candidates))
    return candidates

def mark_hot_post_reply_skipped(
    state: dict,
    reply_id: str,
    *,
    reason: str = "unspecified",
    original_post_id: str | None = None,
    retryable: bool | None = None,
) -> None:
    """Mark hot post reply skipped."""
    reply_id = str(reply_id)
    if not reply_id:
        return

    state["skipped_hot_reply_ids"] = append_unique_capped(
        state.get("skipped_hot_reply_ids", []),
        reply_id,
        2000,
    )

    if retryable is None:
        retryable = False

    records = state.get("skipped_hot_reply_records", {})
    if not isinstance(records, dict):
        records = {}

    records[reply_id] = {
        "reason": reason,
        "retryable": bool(retryable),
        "skipped_epoch": now_epoch(),
    }
    if original_post_id:
        records[reply_id]["original_post_id"] = str(original_post_id)

    # Keep the record map bounded without disturbing the older list field that
    # existing digest scripts already understand.
    if len(records) > 2500:
        trimmed_items = sorted(
            records.items(),
            key=lambda item: int(item[1].get("skipped_epoch", 0) or 0),
        )[-2000:]
        records = dict(trimmed_items)

    state["skipped_hot_reply_records"] = records
    log_event(
        "candidate_skipped",
        lane="hot_post",
        id=reply_id,
        reason=reason,
        original_post_id=original_post_id,
        retryable=bool(retryable),
    )


def maybe_mark_hot_post_reply_skipped(state: dict, candidate: dict, reason: str = "unspecified") -> None:
    """
    Mark a candidate as handled for the hot-post lane when appropriate.

    A tweet can arrive from both /mentions and the hot-post recent-search lane.
    When we de-duplicate those, we keep the normal mention object so
    last_seen_mention_id still advances, but annotate it with
    _also_hot_post_reply=True. If that mention is then skipped, this helper
    still records the ID in skipped_hot_reply_ids so the hot-post lane does not
    reconsider it later and ask Grok a second time.
    """
    if candidate.get("_source") == "hot_post_reply" or candidate.get("_also_hot_post_reply"):
        mark_hot_post_reply_skipped(
            state,
            str(candidate.get("id", "")),
            reason=reason,
            original_post_id=str(candidate.get("_hot_original_post_id", "") or "") or None,
        )


def dedupe_reply_candidates(mentions: list[dict], hot_post_replies: list[dict]) -> list[dict]:
    """
    Merge normal mention candidates and hot-post reply candidates by tweet ID.

    Normal mentions win when the same tweet is present in both sources. That
    preserves last_seen_mention_id handling and prevents the same tweet being
    sent to Grok twice in one check. The retained mention is annotated as also
    belonging to the hot-post lane, so any skip decision is remembered for the
    hot-post search path too.
    """
    mention_by_id: dict[str, dict] = {}
    combined: list[dict] = []
    duplicate_mentions = 0
    duplicate_hot_post_replies = 0

    for mention in mentions:
        mention_id = str(mention.get("id", ""))
        if not mention_id:
            log.info("Dropping mention candidate with no id: %s", mention)
            continue

        if mention_id in mention_by_id:
            duplicate_mentions += 1
            log.info("Dropping duplicate mention candidate id=%s", mention_id)
            continue

        mention_by_id[mention_id] = mention
        combined.append(mention)

    seen_ids = set(mention_by_id)

    for hot_post_reply in hot_post_replies:
        reply_id = str(hot_post_reply.get("id", ""))
        if not reply_id:
            log.info("Dropping hot-post reply candidate with no id: %s", hot_post_reply)
            continue

        if reply_id in mention_by_id:
            duplicate_hot_post_replies += 1
            retained_mention = mention_by_id[reply_id]
            retained_mention["_also_hot_post_reply"] = True

            hot_original_post_id = hot_post_reply.get("_hot_original_post_id")
            if hot_original_post_id:
                retained_mention["_hot_original_post_id"] = str(hot_original_post_id)

            log.info(
                "Dropping duplicate hot-post reply candidate %s: already present as a normal mention; "
                "retained mention candidate marked as also_hot_post_reply",
                reply_id,
            )
            continue

        if reply_id in seen_ids:
            duplicate_hot_post_replies += 1
            log.info("Dropping duplicate hot-post reply candidate id=%s", reply_id)
            continue

        seen_ids.add(reply_id)
        combined.append(hot_post_reply)

    original_count = len(mentions) + len(hot_post_replies)
    if duplicate_mentions or duplicate_hot_post_replies or len(combined) != original_count:
        log.info(
            "Reply candidate de-duplication: mentions=%d hot_post_replies=%d combined=%d "
            "duplicate_mentions=%d duplicate_hot_post_replies=%d",
            len(mentions),
            len(hot_post_replies),
            len(combined),
            duplicate_mentions,
            duplicate_hot_post_replies,
        )

    return combined


# ---------------------------------------------------------------------
# Media / posting
# ---------------------------------------------------------------------

def upload_media_v2(image_path: str) -> str:
    """Return the upload media v2."""
    log.info("Uploading media via X API v2: %s", image_path)

    mime_type, _ = mimetypes.guess_type(image_path)
    if not mime_type:
        mime_type = "image/jpeg"

    log.debug("Detected MIME type for %s: %s", image_path, mime_type)

    with open(image_path, "rb") as f:
        files = {
            "media": (os.path.basename(image_path), f, mime_type),
        }
        data = {
            "media_category": "tweet_image",
            "media_type": mime_type,
        }

        result = x_request(
            "POST",
            "/2/media/upload",
            files=files,
            data=data,
        )

    media_id = str(result["data"]["id"])
    log.info("Uploaded media via v2. media_id=%s", media_id)
    return media_id


def upload_media_v1_1(image_path: str) -> str:
    """Return the upload media v1 1."""
    log.info("Uploading media via legacy v1.1 fallback: %s", image_path)

    url = f"{X_UPLOAD_BASE}/1.1/media/upload.json"

    with open(image_path, "rb") as f:
        files = {
            "media": f,
        }
        data = {
            "media_category": "tweet_image",
        }

        require_remote_operation_unpaused("X v1.1 media upload")
        try:
            response = requests.post(
                url,
                auth=AUTH,
                files=files,
                data=data,
                timeout=request_timeout(),
            )
        except requests.RequestException as e:
            log.exception("v1.1 media upload failed before receiving response")
            raise ApiError(str(e), service="x") from e

    log.debug("v1.1 media upload response status: %s", response.status_code)

    if response.status_code >= 400:
        log.error("Media upload error %s: %s", response.status_code, response.text)
        reset_epoch = print_rate_limit_headers(response)

        raise ApiError(
            f"Media upload error {response.status_code}: {response.text}",
            service="x",
            status_code=response.status_code,
            reset_epoch=reset_epoch,
        )

    try:
        media_id = str(response.json()["media_id_string"])
    except Exception as e:
        log.exception("Could not parse media upload response")
        raise ApiError(f"Could not parse media upload response: {response.text[:500]}", service="x") from e

    log.info("Uploaded media via v1.1. media_id=%s", media_id)
    return media_id


def upload_media(image_path: str) -> str:
    """Upload media through the preferred endpoint with a safe fallback."""
    require_remote_operation_unpaused("X media upload")
    block_if_ambiguous_remote_post()
    try:
        return upload_media_v2(image_path)
    except RemoteOperationsPaused:
        raise
    except ApiError as exc:
        if getattr(exc, "status_code", None) == 429:
            log.exception("v2 media upload was rate limited; not retrying v1.1 fallback")
            raise
        log.exception("v2 media upload failed; trying v1.1 fallback")
        return upload_media_v1_1(image_path)
    except Exception:
        log.exception("v2 media upload failed; trying v1.1 fallback")
        return upload_media_v1_1(image_path)


def unresolved_conversational_reply_receipt_is_blocking() -> bool:
    """Return whether a reply receipt forbids another remote write."""
    status, _receipt = load_confirmed_reply_receipt()
    return status in {"sending", "invalid"}


def block_if_ambiguous_remote_post(
    *,
    prepared_conversational_reply_receipt: dict | None = None,
) -> None:
    """Refuse posting while a remote-write safety incident is unresolved."""
    if _AMBIGUOUS_REMOTE_POST_SEEN:
        try:
            marker_exists = AMBIGUOUS_POST_OUTCOME_FILE.exists()
        except Exception:
            marker_exists = False
        if marker_exists:
            raise AmbiguousRemotePostOutcome(
                "Unreconciled ambiguous/confirmed-persistence remote-write safety "
                f"barrier blocks further posting: {AMBIGUOUS_POST_OUTCOME_FILE}",
                service="x",
            )
        raise AmbiguousRemotePostOutcome(
            "Unreconciled in-process remote-write safety latch blocks further posting",
            service="x",
        )
    try:
        marker_exists = AMBIGUOUS_POST_OUTCOME_FILE.exists()
    except Exception as exc:
        raise AmbiguousRemotePostOutcome(
            "The remote-write safety marker cannot be inspected; failing closed",
            service="x",
        ) from exc
    if marker_exists:
        raise AmbiguousRemotePostOutcome(
            "Unreconciled ambiguous/confirmed-persistence remote-write safety barrier "
            f"blocks further posting: {AMBIGUOUS_POST_OUTCOME_FILE}",
            service="x",
        )
    status, receipt = load_confirmed_reply_receipt()
    if status not in {"sending", "invalid"}:
        return
    if (
        status == "sending"
        and prepared_conversational_reply_receipt is not None
        and receipt == prepared_conversational_reply_receipt
    ):
        return
    raise AmbiguousRemotePostOutcome(
        "An unresolved conversational-reply sending or invalid receipt blocks "
        f"further posting: {CONFIRMED_REPLY_RECEIPT_FILE}",
        service="x",
    )


def ambiguous_remote_post_is_blocking() -> bool:
    """Return the global write barrier state without starting any remote work."""
    if _AMBIGUOUS_REMOTE_POST_SEEN:
        return True
    try:
        if AMBIGUOUS_POST_OUTCOME_FILE.exists():
            return True
    except Exception:
        log.critical(
            "The remote-write safety marker cannot be inspected; treating all remote "
            "writes as blocked",
            exc_info=True,
        )
        return True
    try:
        return unresolved_conversational_reply_receipt_is_blocking()
    except Exception:
        log.critical(
            "The conversational-reply receipt cannot be inspected; treating all "
            "remote writes as blocked",
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


def durable_remote_write_safety_marker_exists() -> bool:
    """Return whether restart safety survives loss of the in-process latch."""
    try:
        return AMBIGUOUS_POST_OUTCOME_FILE.exists()
    except Exception:
        log.critical(
            "The remote-write safety marker cannot be inspected; its durability "
            "cannot be relied upon",
            exc_info=True,
        )
        return False


def durable_remote_write_safety_barrier_exists() -> bool:
    """Return whether restart safety survives loss of the process latch."""
    if durable_remote_write_safety_marker_exists():
        return True
    try:
        status, _receipt = load_confirmed_reply_receipt()
    except Exception:
        return CONFIRMED_REPLY_RECEIPT_FILE.exists()
    return status in {"sending", "invalid"}


def retain_sigint_deferral_without_durable_barrier(*, lane: str) -> None:
    """Explain why controlled shutdown must remain deferred after total loss."""
    log.critical(
        "Keeping SIGINT deferred for lane=%s because the confirmed/ambiguous "
        "remote write has no durable local barrier. The process must remain "
        "alive and idle until manual reconciliation; do not restart it.",
        lane,
    )


def record_ambiguous_remote_post(payload: dict) -> None:
    """Persist a manual-reconciliation barrier without claiming success or failure."""
    global _AMBIGUOUS_REMOTE_POST_SEEN
    _AMBIGUOUS_REMOTE_POST_SEEN = True
    try:
        if AMBIGUOUS_POST_OUTCOME_FILE.exists():
            return
        text = str(payload.get("text") or "")
        atomic_write_json(
            AMBIGUOUS_POST_OUTCOME_FILE,
            {
                "schema_version": 1,
                "recorded_at_epoch": now_epoch(),
                "outcome": "ambiguous_remote_post",
                "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "reply_to_id": str((payload.get("reply") or {}).get("in_reply_to_tweet_id") or ""),
                "media_ids": list((payload.get("media") or {}).get("media_ids") or []),
                "made_with_ai": bool(payload.get("made_with_ai")),
            },
            durable=True,
        )
    except Exception:
        log.critical(
            "AMBIGUOUS REMOTE X POST OUTCOME: the durable safety marker could not be "
            "written. The process-local latch remains active; do not restart this "
            "process before manual reconciliation.",
            exc_info=True,
        )
        return
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
    global _AMBIGUOUS_REMOTE_POST_SEEN
    _AMBIGUOUS_REMOTE_POST_SEEN = True
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
        if not AMBIGUOUS_POST_OUTCOME_FILE.exists():
            atomic_write_json(AMBIGUOUS_POST_OUTCOME_FILE, marker, durable=True)
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
    log.critical(
        "CONFIRMED REMOTE POST LOST COMPLETE LOCAL RECOVERY: post_id=%s lane=%s "
        "failures=%s. All remote writes are blocked pending manual reconciliation: %s",
        post_id,
        lane,
        failures,
        AMBIGUOUS_POST_OUTCOME_FILE,
    )
    return True


def create_post(
    text: str,
    media_ids: list[str] | None = None,
    reply_to_id: str | None = None,
    made_with_ai: bool = False,
    *,
    prepared_conversational_reply_receipt: dict | None = None,
) -> dict:
    """Create an X post with transactional ambiguity handling."""
    require_remote_operation_unpaused("X post creation")
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
    block_if_ambiguous_remote_post(
        prepared_conversational_reply_receipt=(
            prepared_conversational_reply_receipt
        ),
    )
    log.info(
        "Creating X post. reply_to_id=%s media_count=%d made_with_ai=%s text=%r",
        reply_to_id,
        len(media_ids or []),
        made_with_ai,
        text,
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

    def validate_created_post_response(result: dict) -> dict:
        response_data = result.get("data") if isinstance(result, dict) else None
        post_id = response_data.get("id") if isinstance(response_data, dict) else None
        if not valid_post_id(post_id):
            raise AmbiguousRemotePostOutcome(
                f"X may have accepted the post but its response did not include a valid numeric data.id: {result}",
                service="x",
            )
        return result

    def made_with_ai_field_rejected(error: ApiError) -> bool:
        status_code = getattr(error, "status_code", None)
        if status_code not in {400, 422}:
            return False

        message = str(error).lower()
        return "made_with_ai" in message

    try:
        result = x_request("POST", "/2/tweets", json=payload, ambiguous_write=True)
        validate_created_post_response(result)
        log.info("Created X post successfully. response=%s", result)
        return result
    except AmbiguousRemotePostOutcome:
        record_ambiguous_remote_post(payload)
        raise
    except ApiError as exc:
        if made_with_ai and made_with_ai_field_rejected(exc):
            log.warning("Post failed because made_with_ai field was rejected; retrying without made_with_ai field")
            payload.pop("made_with_ai", None)
            try:
                result = x_request("POST", "/2/tweets", json=payload, ambiguous_write=True)
                validate_created_post_response(result)
            except AmbiguousRemotePostOutcome:
                record_ambiguous_remote_post(payload)
                raise
            log.info("Created X post successfully after removing made_with_ai. response=%s", result)
            return result
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
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        log.warning("%s file missing: %s", label, path)
        return None
    except Exception:
        log.exception("Failed loading %s file: %s", label, path)
        return None
    if not isinstance(data, dict):
        log.warning("%s file is not a JSON object: %s", label, path)
        return None
    return data


def collapse_quote_whitespace(text: str) -> str:
    """Collapse quote whitespace."""
    return re.sub(r"\s+", " ", str(text or "").strip())


def quote_text_hash(text: str) -> str:
    """Return whether quote text hash."""
    return hashlib.sha256(collapse_quote_whitespace(text).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    """Return the file SHA-256."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def deep_merge_dict(base: dict, patch: dict) -> dict:
    """Return the deep merge dict."""
    merged = json.loads(json.dumps(base))
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge_dict(merged[key], value)
        else:
            merged[key] = json.loads(json.dumps(value))
    return merged


def apply_quote_analysis_overrides(raw_analysis: dict, overrides: dict | None) -> dict:
    """Apply quote analysis overrides."""
    if not overrides:
        return raw_analysis

    merged = json.loads(json.dumps(raw_analysis))
    quote_overrides = overrides.get("quote_overrides", {})
    if not isinstance(quote_overrides, dict):
        log.warning("Quote analysis override file has invalid quote_overrides")
        return merged

    items = merged.get("items", {})
    for quote_hash, override in quote_overrides.items():
        if not isinstance(override, dict):
            log.warning("Skipping quote override %s: override is not an object", quote_hash)
            continue

        item = items.get(str(quote_hash))
        if not isinstance(item, dict):
            log.warning("Skipping quote override %s: quote hash does not exist", quote_hash)
            continue

        expected_text = override.get("expected_text")
        if expected_text is not None and expected_text != item.get("text"):
            log.warning("Skipping quote override %s: expected_text does not match current quote text", quote_hash)
            continue

        line_numbers = {int(value) for value in item.get("line_numbers", []) if str(value).isdigit()}
        expected_lines = override.get("expected_line_numbers", [])
        try:
            expected_line_numbers = {int(value) for value in expected_lines}
        except Exception:
            log.warning("Skipping quote override %s: expected_line_numbers is invalid", quote_hash)
            continue
        if not expected_line_numbers.issubset(line_numbers):
            log.warning(
                "Skipping quote override %s: expected lines %s not present in record lines %s",
                quote_hash,
                sorted(expected_line_numbers),
                sorted(line_numbers),
            )
            continue

        patch = override.get("analysis_patch")
        if not isinstance(patch, dict):
            log.warning("Skipping quote override %s: analysis_patch is not an object", quote_hash)
            continue

        analysis = item.get("analysis")
        if not isinstance(analysis, dict):
            log.warning("Skipping quote override %s: raw analysis is not an object", quote_hash)
            continue
        item["analysis"] = deep_merge_dict(analysis, patch)
        log.info("Applied quote analysis override for hash=%s reason=%s", quote_hash, override.get("reason"))

    return merged


def load_quote_analysis() -> dict | None:
    """Load validated quotation-analysis metadata and local overrides."""
    raw = load_json_object(QUOTE_ANALYSIS_FILE, label="quote analysis")
    if raw is None:
        return None
    if raw.get("analysis_kind") != "quotes":
        log.error("Quote analysis file has unsupported analysis_kind=%r", raw.get("analysis_kind"))
        return None
    if raw.get("schema_version") != 2:
        log.error("Quote analysis file has unsupported schema_version=%r", raw.get("schema_version"))
        return None
    if not isinstance(raw.get("items"), dict):
        log.error("Quote analysis file has invalid or missing items object: %s", QUOTE_ANALYSIS_FILE)
        return None
    overrides = load_json_object(QUOTE_ANALYSIS_OVERRIDES_FILE, label="quote analysis override")
    return apply_quote_analysis_overrides(raw, overrides)


def load_image_analysis_file(path: Path, *, label: str) -> dict | None:
    """Load image analysis file."""
    raw = load_json_object(path, label=label)
    if raw is None:
        return None
    if raw.get("analysis_kind") != "images":
        log.error("%s file has unsupported analysis_kind=%r", label, raw.get("analysis_kind"))
        return None
    if raw.get("schema_version") != 3:
        log.error("%s file has unsupported schema_version=%r", label, raw.get("schema_version"))
        return None
    if not isinstance(raw.get("items"), dict) or not isinstance(raw.get("path_index"), dict):
        log.error("%s file has invalid required structure: %s", label, path)
        return None
    return raw


def merge_image_analysis(primary: dict, generated: dict | None) -> dict:
    """Merge image analysis."""
    if not isinstance(generated, dict):
        return primary

    merged = dict(primary)
    merged_path_index = dict(primary.get("path_index") or {})
    merged_items = dict(primary.get("items") or {})
    primary_paths = set(merged_path_index)

    for basename, image_hash in sorted((generated.get("path_index") or {}).items()):
        basename = str(basename)
        image_hash = str(image_hash)
        if basename in primary_paths:
            log.warning("Skipping generated image metadata with basename collision: %s", basename)
            continue
        item = (generated.get("items") or {}).get(image_hash)
        if not isinstance(item, dict):
            log.warning("Skipping generated image metadata with missing item hash=%s basename=%s", image_hash, basename)
            continue
        merged_path_index[basename] = image_hash
        merged_items.setdefault(image_hash, item)

    merged["path_index"] = merged_path_index
    merged["items"] = merged_items
    return merged


def load_image_analysis() -> dict | None:
    """Load original and, when enabled, generated image metadata."""
    primary = load_image_analysis_file(IMAGE_ANALYSIS_FILE, label="image analysis")
    if primary is None or not ENABLE_GENERATED_IMAGE_POOL:
        return primary

    generated_path = Path(str(GENERATED_IMAGE_ANALYSIS_FILE)).expanduser()
    generated = load_image_analysis_file(generated_path, label="generated image analysis")
    if generated is None:
        log.warning("Generated image pool enabled but generated image analysis is unavailable; using original image pool only")
        return primary

    return merge_image_analysis(primary, generated)


def mm_dd_in_window(mm_dd: str, start_mm_dd: str, end_mm_dd: str) -> bool:
    """Return the mm dd in window."""
    if start_mm_dd <= end_mm_dd:
        return start_mm_dd <= mm_dd <= end_mm_dd
    return mm_dd >= start_mm_dd or mm_dd <= end_mm_dd


def any_window_matches_today(windows: object, today_mm_dd: str) -> bool:
    """Return whether any window matches today."""
    if not isinstance(windows, list):
        return False
    for window in windows:
        if not isinstance(window, dict):
            continue
        start = str(window.get("start_mm_dd", "") or "")
        end = str(window.get("end_mm_dd", "") or "")
        if re.fullmatch(r"\d{2}-\d{2}", start) and re.fullmatch(r"\d{2}-\d{2}", end):
            if mm_dd_in_window(today_mm_dd, start, end):
                return True
    return False


def quote_season_status(analysis: dict | None, *, today_mm_dd: str) -> dict:
    """Return the quote season status."""
    seasonality = (analysis or {}).get("seasonality", {}) if isinstance(analysis, dict) else {}
    if not isinstance(seasonality, dict):
        seasonality = {}
    windows = seasonality.get("preferred_windows", [])
    in_window = any_window_matches_today(windows, today_mm_dd)
    hard_excluded = bool(seasonality.get("hard_exclude_outside_windows")) and bool(windows) and not in_window
    relevance = str(seasonality.get("relevance", "none") or "none")
    return {
        "in_window": in_window,
        "hard_excluded": hard_excluded,
        "relevance": relevance,
    }


def quote_candidate_weight(analysis: dict | None, *, today_mm_dd: str) -> tuple[float, dict]:
    """Return the quote candidate weight."""
    status = quote_season_status(analysis, today_mm_dd=today_mm_dd)
    if status["hard_excluded"]:
        return 0.0, status

    weight = 1.0
    if status["in_window"]:
        if status["relevance"] == "date_specific":
            weight *= QUOTE_SEASON_DATE_SPECIFIC_WEIGHT
        elif status["relevance"] == "strong":
            weight *= QUOTE_SEASON_STRONG_WEIGHT
        elif status["relevance"] == "soft":
            weight *= QUOTE_SEASON_SOFT_WEIGHT

    scores = (analysis or {}).get("scores", {}) if isinstance(analysis, dict) else {}
    if isinstance(scores, dict):
        values = []
        for key in ("general_post_suitability", "standalone_clarity", "visual_matchability"):
            try:
                values.append(max(0.0, min(100.0, float(scores.get(key, 50)))))
            except Exception:
                pass
        if values:
            quality = sum(values) / len(values)
            weight *= 1.0 + ((quality - 50.0) / 50.0) * (QUOTE_QUALITY_WEIGHT_MAX_MULTIPLIER - 1.0)
            weight = max(0.2, weight)

    return weight, status


def weighted_random_choice(candidates: list[dict]) -> dict:
    """Select one candidate using its configured weight."""
    total = sum(float(candidate.get("weight", 0.0)) for candidate in candidates)
    if total <= 0:
        return random.choice(candidates)
    target = random.uniform(0.0, total)
    running = 0.0
    for candidate in candidates:
        running += float(candidate.get("weight", 0.0))
        if running >= target:
            return candidate
    return candidates[-1]


def quote_metadata_for_hash(quote_analysis: dict | None, quote_hash: str, text: str = "") -> dict | None:
    """Return whether quote metadata for hash."""
    if not isinstance(quote_analysis, dict):
        return None
    item = (quote_analysis.get("items") or {}).get(str(quote_hash), {})
    if not isinstance(item, dict):
        log.warning("Quote metadata missing for current quote hash=%s text=%r", quote_hash, collapse_quote_whitespace(text)[:120])
        return None
    analysed_text = item.get("text")
    if analysed_text is not None and quote_text_hash(str(analysed_text)) != quote_hash:
        log.warning("Quote metadata stale for hash=%s: analysed text does not match hash", quote_hash)
        return None
    analysis = item.get("analysis")
    if not isinstance(analysis, dict):
        log.warning("Quote metadata missing analysis object for hash=%s", quote_hash)
        return None
    return analysis


def current_quote_hashes_by_line(lines: list[str]) -> dict[int, str]:
    """Return whether current quote hashes by line."""
    result: dict[int, str] = {}
    for line_no, line in enumerate(lines):
        if line.rstrip():
            result[line_no] = quote_text_hash(line)
    return result


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
    source = quote_analysis.get("source", {}) if isinstance(quote_analysis.get("source"), dict) else {}
    expected_source_sha = source.get("source_sha256")
    if expected_source_sha:
        current_source_sha = hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()
        if str(expected_source_sha) != current_source_sha:
            log.warning(
                "Quote source SHA differs from analysed source: current=%s analysed=%s; per-quote hashes will be used",
                current_source_sha,
                expected_source_sha,
            )


def current_image_paths() -> list[str]:
    """Return the current image paths."""
    images = glob(IMAGE_GLOB)
    images.sort()
    result = [path for path in images if Path(path).is_file()]
    if not ENABLE_GENERATED_IMAGE_POOL:
        return result

    generated_dir = Path(str(GENERATED_IMAGE_DIR)).expanduser()
    generated_glob = str(generated_dir / str(GENERATED_IMAGE_GLOB))
    try:
        configured_generated = configured_generated_image_paths()
    except ValueError as exc:
        raise RuntimeError(
            "Generated image pool contains an unsafe or unclassifiable file; "
            "refusing to select from the pool"
        ) from exc
    generated_images = [str(path) for path in configured_generated.values()]
    if not generated_images:
        log.warning("Generated image pool enabled but no generated images found matching %s", generated_glob)
        return result

    seen_basenames = {Path(path).name for path in result}
    for path in generated_images:
        basename = Path(path).name
        if basename in seen_basenames:
            log.warning("Skipping generated image with basename collision: %s path=%s", basename, path)
            continue
        result.append(path)
        seen_basenames.add(basename)
    return result


def save_image_used_basenames(path: Path, value: set[str], *, durable: bool = False) -> None:
    """Save image used basenames."""
    path.parent.mkdir(parents=True, exist_ok=True)
    serializable = sorted(str(item) for item in value)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=2)
        f.write("\n")
        if durable:
            f.flush()
            os.fsync(f.fileno())
    os.replace(tmp, path)
    if durable:
        fsync_parent_dir(path, strict=durable)


def fsync_parent_dir(path: Path, *, strict: bool = False) -> None:
    """Synchronise parent dir."""
    try:
        fd = os.open(str(path.parent), os.O_RDONLY)
    except Exception:
        if strict:
            raise
        log.debug("Could not open parent directory for fsync: %s", path.parent, exc_info=True)
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_json(path: Path, value: object, *, durable: bool = False) -> None:
    """Write JSON atomically and optionally durably."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(value, f, indent=2, sort_keys=True)
        f.write("\n")
        if durable:
            f.flush()
            os.fsync(f.fileno())
    os.replace(tmp, path)
    if durable:
        fsync_parent_dir(path, strict=durable)


def valid_post_id(value: object) -> bool:
    """Return whether valid post ID."""
    return bool(re.fullmatch(r"\d{1,30}", str(value or "")))


def valid_receipt_epoch(value: object) -> bool:
    """Return whether valid receipt epoch."""
    if type(value) is not int:
        return False
    epoch = value
    return 1_500_000_000 <= epoch <= 4_102_444_800


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


def valid_receipt_basename(value: object) -> bool:
    """Return whether valid receipt basename."""
    basename = str(value or "")
    return bool(basename) and Path(basename).name == basename and basename not in {".", ".."}


def write_regular_post_receipt(receipt: dict) -> None:
    """Write regular post receipt."""
    if REGULAR_POST_RECEIPT_FILE.exists():
        raise UnresolvedRegularPostReceipt(f"Refusing to overwrite unresolved regular-post receipt: {REGULAR_POST_RECEIPT_FILE}")
    if MEME_POST_RECEIPT_FILE.exists():
        raise UnresolvedMemePostReceipt(f"Refusing regular post while unresolved meme-post receipt exists: {MEME_POST_RECEIPT_FILE}")
    if not regular_post_receipt_is_semantically_valid(receipt):
        raise RuntimeError("Internal error: generated regular-post receipt failed semantic validation")
    atomic_write_json(REGULAR_POST_RECEIPT_FILE, receipt, durable=True)
    log.warning("Wrote confirmed regular-post receipt pending local reconciliation: %s", REGULAR_POST_RECEIPT_FILE)


def regular_post_receipt_is_semantically_valid(data: dict) -> bool:
    """Return whether a regular-post receipt is internally consistent."""
    schema_version = data.get("schema_version")
    if type(schema_version) is not int or schema_version not in {1, 2}:
        return False
    post_id = str(data.get("post_id") or "")
    quote_hash = str(data.get("quote_hash") or "")
    image_basename = str(data.get("image_basename") or "")
    text = data.get("text")
    quote_post_epoch = receipt_int(data.get("quote_post_epoch"))
    next_quote_post_epoch = receipt_int(data.get("next_quote_post_epoch"))

    if not valid_post_id(post_id):
        return False
    if not re.fullmatch(r"[0-9a-f]{64}", quote_hash):
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
    if quote_text_hash(text) != quote_hash:
        return False
    if schema_version == 2:
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
    if next_meme_epoch:
        if not valid_receipt_epoch(next_meme_epoch):
            return False
        mode = str(data.get("next_meme_schedule_mode") or "")
        if mode not in MEME_SCHEDULE_MODES or not mode:
            return False
        anchor_int = receipt_int(data.get("meme_anchor_quote_post_epoch", 0), default=0)
        if anchor_int is None:
            return False
        changed_by_quote = receipt_bool(data.get("meme_schedule_changed_by_quote"))
        if changed_by_quote is None:
            return False
        schedule_date = str(data.get("next_meme_schedule_date") or "")
        if mode == "after_first_quote_after_midday":
            if changed_by_quote:
                if anchor_int != quote_post_epoch:
                    return False
                if next_meme_epoch <= quote_post_epoch:
                    return False
                if schedule_date != safe_epoch_date_str(quote_post_epoch):
                    return False
            else:
                if anchor_int <= 0:
                    return False
                if schedule_date != safe_epoch_date_str(anchor_int):
                    return False
                if next_meme_epoch <= anchor_int:
                    return False
        else:
            if changed_by_quote:
                return False
            if anchor_int:
                return False
            if schedule_date != safe_epoch_date_str(next_meme_epoch):
                return False
    elif data.get("meme_schedule_changed_by_quote") not in (None, False):
        return False
    return True


def load_regular_post_receipt() -> tuple[str, dict | None]:
    """Load regular post receipt."""
    try:
        with open(REGULAR_POST_RECEIPT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return "absent", None
    except Exception:
        log.exception("Malformed regular-post receipt blocks main posting until repaired: %s", REGULAR_POST_RECEIPT_FILE)
        return "invalid", None
    if (
        not isinstance(data, dict)
        or type(data.get("schema_version")) is not int
        or data.get("schema_version") not in {1, 2}
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


def remove_regular_post_receipt() -> None:
    """Remove regular post receipt."""
    try:
        REGULAR_POST_RECEIPT_FILE.unlink()
        log.info("Removed reconciled regular-post receipt: %s", REGULAR_POST_RECEIPT_FILE)
        fsync_parent_dir(REGULAR_POST_RECEIPT_FILE, strict=True)
    except FileNotFoundError:
        pass


def write_meme_post_receipt(receipt: dict) -> None:
    """Write meme post receipt."""
    if MEME_POST_RECEIPT_FILE.exists():
        raise UnresolvedMemePostReceipt(f"Refusing to overwrite unresolved meme-post receipt: {MEME_POST_RECEIPT_FILE}")
    if REGULAR_POST_RECEIPT_FILE.exists():
        raise UnresolvedRegularPostReceipt(f"Refusing meme post while unresolved regular-post receipt exists: {REGULAR_POST_RECEIPT_FILE}")
    if not meme_post_receipt_is_semantically_valid(receipt):
        raise RuntimeError("Internal error: generated meme-post receipt failed semantic validation")
    atomic_write_json(MEME_POST_RECEIPT_FILE, receipt, durable=True)
    log.warning("Wrote confirmed meme-post receipt pending local reconciliation: %s", MEME_POST_RECEIPT_FILE)


def meme_post_receipt_is_semantically_valid(data: dict) -> bool:
    """Return whether a meme-post receipt is internally consistent."""
    if type(data.get("schema_version")) is not int or data.get("schema_version") != 1:
        return False
    if not valid_post_id(data.get("post_id")):
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
    mode = str(data.get("next_meme_schedule_mode") or "fallback")
    if mode not in MEME_SCHEDULE_MODES or mode == "after_first_quote_after_midday":
        return False
    return True


def load_meme_post_receipt() -> tuple[str, dict | None]:
    """Load meme post receipt."""
    try:
        with open(MEME_POST_RECEIPT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return "absent", None
    except Exception:
        log.exception("Malformed meme-post receipt blocks the bot until repaired: %s", MEME_POST_RECEIPT_FILE)
        return "invalid", None
    if not isinstance(data, dict) or data.get("schema_version") != 1:
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


def remove_meme_post_receipt() -> None:
    """Remove meme post receipt."""
    try:
        MEME_POST_RECEIPT_FILE.unlink()
        log.info("Removed reconciled meme-post receipt: %s", MEME_POST_RECEIPT_FILE)
        fsync_parent_dir(MEME_POST_RECEIPT_FILE, strict=True)
    except FileNotFoundError:
        pass


def apply_meme_post_receipt(receipt: dict, state: dict) -> None:
    """Apply meme post receipt."""
    post_id = str(receipt["post_id"])
    meme_basename = str(receipt["meme_basename"])
    meme_post_epoch = int(receipt["meme_post_epoch"])
    next_meme_post_epoch = int(receipt["next_meme_post_epoch"])
    text = str(receipt.get("text") or MEME_POST_TEXT)
    image_summary = str(receipt.get("image_summary") or "")

    state["last_main_post_id"] = post_id
    state["last_meme_post_epoch"] = meme_post_epoch
    posted = set(str(x) for x in state.get("posted_meme_filenames", []))
    posted.add(meme_basename)
    state["posted_meme_filenames"] = sorted(posted)
    state["next_meme_post_epoch"] = next_meme_post_epoch
    state["meme_schedule_version"] = MEME_SCHEDULE_VERSION
    state["next_meme_schedule_mode"] = str(receipt.get("next_meme_schedule_mode") or "fallback")
    state["next_meme_schedule_date"] = epoch_date_str(next_meme_post_epoch)
    state["meme_anchor_quote_post_epoch"] = 0
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
    if status == "invalid" or receipt is None:
        raise InvalidMemePostReceipt(f"Invalid meme-post receipt blocks the bot: {MEME_POST_RECEIPT_FILE}")
    log.warning(
        "Reconciling confirmed meme post receipt post_id=%s meme=%s",
        receipt.get("post_id"),
        receipt.get("meme_basename"),
    )
    apply_meme_post_receipt(receipt, state)
    save_state(state, durable=True)
    remove_meme_post_receipt()
    return True


def apply_regular_post_receipt(receipt: dict, lines_used: set, images_used: set, state: dict) -> None:
    """Apply regular post receipt."""
    post_id = str(receipt["post_id"])
    quote_hash = str(receipt["quote_hash"])
    image_basename = str(receipt["image_basename"])
    quote_post_epoch = int(receipt["quote_post_epoch"])
    next_quote_post_epoch = int(receipt["next_quote_post_epoch"])
    text = str(receipt.get("text") or "")

    if receipt.get("schema_version") == 2:
        lines_used.clear()
        lines_used.update(str(value) for value in receipt["quote_history_after"])
        images_used.clear()
        images_used.update(str(value) for value in receipt["image_history_after"])
    else:
        # Schema v1 did not preserve cycle-boundary resets.  Retain its
        # historical additive interpretation for backward compatibility.
        lines_used.add(quote_hash)
        images_used.add(image_basename)
    last_quote_epoch = int(state.get("last_quote_post_epoch", 0) or 0)
    last_meme_epoch = int(state.get("last_meme_post_epoch", 0) or 0)
    receipt_is_newest_main = quote_post_epoch >= max(last_quote_epoch, last_meme_epoch)
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
        if receipt.get("next_meme_post_epoch"):
            state["next_meme_post_epoch"] = int(receipt["next_meme_post_epoch"])
            state["meme_schedule_version"] = MEME_SCHEDULE_VERSION
            state["next_meme_schedule_mode"] = str(receipt.get("next_meme_schedule_mode") or state.get("next_meme_schedule_mode") or "")
            state["next_meme_schedule_date"] = str(receipt.get("next_meme_schedule_date") or epoch_date_str(int(receipt["next_meme_post_epoch"])))
            state["meme_anchor_quote_post_epoch"] = int(receipt.get("meme_anchor_quote_post_epoch") or 0)
        else:
            maybe_schedule_meme_after_quote_post(state, quote_post_epoch, save=False)
    if text:
        cache_tweet(
            state,
            tweet_id=post_id,
            text=text,
            author_id=str(MY_USER_ID),
            conversation_id=post_id,
            referenced_tweets=[],
            post_type="quote",
        )
    record_recent_own_post(state, post_id)


def save_regular_post_protected_state(lines_used: set, images_used: set, state: dict, *, durable: bool) -> None:
    """Save regular post protected state."""
    save_quote_used_hashes(LINES_USED_FILE, lines_used, durable=durable)
    save_image_used_basenames(IMAGES_USED_FILE, {str(item) for item in images_used}, durable=durable)
    save_state(state, durable=durable)


def json_file_matches(path: Path, expected: object) -> bool:
    """Return whether a JSON file contains exactly the expected value."""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle) == expected
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
) -> bool:
    """Return whether the no-receipt regular-post fallback prevents replay."""
    state_post_epoch = receipt_int(state.get("last_quote_post_epoch"))
    next_post_epoch = receipt_int(state.get("next_quote_post_epoch"))
    return bool(
        valid_post_id(post_id)
        and post_epoch is not None
        and valid_receipt_epoch(post_epoch)
        and str(state.get("last_main_post_id") or "") == str(post_id)
        and quote_hash in lines_used
        and image_basename in images_used
        and state_post_epoch == post_epoch
        and next_post_epoch is not None
        and valid_receipt_epoch(next_post_epoch)
        and next_post_epoch > post_epoch
    )


def confirmed_meme_emergency_representation_is_complete(
    *,
    post_id: str,
    post_epoch: int | None,
    meme_basename: str,
    state: dict,
) -> bool:
    """Return whether the no-receipt meme fallback prevents replay."""
    state_post_epoch = receipt_int(state.get("last_meme_post_epoch"))
    next_post_epoch = receipt_int(state.get("next_meme_post_epoch"))
    posted = {str(item) for item in state.get("posted_meme_filenames", [])}
    return bool(
        valid_post_id(post_id)
        and post_epoch is not None
        and valid_receipt_epoch(post_epoch)
        and str(state.get("last_main_post_id") or "") == str(post_id)
        and meme_basename in posted
        and state_post_epoch == post_epoch
        and next_post_epoch is not None
        and valid_receipt_epoch(next_post_epoch)
        and next_post_epoch > post_epoch
    )


def maybe_post_historical_context_reply(
    *,
    quote_hash: str,
    quote_text: str,
    parent_post_id: str,
    dry_run: bool = False,
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

        store = HistoricalContextReplyStore(
            HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
            HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        )
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
        result = store.post(
            parent_post_id=str(parent_post_id),
            quote_id=str(packet["quote_id"]),
            reply_text=str(formatted["text"]),
            create_post=create_post,
            now_epoch=now_epoch,
            dry_run=dry_run,
            formatter_metadata=formatter_metadata,
        )
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
            reply_preview=event_text[:160],
            reason="post_failed" if result.get("status") == "failed" else "",
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


def historical_context_outbox_store():
    """Return the durable store for auxiliary context-reply obligations."""
    from historical_context_outbox import HistoricalContextOutbox

    return HistoricalContextOutbox(HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE)


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
        quote_text = str(receipt.get("text") or "")
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
) -> str:
    """Record one bounded context failure and return its durable state."""
    if force_terminal or attempt_number >= store.max_attempts:
        obligation = store.record_terminal_failure(
            parent_post_id,
            attempt_number=attempt_number,
            error=error,
            failed_epoch=failed_epoch,
        )
    else:
        obligation = store.record_retryable_failure(
            parent_post_id,
            attempt_number=attempt_number,
            error=error,
            failed_epoch=failed_epoch,
        )
    return str(obligation["context_reply"]["state"])


def recover_interrupted_historical_context_attempt(
    store,
    obligation: dict,
    *,
    recovered_epoch: int,
) -> dict:
    """Resolve a durable interrupted claim without repeating its remote work."""
    from historical_context_formatter import HistoricalContextReplyStore

    parent_id = str(obligation["parent_post_id"])
    context = obligation["context_reply"]
    if context.get("state") != "context_reply_attempting":
        raise ValueError("only an interrupted attempting state can be recovered")
    attempt_number = int(context["attempt_count"])
    context_store = HistoricalContextReplyStore(
        HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
    )
    # The sending receipt is the authoritative ambiguity barrier.  It must be
    # reconciled (confirmed, definitely failed, or left ambiguous) before an
    # interrupted outbox claim can be downgraded or retried.
    context_store.reconcile_receipt()
    history = context_store.history()
    previous = history["items"].get(parent_id)
    if isinstance(previous, dict) and previous.get("quote_id") != context["quote_id"]:
        raise RuntimeError(
            "context history identity conflicts with interrupted outbox attempt"
        )
    if isinstance(previous, dict) and previous.get("status") == "completed":
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
        )
        updated = store.get(parent_id)
        status = "recovered_interrupted_attempt"
        if updated is None or updated["context_reply"]["state"] != state_name:
            raise RuntimeError("interrupted context recovery was not durable")
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
            continue
        try:
            claimed = store.claim_attempt(
                parent_id,
                started_epoch=now_epoch(),
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
            else:
                try:
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
            if status in {"completed", "already_completed"}:
                reply_post_id = str(result.get("reply_post_id") or "")
                updated = store.record_confirmed(
                    parent_id,
                    attempt_number=attempt_number,
                    reply_post_id=reply_post_id,
                    confirmed_epoch=now_epoch(),
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

    if ambiguous_remote_post_is_blocking():
        log.critical(
            "Historical-context auxiliary worker deferred by the unresolved "
            "global remote-write ambiguity barrier"
        )
        return []
    store = historical_context_outbox_store()
    try:
        with store.worker_lock():
            return _process_due_historical_context_obligations(
                store=store,
                parent_post_id=parent_post_id,
                limit=limit,
                runtime_state=runtime_state,
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
) -> bool:
    """Reconcile a durable regular-post receipt without duplicating a remote post."""
    status, receipt = load_regular_post_receipt()
    if status == "absent":
        return False
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
    enqueue_historical_context_obligation(receipt)
    remove_regular_post_receipt()
    log.info(
        "Confirmed main post reconciliation is complete; auxiliary context "
        "obligation is independent. post_id=%s",
        receipt["post_id"],
    )
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
    return REGULAR_POST_RECEIPT_FILE.exists() and MEME_POST_RECEIPT_FILE.exists()


def reconcile_main_post_receipts(
    lines_used: set,
    images_used: set,
    state: dict,
    *,
    minimum_next_quote_epoch: int | None = None,
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


def normalise_tag(value: object) -> str:
    """Normalise tag."""
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def as_string_list(value: object) -> list[str]:
    """Return the as string list."""
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    if value is None:
        return []
    return [str(value)]


def meaningful_tokens(value: object) -> set[str]:
    """Return the meaningful tokens."""
    words = re.findall(r"[a-z0-9]+", str(value or "").lower())
    return {word for word in words if len(word) >= 4 and word not in TOKEN_STOPWORDS}


def phrase_matches_text(phrase: str, text: str) -> bool:
    """Return whether phrase matches text."""
    phrase_tokens = meaningful_tokens(phrase)
    if not phrase_tokens:
        return False
    text_tokens = meaningful_tokens(text)
    if len(phrase_tokens) <= 2:
        required = len(phrase_tokens)
    else:
        required = max(
            1,
            min(len(phrase_tokens), int(round(len(phrase_tokens) * 0.65))),
        )
    return len(phrase_tokens & text_tokens) >= required


def hard_mismatch_tokens(value: object) -> set[str]:
    """Return the hard mismatch tokens."""
    tokens = meaningful_tokens(value)
    normalised: set[str] = set()
    for token in tokens:
        if token.endswith("s") and len(token) > 4:
            normalised.add(token[:-1])
        else:
            normalised.add(token)
    return normalised


def hard_mismatch_phrase_matches_text(phrase: str, text: str) -> bool:
    """Return whether hard mismatch phrase matches text."""
    phrase_tokens = hard_mismatch_tokens(phrase)
    if not phrase_tokens:
        return False
    text_tokens = hard_mismatch_tokens(text)
    if len(phrase_tokens) <= 2:
        required = len(phrase_tokens)
    else:
        required = int((len(phrase_tokens) * 65 + 99) // 100)
    return len(phrase_tokens & text_tokens) >= required


def image_text_corpus(image_analysis: dict) -> str:
    """Return the image text corpus."""
    parts: list[str] = []
    for key in ("description", "scene_summary"):
        if image_analysis.get(key):
            parts.append(str(image_analysis.get(key)))
    for key in ("visible_elements", "themes", "scene_types"):
        parts.extend(as_string_list(image_analysis.get(key)))
    historical = image_analysis.get("historical_context", {})
    if isinstance(historical, dict):
        parts.extend(as_string_list(historical.get("visible_symbols")))
        if historical.get("event_or_context_hint"):
            parts.append(str(historical.get("event_or_context_hint")))
    return " ".join(parts)


def build_image_topic_idf(image_analysis: dict | None) -> dict[str, float]:
    """Build image topic idf."""
    if not isinstance(image_analysis, dict):
        return {}
    docs: list[set[str]] = []
    for item in (image_analysis.get("items") or {}).values():
        analysis = item.get("analysis") if isinstance(item, dict) else None
        if not isinstance(analysis, dict):
            continue
        pairing = analysis.get("pairing", {}) if isinstance(analysis.get("pairing"), dict) else {}
        tags = set()
        for value in as_string_list(pairing.get("best_for_topics")) + as_string_list(analysis.get("themes")):
            tag = normalise_tag(value)
            if tag:
                tags.add(tag)
        docs.append(tags)
    total = max(1, len(docs))
    df: dict[str, int] = {}
    for doc in docs:
        for tag in doc:
            df[tag] = df.get(tag, 0) + 1
    return {tag: 1.0 + (total / (count + 1)) ** 0.5 for tag, count in df.items()}


def visual_energy_score(quote_energy: str, image_energy: str) -> float:
    """Return the visual energy score."""
    order = {"low": 0, "medium": 1, "high": 2}
    if quote_energy not in order or image_energy not in order:
        return 0.0
    distance = abs(order[quote_energy] - order[image_energy])
    if distance == 0:
        return 8.0
    if distance == 1:
        return 2.0
    return -8.0


def image_is_out_of_season(image_analysis: dict, today_mm_dd: str) -> bool:
    """Return whether image is out of season."""
    seasonality = image_analysis.get("seasonality", {})
    if not isinstance(seasonality, dict) or not seasonality.get("avoid_outside_season_or_occasion"):
        return False
    occasions = {normalise_tag(value) for value in as_string_list(seasonality.get("occasions"))}
    visible_season = normalise_tag(seasonality.get("visible_season"))
    if "christmas" in occasions:
        return not mm_dd_in_window(today_mm_dd, "12-10", "12-28")
    if visible_season == "winter":
        return not mm_dd_in_window(today_mm_dd, "12-01", "02-28")
    if visible_season == "spring":
        return not mm_dd_in_window(today_mm_dd, "03-01", "05-31")
    if visible_season == "summer":
        return not mm_dd_in_window(today_mm_dd, "06-01", "08-31")
    if visible_season == "autumn":
        return not mm_dd_in_window(today_mm_dd, "09-01", "11-30")
    return False


def score_image_for_quote(quote_analysis: dict | None, image_analysis: dict | None, idf: dict[str, float] | None = None) -> tuple[float, dict[str, float], bool]:
    """Calculate the production image score and component breakdown for a quotation."""
    if not isinstance(quote_analysis, dict) or not isinstance(image_analysis, dict):
        return 0.0, {"fallback": 0.0}, True

    idf = idf or {}
    components: dict[str, float] = {}
    total = 0.0

    pairing = image_analysis.get("pairing", {}) if isinstance(image_analysis.get("pairing"), dict) else {}
    people = image_analysis.get("people", {}) if isinstance(image_analysis.get("people"), dict) else {}
    historical = image_analysis.get("historical_context", {}) if isinstance(image_analysis.get("historical_context"), dict) else {}
    prefs = quote_analysis.get("archive_image_preferences", {}) if isinstance(quote_analysis.get("archive_image_preferences"), dict) else {}

    image_best_topics = {normalise_tag(value) for value in as_string_list(pairing.get("best_for_topics"))}
    image_themes = {normalise_tag(value) for value in as_string_list(image_analysis.get("themes"))}
    image_weak_topics = {normalise_tag(value) for value in as_string_list(pairing.get("weak_for_topics"))}

    topic_score = 0.0
    for value in as_string_list(quote_analysis.get("primary_topics")):
        tag = normalise_tag(value)
        weight = idf.get(tag, 1.0)
        if tag in image_best_topics:
            topic_score += 5.0 * weight
        if tag in image_themes:
            topic_score += 7.0 * weight
        if tag in image_weak_topics:
            topic_score -= 5.0 * weight
    for value in as_string_list(quote_analysis.get("secondary_topics")):
        tag = normalise_tag(value)
        weight = idf.get(tag, 1.0)
        if tag in image_best_topics:
            topic_score += 2.5 * weight
        if tag in image_themes:
            topic_score += 3.5 * weight
        if tag in image_weak_topics:
            topic_score -= 3.0 * weight
    components["topics"] = topic_score
    total += topic_score

    quote_tones = {normalise_tag(value) for value in as_string_list(quote_analysis.get("tone"))}
    image_tones = {normalise_tag(value) for value in as_string_list(image_analysis.get("tone"))}
    best_tones = {normalise_tag(value) for value in as_string_list(pairing.get("best_for_tones"))}
    image_moods = {normalise_tag(value) for value in as_string_list(people.get("primary_subject_moods"))}
    preferred_moods = {normalise_tag(value) for value in as_string_list(prefs.get("preferred_subject_moods"))}
    tone_score = 3.0 * len(quote_tones & best_tones) + 2.0 * len(quote_tones & image_tones) + 2.0 * len(preferred_moods & image_moods)
    components["tone_mood"] = tone_score
    total += tone_score

    energy_score = visual_energy_score(str(quote_analysis.get("visual_energy", "")), str(image_analysis.get("visual_energy", "")))
    components["visual_energy"] = energy_score
    total += energy_score

    text_corpus = image_text_corpus(image_analysis)
    scene_score = 0.0
    image_scenes = {normalise_tag(value) for value in as_string_list(image_analysis.get("scene_types"))}
    image_activities = {normalise_tag(value) for value in as_string_list(people.get("primary_subject_activities"))}
    image_symbols = {normalise_tag(value) for value in as_string_list(historical.get("visible_symbols")) + as_string_list(image_analysis.get("visible_elements"))}
    for value in as_string_list(prefs.get("preferred_scenes")):
        tag = normalise_tag(value)
        if tag in image_scenes:
            scene_score += 6.0
        elif phrase_matches_text(value, text_corpus):
            scene_score += 2.0
    for value in as_string_list(prefs.get("preferred_activities")):
        if normalise_tag(value) in image_activities or phrase_matches_text(value, text_corpus):
            scene_score += 4.0
    for value in as_string_list(prefs.get("preferred_visible_symbols")) + as_string_list(prefs.get("visual_affinities")):
        if normalise_tag(value) in image_symbols or phrase_matches_text(value, text_corpus):
            scene_score += 4.0
    components["scene_activity_symbols"] = scene_score
    total += scene_score

    historical_score = 0.0
    qhist = quote_analysis.get("historical_context", {}) if isinstance(quote_analysis.get("historical_context"), dict) else {}
    refs = (
        as_string_list(qhist.get("referenced_events"))
        + as_string_list(qhist.get("referenced_people"))
        + as_string_list(qhist.get("referenced_places"))
    )
    matched_ref = any(phrase_matches_text(ref, text_corpus) for ref in refs)
    if matched_ref:
        historical_score += 14.0
    if qhist.get("needs_historical_image_match") and not matched_ref and str(historical.get("specificity", "general")) == "general":
        historical_score -= 4.0
    components["historical"] = historical_score
    total += historical_score

    mismatch_score = 0.0
    for phrase in as_string_list(prefs.get("strong_visual_mismatches")):
        if hard_mismatch_phrase_matches_text(phrase, text_corpus):
            components["strong_mismatch"] = IMAGE_STRONG_MISMATCH_PENALTY
            return IMAGE_STRONG_MISMATCH_PENALTY, components, False
    for phrase in as_string_list(prefs.get("weak_visual_mismatches")):
        if phrase_matches_text(phrase, text_corpus):
            mismatch_score -= 5.0
    components["mismatches"] = mismatch_score
    total += mismatch_score

    quality = image_analysis.get("quality", {}) if isinstance(image_analysis.get("quality"), dict) else {}
    quality_values = []
    for key in ("overall", "crop_suitability_for_x"):
        try:
            quality_values.append(float(quality.get(key, 50)))
        except Exception:
            pass
    for key in ("general_reusability", "semantic_specificity"):
        try:
            quality_values.append(float(pairing.get(key, 50)))
        except Exception:
            pass
    quality_score = ((sum(quality_values) / len(quality_values)) - 50.0) / 50.0 * 4.0 if quality_values else 0.0
    components["quality"] = quality_score
    total += quality_score

    return total, components, True


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


def original_editorial_numeric(value: object, *, key: str) -> float:
    """Return the original editorial numeric."""
    if isinstance(value, bool):
        raise ValueError(f"{key} must be numeric in 0..10, got boolean")
    try:
        number = float(value)
    except Exception as exc:
        raise ValueError(f"{key} must be numeric in 0..10") from exc
    if not math.isfinite(number):
        raise ValueError(f"{key} must be finite")
    if not 0.0 <= number <= 10.0:
        raise ValueError(f"{key} outside 0..10: {number}")
    return number


def original_editorial_concepts(value: object) -> set[str]:
    """Return the original editorial concepts."""
    tag = normalise_tag(value)
    if not tag:
        return set()
    if "_and_" in tag:
        concepts: set[str] = set()
        for part in tag.split("_and_"):
            concepts.update(original_editorial_concepts(part))
        return concepts
    concept = _ORIGINAL_EDITORIAL_SYNONYM_TO_CONCEPT.get(tag)
    if concept in ORIGINAL_EDITORIAL_AFFINITY_CONCEPTS:
        return {concept}
    return set()


def original_editorial_quote_concepts(quote_analysis: dict | None) -> set[str]:
    """Return the original editorial quote concepts."""
    if not isinstance(quote_analysis, dict):
        return set()
    concepts: set[str] = set()
    fields: list[object] = []
    fields.extend(as_string_list(quote_analysis.get("primary_topics")))
    fields.extend(as_string_list(quote_analysis.get("secondary_topics")))
    fields.extend(as_string_list(quote_analysis.get("tone")))
    prefs = quote_analysis.get("archive_image_preferences", {}) if isinstance(quote_analysis.get("archive_image_preferences"), dict) else {}
    for key in ("preferred_subject_moods", "preferred_scenes", "preferred_activities", "preferred_visible_symbols", "visual_affinities"):
        fields.extend(as_string_list(prefs.get(key)))
    hist = quote_analysis.get("historical_context", {}) if isinstance(quote_analysis.get("historical_context"), dict) else {}
    for key in ("referenced_events", "referenced_people", "referenced_places", "specificity"):
        fields.extend(as_string_list(hist.get(key)))
    for value in fields:
        concepts.update(original_editorial_concepts(value))
    return concepts


def original_editorial_image_concepts(editorial: dict | None) -> set[str]:
    """Return the original editorial image concepts."""
    if not isinstance(editorial, dict):
        return set()
    concepts: set[str] = set()
    for key in ("abstract_quote_affinities", "editorial_functions", "best_quote_types"):
        for value in as_string_list(editorial.get(key)):
            concepts.update(original_editorial_concepts(value))
    return concepts


def original_editorial_avoid_concepts(editorial: dict | None) -> set[str]:
    """Return the original editorial avoid concepts."""
    if not isinstance(editorial, dict):
        return set()
    concepts: set[str] = set()
    for value in as_string_list(editorial.get("avoid_quote_types")):
        concepts.update(original_editorial_concepts(value))
    return concepts


def original_editorial_quote_dimension_profile(quote_analysis: dict | None) -> dict[str, float]:
    """Return the original editorial quote dimension profile."""
    if not isinstance(quote_analysis, dict):
        return {dim: 0.0 for dim in ORIGINAL_EDITORIAL_DIMENSIONS}
    concepts = {normalise_tag(value) for value in as_string_list(quote_analysis.get("primary_topics")) + as_string_list(quote_analysis.get("secondary_topics"))}
    controlled = original_editorial_quote_concepts(quote_analysis)
    tone = {normalise_tag(value) for value in as_string_list(quote_analysis.get("tone"))}
    prefs = quote_analysis.get("archive_image_preferences", {}) if isinstance(quote_analysis.get("archive_image_preferences"), dict) else {}
    visual_energy = str(quote_analysis.get("visual_energy") or "").lower()
    hist = quote_analysis.get("historical_context", {}) if isinstance(quote_analysis.get("historical_context"), dict) else {}
    profile = {dim: 0.0 for dim in ORIGINAL_EDITORIAL_DIMENSIONS}

    def add(dim: str, value: float) -> None:
        profile[dim] = min(1.0, max(profile[dim], value))

    if controlled & {"freedom", "duty", "responsibility"}:
        add("conviction", 0.65)
    if controlled & {"warning"} or tone & {"grave", "urgent", "warning"}:
        add("warning", 0.7)
    if controlled & {"defiance"} or tone & {"defiant", "confrontational"}:
        add("defiance", 0.65)
    if controlled & {"patriotism", "national_identity"}:
        add("patriotism", 0.65)
    if controlled & {"economic", "enterprise"}:
        add("economic_seriousness", 0.75)
    if controlled & {"family"} or tone & {"warm", "personal"}:
        add("human_warmth", 0.7)
    if controlled & {"ceremony"}:
        add("ceremony_formality", 0.55)
    if "socialism" in controlled:
        add("conviction", 0.45)
        add("economic_seriousness", 0.45)
    if concepts & {"government", "law_and_order"} or tone & {"authoritative"}:
        add("authority", 0.45)
    if concepts & {"diplomacy", "parliament"}:
        add("statesmanship", 0.5)
    if hist.get("needs_historical_image_match") or str(hist.get("specificity")) in {"specific", "high"}:
        add("historical_iconicity", 0.7)
    if visual_energy == "high":
        add("defiance", 0.35)
    if visual_energy == "low":
        add("statesmanship", 0.25)
    for scene in as_string_list(prefs.get("preferred_scenes")):
        scene_tag = normalise_tag(scene)
        if scene_tag in {"parliament", "office_or_working"}:
            add("statesmanship", 0.45)
        if scene_tag == "formal_portrait":
            add("authority", 0.3)
    return profile


def validate_original_editorial_item(basename: str, entry: dict, image_by_name: dict[str, str]) -> dict:
    """Validate original editorial item."""
    if generated_image_origin_quote_hash(basename):
        raise ValueError(f"generated-style basename is not allowed in original editorial analysis: {basename}")
    if basename not in image_by_name:
        raise ValueError(f"original editorial image is not present in current image corpus: {basename}")
    expected_sha = str(entry.get("sha256") or "")
    if not expected_sha:
        raise ValueError(f"missing sha256 for original editorial image {basename}")
    current_sha = current_image_sha256(image_by_name[basename])
    if current_sha != expected_sha:
        raise ValueError(f"stale SHA-256 for original editorial image {basename}")
    analysis = entry.get("analysis")
    if not isinstance(analysis, dict):
        raise ValueError(f"missing analysis for original editorial image {basename}")
    dims = analysis.get("dimension_scores")
    if not isinstance(dims, dict):
        raise ValueError(f"dimension_scores for {basename} is not an object")
    required_dims = set(ORIGINAL_EDITORIAL_DIMENSIONS)
    actual_dims = set(dims)
    missing_dims = sorted(required_dims - actual_dims)
    unexpected_dims = sorted(actual_dims - required_dims)
    if missing_dims or unexpected_dims:
        details = []
        if missing_dims:
            details.append("missing dimensions: " + ", ".join(missing_dims))
        if unexpected_dims:
            details.append("unexpected dimensions: " + ", ".join(unexpected_dims))
        raise ValueError(f"invalid dimension_scores schema for {basename}: {'; '.join(details)}")
    for dim, value in dims.items():
        original_editorial_numeric(value, key=f"{basename}.dimension_scores.{dim}")
    original_editorial_numeric(analysis.get("overall_editorial_utility", 5.5), key=f"{basename}.overall_editorial_utility")
    return analysis


def load_original_editorial_analysis() -> dict[str, dict]:
    """Load original editorial analysis."""
    path = Path(str(ORIGINAL_EDITORIAL_ANALYSIS_FILE)).expanduser()
    cache_key = str(path)
    if cache_key in _ORIGINAL_EDITORIAL_ANALYSIS_CACHE:
        return _ORIGINAL_EDITORIAL_ANALYSIS_CACHE[cache_key]
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise RuntimeError(f"Original editorial analysis file is not a JSON object: {path}")
    if data.get("analysis_kind") != ORIGINAL_EDITORIAL_ANALYSIS_KIND:
        raise RuntimeError(f"Original editorial analysis has unexpected analysis_kind={data.get('analysis_kind')!r}")
    if (type(data.get("schema_version")) is not int
            or data.get("schema_version") != ORIGINAL_EDITORIAL_SCHEMA_VERSION):
        raise RuntimeError(f"Original editorial analysis has unsupported schema_version={data.get('schema_version')!r}")
    items = data.get("items")
    if not isinstance(items, dict):
        raise RuntimeError("Original editorial analysis items must be an object")
    image_by_name = {Path(path_text).name: path_text for path_text in current_image_paths()}
    result: dict[str, dict] = {}
    try:
        for key, entry in items.items():
            if not isinstance(entry, dict):
                raise ValueError(f"invalid item for {key}")
            basename = str(entry.get("basename") or key)
            result[basename] = validate_original_editorial_item(basename, entry, image_by_name)
        missing_originals = sorted(
            basename
            for basename in image_by_name
            if not generated_image_origin_quote_hash(basename) and basename not in result
        )
        if missing_originals:
            raise ValueError(f"missing original editorial analysis for current image(s): {', '.join(missing_originals[:5])}")
    except ValueError as exc:
        raise RuntimeError(f"Invalid original editorial analysis {path}: {exc}") from exc
    _ORIGINAL_EDITORIAL_ANALYSIS_CACHE[cache_key] = result
    return result


def validate_original_editorial_shadow_startup() -> None:
    """Validate original editorial shadow startup."""
    if not ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING:
        return
    count = len(load_original_editorial_analysis())
    log.info(
        "Original editorial shadow scoring enabled. analysis_file=%s original_items=%d weight=%s max_abs_adjustment=%s",
        ORIGINAL_EDITORIAL_ANALYSIS_FILE,
        count,
        ORIGINAL_EDITORIAL_SHADOW_WEIGHT,
        ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT,
    )


def generated_identity_numeric(value: object, *, key: str, maximum: float = 10.0) -> float:
    """Return the generated identity numeric."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{key} must be a number")
    number = float(value)
    if not math.isfinite(number) or not 0.0 <= number <= maximum:
        raise ValueError(f"{key} must be finite in 0..{maximum:g}")
    return number


def configured_generated_image_paths() -> dict[str, Path]:
    """Return the configured generated image paths."""
    generated_dir = Path(str(GENERATED_IMAGE_DIR)).expanduser()
    generated_glob = str(generated_dir / str(GENERATED_IMAGE_GLOB))
    result: dict[str, Path] = {}
    for path_text in sorted(glob(generated_glob)):
        path = Path(path_text)
        if not path.is_file() or not path_is_same_or_child(path, generated_dir):
            continue
        basename = path.name
        if not generated_image_origin_quote_hash(basename):
            raise ValueError(f"invalid generated image basename in configured pool: {basename}")
        if basename in result:
            raise ValueError(f"duplicate generated image basename in configured pool: {basename}")
        result[basename] = path
    return result


def validate_generated_identity_audit_item(basename: str, item: object, image_by_name: dict[str, Path]) -> dict:
    """Validate generated identity audit item."""
    if not generated_image_origin_quote_hash(basename):
        raise ValueError(f"non-generated basename in identity audit: {basename}")
    if basename not in image_by_name:
        raise ValueError(f"identity audit image is absent from configured generated pool: {basename}")
    if not isinstance(item, dict):
        raise ValueError(f"identity audit item for {basename} must be an object")
    if str(item.get("basename") or basename) != basename:
        raise ValueError(f"identity audit basename mismatch for {basename}")
    expected_hash = str(item.get("image_sha256") or "")
    if not expected_hash or file_sha256(image_by_name[basename]) != expected_hash:
        raise ValueError(f"stale identity audit SHA-256 for {basename}")
    origin_hash = generated_image_origin_quote_hash(basename)
    if str(item.get("origin_quote_hash") or "").lower() != origin_hash:
        raise ValueError(f"identity audit origin quote hash mismatch for {basename}")
    analysis = item.get("analysis")
    if not isinstance(analysis, dict):
        raise ValueError(f"identity audit analysis missing for {basename}")
    policy = analysis.get("recommended_cross_quote_policy")
    if policy not in GENERATED_IDENTITY_POLICIES:
        raise ValueError(f"invalid generated identity policy for {basename}: {policy!r}")
    dependence = analysis.get("identity_dependence")
    if dependence not in GENERATED_IDENTITY_DEPENDENCE_VALUES:
        raise ValueError(f"invalid identity dependence for {basename}: {dependence!r}")
    if type(analysis.get("contains_specific_intended_person")) is not bool:
        raise ValueError(f"contains_specific_intended_person for {basename} must be boolean")
    for key in (
        "recognisability_to_typical_viewer",
        "recognisability_to_politically_interested_viewer",
        "meaning_retention_without_identity",
        "origin_quote_suitability",
        "recommended_penalty_strength",
    ):
        generated_identity_numeric(analysis.get(key), key=f"{basename}.{key}")
    generated_identity_numeric(analysis.get("confidence"), key=f"{basename}.confidence", maximum=1.0)
    return analysis


def load_generated_identity_audit() -> dict[str, dict]:
    """Load generated identity audit."""
    path = Path(str(GENERATED_IDENTITY_AUDIT_FILE)).expanduser()
    cache_key = str(path)
    if cache_key in _GENERATED_IDENTITY_AUDIT_CACHE:
        return _GENERATED_IDENTITY_AUDIT_CACHE[cache_key]
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise RuntimeError(f"Generated identity audit is not a JSON object: {path}")
    if (type(payload.get("schema_version")) is not int
            or payload.get("schema_version") != GENERATED_IDENTITY_AUDIT_SCHEMA_VERSION):
        raise RuntimeError(f"Generated identity audit has unsupported schema_version={payload.get('schema_version')!r}")
    if payload.get("analysis_kind") != GENERATED_IDENTITY_AUDIT_KIND:
        raise RuntimeError(f"Generated identity audit has unexpected analysis_kind={payload.get('analysis_kind')!r}")
    items = payload.get("items")
    if not isinstance(items, dict):
        raise RuntimeError("Generated identity audit items must be an object")
    image_by_name = configured_generated_image_paths()
    if not image_by_name:
        raise RuntimeError("No configured generated images are available for identity audit validation")
    result: dict[str, dict] = {}
    try:
        for basename, item in items.items():
            basename = str(basename)
            result[basename] = validate_generated_identity_audit_item(basename, item, image_by_name)
        missing = sorted(set(image_by_name) - set(result))
        unexpected = sorted(set(result) - set(image_by_name))
        if missing or unexpected:
            raise ValueError(f"identity audit pool coverage mismatch missing={missing[:8]} unexpected={unexpected[:8]}")
    except ValueError as exc:
        raise RuntimeError(f"Invalid generated identity audit {path}: {exc}") from exc
    _GENERATED_IDENTITY_AUDIT_CACHE[cache_key] = result
    return result


def validate_generated_identity_shadow_startup() -> None:
    """Validate generated identity shadow startup."""
    if not ENABLE_GENERATED_IMAGE_POOL:
        if ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING or ENABLE_GENERATED_IDENTITY_POLICY_SCORING:
            log.info(
                "Generated identity-policy processing suspended because the generated image pool is disabled"
            )
        return
    if not (
        ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING
        or ENABLE_GENERATED_IDENTITY_POLICY_SCORING
    ):
        return
    items = load_generated_identity_audit()
    policies = Counter(str(item.get("recommended_cross_quote_policy")) for item in items.values())
    if ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING:
        log.info(
            "Generated identity-policy shadow scoring enabled. audit_file=%s items=%d policies=%s small_penalty=%s strong_penalty=%s",
            GENERATED_IDENTITY_AUDIT_FILE,
            len(items),
            dict(sorted(policies.items())),
            GENERATED_IDENTITY_SHADOW_SMALL_PENALTY,
            GENERATED_IDENTITY_SHADOW_STRONG_PENALTY,
        )
    if ENABLE_GENERATED_IDENTITY_POLICY_SCORING:
        log.info(
            "Generated identity policy production scoring enabled. audit_file=%s items=%d policies=%s small_penalty=%s strong_penalty=%s",
            GENERATED_IDENTITY_AUDIT_FILE,
            len(items),
            dict(sorted(policies.items())),
            GENERATED_IDENTITY_SHADOW_SMALL_PENALTY,
            GENERATED_IDENTITY_SHADOW_STRONG_PENALTY,
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
    basename = str(candidate.get("basename") or "")
    source = str(candidate.get("image_source") or "original")
    baseline = float(candidate.get("score") or 0.0)
    origin_match = bool(candidate.get("origin_quote_match"))
    policy = None
    action = "original_unchanged"
    adjustment: float | None = 0.0
    shadow_score: float | None = baseline
    if source == "generated":
        audit = audit_by_basename.get(basename)
        if not audit:
            raise RuntimeError(f"Generated identity audit missing selected candidate: {basename}")
        policy = str(audit.get("recommended_cross_quote_policy"))
        if origin_match:
            action = "generated_origin_quote_unrestricted"
        elif policy == "unrestricted":
            action = "generated_cross_quote_unrestricted"
        elif policy == "small_penalty":
            action = "generated_cross_quote_small_penalty"
            adjustment = -float(GENERATED_IDENTITY_SHADOW_SMALL_PENALTY)
            shadow_score = baseline + adjustment
        elif policy == "strong_penalty":
            action = "generated_cross_quote_strong_penalty"
            adjustment = -float(GENERATED_IDENTITY_SHADOW_STRONG_PENALTY)
            shadow_score = baseline + adjustment
        elif policy == "origin_quote_only":
            action = "generated_cross_quote_origin_only_excluded"
            adjustment = None
            shadow_score = None
        else:
            raise RuntimeError(f"Unsupported generated identity policy for {basename}: {policy!r}")
    return {
        "basename": basename,
        "source": source,
        "baseline_score": baseline,
        "origin_quote_match": origin_match,
        "identity_policy": policy,
        "identity_action": action,
        "identity_adjustment": adjustment,
        "identity_shadow_score": shadow_score,
    }


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
    audit_by_basename = load_generated_identity_audit() if audit_by_basename is None else audit_by_basename
    rows = [generated_identity_candidate_shadow_row(candidate, audit_by_basename) for candidate in scored_candidates]
    baseline_maximum = max((float(row["baseline_score"]) for row in rows), default=None)
    baseline_tied = [
        row for row in rows if baseline_maximum is not None and float(row["baseline_score"]) == baseline_maximum
    ]
    eligible = [row for row in rows if row["identity_shadow_score"] is not None]
    maximum = max((float(row["identity_shadow_score"]) for row in eligible), default=None)
    tied = [row for row in eligible if float(row["identity_shadow_score"]) == maximum] if maximum is not None else []
    production_basename = str(production_choice.get("basename") or "")
    production_row = next(row for row in rows if row["basename"] == production_basename)
    baseline_winner = None
    shadow_winner = None
    if selection_rng_state is not None:
        baseline_winner = _choice_with_random_state(baseline_tied, selection_rng_state)
        shadow_winner = _choice_with_random_state(tied, selection_rng_state) if tied else None
    generated_rows = [row for row in rows if row["source"] == "generated"]
    cross_quote = [row for row in generated_rows if not row["origin_quote_match"]]
    excluded = [row["basename"] for row in cross_quote if row["identity_policy"] == "origin_quote_only"]
    penalised = [row["basename"] for row in cross_quote if row["identity_policy"] in {"small_penalty", "strong_penalty"}]
    policy_relevant = bool(excluded or penalised)
    counterfactual_valid = bool(
        baseline_winner is not None
        and baseline_winner["basename"] == production_basename
    )
    winner_changed = bool(
        counterfactual_valid
        and policy_relevant
        and (shadow_winner is None or shadow_winner["basename"] != baseline_winner["basename"])
    )
    if selection_rng_state is None or not counterfactual_valid:
        policy_effect = "counterfactual_mismatch"
    elif winner_changed:
        policy_effect = "winner_changed"
    elif policy_relevant:
        policy_effect = "scores_or_eligibility_only"
    else:
        policy_effect = "none"

    def winner_value(key: str) -> object:
        return shadow_winner.get(key) if shadow_winner else None
    return {
        "quote_hash": str(quote_choice.get("quote_hash") or ""),
        "line_no": int(quote_choice.get("line_no", -1)),
        "selection_phase": selection_phase,
        "production_source": production_row["source"],
        "production_winner": production_basename,
        "production_score": round(float(production_row["baseline_score"]), 4),
        "production_origin_quote_match": production_row["origin_quote_match"],
        "production_identity_policy": production_row["identity_policy"],
        "production_identity_action": production_row["identity_action"],
        "production_identity_adjustment": production_row["identity_adjustment"],
        "production_identity_shadow_score": round(float(production_row["identity_shadow_score"]), 4) if production_row["identity_shadow_score"] is not None else None,
        "baseline_winner": baseline_winner.get("basename") if baseline_winner else None,
        "baseline_tie_count": len(baseline_tied),
        "shadow_winner_source": winner_value("source"),
        "shadow_winner": winner_value("basename"),
        "shadow_winner_baseline_score": round(float(winner_value("baseline_score")), 4) if shadow_winner else None,
        "shadow_winner_origin_quote_match": winner_value("origin_quote_match"),
        "shadow_winner_identity_policy": winner_value("identity_policy"),
        "shadow_winner_identity_action": winner_value("identity_action"),
        "shadow_winner_identity_adjustment": winner_value("identity_adjustment"),
        "shadow_winner_score": round(float(winner_value("identity_shadow_score")), 4) if shadow_winner else None,
        "counterfactual_comparison_version": "generated_identity_counterfactual_v1",
        "counterfactual_comparison_valid": counterfactual_valid,
        "counterfactual_policy_winner": winner_value("basename"),
        "policy_effect": policy_effect,
        "winner_changed_by_policy": winner_changed,
        # Compatibility alias retained for existing shadow-log consumers.
        "winner_changed": winner_changed,
        "eligible_candidate_count": len(rows),
        "eligible_original_count": sum(row["source"] == "original" for row in rows),
        "eligible_generated_count": len(generated_rows),
        "cross_quote_generated_count": len(cross_quote),
        "origin_quote_generated_count": len(generated_rows) - len(cross_quote),
        "unrestricted_cross_quote_count": sum(row["identity_policy"] == "unrestricted" for row in cross_quote),
        "small_penalty_count": sum(row["identity_policy"] == "small_penalty" for row in cross_quote),
        "strong_penalty_count": sum(row["identity_policy"] == "strong_penalty" for row in cross_quote),
        "origin_quote_only_excluded_count": len(excluded),
        "excluded_generated_basenames": sorted(excluded)[:12],
        "excluded_generated_basenames_truncated": len(excluded) > 12,
        "penalised_generated_basenames": sorted(penalised)[:12],
        "penalised_generated_basenames_truncated": len(penalised) > 12,
        "small_penalty": float(GENERATED_IDENTITY_SHADOW_SMALL_PENALTY),
        "strong_penalty": float(GENERATED_IDENTITY_SHADOW_STRONG_PENALTY),
        "shadow_tie_count": len(tied),
        "shadow_tie_handling": "shared_random_state_counterfactual",
    }


def generated_identity_policy_selection(
    scored_candidates: list[dict],
    *,
    audit_by_basename: dict[str, dict] | None = None,
) -> tuple[list[dict], list[dict]]:
    """Return policy rows and eligible candidates without mutating input rows."""
    audit_by_basename = load_generated_identity_audit() if audit_by_basename is None else audit_by_basename
    rows = [generated_identity_candidate_shadow_row(candidate, audit_by_basename) for candidate in scored_candidates]
    candidates_by_name = {str(candidate["basename"]): candidate for candidate in scored_candidates}
    eligible: list[dict] = []
    for row in rows:
        if row["identity_shadow_score"] is None:
            continue
        candidate = dict(candidates_by_name[row["basename"]])
        candidate["baseline_score"] = float(row["baseline_score"])
        candidate["score"] = float(row["identity_shadow_score"])
        candidate["identity_policy"] = row["identity_policy"]
        candidate["identity_action"] = row["identity_action"]
        candidate["identity_adjustment"] = row["identity_adjustment"]
        eligible.append(candidate)
    return rows, eligible


def _choice_with_random_state(candidates: list[dict], rng_state: object) -> dict:
    """Replay random.choice without consuming or changing the production RNG."""
    chooser = random.Random()
    chooser.setstate(rng_state)
    return chooser.choice(candidates)


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
    rows_by_name = {row["basename"]: row for row in policy_rows}
    production_row = rows_by_name[str(production_winner["basename"])]
    baseline_best = max(float(row["baseline_score"]) for row in policy_rows)
    baseline_tied = [row for row in policy_rows if float(row["baseline_score"]) == baseline_best]
    policy_eligible = [row for row in policy_rows if row["identity_shadow_score"] is not None]
    policy_best = max(float(row["identity_shadow_score"]) for row in policy_eligible)
    policy_tied = [row for row in policy_eligible if float(row["identity_shadow_score"]) == policy_best]
    baseline_row = _choice_with_random_state(baseline_tied, selection_rng_state)
    counterfactual_policy_row = _choice_with_random_state(policy_tied, selection_rng_state)
    generated_rows = [row for row in policy_rows if row["source"] == "generated"]
    cross_quote = [row for row in generated_rows if not row["origin_quote_match"]]
    excluded = sorted(row["basename"] for row in cross_quote if row["identity_policy"] == "origin_quote_only")
    penalised = sorted(row["basename"] for row in cross_quote if row["identity_policy"] in {"small_penalty", "strong_penalty"})
    policy_relevant = any(
        float(row.get("identity_adjustment") or 0) != 0.0
        or row.get("identity_shadow_score") is None
        for row in cross_quote
    )
    counterfactual_valid = (
        counterfactual_policy_row["basename"] == production_row["basename"]
        and len(policy_tied) == int(policy_tie_count)
    )
    baseline_differs = baseline_row["basename"] != counterfactual_policy_row["basename"]
    changed = counterfactual_valid and policy_relevant and baseline_differs
    if not counterfactual_valid:
        policy_effect = "counterfactual_mismatch"
    elif changed:
        policy_effect = "winner_changed"
    elif policy_relevant:
        policy_effect = "scores_or_eligibility_only"
    else:
        policy_effect = "none"
    return {
        "quote_hash": str(quote_choice.get("quote_hash") or ""),
        "line_no": int(quote_choice.get("line_no", -1)),
        "selection_phase": selection_phase,
        "baseline_winner": baseline_row["basename"],
        "baseline_winner_source": baseline_row["source"],
        "baseline_winner_score": round(float(baseline_row["baseline_score"]), 4),
        "baseline_origin_quote_match": baseline_row["origin_quote_match"],
        "baseline_identity_policy": baseline_row["identity_policy"],
        "baseline_identity_action": baseline_row["identity_action"],
        "production_winner": production_row["basename"],
        "production_winner_source": production_row["source"],
        "production_policy_score": round(float(production_winner["score"]), 4),
        "production_baseline_score": round(float(production_row["baseline_score"]), 4),
        "production_origin_quote_match": production_row["origin_quote_match"],
        "production_identity_policy": production_row["identity_policy"],
        "production_identity_action": production_row["identity_action"],
        "production_identity_adjustment": production_row["identity_adjustment"],
        "counterfactual_comparison_version": "generated_identity_counterfactual_v1",
        "counterfactual_comparison_valid": counterfactual_valid,
        "counterfactual_policy_winner": counterfactual_policy_row["basename"],
        "policy_effect": policy_effect,
        "winner_changed_by_policy": changed,
        "baseline_winner_differs": baseline_differs,
        "eligible_candidate_count_before_policy": len(baseline_candidates),
        "eligible_original_count_before_policy": sum(row["source"] == "original" for row in policy_rows),
        "eligible_generated_count_before_policy": len(generated_rows),
        "eligible_candidate_count_after_policy": sum(row["identity_shadow_score"] is not None for row in policy_rows),
        "origin_quote_only_excluded_count": len(excluded),
        "small_penalty_count": sum(row["identity_policy"] == "small_penalty" for row in cross_quote),
        "strong_penalty_count": sum(row["identity_policy"] == "strong_penalty" for row in cross_quote),
        "excluded_generated_basenames": excluded[:12],
        "excluded_generated_basenames_truncated": len(excluded) > 12,
        "penalised_generated_basenames": penalised[:12],
        "penalised_generated_basenames_truncated": len(penalised) > 12,
        "replacement_source_transition": f"{baseline_row['source']}->{production_row['source']}" if changed else "unchanged",
        "policy_tie_count": int(policy_tie_count),
        "baseline_tie_count": len(baseline_tied),
        "baseline_tie_handling": "shared_random_state_counterfactual",
        "recovery_effect": selection_phase if selection_phase != "normal" else "none",
    }


def log_generated_identity_policy_applied_result(payload: dict) -> None:
    """Log generated identity policy applied result."""
    log.info("GENERATED_IDENTITY_POLICY_APPLIED %s", json.dumps(payload, sort_keys=True, separators=(",", ":")))


def log_generated_identity_policy_shadow_result(
    quote_choice: dict,
    production_choice: dict,
    scored_candidates: list[dict],
    *,
    selection_phase: str,
    selection_rng_state: object | None = None,
) -> None:
    """Log generated identity policy shadow result."""
    if not generated_identity_policy_shadow_active():
        return
    try:
        payload = generated_identity_policy_shadow_result(
            quote_choice,
            production_choice,
            scored_candidates,
            selection_phase=selection_phase,
            selection_rng_state=selection_rng_state,
        )
        log.info("GENERATED_IDENTITY_POLICY_SHADOW_RESULT %s", json.dumps(payload, sort_keys=True, separators=(",", ":")))
    except Exception:
        log.exception(
            "Generated identity-policy shadow evaluation failed; production selection remains unchanged. line_no=%s image=%s phase=%s",
            quote_choice.get("line_no"),
            production_choice.get("basename"),
            selection_phase,
        )


def original_editorial_shadow_score(
    quote_analysis: dict | None,
    editorial: dict | None,
    *,
    weight: float | None = None,
    max_abs_adjustment: float | None = None,
) -> tuple[float, dict]:
    """Calculate the observational editorial adjustment for one image."""
    if not isinstance(editorial, dict):
        return 0.0, {"dimension_score": 0.0, "affinity_score": 0.0, "utility_adjustment": 0.0, "penalty": 0.0, "cap_hit": False}
    weight = ORIGINAL_EDITORIAL_SHADOW_WEIGHT if weight is None else float(weight)
    max_abs_adjustment = ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT if max_abs_adjustment is None else float(max_abs_adjustment)
    q_profile = original_editorial_quote_dimension_profile(quote_analysis)
    raw_dims = editorial.get("dimension_scores") if isinstance(editorial.get("dimension_scores"), dict) else {}
    dimension_terms: list[dict] = []
    dimension_score = 0.0
    for dim, q_value in q_profile.items():
        if q_value <= 0:
            continue
        image_value = original_editorial_numeric(raw_dims.get(dim, 0), key=f"dimension_scores.{dim}") / 10.0
        contribution = q_value * (image_value - 0.45) * 5.0
        dimension_score += contribution
        dimension_terms.append(
            {
                "dimension": dim,
                "quote": round(q_value, 3),
                "image": round(image_value, 3),
                "contribution": round(contribution, 3),
            }
        )
    if q_profile.get("warning", 0) > 0.55 and original_editorial_numeric(raw_dims.get("optimism", 0), key="dimension_scores.optimism") / 10.0 > 0.75:
        dimension_score -= 1.5
        dimension_terms.append({"dimension": "optimism_warning_tension", "contribution": -1.5})
    if q_profile.get("defiance", 0) > 0.45 and original_editorial_numeric(raw_dims.get("ceremony_formality", 0), key="dimension_scores.ceremony_formality") / 10.0 > 0.85:
        dimension_score -= 0.8
        dimension_terms.append({"dimension": "ceremony_action_tension", "contribution": -0.8})

    q_concepts = original_editorial_quote_concepts(quote_analysis)
    image_concepts = original_editorial_image_concepts(editorial)
    avoid = original_editorial_avoid_concepts(editorial)
    affinity_matches = sorted(q_concepts & image_concepts)
    avoid_matches = sorted(q_concepts & avoid)
    affinity_score = min(4.0, 1.0 * len(affinity_matches))
    penalty = 1.25 * len(avoid_matches)
    utility = original_editorial_numeric(editorial.get("overall_editorial_utility", 5.5), key="overall_editorial_utility")
    utility_adjustment = max(-0.5, min(1.0, (utility - 5.5) / 4.5))
    raw_layer = dimension_score + affinity_score + utility_adjustment - penalty
    weighted = raw_layer * weight
    capped = max(-max_abs_adjustment, min(max_abs_adjustment, weighted))
    return capped, {
        "dimension_score": round(dimension_score, 4),
        "affinity_score": round(affinity_score, 4),
        "utility_adjustment": round(utility_adjustment, 4),
        "penalty": round(penalty, 4),
        "raw_layer": round(raw_layer, 4),
        "weighted_adjustment": round(weighted, 4),
        "capped_editorial_adjustment": round(capped, 4),
        "cap_hit": abs(capped - weighted) > 1e-9,
        "dimension_matches": [term["dimension"] for term in dimension_terms if term.get("contribution", 0) > 0],
        "dimension_terms": dimension_terms,
        "affinity_matches": affinity_matches,
        "penalties": avoid_matches,
    }


def log_original_editorial_shadow_result(
    quote_choice: dict,
    production_choice: dict,
    scored_candidates: list[dict],
    *,
    selection_phase: str,
) -> None:
    """Log original editorial shadow result."""
    if not ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING:
        return
    editorial_by_basename = load_original_editorial_analysis()
    original_rows: list[dict] = []
    for candidate in scored_candidates:
        if candidate.get("image_source") != "original":
            continue
        basename = str(candidate.get("basename") or "")
        editorial = editorial_by_basename.get(basename)
        if not editorial:
            continue
        adjustment, detail = original_editorial_shadow_score(quote_choice.get("analysis"), editorial)
        baseline = float(candidate.get("score") or 0.0)
        original_rows.append(
            {
                "basename": basename,
                "baseline_score": baseline,
                "editorial_adjustment": adjustment,
                "shadow_score": baseline + adjustment,
                "detail": detail,
            }
        )
    if not original_rows:
        return
    original_rows.sort(key=lambda row: (-float(row["shadow_score"]), row["basename"]))
    for idx, row in enumerate(original_rows, 1):
        row["shadow_rank"] = idx
    shadow_winner = original_rows[0]
    production_basename = str(production_choice.get("basename") or "")
    production_shadow = next((row for row in original_rows if row["basename"] == production_basename), None)
    winner_changed = production_shadow is not None and shadow_winner["basename"] != production_basename
    payload = {
        "quote_hash": str(quote_choice.get("quote_hash") or ""),
        "line_no": int(quote_choice.get("line_no", -1)),
        "selection_phase": selection_phase,
        "production_source": str(production_choice.get("image_source") or "original"),
        "production_winner": production_basename,
        "production_baseline_score": round(float(production_choice.get("score") or 0.0), 4),
        "production_editorial_adjustment": round(float(production_shadow["editorial_adjustment"]), 4) if production_shadow else None,
        "production_shadow_score": round(float(production_shadow["shadow_score"]), 4) if production_shadow else None,
        "production_shadow_rank": int(production_shadow["shadow_rank"]) if production_shadow else None,
        "shadow_original_winner": shadow_winner["basename"],
        "shadow_winner_baseline_score": round(float(shadow_winner["baseline_score"]), 4),
        "shadow_winner_editorial_adjustment": round(float(shadow_winner["editorial_adjustment"]), 4),
        "shadow_winner_score": round(float(shadow_winner["shadow_score"]), 4),
        "winner_changed": bool(winner_changed),
        "eligible_original_count": len(original_rows),
        "weight": float(ORIGINAL_EDITORIAL_SHADOW_WEIGHT),
        "max_abs_adjustment": float(ORIGINAL_EDITORIAL_SHADOW_MAX_ABS_ADJUSTMENT),
        "cap_hit": bool(shadow_winner["detail"].get("cap_hit") or (production_shadow or {}).get("detail", {}).get("cap_hit")),
        "dimension_matches": shadow_winner["detail"].get("dimension_matches", [])[:8],
        "affinity_matches": shadow_winner["detail"].get("affinity_matches", [])[:8],
        "penalties": shadow_winner["detail"].get("penalties", [])[:8],
    }
    log.info("ORIGINAL_EDITORIAL_SHADOW_RESULT %s", json.dumps(payload, sort_keys=True, separators=(",", ":")))


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
    hard_excluded = 0
    non_empty = 0
    candidates: list[dict] = []
    excluded_quote_hashes = excluded_quote_hashes or set()
    seen_hashes: set[str] = set()

    for line_no in available_lines:
        tweet = lines[line_no].rstrip()
        if not tweet:
            log.debug("Skipping empty line_no=%d", line_no)
            continue
        quote_hash = quote_text_hash(tweet)
        if quote_hash in seen_hashes:
            log.debug("Skipping duplicate quote line_no=%d quote_hash=%s", line_no, quote_hash)
            continue
        seen_hashes.add(quote_hash)
        if quote_hash in excluded_quote_hashes:
            continue
        non_empty += 1

        analysis = quote_metadata_for_hash(quote_analysis, quote_hash, tweet)
        if analysis is None:
            log.warning("Skipping unanalysed current quote line_no=%d quote_hash=%s until quote analysis is refreshed", line_no, quote_hash)
            continue
        weight, season_status = quote_candidate_weight(analysis, today_mm_dd=today_mm_dd)
        if weight <= 0:
            hard_excluded += 1
            continue
        candidates.append(
            {
                "line_no": line_no,
                "text": tweet,
                "quote_hash": quote_hash,
                "analysis": analysis,
                "weight": weight,
                "season_status": season_status,
            }
        )
    return candidates, hard_excluded, non_empty


def load_quote_lines_and_analysis() -> tuple[list[str], dict | None, str]:
    """Load the active quotation source and validated analysis metadata."""
    with open(LINES_FILE) as f:
        lines = f.readlines()

    log.debug("Loaded %d lines from %s", len(lines), LINES_FILE)

    if not lines:
        raise RuntimeError(f"No lines found in {LINES_FILE}")

    quote_analysis = load_quote_analysis()
    if quote_analysis is None:
        raise RuntimeError(f"Quote analysis unavailable or invalid; refusing regular quote posting from {LINES_FILE}")
    validate_quote_analysis_against_lines(quote_analysis, lines)
    return lines, quote_analysis, current_datetime().strftime("%m-%d")


def load_completed_research_quote_hashes() -> set[str]:
    """Load the exact hash-bound ordinary-post eligibility partition."""
    from historical_context_formatter import (
        THATCHER_ATTRIBUTION_RULE_VERSION,
        load_and_validate_corpus_core,
        packet_is_attributed_to_margaret_thatcher,
    )

    packets, _unresolved = load_and_validate_corpus_core(
        HISTORICAL_CONTEXT_RESEARCH_DIR
    )
    manifest = load_json_object(
        RUNTIME_ELIGIBLE_QUOTE_MANIFEST_FILE,
        label="runtime eligible quotation manifest",
    )
    if manifest is None:
        raise RuntimeError(
            "Runtime eligible quotation manifest unavailable; refusing regular "
            f"quote posting: {RUNTIME_ELIGIBLE_QUOTE_MANIFEST_FILE}"
        )

    runtime_ids = manifest.get("runtime_eligible_quote_ids")
    resolved_ids = manifest.get("resolved_manifest_quote_ids")
    aliases = manifest.get("runtime_quote_aliases")
    source_hashes = manifest.get("source_file_hashes")
    declared_count = manifest.get("runtime_eligible_quote_count")
    declared_source_count = manifest.get("source_record_count")
    if (
        manifest.get("schema_version") != 1
        or manifest.get("eligibility_rule_version")
        != THATCHER_ATTRIBUTION_RULE_VERSION
        or type(declared_count) is not int
        or type(declared_source_count) is not int
        or not isinstance(runtime_ids, list)
        or not isinstance(resolved_ids, list)
        or not isinstance(aliases, dict)
        or not isinstance(source_hashes, dict)
        or any(
            not isinstance(value, str)
            or re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in runtime_ids
        )
        or any(
            not isinstance(value, str)
            or re.fullmatch(r"[0-9a-f]{64}", value) is None
            for value in resolved_ids
        )
        or any(
            not isinstance(key, str)
            or re.fullmatch(r"[0-9a-f]{64}", key) is None
            or not isinstance(value, str)
            or re.fullmatch(r"[0-9a-f]{64}", value) is None
            for key, value in aliases.items()
        )
    ):
        raise RuntimeError(
            "Runtime eligible quotation manifest structure is invalid; refusing "
            "regular quote posting"
        )
    if (
        declared_count <= 0
        or len(runtime_ids) != declared_count
        or len(set(runtime_ids)) != declared_count
        or len(resolved_ids) != declared_count
        or len(set(resolved_ids)) != declared_count
        or not set(aliases).issubset(set(runtime_ids))
        or sorted(aliases.get(value, value) for value in runtime_ids)
        != resolved_ids
    ):
        raise RuntimeError(
            "Runtime eligible quotation manifest counts or aliases are invalid; "
            "refusing regular quote posting"
        )

    current_source_hash = file_sha256(LINES_FILE)
    current_packets_hash = file_sha256(COMPLETED_QUOTE_RESEARCH_FILE)
    if (
        source_hashes.get("active_source") != current_source_hash
        or source_hashes.get("completed_quote_research")
        != current_packets_hash
    ):
        raise RuntimeError(
            "Runtime eligible quotation manifest source hashes are stale; "
            "refusing regular quote posting"
        )

    with LINES_FILE.open("r", encoding="utf-8") as handle:
        current_source_ids = {
            quote_text_hash(line.rstrip("\n"))
            for line in handle
            if line.strip()
        }
    if (
        declared_source_count != len(current_source_ids)
        or not set(runtime_ids).issubset(current_source_ids)
    ):
        raise RuntimeError(
            "Runtime eligible quotation manifest does not match the active "
            "quotation source"
        )

    eligible_packet_ids = {
        str(quote_id)
        for quote_id, packet in packets.items()
        if packet_is_attributed_to_margaret_thatcher(packet)
    }
    derived_runtime_ids = {
        quote_text_hash(str(packet.get("quote_text") or ""))
        for packet in packets.values()
        if packet_is_attributed_to_margaret_thatcher(packet)
    }
    if (
        set(resolved_ids) != eligible_packet_ids
        or set(runtime_ids) != derived_runtime_ids
    ):
        raise RuntimeError(
            "Runtime eligible quotation manifest differs from the validated "
            "canonical attribution partition"
        )
    return derived_runtime_ids


def completed_research_quote_hashes() -> set[str]:
    """Return current attribution-eligible completed quotation hashes."""
    return load_completed_research_quote_hashes()


def quote_candidates_for_current_cycle(lines_used: set, *, excluded_quote_hashes: set[str] | None = None) -> list[dict]:
    """Build the unused runtime-eligible quotation pool for the current cycle."""
    log.debug("Choosing unused line. Already used=%d", len(lines_used))

    lines, quote_analysis, today_mm_dd = load_quote_lines_and_analysis()
    hashes_by_line = current_quote_hashes_by_line(lines)
    completed_hashes = completed_research_quote_hashes()
    research_ineligible_hashes = set(hashes_by_line.values()).difference(completed_hashes)
    excluded_quote_hashes = set(excluded_quote_hashes or set()).union(research_ineligible_hashes)
    if research_ineligible_hashes:
        log.info(
            "Excluded %d source quotation(s) without attribution-eligible completed canonical research packets",
            len(research_ineligible_hashes),
        )
    unused_research_eligible_lines = [
        line_no
        for line_no, quote_hash in hashes_by_line.items()
        if quote_hash not in lines_used and quote_hash not in research_ineligible_hashes
    ]
    available_lines = [line_no for line_no, quote_hash in hashes_by_line.items() if quote_hash not in lines_used]

    log.debug("Available unused lines=%d", len(available_lines))

    if not unused_research_eligible_lines:
        log.info("All attribution-eligible researched quotations used; clearing line history")
        lines_used.clear()
        available_lines = list(hashes_by_line)

    candidates, hard_excluded, non_empty = build_quote_candidates(
        lines,
        available_lines,
        quote_analysis,
        today_mm_dd,
        excluded_quote_hashes=excluded_quote_hashes,
    )

    log.info(
        "Quote candidate pool: unused_non_empty=%d hard_excluded_by_date=%d",
        len(candidates),
        hard_excluded,
    )

    if not candidates and non_empty > 0 and hard_excluded == non_empty:
        log.warning(
            "Quote cycle is seasonally exhausted: %d unused quote(s) are hard-excluded today; resetting quote cycle",
            hard_excluded,
        )
        lines_used.clear()
        available_lines = list(hashes_by_line)
        candidates, hard_excluded, non_empty = build_quote_candidates(
            lines,
            available_lines,
            quote_analysis,
            today_mm_dd,
            excluded_quote_hashes=excluded_quote_hashes,
        )
        log.info(
            "Quote candidate pool after seasonal reset: unused_non_empty=%d hard_excluded_by_date=%d",
            len(candidates),
            hard_excluded,
        )

    if not candidates and non_empty > 0:
        full_candidates, full_hard_excluded, full_non_empty = build_quote_candidates(
            lines,
            list(hashes_by_line),
            quote_analysis,
            today_mm_dd,
            excluded_quote_hashes=excluded_quote_hashes,
        )
        if full_candidates:
            log.warning(
                "Quote cycle is exhausted by currently nonselectable quote(s); resetting quote cycle. "
                "unused_non_empty=%d full_selectable=%d full_hard_excluded=%d",
                non_empty,
                len(full_candidates),
                full_hard_excluded,
            )
            lines_used.clear()
            candidates = full_candidates
        elif full_non_empty:
            log.warning(
                "No currently selectable analysed quote exists in full corpus. non_empty=%d hard_excluded=%d",
                full_non_empty,
                full_hard_excluded,
            )

    if candidates:
        return candidates
    raise RuntimeError(f"No non-empty lines found in {LINES_FILE}")


def select_quote_candidate(candidates: list[dict]) -> dict:
    """Select quote candidate."""
    chosen = weighted_random_choice(candidates)
    log.info(
        "Selected quote line_no=%d quote_hash=%s weight=%.2f seasonal_boost=%s",
        chosen["line_no"],
        chosen.get("quote_hash"),
        chosen.get("weight", 0.0),
        bool(chosen.get("season_status", {}).get("in_window")),
    )
    log.debug("Selected quote text=%r", chosen["text"])
    return chosen


def choose_unused_line_candidate(lines_used: set, *, excluded_quote_hashes: set[str] | None = None) -> dict:
    """Select unused line candidate."""
    return select_quote_candidate(quote_candidates_for_current_cycle(lines_used, excluded_quote_hashes=excluded_quote_hashes))


def available_currently_eligible_image_basenames(
    eligible_basenames: set[str],
    images_used: set[str],
    state: dict | None = None,
) -> tuple[list[str], bool]:
    """Return whether available currently eligible image basenames."""
    if not eligible_basenames:
        raise NoEligibleImageForQuote("No currently eligible regular-post images are available")

    available = sorted(eligible_basenames.difference(images_used))
    cycle_reset = False

    if not available:
        log.info("All currently eligible regular-post images used; resetting eligible image cycle")
        for basename in eligible_basenames:
            images_used.discard(basename)
        available = sorted(eligible_basenames)
        cycle_reset = True

        last_name = str((state or {}).get("last_regular_image_filename") or "")
        if len(available) > 1 and last_name in available:
            available.remove(last_name)
            log.info("Temporarily excluded last regular image at eligible-cycle boundary: %s", last_name)

    return available, cycle_reset


def current_image_sha256(path: str) -> str:
    """Return the current image SHA-256."""
    return file_sha256(Path(path))


def image_metadata_for_basename(image_analysis: dict | None, basename: str, path: str | None = None) -> tuple[str | None, dict | None]:
    """Return the image metadata for basename."""
    if not isinstance(image_analysis, dict):
        return None, None
    image_hash = (image_analysis.get("path_index") or {}).get(basename)
    if not image_hash:
        log.warning("Image %s is absent from image analysis; excluding until analysed", basename)
        raise StaleImageMetadata(f"Image metadata missing for {basename}")
    image_hash = str(image_hash)
    if path is not None:
        try:
            current_hash = current_image_sha256(path)
        except Exception:
            log.exception("Could not hash current image for metadata validation: %s", path)
            raise StaleImageMetadata(f"Image content could not be verified for {basename}")
        if current_hash != image_hash:
            log.warning(
                "Image metadata stale for basename=%s: current_hash=%s analysed_hash=%s; excluding until reanalysed",
                basename,
                current_hash,
                image_hash,
            )
            raise StaleImageMetadata(f"Image metadata stale for {basename}")
    item = (image_analysis.get("items") or {}).get(str(image_hash), {})
    analysis = item.get("analysis") if isinstance(item, dict) else None
    if not isinstance(analysis, dict):
        log.warning("Image %s has no valid per-image analysis for hash=%s; excluding until reanalysed", basename, image_hash)
        raise StaleImageMetadata(f"Image analysis missing or invalid for {basename}")
    return str(image_hash), analysis


def generated_image_origin_quote_hash(basename: str) -> str | None:
    """Return whether generated image origin quote hash."""
    match = re.fullmatch(r"tg_([0-9a-fA-F]{64})\.[A-Za-z0-9]+", str(basename))
    if not match:
        return None
    return match.group(1).lower()


def image_selection_observability(basename: str, quote_hash: object = None, origin_quote_boost: float = 0.0) -> dict:
    """Return the image selection observability."""
    origin_quote_hash = generated_image_origin_quote_hash(basename)
    origin_quote_match = bool(origin_quote_hash and origin_quote_hash == str(quote_hash or "").lower())
    return {
        "image_source": "generated" if origin_quote_hash else "original",
        "origin_quote_hash": origin_quote_hash,
        "origin_quote_match": origin_quote_match,
        "origin_quote_boost": float(origin_quote_boost if origin_quote_match else 0.0),
    }


def generated_image_spacing_required() -> int:
    """Return the generated image spacing required."""
    if type(GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN) is not int:
        raise ValueError("GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN must be an integer")
    if GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN < 0:
        raise ValueError("GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN must be non-negative")
    return GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN


def original_posts_since_generated_image(state: dict | None) -> int:
    """Return the original posts since generated image."""
    if not state:
        return generated_image_spacing_required()
    try:
        value = int(state.get("original_regular_posts_since_generated_image", generated_image_spacing_required()) or 0)
    except Exception:
        value = 0
    return max(0, value)


def generated_images_allowed_by_spacing(state: dict | None) -> bool:
    """Return whether generated images allowed by spacing."""
    required = generated_image_spacing_required()
    if required <= 0:
        return True
    return original_posts_since_generated_image(state) >= required


def log_generated_image_spacing_status(state: dict | None) -> bool:
    """Log generated image spacing status."""
    required = generated_image_spacing_required()
    count = original_posts_since_generated_image(state)
    allowed = generated_images_allowed_by_spacing(state)
    log.info(
        "GENERATED_IMAGE_SPACING_STATUS pool_enabled=%s allowed=%s original_posts_since_generated=%d required=%d",
        str(bool(ENABLE_GENERATED_IMAGE_POOL)).lower(),
        str(bool(allowed)).lower(),
        count,
        required,
    )
    if ENABLE_GENERATED_IMAGE_POOL and required > 0 and not allowed:
        log.info(
            "GENERATED_IMAGE_POOL_BLOCKED_BY_SPACING original_posts_since_generated=%d required=%d",
            count,
            required,
        )
    return allowed


def log_generated_image_spacing_state_updated(state: dict | None, image_basename: str) -> None:
    """Log generated image spacing state updated."""
    required = generated_image_spacing_required()
    count = original_posts_since_generated_image(state)
    allowed = generated_images_allowed_by_spacing(state)
    source = "generated" if generated_image_origin_quote_hash(image_basename) else "original"
    log.info(
        "GENERATED_IMAGE_SPACING_STATE_UPDATED pool_enabled=%s allowed=%s original_posts_since_generated=%d required=%d image_source=%s image=%s",
        str(bool(ENABLE_GENERATED_IMAGE_POOL)).lower(),
        str(bool(allowed)).lower(),
        count,
        required,
        source,
        image_basename,
    )


def filter_generated_images_by_spacing(eligible_basenames: set[str], state: dict | None) -> set[str]:
    """Filter generated images by spacing."""
    if not ENABLE_GENERATED_IMAGE_POOL or generated_images_allowed_by_spacing(state):
        return eligible_basenames
    return {basename for basename in eligible_basenames if not generated_image_origin_quote_hash(basename)}


def update_regular_generated_image_spacing_state(state: dict, image_basename: str) -> None:
    """Update regular generated image spacing state."""
    required = generated_image_spacing_required()
    if generated_image_origin_quote_hash(image_basename):
        state["original_regular_posts_since_generated_image"] = 0
        log_generated_image_spacing_state_updated(state, image_basename)
        return
    current = original_posts_since_generated_image(state)
    state["original_regular_posts_since_generated_image"] = min(required, current + 1) if required > 0 else 0
    log_generated_image_spacing_state_updated(state, image_basename)


def regular_generated_image_spacing_already_reflected(state: dict, image_basename: str) -> bool:
    """Return the regular generated image spacing already reflected."""
    if "original_regular_posts_since_generated_image" not in state:
        return False
    if generated_image_origin_quote_hash(image_basename):
        return original_posts_since_generated_image(state) == 0
    return True


def log_regular_image_selection(choice: dict) -> None:
    """Log regular image selection."""
    log.info(
        "REGULAR_IMAGE_SELECTED source=%s basename=%s score=%s origin_quote_hash=%s origin_quote_match=%s origin_quote_boost=%s",
        choice.get("image_source", "original"),
        choice.get("basename", ""),
        choice.get("score"),
        choice.get("origin_quote_hash") or "",
        str(bool(choice.get("origin_quote_match"))).lower(),
        choice.get("origin_quote_boost", 0),
    )


def log_quote_image_semantic_veto_shadow(
    quote_choice: dict,
    production_choice: dict,
    scored_candidates: list[dict],
    *,
    selection_rng_state: object | None = None,
) -> None:
    """Observe the completed production decision without influencing it."""
    if not quote_image_semantic_veto.get("enabled") or quote_image_semantic_veto.get("mode") != "shadow":
        return
    runtime = _QUOTE_IMAGE_SEMANTIC_VETO_SHADOW
    if runtime is None:
        return
    rng_before = random.getstate()
    selected_before = (
        id(production_choice),
        production_choice.get("basename"),
        production_choice.get("image_hash"),
        production_choice.get("score"),
    )
    candidates_before = tuple(
        (id(item), item.get("basename"), item.get("image_hash"), item.get("score"))
        for item in scored_candidates
    )
    try:
        observed_selected = {
            key: production_choice.get(key)
            for key in ("basename", "image_hash", "image_source", "score")
        }
        observed_candidates = [
            {key: item.get(key) for key in ("basename", "image_hash", "image_source", "score")}
            for item in scored_candidates
        ]
        event = runtime.evaluate(
            quote_hash=str(quote_choice.get("quote_hash") or ""),
            selected=observed_selected,
            candidates=observed_candidates,
            quote_preview=str(quote_choice.get("text") or ""),
            tie_break_state=selection_rng_state,
        )
        selected_after = (
            id(production_choice),
            production_choice.get("basename"),
            production_choice.get("image_hash"),
            production_choice.get("score"),
        )
        candidates_after = tuple(
            (id(item), item.get("basename"), item.get("image_hash"), item.get("score"))
            for item in scored_candidates
        )
        if selected_after != selected_before or candidates_after != candidates_before:
            raise RuntimeError("semantic-veto shadow observer mutated the production decision")
        if random.getstate() != rng_before:
            raise RuntimeError("semantic-veto shadow observer consumed production RNG state")
        event["production_selection_changed"] = False
        event_name = str(event.pop("event"))
        log_event(event_name, **event)
    except Exception:
        if random.getstate() != rng_before:
            random.setstate(rng_before)
        log.exception("Quote/image semantic-veto shadow lookup failed; production selection remains unchanged")


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
    images = current_image_paths()
    log.debug("Found %d images matching %s", len(images), IMAGE_GLOB)
    if not images:
        raise RuntimeError(f"No images found matching {IMAGE_GLOB}")

    image_analysis = load_image_analysis()
    normalised, changed = normalise_image_used_basenames(images_used, images, image_analysis)
    if changed:
        images_used.clear()
        images_used.update(normalised)
        save_image_used_basenames(IMAGES_USED_FILE, normalised)
    if image_used_history_has_legacy_indices(images_used):
        raise UnsafeImageHistoryMigration(
            "Image used-history still contains legacy integer entries; refusing regular image posting until full analysed corpus is visible"
        )

    image_by_name = {Path(path).name: path for path in images}

    if image_analysis is None:
        raise GlobalImageUnavailable("Image analysis unavailable or invalid; refusing regular quote/image posting")

    today_mm_dd = current_datetime().strftime("%m-%d")
    idf = build_image_topic_idf(image_analysis)
    eligible_basenames: set[str] = set()
    seasonally_excluded = 0
    stale_excluded = 0
    for basename in image_by_name:
        try:
            _, analysis = image_metadata_for_basename(image_analysis, basename, image_by_name[basename])
        except StaleImageMetadata:
            stale_excluded += 1
            continue
        if analysis is not None and image_is_out_of_season(analysis, today_mm_dd):
            seasonally_excluded += 1
            log.info("Skipping image %s: seasonal image outside appropriate window", basename)
            continue
        eligible_basenames.add(basename)

    if generated_images_allowed is None:
        generated_images_allowed = generated_images_allowed_by_spacing(state)
    spacing_blocked_generated = 0
    if not generated_images_allowed:
        before_spacing = set(eligible_basenames)
        eligible_basenames = filter_generated_images_by_spacing(eligible_basenames, state)
        spacing_blocked_generated = len(before_spacing) - len(eligible_basenames)

    if not eligible_basenames:
        raise GlobalImageUnavailable("No analysed currently eligible regular-post images are available")

    if force_cycle_reset:
        log.info("Forcing eligible image cycle reset for regular quote/image pairing recovery")
        for basename in eligible_basenames:
            images_used.discard(basename)
        available = sorted(eligible_basenames)
        cycle_reset = True
    else:
        available, cycle_reset = available_currently_eligible_image_basenames(eligible_basenames, images_used, state)
    last_name = str((state or {}).get("last_regular_image_filename") or "")
    should_exclude_last = (
        avoid_last_image_at_cycle_boundary
        and len(available) > 1
        and last_name in available
        and (force_cycle_reset or (cycle_boundary_exclusions is not None and last_name in cycle_boundary_exclusions))
    )
    if should_exclude_last:
        available.remove(last_name)
        if cycle_boundary_exclusions is not None:
            cycle_boundary_exclusions.add(last_name)
        log.info("Temporarily excluded last regular image at forced eligible-cycle boundary: %s", last_name)
    log.info(
        "Image cycle status: used_count=%d currently_eligible=%d remaining_count=%d seasonally_excluded=%d stale_excluded=%d spacing_blocked_generated=%d cycle_reset=%s",
        len(images_used),
        len(eligible_basenames),
        len(available),
        seasonally_excluded,
        stale_excluded,
        spacing_blocked_generated,
        cycle_reset,
    )

    if not available:
        raise GlobalImageUnavailable("No currently unused eligible regular-post images are available")

    scored: list[dict] = []
    for basename in available:
        try:
            image_hash, analysis = image_metadata_for_basename(image_analysis, basename, image_by_name[basename])
        except StaleImageMetadata:
            log.info("Skipping image %s: stale analysed content", basename)
            continue
        score, components, eligible = score_image_for_quote(quote_choice.get("analysis"), analysis, idf)
        if not eligible:
            log.info("Skipping image %s: strong visual mismatch with selected quote", basename)
            continue
        origin_quote_hash = generated_image_origin_quote_hash(basename)
        origin_quote_boost = 0.0
        if origin_quote_hash and origin_quote_hash == str(quote_choice.get("quote_hash", "")).lower():
            boost = float(GENERATED_IMAGE_ORIGIN_QUOTE_BOOST)
            score += boost
            components = dict(components)
            components["generated_origin_quote"] = boost
            origin_quote_boost = boost
        path = image_by_name[basename]
        observability = image_selection_observability(
            basename,
            quote_choice.get("quote_hash"),
            origin_quote_boost,
        )
        scored.append(
            {
                "image_no": images.index(path),
                "path": path,
                "basename": basename,
                "image_hash": image_hash,
                "score": score,
                "components": components,
                "cycle_reset": cycle_reset,
                **observability,
            }
        )

    if not scored:
        raise QuoteSpecificImageMismatch("No metadata-eligible regular-post images matched the selected quote")

    baseline_best_score = max(float(item["score"]) for item in scored)
    baseline_tied = [item for item in scored if float(item["score"]) == baseline_best_score]
    policy_rows: list[dict] | None = None
    production_candidates = scored
    if generated_identity_policy_scoring_active():
        policy_rows, policy_candidates = generated_identity_policy_selection(scored)
        if not policy_candidates:
            log.warning(
                "Generated identity policy exhausted phase-specific candidates. line_no=%s quote_hash=%s phase=%s",
                quote_choice.get("line_no"),
                quote_choice.get("quote_hash"),
                selection_phase,
            )
            raise QuoteSpecificImageMismatch("Generated identity policy excluded all phase-specific candidates")
        best_score = max(float(item["score"]) for item in policy_candidates)
        tied = [item for item in policy_candidates if float(item["score"]) == best_score]
        production_candidates = policy_candidates
    else:
        best_score = baseline_best_score
        tied = baseline_tied
    selection_rng_state = random.getstate()
    chosen = random.choice(tied)

    log.info(
        "Selected matched image basename=%s image_no=%d score=%.2f components=%s",
        chosen["basename"],
        chosen["image_no"],
        chosen["score"],
        concise_components(chosen["components"]),
    )
    log_regular_image_selection(chosen)
    log_original_editorial_shadow_result(quote_choice, chosen, scored, selection_phase=selection_phase)
    if generated_identity_policy_scoring_active():
        assert policy_rows is not None
        log_generated_identity_policy_applied_result(
            generated_identity_policy_applied_result(
                quote_choice,
                chosen,
                scored,
                policy_rows,
                len(tied),
                selection_phase=selection_phase,
                selection_rng_state=selection_rng_state,
            )
        )
    else:
        log_generated_identity_policy_shadow_result(
            quote_choice,
            chosen,
            scored,
            selection_phase=selection_phase,
            selection_rng_state=selection_rng_state,
        )
    log_quote_image_semantic_veto_shadow(
        quote_choice,
        chosen,
        production_candidates,
        selection_rng_state=selection_rng_state,
    )
    for item in sorted(scored, key=lambda entry: float(entry["score"]), reverse=True)[:5]:
        log.debug(
            "Image match candidate basename=%s score=%.2f components=%s",
            item["basename"],
            item["score"],
            concise_components(item["components"]),
        )
    return chosen


def choose_regular_quote_image_pair(
    lines_used: set,
    images_used: set,
    state: dict,
    *,
    force_image_cycle_reset: bool = False,
    avoid_last_image_at_cycle_boundary: bool = True,
) -> tuple[dict, dict, int]:
    """Select a production quotation-image pair under current cycle rules."""
    attempted_quote_hashes: set[str] = set()
    attempts = 0
    reset_available_images_once = force_image_cycle_reset
    cycle_boundary_exclusions: set[str] = set()
    generated_images_allowed = log_generated_image_spacing_status(state)

    while attempts < MAX_QUOTE_IMAGE_PAIR_ATTEMPTS:
        attempts += 1
        try:
            quote_choice = choose_unused_line_candidate(lines_used, excluded_quote_hashes=attempted_quote_hashes)
        except RuntimeError:
            if attempted_quote_hashes:
                break
            raise
        attempted_quote_hashes.add(str(quote_choice["quote_hash"]))
        try:
            selection_phase = "normal"
            if force_image_cycle_reset:
                selection_phase = "forced_cycle_reset" if avoid_last_image_at_cycle_boundary else "last_image_fallback"
            image_choice = choose_matched_unused_image(
                images_used,
                quote_choice,
                state,
                force_cycle_reset=reset_available_images_once,
                avoid_last_image_at_cycle_boundary=avoid_last_image_at_cycle_boundary,
                cycle_boundary_exclusions=cycle_boundary_exclusions if force_image_cycle_reset else None,
                generated_images_allowed=generated_images_allowed,
                selection_phase=selection_phase,
            )
            if attempts > 1:
                log.info(
                    "Selected alternate quote/image pair after %d attempt(s). line_no=%s image=%s",
                    attempts,
                    quote_choice.get("line_no"),
                    image_choice.get("basename"),
                )
            return quote_choice, image_choice, attempts
        except QuoteSpecificImageMismatch as exc:
            log.warning(
                "Selected quote line_no=%s quote_hash=%s could not be paired with any currently eligible unused image: %s",
                quote_choice.get("line_no"),
                quote_choice.get("quote_hash"),
                exc,
            )
        finally:
            reset_available_images_once = False

    raise NoViableQuoteImagePair(
        f"No eligible regular quote/image pair found after {attempts} attempt(s); used histories unchanged",
        attempts,
        sorted(cycle_boundary_exclusions)[0] if cycle_boundary_exclusions else None,
    )


def post_random_quote(lines_used: set, images_used: set, state: dict) -> None:
    """Select and post one quotation-image pair transactionally."""
    log.info("Starting quote/image post cycle")
    block_if_ambiguous_remote_post()

    receipt_status = reconcile_main_post_receipts(
        lines_used,
        images_used,
        state,
        minimum_next_quote_epoch=now_epoch(),
    )
    if receipt_status.get("regular"):
        log.warning("Reconciled regular quote/image receipt; not creating a second regular post in the same call")
        return
    original_lines_used = set(lines_used)
    original_images_used = set(images_used)
    confirmed_post_sigint_guard: ConfirmedPostSigintDeferral | None = None

    try:
        require_historical_context_outbox_writable()
        if quote_used_history_has_legacy_indices(lines_used):
            raise CorruptUsedHistoryError(
                "Quote used-history still contains legacy integer entries; refusing regular quote posting until source-verified migration is possible"
            )

        try:
            quote_choice, image_choice, attempts = choose_regular_quote_image_pair(lines_used, images_used, state)
        except NoViableQuoteImagePair as exc:
            log.warning(
                "No viable regular quote/image pair found within current image cycle after %d attempt(s); "
                "resetting image cycle and retrying once",
                exc.attempts,
            )
            try:
                quote_choice, image_choice, attempts = choose_regular_quote_image_pair(
                    lines_used,
                    images_used,
                    state,
                    force_image_cycle_reset=True,
                )
            except NoViableQuoteImagePair as reset_exc:
                if not reset_exc.excluded_last_image:
                    log.error("No viable regular quote/image pair found after image-cycle recovery; giving up for this post attempt")
                    raise RuntimeError(str(reset_exc)) from reset_exc
                log.warning(
                    "No viable regular quote/image pair found after image-cycle recovery while excluding last regular image %s; "
                    "retrying once with last image permitted",
                    reset_exc.excluded_last_image,
                )
                try:
                    quote_choice, image_choice, attempts = choose_regular_quote_image_pair(
                        lines_used,
                        images_used,
                        state,
                        force_image_cycle_reset=True,
                        avoid_last_image_at_cycle_boundary=False,
                    )
                except NoViableQuoteImagePair as final_exc:
                    log.error(
                        "No viable regular quote/image pair found after final last-image recovery fallback; "
                        "giving up for this post attempt"
                    )
                    raise RuntimeError(str(final_exc)) from final_exc
                log.info("Regular quote/image pairing succeeded after permitting last regular image as final recovery fallback")
            else:
                log.info("Regular quote/image pairing succeeded after image-cycle recovery")

        line_no = int(quote_choice["line_no"])
        quote_hash = str(quote_choice["quote_hash"])
        tweet = str(quote_choice["text"])
        image_no = int(image_choice["image_no"])
        image = str(image_choice["path"])
        image_basename = str(image_choice["basename"])
        image_made_with_ai = image_choice.get("image_source") == "generated"
        quote_delay = random.randint(POST_SLEEP_MIN, POST_SLEEP_MAX)
        meme_delay = (
            random.randint(MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS, MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS)
            if ENABLE_DAILY_MEME_POSTS
            else None
        )

        log.info(
            "Posting quote/image. line_no=%d quote_hash=%s image_no=%d image=%s image_score=%s",
            line_no,
            quote_hash,
            image_no,
            image,
            image_choice.get("score"),
        )
        log.debug("Quote text=%r", tweet)

        media_id = upload_media(image)
        confirmed_post_sigint_guard = begin_confirmed_post_sigint_deferral()
        response = create_post(
            text=tweet,
            media_ids=[media_id],
            reply_to_id=None,
            made_with_ai=image_made_with_ai,
        )
        posted_id = response.get("data", {}).get("id") if isinstance(response, dict) else None
        log.debug("Posted_id=%s", posted_id)

        if not valid_post_id(posted_id):
            raise RuntimeError("Quote/image post did not return a valid post id; used histories unchanged")
    except BaseException as remote_exc:
        lines_used.clear()
        lines_used.update(original_lines_used)
        images_used.clear()
        images_used.update(original_images_used)
        if (
            isinstance(remote_exc, AmbiguousRemotePostOutcome)
            and _AMBIGUOUS_REMOTE_POST_SEEN
            and not durable_remote_write_safety_marker_exists()
        ):
            retain_sigint_deferral_without_durable_barrier(lane="quote_image")
        else:
            end_confirmed_post_sigint_deferral(confirmed_post_sigint_guard)
            confirmed_post_sigint_guard = None
        raise

    try:
        quote_post_epoch = now_epoch()
        state["next_quote_post_epoch"] = int(quote_post_epoch) + int(quote_delay)
        quote_schedule_fields, _quote_delay = next_quote_schedule_fields(quote_post_epoch, delay=quote_delay)
        meme_schedule_fields = meme_schedule_fields_after_quote_post(state, quote_post_epoch, delay=meme_delay)
        meme_schedule_changed_by_quote = bool(meme_schedule_fields)
        receipt = {
            "schema_version": 2,
            "post_id": str(posted_id),
            "quote_hash": quote_hash,
            "line_no": line_no,
            "source_line_number": line_no + 1,
            "text": tweet,
            "image_basename": image_basename,
            "quote_post_epoch": quote_post_epoch,
            "next_quote_post_epoch": int(quote_schedule_fields["next_quote_post_epoch"]),
            "next_meme_post_epoch": int(meme_schedule_fields.get("next_meme_post_epoch", state.get("next_meme_post_epoch", 0) or 0) or 0),
            "next_meme_schedule_mode": str(meme_schedule_fields.get("next_meme_schedule_mode", state.get("next_meme_schedule_mode", "")) or ""),
            "next_meme_schedule_date": str(meme_schedule_fields.get("next_meme_schedule_date", state.get("next_meme_schedule_date", "")) or ""),
            "meme_anchor_quote_post_epoch": int(meme_schedule_fields.get("meme_anchor_quote_post_epoch", state.get("meme_anchor_quote_post_epoch", 0) or 0) or 0),
            "meme_schedule_changed_by_quote": meme_schedule_changed_by_quote,
            "quote_history_after": sorted(set(lines_used) | {quote_hash}),
            "image_history_after": sorted(set(images_used) | {image_basename}),
        }
        write_regular_post_receipt(receipt)
    except BaseException as receipt_exc:
        log.critical(
            "Confirmed regular quote/image post_id=%s but failed writing recovery receipt; in-memory used histories remain marked",
            posted_id,
            exc_info=True,
        )
        fallback_failures: list[str] = []
        try:
            lines_used.add(quote_hash)
            images_used.add(image_basename)
            state["last_main_post_id"] = str(posted_id)
            if "quote_post_epoch" in locals():
                state["last_quote_post_epoch"] = quote_post_epoch
            state["last_regular_image_filename"] = image_basename
        except Exception:
            fallback_failures.append("in_memory_regular_post_state")
            log.critical(
                "Emergency in-memory core state update failed after confirmed regular post",
                exc_info=True,
            )
        try:
            update_regular_generated_image_spacing_state(state, image_basename)
        except Exception:
            fallback_failures.append("generated_image_spacing_state")
            log.critical(
                "Emergency generated-image spacing update failed after confirmed regular post",
                exc_info=True,
            )
        if "quote_schedule_fields" in locals():
            try:
                apply_state_fields(state, quote_schedule_fields)
            except Exception:
                fallback_failures.append("quote_schedule_state")
                log.critical(
                    "Emergency quote schedule update failed after confirmed regular post",
                    exc_info=True,
                )
        if "meme_schedule_fields" in locals():
            try:
                apply_state_fields(state, meme_schedule_fields)
            except Exception:
                fallback_failures.append("meme_schedule_state")
                log.critical(
                    "Emergency meme schedule update failed after confirmed regular post",
                    exc_info=True,
                )
        try:
            cache_tweet(
                state,
                tweet_id=str(posted_id),
                text=tweet,
                author_id=str(MY_USER_ID),
                conversation_id=str(posted_id),
                referenced_tweets=[],
                post_type="quote",
            )
            record_recent_own_post(state, str(posted_id))
        except Exception:
            log.critical("Emergency in-memory cache/recent update failed after confirmed regular post", exc_info=True)
        failures = [
            *fallback_failures,
            *emergency_persist_confirmed_regular_post(lines_used, images_used, state),
        ]
        if not confirmed_regular_emergency_representation_is_complete(
            post_id=str(posted_id),
            post_epoch=quote_post_epoch if "quote_post_epoch" in locals() else None,
            quote_hash=quote_hash,
            image_basename=image_basename,
            lines_used=lines_used,
            images_used=images_used,
            state=state,
        ):
            failures.append("incomplete_regular_post_state")
        failure_text = ", ".join(failures) if failures else "receipt"
        if failures:
            durable_barrier = latch_confirmed_post_persistence_failure(
                lane="quote_image",
                post_id=str(posted_id),
                failure_components=["regular_post_receipt", *failures],
            )
            if durable_barrier:
                end_confirmed_post_sigint_deferral(confirmed_post_sigint_guard)
                confirmed_post_sigint_guard = None
            else:
                retain_sigint_deferral_without_durable_barrier(lane="quote_image")
            raise UnrecoverableConfirmedPostPersistenceError(
                f"Confirmed regular quote/image post {posted_id} has no complete "
                f"durable recovery representation: {failure_text}"
            ) from receipt_exc
        end_confirmed_post_sigint_deferral(confirmed_post_sigint_guard)
        confirmed_post_sigint_guard = None
        if not isinstance(receipt_exc, Exception):
            raise
        raise ConfirmedPostLocalPersistenceError(
            f"Confirmed regular quote/image post {posted_id} but failed local recovery receipt/persistence: {failure_text}"
        ) from receipt_exc

    end_confirmed_post_sigint_deferral(confirmed_post_sigint_guard)
    confirmed_post_sigint_guard = None

    try:
        lines_used.add(quote_hash)
        images_used.add(image_basename)
        state["last_main_post_id"] = str(posted_id)
        state["last_quote_post_epoch"] = quote_post_epoch
        state["last_regular_image_filename"] = image_basename
        update_regular_generated_image_spacing_state(state, image_basename)
        apply_state_fields(state, quote_schedule_fields)
        apply_state_fields(state, meme_schedule_fields)
        cache_tweet(
            state,
            tweet_id=str(posted_id),
            text=tweet,
            author_id=str(MY_USER_ID),
            conversation_id=str(posted_id),
            referenced_tweets=[],
            post_type="quote",
        )
        record_recent_own_post(state, str(posted_id))
        save_regular_post_protected_state(lines_used, images_used, state, durable=True)
        enqueue_historical_context_obligation(receipt)
        remove_regular_post_receipt()
    except Exception as exc:
        log.critical("Confirmed regular quote/image post_id=%s but protected local persistence failed", posted_id, exc_info=True)
        raise ConfirmedPostLocalPersistenceError(
            f"Confirmed regular quote/image post {posted_id} but protected local persistence failed"
        ) from exc

    log_event(
        "main_post_posted",
        lane="quote_image",
        post_id=posted_id,
        line_no=line_no,
        image_no=image_no,
        image_basename=image_basename,
        image_hash=image_choice.get("image_hash"),
        image_score=image_choice.get("score"),
        quote_hash=quote_hash,
    )
    safely_process_due_historical_context_obligations(
        parent_post_id=str(posted_id),
        runtime_state=state,
    )
    log.info("Quote/image posted successfully. posted_id=%s", posted_id)


# ---------------------------------------------------------------------
# Daily meme posting
# ---------------------------------------------------------------------

def load_meme_analysis_index() -> dict[str, dict]:
    """Load meme analysis index."""
    log.debug("Loading meme analysis from %s", MEME_ANALYSIS_FILE)

    try:
        with open(MEME_ANALYSIS_FILE, "r") as f:
            data = json.load(f)
    except Exception:
        log.exception("Failed loading meme analysis file: %s", MEME_ANALYSIS_FILE)
        return {}

    index: dict[str, dict] = {}

    for item in data.get("results", []):
        filename = item.get("filename")
        path = item.get("path")
        output_filename = item.get("output_filename")

        if filename:
            index[str(filename)] = item

        if path:
            index[Path(str(path)).name] = item

        if output_filename:
            index[str(output_filename)] = item

    log.info("Loaded meme analysis entries=%d", len(index))
    return index


def original_meme_filename(shortlist_path: Path) -> str:
    """Return the original meme filename."""
    name = shortlist_path.name

    prefix_patterns = [
        r"^\d{3}_impact\d+_share\d+_grade[A-D]_post_as_is_(.+)$",
        r"^\d{3}_score\d+_(?:high|medium|low)_(.+)$",
    ]

    for pattern in prefix_patterns:
        match = re.match(pattern, name)
        if match:
            return match.group(1)

    return name


def build_meme_cache_summary(shortlist_path: Path, analysis_index: dict[str, dict]) -> str:
    """Build meme cache summary."""
    original_name = original_meme_filename(shortlist_path)
    item = analysis_index.get(original_name)

    if not item:
        log.warning(
            "No meme analysis found for shortlist file=%s original_name=%s",
            shortlist_path.name,
            original_name,
        )
        return f"Anti-socialist meme image. Original filename: {original_name}."

    description = str(item.get("grok_description", "")).strip()
    message = str(item.get("anti_socialist_message", "")).strip()
    ranking = item.get("ranking")
    shareability = str(item.get("shareability", "")).strip()

    parts: list[str] = []

    if description:
        parts.append(description)

    if message:
        parts.append(f"Anti-socialist message: {message}")

    metadata_parts: list[str] = []

    if ranking is not None:
        metadata_parts.append(f"ranking {ranking}")

    if shareability:
        metadata_parts.append(f"shareability {shareability}")

    if metadata_parts:
        parts.append("Analysis metadata: " + ", ".join(metadata_parts) + ".")

    return " ".join(parts).strip()


def list_meme_candidates() -> list[Path]:
    """List meme candidates."""
    if not MEME_DIR.exists():
        log.warning("Meme directory does not exist: %s", MEME_DIR)
        return []

    files = [
        p for p in MEME_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"}
    ]

    files.sort(key=lambda p: p.name)
    log.info("Found %d meme candidates in %s", len(files), MEME_DIR)
    return files


def choose_next_meme(state: dict) -> Path | None:
    """Select next meme."""
    candidates = list_meme_candidates()

    if not candidates:
        return None

    posted = set(str(x) for x in state.get("posted_meme_filenames", []))
    available = [p for p in candidates if p.name not in posted]

    if not available:
        log.info("All meme candidates have already been posted")

        if RESET_MEME_CYCLE_WHEN_ALL_POSTED:
            log.info("RESET_MEME_CYCLE_WHEN_ALL_POSTED=True, clearing meme history")
            state["posted_meme_filenames"] = []
            save_state(state)
            return candidates[0]

        return None

    return available[0]


def epoch_date_str(epoch: int | None = None) -> str:
    """Return the epoch date str."""
    if epoch is None:
        epoch = now_epoch()
    return datetime.fromtimestamp(int(epoch)).strftime("%Y-%m-%d")


def meme_posted_on_date(state: dict, date_text: str) -> bool:
    """Return the meme posted on date."""
    last_epoch = int(state.get("last_meme_post_epoch", 0) or 0)
    if not last_epoch:
        return False
    return epoch_date_str(last_epoch) == date_text


def next_meme_fallback_epoch(state: dict, from_epoch: int | None = None) -> int:
    """Return the next meme fallback epoch."""
    if from_epoch is None:
        from_epoch = now_epoch()

    now_dt = datetime.fromtimestamp(from_epoch)
    target = now_dt.replace(
        hour=MEME_FALLBACK_HOUR,
        minute=MEME_FALLBACK_MINUTE,
        second=0,
        microsecond=0,
    )

    target_date = target.strftime("%Y-%m-%d")

    if int(target.timestamp()) <= from_epoch or meme_posted_on_date(state, target_date):
        target = target + timedelta(days=1)

    return int(target.timestamp())


def next_meme_schedule_fields(state: dict, from_epoch: int | None = None, mode: str = "fallback") -> dict:
    """Return the next meme schedule fields."""
    next_epoch = next_meme_fallback_epoch(state, from_epoch)
    return {
        "next_meme_post_epoch": next_epoch,
        "meme_schedule_version": MEME_SCHEDULE_VERSION,
        "next_meme_schedule_mode": mode,
        "next_meme_schedule_date": epoch_date_str(next_epoch),
        "meme_anchor_quote_post_epoch": 0,
    }


def meme_delay_schedule_fields(epoch: int, mode: str) -> dict:
    """Return the meme delay schedule fields."""
    if mode not in MEME_SCHEDULE_MODES or mode in {"", "after_first_quote_after_midday"}:
        raise ValueError(f"Unsupported non-quote meme delay schedule mode: {mode}")
    return {
        "next_meme_post_epoch": int(epoch),
        "meme_schedule_version": MEME_SCHEDULE_VERSION,
        "next_meme_schedule_mode": mode,
        "next_meme_schedule_date": epoch_date_str(int(epoch)),
        "meme_anchor_quote_post_epoch": 0,
    }


def set_meme_delay_schedule(state: dict, *, epoch: int, mode: str, save: bool = True) -> None:
    """Set meme delay schedule."""
    apply_state_fields(state, meme_delay_schedule_fields(epoch, mode))
    if save:
        save_state(state)


def apply_state_fields(state: dict, fields: dict) -> None:
    """Apply state fields."""
    for key, value in fields.items():
        state[key] = value


def schedule_next_meme_post(state: dict, from_epoch: int | None = None, mode: str = "fallback", *, save: bool = True) -> None:
    """
    Schedule the fallback daily meme time. This is deliberately later than the
    preferred organic timing. If a quote/image post happens after midday first,
    maybe_schedule_meme_after_quote_post() will replace this fallback with a
    random 35-75 minute delay after that post.
    """
    fields = next_meme_schedule_fields(state, from_epoch, mode)
    apply_state_fields(state, fields)
    next_epoch = int(fields["next_meme_post_epoch"])
    if save:
        save_state(state)

    log.info(
        "Next meme fallback scheduled at %s mode=%s",
        datetime.fromtimestamp(next_epoch).strftime("%Y-%m-%d %H:%M:%S"),
        mode,
    )


def ensure_meme_schedule_initialized(state: dict) -> None:
    """Ensure meme schedule initialized."""
    if not ENABLE_DAILY_MEME_POSTS:
        return

    next_epoch = int(state.get("next_meme_post_epoch", 0) or 0)
    schedule_version = int(state.get("meme_schedule_version", 0) or 0)

    if schedule_version != MEME_SCHEDULE_VERSION:
        log.info(
            "Migrating meme schedule state to version %s: after first quote/image post after %02d:00, fallback %02d:%02d",
            MEME_SCHEDULE_VERSION,
            MEME_TRIGGER_AFTER_HOUR,
            MEME_FALLBACK_HOUR,
            MEME_FALLBACK_MINUTE,
        )
        schedule_next_meme_post(state, now_epoch(), mode="fallback_migrated")
        return

    if not next_epoch:
        log.info("No next_meme_post_epoch found; scheduling meme fallback")
        schedule_next_meme_post(state, now_epoch(), mode="fallback_startup")
        return

    log.info(
        "Existing next_meme_post_epoch=%s, human=%s, mode=%s, schedule_date=%s",
        next_epoch,
        datetime.fromtimestamp(next_epoch).strftime("%Y-%m-%d %H:%M:%S"),
        state.get("next_meme_schedule_mode"),
        state.get("next_meme_schedule_date"),
    )


def meme_schedule_fields_after_quote_post(state: dict, quote_post_epoch: int | None = None, *, delay: int | None = None) -> dict:
    """Return the meme schedule fields after quote post."""
    if not ENABLE_DAILY_MEME_POSTS:
        return {}

    if quote_post_epoch is None:
        quote_post_epoch = now_epoch()

    quote_dt = datetime.fromtimestamp(int(quote_post_epoch))
    quote_date = quote_dt.strftime("%Y-%m-%d")

    if quote_dt.hour < MEME_TRIGGER_AFTER_HOUR:
        log.info(
            "Quote/image post was before meme trigger hour %02d:00; not scheduling daily meme from it",
            MEME_TRIGGER_AFTER_HOUR,
        )
        return {}

    if meme_posted_on_date(state, quote_date):
        log.info("Daily meme already posted on %s; not scheduling another", quote_date)
        return {}

    next_epoch = int(state.get("next_meme_post_epoch", 0) or 0)
    next_mode = str(state.get("next_meme_schedule_mode", "") or "")

    if next_epoch:
        next_schedule_date = str(state.get("next_meme_schedule_date", "") or "")
        if next_schedule_date == quote_date and next_mode == "after_first_quote_after_midday":
            log.info(
                "Daily meme already scheduled from first post after midday at %s; not rescheduling",
                datetime.fromtimestamp(next_epoch).strftime("%Y-%m-%d %H:%M:%S"),
            )
            return {}

    if delay is None:
        delay = random.randint(MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS, MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS)
    scheduled_epoch = int(quote_post_epoch) + delay
    return {
        "next_meme_post_epoch": scheduled_epoch,
        "meme_schedule_version": MEME_SCHEDULE_VERSION,
        "next_meme_schedule_mode": "after_first_quote_after_midday",
        "next_meme_schedule_date": quote_date,
        "meme_anchor_quote_post_epoch": int(quote_post_epoch),
    }


def maybe_schedule_meme_after_quote_post(state: dict, quote_post_epoch: int | None = None, *, save: bool = True) -> None:
    """Attempt to schedule meme after quote post."""
    fields = meme_schedule_fields_after_quote_post(state, quote_post_epoch)
    if not fields:
        return
    apply_state_fields(state, fields)
    if save:
        save_state(state)

    if quote_post_epoch is None:
        quote_post_epoch = now_epoch()
    scheduled_epoch = int(fields["next_meme_post_epoch"])
    delay = scheduled_epoch - int(quote_post_epoch)

    log.info(
        "Daily meme scheduled for %s: %d seconds after first quote/image post after %02d:00",
        datetime.fromtimestamp(scheduled_epoch).strftime("%Y-%m-%d %H:%M:%S"),
        delay,
        MEME_TRIGGER_AFTER_HOUR,
    )


def run_daily_meme_stage(stage: str, operation):
    """Run one meme stage while preserving the original exception type."""
    try:
        return operation()
    except Exception as exc:
        log.error(
            "Daily meme stage failed. stage=%s error_type=%s error=%s",
            stage,
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        log_event(
            "daily_meme_failure",
            status="failed",
            stage=stage,
            error_type=type(exc).__name__,
            reason=str(exc)[:500],
        )
        raise


def require_valid_meme_post_id(posted_id: object) -> None:
    """Reject a meme response that lacks a confirmed numeric post identity."""
    if not valid_post_id(posted_id):
        raise RuntimeError(
            "Daily meme post did not return a valid post id; meme state unchanged"
        )


def post_next_meme(state: dict) -> None:
    """Select and post the next daily meme transactionally."""
    log.info("Starting daily meme post cycle")
    run_daily_meme_stage("remote_write_barrier", block_if_ambiguous_remote_post)

    def validate_receipt_barriers() -> None:
        if both_main_post_receipts_exist():
            log.critical(
                "Both regular and meme confirmed-post receipts exist; refusing meme posting until manually inspected: %s %s",
                REGULAR_POST_RECEIPT_FILE,
                MEME_POST_RECEIPT_FILE,
            )
            raise InvalidMemePostReceipt("Both main-post receipts exist; manual recovery required")

    run_daily_meme_stage("receipt_barrier", validate_receipt_barriers)
    if run_daily_meme_stage(
        "meme_receipt_reconciliation",
        lambda: reconcile_meme_post_receipt(state),
    ):
        log.warning("Reconciled meme post receipt; not creating a second meme post in the same call")
        return
    run_daily_meme_stage(
        "main_receipt_barrier",
        block_if_unresolved_regular_post_receipt,
    )

    meme_path = run_daily_meme_stage(
        "meme_eligibility_and_asset_selection",
        lambda: choose_next_meme(state),
    )

    if not meme_path:
        log.info("No meme available to post")
        run_daily_meme_stage(
            "schedule_update",
            lambda: schedule_next_meme_post(state),
        )
        return

    analysis_index = run_daily_meme_stage(
        "x_request_preparation",
        load_meme_analysis_index,
    )
    image_summary = run_daily_meme_stage(
        "x_request_preparation",
        lambda: build_meme_cache_summary(meme_path, analysis_index),
    )

    log.info("Posting meme image: %s", meme_path)
    log.debug("Meme image summary for cache: %r", image_summary)

    media_id = run_daily_meme_stage(
        "media_upload",
        lambda: upload_media(str(meme_path)),
    )

    confirmed_post_sigint_guard = begin_confirmed_post_sigint_deferral()
    try:
        response = run_daily_meme_stage(
            "x_post_request",
            lambda: create_post(
                text=MEME_POST_TEXT,
                media_ids=[media_id],
                reply_to_id=None,
                made_with_ai=False,
            ),
        )

        posted_id = response.get("data", {}).get("id") if isinstance(response, dict) else None
        log.debug("Posted meme id=%s", posted_id)

        run_daily_meme_stage(
            "x_post_response_validation",
            lambda: require_valid_meme_post_id(posted_id),
        )
    except BaseException as remote_exc:
        if (
            isinstance(remote_exc, AmbiguousRemotePostOutcome)
            and _AMBIGUOUS_REMOTE_POST_SEEN
            and not durable_remote_write_safety_marker_exists()
        ):
            retain_sigint_deferral_without_durable_barrier(lane="daily_meme")
        else:
            end_confirmed_post_sigint_deferral(confirmed_post_sigint_guard)
            confirmed_post_sigint_guard = None
        raise

    try:
        meme_post_epoch = now_epoch()
        apply_state_fields(state, meme_delay_schedule_fields(int(meme_post_epoch) + 3600, "delayed_exception"))
        planned_state = copy.deepcopy(state)
        planned_state["last_meme_post_epoch"] = meme_post_epoch
        planned_posted = set(
            str(x) for x in planned_state.get("posted_meme_filenames", [])
        )
        planned_posted.add(meme_path.name)
        planned_state["posted_meme_filenames"] = sorted(planned_posted)
        meme_schedule_fields = next_meme_schedule_fields(planned_state, meme_post_epoch, mode="fallback")
        receipt = {
            "schema_version": 1,
            "post_id": str(posted_id),
            "meme_basename": meme_path.name,
            "meme_post_epoch": meme_post_epoch,
            "next_meme_post_epoch": int(meme_schedule_fields["next_meme_post_epoch"]),
            "next_meme_schedule_mode": str(meme_schedule_fields.get("next_meme_schedule_mode") or "fallback"),
            "text": MEME_POST_TEXT,
            "image_summary": image_summary,
        }
        write_meme_post_receipt(receipt)
    except BaseException as receipt_exc:
        log.critical(
            "Confirmed meme post_id=%s but stage=meme_receipt_creation failed; attempting direct durable state save",
            posted_id,
            exc_info=True,
        )
        log_event(
            "daily_meme_failure",
            status="failed",
            stage="meme_receipt_creation",
            post_id=str(posted_id),
            error_type=type(receipt_exc).__name__,
            reason=str(receipt_exc)[:500],
        )
        emergency_state_write_succeeded = False
        emergency_state_complete = False
        try:
            state["last_main_post_id"] = str(posted_id)
            if "meme_post_epoch" in locals():
                state["last_meme_post_epoch"] = meme_post_epoch
            posted = set(str(x) for x in state.get("posted_meme_filenames", []))
            posted.add(meme_path.name)
            state["posted_meme_filenames"] = sorted(posted)
            if "meme_schedule_fields" in locals():
                apply_state_fields(state, meme_schedule_fields)
            try:
                cache_tweet(
                    state,
                    tweet_id=str(posted_id),
                    text=MEME_POST_TEXT,
                    author_id=str(MY_USER_ID),
                    conversation_id=str(posted_id),
                    referenced_tweets=[],
                    image_summary=image_summary,
                    post_type="daily_meme",
                )
                record_recent_own_post(state, str(posted_id))
            except Exception:
                log.critical("Emergency in-memory cache/recent update failed after confirmed meme post", exc_info=True)
            save_state(state, durable=True)
            emergency_state_write_succeeded = True
            emergency_state_complete = confirmed_meme_emergency_representation_is_complete(
                post_id=str(posted_id),
                post_epoch=meme_post_epoch if "meme_post_epoch" in locals() else None,
                meme_basename=meme_path.name,
                state=state,
            )
        except Exception as emergency_exc:
            if isinstance(emergency_exc, StateBackupWriteError) and json_file_matches(STATE_FILE, state):
                emergency_state_write_succeeded = True
                emergency_state_complete = confirmed_meme_emergency_representation_is_complete(
                    post_id=str(posted_id),
                    post_epoch=meme_post_epoch if "meme_post_epoch" in locals() else None,
                    meme_basename=meme_path.name,
                    state=state,
                )
                log.warning(
                    "Emergency canonical state was committed after confirmed meme post, "
                    "but a later backup/finalisation step failed; using the canonical "
                    "durable state as the recovery representation",
                    exc_info=True,
                )
            else:
                log.critical("Emergency state persistence failed after confirmed meme post", exc_info=True)
        if not emergency_state_complete:
            incomplete_component = (
                "incomplete_meme_post_state"
                if emergency_state_write_succeeded
                else "state"
            )
            durable_barrier = latch_confirmed_post_persistence_failure(
                lane="daily_meme",
                post_id=str(posted_id),
                failure_components=["meme_post_receipt", incomplete_component],
            )
            if durable_barrier:
                end_confirmed_post_sigint_deferral(confirmed_post_sigint_guard)
                confirmed_post_sigint_guard = None
            else:
                retain_sigint_deferral_without_durable_barrier(lane="daily_meme")
            raise UnrecoverableConfirmedPostPersistenceError(
                f"Confirmed meme post {posted_id} has no complete durable recovery representation"
            ) from receipt_exc
        end_confirmed_post_sigint_deferral(confirmed_post_sigint_guard)
        confirmed_post_sigint_guard = None
        if not isinstance(receipt_exc, Exception):
            raise
        raise ConfirmedPostLocalPersistenceError(
            f"Confirmed meme post {posted_id} but failed writing recovery receipt"
        ) from receipt_exc

    end_confirmed_post_sigint_deferral(confirmed_post_sigint_guard)
    confirmed_post_sigint_guard = None

    try:
        state["last_main_post_id"] = str(posted_id)
        state["last_meme_post_epoch"] = meme_post_epoch
        posted = set(str(x) for x in state.get("posted_meme_filenames", []))
        posted.add(meme_path.name)
        state["posted_meme_filenames"] = sorted(posted)
        apply_state_fields(state, meme_schedule_fields)
        cache_tweet(
            state,
            tweet_id=str(posted_id),
            text=MEME_POST_TEXT,
            author_id=str(MY_USER_ID),
            conversation_id=str(posted_id),
            referenced_tweets=[],
            image_summary=image_summary,
            post_type="daily_meme",
        )
        record_recent_own_post(state, str(posted_id))
        save_state(state, durable=True)
    except Exception as exc:
        log.critical(
            "Confirmed meme post_id=%s but stage=durable_state_and_schedule_update failed; receipt remains for reconciliation",
            posted_id,
            exc_info=True,
        )
        log_event(
            "daily_meme_failure",
            status="failed",
            stage="durable_state_and_schedule_update",
            post_id=str(posted_id),
            error_type=type(exc).__name__,
            reason=str(exc)[:500],
        )
        raise ConfirmedPostLocalPersistenceError(f"Confirmed meme post {posted_id} but durable state save failed")
    try:
        remove_meme_post_receipt()
    except Exception as exc:
        log.critical(
            "Confirmed meme post_id=%s but stage=meme_receipt_confirmation failed after durable state save",
            posted_id,
            exc_info=True,
        )
        log_event(
            "daily_meme_failure",
            status="failed",
            stage="meme_receipt_confirmation",
            post_id=str(posted_id),
            error_type=type(exc).__name__,
            reason=str(exc)[:500],
        )
        raise ConfirmedPostLocalPersistenceError(f"Confirmed meme post {posted_id} but receipt removal failed") from exc

    log_event("main_post_posted", lane="daily_meme", post_id=posted_id, filename=meme_path.name)
    log.info("Daily meme posted successfully. posted_id=%s file=%s", posted_id, meme_path.name)



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
    """Return whether is probably spam or not worth replying."""
    low = text.lower().strip()
    log.debug("Spam check for text=%r", text)

    if len(low) < 4:
        log.info("Ignoring post: too short")
        return True

    for pattern in SPAMMY_PATTERNS:
        if re.search(pattern, low):
            log.info("Ignoring post: matched spam pattern %s", pattern)
            return True

    if text.count("!") >= 5:
        log.info("Ignoring post: too many exclamation marks")
        return True

    words = low.split()
    if words:
        link_or_mention_count = sum(1 for w in words if w.startswith("@") or w.startswith("http"))
        ratio = link_or_mention_count / len(words)
        log.debug("Post link/mention ratio=%s", ratio)
        if ratio > 0.5:
            log.info("Ignoring post: mostly links/mentions")
            return True

    log.debug("Post passed spam check")
    return False


def xai_user_content(user_prompt: str, media_context: dict | None = None) -> str | list[dict]:
    """Return the xAI user content."""
    if not media_context or media_context.get("status") == "none":
        return user_prompt

    if media_context.get("status") == "unavailable":
        photos_expected = int(media_context.get("photos_expected", 0) or 0)
        return (
            f"{user_prompt}\n\n"
            "ATTACHED MEDIA:\n"
            f"The candidate contains {photos_expected} native X photo attachment(s), "
            "but their contents could not be made available to the model. "
            "Do not invent image contents. If the text depends on the missing image, decline using the required "
            "structured schema: proposer mode=no_reply or reviewer verdict=reject."
        )

    photos = media_context.get("photos", [])
    if not isinstance(photos, list) or not photos:
        return user_prompt

    content: list[dict] = [{"type": "text", "text": user_prompt}]
    for photo in photos[:MAX_REPLY_CONTEXT_PHOTOS]:
        if not isinstance(photo, dict):
            continue
        url = str(photo.get("url") or "").strip()
        if not url:
            continue
        content.append({"type": "image_url", "image_url": {"url": url}})
    return content if len(content) > 1 else user_prompt


def response_text_for_classification(response: requests.Response) -> str:
    """Return the response text for classification."""
    try:
        data = response.json()
    except Exception:
        return response.text or ""
    extracted = extract_error_text_for_classification(data)
    if extracted:
        return extracted
    return ""


def extract_error_text_for_classification(data: object) -> str:
    """Extract error text for classification."""
    parts: list[str] = []

    def add(value: object) -> None:
        if value is None:
            return
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, (int, float, bool)):
            parts.append(str(value))

    def collect_error_object(value: object) -> None:
        if isinstance(value, str):
            add(value)
            return
        if isinstance(value, list):
            for item in value:
                collect_error_object(item)
            return
        if not isinstance(value, dict):
            return
        for key in ("message", "detail", "type", "code", "param", "title", "reason"):
            if key in value:
                add(value.get(key))
        for key in ("error", "errors"):
            if key in value:
                collect_error_object(value.get(key))

    if isinstance(data, str):
        add(data)
    elif isinstance(data, dict):
        for key in ("error", "errors", "message", "detail"):
            if key in data:
                if key in {"error", "errors"}:
                    collect_error_object(data.get(key))
                else:
                    add(data.get(key))
    elif isinstance(data, list):
        for item in data:
            collect_error_object(item)

    return " ".join(part for part in parts if part)


def term_or_phrase_in_text(term: str, text: str) -> bool:
    """Return the term or phrase in text."""
    escaped = re.escape(term)
    escaped = escaped.replace(r"\ ", r"\s+")
    return re.search(rf"(?<![A-Za-z0-9_]){escaped}(?![A-Za-z0-9_])", text) is not None


def any_term_or_phrase_in_text(terms: list[str], text: str) -> bool:
    """Return the any term or phrase in text."""
    return any(term_or_phrase_in_text(term, text) for term in terms)


def xai_error_is_multimodal_input_rejection(response: requests.Response) -> bool:
    """Return whether xAI error is multimodal input rejection."""
    if response.status_code not in {400, 415, 422}:
        return False

    body = response_text_for_classification(response).lower()
    if not body:
        return False

    image_markers = [
        "image",
        "image input",
        "image_url",
        "image url",
        "image content",
        "image attachment",
        "multimodal",
        "vision",
    ]
    rejection_markers = [
        "reject",
        "rejected",
        "rejection",
        "invalid",
        "unsupported",
        "not supported",
        "not allowed",
        "cannot",
        "can't",
    ]

    return any_term_or_phrase_in_text(image_markers, body) and any_term_or_phrase_in_text(
        rejection_markers,
        body,
    )


def recent_auto_reply_texts(state: dict, limit: int = 20) -> list[str]:
    """Return the recent auto reply texts."""
    cache = state.get("tweet_cache", {})
    if not isinstance(cache, dict):
        return []
    rows = [
        value for value in cache.values()
        if isinstance(value, dict)
        and value.get("post_type") == "auto_reply"
        and str(value.get("author_id") or "") == str(MY_USER_ID)
        and str(value.get("text") or "").strip()
    ]
    rows.sort(key=lambda value: (int(value.get("cached_epoch", 0) or 0), str(value.get("id") or "")), reverse=True)
    return [str(value["text"]) for value in rows[:limit]]


def pending_ai_reply_draft_key(target_id: object, candidate_source: object) -> str:
    """Return the V3 pending AI reply draft key."""
    return f"{str(candidate_source or 'mention')}:{str(target_id)}"


def store_pending_ai_reply(
    state: dict,
    target_id: str,
    candidate_source: str,
    reply: str,
    *,
    context: dict[str, object],
) -> bool:
    """Store a reviewer-approved V3 draft after complete local revalidation."""
    from reply_strategy import AIReply, validate_persisted_draft

    if not isinstance(reply, AIReply):
        return False
    try:
        validated = validate_persisted_draft(
            reply.draft_record,
            context=context,
            config=ai_first_reply_strategy,
            repository=reply_evidence_repository(),
            maximum_reply_length=MAX_REPLY_CHARS,
        )
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        log.warning(
            "Refusing invalid V3 pending AI reply draft target_id=%s source=%s reason=%s",
            target_id,
            candidate_source,
            exc,
        )
        return False
    if (
        validated["target_id"] != str(target_id)
        or validated["candidate_source"] != str(candidate_source)
        or validated["proposed_reply"] != str(reply)
    ):
        return False
    drafts = state.setdefault("pending_ai_reply_drafts", {})
    if not isinstance(drafts, dict):
        return False
    drafts[pending_ai_reply_draft_key(target_id, candidate_source)] = copy.deepcopy(validated)
    while len(drafts) > 100:
        drafts.pop(next(iter(drafts)))
    return True


def pending_ai_reply(
    state: dict,
    target_id: str,
    candidate_source: str,
    *,
    context: dict[str, object],
    recent_replies: list[str] | None = None,
) -> str | None:
    """Return a locally revalidated reviewer-approved V3 draft."""
    from reply_strategy import AIReply, validate_persisted_draft

    drafts = state.get("pending_ai_reply_drafts", {})
    if not isinstance(drafts, dict):
        return None
    record = drafts.get(pending_ai_reply_draft_key(target_id, candidate_source))
    try:
        validated = validate_persisted_draft(
            record,
            context=context,
            config=ai_first_reply_strategy,
            repository=reply_evidence_repository(),
            maximum_reply_length=MAX_REPLY_CHARS,
            recent_replies=recent_replies,
        )
    except ReplyEvidenceUnavailable:
        raise
    except (KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
        if record is not None:
            log.warning(
                "Discarding invalid V3 pending AI reply draft target_id=%s source=%s reason=%s",
                target_id,
                candidate_source,
                exc,
            )
            drafts.pop(pending_ai_reply_draft_key(target_id, candidate_source), None)
            if not drafts:
                state.pop("pending_ai_reply_drafts", None)
        return None
    metadata = {
        "strategy_version": validated["strategy_version"],
        "mode": validated["mode"],
        "tone": validated["tone"],
        "confidence": "approved",
        "factual_claim_count": len(validated["factual_claims"]),
        "evidence_ids": validated["evidence_ids"],
        "reviewer_verdict": validated["reviewer_verdict"],
        "model_call_count": validated["model_call_count"],
        "revision_count": validated["revision_count"],
    }
    return AIReply(validated["proposed_reply"], copy.deepcopy(validated), metadata)


def clear_pending_ai_reply(state: dict, target_id: str, candidate_source: str) -> None:
    """Clear one V3 pending reply draft after a terminal outcome or reconciliation."""
    drafts = state.get("pending_ai_reply_drafts")
    if not isinstance(drafts, dict):
        return
    drafts.pop(pending_ai_reply_draft_key(target_id, candidate_source), None)
    if not drafts:
        state.pop("pending_ai_reply_drafts", None)


def log_ai_reply_dry_run(*, context: dict[str, object], reply: str, lane: str, target_id: str) -> None:
    """Emit bounded AI-first dry-run telemetry."""
    metadata = getattr(reply, "pipeline_metadata", None)
    if not isinstance(metadata, dict):
        return
    log_event(
        "ai_reply_pipeline_dry_run",
        lane=lane,
        target_id=target_id,
        contribution_hash=hashlib.sha256(
            str(context.get("incoming_contribution") or "").encode("utf-8")
        ).hexdigest(),
        strategy_version=metadata.get("strategy_version"),
        mode=metadata.get("mode"),
        tone=metadata.get("tone"),
        factual_claim_count=metadata.get("factual_claim_count"),
        evidence_ids=metadata.get("evidence_ids", []),
        reviewer_verdict=metadata.get("reviewer_verdict"),
        model_call_count=metadata.get("model_call_count"),
        revision_count=metadata.get("revision_count"),
        production_post_created=False,
    )


def log_ai_reply_posting_outcome(
    *,
    reply: str,
    status: str,
    lane: str,
    target_id: str,
    failure_reason: str,
) -> None:
    """Emit posting outcome telemetry without exposing prompts or model reasoning."""
    metadata = getattr(reply, "pipeline_metadata", None)
    if not isinstance(metadata, dict):
        return
    log_event(
        "ai_reply_pipeline_outcome",
        status=status,
        lane=lane,
        target_id=target_id,
        reply_post_id="",
        strategy_version=metadata.get("strategy_version"),
        mode=metadata.get("mode"),
        tone=metadata.get("tone"),
        factual_claim_count=metadata.get("factual_claim_count"),
        evidence_ids=metadata.get("evidence_ids", []),
        reviewer_verdict=metadata.get("reviewer_verdict"),
        model_call_count=metadata.get("model_call_count"),
        revision_count=metadata.get("revision_count"),
        failure_reason=failure_reason,
    )


def xai_structured_reply_call(
    *,
    stage: str,
    model: str,
    system_prompt: str,
    user_prompt: str,
    response_schema: dict,
    timeout_seconds: int,
    max_output_tokens: int,
    media_context: dict | None,
) -> object:
    """Send one isolated structured xAI call for an AI-first pipeline stage."""
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": xai_user_content(user_prompt, media_context)},
        ],
        "temperature": 0,
        "max_tokens": max_output_tokens,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": f"ai_reply_{stage}",
                "strict": True,
                "schema": response_schema,
            },
        },
    }
    log.info("Calling AI-first reply stage=%s model=%s", stage, model)
    log_json_debug("xAI structured reply request", redact_xai_payload_for_log(payload))
    require_remote_operation_unpaused(f"xAI reply stage {stage}")
    try:
        response = requests.post(
            f"{XAI_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {XAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=timeout_seconds,
        )
    except requests.RequestException as exc:
        log.exception("xAI reply stage=%s failed before receiving a response", stage)
        raise ApiError(str(exc), service="xai") from exc
    if response.status_code >= 400:
        if media_context and media_context.get("status") == "supplied" and xai_error_is_multimodal_input_rejection(response):
            log.warning(
                "AI-first reply stage=%s rejected supplied media; failing closed without an extra model call",
                stage,
            )
        raise ApiError(
            f"xAI reply stage {stage} error {response.status_code}: {response.text}",
            service="xai",
            status_code=response.status_code,
        )
    try:
        data = response.json()
    except json.JSONDecodeError as exc:
        raise ApiError(
            f"xAI reply stage {stage} returned non-JSON: {response.text[:500]}",
            service="xai",
        ) from exc
    if not isinstance(data, dict):
        raise ApiError(
            f"xAI reply stage {stage} response must be an object",
            service="xai",
        )
    usage = data.get("usage")
    if usage:
        log.info("xAI reply stage=%s usage=%s", stage, usage)
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ApiError(
            f"xAI reply stage {stage} response shape is invalid",
            service="xai",
        ) from exc
    if not isinstance(content, (str, dict)):
        raise ApiError(
            f"xAI reply stage {stage} content must be structured JSON",
            service="xai",
        )
    return content


def generate_ai_first_reply(
    context: dict[str, object],
    media_context: dict | None = None,
    *,
    recent_replies: list[str] | None = None,
    evaluation_outcome: dict | None = None,
) -> str | None:
    """Run the sole conversational reply strategy and return only approved prose."""
    from reply_strategy import STRATEGY_VERSION, run_reply_pipeline

    lane = str(context.get("lane") or "")
    target_id = str(context.get("target_id") or "")
    log.info("Running AI-first reply pipeline lane=%s target_id=%s", lane, target_id)
    result = run_reply_pipeline(
        context=context,
        config=ai_first_reply_strategy,
        repository=reply_evidence_repository(),
        transport=xai_structured_reply_call,
        maximum_reply_length=MAX_REPLY_CHARS,
        recent_replies=recent_replies,
        media_context=media_context,
    )
    if result.reply is None:
        operational_failure = result.status not in {"no_reply", "disabled"}
        log_method = log.error if operational_failure else log.info
        log_method(
            "AI-first reply pipeline ended status=%s lane=%s target_id=%s reason=%s calls=%d revisions=%d",
            result.status,
            lane,
            target_id,
            result.reason,
            result.model_call_count,
            result.revision_count,
        )
        log_event(
            "ai_reply_pipeline_failure" if operational_failure else "ai_reply_pipeline_decision",
            lane=lane,
            target_id=target_id,
            status=result.status,
            strategy_version=STRATEGY_VERSION,
            mode="no_reply" if result.status == "no_reply" else "unavailable",
            tone="none",
            factual_claim_count=0,
            evidence_ids=[],
            reviewer_verdict="not_approved",
            reason=result.reason,
            model_call_count=result.model_call_count,
            revision_count=result.revision_count,
        )
        if evaluation_outcome is not None:
            evaluation_outcome.update({"status": result.status, "reason": result.reason})
        if operational_failure:
            raise ApiError(
                f"AI-first reply pipeline operational failure: {result.reason}",
                service="xai",
            )
        return None

    metadata = result.reply.pipeline_metadata
    log_event(
        "ai_reply_pipeline_decision",
        lane=lane,
        target_id=target_id,
        status="approved",
        strategy_version=metadata["strategy_version"],
        mode=metadata["mode"],
        tone=metadata["tone"],
        factual_claim_count=metadata["factual_claim_count"],
        evidence_ids=metadata["evidence_ids"],
        reviewer_verdict=metadata["reviewer_verdict"],
        model_call_count=metadata["model_call_count"],
        revision_count=metadata["revision_count"],
    )
    if evaluation_outcome is not None:
        evaluation_outcome.update({"status": "approved", "reason": "reviewer_approved"})
    return result.reply


def terminal_reply_evaluation(state: dict, target_id: str) -> dict | None:
    """Return the terminal reply evaluation."""
    records = state.get("reply_evaluation_records", {})
    if not isinstance(records, dict):
        return None
    record = records.get(str(target_id))
    if isinstance(record, dict) and record.get("outcome") in {"no_reply", "reply_not_permitted"}:
        return record
    return None


def record_terminal_reply_evaluation(
    state: dict,
    *,
    target_id: str,
    lane: str,
    reason: str,
    outcome: str = "no_reply",
) -> None:
    """Record terminal reply evaluation."""
    if outcome not in {"no_reply", "reply_not_permitted"}:
        raise ValueError(f"Unsupported terminal reply outcome: {outcome}")
    records = state.get("reply_evaluation_records", {})
    if not isinstance(records, dict):
        records = {}
    records = dict(records)
    records[str(target_id)] = {
        "target_id": str(target_id),
        "lane": str(lane),
        "outcome": outcome,
        "reason": str(reason or "model_selected_no_reply"),
        "evaluated_epoch": now_epoch(),
    }
    state["reply_evaluation_records"] = records


def ai_reply_receipt_draft_is_valid(data: dict, text: object) -> bool:
    """Return whether a receipt carries a currently valid approved V3 draft."""
    from reply_strategy import validate_persisted_draft

    context = data.get("reply_context")
    draft = data.get("ai_reply_draft")
    if not isinstance(context, dict) or not isinstance(draft, dict):
        return False
    try:
        validated = validate_persisted_draft(
            draft,
            context=context,
            config=ai_first_reply_strategy,
            repository=reply_evidence_repository(),
            maximum_reply_length=MAX_REPLY_CHARS,
        )
    except (KeyError, OSError, RuntimeError, TypeError, ValueError):
        return False
    return validated["proposed_reply"] == text


def mention_pagination_provenance_is_valid(value: object) -> bool:
    """Validate the exact mention continuation bound to a reply receipt."""
    if not isinstance(value, dict):
        return False
    if set(value) != {"base_since_id", "next_token"}:
        return False
    base_since_id = value.get("base_since_id")
    next_token = value.get("next_token")
    if not isinstance(base_since_id, str):
        return False
    if base_since_id and not base_since_id.isdigit():
        return False
    if (
        not isinstance(next_token, str)
        or not next_token
        or next_token != next_token.strip()
        or any(character.isspace() for character in next_token)
    ):
        return False
    return True


def _conversational_reply_receipt_is_semantically_valid(
    data: dict,
    *,
    lifecycle_state: str,
) -> bool:
    """Validate one prepared or confirmed conversational-reply receipt."""
    if not isinstance(data, dict):
        return False
    if lifecycle_state not in {"sending", "confirmed"}:
        return False
    schema_version = data.get("schema_version")
    if type(schema_version) is not int or schema_version not in {2, 3, 4}:
        return False
    if schema_version == 2:
        if lifecycle_state != "confirmed" or "lifecycle_state" in data:
            return False
    elif data.get("lifecycle_state") != lifecycle_state:
        return False
    if not valid_post_id(data.get("target_id")):
        return False
    if lifecycle_state == "confirmed" and not valid_post_id(
        data.get("reply_post_id")
    ):
        return False
    if lifecycle_state == "sending" and "reply_post_id" in data:
        return False
    author_id = data.get("author_id")
    if author_id is None or isinstance(author_id, (dict, list)):
        return False
    source = str(data.get("candidate_source") or "")
    if source not in {"mention", "hot_post_reply", "quote_tweet"}:
        return False
    reply_epoch = receipt_int(data.get("reply_epoch"))
    if reply_epoch is None or not valid_receipt_epoch(reply_epoch):
        return False
    if schema_version == 4:
        attempt_epoch = receipt_int(data.get("attempt_epoch"))
        if attempt_epoch is None or not valid_receipt_epoch(attempt_epoch):
            return False
        if lifecycle_state == "sending":
            if "confirmation_epoch" in data or reply_epoch != attempt_epoch:
                return False
            effective_epoch = attempt_epoch
        else:
            confirmation_epoch = receipt_int(data.get("confirmation_epoch"))
            if (
                confirmation_epoch is None
                or not valid_receipt_epoch(confirmation_epoch)
                or confirmation_epoch < attempt_epoch
                or reply_epoch != confirmation_epoch
            ):
                return False
            effective_epoch = confirmation_epoch
        expected_date = safe_epoch_date_str(effective_epoch)
        if expected_date is None or data.get("daily_reply_date") != expected_date:
            return False
        if source == "quote_tweet":
            if data.get("daily_quote_reply_date") != expected_date:
                return False
        elif "daily_quote_reply_date" in data:
            return False
    if "mention_pagination" in data:
        mention_pagination = data.get("mention_pagination")
        if schema_version not in {3, 4} or source != "mention":
            return False
        if not mention_pagination_provenance_is_valid(mention_pagination):
            return False
    text = data.get("reply_text")
    if not isinstance(text, str) or not text:
        return False
    conversation_id = data.get("conversation_id")
    if not valid_post_id(conversation_id):
        return False
    if schema_version in {2, 3}:
        daily_reply_date = data.get("daily_reply_date")
        if daily_reply_date is not None and not isinstance(daily_reply_date, str):
            return False
        daily_quote_reply_date = data.get("daily_quote_reply_date")
        if daily_quote_reply_date is not None and not isinstance(
            daily_quote_reply_date,
            str,
        ):
            return False
    original_post_id = data.get("original_post_id")
    if original_post_id is not None and isinstance(original_post_id, (dict, list)):
        return False
    context = data.get("reply_context")
    if not isinstance(context, dict):
        return False
    if (
        str(context.get("target_id") or "") != str(data["target_id"])
        or str(context.get("thread_id") or "") != str(conversation_id)
        or context.get("lane") != source
    ):
        return False
    if source == "quote_tweet":
        quoted_post = context.get("quoted_post")
        if (
            not valid_post_id(original_post_id)
            or not isinstance(quoted_post, dict)
            or str(quoted_post.get("post_id") or "") != str(original_post_id)
        ):
            return False
    elif original_post_id is not None:
        return False
    if not ai_reply_receipt_draft_is_valid(data, text):
        return False
    clarification = data.get("clarification_reply")
    if clarification is not None:
        if source not in {"mention", "hot_post_reply"} or not isinstance(clarification, dict):
            return False
        if set(clarification) != {
            "thread_id", "prior_bot_reply_id", "original_question_id", "trigger",
        }:
            return False
        if any(
            not valid_post_id(clarification.get(field))
            for field in ("thread_id", "prior_bot_reply_id", "original_question_id")
        ):
            return False
        if clarification.get("trigger") not in {"explicit_correction", "restated_question"}:
            return False
        if str(clarification.get("thread_id")) != str(conversation_id):
            return False
        draft = data.get("ai_reply_draft")
        if not isinstance(draft, dict) or draft.get("mode") != "direct_factual_answer":
            return False
    return True


def confirmed_reply_receipt_is_semantically_valid(data: dict) -> bool:
    """Return whether a confirmed-reply receipt is internally consistent."""
    return _conversational_reply_receipt_is_semantically_valid(
        data,
        lifecycle_state="confirmed",
    )


def sending_reply_receipt_is_semantically_valid(data: dict) -> bool:
    """Return whether a pre-send conversational-reply receipt is complete."""
    return _conversational_reply_receipt_is_semantically_valid(
        data,
        lifecycle_state="sending",
    )


def load_confirmed_reply_receipt() -> tuple[str, dict | None]:
    """Load confirmed reply receipt."""
    try:
        with open(CONFIRMED_REPLY_RECEIPT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return "absent", None
    except Exception:
        log.exception(
            "Malformed confirmed-reply receipt blocks auto-reply processing until repaired: %s",
            CONFIRMED_REPLY_RECEIPT_FILE,
        )
        return "invalid", None
    if not isinstance(data, dict):
        log.critical(
            "Invalid confirmed-reply receipt blocks auto-reply processing until repaired: %s",
            CONFIRMED_REPLY_RECEIPT_FILE,
        )
        return "invalid", None
    if sending_reply_receipt_is_semantically_valid(data):
        return "sending", data
    if not confirmed_reply_receipt_is_semantically_valid(data):
        log.critical(
            "Semantically invalid confirmed-reply receipt blocks auto-reply processing until repaired: %s",
            CONFIRMED_REPLY_RECEIPT_FILE,
        )
        return "invalid", data
    return "valid", data


def write_confirmed_reply_receipt(receipt: dict) -> None:
    """Write confirmed reply receipt."""
    if CONFIRMED_REPLY_RECEIPT_FILE.exists():
        raise InvalidConfirmedReplyReceipt(
            f"Refusing to overwrite unresolved confirmed-reply receipt: {CONFIRMED_REPLY_RECEIPT_FILE}"
        )
    if not confirmed_reply_receipt_is_semantically_valid(receipt):
        raise RuntimeError("Internal error: generated confirmed-reply receipt failed semantic validation")
    atomic_write_json(CONFIRMED_REPLY_RECEIPT_FILE, receipt, durable=True)
    log.warning(
        "Wrote confirmed reply receipt pending local reconciliation source=%s target_id=%s reply_post_id=%s path=%s",
        receipt.get("candidate_source", "mention"),
        receipt.get("target_id"),
        receipt.get("reply_post_id"),
        CONFIRMED_REPLY_RECEIPT_FILE,
    )


def write_sending_reply_receipt(receipt: dict) -> None:
    """Durably record a reply transaction before its remote create request."""
    if CONFIRMED_REPLY_RECEIPT_FILE.exists():
        raise InvalidConfirmedReplyReceipt(
            "Refusing to overwrite unresolved conversational-reply receipt: "
            f"{CONFIRMED_REPLY_RECEIPT_FILE}"
        )
    if not sending_reply_receipt_is_semantically_valid(receipt):
        raise RuntimeError(
            "Internal error: generated sending-reply receipt failed validation"
        )
    atomic_write_json(CONFIRMED_REPLY_RECEIPT_FILE, receipt, durable=True)
    log.warning(
        "Wrote conversational reply sending receipt source=%s target_id=%s path=%s",
        receipt.get("candidate_source", "mention"),
        receipt.get("target_id"),
        CONFIRMED_REPLY_RECEIPT_FILE,
    )


def bind_conversational_reply_attempt_time(receipt_template: dict) -> dict:
    """Bind a schema-v4 reply template to its immediately pre-send time."""
    if (
        not isinstance(receipt_template, dict)
        or receipt_template.get("schema_version") != 4
        or receipt_template.get("lifecycle_state") != "sending"
    ):
        raise RuntimeError("A reply attempt time can only bind a schema-v4 sending template")
    timing_fields = {
        "attempt_epoch",
        "confirmation_epoch",
        "reply_epoch",
        "daily_reply_date",
        "daily_quote_reply_date",
    }
    if timing_fields.intersection(receipt_template):
        raise RuntimeError("Reply attempt template already contains timing fields")
    attempt_epoch = now_epoch()
    attempt_date = epoch_date_str(attempt_epoch)
    prepared = {
        **receipt_template,
        "attempt_epoch": attempt_epoch,
        "reply_epoch": attempt_epoch,
        "daily_reply_date": attempt_date,
    }
    if receipt_template.get("candidate_source") == "quote_tweet":
        prepared["daily_quote_reply_date"] = attempt_date
    if not sending_reply_receipt_is_semantically_valid(prepared):
        raise RuntimeError("Internal error: prepared reply attempt failed validation")
    return prepared


def _confirmed_reply_receipt_from_sending(
    sending_receipt: dict,
    *,
    reply_post_id: str,
    confirmation_epoch: int,
) -> dict:
    """Build the confirmed form without mutating its durable sending input."""
    confirmed = {
        **sending_receipt,
        "lifecycle_state": "confirmed",
        "reply_post_id": str(reply_post_id),
    }
    if sending_receipt.get("schema_version") == 4:
        confirmed_date = epoch_date_str(confirmation_epoch)
        confirmed.update(
            {
                "confirmation_epoch": confirmation_epoch,
                "reply_epoch": confirmation_epoch,
                "daily_reply_date": confirmed_date,
            }
        )
        if sending_receipt.get("candidate_source") == "quote_tweet":
            confirmed["daily_quote_reply_date"] = confirmed_date
    return confirmed


def _reply_confirmation_epoch_after_remote_success(sending_receipt: dict) -> int:
    """Return a conservative monotonic wall time after remote confirmation."""
    observed_epoch = now_epoch()
    if sending_receipt.get("schema_version") != 4:
        return observed_epoch
    attempt_epoch = receipt_int(sending_receipt.get("attempt_epoch"))
    if attempt_epoch is None or observed_epoch >= attempt_epoch:
        return observed_epoch
    log.warning(
        "Wall clock moved backward during conversational reply creation; "
        "using durable attempt epoch as conservative confirmation time "
        "attempt_epoch=%s observed_epoch=%s",
        attempt_epoch,
        observed_epoch,
    )
    return attempt_epoch


def promote_sending_reply_receipt(
    sending_receipt: dict,
    *,
    reply_post_id: str,
    confirmation_epoch: int,
) -> dict:
    """Atomically promote the exact prepared transaction to confirmed."""
    status, current = load_confirmed_reply_receipt()
    if status != "sending" or current != sending_receipt:
        raise UnresolvedSendingReplyReceipt(
            "Conversational reply sending receipt changed before confirmation"
        )
    confirmed = _confirmed_reply_receipt_from_sending(
        sending_receipt,
        reply_post_id=reply_post_id,
        confirmation_epoch=confirmation_epoch,
    )
    if not confirmed_reply_receipt_is_semantically_valid(confirmed):
        raise RuntimeError(
            "Internal error: promoted confirmed-reply receipt failed validation"
        )
    atomic_write_json(CONFIRMED_REPLY_RECEIPT_FILE, confirmed, durable=True)
    log.warning(
        "Promoted conversational reply receipt to confirmed source=%s "
        "target_id=%s reply_post_id=%s path=%s",
        confirmed.get("candidate_source", "mention"),
        confirmed.get("target_id"),
        confirmed.get("reply_post_id"),
        CONFIRMED_REPLY_RECEIPT_FILE,
    )
    return confirmed


def remove_confirmed_reply_receipt(
    receipt: dict | None = None,
    *,
    sending_disposition: str | None = None,
) -> None:
    """Remove confirmed reply receipt."""
    try:
        if receipt is not None:
            with open(CONFIRMED_REPLY_RECEIPT_FILE, "r", encoding="utf-8") as handle:
                current = json.load(handle)
            if current != receipt:
                raise InvalidConfirmedReplyReceipt(
                    "Refusing to remove a conversational-reply receipt whose "
                    "transaction identity changed"
                )
            if receipt.get("lifecycle_state") == "sending":
                if sending_disposition not in {
                    "definite_non_success",
                    "confirmed_state_fallback",
                }:
                    raise ValueError(
                        "Removing a sending reply receipt requires an explicit "
                        "disposition"
                    )
            elif sending_disposition is not None:
                raise ValueError(
                    "A confirmed reply receipt cannot use a sending disposition"
                )
        CONFIRMED_REPLY_RECEIPT_FILE.unlink()
        if receipt:
            if receipt.get("lifecycle_state") == "sending":
                if sending_disposition == "definite_non_success":
                    log.info(
                        "Removed conversational reply sending receipt after definite "
                        "non-success source=%s target_id=%s path=%s",
                        receipt.get("candidate_source", "mention"),
                        receipt.get("target_id"),
                        CONFIRMED_REPLY_RECEIPT_FILE,
                    )
                elif sending_disposition == "confirmed_state_fallback":
                    log.warning(
                        "Removed conversational reply sending receipt after "
                        "confirmed identity was preserved in canonical state "
                        "source=%s target_id=%s path=%s",
                        receipt.get("candidate_source", "mention"),
                        receipt.get("target_id"),
                        CONFIRMED_REPLY_RECEIPT_FILE,
                    )
            else:
                log.info(
                    "Removed reconciled confirmed-reply receipt source=%s "
                    "target_id=%s reply_post_id=%s path=%s",
                    receipt.get("candidate_source", "mention"),
                    receipt.get("target_id"),
                    receipt.get("reply_post_id"),
                    CONFIRMED_REPLY_RECEIPT_FILE,
                )
        else:
            log.info("Removed reconciled confirmed-reply receipt: %s", CONFIRMED_REPLY_RECEIPT_FILE)
        fsync_parent_dir(CONFIRMED_REPLY_RECEIPT_FILE, strict=True)
    except FileNotFoundError:
        return


def conversational_reply_confirmation_epoch(receipt: dict) -> int:
    """Return the best available confirmed time for a reply receipt."""
    if receipt.get("schema_version") == 4:
        confirmation_epoch = receipt_int(receipt.get("confirmation_epoch"))
        if confirmation_epoch is None:
            raise InvalidConfirmedReplyReceipt(
                "Schema-v4 confirmed reply receipt lacks a confirmation epoch"
            )
        return confirmation_epoch
    reply_epoch = receipt_int(receipt.get("reply_epoch"))
    if reply_epoch is None:
        raise InvalidConfirmedReplyReceipt(
            "Legacy confirmed reply receipt lacks its best-known reply epoch"
        )
    return reply_epoch


def _valid_iso_date(value: object) -> bool:
    """Return whether a value is a canonical calendar date."""
    if not isinstance(value, str):
        return False
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d") == value
    except ValueError:
        return False


def _advance_reply_counters_to_confirmation_date(
    state: dict,
    confirmation_date: str,
    *,
    include_quote_lane: bool,
) -> None:
    """Advance stale daily reply buckets without rolling newer state backward."""
    state_reply_date = state.get("daily_reply_date")
    if not _valid_iso_date(state_reply_date) or state_reply_date < confirmation_date:
        log.info(
            "Advancing daily reply accounting to confirmation date. "
            "previous_date=%s new_date=%s previous_count=%s",
            state_reply_date,
            confirmation_date,
            state.get("daily_reply_count"),
        )
        state["daily_reply_date"] = confirmation_date
        state["daily_reply_count"] = 0
        state["daily_replied_author_ids"] = []
        state["daily_replied_author_counts"] = {}
    if not include_quote_lane:
        return
    state_quote_date = state.get("daily_quote_reply_date")
    if not _valid_iso_date(state_quote_date) or state_quote_date < confirmation_date:
        log.info(
            "Advancing daily quote-reply accounting to confirmation date. "
            "previous_date=%s new_date=%s previous_count=%s",
            state_quote_date,
            confirmation_date,
            state.get("daily_quote_reply_count"),
        )
        state["daily_quote_reply_date"] = confirmation_date
        state["daily_quote_reply_count"] = 0


def apply_confirmed_reply_receipt(state: dict, receipt: dict) -> None:
    """Apply confirmed reply receipt."""
    target_id = str(receipt["target_id"])
    reply_post_id = str(receipt["reply_post_id"])
    author_id = str(receipt.get("author_id") or "")
    reply_epoch = conversational_reply_confirmation_epoch(receipt)
    candidate_source = str(receipt.get("candidate_source") or "mention")
    conversation_id = str(receipt.get("conversation_id") or target_id)
    reply_text = str(receipt.get("reply_text") or "")
    receipt_reply_date = str(receipt.get("daily_reply_date") or epoch_date_str(reply_epoch))
    receipt_quote_reply_date = str(receipt.get("daily_quote_reply_date") or receipt_reply_date)
    if receipt.get("schema_version") == 4:
        _advance_reply_counters_to_confirmation_date(
            state,
            receipt_reply_date,
            include_quote_lane=candidate_source == "quote_tweet",
        )
    clarification = receipt.get("clarification_reply")
    if isinstance(clarification, dict):
        existing_records = state.get("clarification_reply_records", {})
        existing = existing_records.get(str(clarification["thread_id"])) if isinstance(existing_records, dict) else None
        if isinstance(existing, dict) and str(existing.get("reply_post_id") or "") != reply_post_id:
            raise InvalidConfirmedReplyReceipt(
                f"clarification thread {clarification['thread_id']} already has a different completed repair"
            )
    mention_pagination_to_preserve: dict | None = None
    if "mention_pagination" in receipt:
        mention_pagination = receipt.get("mention_pagination")
        if (
            candidate_source != "mention"
            or not mention_pagination_provenance_is_valid(mention_pagination)
        ):
            raise InvalidConfirmedReplyReceipt(
                "Confirmed reply receipt has invalid mention pagination provenance"
            )
        base_since_id = str(mention_pagination["base_since_id"])
        current_since_id = str(state.get("last_seen_mention_id") or "")
        if current_since_id != base_since_id:
            raise InvalidConfirmedReplyReceipt(
                "Confirmed mention receipt pagination base does not match "
                "the current mention watermark"
            )
        mention_pagination_to_preserve = copy.deepcopy(mention_pagination)
    elif candidate_source == "mention":
        # Receipts written by pre-provenance versions can still be reconciled
        # safely when canonical state carries an exact continuation bound to
        # the unchanged watermark. Preserving it favours harmless deduplication
        # over skipping the unseen tail of a truncated result set.
        active_pagination = state.get("mention_pagination")
        current_since_id = str(state.get("last_seen_mention_id") or "")
        if (
            mention_pagination_provenance_is_valid(active_pagination)
            and str(active_pagination["base_since_id"]) == current_since_id
        ):
            mention_pagination_to_preserve = copy.deepcopy(active_pagination)
            log.warning(
                "Preserving active mention pagination for a legacy confirmed "
                "reply receipt without transaction-bound provenance"
            )
    clear_pending_ai_reply(state, target_id, candidate_source)

    if candidate_source == "quote_tweet":
        replied_to_ids = set(str(x) for x in state.get("replied_to_quote_post_ids", []))
        already_recorded = target_id in replied_to_ids
        mark_quote_tweet_replied(state, target_id)
    else:
        replied_to_ids = set(str(x) for x in state.get("replied_to_ids", []))
        already_recorded = target_id in replied_to_ids
        state["replied_to_ids"] = append_unique_durable(
            state.get("replied_to_ids", []),
            target_id,
        )

    state["own_auto_reply_ids"] = append_unique_capped(
        state.get("own_auto_reply_ids", []),
        reply_post_id,
        1000,
    )

    if not already_recorded and state.get("daily_reply_date") == receipt_reply_date:
        state["daily_reply_count"] = int(state.get("daily_reply_count", 0) or 0) + 1
        if author_id:
            mark_daily_author_replied(state, author_id)
    if (
        candidate_source == "quote_tweet"
        and not already_recorded
        and state.get("daily_quote_reply_date") == receipt_quote_reply_date
    ):
        state["daily_quote_reply_count"] = int(state.get("daily_quote_reply_count", 0) or 0) + 1

    try:
        state["last_reply_epoch"] = max(int(state.get("last_reply_epoch", 0) or 0), reply_epoch)
    except Exception:
        state["last_reply_epoch"] = reply_epoch

    if candidate_source == "mention":
        if mention_pagination_to_preserve is not None:
            state["mention_pagination"] = mention_pagination_to_preserve
            log.info(
                "Preserved mention pagination continuation after confirmed "
                "reply target_id=%s base_since_id=%s",
                target_id,
                mention_pagination_to_preserve["base_since_id"] or None,
            )
        else:
            update_last_seen_mention_id(state, target_id)

    cache_tweet(
        state,
        tweet_id=reply_post_id,
        text=reply_text,
        author_id=str(MY_USER_ID),
        conversation_id=conversation_id,
        referenced_tweets=[
            {
                "type": "replied_to",
                "id": target_id,
            }
        ],
        created_at=(
            datetime.fromtimestamp(reply_epoch).isoformat()
            if receipt.get("schema_version") == 4
            else None
        ),
        post_type="auto_reply",
    )
    ai_reply_draft = receipt.get("ai_reply_draft")
    if isinstance(ai_reply_draft, dict):
        record = {
            "target_id": target_id,
            "reply_post_id": reply_post_id,
            "candidate_source": candidate_source,
            "reply_epoch": reply_epoch,
            **ai_reply_draft,
        }
        if receipt.get("schema_version") == 4:
            record["attempt_epoch"] = int(receipt["attempt_epoch"])
            record["confirmation_epoch"] = reply_epoch
        history = [
            item for item in state.get("ai_reply_history", [])
            if isinstance(item, dict) and str(item.get("reply_post_id") or "") != reply_post_id
        ]
        history.append(record)
        state["ai_reply_history"] = history[-1000:]
        log_event(
            "ai_reply_pipeline_outcome",
            status="confirmed",
            lane=candidate_source,
            target_id=target_id,
            reply_post_id=reply_post_id,
            strategy_version=ai_reply_draft.get("strategy_version"),
            mode=ai_reply_draft.get("mode"),
            tone=ai_reply_draft.get("tone"),
            factual_claim_count=len(ai_reply_draft.get("factual_claims", [])),
            evidence_ids=ai_reply_draft.get("evidence_ids", []),
            reviewer_verdict=ai_reply_draft.get("reviewer_verdict"),
            model_call_count=ai_reply_draft.get("model_call_count"),
            revision_count=ai_reply_draft.get("revision_count"),
        )
    if isinstance(clarification, dict):
        thread_id = str(clarification["thread_id"])
        records = state.get("clarification_reply_records", {})
        if not isinstance(records, dict):
            records = {}
        existing = records.get(thread_id)
        if not isinstance(existing, dict):
            records = dict(records)
            records[thread_id] = {
                "thread_id": thread_id,
                "author_id": author_id,
                "target_id": target_id,
                "reply_post_id": reply_post_id,
                "prior_bot_reply_id": str(clarification["prior_bot_reply_id"]),
                "original_question_id": str(clarification["original_question_id"]),
                "trigger": str(clarification["trigger"]),
                "completed_epoch": reply_epoch,
                "status": "repair_reply_completed",
                "clarification_reply_used": True,
                "thread_terminal": True,
            }
            state["clarification_reply_records"] = records
            log_event(
                "clarification_reply_used",
                thread_id=thread_id,
                author_id=author_id,
                target_id=target_id,
                reply_post_id=reply_post_id,
                trigger=clarification["trigger"],
            )
            log_event(
                "repair_reply_completed",
                thread_id=thread_id,
                author_id=author_id,
                target_id=target_id,
                reply_post_id=reply_post_id,
            )


def update_last_seen_mention_id(state: dict, mention_id: str) -> None:
    """Advance the durable mention watermark without moving it backwards."""
    previous = state.get("last_seen_mention_id")
    log.debug("Updating last_seen_mention_id. previous=%s new_candidate=%s", previous, mention_id)
    if previous is None:
        state["last_seen_mention_id"] = str(mention_id)
        return
    try:
        state["last_seen_mention_id"] = str(max(int(previous), int(mention_id)))
    except ValueError:
        state["last_seen_mention_id"] = str(mention_id)
    log.debug("last_seen_mention_id is now %s", state["last_seen_mention_id"])


def mark_mention_seen_if_applicable(state: dict, candidate: dict) -> None:
    """Advance the mention watermark unless pagination was incomplete."""
    if candidate.get("_pagination_truncated"):
        log.warning(
            "Not advancing mention watermark for %s because mention pagination was truncated",
            candidate.get("id"),
        )
        return
    if candidate.get("_source", "mention") == "mention":
        update_last_seen_mention_id(state, str(candidate.get("id", "")))


def reconcile_confirmed_reply_receipt(state: dict) -> bool:
    """Reconcile a confirmed reply without duplicating the remote post."""
    status, receipt = load_confirmed_reply_receipt()
    if status == "absent":
        return False
    if status == "sending" and receipt is not None:
        raise UnresolvedSendingReplyReceipt(
            "A conversational reply was interrupted after its durable sending "
            "receipt was written; manual reconciliation is required before any "
            "remote write"
        )
    if status == "invalid" or receipt is None:
        raise InvalidConfirmedReplyReceipt(
            f"Invalid confirmed-reply receipt blocks auto-reply processing: {CONFIRMED_REPLY_RECEIPT_FILE}"
        )

    log.warning(
        "Reconciling confirmed reply receipt source=%s target_id=%s reply_post_id=%s",
        receipt.get("candidate_source", "mention"),
        receipt.get("target_id"),
        receipt.get("reply_post_id"),
    )
    apply_confirmed_reply_receipt(state, receipt)
    try:
        save_state(state, durable=True)
    except Exception as exc:
        log.critical(
            "Confirmed reply receipt was applied in memory but state save failed; receipt remains for retry",
            exc_info=True,
        )
        raise ConfirmedReplyLocalPersistenceError("Confirmed reply receipt reconciliation state save failed") from exc
    try:
        remove_confirmed_reply_receipt(receipt)
    except Exception as exc:
        log.critical("Confirmed reply receipt state was saved but receipt removal failed", exc_info=True)
        raise ConfirmedReplyLocalPersistenceError("Confirmed reply receipt removal failed") from exc
    return True


def confirmed_reply_emergency_representation_is_complete(
    receipt: dict,
    state: dict,
) -> bool:
    """Return whether state alone durably suppresses a confirmed reply replay."""
    if not confirmed_reply_receipt_is_semantically_valid(receipt):
        return False
    target_id = str(receipt["target_id"])
    reply_post_id = str(receipt["reply_post_id"])
    candidate_source = str(receipt.get("candidate_source") or "mention")
    try:
        reply_epoch = conversational_reply_confirmation_epoch(receipt)
    except InvalidConfirmedReplyReceipt:
        return False
    state_reply_epoch = receipt_int(state.get("last_reply_epoch"))
    own_reply_ids = {str(item) for item in state.get("own_auto_reply_ids", [])}
    drafts = state.get("pending_ai_reply_drafts")
    pending_key = pending_ai_reply_draft_key(target_id, candidate_source)
    if (
        state_reply_epoch is None
        or state_reply_epoch < reply_epoch
        or reply_post_id not in own_reply_ids
        or (isinstance(drafts, dict) and pending_key in drafts)
    ):
        return False
    if candidate_source == "quote_tweet":
        return bool(
            target_id
            in {
                str(item)
                for item in state.get("replied_to_quote_post_ids", [])
            }
            and target_id
            in {str(item) for item in state.get("seen_quote_post_ids", [])}
        )
    return target_id in {
        str(item) for item in state.get("replied_to_ids", [])
    }


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
    if "reply_post_id" in receipt_template:
        raise ValueError("reply receipt template must not contain reply_post_id")
    # The reply text may be an ``AIReply`` string subclass whose constructor
    # requires provenance arguments, so ``deepcopy`` cannot reconstruct it.
    # Callers have already copied every mutable nested payload placed in the
    # template; a fresh outer mapping is sufficient and preserves the exact
    # reviewed string object for draft validation.
    receipt_template = dict(receipt_template)
    if (
        not sending_reply_receipt_is_semantically_valid(receipt_template)
        or str(receipt_template.get("candidate_source") or "") != str(lane)
    ):
        raise RuntimeError(
            "Refusing conversational X write with an invalid reply receipt template"
        )

    write_sending_reply_receipt(receipt_template)
    sigint_guard = begin_confirmed_post_sigint_deferral()
    try:
        response = create_post(
            text=reply_text,
            media_ids=None,
            reply_to_id=reply_to_id,
            made_with_ai=made_with_ai,
            prepared_conversational_reply_receipt=receipt_template,
        )
    except AmbiguousRemotePostOutcome as exc:
        # The pre-send receipt is itself the restart-safe ambiguity barrier,
        # including when create_post could not write its global marker.
        end_confirmed_post_sigint_deferral(sigint_guard)
        try:
            record_api_error(state, exc, "x", scope="write")
            save_state(state)
        except Exception:
            log.critical(
                "The conversational reply sending receipt is durable, but "
                "write-error bookkeeping could not be persisted",
                exc_info=True,
            )
        raise
    except (ApiError, RemoteOperationsPaused) as remote_error:
        # These outcomes are classified before transmission or from a definite
        # non-success HTTP response. They cannot represent an accepted post.
        try:
            remove_confirmed_reply_receipt(
                receipt_template,
                sending_disposition="definite_non_success",
            )
        except Exception as removal_error:
            end_confirmed_post_sigint_deferral(sigint_guard)
            raise ConfirmedReplyLocalPersistenceError(
                "A definitely failed conversational reply left its durable "
                "sending receipt unresolved"
            ) from removal_error
        end_confirmed_post_sigint_deferral(sigint_guard)
        raise
    except BaseException as remote_error:
        # Any unclassified interruption may have happened after bytes reached
        # X. Preserve the pre-send receipt as a restart-safe manual
        # reconciliation barrier. In particular, never discard it for
        # KeyboardInterrupt/SystemExit or an unexpected transport exception.
        end_confirmed_post_sigint_deferral(sigint_guard)
        if not isinstance(remote_error, Exception):
            raise
        raise AmbiguousRemotePostOutcome(
            "Conversational reply execution was interrupted with an unclassified "
            "remote outcome; its durable sending receipt requires reconciliation",
            service="x",
        ) from remote_error

    own_reply_id = str(response.get("data", {}).get("id") or "")
    confirmation_epoch = _reply_confirmation_epoch_after_remote_success(
        receipt_template
    )
    receipt = _confirmed_reply_receipt_from_sending(
        receipt_template,
        reply_post_id=own_reply_id,
        confirmation_epoch=confirmation_epoch,
    )
    try:
        if not confirmed_reply_receipt_is_semantically_valid(receipt):
            raise RuntimeError(
                "Internal error: confirmed reply representation failed validation"
            )
        receipt = promote_sending_reply_receipt(
            receipt_template,
            reply_post_id=own_reply_id,
            confirmation_epoch=confirmation_epoch,
        )
    except BaseException as receipt_error:
        fallback_error: BaseException | None = None
        fallback_complete = False
        try:
            if not confirmed_reply_receipt_is_semantically_valid(receipt):
                raise InvalidConfirmedReplyReceipt(
                    "Refusing to apply an invalid confirmed reply representation"
                )
            apply_confirmed_reply_receipt(state, receipt)
            try:
                save_state(state, durable=True)
            except StateBackupWriteError:
                if not json_file_matches(STATE_FILE, state):
                    raise
                log.warning(
                    "Confirmed conversational reply canonical state was committed, "
                    "but its latest backup write failed; using canonical state as "
                    "the durable replay barrier",
                    exc_info=True,
                )
            fallback_complete = bool(
                confirmed_reply_emergency_representation_is_complete(
                    receipt,
                    state,
                )
                and json_file_matches(STATE_FILE, state)
            )
        except BaseException as exc:
            fallback_error = exc
            log.critical(
                "Confirmed conversational reply id=%s target=%s lost its receipt "
                "and emergency state save",
                own_reply_id,
                reply_to_id,
                exc_info=True,
            )

        if not fallback_complete:
            durable_marker_written = latch_confirmed_post_persistence_failure(
                lane=lane,
                post_id=own_reply_id,
                failure_components=[
                    "confirmed_reply_receipt",
                    "reply_state",
                ],
            )
            status, current_receipt = load_confirmed_reply_receipt()
            if (
                durable_marker_written
                or status == "invalid"
                or (status == "sending" and current_receipt == receipt_template)
            ):
                end_confirmed_post_sigint_deferral(sigint_guard)
            else:
                retain_sigint_deferral_without_durable_barrier(lane=lane)
            raise UnrecoverableConfirmedReplyPersistenceError(
                f"Confirmed conversational reply {own_reply_id} to "
                f"{reply_to_id} has no complete durable recovery representation"
            ) from (fallback_error or receipt_error)

        try:
            remove_confirmed_reply_receipt(
                receipt_template,
                sending_disposition="confirmed_state_fallback",
            )
        except Exception as removal_error:
            end_confirmed_post_sigint_deferral(sigint_guard)
            raise ConfirmedReplyLocalPersistenceError(
                f"Confirmed conversational reply {own_reply_id} to {reply_to_id} "
                "was preserved in canonical state but its sending receipt remains"
            ) from removal_error
        end_confirmed_post_sigint_deferral(sigint_guard)
        if not isinstance(receipt_error, Exception):
            raise
        raise ConfirmedReplyLocalPersistenceError(
            f"Confirmed conversational reply {own_reply_id} to {reply_to_id} "
            "but failed writing its recovery receipt"
        ) from receipt_error

    end_confirmed_post_sigint_deferral(sigint_guard)
    return response, receipt


def maybe_reply_to_mentions(state: dict) -> str:
    """Process eligible mention and hot-post candidates under all reply limits."""
    log.info("Starting mention reply check")
    block_if_ambiguous_remote_post()

    if not ENABLE_AUTO_REPLIES:
        log.info("Auto replies disabled")
        return NORMAL_CHECK_STATUS_DISABLED

    if ai_first_reply_strategy.get("enabled") is not True:
        log.info("AI-first reply strategy disabled; skipping mention/hot-post checks")
        return NORMAL_CHECK_STATUS_DISABLED

    if lane_paused("disable_replies", "disable_normal_replies"):
        log.info("Skipping mention/hot-post reply check due to runtime control file")
        return NORMAL_CHECK_STATUS_DISABLED

    if in_api_cooldown(state):
        log.info("Skipping mention check due to X read API cooldown")
        return NORMAL_CHECK_STATUS_SKIPPED_COOLDOWN
    if in_api_cooldown(state, scope="write"):
        log.info("Skipping mention check due to X write API cooldown")
        return NORMAL_CHECK_STATUS_SKIPPED_COOLDOWN
    if in_api_cooldown(state, scope="xai"):
        log.info("Skipping mention check due to xAI API cooldown")
        return NORMAL_CHECK_STATUS_SKIPPED_COOLDOWN

    reset_daily_reply_count_if_needed(state)
    if reconcile_confirmed_reply_receipt(state):
        log.warning("Reconciled confirmed reply receipt before checking new mention candidates")

    daily_replied_author_counts = daily_author_reply_counts(state)

    log.debug(
        "Reply cap status: daily_reply_count=%s max=%s",
        state.get("daily_reply_count"),
        MAX_AUTO_REPLIES_PER_DAY,
    )
    log.debug(
        "Daily per-author cap status: authors_replied_today=%d max_per_author=%s",
        len(daily_replied_author_counts),
        MAX_REPLIES_PER_AUTHOR_PER_DAY,
    )

    if state["daily_reply_count"] >= MAX_AUTO_REPLIES_PER_DAY:
        log.info("Daily generated/replied cap reached")
        save_state(state)
        return NORMAL_CHECK_STATUS_SKIPPED_CAP

    current = now_epoch()

    seconds_since_last_reply = current - int(state.get("last_reply_epoch", 0))
    log.debug(
        "Seconds since last generated/replied=%s minimum=%s",
        seconds_since_last_reply,
        MIN_SECONDS_BETWEEN_REPLIES,
    )

    if seconds_since_last_reply < MIN_SECONDS_BETWEEN_REPLIES:
        log.info("Skipping mention check: minimum interval between replies not reached")
        return NORMAL_CHECK_STATUS_SKIPPED_SPACING

    try:
        mentions = get_mentions(state)
    except ApiError as e:
        log.exception("Failed to get mention reply candidates")
        record_api_error(state, e, "x")
        save_state(state)
        return NORMAL_CHECK_STATUS_API_ERROR
    except Exception:
        log.exception("Unexpected failure getting mention reply candidates")
        save_state(state)
        return NORMAL_CHECK_STATUS_API_ERROR

    try:
        hot_post_replies = get_hot_post_reply_candidates(state)
    except ApiError as e:
        log.exception("Failed to get optional hot-post reply candidates; continuing with mentions")
        record_api_error(state, e, "x", scope="quote")
        save_state(state)
        hot_post_replies = []
    except Exception:
        log.exception("Unexpected failure getting optional hot-post reply candidates; continuing with mentions")
        save_state(state)
        hot_post_replies = []

    mentions = dedupe_reply_candidates(mentions, hot_post_replies)

    if not mentions:
        log.info("No mention or hot-post reply candidates returned")
        return NORMAL_CHECK_STATUS_CHECKED

    mentions = valid_tweets_sorted_by_id(mentions, context="mention/hot-post candidate")

    replied_to_ids = set(str(x) for x in state.get("replied_to_ids", []))
    dry_run_seen_ids = set(str(x) for x in state.get("dry_run_seen_mention_ids", []))

    log.debug("replied_to_ids count=%d", len(replied_to_ids))
    log.debug("dry_run_seen_ids count=%d", len(dry_run_seen_ids))

    for mention in mentions:
        mention_id = str(mention["id"])
        author_id = str(mention.get("author_id"))
        incoming_text = mention.get("text", "")
        candidate_source = mention.get("_source", "mention")
        candidate_log_source = candidate_source
        if candidate_source == "mention" and mention.get("_also_hot_post_reply"):
            candidate_log_source = "mention+hot_post_reply"

        log.info(
            "Considering %s id=%s author_id=%s text=%r",
            candidate_log_source,
            mention_id,
            author_id,
            incoming_text,
        )

        if mention_id in replied_to_ids:
            log.info("Skipping %s %s: already replied to", candidate_source, mention_id)
            maybe_mark_hot_post_reply_skipped(state, mention, reason="already_replied")
            log_event("candidate_skipped", lane=candidate_log_source, id=mention_id, reason="already_replied")
            mark_mention_seen_if_applicable(state, mention)
            continue

        if DRY_RUN_REPLIES and mention_id in dry_run_seen_ids:
            log.info("Skipping %s %s: already seen in dry-run", candidate_source, mention_id)
            maybe_mark_hot_post_reply_skipped(state, mention, reason="dry_run_already_seen")
            log_event("candidate_skipped", lane=candidate_log_source, id=mention_id, reason="dry_run_already_seen")
            mark_mention_seen_if_applicable(state, mention)
            continue

        prior_evaluation = terminal_reply_evaluation(state, mention_id)
        if prior_evaluation is not None:
            prior_outcome = str(prior_evaluation.get("outcome") or "no_reply")
            log.info(
                "Skipping %s %s: terminal %s evaluation already recorded reason=%s",
                candidate_source,
                mention_id,
                prior_outcome,
                prior_evaluation.get("reason", ""),
            )
            skip_reason = f"already_evaluated_{prior_outcome}"
            maybe_mark_hot_post_reply_skipped(state, mention, reason=skip_reason)
            log_event(
                "candidate_skipped",
                lane=candidate_log_source,
                id=mention_id,
                reason=skip_reason,
            )
            mark_mention_seen_if_applicable(state, mention)
            save_state(state)
            continue

        if author_id == str(MY_USER_ID):
            log.info("Skipping %s %s: authored by our own account", candidate_source, mention_id)
            maybe_mark_hot_post_reply_skipped(state, mention, reason="own_account")
            log_event("candidate_skipped", lane=candidate_log_source, id=mention_id, reason="own_account")
            mark_mention_seen_if_applicable(state, mention)
            continue

        if clarification_thread_is_terminal(state, mention):
            log.info(
                "Skipping %s %s: clarification already completed and thread is terminal",
                candidate_source,
                mention_id,
            )
            maybe_mark_hot_post_reply_skipped(state, mention, reason="clarification_thread_terminal")
            log_event(
                "candidate_skipped",
                lane=candidate_log_source,
                id=mention_id,
                reason="clarification_thread_terminal",
            )
            mark_mention_seen_if_applicable(state, mention)
            save_state(state)
            continue

        if not reply_target_is_directly_eligible(mention):
            reason = "target_does_not_directly_mention_account"
            log.warning(
                "Skipping %s %s before context/media/model work: target is not directly reply-eligible",
                candidate_source,
                mention_id,
            )
            drafts = state.get("pending_ai_reply_drafts", {})
            pending_key = pending_ai_reply_draft_key(mention_id, str(candidate_source))
            pending_record = drafts.get(pending_key) if isinstance(drafts, dict) else None
            if isinstance(pending_record, dict):
                log_event(
                    "ai_reply_pipeline_outcome",
                    status="posting_failed_terminal",
                    lane=str(candidate_source),
                    target_id=mention_id,
                    reply_post_id="",
                    strategy_version=pending_record.get("strategy_version"),
                    mode=pending_record.get("mode"),
                    reviewer_verdict=pending_record.get("reviewer_verdict"),
                    failure_reason="reply_not_permitted_preflight",
                )
                clear_pending_ai_reply(state, mention_id, str(candidate_source))
            record_terminal_reply_evaluation(
                state,
                target_id=mention_id,
                lane=str(candidate_source),
                reason=reason,
                outcome="reply_not_permitted",
            )
            maybe_mark_hot_post_reply_skipped(state, mention, reason="reply_not_permitted")
            log_event(
                "reply_target_terminal",
                lane=candidate_log_source,
                target_id=mention_id,
                outcome="reply_not_permitted",
                reason=reason,
            )
            mark_mention_seen_if_applicable(state, mention)
            save_state(state, durable=True)
            continue

        clarification = clarification_reply_context(state, mention, current=current)
        author_cap_reached = daily_author_reply_count(state, author_id) >= MAX_REPLIES_PER_AUTHOR_PER_DAY
        if author_cap_reached and clarification is None:
            log.info(
                "Skipping mention %s: already reached per-author daily cap for author_id=%s",
                mention_id,
                author_id,
            )
            maybe_mark_hot_post_reply_skipped(state, mention, reason="author_daily_cap")
            log_event("candidate_skipped", lane=candidate_log_source, id=mention_id, reason="author_daily_cap", author_id=author_id)
            mark_mention_seen_if_applicable(state, mention)
            save_state(state)
            continue
        if author_cap_reached:
            log.info(
                "Permitting one clarification reply past per-author cap target_id=%s thread_id=%s author_id=%s",
                mention_id,
                clarification["thread_id"],
                author_id,
            )
            log_event(
                "clarification_reply_cap_override",
                target_id=mention_id,
                thread_id=clarification["thread_id"],
                author_id=author_id,
                bypassed_cap="per_author_daily",
            )

        if is_probably_spam_or_not_worth_replying(incoming_text):
            log.info("Skipping %s %s: spam/not worth replying", candidate_source, mention_id)
            maybe_mark_hot_post_reply_skipped(state, mention, reason="spam_or_not_worth_replying")
            log_event("candidate_skipped", lane=candidate_log_source, id=mention_id, reason="spam_or_not_worth_replying")
            mark_mention_seen_if_applicable(state, mention)
            save_state(state)
            continue

        try:
            reply_context, should_continue = build_context_for_reply_ai(mention, state)
        except ApiError as e:
            log.exception("Could not build context for %s %s due to API error", candidate_source, mention_id)
            record_api_error(state, e, "x")
            save_state(state)
            return NORMAL_CHECK_STATUS_API_ERROR

        if not should_continue:
            log.info("Skipping %s %s: could not build usable context or configured to skip", candidate_source, mention_id)
            maybe_mark_hot_post_reply_skipped(state, mention, reason="context_unavailable")
            log_event("candidate_skipped", lane=candidate_log_source, id=mention_id, reason="context_unavailable")
            mark_mention_seen_if_applicable(state, mention)
            save_state(state)
            continue

        if clarification is not None:
            reply_context["clarification_request"] = {
                "original_question": trim_context_text(
                    clarification["question_text"],
                    REPLY_INCOMING_MAX_CHARS,
                ),
                "correction": str(reply_context["incoming_contribution"]),
            }

        try:
            reply_evidence_repository()
        except ReplyEvidenceUnavailable as exc:
            log.error(
                "Skipping conversational reply target_id=%s because local evidence is unavailable: %s",
                mention_id,
                exc,
            )
            log_event(
                "reply_evidence_unavailable",
                lane=str(candidate_source),
                target_id=mention_id,
            )
            return NORMAL_CHECK_STATUS_CHECKED

        media_context = reply_media_context_for_candidate(
            mention,
            lane=str(candidate_source),
            target_id=mention_id,
        )

        reply_text = pending_ai_reply(
            state,
            mention_id,
            str(candidate_source),
            context=reply_context,
            recent_replies=recent_auto_reply_texts(state),
        )
        evaluation_outcome: dict[str, str] = {}
        try:
            if reply_text is None:
                reply_text = generate_ai_first_reply(
                    reply_context,
                    media_context,
                    recent_replies=recent_auto_reply_texts(state),
                    evaluation_outcome=evaluation_outcome,
                )
            else:
                log.info("Reusing persisted AI-first reply draft target_id=%s source=%s", mention_id, candidate_source)
        except ApiError as e:
            log.exception("Failed to ask Grok for reply")
            record_api_error(state, e, "xai")
            save_state(state)
            return NORMAL_CHECK_STATUS_API_ERROR
        except Exception as e:
            log.exception("Unexpected Grok failure")
            record_api_error(state, e, "xai")
            save_state(state)
            return NORMAL_CHECK_STATUS_API_ERROR

        from reply_strategy import AIReply
        if not reply_text:
            if (
                not DRY_RUN_REPLIES
                and evaluation_outcome.get("status") == "no_reply"
            ):
                record_terminal_reply_evaluation(
                    state,
                    target_id=mention_id,
                    lane=str(candidate_source),
                    reason=evaluation_outcome.get("reason", "model_selected_no_reply"),
                )
            log.info("No usable reply generated for %s %s", candidate_source, mention_id)
            maybe_mark_hot_post_reply_skipped(state, mention, reason="no_usable_reply_generated")
            log_event("candidate_skipped", lane=candidate_log_source, id=mention_id, reason="no_usable_reply_generated")
            mark_mention_seen_if_applicable(state, mention)
            save_state(state)
            continue
        if not isinstance(reply_text, AIReply):
            log.error(
                "AI-first reply pipeline returned an unapproved reply type; refusing target_id=%s source=%s",
                mention_id,
                candidate_source,
            )
            record_terminal_reply_evaluation(
                state,
                target_id=mention_id,
                lane=str(candidate_source),
                reason="reviewer_approval_missing",
            )
            maybe_mark_hot_post_reply_skipped(state, mention, reason="reviewer_approval_missing")
            mark_mention_seen_if_applicable(state, mention)
            save_state(state, durable=True)
            continue
        if clarification is not None and reply_text.draft_record.get("mode") != "direct_factual_answer":
            log.error("Clarification reply lacks direct_factual_answer mode; refusing target_id=%s", mention_id)
            record_terminal_reply_evaluation(
                state,
                target_id=mention_id,
                lane=str(candidate_source),
                reason="clarification_not_direct_factual_answer",
            )
            maybe_mark_hot_post_reply_skipped(
                state,
                mention,
                reason="clarification_not_direct_factual_answer",
            )
            mark_mention_seen_if_applicable(state, mention)
            save_state(state, durable=True)
            continue

        log.info("Generated reply to mention %s: %r", mention_id, reply_text)

        if not DRY_RUN_REPLIES:
            draft_stored = store_pending_ai_reply(
                state,
                mention_id,
                str(candidate_source),
                reply_text,
                context=reply_context,
            )
            if not draft_stored:
                log.error(
                    "AI-first reply draft failed persistence validation; "
                    "skipping target_id=%s source=%s",
                    mention_id,
                    candidate_source,
                )
                record_terminal_reply_evaluation(
                    state,
                    target_id=mention_id,
                    lane=str(candidate_source),
                    reason="ai_reply_persistence_validation_failed",
                )
                maybe_mark_hot_post_reply_skipped(
                    state,
                    mention,
                    reason="ai_reply_persistence_validation_failed",
                )
                log_event(
                    "candidate_skipped",
                    lane=candidate_log_source,
                    id=mention_id,
                    reason="ai_reply_persistence_validation_failed",
                )
                mark_mention_seen_if_applicable(state, mention)
                save_state(state, durable=True)
                continue
            save_state(state, durable=True)

        if DRY_RUN_REPLIES:
            log.warning("DRY_RUN_REPLIES=True, not posting generated reply")
            log_ai_reply_dry_run(
                context=reply_context,
                reply=reply_text,
                lane=str(candidate_source),
                target_id=mention_id,
            )

            completion_epoch = now_epoch()
            reset_daily_reply_count_if_needed(state)
            state["daily_reply_count"] += 1
            state["last_reply_epoch"] = completion_epoch

            dry_run_seen_ids.add(mention_id)
            state["dry_run_seen_mention_ids"] = append_unique_capped(
                state.get("dry_run_seen_mention_ids", []),
                mention_id,
                1000,
            )

            mark_daily_author_replied(state, author_id)

            cache_tweet(
                state,
                tweet_id=f"dry-run-reply-{mention_id}",
                text=reply_text,
                author_id=str(MY_USER_ID),
                conversation_id=str(mention.get("conversation_id", mention_id)),
                referenced_tweets=[
                    {
                        "type": "replied_to",
                        "id": str(mention_id),
                    }
                ],
                post_type="auto_reply",
            )

            mark_mention_seen_if_applicable(state, mention)
            save_state(state)
            return NORMAL_CHECK_STATUS_POSTED

        receipt_template = {
            "schema_version": 4,
            "lifecycle_state": "sending",
            "target_id": mention_id,
            "author_id": author_id,
            "candidate_source": candidate_source,
            "conversation_id": str(
                mention.get("conversation_id", mention_id)
            ),
            "reply_text": reply_text,
            "reply_context": copy.deepcopy(reply_context),
            "ai_reply_draft": copy.deepcopy(reply_text.draft_record),
        }
        if candidate_source == "mention" and mention.get("_pagination_truncated"):
            mention_pagination = mention.get("_mention_pagination")
            if not mention_pagination_provenance_is_valid(mention_pagination):
                raise RuntimeError(
                    "Refusing to post a reply from a truncated mention batch "
                    "without valid pagination provenance"
                )
            base_since_id = str(mention_pagination["base_since_id"])
            current_since_id = str(state.get("last_seen_mention_id") or "")
            active_pagination = state.get("mention_pagination")
            if (
                current_since_id != base_since_id
                or not mention_pagination_provenance_is_valid(active_pagination)
                or active_pagination != mention_pagination
            ):
                raise RuntimeError(
                    "Refusing to post a reply whose mention pagination "
                    "provenance no longer matches durable state"
                )
            receipt_template["mention_pagination"] = copy.deepcopy(
                mention_pagination
            )
        if clarification is not None:
            receipt_template["clarification_reply"] = {
                key: clarification[key]
                for key in (
                    "thread_id",
                    "prior_bot_reply_id",
                    "original_question_id",
                    "trigger",
                )
            }

        receipt_template = bind_conversational_reply_attempt_time(receipt_template)
        try:
            reply_response, receipt = (
                post_conversational_reply_with_durable_identity(
                    state=state,
                    receipt_template=receipt_template,
                    reply_text=reply_text,
                    reply_to_id=mention_id,
                    made_with_ai=MARK_AI_REPLIES_AS_AI,
                    lane=str(candidate_source),
                )
            )
        except UnrecoverableConfirmedReplyPersistenceError:
            log.critical(
                "Confirmed %s reply lost every complete durable local identity; "
                "the global remote-write safety barrier remains active",
                candidate_log_source,
                exc_info=True,
            )
            raise
        except ConfirmedReplyLocalPersistenceError:
            log.critical(
                "Confirmed %s reply required its durable state fallback",
                candidate_log_source,
                exc_info=True,
            )
            raise
        except AmbiguousRemotePostOutcome:
            log.critical(
                "%s reply stopped after an ambiguous remote outcome; the global "
                "remote-write safety barrier remains active",
                candidate_log_source,
                exc_info=True,
            )
            log_ai_reply_posting_outcome(
                reply=reply_text,
                status="posting_failed_retryable",
                lane=str(candidate_source),
                target_id=mention_id,
                failure_reason="ambiguous_remote_outcome",
            )
            raise
        except ApiError as e:
            if api_error_is_reply_not_allowed(e):
                log.warning(
                    "Cannot reply to mention %s because X says replies are not allowed; "
                    "marking mention as handled without consuming reply quota",
                    mention_id,
                )
                log_ai_reply_posting_outcome(
                    reply=reply_text,
                    status="posting_failed_terminal",
                    lane=str(candidate_source),
                    target_id=mention_id,
                    failure_reason="reply_not_permitted",
                )
                record_terminal_reply_evaluation(
                    state,
                    target_id=mention_id,
                    lane=str(candidate_source),
                    reason="x_reply_not_permitted",
                    outcome="reply_not_permitted",
                )
                log_event(
                    "reply_target_terminal",
                    lane=candidate_log_source,
                    target_id=mention_id,
                    outcome="reply_not_permitted",
                    reason="x_reply_not_permitted",
                )
                replied_to_ids.add(mention_id)
                clear_pending_ai_reply(state, mention_id, str(candidate_source))
                state["replied_to_ids"] = append_unique_durable(
                    state.get("replied_to_ids", []),
                    mention_id,
                )
                mark_mention_seen_if_applicable(state, mention)
                save_state(state, durable=True)
                return NORMAL_CHECK_STATUS_CHECKED

            log.exception("Failed to post generated reply")
            log_ai_reply_posting_outcome(
                reply=reply_text,
                status="posting_failed_retryable",
                lane=str(candidate_source),
                target_id=mention_id,
                failure_reason=f"x_api_{getattr(e, 'status_code', 'error')}",
            )
            record_api_error(state, e, "x", scope="write")
            save_state(state)
            return NORMAL_CHECK_STATUS_API_ERROR
        except Exception as e:
            log.exception("Unexpected failure posting generated reply")
            log_ai_reply_posting_outcome(
                reply=reply_text,
                status="posting_failed_retryable",
                lane=str(candidate_source),
                target_id=mention_id,
                failure_reason="unexpected_posting_error",
            )
            record_api_error(state, e, "x", scope="write")
            save_state(state)
            return NORMAL_CHECK_STATUS_API_ERROR

        own_reply_id = str(receipt["reply_post_id"])

        apply_confirmed_reply_receipt(state, receipt)
        log.info("Recorded and cached own auto-reply id=%s", own_reply_id)
        save_state(state, durable=True)
        try:
            remove_confirmed_reply_receipt(receipt)
        except Exception as exc:
            log.critical(
                "Confirmed reply id=%s to target=%s was saved but receipt removal failed",
                own_reply_id,
                mention_id,
                exc_info=True,
            )
            raise ConfirmedReplyLocalPersistenceError(
                f"Confirmed reply {own_reply_id} to {mention_id} but receipt removal failed"
            ) from exc

        log_event(
            "reply_posted",
            lane=candidate_log_source,
            target_id=mention_id,
            author_id=author_id,
            reply_post_id=own_reply_id,
            daily_reply_count=state.get("daily_reply_count"),
        )
        log.info("Reply posted successfully")
        return NORMAL_CHECK_STATUS_POSTED

    save_state(state)
    log.info("Mention reply check finished with no reply generated/posted")
    return NORMAL_CHECK_STATUS_CHECKED


# ---------------------------------------------------------------------
# Quote-post replies
# ---------------------------------------------------------------------

def load_extra_quote_watch_post_ids() -> list[str]:
    """
    Read extra own-post IDs to include in quote-tweet checks.

    This is deliberately read immediately before each quote-tweet check,
    not just at startup, so the file can be edited while the bot is running.

    File format:
      - one post ID per line
      - blank lines ignored
      - lines beginning with # ignored
      - inline comments allowed after whitespace + #
    """
    path = EXTRA_QUOTE_WATCH_FILE

    if not path.exists():
        return []

    post_ids: list[str] = []

    try:
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()

            if not line or line.startswith("#"):
                continue

            # Allow:
            # 2070843320310419627  # hot meme post
            if " #" in line:
                line = line.split(" #", 1)[0].strip()

            if not line.isdigit():
                log.warning("Ignoring invalid extra quote-watch post ID in %s: %r", path, raw_line)
                continue

            if line not in post_ids:
                post_ids.append(line)

            if len(post_ids) >= MAX_EXTRA_QUOTE_WATCH_POSTS:
                break

    except Exception as exc:
        log.warning("Could not read extra quote-watch post IDs from %s: %s", path, exc)
        return []

    return post_ids


def build_quote_lookup_post_ids(state: dict) -> list[str]:
    """
    Build the list of own posts to inspect for quote-tweets.

    Extra watched posts are loaded fresh for every quote-tweet check and
    are given priority, so a hot older meme does not fall behind the last
    five ordinary quote/image posts.
    """
    recent_ids = get_recent_own_post_ids_for_quote_lookup(state)
    extra_ids = load_extra_quote_watch_post_ids()

    clean_ids: list[str] = []
    seen: set[str] = set()

    # Extra watched posts first: these are explicitly selected hot posts.
    for tweet_id in extra_ids + recent_ids:
        tweet_id = str(tweet_id).strip()
        if not tweet_id or tweet_id in seen:
            continue
        seen.add(tweet_id)
        clean_ids.append(tweet_id)

        if len(clean_ids) >= MAX_QUOTE_POSTS_PER_CHECK:
            break

    if extra_ids:
        log.info(
            "Quote-tweet check loaded %d extra watched post(s) from %s: %s",
            len(extra_ids),
            EXTRA_QUOTE_WATCH_FILE,
            ", ".join(extra_ids),
        )

    log.info("Own posts for quote lookup: %s", clean_ids)

    return clean_ids


def get_recent_own_post_ids_for_quote_lookup(state: dict) -> list[str]:
    """Return recent own post IDs for quote lookup."""
    seed_recent_own_post_ids_from_cache(state)

    ids = [str(x) for x in state.get("recent_own_post_ids", [])]

    if state.get("last_main_post_id"):
        last_id = str(state["last_main_post_id"])
        if last_id not in ids:
            ids.insert(0, last_id)

    clean_ids: list[str] = []
    seen: set[str] = set()

    for tweet_id in ids:
        if tweet_id in seen:
            continue
        seen.add(tweet_id)
        clean_ids.append(tweet_id)

    return clean_ids[:QUOTE_POST_LOOKBACK_MAIN_POSTS]


def get_quote_tweets_for_post(post_id: str, state: dict | None = None) -> list[dict]:
    """Return quote tweets for post."""
    log.info("Fetching quote tweets for post_id=%s", post_id)

    params = {
        "max_results": QUOTE_LOOKUP_API_MAX_RESULTS,
        "tweet.fields": "author_id,created_at,conversation_id,referenced_tweets,attachments",
        "expansions": "author_id,attachments.media_keys",
        "user.fields": "description,username,name,public_metrics",
        "media.fields": "media_key,type,url,preview_image_url",
    }
    pagination_tokens: dict[str, str] = {}
    post_id = str(post_id)
    if state is not None:
        raw_tokens = state.get("quote_lookup_pagination_tokens", {})
        if isinstance(raw_tokens, dict):
            pagination_tokens = {str(key): str(value) for key, value in raw_tokens.items() if str(value)}

    if pagination_tokens.get(post_id):
        params["pagination_token"] = pagination_tokens[post_id]
        log.info("Quote lookup for post_id=%s resuming with pagination_token=%s", post_id, pagination_tokens[post_id])

    def clear_invalid_quote_lookup_cursor() -> None:
        if state is None:
            return
        pagination_tokens.pop(post_id, None)
        state["quote_lookup_pagination_tokens"] = dict(pagination_tokens)
        save_state(state, durable=True)

    result = x_paginated_get(
        x_quote_lookup_request,
        f"/2/tweets/{post_id}/quote_tweets",
        params,
        max_pages=QUOTE_LOOKUP_MAX_PAGES_PER_POST,
        label=f"quote tweets for {post_id}",
        on_invalid_cursor=clear_invalid_quote_lookup_cursor,
    )
    pagination = result.get("_pagination", {}) if isinstance(result.get("_pagination", {}), dict) else {}
    if state is not None:
        next_token = str(pagination.get("next_token") or "")
        if next_token:
            pagination_tokens[post_id] = next_token
            state["quote_lookup_pagination_tokens"] = pagination_tokens
            log.warning("Quote lookup for post_id=%s truncated; saved pagination continuation token", post_id)
        elif post_id in pagination_tokens:
            pagination_tokens.pop(post_id, None)
            state["quote_lookup_pagination_tokens"] = pagination_tokens
            log.info("Quote lookup for post_id=%s reached end of pagination; cleared continuation token", post_id)

    quote_tweets = result.get("data", [])
    attach_media_to_tweets(quote_tweets, result.get("includes", {}))

    users_by_id = {
        str(user.get("id")): user
        for user in result.get("includes", {}).get("users", [])
    }

    for quote_tweet in quote_tweets:
        author_id = str(quote_tweet.get("author_id", ""))
        quote_tweet["_author_user"] = users_by_id.get(author_id, {})

    log.info("Fetched %d quote tweet(s) for post_id=%s", len(quote_tweets), post_id)
    log_json_debug("Quote tweets returned", quote_tweets)

    return quote_tweets


def quote_tweet_is_old_enough(quote_tweet: dict) -> bool:
    """Return whether quote tweet is old enough."""
    created_epoch = parse_x_datetime_to_epoch(quote_tweet.get("created_at"))

    if created_epoch is None:
        log.warning(
            "Quote tweet %s has no usable created_at; treating as too recent so it can be retried later",
            quote_tweet.get("id"),
        )
        return False

    age = now_epoch() - created_epoch
    log.debug(
        "Quote tweet id=%s age_seconds=%s required_delay=%s",
        quote_tweet.get("id"),
        age,
        QUOTE_REPLY_DELAY_SECONDS,
    )

    return age >= QUOTE_REPLY_DELAY_SECONDS


def quote_tweet_directly_quotes_original(quote_tweet: dict, original_post_id: str) -> bool:
    """
    The /quote_tweets endpoint can surface reposts/retweets of someone else's
    quote-tweet. Those are not good reply targets. Only treat the item as
    replyable if X's structured referenced_tweets says it directly quoted the
    original post we are checking.
    """
    original_post_id = str(original_post_id)
    refs = quote_tweet.get("referenced_tweets", []) or []

    if any(ref.get("type") == "retweeted" for ref in refs):
        return False

    if any(
        ref.get("type") == "quoted" and str(ref.get("id")) == original_post_id
        for ref in refs
    ):
        return True

    # Fallback for any odd/legacy response shape: obvious old-style retweets
    # should not be treated as quote-tweets worth replying to.
    text = clean_text_for_reply_context(quote_tweet.get("text", ""))
    if re.match(r"^RT\s+@\w+:", text):
        return False

    # Conservative default: ambiguous quote lookup results are skipped rather
    # than sent to Grok or replied to.
    return False


def quote_author_profile_text(quote_tweet: dict) -> str:
    """Return the quote author profile text."""
    user = quote_tweet.get("_author_user", {}) or {}

    parts = [
        str(user.get("name", "")),
        str(user.get("username", "")),
        str(user.get("description", "")),
    ]

    public_metrics = user.get("public_metrics", {}) or {}

    if public_metrics:
        parts.append(
            "followers={followers_count} following={following_count} tweets={tweet_count}".format(
                followers_count=public_metrics.get("followers_count", ""),
                following_count=public_metrics.get("following_count", ""),
                tweet_count=public_metrics.get("tweet_count", ""),
            )
        )

    return "\n".join(part for part in parts if part.strip())


def build_quote_tweet_reply_context(original_tweet: dict, quote_tweet: dict) -> dict[str, object]:
    """Build a structured quote-post context with commentary kept primary."""
    context: dict[str, object] = {
        "target_id": str(quote_tweet.get("id") or ""),
        "thread_id": str(quote_tweet.get("conversation_id") or quote_tweet.get("id") or ""),
        "lane": "quote_tweet",
        "incoming_contribution": trim_context_text(
            str(quote_tweet.get("text") or ""),
            REPLY_INCOMING_MAX_CHARS,
        ),
        "quoted_post": {
            "post_id": str(original_tweet.get("id") or "unknown"),
            "author_role": "account",
            "text": trim_context_text(
                tweet_context_text(original_tweet),
                THREAD_CONTEXT_MAX_CHARS_PER_POST,
            ),
        },
        "parent_thread": [],
        "clarification_request": None,
        "current_date": current_datetime().strftime("%Y-%m-%d"),
    }
    log_json_debug("Quote-tweet context sent to AI reply pipeline", context)
    return context


def mark_quote_tweet_skipped(state: dict, quote_id: str) -> None:
    """Mark quote tweet skipped."""
    quote_id = str(quote_id)

    state["seen_quote_post_ids"] = append_unique_capped(
        state.get("seen_quote_post_ids", []),
        quote_id,
        2000,
    )
    state["skipped_quote_post_ids"] = append_unique_capped(
        state.get("skipped_quote_post_ids", []),
        quote_id,
        2000,
    )


def mark_quote_tweet_replied(state: dict, quote_id: str) -> None:
    """Mark quote tweet replied."""
    quote_id = str(quote_id)

    state["seen_quote_post_ids"] = append_unique_capped(
        state.get("seen_quote_post_ids", []),
        quote_id,
        2000,
    )
    state["replied_to_quote_post_ids"] = append_unique_durable(
        state.get("replied_to_quote_post_ids", []),
        quote_id,
    )


def mark_quote_spam_author(state: dict, author_id: str) -> None:
    """Mark quote spam author."""
    author_id = str(author_id)

    state["quote_spam_author_ids"] = append_unique_capped(
        state.get("quote_spam_author_ids", []),
        author_id,
        2000,
    )
    log.info("Marked author_id=%s as quote spam author. spam_author_count=%d", author_id, len(state["quote_spam_author_ids"]))


def maybe_reply_to_quote_tweets(state: dict) -> str:
    """Process eligible quote-tweet candidates under all reply limits."""
    log.info("Starting quote-tweet reply check")
    block_if_ambiguous_remote_post()

    if not ENABLE_QUOTE_TWEET_CHECKS:
        log.info("Quote-tweet checks disabled")
        return QUOTE_CHECK_STATUS_DISABLED

    if not ENABLE_AUTO_REPLIES:
        log.info("Auto replies disabled; skipping quote-tweet checks")
        return QUOTE_CHECK_STATUS_DISABLED

    if ai_first_reply_strategy.get("enabled") is not True:
        log.info("AI-first reply strategy disabled; skipping quote-tweet checks")
        return QUOTE_CHECK_STATUS_DISABLED

    if lane_paused("disable_replies", "disable_quote_replies"):
        log.info("Skipping quote-tweet check due to runtime control file")
        return QUOTE_CHECK_STATUS_DISABLED

    if in_api_cooldown(state, scope="write") or in_api_cooldown(state, scope="xai") or in_api_cooldown(state, scope="quote"):
        log.info("Skipping quote-tweet check due to API cooldown")
        return QUOTE_CHECK_STATUS_SKIPPED_COOLDOWN

    reset_daily_reply_count_if_needed(state)
    reset_daily_quote_reply_count_if_needed(state)
    if reconcile_confirmed_reply_receipt(state):
        log.warning("Reconciled confirmed reply receipt before checking new quote-tweet candidates")

    if int(state.get("daily_reply_count", 0) or 0) >= MAX_AUTO_REPLIES_PER_DAY:
        log.info("Skipping quote-tweet check: total daily reply cap reached")
        return QUOTE_CHECK_STATUS_SKIPPED_CAP

    if int(state.get("daily_quote_reply_count", 0) or 0) >= MAX_QUOTE_REPLIES_PER_DAY:
        log.info("Skipping quote-tweet check: daily quote-reply cap reached")
        return QUOTE_CHECK_STATUS_SKIPPED_CAP

    current = now_epoch()
    seconds_since_last_reply = current - int(state.get("last_reply_epoch", 0) or 0)

    if seconds_since_last_reply < MIN_SECONDS_BETWEEN_REPLIES:
        log.info(
            "Skipping quote-tweet check: minimum interval between replies not reached. seconds_since=%s",
            seconds_since_last_reply,
        )
        return QUOTE_CHECK_STATUS_SKIPPED_SPACING

    own_post_ids_for_quote_lookup = build_quote_lookup_post_ids(state)

    if not own_post_ids_for_quote_lookup:
        log.info("No own posts available for quote lookup")
        return QUOTE_CHECK_STATUS_CHECKED

    seen_quote_ids = set(str(x) for x in state.get("seen_quote_post_ids", []))
    replied_quote_ids = set(str(x) for x in state.get("replied_to_quote_post_ids", []))
    skipped_quote_ids = set(str(x) for x in state.get("skipped_quote_post_ids", []))
    quote_spam_author_ids = set(str(x) for x in state.get("quote_spam_author_ids", []))
    replied_to_ids = set(str(x) for x in state.get("replied_to_ids", []))
    daily_author_reply_counts(state)

    processed_candidates = 0

    for original_post_id in own_post_ids_for_quote_lookup:
        if processed_candidates >= MAX_QUOTE_POSTS_PER_CHECK:
            break

        try:
            original_tweet = get_tweet_by_id_cached(original_post_id, state)
        except ApiError as e:
            log.exception("Failed to fetch original own post %s", original_post_id)
            record_api_error(state, e, "x", scope="quote")
            save_state(state)
            continue
        except Exception:
            log.exception("Unexpected failure fetching original own post %s", original_post_id)
            save_state(state)
            continue

        if not original_tweet:
            log.info("Could not find/fetch original own post %s", original_post_id)
            continue

        try:
            quote_tweets = get_quote_tweets_for_post(original_post_id, state)
        except ApiError as e:
            log.exception("Failed to fetch quote tweets for post %s", original_post_id)
            record_api_error(state, e, "x", scope="quote")
            save_state(state)
            return QUOTE_CHECK_STATUS_CHECKED
        except Exception:
            log.exception("Unexpected failure fetching quote tweets for post %s", original_post_id)
            save_state(state)
            return QUOTE_CHECK_STATUS_CHECKED

        for quote_tweet in valid_tweets_sorted_by_id(quote_tweets, context="quote-tweet candidate"):
            if processed_candidates >= MAX_QUOTE_POSTS_PER_CHECK:
                break

            quote_id = str(quote_tweet.get("id", ""))
            author_id = str(quote_tweet.get("author_id", ""))
            quote_text = quote_tweet.get("text", "")

            if not quote_id:
                continue

            if author_id in quote_spam_author_ids:
                log.info(
                    "Skipping quote tweet %s: author_id=%s is in quote spam author list",
                    quote_id,
                    author_id,
                )
                mark_quote_tweet_skipped(state, quote_id)
                save_state(state)
                continue

            log.info(
                "Considering quote tweet id=%s author_id=%s original_post_id=%s text=%r",
                quote_id,
                author_id,
                original_post_id,
                quote_text,
            )

            if quote_id in seen_quote_ids or quote_id in replied_quote_ids or quote_id in skipped_quote_ids:
                log.info("Skipping quote tweet %s: already seen/replied/skipped", quote_id)
                continue

            prior_evaluation = terminal_reply_evaluation(state, quote_id)
            if prior_evaluation is not None:
                log.info(
                    "Skipping quote tweet %s: terminal %s evaluation already recorded reason=%s",
                    quote_id,
                    prior_evaluation.get("outcome", "no_reply"),
                    prior_evaluation.get("reason", ""),
                )
                mark_quote_tweet_skipped(state, quote_id)
                save_state(state)
                continue

            if not quote_tweet_directly_quotes_original(quote_tweet, original_post_id):
                log.info(
                    "Skipping quote tweet %s: not a direct quote of original post %s. referenced_tweets=%s",
                    quote_id,
                    original_post_id,
                    quote_tweet.get("referenced_tweets", []),
                )
                mark_quote_tweet_skipped(state, quote_id)
                save_state(state)
                continue

            if quote_id in replied_to_ids:
                log.info("Skipping quote tweet %s: already handled by normal mention path", quote_id)
                mark_quote_tweet_skipped(state, quote_id)
                save_state(state)
                continue

            if author_id == str(MY_USER_ID):
                log.info("Skipping quote tweet %s: authored by own account", quote_id)
                mark_quote_tweet_skipped(state, quote_id)
                save_state(state)
                continue

            if not quote_tweet_is_old_enough(quote_tweet):
                log.info(
                    "Quote tweet %s is too recent; leaving unmarked so it can be checked later",
                    quote_id,
                )
                continue

            if daily_author_reply_count(state, author_id) >= MAX_REPLIES_PER_AUTHOR_PER_DAY:
                log.info(
                    "Skipping quote tweet %s: already reached per-author daily cap for author_id=%s",
                    quote_id,
                    author_id,
                )
                mark_quote_tweet_skipped(state, quote_id)
                save_state(state)
                continue

            cleaned_quote_text = clean_text_for_reply_context(quote_text)
            cleaned_author_profile = clean_text_for_reply_context(
                quote_author_profile_text(quote_tweet)
            )

            if not cleaned_quote_text:
                log.info("Skipping quote tweet %s: no usable quote text after cleaning", quote_id)
                mark_quote_tweet_skipped(state, quote_id)
                log_event("quote_tweet_skipped", quote_tweet_id=quote_id, reason="no_usable_quote_text")
                save_state(state)
                continue

            spam_check_text = f"{cleaned_quote_text}\n{cleaned_author_profile}".strip()

            if is_probably_spam_or_not_worth_replying(spam_check_text):
                log.info(
                    "Skipping quote tweet %s: quote text/profile matched spam; marking author_id=%s as quote spam",
                    quote_id,
                    author_id,
                )
                log.debug("Quote spam check text for quote_id=%s: %r", quote_id, spam_check_text)
                mark_quote_tweet_skipped(state, quote_id)
                mark_quote_spam_author(state, author_id)
                quote_spam_author_ids.add(author_id)
                save_state(state)
                continue

            cache_tweet(
                state,
                tweet_id=quote_id,
                text=quote_text,
                author_id=author_id,
                conversation_id=str(quote_tweet.get("conversation_id", quote_id)),
                referenced_tweets=quote_tweet.get("referenced_tweets", []),
                created_at=quote_tweet.get("created_at"),
                post_type="quote_tweet",
            )
            save_state(state)

            reply_context = build_quote_tweet_reply_context(original_tweet, quote_tweet)

            processed_candidates += 1
            try:
                reply_evidence_repository()
            except ReplyEvidenceUnavailable as exc:
                log.error(
                    "Skipping quote-tweet reply target_id=%s because local evidence is unavailable: %s",
                    quote_id,
                    exc,
                )
                log_event(
                    "reply_evidence_unavailable",
                    lane="quote_tweet",
                    target_id=quote_id,
                )
                return QUOTE_CHECK_STATUS_CHECKED

            media_context = reply_media_context_for_candidate(
                quote_tweet,
                lane="quote_tweet",
                target_id=quote_id,
            )

            reply_text = pending_ai_reply(
                state,
                quote_id,
                "quote_tweet",
                context=reply_context,
                recent_replies=recent_auto_reply_texts(state),
            )
            evaluation_outcome: dict[str, str] = {}
            try:
                if reply_text is None:
                    reply_text = generate_ai_first_reply(
                        reply_context,
                        media_context,
                        recent_replies=recent_auto_reply_texts(state),
                        evaluation_outcome=evaluation_outcome,
                    )
                else:
                    log.info("Reusing persisted AI-first reply draft target_id=%s source=quote_tweet", quote_id)
            except ApiError as e:
                log.exception("Failed to ask Grok for quote-tweet reply")
                record_api_error(state, e, "xai")
                save_state(state)
                return QUOTE_CHECK_STATUS_CHECKED
            except Exception as e:
                log.exception("Unexpected Grok failure during quote-tweet reply")
                record_api_error(state, e, "xai")
                save_state(state)
                return QUOTE_CHECK_STATUS_CHECKED

            from reply_strategy import AIReply
            if not reply_text:
                if (
                    not DRY_RUN_REPLIES
                    and evaluation_outcome.get("status") == "no_reply"
                ):
                    record_terminal_reply_evaluation(
                        state,
                        target_id=quote_id,
                        lane="quote_tweet",
                        reason=evaluation_outcome.get("reason", "model_selected_no_reply"),
                    )
                log.info("No usable reply generated for quote tweet %s", quote_id)
                mark_quote_tweet_skipped(state, quote_id)
                save_state(state, durable=evaluation_outcome.get("status") == "no_reply")
                continue
            if not isinstance(reply_text, AIReply):
                log.error(
                    "AI-first reply pipeline returned an unapproved reply type; refusing quote_tweet target_id=%s",
                    quote_id,
                )
                record_terminal_reply_evaluation(
                    state,
                    target_id=quote_id,
                    lane="quote_tweet",
                    reason="reviewer_approval_missing",
                )
                mark_quote_tweet_skipped(state, quote_id)
                save_state(state, durable=True)
                continue

            log.info("Generated reply to quote tweet %s: %r", quote_id, reply_text)

            if not DRY_RUN_REPLIES:
                draft_stored = store_pending_ai_reply(
                    state,
                    quote_id,
                    "quote_tweet",
                    reply_text,
                    context=reply_context,
                )
                if not draft_stored:
                    log.error(
                        "AI-first reply draft failed persistence validation; "
                        "skipping target_id=%s source=quote_tweet",
                        quote_id,
                    )
                    mark_quote_tweet_skipped(state, quote_id)
                    save_state(state, durable=True)
                    continue
                save_state(state, durable=True)

            if DRY_RUN_REPLIES:
                log.warning("DRY_RUN_REPLIES=True, not posting generated quote-tweet reply")
                log_ai_reply_dry_run(
                    context=reply_context,
                    reply=reply_text,
                    lane="quote_tweet",
                    target_id=quote_id,
                )

                completion_epoch = now_epoch()
                reset_daily_reply_count_if_needed(state)
                reset_daily_quote_reply_count_if_needed(state)
                state["daily_reply_count"] = int(state.get("daily_reply_count", 0) or 0) + 1
                state["daily_quote_reply_count"] = int(state.get("daily_quote_reply_count", 0) or 0) + 1
                state["last_reply_epoch"] = completion_epoch

                mark_daily_author_replied(state, author_id)
                mark_quote_tweet_replied(state, quote_id)

                cache_tweet(
                    state,
                    tweet_id=f"dry-run-quote-reply-{quote_id}",
                    text=reply_text,
                    author_id=str(MY_USER_ID),
                    conversation_id=str(quote_tweet.get("conversation_id", quote_id)),
                    referenced_tweets=[
                        {
                            "type": "replied_to",
                            "id": str(quote_id),
                        }
                    ],
                    post_type="auto_reply",
                )
                save_state(state)
                return QUOTE_CHECK_STATUS_POSTED

            receipt_template = {
                "schema_version": 4,
                "lifecycle_state": "sending",
                "target_id": quote_id,
                "author_id": author_id,
                "candidate_source": "quote_tweet",
                "conversation_id": str(
                    quote_tweet.get("conversation_id", quote_id)
                ),
                "reply_text": reply_text,
                "original_post_id": str(original_post_id),
                "reply_context": copy.deepcopy(reply_context),
                "ai_reply_draft": copy.deepcopy(reply_text.draft_record),
            }
            receipt_template = bind_conversational_reply_attempt_time(
                receipt_template
            )
            try:
                reply_response, receipt = (
                    post_conversational_reply_with_durable_identity(
                        state=state,
                        receipt_template=receipt_template,
                        reply_text=reply_text,
                        reply_to_id=quote_id,
                        made_with_ai=MARK_AI_REPLIES_AS_AI,
                        lane="quote_tweet",
                    )
                )
            except UnrecoverableConfirmedReplyPersistenceError:
                log.critical(
                    "Confirmed quote-tweet reply lost every complete durable local "
                    "identity; the global remote-write safety barrier remains active",
                    exc_info=True,
                )
                raise
            except ConfirmedReplyLocalPersistenceError:
                log.critical(
                    "Confirmed quote-tweet reply required its durable state fallback",
                    exc_info=True,
                )
                raise
            except AmbiguousRemotePostOutcome:
                log.critical(
                    "Quote-tweet reply stopped after an ambiguous remote outcome; "
                    "the global remote-write safety barrier remains active",
                    exc_info=True,
                )
                log_ai_reply_posting_outcome(
                    reply=reply_text,
                    status="posting_failed_retryable",
                    lane="quote_tweet",
                    target_id=quote_id,
                    failure_reason="ambiguous_remote_outcome",
                )
                raise
            except ApiError as e:
                if api_error_is_reply_not_allowed(e):
                    log.warning(
                        "Cannot reply to quote tweet %s because X says replies are not allowed; "
                        "marking quote tweet as skipped without consuming reply quota",
                        quote_id,
                    )
                    log_ai_reply_posting_outcome(
                        reply=reply_text,
                        status="posting_failed_terminal",
                        lane="quote_tweet",
                        target_id=quote_id,
                        failure_reason="reply_not_permitted",
                    )
                    log_event(
                        "reply_target_terminal",
                        lane="quote_tweet",
                        target_id=quote_id,
                        outcome="reply_not_permitted",
                        reason="x_reply_not_permitted",
                    )
                    record_terminal_reply_evaluation(
                        state,
                        target_id=quote_id,
                        lane="quote_tweet",
                        reason="x_reply_not_permitted",
                        outcome="reply_not_permitted",
                    )
                    mark_quote_tweet_skipped(state, quote_id)
                    clear_pending_ai_reply(state, quote_id, "quote_tweet")
                    save_state(state, durable=True)
                    return QUOTE_CHECK_STATUS_CHECKED

                log.exception("Failed to post generated quote-tweet reply")
                log_ai_reply_posting_outcome(
                    reply=reply_text,
                    status="posting_failed_retryable",
                    lane="quote_tweet",
                    target_id=quote_id,
                    failure_reason=f"x_api_{getattr(e, 'status_code', 'error')}",
                )
                record_api_error(state, e, "x", scope="write")
                save_state(state)
                return QUOTE_CHECK_STATUS_CHECKED
            except Exception as e:
                log.exception("Unexpected failure posting generated quote-tweet reply")
                log_ai_reply_posting_outcome(
                    reply=reply_text,
                    status="posting_failed_retryable",
                    lane="quote_tweet",
                    target_id=quote_id,
                    failure_reason="unexpected_posting_error",
                )
                record_api_error(state, e, "x", scope="write")
                save_state(state)
                return QUOTE_CHECK_STATUS_CHECKED

            own_reply_id = str(receipt["reply_post_id"])

            apply_confirmed_reply_receipt(state, receipt)
            log.info("Recorded and cached own quote-tweet auto-reply id=%s", own_reply_id)
            save_state(state, durable=True)
            try:
                remove_confirmed_reply_receipt(receipt)
            except Exception as exc:
                log.critical(
                    "Confirmed quote-tweet reply id=%s to target=%s was saved but receipt removal failed",
                    own_reply_id,
                    quote_id,
                    exc_info=True,
                )
                raise ConfirmedReplyLocalPersistenceError(
                    f"Confirmed quote-tweet reply {own_reply_id} to {quote_id} but receipt removal failed"
                ) from exc

            log_event(
                "reply_posted",
                lane="quote_tweet",
                target_id=quote_id,
                author_id=author_id,
                original_post_id=original_post_id,
                reply_post_id=own_reply_id,
                daily_reply_count=state.get("daily_reply_count"),
                daily_quote_reply_count=state.get("daily_quote_reply_count"),
            )
            log.info("Quote-tweet reply posted successfully")
            return QUOTE_CHECK_STATUS_POSTED

    save_state(state)
    log.info("Quote-tweet reply check finished with no reply generated/posted")
    return QUOTE_CHECK_STATUS_CHECKED


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
    ambiguity_blocked = False
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

    reply_lane_priority = str(state.get("next_reply_lane_priority", "normal") or "normal")

    seconds_since_last_reply = current - int(state.get("last_reply_epoch", 0) or 0)
    reply_spacing_open = seconds_since_last_reply >= MIN_SECONDS_BETWEEN_REPLIES
    mention_check_due = ENABLE_AUTO_REPLIES and current - last_reply_check_epoch >= REPLY_CHECK_EVERY_SECONDS
    quote_check_due = (
        ENABLE_QUOTE_TWEET_CHECKS
        and current - last_quote_tweet_check_epoch >= QUOTE_CHECK_EVERY_SECONDS
    )

    def run_normal_check(*, forced: bool = False) -> bool:
        nonlocal last_reply_check_epoch, ambiguity_blocked

        if forced:
            log.info(
                "Quote-tweet check is due, but normal/hot-post reply lane has priority; "
                "running normal reply check first"
            )
        else:
            log.info("Due to check mentions")

        try:
            normal_check_status = maybe_reply_to_mentions(state)
        except (
            AmbiguousRemotePostOutcome,
            UnrecoverableConfirmedReplyPersistenceError,
        ):
            ambiguity_blocked = True
            log.critical(
                "Normal reply lane stopped by the global remote-write safety barrier"
            )
            return False
        log.info("Normal/hot-post reply check status=%s", normal_check_status)
        if ambiguous_remote_post_is_blocking():
            ambiguity_blocked = True
            log.critical("Normal reply lane created an ambiguous-post barrier; skipping all later lanes")
            return False

        if normal_check_status != NORMAL_CHECK_STATUS_SKIPPED_SPACING:
            last_reply_check_epoch = current
            state["last_reply_check_epoch"] = current
            save_state(state)
        else:
            log.info(
                "Normal/hot-post reply check skipped only because of reply spacing; "
                "normal check interval not consumed"
            )

        if normal_check_status == NORMAL_CHECK_STATUS_POSTED:
            state["next_reply_lane_priority"] = "quote"
            save_state(state)
            log.info("Normal/hot-post reply lane posted; next reply-lane priority=quote")
            return True

        if forced:
            log.info("Normal/hot-post reply lane did not post; quote-tweet lane may use this slot")

        return False

    def run_quote_check() -> bool:
        nonlocal last_quote_tweet_check_epoch, ambiguity_blocked

        log.info("Due to check quote tweets")
        priority_at_check = str(state.get("next_reply_lane_priority", reply_lane_priority) or reply_lane_priority)
        try:
            quote_check_status = maybe_reply_to_quote_tweets(state)
        except (
            AmbiguousRemotePostOutcome,
            UnrecoverableConfirmedReplyPersistenceError,
        ):
            ambiguity_blocked = True
            log.critical(
                "Quote-tweet lane stopped by the global remote-write safety barrier"
            )
            return False

        log.info("Quote-tweet check status=%s", quote_check_status)
        if ambiguous_remote_post_is_blocking():
            ambiguity_blocked = True
            log.critical("Quote-tweet lane created an ambiguous-post barrier; skipping all later lanes")
            return False
        log_event("quote_check_status", status=quote_check_status, priority=priority_at_check)

        if quote_check_status == QUOTE_CHECK_STATUS_POSTED:
            state["next_reply_lane_priority"] = "normal"
            save_state(state)
            log.info("Quote-tweet reply lane posted; next reply-lane priority=normal")

        if quote_check_status != QUOTE_CHECK_STATUS_SKIPPED_SPACING:
            last_quote_tweet_check_epoch = current
            state["last_quote_tweet_check_epoch"] = current
            save_state(state)
        else:
            retry_epoch = current - QUOTE_CHECK_EVERY_SECONDS + QUOTE_CHECK_SPACING_RETRY_SECONDS
            last_quote_tweet_check_epoch = retry_epoch
            state["last_quote_tweet_check_epoch"] = retry_epoch
            save_state(state)

            log.info(
                "Quote-tweet check skipped only because of reply spacing; "
                "will retry in about %d seconds",
                QUOTE_CHECK_SPACING_RETRY_SECONDS,
            )

        return quote_check_status == QUOTE_CHECK_STATUS_POSTED

    if reply_spacing_open and quote_check_due and reply_lane_priority == "quote":
        quote_posted = run_quote_check()
        if not quote_posted and mention_check_due and not ambiguity_blocked:
            run_normal_check()
    elif mention_check_due:
        normal_posted = run_normal_check()
        if not normal_posted and quote_check_due and not ambiguity_blocked:
            run_quote_check()
    elif (
        reply_spacing_open
        and quote_check_due
        and reply_lane_priority == "normal"
        and ENABLE_AUTO_REPLIES
    ):
        normal_posted = run_normal_check(forced=True)
        if not normal_posted and not ambiguity_blocked:
            run_quote_check()
    elif quote_check_due:
        run_quote_check()
    else:
        log.debug(
            "Not due to check mentions. seconds_until_next=%s",
            max(0, REPLY_CHECK_EVERY_SECONDS - (current - last_reply_check_epoch)),
        )
        log.debug(
            "Not due to check quote tweets. seconds_until_next=%s",
            max(0, QUOTE_CHECK_EVERY_SECONDS - (current - last_quote_tweet_check_epoch)),
        )

    return last_reply_check_epoch, last_quote_tweet_check_epoch


def main() -> None:
    """Run the command-line entry point."""
    require_production_bootstrap()
    require_established_installation()
    block_if_ambiguous_remote_post()
    random.seed()
    acquire_instance_lock()
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
    log.info("Config: DRY_RUN_REPLIES=%s", DRY_RUN_REPLIES)
    log.info("Config: REPLY_CHECK_EVERY_SECONDS=%s", REPLY_CHECK_EVERY_SECONDS)
    log.info("Config: MAX_AUTO_REPLIES_PER_DAY=%s", MAX_AUTO_REPLIES_PER_DAY)
    log.info("Config: MAX_REPLIES_PER_AUTHOR_PER_DAY=%s", MAX_REPLIES_PER_AUTHOR_PER_DAY)
    log.info("Config: MAX_MENTIONS_PER_CHECK=%s", MAX_MENTIONS_PER_CHECK)
    log.info("Config: MENTIONS_MAX_PAGES_PER_CHECK=%s", MENTIONS_MAX_PAGES_PER_CHECK)
    log.info("Config: MIN_SECONDS_BETWEEN_REPLIES=%s", MIN_SECONDS_BETWEEN_REPLIES)
    log.info("Config: ALWAYS_FETCH_PARENT_FOR_CONTEXT=%s", ALWAYS_FETCH_PARENT_FOR_CONTEXT)
    log.info("Config: SKIP_REPLIES_TO_OWN_AUTO_REPLIES=%s", SKIP_REPLIES_TO_OWN_AUTO_REPLIES)
    log.info("Config: THREAD_CONTEXT_MAX_DEPTH=%s", THREAD_CONTEXT_MAX_DEPTH)
    log.info("Config: THREAD_CONTEXT_MAX_CHARS_PER_POST=%s", THREAD_CONTEXT_MAX_CHARS_PER_POST)
    log.info("Config: THREAD_CONTEXT_MAX_TOTAL_CHARS=%s", THREAD_CONTEXT_MAX_TOTAL_CHARS)
    log.info("Config: TWEET_CACHE_MAX_AGE_SECONDS=%s", TWEET_CACHE_MAX_AGE_SECONDS)
    log.info("Config: TWEET_CACHE_MAX_ITEMS=%s", TWEET_CACHE_MAX_ITEMS)
    log.info("Config: STATE_BACKUP_COUNT=%s", STATE_BACKUP_COUNT)
    log.info("Config: XAI_MODEL=%s", XAI_MODEL)

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
    startup_current = now_epoch()
    reconcile_startup_main_post_receipts(
        lines_used,
        images_used,
        state,
        startup_current,
    )

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

    ambiguity_pause_logged = False
    maintenance_pause_logged = global_remote_writes_paused()
    while True:
        if ambiguous_remote_post_is_blocking():
            if not ambiguity_pause_logged:
                try:
                    durable_marker_confirmed = (
                        durable_remote_write_safety_barrier_exists()
                    )
                except Exception:
                    durable_marker_confirmed = False
                    log.critical(
                        "The remote-write safety marker could not be inspected; the "
                        "process will remain latched and must not be restarted",
                        exc_info=True,
                    )
                if durable_marker_confirmed:
                    log.critical(
                        "All remote posting and reply lanes are paused by the durable "
                        "remote-write safety barrier; manual reconciliation is required "
                        "before a controlled restart"
                    )
                else:
                    log.critical(
                        "All remote posting and reply lanes are paused by an in-process-only "
                        "remote-write safety latch because its durable marker could not be "
                        "written; do not restart before manual reconciliation"
                    )
                ambiguity_pause_logged = True
            sleep(60)
            continue
        ambiguity_pause_logged = False

        current = now_epoch()
        log.debug("Main loop tick. epoch=%s", current)

        if global_remote_writes_paused():
            if not maintenance_pause_logged:
                log.warning(
                    "Global runtime control pause is active; all remote-write lanes "
                    "remain idle"
                )
            maintenance_pause_logged = True
            sleep(60)
            continue
        if maintenance_pause_logged:
            log.info("Global runtime control pause cleared; resuming scheduled lanes")
        maintenance_pause_logged = False

        safely_process_due_historical_context_obligations(
            limit=1,
            runtime_state=state,
        )
        if ambiguous_remote_post_is_blocking():
            continue

        last_reply_check_epoch, last_quote_tweet_check_epoch = run_reply_lane_checks_for_tick(
            state,
            current,
            last_reply_check_epoch,
            last_quote_tweet_check_epoch,
        )
        if ambiguous_remote_post_is_blocking():
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

                if not quote_posted:
                    schedule_next_quote_post(state, current)
        else:
            log.debug(
                "Not due to post quote/image. seconds_until_next=%s",
                max(0, next_quote_epoch - current),
            )

        if ambiguous_remote_post_is_blocking():
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
            else:
                log.debug(
                    "Not due to post daily meme. seconds_until_next=%s",
                    max(0, next_meme_epoch - current),
                )

        log.debug("Sleeping for 60 seconds")
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
    """Run local checks without posting or calling X/xAI."""
    require_production_bootstrap()
    log.info("Running self-test only; no X or xAI API calls will be made")
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

    _self_test_warn("local config file present", LOCAL_CONFIG_FILE.exists(), str(LOCAL_CONFIG_FILE))
    if LOCAL_CONFIG_FILE.exists():
        try:
            with open(LOCAL_CONFIG_FILE, "r") as f:
                cfg = json.load(f)
            require("local config is JSON object", isinstance(cfg, dict), str(type(cfg).__name__))
        except Exception as exc:
            require("local config parses", False, str(exc))

    _self_test_warn("runtime control file absent", not CONTROL_FILE.exists(), str(CONTROL_FILE))
    if CONTROL_FILE.exists():
        try:
            with open(CONTROL_FILE, "r") as f:
                ctrl = json.load(f)
            require("runtime control is JSON object", isinstance(ctrl, dict), str(type(ctrl).__name__))
        except Exception as exc:
            require("runtime control parses", False, str(exc))

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
    if ENABLE_AUTO_REPLIES:
        require("XAI_API_KEY set when auto replies enabled", bool(XAI_API_KEY))
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
    require_production_bootstrap()
    require_established_installation()
    block_if_ambiguous_remote_post()
    if os.getenv("MRS_TEST_MODE") != "1":
        log.error("--test-cycle requires MRS_TEST_MODE=1")
        return 2

    acquire_instance_lock()
    reconcile_runtime_historical_context_state()

    log.info("Running one test cycle")
    log.info("Base dir=%s", BASE_DIR)
    log.info("State file=%s", STATE_FILE)
    log.info("Log file=%s", LOG_FILE)
    log.info("X base=%s", X_BASE)
    log.info("X upload base=%s", X_UPLOAD_BASE)
    log.info("xAI base=%s", XAI_BASE)

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
    require_production_bootstrap()
    require_established_installation()
    block_if_ambiguous_remote_post()
    if not require_test_mode("--test-main-tick"):
        return 2

    acquire_instance_lock()
    reconcile_runtime_historical_context_state()

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

    run_reply_lane_checks_for_tick(
        state,
        current,
        last_reply_check_epoch=last_reply_check_epoch,
        last_quote_tweet_check_epoch=last_quote_tweet_check_epoch,
    )
    if ambiguous_remote_post_is_blocking():
        wait_for_durable_barrier_before_one_shot_exit(
            lane="production_reply_tick",
        )
        return 0

    save_state(state)
    log.info("Test production reply-lane tick finished")
    return 0


def require_test_mode(command_name: str) -> bool:
    """Require test mode."""
    if os.getenv("MRS_TEST_MODE") != "1":
        log.error("%s requires MRS_TEST_MODE=1", command_name)
        return False
    return True


def prepare_test_main_post_state(state: dict) -> None:
    """Prepare test main post state."""
    if ENABLE_DAILY_MEME_POSTS:
        ensure_meme_schedule_initialized(state)


def wait_for_durable_barrier_before_one_shot_exit(*, lane: str) -> None:
    """Keep a one-shot posting process alive while its only barrier is memory."""
    if (
        not _AMBIGUOUS_REMOTE_POST_SEEN
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
    require_production_bootstrap()
    require_established_installation()
    block_if_ambiguous_remote_post()
    if not require_test_mode("--test-post-quote"):
        return 2

    acquire_instance_lock()
    reconcile_runtime_historical_context_state()

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
    require_production_bootstrap()
    require_established_installation()
    block_if_ambiguous_remote_post()
    if not require_test_mode("--test-post-meme"):
        return 2

    acquire_instance_lock()
    reconcile_runtime_historical_context_state()

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


if __name__ == "__main__":
    try:
        production_bootstrap()
        if INITIALISE_REQUESTED:
            sys.exit(initialise_installation())
        if SELF_TEST_REQUESTED:
            sys.exit(run_self_test())
        if TEST_CYCLE_REQUESTED:
            sys.exit(run_test_cycle())
        if TEST_MAIN_TICK_REQUESTED:
            sys.exit(run_test_main_tick())
        if TEST_POST_QUOTE_REQUESTED:
            sys.exit(run_test_post_quote())
        if TEST_POST_MEME_REQUESTED:
            sys.exit(run_test_post_meme())
        main()
    except KeyboardInterrupt:
        log.warning("Bot stopped by KeyboardInterrupt")
    except Exception:
        log.exception("Bot crashed with unhandled exception")
        raise
