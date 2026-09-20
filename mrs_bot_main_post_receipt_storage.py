"""Own main-post receipt storage and durable lifecycle transitions.

Each operation binds current paths, I/O, validation and error authorities. Calls
to another storage operation refresh that binding at the existing boundary;
publication gates, exact receipt bytes and lane-specific reader contracts stay
local to this owner. Receipt grammar and schedule materialization resolve the
current values owner only when invoked, independently at each operation boundary.
Removal receives an explicitly proof-bound retirement capability from the root
adapter. Importing the module performs no runtime work.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from logging import Logger
from pathlib import Path
from typing import TYPE_CHECKING

from mrs_bot_durable_json_io import canonical_atomic_json_bytes

if TYPE_CHECKING:
    from mrs_bot_main_post_receipts import MainPostReceiptValues


@dataclass(frozen=True)
class MainPostReceipts:
    """Store both main-post lanes with fresh bindings for nested operations."""

    MEME_POST_RECEIPT_FILE: Path
    REGULAR_POST_RECEIPT_FILE: Path
    CONFIRMED_REPLY_RECEIPT_FILE: Path
    InvalidConfirmedReplyReceipt: type[Exception]
    UnresolvedRegularPostReceipt: type[Exception]
    current_main_post_attempt_is_semantically_valid: Callable[..., bool]
    durable_create_receipt_json: Callable[..., None]
    log: Logger
    receipt_namespace_entry_exists: Callable[..., bool]
    remote_receipt_retirement_is_blocking: Callable[..., bool]
    AmbiguousRemotePostOutcome: type[Exception]
    replace_exact_source_receipt_document: Callable[..., None]
    transaction_mutation_authority: Callable[..., object]
    load_receipt_json_no_follow: Callable[[Path], tuple[bool, object | None]]
    UnresolvedMemePostReceipt: type[Exception]
    atomic_write_json: Callable[..., None]
    confirmed_receipt_matches_main_attempt: Callable[..., bool]
    values: Callable[[], MainPostReceiptValues]
    current: Callable[[], MainPostReceipts]

    def attempt_path(
        self,
        attempt: dict,
    ) -> Path:
        """Return the receipt path which owns one main-post attempt."""
        lane = str(attempt.get("lane") or "")
        if lane == "quote_image":
            return self.REGULAR_POST_RECEIPT_FILE
        if lane == "daily_meme":
            return self.MEME_POST_RECEIPT_FILE
        raise ValueError(f"Unsupported main-post attempt lane: {lane}")

    def write_attempt(
        self,
        attempt: dict,
    ) -> None:
        """Durably record a main-post transaction before its X create request."""
        if (
            not self.current_main_post_attempt_is_semantically_valid(attempt)
            or attempt.get("lifecycle_state") != "sending"
        ):
            raise RuntimeError(
                "Only a current-schema sending main-post attempt may enter the "
                "live write path"
            )
        if self.remote_receipt_retirement_is_blocking():
            raise self.UnresolvedRegularPostReceipt(
                "Refusing a main-post attempt while source-receipt retirement is incomplete"
            )
        if self.receipt_namespace_entry_exists(
            self.REGULAR_POST_RECEIPT_FILE
        ) or self.receipt_namespace_entry_exists(self.MEME_POST_RECEIPT_FILE):
            raise self.UnresolvedRegularPostReceipt(
                "Refusing to overwrite an unresolved regular or meme transaction"
            )
        if self.receipt_namespace_entry_exists(self.CONFIRMED_REPLY_RECEIPT_FILE):
            raise self.InvalidConfirmedReplyReceipt(
                "Refusing a main-post attempt while a conversational-reply receipt exists"
            )
        path = self.current().attempt_path(attempt)
        try:
            self.durable_create_receipt_json(path, attempt)
        except FileExistsError as exc:
            raise self.UnresolvedRegularPostReceipt(
                "Refusing to overwrite a receipt namespace entry which appeared "
                f"during main-post publication: {path}"
            ) from exc
        self.log.warning(
            "Wrote main-post sending receipt lane=%s attempt_id=%s path=%s",
            attempt["lane"],
            attempt["attempt_id"],
            path,
        )

    def mark_attempting(
        self,
        attempt: dict,
    ) -> dict:
        """Atomically consume one sending authorisation before remote transmission."""
        if (
            not self.current_main_post_attempt_is_semantically_valid(attempt)
            or attempt.get("lifecycle_state") != "sending"
        ):
            raise self.AmbiguousRemotePostOutcome(
                "Only a current-schema main-post attempt may transmit once from "
                "sending state",
                service="x",
            )
        path = self.current().attempt_path(attempt)
        status, current = (
            self.current().load_regular()
            if path == self.REGULAR_POST_RECEIPT_FILE
            else self.current().load_meme()
        )
        if status != "sending" or current != attempt:
            raise self.AmbiguousRemotePostOutcome(
                "Main-post sending receipt changed before transmission",
                service="x",
            )
        attempting = {**attempt, "lifecycle_state": "attempting"}
        if not self.current_main_post_attempt_is_semantically_valid(attempting):
            raise RuntimeError("Attempting main-post receipt failed validation")
        self.replace_exact_source_receipt_document(
            path,
            expected_bytes=canonical_atomic_json_bytes(attempt),
            replacement_bytes=canonical_atomic_json_bytes(attempting),
            mutation_authority=self.transaction_mutation_authority(
                "main-post sending-to-attempting receipt promotion"
            ),
        )
        self.log.warning(
            "Promoted main-post receipt to attempting lane=%s attempt_id=%s path=%s",
            attempting["lane"],
            attempting["attempt_id"],
            path,
        )
        return attempting

    def remove_attempt(
        self,
        attempt: dict,
        *,
        sending_disposition: str,
        retire_current_source_receipt: Callable[..., None],
    ) -> None:
        """Retire an exact sending attempt after one proved-safe disposition."""
        if sending_disposition not in {
            "definite_non_success",
            "confirmed_state_fallback",
        }:
            raise ValueError("A main-post sending receipt requires an explicit disposition")
        if not self.current_main_post_attempt_is_semantically_valid(attempt):
            raise self.AmbiguousRemotePostOutcome(
                "Refusing to mutate a legacy or invalid main-post attempt",
                service="x",
            )
        path = self.current().attempt_path(attempt)
        try:
            present, current = self.load_receipt_json_no_follow(path)
            if not present:
                raise FileNotFoundError(path)
            if (
                current != attempt
                or not self.current_main_post_attempt_is_semantically_valid(current)
            ):
                raise self.AmbiguousRemotePostOutcome(
                    "Refusing to remove a changed main-post sending receipt",
                    service="x",
                )
            retire_current_source_receipt(
                path,
                canonical_atomic_json_bytes(attempt),
            )
            self.log.info(
                "Removed main-post sending receipt disposition=%s "
                "lane=%s attempt_id=%s path=%s",
                sending_disposition,
                attempt["lane"],
                attempt["attempt_id"],
                path,
            )
        except FileNotFoundError as exc:
            raise self.AmbiguousRemotePostOutcome(
                "Main-post sending receipt disappeared before definite-failure retirement",
                service="x",
            ) from exc

    def finalize_pending(
        self,
        pending: dict,
    ) -> dict:
        """Atomically replace one pending schedule with its complete local receipt."""
        if not self.values().pending_is_valid(pending):
            raise RuntimeError("Refusing to finalise an invalid pending receipt")
        attempt = pending["source_attempt"]
        path = self.current().attempt_path(attempt)
        status, current = (
            self.current().load_regular()
            if path == self.REGULAR_POST_RECEIPT_FILE
            else self.current().load_meme()
        )
        if status != "pending_schedule" or current != pending:
            raise RuntimeError(
                "Confirmed pending-schedule receipt changed before finalisation"
            )
        if attempt["lane"] == "quote_image":
            receipt = self.values().materialize_regular(pending)
        else:
            receipt = self.values().materialize_meme(pending)
        if attempt["lane"] == "quote_image":
            self.current().write_regular(receipt)
        else:
            self.current().write_meme(receipt)
        self.log.warning(
            "Finalised confirmed pending-schedule receipt lane=%s post_id=%s path=%s",
            attempt["lane"],
            pending["post_id"],
            path,
        )
        return receipt

    def _write_main_post_receipt(
        self,
        receipt: dict,
        *,
        lane_name: str,
        expected_lane: str,
        receipt_path: Path,
        opposite_lane_name: str,
        opposite_receipt_path: Path,
        current_attempt_schema_versions: set[int],
        unresolved_receipt_error: type[Exception],
        unresolved_opposite_receipt_error: type[Exception],
    ) -> None:
        """Publish either lane using the current adapter's paths and authorities."""
        if self.remote_receipt_retirement_is_blocking():
            raise unresolved_receipt_error(
                f"Refusing {lane_name} receipt publication during source-receipt retirement"
            )
        if self.receipt_namespace_entry_exists(opposite_receipt_path):
            raise unresolved_opposite_receipt_error(
                f"Refusing {lane_name} post while unresolved {opposite_lane_name}-post "
                f"receipt exists: {opposite_receipt_path}"
            )
        if self.values().pending_is_valid(
            receipt,
            expected_lane=expected_lane,
        ):
            status, attempt = (
                self.current().load_regular()
                if expected_lane == "quote_image" else self.current().load_meme()
            )
            if status != "sending" or attempt != receipt["source_attempt"]:
                raise unresolved_receipt_error(
                    f"Refusing to promote a changed {lane_name} main-post attempt"
                )
            self.atomic_write_json(receipt_path, receipt, durable=True)
            self.log.warning(
                f"Wrote confirmed {lane_name} pending-schedule receipt post_id=%s path=%s",
                receipt.get("post_id"),
                receipt_path,
            )
            return
        if not (
            self.values().regular_is_valid(receipt)
            if expected_lane == "quote_image" else self.values().meme_is_valid(receipt)
        ):
            raise RuntimeError(
                f"Internal error: generated {lane_name}-post receipt failed semantic validation"
            )
        if self.receipt_namespace_entry_exists(receipt_path):
            status, current = (
                self.current().load_regular()
                if expected_lane == "quote_image" else self.current().load_meme()
            )
            if status == "pending_schedule" and current is not None:
                if (
                    self.values().materialize_regular(current)
                    if expected_lane == "quote_image" else self.values().materialize_meme(current)
                ) != receipt:
                    raise unresolved_receipt_error(
                        "Refusing a schedule result which does not match the durable "
                        f"confirmed {lane_name} plan"
                    )
                self.atomic_write_json(receipt_path, receipt, durable=True)
                self.log.warning(
                    f"Finalised {lane_name}-post pending schedule post_id=%s path=%s",
                    receipt.get("post_id"),
                    receipt_path,
                )
                return
            attempt = current
            if (
                status == "sending"
                and isinstance(attempt, dict)
                and attempt.get("schema_version") in current_attempt_schema_versions
            ):
                raise unresolved_receipt_error(
                    f"Current-schema {lane_name} attempts must be promoted through the "
                    "durable confirmed pending-schedule receipt"
                )
            if (
                status != "sending"
                or attempt is None
                or not self.confirmed_receipt_matches_main_attempt(receipt, attempt)
            ):
                raise unresolved_receipt_error(
                    f"Refusing to overwrite an unresolved {lane_name}-post receipt: "
                    f"{receipt_path}"
                )
            self.atomic_write_json(receipt_path, receipt, durable=True)
            self.log.warning(
                f"Promoted {lane_name}-post sending receipt to confirmed attempt_id=%s "
                "post_id=%s path=%s",
                receipt.get("attempt_id"),
                receipt.get("post_id"),
                receipt_path,
            )
            return
        try:
            self.durable_create_receipt_json(receipt_path, receipt)
        except FileExistsError as exc:
            raise unresolved_receipt_error(
                f"Refusing to overwrite a {lane_name}-post receipt namespace entry "
                "which appeared during publication"
            ) from exc
        self.log.warning(
            f"Wrote confirmed {lane_name}-post receipt pending local reconciliation: %s",
            receipt_path,
        )

    def write_regular(
        self,
        receipt: dict,
    ) -> None:
        """Write regular post receipt."""
        self._write_main_post_receipt(
            receipt,
            lane_name="regular",
            expected_lane="quote_image",
            receipt_path=self.REGULAR_POST_RECEIPT_FILE,
            opposite_lane_name="meme",
            opposite_receipt_path=self.MEME_POST_RECEIPT_FILE,
            current_attempt_schema_versions={4, 5, 6},
            unresolved_receipt_error=self.UnresolvedRegularPostReceipt,
            unresolved_opposite_receipt_error=self.UnresolvedMemePostReceipt,
        )

    def load_regular(
        self,
    ) -> tuple[str, dict | None]:
        """Load regular post receipt."""
        try:
            present, data = self.load_receipt_json_no_follow(self.REGULAR_POST_RECEIPT_FILE)
        except Exception:
            self.log.exception("Malformed or unsafe regular-post receipt blocks main posting until repaired: %s", self.REGULAR_POST_RECEIPT_FILE)
            return "invalid", None
        if not present:
            return "absent", None
        if isinstance(data, dict) and self.values().attempt_is_valid(data):
            if data.get("lane") == "quote_image":
                return "sending", data
            self.log.critical(
                "A meme attempt was stored in the regular-post receipt path: %s",
                self.REGULAR_POST_RECEIPT_FILE,
            )
            return "invalid", data
        if self.values().pending_is_valid(
            data,
            expected_lane="quote_image",
        ):
            return "pending_schedule", data
        if (
            not isinstance(data, dict)
            or type(data.get("schema_version")) is not int
            or data.get("schema_version") not in {1, 2, 3, 4}
        ):
            self.log.critical("Invalid regular-post receipt blocks main posting until repaired: %s", self.REGULAR_POST_RECEIPT_FILE)
            return "invalid", None
        required = ("post_id", "quote_hash", "image_basename", "quote_post_epoch", "next_quote_post_epoch")
        if not all(data.get(key) for key in required):
            self.log.critical("Incomplete regular-post receipt blocks main posting until repaired: %s", self.REGULAR_POST_RECEIPT_FILE)
            return "invalid", None
        if not self.values().regular_is_valid(data):
            self.log.critical(
                "Semantically invalid or unsupported-version regular-post receipt blocks main posting until repaired; "
                "do not continue an upgrade while a confirmed-post receipt exists: %s",
                self.REGULAR_POST_RECEIPT_FILE,
            )
            return "invalid", None
        return "valid", data

    def remove_regular(
        self,
        receipt: dict,
        *,
        retire_current_source_receipt: Callable[..., None],
    ) -> None:
        """Retire one exact reconciled regular-post receipt."""

        retire_current_source_receipt(
            self.REGULAR_POST_RECEIPT_FILE,
            canonical_atomic_json_bytes(receipt),
        )
        self.log.info("Removed reconciled regular-post receipt: %s", self.REGULAR_POST_RECEIPT_FILE)

    def write_meme(
        self,
        receipt: dict,
    ) -> None:
        """Write meme post receipt."""
        self._write_main_post_receipt(
            receipt,
            lane_name="meme",
            expected_lane="daily_meme",
            receipt_path=self.MEME_POST_RECEIPT_FILE,
            opposite_lane_name="regular",
            opposite_receipt_path=self.REGULAR_POST_RECEIPT_FILE,
            current_attempt_schema_versions={3, 4, 5},
            unresolved_receipt_error=self.UnresolvedMemePostReceipt,
            unresolved_opposite_receipt_error=self.UnresolvedRegularPostReceipt,
        )

    def load_meme(
        self,
    ) -> tuple[str, dict | None]:
        """Load meme post receipt."""
        try:
            present, data = self.load_receipt_json_no_follow(self.MEME_POST_RECEIPT_FILE)
        except Exception:
            self.log.exception("Malformed or unsafe meme-post receipt blocks the bot until repaired: %s", self.MEME_POST_RECEIPT_FILE)
            return "invalid", None
        if not present:
            return "absent", None
        if isinstance(data, dict) and self.values().attempt_is_valid(data):
            if data.get("lane") == "daily_meme":
                return "sending", data
            self.log.critical(
                "A regular-post attempt was stored in the meme receipt path: %s",
                self.MEME_POST_RECEIPT_FILE,
            )
            return "invalid", data
        if self.values().pending_is_valid(
            data,
            expected_lane="daily_meme",
        ):
            return "pending_schedule", data
        if (
            not isinstance(data, dict)
            or data.get("schema_version") not in {1, 2}
        ):
            self.log.critical("Invalid meme-post receipt blocks the bot until repaired: %s", self.MEME_POST_RECEIPT_FILE)
            return "invalid", None
        required = ("post_id", "meme_basename", "meme_post_epoch", "next_meme_post_epoch")
        if not all(data.get(key) for key in required):
            self.log.critical("Incomplete meme-post receipt blocks the bot until repaired: %s", self.MEME_POST_RECEIPT_FILE)
            return "invalid", None
        if not self.values().meme_is_valid(data):
            self.log.critical(
                "Semantically invalid or unsupported-version meme-post receipt blocks the bot until repaired; "
                "do not continue an upgrade while a confirmed-post receipt exists: %s",
                self.MEME_POST_RECEIPT_FILE,
            )
            return "invalid", None
        return "valid", data

    def remove_meme(
        self,
        receipt: dict,
        *,
        retire_current_source_receipt: Callable[..., None],
    ) -> None:
        """Retire one exact reconciled meme-post receipt."""

        retire_current_source_receipt(
            self.MEME_POST_RECEIPT_FILE,
            canonical_atomic_json_bytes(receipt),
        )
        self.log.info("Removed reconciled meme-post receipt: %s", self.MEME_POST_RECEIPT_FILE)
