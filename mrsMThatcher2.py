#!/usr/bin/env python3

from __future__ import annotations

import html
import copy
import fcntl
import hashlib
import json
import logging
import mimetypes
import os
import random
import re
import shutil
import sys
from datetime import datetime, timedelta
from glob import glob
from logging.handlers import RotatingFileHandler
from pathlib import Path
from time import sleep
from urllib.parse import urlsplit, urlunsplit

import requests
from requests_oauthlib import OAuth1


SELF_TEST_REQUESTED = "--self-test" in sys.argv
TEST_CYCLE_REQUESTED = "--test-cycle" in sys.argv
TEST_MAIN_TICK_REQUESTED = "--test-main-tick" in sys.argv
TEST_POST_QUOTE_REQUESTED = "--test-post-quote" in sys.argv
TEST_POST_MEME_REQUESTED = "--test-post-meme" in sys.argv
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

MIN_SECONDS_BETWEEN_REPLIES = 3600

DRY_RUN_REPLIES = False
MARK_AI_REPLIES_AS_AI = False

ALWAYS_FETCH_PARENT_FOR_CONTEXT = True
SKIP_REPLIES_TO_OWN_AUTO_REPLIES = False

THREAD_CONTEXT_MAX_DEPTH = 3
THREAD_CONTEXT_MAX_CHARS_PER_POST = 500
THREAD_CONTEXT_MAX_TOTAL_CHARS = 1500

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
MAX_GROK_OUTPUT_TOKENS = 120

# ---------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------

PRODUCTION_BASE_DIR = Path("/disks/disk1/etc/mrsMThatcher")
BASE_DIR = Path(os.getenv("MRS_BASE_DIR", str(PRODUCTION_BASE_DIR))).expanduser()


def path_is_same_or_child(path: Path, parent: Path) -> bool:
    try:
        path_resolved = path.resolve()
        parent_resolved = parent.resolve()
        return path_resolved == parent_resolved or parent_resolved in path_resolved.parents
    except Exception:
        path_abs = path.absolute()
        parent_abs = parent.absolute()
        return path_abs == parent_abs or parent_abs in path_abs.parents


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
QUOTE_ANALYSIS_OVERRIDES_FILE = BASE_DIR / "quote_analysis_overrides.json"

LINES_USED_FILE = BASE_DIR / "lines_used.json"
IMAGES_USED_FILE = BASE_DIR / "images_used.json"
REGULAR_POST_RECEIPT_FILE = BASE_DIR / "regular_post_receipt.json"
MEME_POST_RECEIPT_FILE = BASE_DIR / "meme_post_receipt.json"
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

def setup_logging() -> logging.Logger:
    BASE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    level_name = os.getenv("LOG_LEVEL", "DEBUG").upper()
    level = getattr(logging, level_name, logging.DEBUG)

    logger = logging.getLogger("mrsMThatcher")
    logger.setLevel(level)
    logger.handlers.clear()

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)-8s %(funcName)s:%(lineno)d - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    file_handler = RotatingFileHandler(
        LOG_FILE,
        maxBytes=2_000_000,
        backupCount=5,
    )
    file_handler.setLevel(level)
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    logger.debug("Logging initialised. LOG_LEVEL=%s LOG_FILE=%s", level_name, LOG_FILE)
    return logger


log = setup_logging()
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
    if not value:
        return "<missing>"
    if len(value) <= visible * 2:
        return "<set-but-short>"
    return f"{value[:visible]}...{value[-visible:]}"


def log_json_debug(label: str, obj: object, max_chars: int = 4000) -> None:
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
    "MAX_GROK_OUTPUT_TOKENS",

    # Operational hardening
    "STATE_BACKUP_COUNT",
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
    "MAX_GROK_OUTPUT_TOKENS",
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
    "MAX_GROK_OUTPUT_TOKENS",
}


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
        if isinstance(value, bool):
            raise ValueError(f"{key} must be an integer, not a boolean")
        coerced = int(value)
        if key in LOCAL_CONFIG_NON_NEGATIVE_INT_KEYS and coerced < 0:
            raise ValueError(f"{key} must be non-negative")
        if key in LOCAL_CONFIG_POSITIVE_INT_KEYS and coerced <= 0:
            raise ValueError(f"{key} must be positive")
        return coerced

    if isinstance(current_value, str):
        return str(value)

    return value


def validate_runtime_config_values(values: dict[str, object]) -> list[str]:
    """Return validation errors for runtime config values.

    This is intentionally conservative for local overrides. Script defaults are
    expected to pass, and invalid local values are rolled back before startup.
    """
    errors: list[str] = []

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
        "MAX_REPLY_CHARS",
        "MAX_GROK_OUTPUT_TOKENS",
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
    except Exception:
        log.exception("Failed to read local config file %s; using script defaults", LOCAL_CONFIG_FILE)
        return

    if not isinstance(data, dict):
        log.error("Local config file %s is not a JSON object; ignoring it", LOCAL_CONFIG_FILE)
        return

    applied: dict[str, object] = {}
    ignored: list[str] = []
    original_values = {
        key: globals()[key]
        for key in data
        if key in LOCAL_CONFIG_ALLOWED_KEYS and key in globals()
    }

    for key, value in data.items():
        if key not in LOCAL_CONFIG_ALLOWED_KEYS or key not in globals():
            ignored.append(str(key))
            continue

        try:
            coerced = _coerce_local_config_value(key, value, globals()[key])
        except Exception as exc:
            log.error("Ignoring invalid local config override %s=%r: %s", key, value, exc)
            continue

        globals()[key] = coerced
        applied[key] = coerced

    for key in list(applied):
        trial = {name: globals()[name] for name in LOCAL_CONFIG_ALLOWED_KEYS if name in globals()}
        errors = validate_runtime_config_values(trial)
        if not errors:
            break

        if key not in original_values:
            continue

        current_value = globals()[key]
        globals()[key] = original_values[key]
        trial[key] = original_values[key]
        remaining_errors = validate_runtime_config_values(
            {name: globals()[name] for name in LOCAL_CONFIG_ALLOWED_KEYS if name in globals()}
        )
        if len(remaining_errors) < len(errors):
            log.error(
                "Ignoring unsafe local config override %s=%r: %s",
                key,
                current_value,
                "; ".join(errors),
            )
            applied.pop(key, None)
        else:
            globals()[key] = current_value

    final_errors = validate_runtime_config_values(
        {name: globals()[name] for name in LOCAL_CONFIG_ALLOWED_KEYS if name in globals()}
    )
    for error in final_errors:
        log.error("Runtime config validation warning: %s", error)

    if ignored:
        log.warning("Ignoring unsupported local config key(s): %s", ", ".join(sorted(ignored)))

    if applied:
        log.info("Applied %d local config override(s) from %s", len(applied), LOCAL_CONFIG_FILE)
        log_json_debug("Local config overrides applied", applied)
    else:
        log.info("Local config file present but no valid overrides applied: %s", LOCAL_CONFIG_FILE)


apply_local_config()

RUNTIME_CONFIG_ERRORS = validate_runtime_config_values(
    {name: globals()[name] for name in LOCAL_CONFIG_ALLOWED_KEYS if name in globals()}
)
if RUNTIME_CONFIG_ERRORS:
    for error in RUNTIME_CONFIG_ERRORS:
        log.critical("Invalid runtime config: %s", error)
    if not SELF_TEST_REQUESTED:
        sys.exit(1)


# ---------------------------------------------------------------------
# Runtime control / pause file
# ---------------------------------------------------------------------

_CONTROL_CACHE: dict[str, object] = {"mtime": None, "data": {}}


def parse_control_time(value: object) -> int:
    if value in (None, ""):
        return 0

    if isinstance(value, (int, float)):
        return int(value)

    text = str(value).strip()
    if not text:
        return 0

    if text.isdigit():
        return int(text)

    # Accept common local-time strings such as "2026-06-30 18:00" or ISO-ish values.
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M"):
        try:
            return int(datetime.strptime(text, fmt).timestamp())
        except ValueError:
            pass

    try:
        return int(datetime.fromisoformat(text).timestamp())
    except ValueError as exc:
        raise ValueError(f"Cannot parse control time {value!r}") from exc


def load_control() -> dict:
    try:
        stat = CONTROL_FILE.stat()
    except FileNotFoundError:
        _CONTROL_CACHE["mtime"] = None
        _CONTROL_CACHE["data"] = {}
        return {}
    except OSError:
        log.exception("Could not stat control file %s; failing open", CONTROL_FILE)
        return {}

    if _CONTROL_CACHE.get("mtime") == stat.st_mtime:
        data = _CONTROL_CACHE.get("data", {})
        return data if isinstance(data, dict) else {}

    try:
        with open(CONTROL_FILE, "r") as f:
            data = json.load(f)
    except Exception:
        log.exception("Failed to read control file %s; failing open", CONTROL_FILE)
        _CONTROL_CACHE["mtime"] = stat.st_mtime
        _CONTROL_CACHE["data"] = {}
        return {}

    if not isinstance(data, dict):
        log.error("Control file %s is not a JSON object; failing open", CONTROL_FILE)
        data = {}

    _CONTROL_CACHE["mtime"] = stat.st_mtime
    _CONTROL_CACHE["data"] = data
    log.info("Loaded runtime control file %s", CONTROL_FILE)
    log_json_debug("Runtime control", data)
    return data


def control_bool(data: dict, key: str) -> bool:
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


# ---------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------

CONSUMER_KEY = os.getenv("X_CONSUMER_KEY", "")
CONSUMER_SECRET = os.getenv("X_CONSUMER_SECRET", "")
ACCESS_TOKEN = os.getenv("X_ACCESS_TOKEN", "")
ACCESS_SECRET = os.getenv("X_ACCESS_SECRET", "")
MY_USER_ID = os.getenv("X_MY_USER_ID", "")
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

if not all([CONSUMER_KEY, CONSUMER_SECRET, ACCESS_TOKEN, ACCESS_SECRET, MY_USER_ID]):
    log.critical(
        "Missing X credentials. Set X_CONSUMER_KEY, X_CONSUMER_SECRET, "
        "X_ACCESS_TOKEN, X_ACCESS_SECRET, X_MY_USER_ID"
    )
    if not SELF_TEST_REQUESTED:
        sys.exit(1)

