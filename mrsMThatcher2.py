#!/usr/bin/env python3

from __future__ import annotations

import html
import json
import logging
import mimetypes
import os
import pickle
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

QUOTE_CHECK_STATUS_CHECKED = "checked"
QUOTE_CHECK_STATUS_POSTED = "posted"
QUOTE_CHECK_STATUS_SKIPPED_SPACING = "skipped_spacing"
QUOTE_CHECK_STATUS_SKIPPED_CAP = "skipped_cap"
QUOTE_CHECK_STATUS_SKIPPED_COOLDOWN = "skipped_cooldown"
QUOTE_CHECK_STATUS_DISABLED = "disabled"

# ---------------------------------------------------------------------
# Reply automation
# ---------------------------------------------------------------------

ENABLE_AUTO_REPLIES = True

REPLY_CHECK_EVERY_SECONDS = 900
MAX_AUTO_REPLIES_PER_DAY = 24
MAX_REPLIES_PER_AUTHOR_PER_DAY = 1
MAX_MENTIONS_PER_CHECK = 5

# Optional hot-post reply lane. This reuses the same watched post ID file
# as the quote-tweet lane, but looks for ordinary replies in that post
# conversation via recent search and feeds them through the normal reply logic.
ENABLE_HOT_POST_REPLY_CHECKS = True
MAX_HOT_POST_REPLIES_PER_CHECK = 10
HOT_POST_REPLY_SEARCH_API_MAX_RESULTS = 10
HOT_POST_REPLY_USE_SINCE_ID = True
HOT_POST_REPLY_FULL_RESCAN_EVERY_CHECKS = 12

MIN_SECONDS_BETWEEN_REPLIES = 3600

DRY_RUN_REPLIES = False
MARK_AI_REPLIES_AS_AI = False

ALWAYS_FETCH_PARENT_FOR_CONTEXT = True
SKIP_REPLIES_TO_OWN_AUTO_REPLIES = False

THREAD_CONTEXT_MAX_DEPTH = 3
THREAD_CONTEXT_MAX_CHARS_PER_POST = 300
THREAD_CONTEXT_MAX_TOTAL_CHARS = 1200

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

PICKLE_FILE = BASE_DIR / "lines_used.pickle"
IMAGE_PICKLE_FILE = BASE_DIR / "images_used.pickle"
STATE_FILE = BASE_DIR / "bot_state.json"
LOG_FILE = Path(os.getenv("MRS_LOG_FILE", str(BASE_DIR / "mrsMThatcher.log"))).expanduser()
if TEST_MODE and path_is_same_or_child(LOG_FILE, PRODUCTION_BASE_DIR):
    print(
        f"Refusing to run in MRS_TEST_MODE with production LOG_FILE={LOG_FILE}",
        file=sys.stderr,
    )
    sys.exit(2)
LOCAL_CONFIG_FILE = BASE_DIR / "mrsMThatcher.local.json"
CONTROL_FILE = BASE_DIR / "mrsMThatcher.control.json"
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

    for min_key, max_key in (
        ("POST_SLEEP_MIN", "POST_SLEEP_MAX"),
        ("MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS", "MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS"),
    ):
        if int(globals()[min_key]) <= int(globals()[max_key]):
            continue

        log.error(
            "Ignoring invalid local config timing range %s=%r > %s=%r",
            min_key,
            globals()[min_key],
            max_key,
            globals()[max_key],
        )
        for key in (min_key, max_key):
            if key in applied:
                globals()[key] = original_values[key]
                applied.pop(key, None)

    if ignored:
        log.warning("Ignoring unsupported local config key(s): %s", ", ".join(sorted(ignored)))

    if applied:
        log.info("Applied %d local config override(s) from %s", len(applied), LOCAL_CONFIG_FILE)
        log_json_debug("Local config overrides applied", applied)
    else:
        log.info("Local config file present but no valid overrides applied: %s", LOCAL_CONFIG_FILE)


apply_local_config()


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
    return bool(data.get(key))


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


# ---------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------

def load_pickle_set(path: Path) -> set:
    log.debug("Loading pickle set from %s", path)

    try:
        with open(path, "rb") as f:
            value = pickle.load(f)
            if isinstance(value, set):
                log.debug("Loaded %d entries from %s", len(value), path)
                return value

            converted = set(value)
            log.debug("Loaded %d converted entries from %s", len(converted), path)
            return converted
    except FileNotFoundError:
        log.warning("Pickle file does not exist yet: %s", path)
        return set()
    except OSError:
        log.exception("OS error loading pickle file %s; using empty set", path)
        return set()
    except Exception:
        log.exception("Failed loading pickle file %s; using empty set", path)
        return set()


