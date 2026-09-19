"""Store main-post receipts and preserve their durable lifecycle transitions.

Eleven explicit root adapters supply current paths, callbacks, modules, logging
and exception authorities on each call. One shared publication lifecycle keeps
lane-specific gates, sending promotion and pending schedule finalization explicit;
readers retain their distinct contracts. Exact retirement, object references and
native error boundaries are preserved. Sibling calls use
current root callbacks. Source authority, atomic I/O, journals, validators,
materializers, builders, confirmation promotion, recovery and transport remain
in their existing locations. This owner retains no runtime dependencies or state
and performs no import-time runtime work or reverse bot import.
"""

from __future__ import annotations

from collections.abc import Callable
from logging import Logger
from pathlib import Path


def main_post_attempt_path(
    attempt: dict,
    *,
    MEME_POST_RECEIPT_FILE: Path,
    REGULAR_POST_RECEIPT_FILE: Path,
) -> Path:
    """Return the receipt path which owns one main-post attempt."""
    lane = str(attempt.get("lane") or "")
    if lane == "quote_image":
        return REGULAR_POST_RECEIPT_FILE
    if lane == "daily_meme":
        return MEME_POST_RECEIPT_FILE
    raise ValueError(f"Unsupported main-post attempt lane: {lane}")


def write_main_post_attempt(
    attempt: dict,
    *,
    CONFIRMED_REPLY_RECEIPT_FILE: Path,
    InvalidConfirmedReplyReceipt: type[Exception],
    MEME_POST_RECEIPT_FILE: Path,
    REGULAR_POST_RECEIPT_FILE: Path,
    UnresolvedRegularPostReceipt: type[Exception],
    current_main_post_attempt_is_semantically_valid: Callable[..., bool],
    durable_create_receipt_json: Callable[..., None],
    log: Logger,
    main_post_attempt_path: Callable[..., Path],
    receipt_namespace_entry_exists: Callable[..., bool],
    remote_receipt_retirement_is_blocking: Callable[..., bool],
) -> None:
    """Durably record a main-post transaction before its X create request."""
    if (
        not current_main_post_attempt_is_semantically_valid(attempt)
        or attempt.get("lifecycle_state") != "sending"
    ):
        raise RuntimeError(
            "Only a current-schema sending main-post attempt may enter the "
            "live write path"
        )
    if remote_receipt_retirement_is_blocking():
        raise UnresolvedRegularPostReceipt(
            "Refusing a main-post attempt while source-receipt retirement is incomplete"
        )
    if receipt_namespace_entry_exists(
        REGULAR_POST_RECEIPT_FILE
    ) or receipt_namespace_entry_exists(MEME_POST_RECEIPT_FILE):
        raise UnresolvedRegularPostReceipt(
            "Refusing to overwrite an unresolved regular or meme transaction"
        )
    if receipt_namespace_entry_exists(CONFIRMED_REPLY_RECEIPT_FILE):
        raise InvalidConfirmedReplyReceipt(
            "Refusing a main-post attempt while a conversational-reply receipt exists"
        )
    path = main_post_attempt_path(attempt)
    try:
        durable_create_receipt_json(path, attempt)
    except FileExistsError as exc:
        raise UnresolvedRegularPostReceipt(
            "Refusing to overwrite a receipt namespace entry which appeared "
            f"during main-post publication: {path}"
        ) from exc
    log.warning(
        "Wrote main-post sending receipt lane=%s attempt_id=%s path=%s",
        attempt["lane"],
        attempt["attempt_id"],
        path,
    )


