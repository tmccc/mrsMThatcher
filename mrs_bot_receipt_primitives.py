"""Receipt scalar values, calendars and confirmation time.

The root supplies current runtime dependencies explicitly on each call. This
module performs no runtime work at import and retains no runtime authority.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def valid_post_id(
    value: object,
) -> bool:
    """Return whether valid post ID."""
    return bool(re.fullmatch(r"\d{1,30}", str(value or "")))


def valid_string_post_id(
    value: object,
) -> bool:
    """Return whether a durable receipt stores an exact string post ID."""
    return type(value) is str and valid_post_id(value)


def valid_receipt_epoch(
    value: object,
    *,
    MAX_CONFIRMATION_EPOCH: Any,
    MIN_CONFIRMATION_EPOCH: Any,
) -> bool:
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


def safe_epoch_date_str(
    epoch: int,
    *,
    epoch_date_str: Any,
) -> str | None:
    """Return the safe epoch date str."""
    try:
        return epoch_date_str(epoch)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def safe_reply_cap_date_str(
    epoch: int,
    *,
    reply_cap_date_str: Any,
) -> str | None:
    """Return a safe Europe/London conversational daily-cap date."""
    try:
        return reply_cap_date_str(epoch)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def main_post_schedule_zone(
    timezone_name: object,
    *,
    MAIN_POST_SCHEDULE_TIMEZONE: Any,
    ZoneInfo: Any,
    ZoneInfoNotFoundError: Any,
) -> ZoneInfo:
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


def bound_schedule_datetime(
    epoch: int,
    timezone_name: object,
    *,
    datetime: Any,
    main_post_schedule_zone: Any,
) -> datetime:
    """Interpret one durable epoch in its exact receipt-bound calendar zone."""

    if type(epoch) is not int:
        raise TypeError("bound schedule epoch must be an integer")
    return datetime.fromtimestamp(epoch, tz=main_post_schedule_zone(timezone_name))


def safe_bound_schedule_date_str(
    epoch: int,
    timezone_name: object,
    *,
    bound_schedule_datetime: Any,
) -> str | None:
    """Return a bound calendar date, or ``None`` for invalid receipt input."""

    try:
        return bound_schedule_datetime(epoch, timezone_name).strftime("%Y-%m-%d")
    except (TypeError, ValueError, RuntimeError, OverflowError, OSError):
        return None


def valid_receipt_basename(
    value: object,
) -> bool:
    """Return whether valid receipt basename."""
    if type(value) is not str:
        return False
    basename = value
    return bool(basename) and Path(basename).name == basename and basename not in {".", ".."}


def confirmation_epoch_after_remote_success(
    source_receipt: dict,
    *,
    TransportJournalError: Any,
    log: Any,
    now_epoch: Any,
    valid_receipt_epoch: Any,
) -> int:
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


def epoch_date_str(
    epoch: int | None = None,
    *,
    datetime: Any,
    now_epoch: Any,
) -> str:
    """Return the epoch date str."""
    if epoch is None:
        epoch = now_epoch()
    return datetime.fromtimestamp(int(epoch)).strftime("%Y-%m-%d")


def reply_cap_date_str(
    epoch: int | None = None,
    *,
    MAIN_POST_SCHEDULE_TIMEZONE: Any,
    ZoneInfo: Any,
    datetime: Any,
    now_epoch: Any,
) -> str:
    """Return the conversational daily-cap date in Europe/London."""
    if epoch is None:
        epoch = now_epoch()
    return datetime.fromtimestamp(
        int(epoch),
        tz=ZoneInfo(MAIN_POST_SCHEDULE_TIMEZONE),
    ).strftime("%Y-%m-%d")
