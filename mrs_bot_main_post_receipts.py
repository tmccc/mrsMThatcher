"""Validate main-post receipts and materialize their durable bound schedules.

MainPostReceiptValues owns attempt, pending and completed receipt grammar and
bound schedule materialization. Each operation retains its bound policy, date,
epoch and exception references. Sibling operations resolve a fresh owner at the
original invocation point so intervening runtime changes remain visible; no
operation snapshots an entire validation/materialization chain.
Fixed copying, hashing, payload reconstruction and calendar arithmetic are direct.
Legacy rules, native failures, receipt copies and the nonrecursive result-check
bypass stay unchanged. Builders, durable storage, transport and reconciliation
remain separate. Construction and import perform no runtime access; no caller
receipt or state is retained.
"""

from __future__ import annotations

import copy
import hashlib
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from mrs_bot_main_post_attempt_values import (
    MAIN_POST_ATTEMPT_MAX_BOUND_DELAY_SECONDS,
    canonical_remote_post_payload_sha256,
    main_post_attempt_payload,
)

from mrs_bot_durable_json_io import canonical_atomic_json_bytes
from mrs_bot_asset_metadata import quote_text_hash
from mrs_bot_receipt_primitives import (
    receipt_bool,
    receipt_int,
    valid_receipt_basename,
    valid_string_post_id,
)