def mark_main_post_attempt_attempting(
    attempt: dict,
    *,
    AmbiguousRemotePostOutcome: type[Exception],
    REGULAR_POST_RECEIPT_FILE: Path,
    canonical_atomic_json_bytes: Callable[..., bytes],
    current_main_post_attempt_is_semantically_valid: Callable[..., bool],
    load_meme_post_receipt: Callable[..., tuple[str, dict | None]],
    load_regular_post_receipt: Callable[..., tuple[str, dict | None]],
    log: Logger,
    main_post_attempt_path: Callable[..., Path],
    replace_exact_source_receipt_document: Callable[..., None],
    transaction_mutation_authority: Callable[..., object],
) -> dict:
    """Atomically consume one sending authorisation before remote transmission."""
    if (
        not current_main_post_attempt_is_semantically_valid(attempt)
        or attempt.get("lifecycle_state") != "sending"
    ):
        raise AmbiguousRemotePostOutcome(
            "Only a current-schema main-post attempt may transmit once from "
            "sending state",
            service="x",
        )
    path = main_post_attempt_path(attempt)
    status, current = (
        load_regular_post_receipt()
        if path == REGULAR_POST_RECEIPT_FILE
        else load_meme_post_receipt()
    )
    if status != "sending" or current != attempt:
        raise AmbiguousRemotePostOutcome(
            "Main-post sending receipt changed before transmission",
            service="x",
        )
    attempting = {**attempt, "lifecycle_state": "attempting"}
    if not current_main_post_attempt_is_semantically_valid(attempting):
        raise RuntimeError("Attempting main-post receipt failed validation")
    replace_exact_source_receipt_document(
        path,
        expected_bytes=canonical_atomic_json_bytes(attempt),
        replacement_bytes=canonical_atomic_json_bytes(attempting),
        mutation_authority=transaction_mutation_authority(
            "main-post sending-to-attempting receipt promotion"
        ),
    )
    log.warning(
        "Promoted main-post receipt to attempting lane=%s attempt_id=%s path=%s",
        attempting["lane"],
        attempting["attempt_id"],
        path,
    )
    return attempting


def remove_main_post_attempt(
    attempt: dict,
    *,
    sending_disposition: str,
    AmbiguousRemotePostOutcome: type[Exception],
    canonical_atomic_json_bytes: Callable[..., bytes],
    current_main_post_attempt_is_semantically_valid: Callable[..., bool],
    load_receipt_json_no_follow: Callable[[Path], tuple[bool, object | None]],
    log: Logger,
    main_post_attempt_path: Callable[..., Path],
    retire_current_source_receipt: Callable[..., None],
) -> None:
    """Retire an exact sending attempt after one proved-safe disposition."""
    if sending_disposition not in {
        "definite_non_success",
        "confirmed_state_fallback",
    }:
        raise ValueError("A main-post sending receipt requires an explicit disposition")
    if not current_main_post_attempt_is_semantically_valid(attempt):
        raise AmbiguousRemotePostOutcome(
            "Refusing to mutate a legacy or invalid main-post attempt",
            service="x",
        )
    path = main_post_attempt_path(attempt)
    try:
        present, current = load_receipt_json_no_follow(path)
        if not present:
            raise FileNotFoundError(path)
        if (
            current != attempt
            or not current_main_post_attempt_is_semantically_valid(current)
        ):
            raise AmbiguousRemotePostOutcome(
                "Refusing to remove a changed main-post sending receipt",
                service="x",
            )
        retire_current_source_receipt(
            path,
            canonical_atomic_json_bytes(attempt),
        )
        log.info(
            "Removed main-post sending receipt disposition=%s "
            "lane=%s attempt_id=%s path=%s",
            sending_disposition,
            attempt["lane"],
            attempt["attempt_id"],
            path,
        )
    except FileNotFoundError as exc:
        raise AmbiguousRemotePostOutcome(
            "Main-post sending receipt disappeared before definite-failure retirement",
            service="x",
        ) from exc


def finalize_confirmed_pending_schedule_receipt(
    pending: dict,
    *,
    REGULAR_POST_RECEIPT_FILE: Path,
    confirmed_pending_schedule_receipt_is_semantically_valid: Callable[..., bool],
    load_meme_post_receipt: Callable[..., tuple[str, dict | None]],
    load_regular_post_receipt: Callable[..., tuple[str, dict | None]],
    log: Logger,
    main_post_attempt_path: Callable[..., Path],
    materialize_bound_meme_schedule_receipt: Callable[..., dict],
    materialize_bound_regular_schedule_receipt: Callable[..., dict],
    write_meme_post_receipt: Callable[..., None],
    write_regular_post_receipt: Callable[..., None],
) -> dict:
    """Atomically replace one pending schedule with its complete local receipt."""
    if not confirmed_pending_schedule_receipt_is_semantically_valid(pending):
        raise RuntimeError("Refusing to finalise an invalid pending receipt")
    attempt = pending["source_attempt"]
    path = main_post_attempt_path(attempt)
    status, current = (
        load_regular_post_receipt()
        if path == REGULAR_POST_RECEIPT_FILE
        else load_meme_post_receipt()
    )
    if status != "pending_schedule" or current != pending:
        raise RuntimeError(
            "Confirmed pending-schedule receipt changed before finalisation"
        )
    if attempt["lane"] == "quote_image":
        receipt = materialize_bound_regular_schedule_receipt(pending)
    else:
        receipt = materialize_bound_meme_schedule_receipt(pending)
    if attempt["lane"] == "quote_image":
        write_regular_post_receipt(receipt)
    else:
        write_meme_post_receipt(receipt)
    log.warning(
        "Finalised confirmed pending-schedule receipt lane=%s post_id=%s path=%s",
        attempt["lane"],
        pending["post_id"],
        path,
    )
    return receipt