if ENABLE_AUTO_REPLIES and not XAI_API_KEY:
    log.critical("ENABLE_AUTO_REPLIES is True, but XAI_API_KEY is not set.")
    if not SELF_TEST_REQUESTED:
        sys.exit(1)

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


def parse_request_timeout_seconds() -> float:
    raw = os.getenv("MRS_REQUEST_TIMEOUT_SECONDS", "60")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        log.error("Invalid MRS_REQUEST_TIMEOUT_SECONDS=%r; using default 60", raw)
        return 60.0

    if value <= 0:
        log.error("Invalid MRS_REQUEST_TIMEOUT_SECONDS=%r; using default 60", raw)
        return 60.0

    return value


REQUEST_TIMEOUT_SECONDS = parse_request_timeout_seconds()

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
    def __init__(
        self,
        message: str,
        *,
        service: str,
        status_code: int | None = None,
        reset_epoch: int | None = None,
    ) -> None:
        super().__init__(message)
        self.service = service
        self.status_code = status_code
        self.reset_epoch = reset_epoch


def api_error_is_reply_not_allowed(error: Exception) -> bool:
    """
    X can return 403 when the target post's conversation controls do not allow
    this account to reply. This is not a transient API fault and should not
    consume reply quota or trigger the circuit breaker.
    """
    status_code = getattr(error, "status_code", None)
    message = str(error).lower()

    return (
        status_code == 403
        and (
            "reply to this conversation is not allowed" in message
            or "not been mentioned or otherwise engaged by the author" in message
            or "not allowed to reply" in message
        )
    )


def api_error_is_permanent_target_failure(error: Exception) -> bool:
    """Return true for target-specific failures that should not trip breakers."""
    return getattr(error, "status_code", None) in {403, 404}


# ---------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------

def coerce_used_set(value: object, *, path: Path) -> set:
    if isinstance(value, set):
        return value
    if isinstance(value, list):
        return set(value)

    raise ValueError(f"Used-history file {path} must contain a JSON list")


def used_set_to_sorted_list(value: set) -> list:
    def sort_key(item: object) -> tuple[int, int | str]:
        try:
            return (0, int(item))
        except Exception:
            return (1, str(item))

    return sorted(value, key=sort_key)


def load_used_set(path: Path, *, legacy_pickle_path: Path | None = None) -> set:
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
    item_text = str(item)
    existing = [str(value) for value in values] if isinstance(values, list) else []
    existing = [value for value in existing if value != item_text]
    existing.append(item_text)
    return existing[-max_items:]


def normalise_state_int(value: object, *, key: str, path: Path) -> int | None:
    if isinstance(value, bool):
        log.error("State candidate %s has invalid %s boolean value %r; ignoring", path, key, value)
        return None
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        log.error("State candidate %s has invalid %s value %r; ignoring", path, key, value)
        return None
    if number < 0:
        log.error("State candidate %s has negative %s value %r; ignoring", path, key, value)
        return None
    return number


def normalise_state_epoch(value: object, *, key: str, path: Path) -> int | None:
    number = normalise_state_int(value, key=key, path=path)
    if number is None:
        return None
    if number > MAX_REASONABLE_STATE_EPOCH:
        log.error("State candidate %s has impossible epoch %s=%r; ignoring", path, key, value)
        return None
    return number


def normalise_string_list(value: object, *, key: str, path: Path) -> list[str] | None:
    if not isinstance(value, list):
        log.error("State candidate %s has invalid %s type %s; ignoring", path, key, type(value).__name__)
        return None
    return [str(item) for item in value if item is not None]


def normalise_int_list(value: object, *, key: str, path: Path) -> list[int] | None:
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
    out = normalise_int_list(value, key=key, path=path)
    if out is None:
        return None
    for number in out:
        if number > MAX_REASONABLE_STATE_EPOCH:
            log.error("State candidate %s has impossible %s epoch item %r; ignoring", path, key, number)
            return None
    return out


def normalise_string_map(value: object, *, key: str, path: Path) -> dict[str, str] | None:
    if not isinstance(value, dict):
        log.error("State candidate %s has invalid %s type %s; ignoring", path, key, type(value).__name__)
        return None
    return {str(k): str(v) for k, v in value.items() if v is not None}


def normalise_int_map(value: object, *, key: str, path: Path) -> dict[str, int] | None:
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
    if not isinstance(value, dict):
        log.error("State candidate %s has invalid mention_pagination type %s; ignoring", path, type(value).__name__)
        return None
    out: dict[str, str] = {}
    for key in ("base_since_id", "next_token"):
        if value.get(key):
            out[key] = str(value[key])
    return out


def normalise_optional_scalar(value: object, *, key: str, path: Path) -> str | None:
    if value is None:
        return ""
    if isinstance(value, (str, int)):
        return str(value)
    log.error("State candidate %s has invalid %s type %s; ignoring", path, key, type(value).__name__)
    return None


def normalise_optional_numeric_id(value: object, *, key: str, path: Path) -> str | None:
    if value in (None, ""):
        return ""
    text = str(value)
    if text.isdigit():
        return text
    log.error("State candidate %s has invalid %s value %r; ignoring", path, key, value)
    return None


def validate_meme_schedule_state(state: dict, *, path: Path) -> bool:
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
    record_map_keys = {"skipped_hot_reply_records"}
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
    raw_value = state.get(key, 0)
    try:
        value = int(raw_value or 0)
    except (TypeError, ValueError):
        log.warning("Ignoring malformed scheduler epoch %s=%r", key, raw_value)
        value = 0

    if value < 0:
        log.warning("Ignoring negative scheduler epoch %s=%r", key, raw_value)
        value = 0

    if current is not None and value > current:
        log.warning("Ignoring future scheduler epoch %s=%r current=%s", key, raw_value, current)
        value = 0

    if raw_value != value:
        state[key] = value
        return value, True

    return value, False


def fsync_file(path: Path) -> None:
    with open(path, "rb") as f:
        os.fsync(f.fileno())


def copy_state_backup(src: Path, dst: Path, *, durable: bool = False) -> None:
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
    if STATE_BACKUP_COUNT <= 0 or not STATE_FILE.exists():
        return
    bak1 = STATE_FILE.with_name(f"{STATE_FILE.name}.bak1")
    copy_state_backup(STATE_FILE, bak1, durable=durable)
    log.debug("Latest committed state backup written: %s", bak1)


def save_state(state: dict, *, durable: bool = False) -> None:
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
    write_latest_state_backup(durable=durable)


def reset_daily_reply_count_if_needed(state: dict) -> None:
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
    return daily_author_reply_counts(state).get(str(author_id), 0)


def mark_daily_author_replied(state: dict, author_id: str) -> None:
    author_id = str(author_id)
    counts = daily_author_reply_counts(state)
    counts[author_id] = counts.get(author_id, 0) + 1
    state["daily_replied_author_counts"] = counts

    state["daily_replied_author_ids"] = append_unique_capped(
        state.get("daily_replied_author_ids", []),
        author_id,
        1000,
    )


# ---------------------------------------------------------------------
# Time / cooldown / error handling
# ---------------------------------------------------------------------

def now_epoch() -> int:
    if TEST_MODE and os.getenv("MRS_FAKE_NOW_EPOCH"):
        return int(os.getenv("MRS_FAKE_NOW_EPOCH", "0"))
    return int(datetime.now().timestamp())


def current_datetime() -> datetime:
    return datetime.fromtimestamp(now_epoch())


def parse_x_datetime_to_epoch(value: str | None) -> int | None:
    if not value:
        return None

    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return int(parsed.timestamp())
    except Exception:
        log.warning("Could not parse X datetime: %r", value)
        return None


def parse_tweet_id(value: object, *, context: str) -> int | None:
    value_str = str(value or "")
    if not value_str.isdigit():
        log.warning("Skipping %s with invalid tweet id=%r", context, value)
        return None
    return int(value_str)


def valid_tweets_sorted_by_id(tweets: list[dict], *, context: str) -> list[dict]:
    valid: list[tuple[int, dict]] = []
    for tweet in tweets:
        tweet_id = parse_tweet_id(tweet.get("id"), context=context)
        if tweet_id is None:
            continue
        valid.append((tweet_id, tweet))
    return [tweet for _, tweet in sorted(valid, key=lambda item: item[0])]


def in_api_cooldown(state: dict, *, scope: str = "api") -> bool:
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
    state = load_state()
    clear_expired_api_cooldowns(state)
    sanitize_next_reply_lane_priority(state)
    return state


def prune_error_epochs(epochs: list[int]) -> list[int]:
    cutoff = now_epoch() - ERROR_WINDOW_SECONDS
    pruned = [int(e) for e in epochs if int(e) >= cutoff]
    log.debug("Pruned error epochs from %d to %d", len(epochs), len(pruned))
    return pruned


def cooldown_until_for_rate_limit(current: int, reset_epoch: int | None) -> int:
    if reset_epoch and reset_epoch > current:
        return reset_epoch + 60
    return current + COOLDOWN_AFTER_429_SECONDS


def record_api_error(state: dict, error: Exception, service: str, *, scope: str = "api") -> None:
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


def x_request(method: str, path: str, **kwargs) -> dict:
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

    try:
        response = requests.request(
            method,
            url,
            auth=AUTH,
            timeout=REQUEST_TIMEOUT_SECONDS,
            **kwargs,
        )
    except requests.RequestException as e:
        log.exception("X request failed before receiving response")
        raise ApiError(str(e), service="x") from e

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

        raise ApiError(
            f"X API error {response.status_code}: {response.text}",
            service="x",
            status_code=response.status_code,
            reset_epoch=reset_epoch,
        )

    if not response.text:
        log.debug("X response has empty body")
        return {}

    try:
        data = response.json()
    except json.JSONDecodeError as e:
        log.error("X API returned non-JSON response: %s", response.text[:1000])
        raise ApiError(f"X API returned non-JSON response: {response.text[:500]}", service="x") from e

    log_json_debug("X response json", data)
    return data