def save_pickle_set(path: Path, value: set) -> None:
    log.debug("Saving %d entries to pickle %s", len(value), path)

    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump(value, f)


def default_state() -> dict:
    return {
        "last_seen_mention_id": None,
        "replied_to_ids": [],
        "dry_run_seen_mention_ids": [],
        "skipped_hot_reply_ids": [],
        "skipped_hot_reply_records": {},
        "hot_post_reply_since_ids": {},
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
        "last_main_post_id": None,
        "last_quote_post_epoch": 0,
        "next_quote_post_epoch": 0,

        "recent_own_post_ids": [],

        "seen_quote_post_ids": [],
        "replied_to_quote_post_ids": [],
        "skipped_quote_post_ids": [],
        "quote_spam_author_ids": [],
        "daily_quote_reply_date": None,
        "daily_quote_reply_count": 0,
        "last_quote_tweet_check_epoch": 0,

        "x_error_epochs": [],
        "xai_error_epochs": [],
        "api_cooldown_until_epoch": 0,
        "api_cooldown_reason": "",
    }


def load_state() -> dict:
    log.debug("Loading state from %s", STATE_FILE)

    candidates = [STATE_FILE]
    candidates.extend(STATE_FILE.with_name(f"{STATE_FILE.name}.bak{i}") for i in range(1, STATE_BACKUP_COUNT + 1))

    for candidate in candidates:
        if not candidate.exists():
            log.warning("State file candidate does not exist: %s", candidate)
            continue

        try:
            with open(candidate, "r") as f:
                state = json.load(f)
        except Exception:
            log.exception("Failed loading state candidate %s", candidate)
            continue

        if not isinstance(state, dict):
            log.error("State file candidate %s is not a JSON object; ignoring", candidate)
            continue

        merged = default_state()
        merged.update(state)
        if candidate != STATE_FILE:
            log.warning("Recovered state from backup %s", candidate)
        log_json_debug("Loaded state", merged)
        return merged

    log.error("No usable state file or backup found; using default state")
    return default_state()


def rotate_state_backups() -> None:
    if STATE_BACKUP_COUNT <= 0 or not STATE_FILE.exists():
        return

    try:
        for i in range(STATE_BACKUP_COUNT, 1, -1):
            older = STATE_FILE.with_name(f"{STATE_FILE.name}.bak{i - 1}")
            newer = STATE_FILE.with_name(f"{STATE_FILE.name}.bak{i}")
            if older.exists():
                older.replace(newer)

        bak1 = STATE_FILE.with_name(f"{STATE_FILE.name}.bak1")
        shutil.copy2(STATE_FILE, bak1)
        log.debug("State backup written: %s", bak1)
    except Exception:
        log.exception("Failed rotating state backups; continuing with state save")


def save_state(state: dict) -> None:
    log.debug("Saving state to %s", STATE_FILE)
    log_json_debug("State being saved", state)

    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)

    tmp = STATE_FILE.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(state, f, indent=2, sort_keys=True)

    rotate_state_backups()
    tmp.replace(STATE_FILE)


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

    authors = set(str(x) for x in state.get("daily_replied_author_ids", []))
    authors.add(author_id)
    state["daily_replied_author_ids"] = list(authors)[-1000:]


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


def in_api_cooldown(state: dict) -> bool:
    until = int(state.get("api_cooldown_until_epoch", 0) or 0)

    if until <= now_epoch():
        return False

    reason = state.get("api_cooldown_reason", "API cooldown")
    until_human = datetime.fromtimestamp(until).strftime("%Y-%m-%d %H:%M:%S")
    log.warning("API cooldown active until %s: %s", until_human, reason)
    return True


def prune_error_epochs(epochs: list[int]) -> list[int]:
    cutoff = now_epoch() - ERROR_WINDOW_SECONDS
    pruned = [int(e) for e in epochs if int(e) >= cutoff]
    log.debug("Pruned error epochs from %d to %d", len(epochs), len(pruned))
    return pruned