def _write_main_post_receipt(
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
    atomic_write_json: Callable[..., None],
    confirmed_pending_schedule_receipt_is_semantically_valid: Callable[..., bool],
    confirmed_receipt_matches_main_attempt: Callable[..., bool],
    durable_create_receipt_json: Callable[..., None],
    load_post_receipt: Callable[..., tuple[str, dict | None]],
    log: Logger,
    materialize_bound_schedule_receipt: Callable[..., dict],
    post_receipt_is_semantically_valid: Callable[..., bool],
    receipt_namespace_entry_exists: Callable[..., bool],
    remote_receipt_retirement_is_blocking: Callable[..., bool],
) -> None:
    """Publish either lane using the current adapter's paths and authorities."""
    if remote_receipt_retirement_is_blocking():
        raise unresolved_receipt_error(
            f"Refusing {lane_name} receipt publication during source-receipt retirement"
        )
    if receipt_namespace_entry_exists(opposite_receipt_path):
        raise unresolved_opposite_receipt_error(
            f"Refusing {lane_name} post while unresolved {opposite_lane_name}-post "
            f"receipt exists: {opposite_receipt_path}"
        )
    if confirmed_pending_schedule_receipt_is_semantically_valid(
        receipt,
        expected_lane=expected_lane,
    ):
        status, attempt = load_post_receipt()
        if status != "sending" or attempt != receipt["source_attempt"]:
            raise unresolved_receipt_error(
                f"Refusing to promote a changed {lane_name} main-post attempt"
            )
        atomic_write_json(receipt_path, receipt, durable=True)
        log.warning(
            f"Wrote confirmed {lane_name} pending-schedule receipt post_id=%s path=%s",
            receipt.get("post_id"),
            receipt_path,
        )
        return
    if not post_receipt_is_semantically_valid(receipt):
        raise RuntimeError(
            f"Internal error: generated {lane_name}-post receipt failed semantic validation"
        )
    if receipt_namespace_entry_exists(receipt_path):
        status, current = load_post_receipt()
        if status == "pending_schedule" and current is not None:
            if materialize_bound_schedule_receipt(current) != receipt:
                raise unresolved_receipt_error(
                    "Refusing a schedule result which does not match the durable "
                    f"confirmed {lane_name} plan"
                )
            atomic_write_json(receipt_path, receipt, durable=True)
            log.warning(
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
            or not confirmed_receipt_matches_main_attempt(receipt, attempt)
        ):
            raise unresolved_receipt_error(
                f"Refusing to overwrite an unresolved {lane_name}-post receipt: "
                f"{receipt_path}"
            )
        atomic_write_json(receipt_path, receipt, durable=True)
        log.warning(
            f"Promoted {lane_name}-post sending receipt to confirmed attempt_id=%s "
            "post_id=%s path=%s",
            receipt.get("attempt_id"),
            receipt.get("post_id"),
            receipt_path,
        )
        return
    try:
        durable_create_receipt_json(receipt_path, receipt)
    except FileExistsError as exc:
        raise unresolved_receipt_error(
            f"Refusing to overwrite a {lane_name}-post receipt namespace entry "
            "which appeared during publication"
        ) from exc
    log.warning(
        f"Wrote confirmed {lane_name}-post receipt pending local reconciliation: %s",
        receipt_path,
    )