@dataclass(frozen=True)
class MainPostReceiptValues:
    """Own receipt grammar with operation-local bindings and fresh sibling calls."""

    schedule_timezone: str
    schedule_modes: set[str]
    schedule_version: int
    bound_meme_state_is_valid: Callable[..., bool]
    safe_schedule_date: Callable[..., str | None]
    valid_epoch: Callable[..., bool]
    invalid_regular_receipt: type[Exception]
    invalid_meme_receipt: type[Exception]
    bound_schedule_datetime: Callable[..., datetime]
    current: Callable[[], MainPostReceiptValues]

    def attempt_is_valid(self, data: object) -> bool:
        """Return whether a pre-send regular or meme attempt is self-consistent."""
        if not isinstance(data, dict):
            return False
        lane = data.get("lane")
        if type(lane) is not str:
            return False
        supported_schemas = {
            "quote_image": {3, 4, 5},
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
        if attempt_epoch is None or not self.valid_epoch(attempt_epoch):
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
            if set(selected) != {
                "quote_hash",
                "line_no",
                "source_line_number",
                "image_basename",
                "image_no",
            }:
                return False
            quote_hash = selected.get("quote_hash")
            if (
                type(quote_hash) is not str
                or re.fullmatch(r"[0-9a-f]{64}", quote_hash) is None
                or quote_text_hash(text) != quote_hash
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
            if schema_version in {4, 5}:
                expected_recovery_keys |= {
                    "meme_scheduling_enabled",
                    "meme_trigger_after_hour",
                    "meme_schedule_version",
                    "meme_schedule_before",
                }
            if schema_version == 5:
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
            if schema_version in {4, 5} and (
                type(recovery_plan.get("meme_scheduling_enabled")) is not bool
                or type(recovery_plan.get("meme_trigger_after_hour")) is not int
                or not 0 <= int(recovery_plan["meme_trigger_after_hour"]) <= 23
                or type(recovery_plan.get("meme_schedule_version")) is not int
                or not 1
                <= int(recovery_plan["meme_schedule_version"])
                <= self.schedule_version
                or not self.bound_meme_state_is_valid(
                    recovery_plan.get("meme_schedule_before"),
                    schedule_timezone=(
                        recovery_plan.get("schedule_timezone")
                        if schema_version == 5
                        else None
                    ),
                )
                or (
                    schema_version == 5
                    and (
                        type(recovery_plan.get("schedule_timezone")) is not str
                        or recovery_plan["schedule_timezone"]
                        != self.schedule_timezone
                        or self.safe_schedule_date(
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
                        <= self.schedule_version
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
                                != self.schedule_timezone
                                or self.safe_schedule_date(
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


    def regular_is_valid(self, data: dict) -> bool:
        """Return whether a regular-post receipt is internally consistent."""
        schema_version = data.get("schema_version")
        if type(schema_version) is not int or schema_version not in {1, 2, 3}:
            return False
        post_id = data.get("post_id")
        quote_hash = data.get("quote_hash")
        image_basename = data.get("image_basename")
        text = data.get("text")
        quote_post_epoch = receipt_int(data.get("quote_post_epoch"))
        next_quote_post_epoch = receipt_int(data.get("next_quote_post_epoch"))
        lineage_attempt = data.get("source_attempt")
        bound_timezone = self.schedule_timezone
        if (
            isinstance(lineage_attempt, dict)
            and lineage_attempt.get("schema_version") == 5
            and isinstance(lineage_attempt.get("recovery_plan"), dict)
        ):
            bound_timezone = lineage_attempt["recovery_plan"].get(
                "schedule_timezone"
            )

        def schedule_date_for_epoch(epoch: int) -> str | None:
            return self.safe_schedule_date(epoch, bound_timezone)

        if not valid_string_post_id(post_id):
            return False
        if type(quote_hash) is not str or not re.fullmatch(r"[0-9a-f]{64}", quote_hash):
            return False
        if quote_post_epoch is None or not self.valid_epoch(quote_post_epoch):
            return False
        if next_quote_post_epoch is None or not self.valid_epoch(next_quote_post_epoch):
            return False
        if next_quote_post_epoch <= quote_post_epoch:
            return False
        if not valid_receipt_basename(image_basename):
            return False
        if not isinstance(text, str) or not text.strip():
            return False
        if quote_text_hash(text) != quote_hash:
            return False
        if schema_version in {2, 3}:
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
        if schema_version == 3 and (
            type(schedule_version) is not int
            or schedule_version < 0
            or schedule_version > self.schedule_version
        ):
            return False
        if schema_version < 3 and schedule_version is not None and (
            type(schedule_version) is not int
            or schedule_version < 0
            or schedule_version > self.schedule_version
        ):
            return False
        if next_meme_epoch:
            if schema_version == 3 and schedule_version < 1:
                return False
            if not self.valid_epoch(next_meme_epoch):
                return False
            mode = data.get("next_meme_schedule_mode")
            if type(mode) is not str:
                return False
            if mode not in self.schedule_modes or not mode:
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
                or schema_version != 3
                or type(data.get("line_no")) is not int
                or type(data.get("source_line_number")) is not int
                or type(data.get("image_no")) is not int
                or not isinstance(source_attempt, dict)
                or source_attempt.get("schema_version") != 5
                or source_attempt.get("lifecycle_state") != "attempting"
                or source_attempt.get("lane") != "quote_image"
                or not self.current().attempt_is_valid(source_attempt)
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
                not self.current().pending_is_valid(
                    pending,
                    expected_lane="quote_image",
                )
                or self.current().materialize_regular(
                    pending,
                    _validate_result=False,
                )
                != data
            ):
                return False
        return True


    def pending_is_valid(self, data: object, *, expected_lane: str | None = None) -> bool:
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
        if confirmation_epoch is None or not self.valid_epoch(confirmation_epoch):
            return False
        attempt = data.get("source_attempt")
        if (
            not self.current().attempt_is_valid(attempt)
            or attempt.get("lifecycle_state") != "attempting"
            # Older attempts remain readable as conservative restart barriers,
            # but their bytes did not bind a calendar zone.  They therefore cannot
            # authorise post-confirmation schedule materialisation.
            or attempt.get("schema_version") != 5
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


    def materialize_regular(self, pending: dict, *, _validate_result: bool = True) -> dict:
        """Build a full regular receipt solely from its durable bound plan."""
        if not self.current().pending_is_valid(
            pending,
            expected_lane="quote_image",
        ):
            raise self.invalid_regular_receipt(
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
            quote_dt = self.bound_schedule_datetime(
                quote_post_epoch,
                schedule_timezone,
            )
            quote_date = quote_dt.strftime("%Y-%m-%d")
            last_meme_epoch = int(snapshot["last_meme_post_epoch"])
            meme_already_posted = bool(
                last_meme_epoch
                and self.safe_schedule_date(
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

        receipt = {
            "schema_version": 3,
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
        if _validate_result and not self.current().regular_is_valid(receipt):
            raise self.invalid_regular_receipt(
                "Bound regular schedule produced an invalid confirmed receipt"
            )
        return receipt


    def materialize_meme(self, pending: dict, *, _validate_result: bool = True) -> dict:
        """Build a full meme receipt solely from its durable bound plan."""
        if not self.current().pending_is_valid(
            pending,
            expected_lane="daily_meme",
        ):
            raise self.invalid_meme_receipt(
                "Invalid confirmed meme pending-schedule receipt"
            )
        attempt = pending["source_attempt"]
        selected = attempt["selected_identity"]
        plan = attempt["recovery_plan"]
        meme_post_epoch = int(pending["confirmation_epoch"])
        schedule_timezone = str(plan["schedule_timezone"])
        confirmation_dt = self.bound_schedule_datetime(
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
        if _validate_result and not self.current().meme_is_valid(receipt):
            raise self.invalid_meme_receipt(
                "Bound meme schedule produced an invalid confirmed receipt"
            )
        return receipt


    def meme_is_valid(self, data: dict) -> bool:
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
        if meme_post_epoch is None or not self.valid_epoch(meme_post_epoch):
            return False
        if next_meme_post_epoch is None or not self.valid_epoch(next_meme_post_epoch):
            return False
        if next_meme_post_epoch <= meme_post_epoch:
            return False
        schedule_version = data.get("meme_schedule_version")
        if schema_version == 2 and (
            type(schedule_version) is not int
            or schedule_version < 1
            or schedule_version > self.schedule_version
        ):
            return False
        if schema_version == 1 and schedule_version is not None and (
            type(schedule_version) is not int
            or schedule_version < 1
            or schedule_version > self.schedule_version
        ):
            return False
        mode = data.get("next_meme_schedule_mode", "fallback")
        if type(mode) is not str:
            return False
        if mode not in self.schedule_modes or mode == "after_first_quote_after_midday":
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
                or not self.current().attempt_is_valid(source_attempt)
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
                not self.current().pending_is_valid(
                    pending,
                    expected_lane="daily_meme",
                )
                or self.current().materialize_meme(
                    pending,
                    _validate_result=False,
                )
                != data
            ):
                return False
        return True
