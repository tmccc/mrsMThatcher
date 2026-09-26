"""Read-only state, configuration, pause-control and feature-lifecycle observations.

Project paths, stable readers and strict JSON parsers are explicit dependencies.
File-time conversion and the pause clock are supplied by the caller. Importing
this module performs no runtime reads, home lookup or service initialisation.
Lifecycle loading imports its read-only register helpers lazily inside the
existing failure boundary, using only the supplied project directory.
Report assembly, current-health interpretation and state observation timing stay
with the digest; this module imports neither it, Markdown nor the bot.
"""
from __future__ import annotations

import ast
import hashlib
import os
import stat
from datetime import datetime
from pathlib import Path
from typing import AbstractSet, Any, Callable, Dict, List, Optional, Tuple

from mrs_log_digest_contracts import (
    ParseNativeObject, ReadStableSnapshot, RuntimeConfigSnapshot,
    RuntimeStateSnapshot,
)
from mrs_log_digest_values import bounded_exception_status, dt_text
from runtime_control_contract import (
    CONTROL_ALLOWED_KEYS as REMOTE_WRITE_CONTROL_ALLOWED_KEYS,
    CONTROL_BOOLEAN_KEYS as REMOTE_WRITE_CONTROL_BOOLEAN_KEYS,
    CONTROL_TIME_KEYS as REMOTE_WRITE_CONTROL_TIME_KEYS,
    MAX_CONTROL_EPOCH,
    parse_control_time,
    validate_control_metadata,
    validate_control_values,
)


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
    "RESET_MEME_CYCLE_WHEN_ALL_POSTED",
    "MEME_TRIGGER_AFTER_HOUR",
    "MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS",
    "MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS",
    "MEME_FALLBACK_HOUR",
    "MEME_FALLBACK_MINUTE",
    "MEME_MIN_SECONDS_AFTER_QUOTE_POST",
    "MEME_SCHEDULE_VERSION",
    "POST_SLEEP_MIN",
    "POST_SLEEP_MAX",
}


def load_current_runtime_state(
    project_dir: Path,
    *,
    read_snapshot: ReadStableSnapshot,
    parse_json_object: ParseNativeObject,
    fromtimestamp: Callable[[float], datetime],
    maximum: int = CURRENT_RUNTIME_STATE_MAX_BYTES,
) -> Tuple[Optional[RuntimeStateSnapshot], Path, Optional[datetime], str]:
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
        return RuntimeStateSnapshot(data), path, mtime, "available"
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
    read_snapshot: ReadStableSnapshot,
    parse_json_object: ParseNativeObject,
    fromtimestamp: Callable[[float], datetime],
    maximum: int = CURRENT_RUNTIME_CONFIG_MAX_BYTES,
    report_keys: AbstractSet[str] = CURRENT_CONFIG_REPORT_KEYS,
) -> Tuple[Optional[RuntimeConfigSnapshot], Path, Optional[datetime], str]:
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
        return RuntimeConfigSnapshot(config), path, fromtimestamp(metadata.st_mtime), "available"
    except FileNotFoundError:
        return None, path, None, "absent"
    except RuntimeError as exc:
        status = "unstable" if "changed" in str(exc) else "malformed"
        return None, path, None, bounded_exception_status(status, exc)
    except Exception as exc:
        return None, path, None, bounded_exception_status("malformed", exc)