def write_regular_post_receipt(
    receipt: dict,
    *,
    MEME_POST_RECEIPT_FILE: Path,
    REGULAR_POST_RECEIPT_FILE: Path,
    UnresolvedMemePostReceipt: type[Exception],
    UnresolvedRegularPostReceipt: type[Exception],
    atomic_write_json: Callable[..., None],
    confirmed_pending_schedule_receipt_is_semantically_valid: Callable[..., bool],
    confirmed_receipt_matches_main_attempt: Callable[..., bool],
    durable_create_receipt_json: Callable[..., None],
    load_regular_post_receipt: Callable[..., tuple[str, dict | None]],
    log: Logger,
    materialize_bound_regular_schedule_receipt: Callable[..., dict],
    receipt_namespace_entry_exists: Callable[..., bool],
    regular_post_receipt_is_semantically_valid: Callable[..., bool],
    remote_receipt_retirement_is_blocking: Callable[..., bool],
) -> None:
    """Write regular post receipt."""
    _write_main_post_receipt(
        receipt,
        lane_name="regular",
        expected_lane="quote_image",
        receipt_path=REGULAR_POST_RECEIPT_FILE,
        opposite_lane_name="meme",
        opposite_receipt_path=MEME_POST_RECEIPT_FILE,
        current_attempt_schema_versions={4, 5, 6},
        unresolved_receipt_error=UnresolvedRegularPostReceipt,
        unresolved_opposite_receipt_error=UnresolvedMemePostReceipt,
        atomic_write_json=atomic_write_json,
        confirmed_pending_schedule_receipt_is_semantically_valid=confirmed_pending_schedule_receipt_is_semantically_valid,
        confirmed_receipt_matches_main_attempt=confirmed_receipt_matches_main_attempt,
        durable_create_receipt_json=durable_create_receipt_json,
        load_post_receipt=load_regular_post_receipt,
        log=log,
        materialize_bound_schedule_receipt=materialize_bound_regular_schedule_receipt,
        post_receipt_is_semantically_valid=regular_post_receipt_is_semantically_valid,
        receipt_namespace_entry_exists=receipt_namespace_entry_exists,
        remote_receipt_retirement_is_blocking=remote_receipt_retirement_is_blocking,
    )


def load_regular_post_receipt(
    *,
    REGULAR_POST_RECEIPT_FILE: Path,
    confirmed_pending_schedule_receipt_is_semantically_valid: Callable[..., bool],
    load_receipt_json_no_follow: Callable[..., tuple[bool, object]],
    log: Logger,
    main_post_attempt_is_semantically_valid: Callable[..., bool],
    regular_post_receipt_is_semantically_valid: Callable[..., bool],
) -> tuple[str, dict | None]:
    """Load regular post receipt."""
    try:
        present, data = load_receipt_json_no_follow(REGULAR_POST_RECEIPT_FILE)
    except Exception:
        log.exception("Malformed or unsafe regular-post receipt blocks main posting until repaired: %s", REGULAR_POST_RECEIPT_FILE)
        return "invalid", None
    if not present:
        return "absent", None
    if isinstance(data, dict) and main_post_attempt_is_semantically_valid(data):
        if data.get("lane") == "quote_image":
            return "sending", data
        log.critical(
            "A meme attempt was stored in the regular-post receipt path: %s",
            REGULAR_POST_RECEIPT_FILE,
        )
        return "invalid", data
    if confirmed_pending_schedule_receipt_is_semantically_valid(
        data,
        expected_lane="quote_image",
    ):
        return "pending_schedule", data
    if (
        not isinstance(data, dict)
        or type(data.get("schema_version")) is not int
        or data.get("schema_version") not in {1, 2, 3, 4}
    ):
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


def remove_regular_post_receipt(
    receipt: dict,
    *,
    REGULAR_POST_RECEIPT_FILE: Path,
    canonical_atomic_json_bytes: Callable[..., bytes],
    log: Logger,
    retire_current_source_receipt: Callable[..., None],
) -> None:
    """Retire one exact reconciled regular-post receipt."""

    retire_current_source_receipt(
        REGULAR_POST_RECEIPT_FILE,
        canonical_atomic_json_bytes(receipt),
    )
    log.info("Removed reconciled regular-post receipt: %s", REGULAR_POST_RECEIPT_FILE)