def x_bearer_request(method: str, path: str, **kwargs) -> dict:
    if not X_BEARER_TOKEN:
        raise ApiError("X_BEARER_TOKEN is not set", service="x")

    url = f"{X_BASE}{path}"

    log.debug("X bearer request: %s %s", method, url)

    if "params" in kwargs:
        log_json_debug("X bearer request params", kwargs["params"])

    try:
        response = requests.request(
            method,
            url,
            headers={
                "Authorization": f"Bearer {X_BEARER_TOKEN}",
            },
            timeout=REQUEST_TIMEOUT_SECONDS,
            **kwargs,
        )
    except requests.RequestException as e:
        log.exception("X bearer request failed before receiving response")
        raise ApiError(str(e), service="x") from e

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
        ) from e

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


def x_paginated_get(request_func, path: str, params: dict, *, max_pages: int, label: str) -> dict:
    combined: dict[str, object] = {"data": []}
    users_by_id: dict[str, dict] = {}
    next_token = ""
    pages_fetched = 0

    for page in range(1, max(1, int(max_pages)) + 1):
        pages_fetched = page
        page_params = dict(params)
        if next_token:
            page_params["pagination_token"] = next_token

        result = request_func(path, page_params)
        page_data = result.get("data", [])
        if isinstance(page_data, list):
            combined["data"].extend(page_data)

        for user in result.get("includes", {}).get("users", []) or []:
            user_id = str(user.get("id", ""))
            if user_id:
                users_by_id[user_id] = user

        next_token = str((result.get("meta", {}) or {}).get("next_token", "") or "")
        log.info(
            "Fetched %s page %d/%d items=%d next_token=%s",
            label,
            page,
            max_pages,
            len(page_data) if isinstance(page_data, list) else 0,
            bool(next_token),
        )
        if not next_token:
            break

    if users_by_id:
        combined["includes"] = {"users": list(users_by_id.values())}
    combined["_pagination"] = {
        "pages_fetched": pages_fetched,
        "truncated": bool(next_token),
        "next_token": next_token or None,
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
    tweet_id = str(tweet_id)

    ids = [str(x) for x in state.get("recent_own_post_ids", []) if str(x) != tweet_id]
    ids.insert(0, tweet_id)
    state["recent_own_post_ids"] = ids[:RECENT_OWN_POST_IDS_MAX]

    log.info("Recorded recent own post id=%s recent_count=%d", tweet_id, len(state["recent_own_post_ids"]))


def seed_recent_own_post_ids_from_cache(state: dict) -> None:
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
    for ref in tweet.get("referenced_tweets", []):
        if ref.get("type") == "replied_to":
            parent_id = ref.get("id")
            if parent_id:
                return str(parent_id)

    return None


def get_tweet_by_id(tweet_id: str) -> dict | None:
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
    log_json_debug("Fetched tweet", tweet)

    return tweet


def get_tweet_by_id_cached(tweet_id: str, state: dict) -> dict | None:
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


def clean_text_for_grok_context(text: str) -> str:
    text = html.unescape(text or "")
    text = re.sub(r"https?://\S+", "", text)
    text = " ".join(text.split())
    return text.strip()


def tweet_context_text(tweet: dict) -> str:
    cleaned = clean_text_for_grok_context(tweet.get("text", ""))

    if cleaned:
        return cleaned

    image_summary = clean_text_for_grok_context(tweet.get("image_summary", ""))
    if image_summary:
        return f"[Image/meme summary: {image_summary}]"

    return ""


def trim_context_text(text: str, max_chars: int) -> str:
    text = clean_text_for_grok_context(text)

    if len(text) <= max_chars:
        return text

    return text[:max_chars].rsplit(" ", 1)[0].rstrip(".,;:") + "..."


def build_parent_chain(mention: dict, state: dict) -> list[dict]:
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
    if not tweet:
        return False

    if str(tweet.get("author_id")) != str(MY_USER_ID):
        return False

    own_auto_reply_ids = set(str(x) for x in state.get("own_auto_reply_ids", []))
    return str(tweet.get("id")) in own_auto_reply_ids


def build_context_for_grok(mention: dict, state: dict) -> tuple[str, bool]:
    mention_id = str(mention.get("id"))
    mention_text = mention.get("text", "").strip()

    if not mention_text:
        log.info("Mention %s has no text; skipping", mention_id)
        return "", False

    chain: list[dict] = []

    if ALWAYS_FETCH_PARENT_FOR_CONTEXT:
        try:
            chain = build_parent_chain(mention, state)
        except ApiError as e:
            log.warning("Could not build parent chain for mention %s: %s", mention_id, e)
            raise
        except Exception as e:
            log.warning("Unexpected failure building parent chain for mention %s: %s", mention_id, e)
            return "", False

    immediate_parent = chain[-1] if chain else None

    if SKIP_REPLIES_TO_OWN_AUTO_REPLIES and is_our_auto_reply(immediate_parent, state):
        log.info(
            "Skipping mention %s: immediate parent %s is one of our own auto-replies",
            mention_id,
            immediate_parent.get("id") if immediate_parent else None,
        )
        return "", False

    parts: list[str] = []

    if chain:
        parts.append(
            "Thread context, oldest to newest. "
            "This is limited cached context only; do not assume facts not shown here."
        )

        own_auto_reply_ids = set(str(x) for x in state.get("own_auto_reply_ids", []))

        for index, tweet in enumerate(chain, start=1):
            tweet_id = str(tweet.get("id", ""))
            author_id = str(tweet.get("author_id", ""))

            if author_id == str(MY_USER_ID):
                own_auto = "yes" if tweet_id in own_auto_reply_ids else "no"
                speaker = f"this account, own_auto_reply={own_auto}"
            else:
                speaker = f"user {author_id}"

            trimmed = trim_context_text(tweet_context_text(tweet), THREAD_CONTEXT_MAX_CHARS_PER_POST)
            parts.append(f"{index}. Parent post by {speaker}:\n{trimmed}")

        parts.append(
            "Incoming post/comment to answer:\n"
            f"{trim_context_text(mention_text, THREAD_CONTEXT_MAX_CHARS_PER_POST)}"
        )
    else:
        parts.append(
            "Incoming standalone post/comment to answer:\n"
            f"{trim_context_text(mention_text, THREAD_CONTEXT_MAX_CHARS_PER_POST)}"
        )

    context = "\n\n".join(parts)

    if len(context) > THREAD_CONTEXT_MAX_TOTAL_CHARS:
        log.debug(
            "Truncating full context from %d to %d chars",
            len(context),
            THREAD_CONTEXT_MAX_TOTAL_CHARS,
        )
        context = context[:THREAD_CONTEXT_MAX_TOTAL_CHARS].rsplit(" ", 1)[0].rstrip(".,;:") + "..."

    log.info(
        "Built Grok context for mention %s. chain_items=%d immediate_parent=%s",
        mention_id,
        len(chain),
        immediate_parent.get("id") if immediate_parent else None,
    )
    log_json_debug("Context sent to Grok", context)

    return context, True


# ---------------------------------------------------------------------
# Mentions
# ---------------------------------------------------------------------

def get_mentions(state: dict) -> list[dict]:
    base_since_id = str(state.get("last_seen_mention_id") or "")
    log.info(
        "Fetching mentions. last_seen_mention_id=%s max_results=%s",
        base_since_id or None,
        MAX_MENTIONS_PER_CHECK,
    )

    params = {
        "max_results": MAX_MENTIONS_PER_CHECK,
        "tweet.fields": "author_id,created_at,conversation_id,referenced_tweets",
        "expansions": "author_id",
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

    result = x_paginated_get(
        lambda path, page_params: x_request("GET", path, params=page_params),
        f"/2/users/{MY_USER_ID}/mentions",
        params,
        max_pages=MENTIONS_MAX_PAGES_PER_CHECK,
        label="mentions",
    )

    mentions = result.get("data", [])
    pagination = result.get("_pagination", {}) if isinstance(result.get("_pagination", {}), dict) else {}
    truncated = bool(pagination.get("truncated"))
    log.info("Fetched %d mentions", len(mentions))
    log_json_debug("Mentions returned", mentions)
    if truncated:
        log.warning("Mention pagination was truncated; mention watermark will not advance this cycle")
        next_token = str(pagination.get("next_token") or "")
        if next_token:
            state["mention_pagination"] = {
                "base_since_id": base_since_id,
                "next_token": next_token,
            }
            save_state(state)
        for mention in mentions:
            mention["_pagination_truncated"] = True
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
    Fetch ordinary replies in conversations for watched hot posts.

    This deliberately uses the same extra_quote_watch_post_ids.txt file as the
    quote-tweet watch lane, so adding one hot post ID makes both lanes watch it.
    Candidates are marked with _source=hot_post_reply and then passed through the
    normal mention/reply generation path.

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
            "tweet.fields": "author_id,created_at,conversation_id,referenced_tweets",
            "expansions": "author_id",
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

        try:
            result = x_paginated_get(
                x_quote_lookup_request,
                "/2/tweets/search/recent",
                params,
                max_pages=HOT_POST_REPLY_SEARCH_MAX_PAGES_PER_CHECK,
                label=f"hot-post replies for {original_post_id}",
            )
        except ApiError:
            log.exception("Failed to fetch hot-post replies for post %s", original_post_id)
            raise
        except Exception:
            log.exception("Unexpected failure fetching hot-post replies for post %s", original_post_id)
            raise

        replies = result.get("data", [])
        pagination = result.get("_pagination", {}) if isinstance(result.get("_pagination", {}), dict) else {}
        pagination_truncated = bool(pagination.get("truncated"))
        next_pagination_token = str(pagination.get("next_token") or "")
        log.info("Fetched %d hot-post conversation candidate(s) for post_id=%s", len(replies), original_post_id)
        log_json_debug("Hot-post reply candidates returned", replies)

        raw_ids = [int(str(reply.get("id", "0"))) for reply in replies if str(reply.get("id", "")).isdigit()]
        raw_highest_id = str(max(raw_ids)) if raw_ids else ""
        candidates_for_this_post = 0

        for reply in valid_tweets_sorted_by_id(replies, context="hot-post reply candidate"):
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
        elif pagination_truncated:
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
    log.info("Uploading media via legacy v1.1 fallback: %s", image_path)

    url = f"{X_UPLOAD_BASE}/1.1/media/upload.json"

    with open(image_path, "rb") as f:
        files = {
            "media": f,
        }
        data = {
            "media_category": "tweet_image",
        }

        try:
            response = requests.post(
                url,
                auth=AUTH,
                files=files,
                data=data,
                timeout=REQUEST_TIMEOUT_SECONDS,
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
    try:
        return upload_media_v2(image_path)
    except ApiError as exc:
        if getattr(exc, "status_code", None) == 429:
            log.exception("v2 media upload was rate limited; not retrying v1.1 fallback")
            raise
        log.exception("v2 media upload failed; trying v1.1 fallback")
        return upload_media_v1_1(image_path)
    except Exception:
        log.exception("v2 media upload failed; trying v1.1 fallback")
        return upload_media_v1_1(image_path)


def create_post(
    text: str,
    media_ids: list[str] | None = None,
    reply_to_id: str | None = None,
    made_with_ai: bool = False,
) -> dict:
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
        post_id = result.get("data", {}).get("id") if isinstance(result, dict) else None
        if not valid_post_id(post_id):
            raise ApiError(f"X post creation response did not include a valid numeric data.id: {result}", service="x")
        return result

    def made_with_ai_field_rejected(error: ApiError) -> bool:
        status_code = getattr(error, "status_code", None)
        if status_code not in {400, 422}:
            return False

        message = str(error).lower()
        return "made_with_ai" in message

    try:
        result = x_request("POST", "/2/tweets", json=payload)
        validate_created_post_response(result)
        log.info("Created X post successfully. response=%s", result)
        return result
    except ApiError as exc:
        if made_with_ai and made_with_ai_field_rejected(exc):
            log.warning("Post failed because made_with_ai field was rejected; retrying without made_with_ai field")
            payload.pop("made_with_ai", None)
            result = x_request("POST", "/2/tweets", json=payload)
            validate_created_post_response(result)
            log.info("Created X post successfully after removing made_with_ai. response=%s", result)
            return result
        raise


# ---------------------------------------------------------------------
# Quote/image posting
# ---------------------------------------------------------------------

class NoEligibleImageForQuote(RuntimeError):
    pass


class QuoteSpecificImageMismatch(NoEligibleImageForQuote):
    pass


class GlobalImageUnavailable(NoEligibleImageForQuote):
    pass


class InvalidRegularPostReceipt(RuntimeError):
    pass


class UnresolvedRegularPostReceipt(RuntimeError):
    pass


class InvalidMemePostReceipt(RuntimeError):
    pass


class UnresolvedMemePostReceipt(RuntimeError):
    pass


class ConfirmedPostLocalPersistenceError(RuntimeError):
    # This covers failures after a valid remote post id is known. A hard crash
    # after receiving that id but before durable receipt fsync can still leave
    # no replay record. Separately, if X accepts a post but no response reaches
    # this process, there is no known post id to receipt.
    pass


class CorruptUsedHistoryError(RuntimeError):
    pass


class UnsafeImageHistoryMigration(RuntimeError):
    pass


class StaleImageMetadata(RuntimeError):
    pass

TOKEN_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in",
    "is", "it", "of", "on", "or", "our", "the", "their", "this", "to", "with",
}


def load_json_object(path: Path, *, label: str) -> dict | None:
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
    return re.sub(r"\s+", " ", str(text or "").strip())


def quote_text_hash(text: str) -> str:
    return hashlib.sha256(collapse_quote_whitespace(text).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def deep_merge_dict(base: dict, patch: dict) -> dict:
    merged = json.loads(json.dumps(base))
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge_dict(merged[key], value)
        else:
            merged[key] = json.loads(json.dumps(value))
    return merged


def apply_quote_analysis_overrides(raw_analysis: dict, overrides: dict | None) -> dict:
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


def load_image_analysis() -> dict | None:
    raw = load_json_object(IMAGE_ANALYSIS_FILE, label="image analysis")
    if raw is None:
        return None
    if raw.get("analysis_kind") != "images":
        log.error("Image analysis file has unsupported analysis_kind=%r", raw.get("analysis_kind"))
        return None
    if raw.get("schema_version") != 3:
        log.error("Image analysis file has unsupported schema_version=%r", raw.get("schema_version"))
        return None
    if not isinstance(raw.get("items"), dict) or not isinstance(raw.get("path_index"), dict):
        log.error("Image analysis file has invalid required structure: %s", IMAGE_ANALYSIS_FILE)
        return None
    return raw


def mm_dd_in_window(mm_dd: str, start_mm_dd: str, end_mm_dd: str) -> bool:
    if start_mm_dd <= end_mm_dd:
        return start_mm_dd <= mm_dd <= end_mm_dd
    return mm_dd >= start_mm_dd or mm_dd <= end_mm_dd


def any_window_matches_today(windows: object, today_mm_dd: str) -> bool:
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


def quote_metadata_for_line(quote_analysis: dict | None, line_no: int) -> tuple[str | None, dict | None]:
    try:
        with open(LINES_FILE, encoding="utf-8") as f:
            lines = f.readlines()
        text = lines[line_no].rstrip()
    except Exception:
        return None, None
    quote_hash = quote_text_hash(text)
    return quote_hash, quote_metadata_for_hash(quote_analysis, quote_hash, text)


def current_quote_hashes_by_line(lines: list[str]) -> dict[int, str]:
    result: dict[int, str] = {}
    for line_no, line in enumerate(lines):
        if line.rstrip():
            result[line_no] = quote_text_hash(line)
    return result


def quote_used_history_has_legacy_indices(value: set) -> bool:
    return any(re.fullmatch(r"-?\d+", str(item)) for item in value)


def quote_source_matches_analysis(quote_analysis: dict | None, lines: list[str]) -> bool:
    if not isinstance(quote_analysis, dict):
        return False
    source = quote_analysis.get("source", {}) if isinstance(quote_analysis.get("source"), dict) else {}
    expected = source.get("source_sha256")
    if not expected:
        return False
    current = hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()
    return str(expected) == current


def normalise_quote_used_hashes(raw_used: set, lines: list[str], quote_analysis: dict | None = None) -> tuple[set, bool]:
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
    save_used_set(path, {str(item) for item in value}, durable=durable)


def validate_quote_analysis_against_lines(quote_analysis: dict, lines: list[str]) -> None:
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
    images = glob(IMAGE_GLOB)
    images.sort()
    return [path for path in images if Path(path).is_file()]


def save_image_used_basenames(path: Path, value: set[str], *, durable: bool = False) -> None:
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
    return bool(re.fullmatch(r"\d{1,30}", str(value or "")))


def valid_receipt_epoch(value: object) -> bool:
    try:
        epoch = int(value)
    except Exception:
        return False
    return 1_500_000_000 <= epoch <= 4_102_444_800


def receipt_int(value: object, default: int | None = None) -> int | None:
    if value in (None, "") and default is not None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return None


def receipt_bool(value: object) -> bool | None:
    if isinstance(value, bool):
        return value
    return None


def safe_epoch_date_str(epoch: int) -> str | None:
    try:
        return epoch_date_str(epoch)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def valid_receipt_basename(value: object) -> bool:
    basename = str(value or "")
    return bool(basename) and Path(basename).name == basename and basename not in {".", ".."}


def write_regular_post_receipt(receipt: dict) -> None:
    if REGULAR_POST_RECEIPT_FILE.exists():
        raise UnresolvedRegularPostReceipt(f"Refusing to overwrite unresolved regular-post receipt: {REGULAR_POST_RECEIPT_FILE}")
    if MEME_POST_RECEIPT_FILE.exists():
        raise UnresolvedMemePostReceipt(f"Refusing regular post while unresolved meme-post receipt exists: {MEME_POST_RECEIPT_FILE}")
    if not regular_post_receipt_is_semantically_valid(receipt):
        raise RuntimeError("Internal error: generated regular-post receipt failed semantic validation")
    atomic_write_json(REGULAR_POST_RECEIPT_FILE, receipt, durable=True)
    log.warning("Wrote confirmed regular-post receipt pending local reconciliation: %s", REGULAR_POST_RECEIPT_FILE)


def regular_post_receipt_is_semantically_valid(data: dict) -> bool:
    if data.get("schema_version") != 1:
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
    try:
        with open(REGULAR_POST_RECEIPT_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return "absent", None
    except Exception:
        log.exception("Malformed regular-post receipt blocks main posting until repaired: %s", REGULAR_POST_RECEIPT_FILE)
        return "invalid", None
    if not isinstance(data, dict) or data.get("schema_version") != 1:
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
    try:
        REGULAR_POST_RECEIPT_FILE.unlink()
        log.info("Removed reconciled regular-post receipt: %s", REGULAR_POST_RECEIPT_FILE)
        fsync_parent_dir(REGULAR_POST_RECEIPT_FILE, strict=True)
    except FileNotFoundError:
        pass


def write_meme_post_receipt(receipt: dict) -> None:
    if MEME_POST_RECEIPT_FILE.exists():
        raise UnresolvedMemePostReceipt(f"Refusing to overwrite unresolved meme-post receipt: {MEME_POST_RECEIPT_FILE}")
    if REGULAR_POST_RECEIPT_FILE.exists():
        raise UnresolvedRegularPostReceipt(f"Refusing meme post while unresolved regular-post receipt exists: {REGULAR_POST_RECEIPT_FILE}")
    if not meme_post_receipt_is_semantically_valid(receipt):
        raise RuntimeError("Internal error: generated meme-post receipt failed semantic validation")
    atomic_write_json(MEME_POST_RECEIPT_FILE, receipt, durable=True)
    log.warning("Wrote confirmed meme-post receipt pending local reconciliation: %s", MEME_POST_RECEIPT_FILE)


def meme_post_receipt_is_semantically_valid(data: dict) -> bool:
    if data.get("schema_version") != 1:
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
    try:
        MEME_POST_RECEIPT_FILE.unlink()
        log.info("Removed reconciled meme-post receipt: %s", MEME_POST_RECEIPT_FILE)
        fsync_parent_dir(MEME_POST_RECEIPT_FILE, strict=True)
    except FileNotFoundError:
        pass


def apply_meme_post_receipt(receipt: dict, state: dict) -> None:
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


def block_if_unresolved_meme_post_receipt() -> None:
    status, _receipt = load_meme_post_receipt()
    if status == "absent":
        return
    if status == "invalid":
        raise InvalidMemePostReceipt(f"Invalid meme-post receipt blocks the bot: {MEME_POST_RECEIPT_FILE}")
    raise UnresolvedMemePostReceipt(f"Unresolved meme-post receipt must be reconciled before another main post: {MEME_POST_RECEIPT_FILE}")


def apply_regular_post_receipt(receipt: dict, lines_used: set, images_used: set, state: dict) -> None:
    post_id = str(receipt["post_id"])
    quote_hash = str(receipt["quote_hash"])
    image_basename = str(receipt["image_basename"])
    quote_post_epoch = int(receipt["quote_post_epoch"])
    next_quote_post_epoch = int(receipt["next_quote_post_epoch"])
    text = str(receipt.get("text") or "")

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
        state["last_quote_post_epoch"] = quote_post_epoch
        state["last_regular_image_filename"] = image_basename
        state["next_quote_post_epoch"] = next_quote_post_epoch
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
    save_quote_used_hashes(LINES_USED_FILE, lines_used, durable=durable)
    save_image_used_basenames(IMAGES_USED_FILE, {str(item) for item in images_used}, durable=durable)
    save_state(state, durable=durable)


def emergency_persist_confirmed_regular_post(lines_used: set, images_used: set, state: dict) -> list[str]:
    failures: list[str] = []
    for name, func in (
        ("quote_history", lambda: save_quote_used_hashes(LINES_USED_FILE, lines_used, durable=True)),
        ("image_history", lambda: save_image_used_basenames(IMAGES_USED_FILE, {str(item) for item in images_used}, durable=True)),
        ("state", lambda: save_state(state, durable=True)),
    ):
        try:
            func()
        except Exception:
            failures.append(name)
            log.critical("Emergency persistence component failed after confirmed regular post: %s", name, exc_info=True)
    return failures


def reconcile_regular_post_receipt(lines_used: set, images_used: set, state: dict) -> bool:
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
    save_regular_post_protected_state(lines_used, images_used, state, durable=True)
    remove_regular_post_receipt()
    return True


def block_if_unresolved_regular_post_receipt() -> None:
    status, _receipt = load_regular_post_receipt()
    if status == "absent":
        return
    if status == "invalid":
        raise InvalidRegularPostReceipt(f"Invalid regular-post receipt blocks main posting: {REGULAR_POST_RECEIPT_FILE}")
    raise UnresolvedRegularPostReceipt(f"Unresolved regular-post receipt must be reconciled before another main post: {REGULAR_POST_RECEIPT_FILE}")


def both_main_post_receipts_exist() -> bool:
    return REGULAR_POST_RECEIPT_FILE.exists() and MEME_POST_RECEIPT_FILE.exists()


def reconcile_main_post_receipts(lines_used: set, images_used: set, state: dict) -> dict[str, bool]:
    if both_main_post_receipts_exist():
        log.critical(
            "Both regular and meme confirmed-post receipts exist; refusing automatic reconciliation until manually inspected: %s %s",
            REGULAR_POST_RECEIPT_FILE,
            MEME_POST_RECEIPT_FILE,
        )
        raise InvalidRegularPostReceipt("Both main-post receipts exist; manual recovery required")
    return {
        "regular": reconcile_regular_post_receipt(lines_used, images_used, state),
        "meme": reconcile_meme_post_receipt(state),
    }


def block_if_unresolved_main_post_receipt() -> None:
    if both_main_post_receipts_exist():
        log.critical(
            "Both regular and meme confirmed-post receipts exist; refusing main posting until manually inspected: %s %s",
            REGULAR_POST_RECEIPT_FILE,
            MEME_POST_RECEIPT_FILE,
        )
        raise InvalidRegularPostReceipt("Both main-post receipts exist; manual recovery required")
    block_if_unresolved_regular_post_receipt()
    block_if_unresolved_meme_post_receipt()


def image_used_history_has_legacy_indices(images_used: set) -> bool:
    return any(re.fullmatch(r"-?\d+", str(item)) for item in images_used)


def image_corpus_verified_for_legacy_migration(images: list[str], image_analysis: dict | None) -> bool:
    if not isinstance(image_analysis, dict):
        return False
    expected = set(str(name) for name in (image_analysis.get("path_index") or {}).keys())
    visible = {Path(path).name for path in images}
    return bool(expected) and visible == expected


def normalise_image_used_basenames(images_used: set, images: list[str], image_analysis: dict | None = None) -> tuple[set, bool]:
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
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def as_string_list(value: object) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if item is not None]
    if value is None:
        return []
    return [str(value)]


def meaningful_tokens(value: object) -> set[str]:
    words = re.findall(r"[a-z0-9]+", str(value or "").lower())
    return {word for word in words if len(word) >= 4 and word not in TOKEN_STOPWORDS}


def phrase_matches_text(phrase: str, text: str) -> bool:
    phrase_tokens = meaningful_tokens(phrase)
    if not phrase_tokens:
        return False
    text_tokens = meaningful_tokens(text)
    required = max(1, min(len(phrase_tokens), int(round(len(phrase_tokens) * 0.65))))
    return len(phrase_tokens & text_tokens) >= required


def hard_mismatch_tokens(value: object) -> set[str]:
    tokens = meaningful_tokens(value)
    normalised: set[str] = set()
    for token in tokens:
        if token.endswith("s") and len(token) > 4:
            normalised.add(token[:-1])
        else:
            normalised.add(token)
    return normalised


def hard_mismatch_phrase_matches_text(phrase: str, text: str) -> bool:
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


def concise_components(components: dict[str, float]) -> str:
    return ", ".join(f"{key}={value:.1f}" for key, value in sorted(components.items()))


def build_quote_candidates(
    lines: list[str],
    available_lines: list[int],
    quote_analysis: dict | None,
    today_mm_dd: str,
    *,
    excluded_quote_hashes: set[str] | None = None,
) -> tuple[list[dict], int, int]:
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


def quote_candidates_for_current_cycle(lines_used: set, *, excluded_quote_hashes: set[str] | None = None) -> list[dict]:
    log.debug("Choosing unused line. Already used=%d", len(lines_used))

    lines, quote_analysis, today_mm_dd = load_quote_lines_and_analysis()
    hashes_by_line = current_quote_hashes_by_line(lines)
    available_lines = [line_no for line_no, quote_hash in hashes_by_line.items() if quote_hash not in lines_used]

    log.debug("Available unused lines=%d", len(available_lines))

    if not available_lines:
        log.info("All lines used; clearing line history")
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
    return select_quote_candidate(quote_candidates_for_current_cycle(lines_used, excluded_quote_hashes=excluded_quote_hashes))


def choose_unused_line(lines_used: set) -> tuple[int, str]:
    chosen = choose_unused_line_candidate(lines_used)
    return int(chosen["line_no"]), str(chosen["text"])


def available_image_basenames(images: list[str], images_used: set[str], state: dict | None = None) -> tuple[list[str], bool]:
    if not images:
        raise RuntimeError(f"No images found matching {IMAGE_GLOB}")

    basenames = [Path(path).name for path in images]
    all_basenames = set(basenames)
    available = sorted(all_basenames.difference(images_used))
    cycle_reset = False

    if not available:
        log.info("All images used; clearing image history")
        images_used.clear()
        available = sorted(all_basenames)
        cycle_reset = True

        last_name = str((state or {}).get("last_regular_image_filename") or "")
        if len(available) > 1 and last_name in available:
            available.remove(last_name)
            log.info("Temporarily excluded last regular image at cycle boundary: %s", last_name)

    return available, cycle_reset


def available_currently_eligible_image_basenames(
    eligible_basenames: set[str],
    images_used: set[str],
    state: dict | None = None,
) -> tuple[list[str], bool]:
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


def choose_random_unused_image(images_used: set, state: dict | None = None) -> dict:
    log.debug("Choosing random unused image. Already used=%d", len(images_used))

    images = current_image_paths()
    log.debug("Found %d images matching %s", len(images), IMAGE_GLOB)

    normalised, changed = normalise_image_used_basenames(images_used, images)
    if changed:
        images_used.clear()
        images_used.update(normalised)

    available, cycle_reset = available_image_basenames(images, images_used, state)
    selected_basename = random.choice(available)
    image_by_name = {Path(path).name: path for path in images}
    selected_path = image_by_name[selected_basename]
    image_no = images.index(selected_path)

    log.info(
        "Selected random image basename=%s image_no=%d cycle_reset=%s used_count=%d remaining_count=%d",
        selected_basename,
        image_no,
        cycle_reset,
        len(images_used),
        len(available),
    )
    return {
        "image_no": image_no,
        "path": selected_path,
        "basename": selected_basename,
        "score": None,
        "components": {},
        "cycle_reset": cycle_reset,
    }


def choose_unused_image(images_used: set) -> tuple[int, str]:
    chosen = choose_random_unused_image(images_used)
    return int(chosen["image_no"]), str(chosen["path"])


def current_image_sha256(path: str) -> str:
    return file_sha256(Path(path))


def image_metadata_for_basename(image_analysis: dict | None, basename: str, path: str | None = None) -> tuple[str | None, dict | None]:
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


def choose_matched_unused_image(images_used: set, quote_choice: dict, state: dict) -> dict:
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

    if not eligible_basenames:
        raise GlobalImageUnavailable("No analysed currently eligible regular-post images are available")

    available, cycle_reset = available_currently_eligible_image_basenames(eligible_basenames, images_used, state)
    log.info(
        "Image cycle status: used_count=%d currently_eligible=%d remaining_count=%d seasonally_excluded=%d stale_excluded=%d cycle_reset=%s",
        len(images_used),
        len(eligible_basenames),
        len(available),
        seasonally_excluded,
        stale_excluded,
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
        path = image_by_name[basename]
        scored.append(
            {
                "image_no": images.index(path),
                "path": path,
                "basename": basename,
                "image_hash": image_hash,
                "score": score,
                "components": components,
                "cycle_reset": cycle_reset,
            }
        )

    if not scored:
        raise QuoteSpecificImageMismatch("No metadata-eligible regular-post images matched the selected quote")

    best_score = max(float(item["score"]) for item in scored)
    tied = [item for item in scored if float(item["score"]) == best_score]
    chosen = random.choice(tied)

    log.info(
        "Selected matched image basename=%s image_no=%d score=%.2f components=%s",
        chosen["basename"],
        chosen["image_no"],
        chosen["score"],
        concise_components(chosen["components"]),
    )
    for item in sorted(scored, key=lambda entry: float(entry["score"]), reverse=True)[:5]:
        log.debug(
            "Image match candidate basename=%s score=%.2f components=%s",
            item["basename"],
            item["score"],
            concise_components(item["components"]),
        )
    return chosen


def post_random_quote(lines_used: set, images_used: set, state: dict) -> None:
    log.info("Starting quote/image post cycle")

    receipt_status = reconcile_main_post_receipts(lines_used, images_used, state)
    if receipt_status.get("regular"):
        log.warning("Reconciled regular quote/image receipt; not creating a second regular post in the same call")
        return
    original_lines_used = set(lines_used)
    original_images_used = set(images_used)

    try:
        if quote_used_history_has_legacy_indices(lines_used):
            raise CorruptUsedHistoryError(
                "Quote used-history still contains legacy integer entries; refusing regular quote posting until source-verified migration is possible"
            )

        attempted_quote_hashes: set[str] = set()
        quote_choice = None
        image_choice = None
        attempts = 0
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
                image_choice = choose_matched_unused_image(images_used, quote_choice, state)
                if attempts > 1:
                    log.info(
                        "Selected alternate quote/image pair after %d attempt(s). line_no=%s image=%s",
                        attempts,
                        quote_choice.get("line_no"),
                        image_choice.get("basename"),
                    )
                break
            except QuoteSpecificImageMismatch as exc:
                log.warning(
                    "Selected quote line_no=%s quote_hash=%s could not be paired with any currently eligible unused image: %s",
                    quote_choice.get("line_no"),
                    quote_choice.get("quote_hash"),
                    exc,
                )
                quote_choice = None
                image_choice = None

        if quote_choice is None or image_choice is None:
            raise RuntimeError(
                f"No eligible regular quote/image pair found after {attempts} attempt(s); used histories unchanged"
            )

        line_no = int(quote_choice["line_no"])
        quote_hash = str(quote_choice["quote_hash"])
        tweet = str(quote_choice["text"])
        image_no = int(image_choice["image_no"])
        image = str(image_choice["path"])
        image_basename = str(image_choice["basename"])
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
        response = create_post(
            text=tweet,
            media_ids=[media_id],
            reply_to_id=None,
            made_with_ai=False,
        )
        posted_id = response.get("data", {}).get("id") if isinstance(response, dict) else None
        log.debug("Posted_id=%s", posted_id)

        if not valid_post_id(posted_id):
            raise RuntimeError("Quote/image post did not return a valid post id; used histories unchanged")
    except Exception:
        lines_used.clear()
        lines_used.update(original_lines_used)
        images_used.clear()
        images_used.update(original_images_used)
        raise

    try:
        quote_post_epoch = now_epoch()
        state["next_quote_post_epoch"] = int(quote_post_epoch) + int(quote_delay)
        quote_schedule_fields, _quote_delay = next_quote_schedule_fields(quote_post_epoch, delay=quote_delay)
        meme_schedule_fields = meme_schedule_fields_after_quote_post(state, quote_post_epoch, delay=meme_delay)
        meme_schedule_changed_by_quote = bool(meme_schedule_fields)
        receipt = {
            "schema_version": 1,
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
        }
        write_regular_post_receipt(receipt)
    except Exception as exc:
        log.critical(
            "Confirmed regular quote/image post_id=%s but failed writing recovery receipt; in-memory used histories remain marked",
            posted_id,
            exc_info=True,
        )
        lines_used.add(quote_hash)
        images_used.add(image_basename)
        state["last_main_post_id"] = str(posted_id)
        if "quote_post_epoch" in locals():
            state["last_quote_post_epoch"] = quote_post_epoch
        state["last_regular_image_filename"] = image_basename
        if "quote_schedule_fields" in locals():
            apply_state_fields(state, quote_schedule_fields)
        if "meme_schedule_fields" in locals():
            apply_state_fields(state, meme_schedule_fields)
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
        failures = emergency_persist_confirmed_regular_post(lines_used, images_used, state)
        failure_text = ", ".join(failures) if failures else "receipt"
        raise ConfirmedPostLocalPersistenceError(
            f"Confirmed regular quote/image post {posted_id} but failed local recovery receipt/persistence: {failure_text}"
        ) from exc

    try:
        lines_used.add(quote_hash)
        images_used.add(image_basename)
        state["last_main_post_id"] = str(posted_id)
        state["last_quote_post_epoch"] = quote_post_epoch
        state["last_regular_image_filename"] = image_basename
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
        image_score=image_choice.get("score"),
    )
    log.info("Quote/image posted successfully. posted_id=%s", posted_id)


# ---------------------------------------------------------------------
# Daily meme posting
# ---------------------------------------------------------------------

def load_meme_analysis_index() -> dict[str, dict]:
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
    if epoch is None:
        epoch = now_epoch()
    return datetime.fromtimestamp(int(epoch)).strftime("%Y-%m-%d")


def meme_posted_on_date(state: dict, date_text: str) -> bool:
    last_epoch = int(state.get("last_meme_post_epoch", 0) or 0)
    if not last_epoch:
        return False
    return epoch_date_str(last_epoch) == date_text


def next_meme_fallback_epoch(state: dict, from_epoch: int | None = None) -> int:
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
    next_epoch = next_meme_fallback_epoch(state, from_epoch)
    return {
        "next_meme_post_epoch": next_epoch,
        "meme_schedule_version": MEME_SCHEDULE_VERSION,
        "next_meme_schedule_mode": mode,
        "next_meme_schedule_date": epoch_date_str(next_epoch),
        "meme_anchor_quote_post_epoch": 0,
    }


def meme_delay_schedule_fields(epoch: int, mode: str) -> dict:
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
    apply_state_fields(state, meme_delay_schedule_fields(epoch, mode))
    if save:
        save_state(state)


def apply_state_fields(state: dict, fields: dict) -> None:
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


def post_next_meme(state: dict) -> None:
    log.info("Starting daily meme post cycle")
    if both_main_post_receipts_exist():
        log.critical(
            "Both regular and meme confirmed-post receipts exist; refusing meme posting until manually inspected: %s %s",
            REGULAR_POST_RECEIPT_FILE,
            MEME_POST_RECEIPT_FILE,
        )
        raise InvalidMemePostReceipt("Both main-post receipts exist; manual recovery required")
    if reconcile_meme_post_receipt(state):
        log.warning("Reconciled meme post receipt; not creating a second meme post in the same call")
        return
    block_if_unresolved_regular_post_receipt()

    meme_path = choose_next_meme(state)

    if not meme_path:
        log.info("No meme available to post")
        schedule_next_meme_post(state)
        return

    analysis_index = load_meme_analysis_index()
    image_summary = build_meme_cache_summary(meme_path, analysis_index)

    log.info("Posting meme image: %s", meme_path)
    log.debug("Meme image summary for cache: %r", image_summary)

    media_id = upload_media(str(meme_path))

    response = create_post(
        text=MEME_POST_TEXT,
        media_ids=[media_id],
        reply_to_id=None,
        made_with_ai=False,
    )

    posted_id = response.get("data", {}).get("id") if isinstance(response, dict) else None
    log.debug("Posted meme id=%s", posted_id)

    if not valid_post_id(posted_id):
        raise RuntimeError("Daily meme post did not return a valid post id; meme state unchanged")

    try:
        meme_post_epoch = now_epoch()
        apply_state_fields(state, meme_delay_schedule_fields(int(meme_post_epoch) + 3600, "delayed_exception"))
        planned_state = copy.deepcopy(state)
        planned_state["last_meme_post_epoch"] = meme_post_epoch
        planned_posted = set(str(x) for x in planned_state.get("posted_meme_filenames", []))
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
    except Exception as exc:
        log.critical(
            "Confirmed meme post_id=%s but failed writing recovery receipt; attempting direct durable state save",
            posted_id,
            exc_info=True,
        )
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
        except Exception:
            log.critical("Emergency state persistence failed after confirmed meme post", exc_info=True)
        raise ConfirmedPostLocalPersistenceError(f"Confirmed meme post {posted_id} but failed writing recovery receipt") from exc

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
    except Exception:
        log.critical("Confirmed meme post_id=%s but durable state save failed; receipt remains for reconciliation", posted_id, exc_info=True)
        raise ConfirmedPostLocalPersistenceError(f"Confirmed meme post {posted_id} but durable state save failed")
    try:
        remove_meme_post_receipt()
    except Exception as exc:
        log.critical("Confirmed meme post_id=%s but receipt removal failed after durable state save", posted_id, exc_info=True)
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

BLOCKED_REPLY_PATTERNS = [
    r"https?://",
    r"\bkill\b",
    r"\bdie\b",
    r"\bsuicide\b",
    r"\bhang\b",
    r"\bshoot\b",
    r"\bstab\b",
    r"\btraitor should\b",
    r"\bdeserves to\b",
]


def is_probably_spam_or_not_worth_replying(text: str) -> bool:
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


def clean_generated_reply(text: str) -> str:
    log.debug("Raw Grok reply before cleaning: %r", text)

    text = text.strip()
    text = text.strip('"“”')
    text = re.sub(r"https?://\S+", "", text).strip()
    text = " ".join(text.split())

    if len(text) > MAX_REPLY_CHARS:
        log.debug("Truncating reply from %d chars to %d chars", len(text), MAX_REPLY_CHARS)
        text = text[:MAX_REPLY_CHARS].rsplit(" ", 1)[0].rstrip(".,;:") + "."

    log.debug("Cleaned Grok reply: %r", text)
    return text


def generated_reply_is_safe_enough(text: str) -> bool:
    low = text.lower()
    log.debug("Safety check for generated reply=%r", text)

    if not text:
        log.info("Rejected generated reply: empty")
        return False

    if len(text) < 10:
        log.info("Rejected generated reply: too short")
        return False

    if text.strip().upper() == "SKIP":
        return True

    for pattern in BLOCKED_REPLY_PATTERNS:
        if re.search(pattern, low):
            log.info("Rejected generated reply: matched blocked pattern %s", pattern)
            return False

    forbidden_phrases = [
        "margaret thatcher said",
        "thatcher said",
        "as margaret thatcher",
        "i am margaret thatcher",
    ]

    for phrase in forbidden_phrases:
        if phrase in low:
            log.info("Rejected generated reply: matched forbidden phrase %r", phrase)
            return False

    log.debug("Generated reply passed safety check")
    return True


def ask_grok_for_reply(context_text: str) -> str | None:
    log.info("Asking Grok for reply. context_text=%r", context_text)

    system_prompt = (
        "You write replies for a Margaret Thatcher quotation account on X. "
        "The voice is dry, firm, witty, occasionally cheeky, and sometimes sarcastic, "
        "with more substance than a slogan. "
        "Do not be abusive, use slurs, threaten, encourage harassment, include URLs, "
        "claim to be Margaret Thatcher, or invent quotes, events, statistics, or policy facts. "
        "Maximum 270 characters. Prefer one or two crisp sentences. "
        "Return only the reply text, or exactly SKIP."
    )

    user_prompt = (
        "Use only the supplied limited context. "
        "Default to replying when a civil, worthwhile reply is possible. "
        "Return exactly SKIP when the post is mostly handles or links, spam, abuse, gibberish, "
        "requires missing context, would require inventing facts, or would prolong a needless exchange. "
        "Do not skip merely because the post is short, complimentary, mildly vague, "
        "or in a language other than English if the meaning is clear. "
        "If the incoming post replies to or quotes this account's own previous auto-reply, "
        "avoid prolonging the exchange unless a further reply is clearly useful. "
        "For simple praise, agreement, or light comments, write a brief graceful reply "
        "unless the exchange is becoming pointless. "
        "A little sarcasm is acceptable when the other person is being foolish, "
        "but keep it civil, crisp, and quotable.\n\n"
        f"{context_text}"
    )

    payload = {
        "model": XAI_MODEL,
        "messages": [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": user_prompt,
            },
        ],
        "temperature": 0.7,
        "max_tokens": MAX_GROK_OUTPUT_TOKENS,
    }

    log_json_debug("xAI request payload", payload)

    try:
        response = requests.post(
            f"{XAI_BASE}/chat/completions",
            headers={
                "Authorization": f"Bearer {XAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.RequestException as e:
        log.exception("xAI request failed before receiving response")
        raise ApiError(str(e), service="xai") from e

    log.debug("xAI response status=%s", response.status_code)

    if response.status_code >= 400:
        log.error("xAI error %s: %s", response.status_code, response.text)

        raise ApiError(
            f"xAI error {response.status_code}: {response.text}",
            service="xai",
            status_code=response.status_code,
        )

    try:
        data = response.json()
    except json.JSONDecodeError as e:
        log.error("xAI returned non-JSON response: %s", response.text[:1000])
        raise ApiError(f"xAI returned non-JSON response: {response.text[:500]}", service="xai") from e

    log_json_debug("xAI response json", data)

    usage = data.get("usage")
    if usage:
        log.info("xAI usage=%s", usage)

    try:
        reply = data["choices"][0]["message"]["content"]
    except Exception as e:
        log.exception("Could not parse xAI response")
        raise ApiError(f"Could not parse xAI response: {json.dumps(data)[:1000]}", service="xai") from e

    reply = clean_generated_reply(reply)

    if reply.strip().upper() == "SKIP":
        log.info("Grok chose to skip")
        return None

    if not generated_reply_is_safe_enough(reply):
        log.warning("Rejected generated reply after safety checks: %r", reply)
        return None

    log.info("Grok generated usable reply: %r", reply)
    return reply


# ---------------------------------------------------------------------
# Mention replies
# ---------------------------------------------------------------------

def update_last_seen_mention_id(state: dict, mention_id: str) -> None:
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
    if candidate.get("_pagination_truncated"):
        log.warning("Not advancing mention watermark for %s because mention pagination was truncated", candidate.get("id"))
        return
    if candidate.get("_source", "mention") == "mention":
        update_last_seen_mention_id(state, str(candidate.get("id", "")))


def maybe_reply_to_mentions(state: dict) -> str:
    log.info("Starting mention reply check")

    if not ENABLE_AUTO_REPLIES:
        log.info("Auto replies disabled")
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

        if author_id == str(MY_USER_ID):
            log.info("Skipping %s %s: authored by our own account", candidate_source, mention_id)
            maybe_mark_hot_post_reply_skipped(state, mention, reason="own_account")
            log_event("candidate_skipped", lane=candidate_log_source, id=mention_id, reason="own_account")
            mark_mention_seen_if_applicable(state, mention)
            continue

        if daily_author_reply_count(state, author_id) >= MAX_REPLIES_PER_AUTHOR_PER_DAY:
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

        if is_probably_spam_or_not_worth_replying(incoming_text):
            log.info("Skipping %s %s: spam/not worth replying", candidate_source, mention_id)
            maybe_mark_hot_post_reply_skipped(state, mention, reason="spam_or_not_worth_replying")
            log_event("candidate_skipped", lane=candidate_log_source, id=mention_id, reason="spam_or_not_worth_replying")
            mark_mention_seen_if_applicable(state, mention)
            save_state(state)
            continue

        try:
            context_text, should_continue = build_context_for_grok(mention, state)
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

        try:
            reply_text = ask_grok_for_reply(context_text)
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

        if not reply_text:
            log.info("No usable reply generated for %s %s", candidate_source, mention_id)
            maybe_mark_hot_post_reply_skipped(state, mention, reason="no_usable_reply_generated")
            log_event("candidate_skipped", lane=candidate_log_source, id=mention_id, reason="no_usable_reply_generated")
            mark_mention_seen_if_applicable(state, mention)
            save_state(state)
            continue

        log.info("Generated reply to mention %s: %r", mention_id, reply_text)

        if DRY_RUN_REPLIES:
            log.warning("DRY_RUN_REPLIES=True, not posting generated reply")

            state["daily_reply_count"] += 1
            state["last_reply_epoch"] = current

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

        try:
            reply_response = create_post(
                text=reply_text,
                media_ids=None,
                reply_to_id=mention_id,
                made_with_ai=MARK_AI_REPLIES_AS_AI,
            )
        except ApiError as e:
            if api_error_is_reply_not_allowed(e):
                log.warning(
                    "Cannot reply to mention %s because X says replies are not allowed; "
                    "marking mention as handled without consuming reply quota",
                    mention_id,
                )
                replied_to_ids.add(mention_id)
                state["replied_to_ids"] = append_unique_capped(
                    state.get("replied_to_ids", []),
                    mention_id,
                    1000,
                )
                mark_mention_seen_if_applicable(state, mention)
                save_state(state)
                return NORMAL_CHECK_STATUS_CHECKED

            log.exception("Failed to post generated reply")
            record_api_error(state, e, "x", scope="write")
            save_state(state)
            return NORMAL_CHECK_STATUS_API_ERROR
        except Exception as e:
            log.exception("Unexpected failure posting generated reply")
            record_api_error(state, e, "x", scope="write")
            save_state(state)
            return NORMAL_CHECK_STATUS_API_ERROR

        state["daily_reply_count"] += 1
        state["last_reply_epoch"] = current

        replied_to_ids.add(mention_id)
        state["replied_to_ids"] = append_unique_capped(
            state.get("replied_to_ids", []),
            mention_id,
            1000,
        )

        mark_daily_author_replied(state, author_id)

        own_reply_id = reply_response.get("data", {}).get("id")
        if own_reply_id:
            state["own_auto_reply_ids"] = append_unique_capped(
                state.get("own_auto_reply_ids", []),
                own_reply_id,
                1000,
            )

            cache_tweet(
                state,
                tweet_id=str(own_reply_id),
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

            log.info("Recorded and cached own auto-reply id=%s", own_reply_id)

        mark_mention_seen_if_applicable(state, mention)
        save_state(state)

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
    log.info("Fetching quote tweets for post_id=%s", post_id)

    params = {
        "max_results": QUOTE_LOOKUP_API_MAX_RESULTS,
        "tweet.fields": "author_id,created_at,conversation_id,referenced_tweets",
        "expansions": "author_id",
        "user.fields": "description,username,name,public_metrics",
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

    result = x_paginated_get(
        x_quote_lookup_request,
        f"/2/tweets/{post_id}/quote_tweets",
        params,
        max_pages=QUOTE_LOOKUP_MAX_PAGES_PER_POST,
        label=f"quote tweets for {post_id}",
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
    text = clean_text_for_grok_context(quote_tweet.get("text", ""))
    if re.match(r"^RT\s+@\w+:", text):
        return False

    # Conservative default: ambiguous quote lookup results are skipped rather
    # than sent to Grok or replied to.
    return False


def quote_author_profile_text(quote_tweet: dict) -> str:
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


def build_quote_tweet_context(original_tweet: dict, quote_tweet: dict) -> str:
    original_text = trim_context_text(tweet_context_text(original_tweet), THREAD_CONTEXT_MAX_CHARS_PER_POST)
    quote_text = trim_context_text(quote_tweet.get("text", ""), THREAD_CONTEXT_MAX_CHARS_PER_POST)

    parts = [
        "A user has quote-posted one of this account's posts.",
        "Reply only if a civil, useful reply is warranted. Do not assume facts not shown.",
        "",
        "Original post from this account:",
        original_text or "[No usable text available.]",
        "",
        f"Quote post by user {quote_tweet.get('author_id')}:",
        quote_text or "[No usable text available.]",
    ]

    context = "\n".join(parts)

    if len(context) > THREAD_CONTEXT_MAX_TOTAL_CHARS:
        context = context[:THREAD_CONTEXT_MAX_TOTAL_CHARS].rsplit(" ", 1)[0].rstrip(".,;:") + "..."

    log_json_debug("Quote-tweet context sent to Grok", context)
    return context


def mark_quote_tweet_skipped(state: dict, quote_id: str) -> None:
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
    quote_id = str(quote_id)

    state["seen_quote_post_ids"] = append_unique_capped(
        state.get("seen_quote_post_ids", []),
        quote_id,
        2000,
    )
    state["replied_to_quote_post_ids"] = append_unique_capped(
        state.get("replied_to_quote_post_ids", []),
        quote_id,
        2000,
    )


def mark_quote_spam_author(state: dict, author_id: str) -> None:
    author_id = str(author_id)

    state["quote_spam_author_ids"] = append_unique_capped(
        state.get("quote_spam_author_ids", []),
        author_id,
        2000,
    )
    log.info("Marked author_id=%s as quote spam author. spam_author_count=%d", author_id, len(state["quote_spam_author_ids"]))


def maybe_reply_to_quote_tweets(state: dict) -> str:
    log.info("Starting quote-tweet reply check")

    if not ENABLE_QUOTE_TWEET_CHECKS:
        log.info("Quote-tweet checks disabled")
        return QUOTE_CHECK_STATUS_DISABLED

    if not ENABLE_AUTO_REPLIES:
        log.info("Auto replies disabled; skipping quote-tweet checks")
        return QUOTE_CHECK_STATUS_DISABLED

    if lane_paused("disable_replies", "disable_quote_replies"):
        log.info("Skipping quote-tweet check due to runtime control file")
        return QUOTE_CHECK_STATUS_DISABLED

    if in_api_cooldown(state, scope="write") or in_api_cooldown(state, scope="xai") or in_api_cooldown(state, scope="quote"):
        log.info("Skipping quote-tweet check due to API cooldown")
        return QUOTE_CHECK_STATUS_SKIPPED_COOLDOWN

    reset_daily_reply_count_if_needed(state)
    reset_daily_quote_reply_count_if_needed(state)

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

            cleaned_quote_text = clean_text_for_grok_context(quote_text)
            cleaned_author_profile = clean_text_for_grok_context(
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

            context_text = build_quote_tweet_context(original_tweet, quote_tweet)

            processed_candidates += 1

            try:
                reply_text = ask_grok_for_reply(context_text)
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

            if not reply_text:
                log.info("No usable reply generated for quote tweet %s", quote_id)
                mark_quote_tweet_skipped(state, quote_id)
                save_state(state)
                continue

            log.info("Generated reply to quote tweet %s: %r", quote_id, reply_text)

            if DRY_RUN_REPLIES:
                log.warning("DRY_RUN_REPLIES=True, not posting generated quote-tweet reply")

                state["daily_reply_count"] = int(state.get("daily_reply_count", 0) or 0) + 1
                state["daily_quote_reply_count"] = int(state.get("daily_quote_reply_count", 0) or 0) + 1
                state["last_reply_epoch"] = current

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

            try:
                reply_response = create_post(
                    text=reply_text,
                    media_ids=None,
                    reply_to_id=quote_id,
                    made_with_ai=MARK_AI_REPLIES_AS_AI,
                )
            except ApiError as e:
                if api_error_is_reply_not_allowed(e):
                    log.warning(
                        "Cannot reply to quote tweet %s because X says replies are not allowed; "
                        "marking quote tweet as skipped without consuming reply quota",
                        quote_id,
                    )
                    mark_quote_tweet_skipped(state, quote_id)
                    save_state(state)
                    return QUOTE_CHECK_STATUS_CHECKED

                log.exception("Failed to post generated quote-tweet reply")
                record_api_error(state, e, "x", scope="write")
                save_state(state)
                return QUOTE_CHECK_STATUS_CHECKED
            except Exception as e:
                log.exception("Unexpected failure posting generated quote-tweet reply")
                record_api_error(state, e, "x", scope="write")
                save_state(state)
                return QUOTE_CHECK_STATUS_CHECKED

            state["daily_reply_count"] = int(state.get("daily_reply_count", 0) or 0) + 1
            state["daily_quote_reply_count"] = int(state.get("daily_quote_reply_count", 0) or 0) + 1
            state["last_reply_epoch"] = current

            mark_quote_tweet_replied(state, quote_id)

            mark_daily_author_replied(state, author_id)

            own_reply_id = reply_response.get("data", {}).get("id")
            if own_reply_id:
                state["own_auto_reply_ids"] = append_unique_capped(
                    state.get("own_auto_reply_ids", []),
                    own_reply_id,
                    1000,
                )

                cache_tweet(
                    state,
                    tweet_id=str(own_reply_id),
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

                log.info("Recorded and cached own quote-tweet auto-reply id=%s", own_reply_id)

            save_state(state)

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
    if from_epoch is None:
        from_epoch = now_epoch()

    if delay is None:
        delay = random.randint(POST_SLEEP_MIN, POST_SLEEP_MAX)
    return {"next_quote_post_epoch": int(from_epoch) + delay}, delay


def schedule_next_quote_post(state: dict, from_epoch: int | None = None, *, save: bool = True) -> None:
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
        nonlocal last_reply_check_epoch

        if forced:
            log.info(
                "Quote-tweet check is due, but normal/hot-post reply lane has priority; "
                "running normal reply check first"
            )
        else:
            log.info("Due to check mentions")

        normal_check_status = maybe_reply_to_mentions(state)
        log.info("Normal/hot-post reply check status=%s", normal_check_status)

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
        nonlocal last_quote_tweet_check_epoch

        log.info("Due to check quote tweets")
        priority_at_check = str(state.get("next_reply_lane_priority", reply_lane_priority) or reply_lane_priority)
        quote_check_status = maybe_reply_to_quote_tweets(state)

        log.info("Quote-tweet check status=%s", quote_check_status)
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
        if not quote_posted and mention_check_due:
            run_normal_check()
    elif mention_check_due:
        normal_posted = run_normal_check()
        if not normal_posted and quote_check_due:
            run_quote_check()
    elif (
        reply_spacing_open
        and quote_check_due
        and reply_lane_priority == "normal"
        and ENABLE_AUTO_REPLIES
    ):
        normal_posted = run_normal_check(forced=True)
        if not normal_posted:
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
    random.seed()
    acquire_instance_lock()

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

    with open(LINES_FILE, encoding="utf-8") as f:
        quote_lines_for_history = f.readlines()
    lines_used = load_quote_used_hashes(quote_lines_for_history)
    images_used = load_image_used_basenames(current_image_paths())
    state = load_runtime_state()
    reconcile_main_post_receipts(lines_used, images_used, state)

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

    while True:
        current = now_epoch()
        log.debug("Main loop tick. epoch=%s", current)

        last_reply_check_epoch, last_quote_tweet_check_epoch = run_reply_lane_checks_for_tick(
            state,
            current,
            last_reply_check_epoch,
            last_quote_tweet_check_epoch,
        )

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
                except ConfirmedPostLocalPersistenceError:
                    quote_posted = True
                    log.exception("Quote/image post was confirmed remotely but local recovery/persistence failed; not scheduling an error retry")
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
                    except ConfirmedPostLocalPersistenceError:
                        log.exception("Daily meme post was confirmed remotely but local recovery/persistence failed; not scheduling an error retry")
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
    require("runtime config validates", not RUNTIME_CONFIG_ERRORS, "; ".join(RUNTIME_CONFIG_ERRORS))

    if failures:
        log.error("Self-test finished with %d failure(s)", failures)
        return 1

    log.info("Self-test finished successfully")
    return 0


def run_test_cycle() -> int:
    """Run one local integration-test pass without entering the posting loop."""
    if os.getenv("MRS_TEST_MODE") != "1":
        log.error("--test-cycle requires MRS_TEST_MODE=1")
        return 2

    acquire_instance_lock()

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

    if reply_lane_priority == "quote":
        quote_status = maybe_reply_to_quote_tweets(state)
        log.info("Test-cycle quote-tweet check status=%s", quote_status)
        log_event("quote_check_status", status=quote_status, priority="test_cycle")
        after_quote_epoch = int(state.get("last_reply_epoch", 0) or 0)

        if after_quote_epoch != before_reply_epoch:
            state["next_reply_lane_priority"] = "normal"
            save_state(state)
            log.info("Test-cycle quote-tweet lane posted; next reply-lane priority=normal")
        else:
            maybe_reply_to_mentions(state)
            after_reply_epoch = int(state.get("last_reply_epoch", 0) or 0)
            if after_reply_epoch != after_quote_epoch:
                state["next_reply_lane_priority"] = "quote"
                save_state(state)
                log.info("Test-cycle normal/hot-post lane posted; next reply-lane priority=quote")
    else:
        maybe_reply_to_mentions(state)
        after_reply_epoch = int(state.get("last_reply_epoch", 0) or 0)

        if after_reply_epoch != before_reply_epoch:
            state["next_reply_lane_priority"] = "quote"
            save_state(state)
            log.info("Test-cycle normal/hot-post lane posted; next reply-lane priority=quote")
        else:
            quote_status = maybe_reply_to_quote_tweets(state)
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

    acquire_instance_lock()

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

    save_state(state)
    log.info("Test production reply-lane tick finished")
    return 0


def require_test_mode(command_name: str) -> bool:
    if os.getenv("MRS_TEST_MODE") != "1":
        log.error("%s requires MRS_TEST_MODE=1", command_name)
        return False
    return True


def prepare_test_main_post_state(state: dict) -> None:
    if ENABLE_DAILY_MEME_POSTS:
        ensure_meme_schedule_initialized(state)


def run_test_post_quote() -> int:
    """Run one quote/image post cycle for local integration tests."""
    if not require_test_mode("--test-post-quote"):
        return 2

    acquire_instance_lock()

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
    except ConfirmedPostLocalPersistenceError:
        log.critical(
            "REMOTE X POST WAS CONFIRMED; DO NOT RETRY MANUALLY. "
            "Test quote/image local persistence/recovery needs attention.",
            exc_info=True,
        )
        save_state(state)
        return 3
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

    acquire_instance_lock()

    log.info("Running one test daily meme post cycle")
    state = load_runtime_state()
    prepare_test_main_post_state(state)

    if lane_paused("disable_meme_posts"):
        log.warning("Skipping test daily meme post due to runtime control file")
        save_state(state)
        return 0

    try:
        post_next_meme(state)
    except ConfirmedPostLocalPersistenceError:
        log.critical(
            "REMOTE X POST WAS CONFIRMED; DO NOT RETRY MANUALLY. "
            "Test daily meme local persistence/recovery needs attention.",
            exc_info=True,
        )
        save_state(state)
        return 3
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