def meme_queue_health_snapshot(
    project_dir: Path,
    *,
    runtime_state: Optional[Dict[str, Any]],
    runtime_state_status: str,
    runtime_config: Optional[Dict[str, Any]],
    runtime_config_status: str,
    observed_at: datetime,
    state_observed_at: datetime,
    read_snapshot: Callable[..., Tuple[bytes, os.stat_result]],
) -> Dict[str, Any]:
    """Observe current meme inventory without selecting, resetting or posting.

    Counts use the bot's image extensions and filename history. Missing history
    or uninspectable inputs are unknown, not an empty used-history set. Local
    overrides are applied to literal defaults read without executing bot code.
    This snapshot describes files/configuration now, independently of log-window
    activity; it does not establish scheduling or remote-write eligibility.
    """
    directory = project_dir / "final_posting_queue_top90_as_is" / "images"
    result: Dict[str, Any] = {
        "available": False,
        "status": "unknown",
        "scope": "current_filesystem_and_configuration",
        "observed_at": dt_text(observed_at),
        "state_observed_at": dt_text(state_observed_at),
        "directory": str(directory),
        "candidate_count": None,
        "posted_count": None,
        "unposted_count": None,
        "available_to_select_count": None,
        "posting_enabled": None,
        "reset_when_all_posted": None,
        "configuration_sources": {},
    }
    try:
        if runtime_config_status not in {"available", "absent"}:
            raise ValueError(f"current configuration unavailable: {runtime_config_status}")
        config = runtime_config or {}
        keys = {"ENABLE_DAILY_MEME_POSTS", "RESET_MEME_CYCLE_WHEN_ALL_POSTED"}
        missing_defaults = keys - config.keys()
        values = {}
        if missing_defaults:
            source, _ = read_snapshot(project_dir / "mrsMThatcher2.py", maximum=4 * 1024 * 1024)
            for node in ast.parse(source).body:
                if not isinstance(node, ast.Assign):
                    continue
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id in missing_defaults:
                        if target.id in values:
                            raise ValueError(f"duplicate meme default: {target.id}")
                        value = ast.literal_eval(node.value)
                        if type(value) is not bool:
                            raise ValueError(f"non-boolean meme default: {target.id}")
                        values[target.id] = value
                        result["configuration_sources"][target.id] = "mrsMThatcher2.py default"
            if missing_defaults - values.keys():
                raise ValueError("meme configuration defaults unavailable")
        for key in keys & config.keys():
            value = config[key]
            if isinstance(value, str):
                normal = value.strip().lower()
                if normal in {"1", "true", "yes", "on", "0", "false", "no", "off"}:
                    value = normal in {"1", "true", "yes", "on"}
            if type(value) is not bool:
                raise ValueError(f"{key} must be a boolean")
            values[key] = value
            result["configuration_sources"][key] = "mrsMThatcher.local.json override"
        result["posting_enabled"] = values["ENABLE_DAILY_MEME_POSTS"]
        result["reset_when_all_posted"] = values["RESET_MEME_CYCLE_WHEN_ALL_POSTED"]

        try:
            metadata = directory.lstat()
        except FileNotFoundError:
            result.update(status="missing", reason="Meme directory does not exist.")
            return result
        if not stat.S_ISDIR(metadata.st_mode):
            raise ValueError("meme directory is not a regular directory")
        candidates = set()
        with os.scandir(directory) as entries:
            for entry in entries:
                if Path(entry.name).suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
                    continue
                if entry.is_symlink():
                    raise ValueError("meme inventory contains a symbolic-link image")
                if entry.is_file(follow_symlinks=False):
                    if not os.access(entry.path, os.R_OK):
                        raise ValueError("meme inventory contains an unreadable image")
                    candidates.add(entry.name)
        result["candidate_count"] = len(candidates)
        if not candidates:
            result.update(
                available=True, status="empty", posted_count=0, unposted_count=0,
                available_to_select_count=0, reason="Meme directory contains no candidate images.",
            )
            return result
        if runtime_state_status != "available" or not isinstance(runtime_state, dict):
            raise ValueError(f"current meme history unavailable: {runtime_state_status}")
        history = runtime_state.get("posted_meme_filenames")
        if not isinstance(history, list) or not all(isinstance(name, str) and name for name in history):
            raise ValueError("posted_meme_filenames is missing or malformed")
        posted_count = len(candidates.intersection(history))
        unposted_count = len(candidates) - posted_count
        result.update(available=True, posted_count=posted_count, unposted_count=unposted_count)
        if not result["posting_enabled"]:
            result.update(status="disabled", available_to_select_count=0, reason="Daily meme posting is disabled.")
        elif unposted_count:
            result.update(status="available", available_to_select_count=unposted_count, reason="Unposted meme images remain in the current cycle.")
        elif result["reset_when_all_posted"]:
            result.update(status="recycling_available", available_to_select_count=len(candidates), reason="All candidate images have been posted; automatic recycling makes them available at the next selection.")
        else:
            result.update(status="exhausted", available_to_select_count=0, reason="All candidate images have been posted and automatic recycling is disabled.")
    except Exception as exc:
        result.update(available=False, status="unknown", reason=bounded_exception_status("inspection unavailable", exc))
    return result


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
    """Parse one control timestamp using the same contract as the running bot."""
    return parse_control_time(
        value,
        MAX_REASONABLE_STATE_EPOCH=MAX_CONTROL_EPOCH,
        datetime=datetime,
    )


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
        validate_control_metadata(
            value, CONTROL_ALLOWED_KEYS=REMOTE_WRITE_CONTROL_ALLOWED_KEYS,
        )
        generation = value.get("generation")
        now_epoch = int(now().timestamp())
        value = validate_control_values(
            value,
            CONTROL_BOOLEAN_KEYS=REMOTE_WRITE_CONTROL_BOOLEAN_KEYS,
            CONTROL_TIME_KEYS=REMOTE_WRITE_CONTROL_TIME_KEYS,
            parse_control_time=_control_epoch,
        )
        active: List[str] = []
        for key in sorted(REMOTE_WRITE_CONTROL_BOOLEAN_KEYS):
            if key not in value:
                continue
            raw = value[key]
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