def record_api_error(state: dict, error: Exception, service: str) -> None:
    current = now_epoch()

    if service == "x":
        key = "x_error_epochs"
        max_errors = MAX_X_ERRORS_PER_WINDOW
    else:
        key = "xai_error_epochs"
        max_errors = MAX_XAI_ERRORS_PER_WINDOW

    epochs = prune_error_epochs(state.get(key, []))
    epochs.append(current)
    state[key] = epochs

    status_code = getattr(error, "status_code", None)
    reset_epoch = getattr(error, "reset_epoch", None)

    log.warning(
        "Recorded %s API error. status_code=%s errors_in_window=%d/%d reset_epoch=%s error=%s",
        service,
        status_code,
        len(epochs),
        max_errors,
        reset_epoch,
        error,
    )

    if status_code == 429:
        cooldown_until = reset_epoch if reset_epoch and reset_epoch > current else current + COOLDOWN_AFTER_429_SECONDS
        state["api_cooldown_until_epoch"] = cooldown_until + 60
        state["api_cooldown_reason"] = f"{service} returned 429/rate limit"
        log.error(
            "Entering API cooldown after 429 until %s",
            datetime.fromtimestamp(state["api_cooldown_until_epoch"]).strftime("%Y-%m-%d %H:%M:%S"),
        )
        save_state(state)
        return

    if len(epochs) >= max_errors:
        state["api_cooldown_until_epoch"] = current + COOLDOWN_AFTER_REPEATED_ERRORS_SECONDS
        state["api_cooldown_reason"] = f"too many {service} API errors in the last hour"
        log.error(
            "Entering API cooldown after repeated errors until %s",
            datetime.fromtimestamp(state["api_cooldown_until_epoch"]).strftime("%Y-%m-%d %H:%M:%S"),
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
        reset_time_human = datetime.fromtimestamp(reset_epoch).strftime("%Y-%m-%d %H:%M:%S")
        log.warning("Rate Limit Resets At: %s", reset_time_human)
        return reset_epoch
    except ValueError:
        log.warning("Rate Limit Resets At: %s", reset_time)
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
        "created_at": created_at or datetime.now().isoformat(),
        "referenced_tweets": referenced_tweets or [],
        "text": text or "",
        "cached_epoch": now_epoch(),
    }

    if image_summary:
        cached_tweet["image_summary"] = image_summary

    if post_type:
        cached_tweet["post_type"] = post_type

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

        parent = get_tweet_by_id_cached(parent_id, state)
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
    log.info(
        "Fetching mentions. last_seen_mention_id=%s max_results=%s",
        state.get("last_seen_mention_id"),
        MAX_MENTIONS_PER_CHECK,
    )

    params = {
        "max_results": MAX_MENTIONS_PER_CHECK,
        "tweet.fields": "author_id,created_at,conversation_id,referenced_tweets",
        "expansions": "author_id",
    }

    if state.get("last_seen_mention_id"):
        params["since_id"] = str(state["last_seen_mention_id"])

    result = x_request(
        "GET",
        f"/2/users/{MY_USER_ID}/mentions",
        params=params,
    )

    mentions = result.get("data", [])
    log.info("Fetched %d mentions", len(mentions))
    log_json_debug("Mentions returned", mentions)

    if mentions:
        for mention in mentions:
            cache_tweet(
                state,
                tweet_id=str(mention["id"]),
                text=mention.get("text", ""),
                author_id=str(mention.get("author_id", "")),
                conversation_id=str(mention.get("conversation_id", mention["id"])),
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

    candidates: list[dict] = []
    state_changed = False

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
        current_count = previous_count + 1
        check_counts[original_post_id] = current_count
        state_changed = True

        full_rescan = False
        if HOT_POST_REPLY_FULL_RESCAN_EVERY_CHECKS > 0:
            full_rescan = current_count % HOT_POST_REPLY_FULL_RESCAN_EVERY_CHECKS == 0

        # conversation_id finds replies in the original post's conversation.
        # We exclude this account and retweets; later filtering keeps only actual replies.
        query = f"conversation_id:{original_post_id} -from:{MY_USER_ID} -is:retweet"

        params = {
            "query": query,
            "max_results": max(HOT_POST_REPLY_SEARCH_API_MAX_RESULTS, MAX_HOT_POST_REPLIES_PER_CHECK),
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

        try:
            result = x_quote_lookup_request(
                "/2/tweets/search/recent",
                params=params,
            )
        except ApiError:
            log.exception("Failed to fetch hot-post replies for post %s", original_post_id)
            raise
        except Exception:
            log.exception("Unexpected failure fetching hot-post replies for post %s", original_post_id)
            raise

        replies = result.get("data", [])
        log.info("Fetched %d hot-post conversation candidate(s) for post_id=%s", len(replies), original_post_id)
        log_json_debug("Hot-post reply candidates returned", replies)

        raw_ids = [int(str(reply.get("id", "0"))) for reply in replies if str(reply.get("id", "")).isdigit()]
        raw_highest_id = str(max(raw_ids)) if raw_ids else ""
        candidates_for_this_post = 0

        for reply in sorted(replies, key=lambda t: int(t.get("id", 0))):
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
        if HOT_POST_REPLY_USE_SINCE_ID and raw_highest_id and candidates_for_this_post == 0:
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

    skipped = set(str(x) for x in state.get("skipped_hot_reply_ids", []))
    skipped.add(reply_id)
    state["skipped_hot_reply_ids"] = list(skipped)[-2000:]

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

    def made_with_ai_field_rejected(error: ApiError) -> bool:
        status_code = getattr(error, "status_code", None)
        if status_code not in {400, 422}:
            return False

        message = str(error).lower()
        return "made_with_ai" in message

    try:
        result = x_request("POST", "/2/tweets", json=payload)
        log.info("Created X post successfully. response=%s", result)
        return result
    except ApiError as exc:
        if made_with_ai and made_with_ai_field_rejected(exc):
            log.warning("Post failed because made_with_ai field was rejected; retrying without made_with_ai field")
            payload.pop("made_with_ai", None)
            result = x_request("POST", "/2/tweets", json=payload)
            log.info("Created X post successfully after removing made_with_ai. response=%s", result)
            return result
        raise


# ---------------------------------------------------------------------
# Quote/image posting
# ---------------------------------------------------------------------

def choose_unused_line(lines_used: set) -> tuple[int, str]:
    log.debug("Choosing unused line. Already used=%d", len(lines_used))

    with open(LINES_FILE) as f:
        lines = f.readlines()

    log.debug("Loaded %d lines from %s", len(lines), LINES_FILE)

    if not lines:
        raise RuntimeError(f"No lines found in {LINES_FILE}")

    all_lines = set(range(len(lines)))
    available_lines = list(all_lines.difference(lines_used))

    log.debug("Available unused lines=%d", len(available_lines))

    if not available_lines:
        log.info("All lines used; clearing line history")
        lines_used.clear()
        available_lines = list(all_lines)

    random.shuffle(available_lines)

    for line_no in available_lines:
        tweet = lines[line_no].rstrip()
        if tweet:
            log.debug("Selected line_no=%d text=%r", line_no, tweet)
            return line_no, tweet

        log.debug("Skipping empty line_no=%d", line_no)
        lines_used.add(line_no)

    raise RuntimeError(f"No non-empty lines found in {LINES_FILE}")


def choose_unused_image(images_used: set) -> tuple[int, str]:
    log.debug("Choosing unused image. Already used=%d", len(images_used))

    images = glob(IMAGE_GLOB)
    images.sort()

    log.debug("Found %d images matching %s", len(images), IMAGE_GLOB)

    if not images:
        raise RuntimeError(f"No images found matching {IMAGE_GLOB}")

    all_images = set(range(len(images)))
    available_images = list(all_images.difference(images_used))

    log.debug("Available unused images=%d", len(available_images))

    if not available_images:
        log.info("All images used; clearing image history")
        images_used.clear()
        available_images = list(all_images)

    image_no = random.choice(available_images)
    image = images[image_no]

    log.debug("Selected image_no=%d path=%s", image_no, image)
    return image_no, image


def post_random_quote(lines_used: set, images_used: set, state: dict) -> None:
    log.info("Starting quote/image post cycle")

    line_no, tweet = choose_unused_line(lines_used)
    image_no, image = choose_unused_image(images_used)

    log.info("Posting quote/image. line_no=%d image_no=%d image=%s", line_no, image_no, image)
    log.debug("Quote text=%r", tweet)

    media_id = upload_media(image)

    response = create_post(
        text=tweet,
        media_ids=[media_id],
        reply_to_id=None,
        made_with_ai=False,
    )

    posted_id = response.get("data", {}).get("id")
    log.debug("Posted_id=%s", posted_id)

    if posted_id:
        quote_post_epoch = now_epoch()
        state["last_main_post_id"] = str(posted_id)
        state["last_quote_post_epoch"] = quote_post_epoch

        maybe_schedule_meme_after_quote_post(state, quote_post_epoch)

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
        save_state(state)

    lines_used.add(line_no)
    save_pickle_set(PICKLE_FILE, lines_used)

    images_used.add(image_no)
    save_pickle_set(IMAGE_PICKLE_FILE, images_used)

    log_event("main_post_posted", lane="quote_image", post_id=posted_id, line_no=line_no, image_no=image_no)
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


def schedule_next_meme_post(state: dict, from_epoch: int | None = None, mode: str = "fallback") -> None:
    """
    Schedule the fallback daily meme time. This is deliberately later than the
    preferred organic timing. If a quote/image post happens after midday first,
    maybe_schedule_meme_after_quote_post() will replace this fallback with a
    random 35-75 minute delay after that post.
    """
    next_epoch = next_meme_fallback_epoch(state, from_epoch)
    state["next_meme_post_epoch"] = next_epoch
    state["meme_schedule_version"] = MEME_SCHEDULE_VERSION
    state["next_meme_schedule_mode"] = mode
    state["next_meme_schedule_date"] = epoch_date_str(next_epoch)
    state["meme_anchor_quote_post_epoch"] = 0
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


def maybe_schedule_meme_after_quote_post(state: dict, quote_post_epoch: int | None = None) -> None:
    if not ENABLE_DAILY_MEME_POSTS:
        return

    if quote_post_epoch is None:
        quote_post_epoch = now_epoch()

    quote_dt = datetime.fromtimestamp(int(quote_post_epoch))
    quote_date = quote_dt.strftime("%Y-%m-%d")

    if quote_dt.hour < MEME_TRIGGER_AFTER_HOUR:
        log.info(
            "Quote/image post was before meme trigger hour %02d:00; not scheduling daily meme from it",
            MEME_TRIGGER_AFTER_HOUR,
        )
        return

    if meme_posted_on_date(state, quote_date):
        log.info("Daily meme already posted on %s; not scheduling another", quote_date)
        return

    next_epoch = int(state.get("next_meme_post_epoch", 0) or 0)
    next_mode = str(state.get("next_meme_schedule_mode", "") or "")

    if next_epoch:
        next_date = epoch_date_str(next_epoch)
        if next_date == quote_date and next_mode == "after_first_quote_after_midday":
            log.info(
                "Daily meme already scheduled from first post after midday at %s; not rescheduling",
                datetime.fromtimestamp(next_epoch).strftime("%Y-%m-%d %H:%M:%S"),
            )
            return

    delay = random.randint(MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS, MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS)
    scheduled_epoch = int(quote_post_epoch) + delay

    state["next_meme_post_epoch"] = scheduled_epoch
    state["meme_schedule_version"] = MEME_SCHEDULE_VERSION
    state["next_meme_schedule_mode"] = "after_first_quote_after_midday"
    state["next_meme_schedule_date"] = quote_date
    state["meme_anchor_quote_post_epoch"] = int(quote_post_epoch)
    save_state(state)

    log.info(
        "Daily meme scheduled for %s: %d seconds after first quote/image post after %02d:00",
        datetime.fromtimestamp(scheduled_epoch).strftime("%Y-%m-%d %H:%M:%S"),
        delay,
        MEME_TRIGGER_AFTER_HOUR,
    )


def post_next_meme(state: dict) -> None:
    log.info("Starting daily meme post cycle")

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

    posted_id = response.get("data", {}).get("id")
    log.debug("Posted meme id=%s", posted_id)

    if posted_id:
        state["last_main_post_id"] = str(posted_id)
        state["last_meme_post_epoch"] = now_epoch()

        posted = set(str(x) for x in state.get("posted_meme_filenames", []))
        posted.add(meme_path.name)
        state["posted_meme_filenames"] = sorted(posted)

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
        save_state(state)

    log_event("main_post_posted", lane="daily_meme", post_id=posted_id, filename=meme_path.name)
    log.info("Daily meme posted successfully. posted_id=%s file=%s", posted_id, meme_path.name)

    schedule_next_meme_post(state)


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
    if candidate.get("_source", "mention") == "mention":
        update_last_seen_mention_id(state, str(candidate.get("id", "")))


def maybe_reply_to_mentions(state: dict) -> None:
    log.info("Starting mention reply check")

    if not ENABLE_AUTO_REPLIES:
        log.info("Auto replies disabled")
        return

    if lane_paused("disable_replies", "disable_normal_replies"):
        log.info("Skipping mention/hot-post reply check due to runtime control file")
        return

    if in_api_cooldown(state):
        log.info("Skipping mention check due to API cooldown")
        return

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
        return

    current = now_epoch()

    seconds_since_last_reply = current - int(state.get("last_reply_epoch", 0))
    log.debug(
        "Seconds since last generated/replied=%s minimum=%s",
        seconds_since_last_reply,
        MIN_SECONDS_BETWEEN_REPLIES,
    )

    if seconds_since_last_reply < MIN_SECONDS_BETWEEN_REPLIES:
        log.info("Skipping mention check: minimum interval between replies not reached")
        return

    try:
        mentions = get_mentions(state)
        hot_post_replies = get_hot_post_reply_candidates(state)
        mentions = dedupe_reply_candidates(mentions, hot_post_replies)
    except ApiError as e:
        log.exception("Failed to get mention/hot-post reply candidates")
        record_api_error(state, e, "x")
        save_state(state)
        return
    except Exception as e:
        log.exception("Unexpected failure getting mention/hot-post reply candidates")
        record_api_error(state, e, "x")
        save_state(state)
        return

    if not mentions:
        log.info("No mention or hot-post reply candidates returned")
        return

    mentions = sorted(mentions, key=lambda t: int(t["id"]))

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
            return

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
            return
        except Exception as e:
            log.exception("Unexpected Grok failure")
            record_api_error(state, e, "xai")
            save_state(state)
            return

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
            state["dry_run_seen_mention_ids"] = list(dry_run_seen_ids)[-1000:]

            mark_daily_author_replied(state, author_id)

            mark_mention_seen_if_applicable(state, mention)
            save_state(state)
            return

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
                state["replied_to_ids"] = list(replied_to_ids)[-1000:]
                mark_mention_seen_if_applicable(state, mention)
                save_state(state)
                return

            log.exception("Failed to post generated reply")
            record_api_error(state, e, "x")
            save_state(state)
            return
        except Exception as e:
            log.exception("Unexpected failure posting generated reply")
            record_api_error(state, e, "x")
            save_state(state)
            return

        state["daily_reply_count"] += 1
        state["last_reply_epoch"] = current

        replied_to_ids.add(mention_id)
        state["replied_to_ids"] = list(replied_to_ids)[-1000:]

        mark_daily_author_replied(state, author_id)

        own_reply_id = reply_response.get("data", {}).get("id")
        if own_reply_id:
            own_auto_reply_ids = set(str(x) for x in state.get("own_auto_reply_ids", []))
            own_auto_reply_ids.add(str(own_reply_id))
            state["own_auto_reply_ids"] = list(own_auto_reply_ids)[-1000:]

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
        return

    save_state(state)
    log.info("Mention reply check finished with no reply generated/posted")


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


def get_quote_tweets_for_post(post_id: str) -> list[dict]:
    log.info("Fetching quote tweets for post_id=%s", post_id)

    params = {
        "max_results": max(QUOTE_LOOKUP_API_MAX_RESULTS, MAX_QUOTE_POSTS_PER_CHECK),
        "tweet.fields": "author_id,created_at,conversation_id,referenced_tweets",
        "expansions": "author_id",
        "user.fields": "description,username,name,public_metrics",
    }

    result = x_quote_lookup_request(
        f"/2/tweets/{post_id}/quote_tweets",
        params=params,
    )

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
            "Quote tweet %s has no usable created_at; treating as old enough",
            quote_tweet.get("id"),
        )
        return True

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

    seen = set(str(x) for x in state.get("seen_quote_post_ids", []))
    skipped = set(str(x) for x in state.get("skipped_quote_post_ids", []))

    seen.add(quote_id)
    skipped.add(quote_id)

    state["seen_quote_post_ids"] = list(seen)[-2000:]
    state["skipped_quote_post_ids"] = list(skipped)[-2000:]


def mark_quote_tweet_replied(state: dict, quote_id: str) -> None:
    quote_id = str(quote_id)

    seen = set(str(x) for x in state.get("seen_quote_post_ids", []))
    replied = set(str(x) for x in state.get("replied_to_quote_post_ids", []))

    seen.add(quote_id)
    replied.add(quote_id)

    state["seen_quote_post_ids"] = list(seen)[-2000:]
    state["replied_to_quote_post_ids"] = list(replied)[-2000:]


def mark_quote_spam_author(state: dict, author_id: str) -> None:
    author_id = str(author_id)

    spam_authors = set(str(x) for x in state.get("quote_spam_author_ids", []))
    spam_authors.add(author_id)

    state["quote_spam_author_ids"] = list(spam_authors)[-2000:]
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

    if in_api_cooldown(state):
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
            record_api_error(state, e, "x")
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
            quote_tweets = get_quote_tweets_for_post(original_post_id)
        except ApiError as e:
            log.exception("Failed to fetch quote tweets for post %s", original_post_id)
            record_api_error(state, e, "x")
            save_state(state)
            return QUOTE_CHECK_STATUS_CHECKED
        except Exception:
            log.exception("Unexpected failure fetching quote tweets for post %s", original_post_id)
            save_state(state)
            return QUOTE_CHECK_STATUS_CHECKED

        for quote_tweet in sorted(quote_tweets, key=lambda t: int(t.get("id", 0))):
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
                record_api_error(state, e, "x")
                save_state(state)
                return QUOTE_CHECK_STATUS_CHECKED
            except Exception as e:
                log.exception("Unexpected failure posting generated quote-tweet reply")
                record_api_error(state, e, "x")
                save_state(state)
                return QUOTE_CHECK_STATUS_CHECKED

            state["daily_reply_count"] = int(state.get("daily_reply_count", 0) or 0) + 1
            state["daily_quote_reply_count"] = int(state.get("daily_quote_reply_count", 0) or 0) + 1
            state["last_reply_epoch"] = current

            mark_quote_tweet_replied(state, quote_id)

            mark_daily_author_replied(state, author_id)

            own_reply_id = reply_response.get("data", {}).get("id")
            if own_reply_id:
                own_auto_reply_ids = set(str(x) for x in state.get("own_auto_reply_ids", []))
                own_auto_reply_ids.add(str(own_reply_id))
                state["own_auto_reply_ids"] = list(own_auto_reply_ids)[-1000:]

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

def schedule_next_quote_post(state: dict, from_epoch: int | None = None) -> None:
    if from_epoch is None:
        from_epoch = now_epoch()

    delay = random.randint(POST_SLEEP_MIN, POST_SLEEP_MAX)
    state["next_quote_post_epoch"] = from_epoch + delay
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
    reply_lane_priority = str(state.get("next_reply_lane_priority", "normal") or "normal")
    if reply_lane_priority not in {"normal", "quote"}:
        reply_lane_priority = "normal"
        state["next_reply_lane_priority"] = reply_lane_priority
        save_state(state)

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

        before_reply_epoch = int(state.get("last_reply_epoch", 0) or 0)
        maybe_reply_to_mentions(state)
        last_reply_check_epoch = current
        after_reply_epoch = int(state.get("last_reply_epoch", 0) or 0)

        if after_reply_epoch != before_reply_epoch:
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
    log.info("Config: MAX_QUOTE_REPLIES_PER_DAY=%s", MAX_QUOTE_REPLIES_PER_DAY)
    log.info("Config: QUOTE_POST_LOOKBACK_MAIN_POSTS=%s", QUOTE_POST_LOOKBACK_MAIN_POSTS)
    log.info("Config: RECENT_OWN_POST_IDS_MAX=%s", RECENT_OWN_POST_IDS_MAX)
    log.info("Config: EXTRA_QUOTE_WATCH_FILE=%s", EXTRA_QUOTE_WATCH_FILE)
    log.info("Config: MAX_EXTRA_QUOTE_WATCH_POSTS=%s", MAX_EXTRA_QUOTE_WATCH_POSTS)
    log.info("Config: HOT_POST_REPLY_USE_SINCE_ID=%s", HOT_POST_REPLY_USE_SINCE_ID)
    log.info("Config: HOT_POST_REPLY_FULL_RESCAN_EVERY_CHECKS=%s", HOT_POST_REPLY_FULL_RESCAN_EVERY_CHECKS)
    log.info("Config: X_BEARER_TOKEN_SET=%s", bool(X_BEARER_TOKEN))

    images_at_start = glob(IMAGE_GLOB)
    log.info("Images found at startup=%d", len(images_at_start))

    if ENABLE_DAILY_MEME_POSTS:
        meme_candidates_at_start = list_meme_candidates()
        log.info("Meme candidates found at startup=%d", len(meme_candidates_at_start))

    lines_used = load_pickle_set(PICKLE_FILE)
    images_used = load_pickle_set(IMAGE_PICKLE_FILE)
    state = load_state()

    seed_recent_own_post_ids_from_cache(state)
    save_state(state)

    last_reply_check_epoch = 0
    last_quote_tweet_check_epoch = int(state.get("last_quote_tweet_check_epoch", 0) or 0)

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
            elif in_api_cooldown(state):
                log.warning("Skipping quote/image post due to API cooldown")
                schedule_next_quote_post(state, current)
            else:
                try:
                    post_random_quote(lines_used, images_used, state)
                except ApiError as e:
                    log.exception("Quote/image posting failed due to API error")
                    record_api_error(state, e, "x")
                except Exception:
                    log.exception("Quote/image posting failed unexpectedly")

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
                    state["next_meme_post_epoch"] = current + 300
                    state["next_meme_schedule_mode"] = "delayed_runtime_control"
                    state["next_meme_schedule_date"] = epoch_date_str(current + 300)
                    state["meme_schedule_version"] = MEME_SCHEDULE_VERSION
                    save_state(state)
                elif seconds_since_quote < MEME_MIN_SECONDS_AFTER_QUOTE_POST:
                    log.info(
                        "Meme post due, but delaying because last quote post was %d seconds ago",
                        seconds_since_quote,
                    )
                    state["next_meme_post_epoch"] = current + 1800
                    state["next_meme_schedule_mode"] = "delayed_recent_quote"
                    state["next_meme_schedule_date"] = epoch_date_str(current + 1800)
                    state["meme_schedule_version"] = MEME_SCHEDULE_VERSION
                    save_state(state)
                elif in_api_cooldown(state):
                    log.warning("Skipping daily meme post due to API cooldown")
                    state["next_meme_post_epoch"] = current + 3600
                    state["next_meme_schedule_mode"] = "delayed_api_cooldown"
                    state["next_meme_schedule_date"] = epoch_date_str(current + 3600)
                    state["meme_schedule_version"] = MEME_SCHEDULE_VERSION
                    save_state(state)
                else:
                    try:
                        post_next_meme(state)
                    except ApiError as e:
                        log.exception("Daily meme posting failed due to API error")
                        record_api_error(state, e, "x")
                        state["next_meme_post_epoch"] = current + 3600
                        state["next_meme_schedule_mode"] = "delayed_api_error"
                        state["next_meme_schedule_date"] = epoch_date_str(current + 3600)
                        state["meme_schedule_version"] = MEME_SCHEDULE_VERSION
                        save_state(state)
                    except Exception:
                        log.exception("Daily meme posting failed unexpectedly")
                        state["next_meme_post_epoch"] = current + 3600
                        state["next_meme_schedule_mode"] = "delayed_exception"
                        state["next_meme_schedule_date"] = epoch_date_str(current + 3600)
                        state["meme_schedule_version"] = MEME_SCHEDULE_VERSION
                        save_state(state)
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

    _self_test_warn("runtime control file present", CONTROL_FILE.exists(), str(CONTROL_FILE))
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

    log.info("Running one test cycle")
    log.info("Base dir=%s", BASE_DIR)
    log.info("State file=%s", STATE_FILE)
    log.info("Log file=%s", LOG_FILE)
    log.info("X base=%s", X_BASE)
    log.info("X upload base=%s", X_UPLOAD_BASE)
    log.info("xAI base=%s", XAI_BASE)

    state = load_state()

    reply_lane_priority = str(state.get("next_reply_lane_priority", "normal") or "normal")
    if reply_lane_priority not in {"normal", "quote"}:
        reply_lane_priority = "normal"
        state["next_reply_lane_priority"] = reply_lane_priority
        save_state(state)

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

    log.info("Running one test production reply-lane tick")
    state = load_state()
    current = now_epoch()
    last_quote_tweet_check_epoch = int(state.get("last_quote_tweet_check_epoch", 0) or 0)

    run_reply_lane_checks_for_tick(
        state,
        current,
        last_reply_check_epoch=0,
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


def run_test_post_quote() -> int:
    """Run one quote/image post cycle for local integration tests."""
    if not require_test_mode("--test-post-quote"):
        return 2

    log.info("Running one test quote/image post cycle")
    state = load_state()

    if lane_paused("disable_quote_posts"):
        log.warning("Skipping test quote/image post due to runtime control file")
        save_state(state)
        return 0

    lines_used = load_pickle_set(PICKLE_FILE)
    images_used = load_pickle_set(IMAGE_PICKLE_FILE)

    try:
        post_random_quote(lines_used, images_used, state)
    except ApiError as exc:
        log.exception("Test quote/image post failed due to API error")
        record_api_error(state, exc, "x")
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

    log.info("Running one test daily meme post cycle")
    state = load_state()

    if lane_paused("disable_meme_posts"):
        log.warning("Skipping test daily meme post due to runtime control file")
        save_state(state)
        return 0

    try:
        post_next_meme(state)
    except ApiError as exc:
        log.exception("Test daily meme post failed due to API error")
        record_api_error(state, exc, "x")
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
