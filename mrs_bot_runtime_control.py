"""Read and validate runtime controls and apply global and lane pause policy.

The root supplies current settings, cache, key sets, callbacks, exception,
standard-library authorities and logger on each call. The four fixed key sets
share their initial objects with the root. Explicit calls read bounded stable
file snapshots, update the supplied cache and log through current callbacks;
original bodies preserve private copies, fail-closed results and call order.

Configuration, the cache lifecycle, strict JSON parsing and remote-write
boundaries remain in the root. Import uses only the standard library and
constructs fixed frozensets without file, environment, provider, clock or RNG
work. No callbacks, configuration, clients or runtime state are retained.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from pathlib import Path
from types import ModuleType



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


def parse_control_time(
    value: object,
    *,
    Decimal: type,
    MAX_REASONABLE_STATE_EPOCH: int,
    datetime: type,
    math: ModuleType,
) -> int:
    """Parse a runtime-control timestamp into an epoch value."""
    if isinstance(value, bool) or value is None:
        raise ValueError("control timestamp must not be a boolean or null")
    if type(value) is int:
        epoch = value
    elif type(value) is Decimal:
        if not value.is_finite() or value != value.to_integral_value():
            raise ValueError(
                "control timestamp exact number must be finite and integral"
            )
        if value < 0 or value > MAX_REASONABLE_STATE_EPOCH:
            raise ValueError(
                f"control timestamp is outside the supported epoch range: {value}"
            )
        epoch = int(value)
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


def validate_control_document(
    data: object,
    *,
    CONTROL_ALLOWED_KEYS: frozenset[str],
    CONTROL_BOOLEAN_KEYS: frozenset[str],
    CONTROL_TIME_KEYS: frozenset[str],
    Decimal: type,
    parse_control_time: Callable,
) -> dict:
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
            parsed_epoch = parse_control_time(value)
            if type(value) is Decimal:
                validated[key_text] = parsed_epoch
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


def control_failure_result(
    reason: str,
    *,
    signature: object,
    CONTROL_FILE: Path,
    _CONTROL_CACHE: dict[str, object],
    log: logging.Logger,
) -> dict:
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


def _runtime_control_stat_identity(
    file_stat: os.stat_result,
    *,
    stat: ModuleType,
) -> tuple[int, ...]:
    """Return the fields which must remain stable for one control snapshot."""

    return (
        file_stat.st_dev,
        file_stat.st_ino,
        stat.S_IFMT(file_stat.st_mode),
        file_stat.st_size,
        file_stat.st_mtime_ns,
        file_stat.st_ctime_ns,
    )


def _read_stable_runtime_control(
    *,
    CONTROL_FILE: Path,
    RUNTIME_CONTROL_MAX_BYTES: int,
    _RuntimeControlAbsent: type[FileNotFoundError],
    _runtime_control_stat_identity: Callable,
    hashlib: ModuleType,
    os: ModuleType,
    stat: ModuleType,
) -> tuple[bytes, tuple[object, ...]]:
    """Read one regular, non-symlink control file as a stable byte snapshot."""

    control_path = os.path.abspath(os.fspath(CONTROL_FILE))
    try:
        before_path = os.lstat(control_path)
    except FileNotFoundError as exc:
        raise _RuntimeControlAbsent(control_path) from exc
    if not stat.S_ISREG(before_path.st_mode):
        raise ValueError("runtime control must be a regular file")

    nonblock = getattr(os, "O_NONBLOCK", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow or not nonblock:
        raise RuntimeError(
            "runtime control requires O_NOFOLLOW and O_NONBLOCK support"
        )
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | nonblock
    descriptor = os.open(control_path, flags | nofollow)
    try:
        before_fd = os.fstat(descriptor)
        if not stat.S_ISREG(before_fd.st_mode):
            raise ValueError("runtime control must be a regular file")
        if _runtime_control_stat_identity(before_path) != _runtime_control_stat_identity(
            before_fd
        ):
            raise RuntimeError("runtime control changed before it was opened")
        if before_fd.st_size > RUNTIME_CONTROL_MAX_BYTES:
            raise ValueError(
                f"runtime control exceeds {RUNTIME_CONTROL_MAX_BYTES} bytes"
            )

        def read_document() -> bytes:
            chunks: list[bytes] = []
            observed = 0
            while observed <= RUNTIME_CONTROL_MAX_BYTES:
                chunk = os.read(
                    descriptor,
                    min(8192, RUNTIME_CONTROL_MAX_BYTES + 1 - observed),
                )
                if not chunk:
                    break
                chunks.append(chunk)
                observed += len(chunk)
            return b"".join(chunks)

        document = read_document()
        middle_fd = os.fstat(descriptor)
        if _runtime_control_stat_identity(before_fd) != _runtime_control_stat_identity(
            middle_fd
        ):
            raise RuntimeError("runtime control changed during its first read")
        if len(document) != before_fd.st_size:
            raise ValueError(
                "runtime control length did not match its stable file identity"
            )
        os.lseek(descriptor, 0, os.SEEK_SET)
        repeated_document = read_document()
        after_fd = os.fstat(descriptor)
        after_path = os.lstat(control_path)
    finally:
        os.close(descriptor)

    if (
        len(document) > RUNTIME_CONTROL_MAX_BYTES
        or len(document) != before_fd.st_size
    ):
        raise ValueError(
            "runtime control length did not match its stable file identity"
        )
    if repeated_document != document:
        raise RuntimeError("runtime control bytes changed during stable read")
    if _runtime_control_stat_identity(middle_fd) != _runtime_control_stat_identity(
        after_fd
    ):
        raise RuntimeError("runtime control changed while it was read")

    if _runtime_control_stat_identity(after_fd) != _runtime_control_stat_identity(
        after_path
    ):
        raise RuntimeError("runtime control path changed while it was read")

    signature: tuple[object, ...] = (
        control_path,
        *_runtime_control_stat_identity(after_fd),
        hashlib.sha256(document).hexdigest(),
    )
    return document, signature


def load_control(
    *,
    CONTROL_FILE: Path,
    _CONTROL_CACHE: dict[str, object],
    _RuntimeControlAbsent: type[FileNotFoundError],
    _read_stable_runtime_control: Callable,
    control_failure_result: Callable,
    load_strict_runtime_json: Callable,
    log: logging.Logger,
    log_json_debug: Callable,
    validate_control_document: Callable,
) -> dict:
    """Load and validate the optional fail-safe runtime-control document."""
    try:
        document, signature = _read_stable_runtime_control()
    except _RuntimeControlAbsent:
        _CONTROL_CACHE["signature"] = None
        _CONTROL_CACHE["data"] = {}
        _CONTROL_CACHE["has_valid"] = False
        _CONTROL_CACHE["failure_signature"] = None
        return {}
    except OSError as exc:
        return control_failure_result(
            str(exc),
            signature=("read", type(exc).__name__, str(exc)),
        )
    except Exception as exc:
        return control_failure_result(
            str(exc),
            signature=("snapshot", type(exc).__name__, str(exc)),
        )

    try:
        data = load_strict_runtime_json(
            document,
            label="runtime control",
            parse_floats_as_decimal=True,
        )
        data = validate_control_document(data)
    except Exception as exc:
        return control_failure_result(str(exc), signature=("content", signature, type(exc).__name__, str(exc)))

    changed = (
        _CONTROL_CACHE.get("signature") != signature
        or _CONTROL_CACHE.get("data") != data
    )
    _CONTROL_CACHE["signature"] = signature
    _CONTROL_CACHE["data"] = dict(data)
    _CONTROL_CACHE["has_valid"] = True
    _CONTROL_CACHE["failure_signature"] = None
    if changed:
        log.info("Loaded runtime control file %s", CONTROL_FILE)
        log_json_debug("Runtime control", data)
    return dict(data)


def control_bool(
    data: dict,
    key: str,
    *,
    log: logging.Logger,
) -> bool:
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
    log.warning("Invalid boolean passed directly to control_bool for %s=%r; returning false (runtime loader rejects invalid controls)", key, value)
    return False


def control_pause_active(
    data: dict,
    *keys: str,
    control_bool: Callable,
    log: logging.Logger,
    now_epoch: Callable,
    parse_control_time: Callable,
) -> tuple[bool, str, int]:
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
                log.error("Invalid pause passed directly to control_pause_active for %s=%r; runtime loader rejects invalid controls: %s", key, data.get(key), exc)
                continue
            if until_epoch > current:
                return True, key, until_epoch

    return False, "", 0


def lane_paused(
    *lane_keys: str,
    control_pause_active: Callable,
    datetime: type,
    load_control: Callable,
    log: logging.Logger,
    log_event: Callable,
) -> bool:
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


def global_remote_writes_paused(
    *,
    control_pause_active: Callable,
    load_control: Callable,
) -> bool:
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
