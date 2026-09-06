"""Normalise durable state scalar and collection values.

Eleven root adapters supply current regex, math, logger, epoch cap and nested
normalizers on each call. Original bodies preserve the distinct ID/scalar
coercions, validation and logging order, shallow record copies and epoch-list
identity. Full state/schema/reader validation, higher-level mention/cache/receipt
policy and durable I/O remain in their existing locations. This owner retains
no callbacks, configuration, paths or state and performs no import-time runtime
work or reverse application import.
"""

from __future__ import annotations

from collections.abc import Callable
from logging import Logger
from pathlib import Path
from types import ModuleType


def bounded_tweet_id_value(
    value: object,
    *,
    allow_empty: bool = False,
    re: ModuleType,
) -> int | None:
    """Parse one bounded string tweet ID without unbounded integer conversion."""
    if allow_empty and value == "":
        return 0
    if type(value) is not str or not re.fullmatch(r"\d{1,30}", value):
        return None
    return int(value)


def normalise_state_int(
    value: object,
    *,
    key: str,
    path: Path,
    log: Logger,
    math: ModuleType,
) -> int | None:
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


def normalise_state_epoch(
    value: object,
    *,
    key: str,
    path: Path,
    MAX_REASONABLE_STATE_EPOCH: int,
    log: Logger,
    normalise_state_int: Callable[..., int | None],
) -> int | None:
    """Normalise state epoch."""
    number = normalise_state_int(value, key=key, path=path)
    if number is None:
        return None
    if number > MAX_REASONABLE_STATE_EPOCH:
        log.error("State candidate %s has impossible epoch %s=%r; ignoring", path, key, value)
        return None
    return number


def normalise_string_list(
    value: object,
    *,
    key: str,
    path: Path,
    log: Logger,
) -> list[str] | None:
    """Normalise string list."""
    if not isinstance(value, list):
        log.error("State candidate %s has invalid %s type %s; ignoring", path, key, type(value).__name__)
        return None
    return [str(item) for item in value if item is not None]


def normalise_int_list(
    value: object,
    *,
    key: str,
    path: Path,
    log: Logger,
    normalise_state_int: Callable[..., int | None],
) -> list[int] | None:
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


def normalise_epoch_list(
    value: object,
    *,
    key: str,
    path: Path,
    MAX_REASONABLE_STATE_EPOCH: int,
    log: Logger,
    normalise_int_list: Callable[..., list[int] | None],
) -> list[int] | None:
    """Normalise epoch list."""
    out = normalise_int_list(value, key=key, path=path)
    if out is None:
        return None
    for number in out:
        if number > MAX_REASONABLE_STATE_EPOCH:
            log.error("State candidate %s has impossible %s epoch item %r; ignoring", path, key, number)
            return None
    return out


def normalise_string_map(
    value: object,
    *,
    key: str,
    path: Path,
    log: Logger,
) -> dict[str, str] | None:
    """Normalise string map."""
    if not isinstance(value, dict):
        log.error("State candidate %s has invalid %s type %s; ignoring", path, key, type(value).__name__)
        return None
    return {str(k): str(v) for k, v in value.items() if v is not None}


def normalise_int_map(
    value: object,
    *,
    key: str,
    path: Path,
    log: Logger,
    normalise_state_int: Callable[..., int | None],
) -> dict[str, int] | None:
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


def normalise_record_map(
    value: object,
    *,
    key: str,
    path: Path,
    log: Logger,
) -> dict[str, dict] | None:
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


def normalise_optional_scalar(
    value: object,
    *,
    key: str,
    path: Path,
    log: Logger,
) -> str | None:
    """Normalise optional scalar."""
    if value is None:
        return ""
    if isinstance(value, (str, int)):
        return str(value)
    log.error("State candidate %s has invalid %s type %s; ignoring", path, key, type(value).__name__)
    return None


def normalise_optional_numeric_id(
    value: object,
    *,
    key: str,
    path: Path,
    bounded_tweet_id_value: Callable[..., int | None],
    log: Logger,
) -> str | None:
    """Normalise optional numeric ID."""
    if value in (None, ""):
        return ""
    text = str(value)
    if bounded_tweet_id_value(text) is not None:
        return text
    log.error("State candidate %s has invalid %s value %r; ignoring", path, key, value)
    return None
