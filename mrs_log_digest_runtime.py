"""Read-only observations of current bot state, configuration and pause controls.

Project paths, stable readers and strict JSON parsers are explicit dependencies.
File-time conversion and the pause clock are supplied by the caller. Importing
this module performs no runtime reads, home lookup or service initialisation.
Report assembly, current-health interpretation and state observation timing stay
with the digest; this module imports neither it, Markdown nor the bot.
"""
from __future__ import annotations

import hashlib
import math
import os
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import AbstractSet, Any, Callable, Dict, List, Optional, Tuple

from mrs_log_digest_values import bounded_exception_status, dt_text


CURRENT_RUNTIME_STATE_MAX_BYTES = 64 * 1024 * 1024


CURRENT_RUNTIME_CONFIG_MAX_BYTES = 64 * 1024


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


def load_current_runtime_state(
    project_dir: Path,
    *,
    read_snapshot: Callable[..., Tuple[bytes, os.stat_result]],
    parse_json_object: Callable[..., Dict[str, Any]],
    fromtimestamp: Callable[[float], datetime],
    maximum: int = CURRENT_RUNTIME_STATE_MAX_BYTES,
) -> Tuple[Optional[Dict[str, Any]], Path, Optional[datetime], str]:
    """Read current state and return content bound to its observed file metadata.

    The supplied reader enforces stable bounded no-follow reads; the parser
    retains native JSON numeric types. ``fromtimestamp`` converts file mtime
    using the caller's local-time semantics. This reader does not sample a
    clock: the digest records observation time immediately after it returns.
    """
    path = project_dir / "bot_state.json"
    try:
        raw, metadata = read_snapshot(
            path,
            maximum=maximum,
        )
        data = parse_json_object(raw, label="bot_state.json")
        if not any(
            key in data
            for key in (
                "daily_reply_count",
                "last_main_post_id",
                "last_seen_mention_id",
                "next_reply_lane_priority",
                "author_evaluation_quarantines",
            )
        ):
            raise ValueError("state has no recognised runtime fields")
        for key in ("daily_reply_count", "daily_quote_reply_count"):
            if key in data and (
                type(data[key]) is not int or data[key] < 0
            ):
                raise ValueError(f"{key} is not a non-negative integer")
        mtime = fromtimestamp(metadata.st_mtime)
        return data, path, mtime, "available"
    except FileNotFoundError:
        return None, path, None, "absent"
    except RuntimeError as exc:
        status = "unstable" if "changed" in str(exc) else "malformed"
        return None, path, None, bounded_exception_status(status, exc)
    except Exception as exc:
        return None, path, None, bounded_exception_status("malformed", exc)


def load_current_runtime_config(
    project_dir: Path,
    *,
    read_snapshot: Callable[..., Tuple[bytes, os.stat_result]],
    parse_json_object: Callable[..., Dict[str, Any]],
    fromtimestamp: Callable[[float], datetime],
    maximum: int = CURRENT_RUNTIME_CONFIG_MAX_BYTES,
    report_keys: AbstractSet[str] = CURRENT_CONFIG_REPORT_KEYS,
) -> Tuple[Optional[Dict[str, Any]], Path, Optional[datetime], str]:
    """Read and validate allow-listed overrides through the supplied stable reader.

    The native JSON parser validates the complete document before filtering.
    Returned source times describe the metadata bound to these bytes, with
    local-time conversion supplied by the caller; no current clock is sampled.
    """
    path = project_dir / "mrsMThatcher.local.json"
    try:
        raw, metadata = read_snapshot(
            path,
            maximum=maximum,
        )
        data = parse_json_object(
            raw,
            label="mrsMThatcher.local.json",
        )
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
            if key in report_keys
        }
        config["_config_source"] = "mrsMThatcher.local.json"
        config["_config_source_path"] = str(path)
        config["_config_source_time"] = dt_text(
            fromtimestamp(metadata.st_mtime)
        )
        return config, path, fromtimestamp(metadata.st_mtime), "available"
    except FileNotFoundError:
        return None, path, None, "absent"
    except RuntimeError as exc:
        status = "unstable" if "changed" in str(exc) else "malformed"
        return None, path, None, bounded_exception_status(status, exc)
    except Exception as exc:
        return None, path, None, bounded_exception_status("malformed", exc)


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


def runtime_control_snapshot(
    project_dir: Path,
    *,
    read_bytes: Callable[..., bytes],
    parse_json_object: Callable[..., Dict[str, Any]],
    now: Callable[[], datetime],
) -> Dict[str, Any]:
    """Read current operator pauses, treating an invalid document as a global pause.

    The caller supplies a stable bounded no-follow reader and the strict JSON
    parser that preserves exact Decimal values. ``now`` is sampled only after
    reading, parsing, checking allowed keys and validating generation, before
    boolean/time validation and expiry comparisons, as in the digest.
    """

    path = Path(project_dir) / "mrsMThatcher.control.json"
    try:
        data = read_bytes(
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
        value = parse_json_object(data, label="runtime control")
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
        now_epoch = int(now().timestamp())
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