def write_meme_post_receipt(
    receipt: dict,
    *,
    MEME_POST_RECEIPT_FILE: Path,
    REGULAR_POST_RECEIPT_FILE: Path,
    UnresolvedMemePostReceipt: type[Exception],
    UnresolvedRegularPostReceipt: type[Exception],
    atomic_write_json: Callable[..., None],
    confirmed_pending_schedule_receipt_is_semantically_valid: Callable[..., bool],
    confirmed_receipt_matches_main_attempt: Callable[..., bool],
    durable_create_receipt_json: Callable[..., None],
    load_meme_post_receipt: Callable[..., tuple[str, dict | None]],
    log: Logger,
    materialize_bound_meme_schedule_receipt: Callable[..., dict],
    meme_post_receipt_is_semantically_valid: Callable[..., bool],
    receipt_namespace_entry_exists: Callable[..., bool],
    remote_receipt_retirement_is_blocking: Callable[..., bool],
) -> None:
    """Write meme post receipt."""
    _write_main_post_receipt(
        receipt,
        lane_name="meme",
        expected_lane="daily_meme",
        receipt_path=MEME_POST_RECEIPT_FILE,
        opposite_lane_name="regular",
        opposite_receipt_path=REGULAR_POST_RECEIPT_FILE,
        current_attempt_schema_versions={3, 4, 5},
        unresolved_receipt_error=UnresolvedMemePostReceipt,
        unresolved_opposite_receipt_error=UnresolvedRegularPostReceipt,
        atomic_write_json=atomic_write_json,
        confirmed_pending_schedule_receipt_is_semantically_valid=confirmed_pending_schedule_receipt_is_semantically_valid,
        confirmed_receipt_matches_main_attempt=confirmed_receipt_matches_main_attempt,
        durable_create_receipt_json=durable_create_receipt_json,
        load_post_receipt=load_meme_post_receipt,
        log=log,
        materialize_bound_schedule_receipt=materialize_bound_meme_schedule_receipt,
        post_receipt_is_semantically_valid=meme_post_receipt_is_semantically_valid,
        receipt_namespace_entry_exists=receipt_namespace_entry_exists,
        remote_receipt_retirement_is_blocking=remote_receipt_retirement_is_blocking,
    )


def load_meme_post_receipt(
    *,
    MEME_POST_RECEIPT_FILE: Path,
    confirmed_pending_schedule_receipt_is_semantically_valid: Callable[..., bool],
    load_receipt_json_no_follow: Callable[..., tuple[bool, object]],
    log: Logger,
    main_post_attempt_is_semantically_valid: Callable[..., bool],
    meme_post_receipt_is_semantically_valid: Callable[..., bool],
) -> tuple[str, dict | None]:
    """Load meme post receipt."""
    try:
        present, data = load_receipt_json_no_follow(MEME_POST_RECEIPT_FILE)
    except Exception:
        log.exception("Malformed or unsafe meme-post receipt blocks the bot until repaired: %s", MEME_POST_RECEIPT_FILE)
        return "invalid", None
    if not present:
        return "absent", None
    if isinstance(data, dict) and main_post_attempt_is_semantically_valid(data):
        if data.get("lane") == "daily_meme":
            return "sending", data
        log.critical(
            "A regular-post attempt was stored in the meme receipt path: %s",
            MEME_POST_RECEIPT_FILE,
        )
        return "invalid", data
    if confirmed_pending_schedule_receipt_is_semantically_valid(
        data,
        expected_lane="daily_meme",
    ):
        return "pending_schedule", data
    if (
        not isinstance(data, dict)
        or data.get("schema_version") not in {1, 2}
    ):
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


def remove_meme_post_receipt(
    receipt: dict,
    *,
    MEME_POST_RECEIPT_FILE: Path,
    canonical_atomic_json_bytes: Callable[..., bytes],
    log: Logger,
    retire_current_source_receipt: Callable[..., None],
) -> None:
    """Retire one exact reconciled meme-post receipt."""

    retire_current_source_receipt(
        MEME_POST_RECEIPT_FILE,
        canonical_atomic_json_bytes(receipt),
    )
    log.info("Removed reconciled meme-post receipt: %s", MEME_POST_RECEIPT_FILE)
