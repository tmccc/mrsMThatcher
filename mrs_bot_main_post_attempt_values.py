"""Main-post payload, bound-plan, attempt and confirmation values.

Canonical payload reconstruction, copying, hashing and ID shape checks are
fixed local operations. The
root supplies current runtime dependencies explicitly on each call. This module
performs no runtime work at import and retains no runtime authority.
"""
from __future__ import annotations

import copy
import hashlib
import json
import logging

from collections.abc import Callable, Mapping
from typing import Any, TYPE_CHECKING, Protocol, cast

if TYPE_CHECKING:
    from mrs_bot_core_contracts import BoundMemeScheduleState, PendingMainPostReceipt, SendingMainPostAttempt


class RandomBytes(Protocol):
    """Only the random-byte operation used to create an attempt identity."""

    def urandom(self, size: int) -> bytes:
        """Return bytes from the operation-bound source."""
        ...

from mrs_bot_receipt_primitives import valid_post_id


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


def canonical_remote_post_payload_sha256(payload: Mapping[str, object]) -> str:
    """Return the stable identity of one exact X create payload."""
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def main_post_attempt_payload(attempt: Mapping[str, object]) -> dict[str, object]:
    """Reconstruct the exact remote payload bound by a main-post attempt."""
    payload: dict[str, object] = {}
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


def bound_meme_schedule_state(
    state: Mapping[str, Any],
    *,
    schedule_timezone: str | None = None,
    MAIN_POST_SCHEDULE_TIMEZONE: str,
    MEME_SCHEDULE_VERSION: int,
    safe_bound_schedule_date_str: Callable[[int, str], str | None],
) -> BoundMemeScheduleState:
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
    MAIN_POST_SCHEDULE_TIMEZONE: str,
    MEME_SCHEDULE_MODES: set[str],
    MEME_SCHEDULE_VERSION: int,
    safe_bound_schedule_date_str: Callable[[int, str], str | None],
    valid_receipt_epoch: Callable[[int], bool],
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


def main_post_attempt_binds_payload(
    attempt: Mapping[str, Any],
    payload: Mapping[str, object],
    *,
    current_main_post_attempt_is_semantically_valid: Callable[[object], bool],
) -> bool:
    """Return whether an attempt authorises exactly one remote payload."""
    return bool(
        current_main_post_attempt_is_semantically_valid(attempt)
        and main_post_attempt_payload(attempt) == payload
        and type(attempt.get("payload_sha256")) is str
        and canonical_remote_post_payload_sha256(payload)
        == attempt["payload_sha256"]
    )


def current_main_post_attempt_is_semantically_valid(
    data: object,
    *,
    main_post_attempt_is_semantically_valid: Callable[[object], bool],
) -> bool:
    """Return whether an attempt belongs to the current writable generation."""

    return bool(
        main_post_attempt_is_semantically_valid(data)
        and isinstance(data, dict)
        and data.get("schema_version") == 5
    )


def build_main_post_attempt(
    *,
    lane: str,
    text: str,
    media_ids: list[str],
    made_with_ai: bool,
    selected_identity: dict[str, object],
    recovery_plan: dict[str, object],
    attempt_epoch: int | None = None,
    MAIN_POST_SCHEDULE_TIMEZONE: str,
    current_main_post_attempt_is_semantically_valid: Callable[[object], bool],
    now_epoch: Callable[[], int],
    os: RandomBytes,
) -> SendingMainPostAttempt:
    """Build a durable pre-send identity for one main-post transaction."""
    if lane not in {"quote_image", "daily_meme"}:
        raise ValueError(f"Unsupported main-post lane: {lane}")
    payload: dict[str, object] = {}
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
    attempt = {
        "schema_version": 5,
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
    if not current_main_post_attempt_is_semantically_valid(attempt):
        raise RuntimeError("Internal error: generated main-post attempt is invalid")
    return cast("SendingMainPostAttempt", attempt)


def confirmed_receipt_matches_main_attempt(
    receipt: Mapping[str, Any],
    attempt: Mapping[str, Any],
    *,
    main_post_attempt_is_semantically_valid: Callable[[object], bool],
) -> bool:
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


def build_confirmed_pending_schedule_receipt(
    attempt: Mapping[str, Any],
    *,
    post_id: str,
    confirmation_epoch: int,
    image_summary: str = '',
    confirmed_pending_schedule_receipt_is_semantically_valid: Callable[..., bool],
    main_post_attempt_is_semantically_valid: Callable[[object], bool],
    valid_receipt_epoch: Callable[[int], bool],
) -> PendingMainPostReceipt:
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
    return cast("PendingMainPostReceipt", pending)


def confirmation_epoch_for_main_attempt(
    attempt: Mapping[str, Any],
    observed_epoch: int,
    *,
    log: logging.Logger,
) -> int:
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
