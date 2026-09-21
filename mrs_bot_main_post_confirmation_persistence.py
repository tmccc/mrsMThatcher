"""Main-post confirmation promotion and protected persistence.

The root supplies current receipt/value owners and transport dependencies on
each call. Promotion calls those owners directly while exact source replacement
and persistence barriers remain explicit. Import performs no runtime work.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, TYPE_CHECKING

from mrs_bot_durable_json_io import canonical_atomic_json_bytes

if TYPE_CHECKING:
    from mrs_bot_main_post_receipt_storage import MainPostReceipts
    from mrs_bot_main_post_receipts import MainPostReceiptValues
    from mrs_bot_state_generation import StateCommitProof


def atomic_json_file_exactly_matches(
    path: Path,
    value: object,
) -> bool:
    """Compare a receipt with its expected canonical bytes without JSON parsing."""
    try:
        from mrs_bot_state_generation import StateCommitProof, directory_identity, file_identity
        import hashlib

        expected = canonical_atomic_json_bytes(value)
        proof = StateCommitProof(path, directory_identity(path.parent), file_identity(path),
                                 hashlib.sha256(expected).hexdigest(), 0, 1024 * 1024)
        proof.require_current()
        return True
    except Exception:
        return False


def promote_main_post_attempt_to_confirmed_pending_schedule(
    attempt: dict,
    *,
    post_id: str,
    confirmation_epoch: int,
    image_summary: str = '',
    receipts: MainPostReceipts,
    receipt_values: MainPostReceiptValues,
    AmbiguousRemotePostOutcome: Any,
    BoundSourceReceiptTransitionError: Any,
    ConfirmedPendingScheduleDurabilityUncertain: Any,
    REGULAR_POST_RECEIPT_FILE: Any,
    TRANSPORT_SOURCE_VALIDATOR_ID: Any,
    TransportJournalError: Any,
    _set_ambiguous_remote_post_seen: Any,
    atomic_json_file_exactly_matches: Any,
    bind_confirmed_transport_source: Any,
    fsync_parent_dir: Any,
    journal_path_for_receipt: Any,
    latch_confirmed_post_persistence_failure: Any,
    log: Any,
    remote_write_safety_incident_is_latched: Any,
    replace_bound_source_receipt: Any,
    transaction_mutation_authority: Any,
    transport_source_semantic_validator: Any,
) -> dict:
    """Atomically bind a confirmed remote identity before fallible local work."""
    pending = receipt_values.current().build_pending(
        attempt,
        post_id=post_id,
        confirmation_epoch=confirmation_epoch,
        image_summary=image_summary,
    )
    path = receipts.current().attempt_path(attempt)
    status, current = (
        receipts.current().load_regular()
        if path == REGULAR_POST_RECEIPT_FILE
        else receipts.current().load_meme()
    )
    if status != "sending" or current != attempt:
        raise AmbiguousRemotePostOutcome(
            "Main-post attempt changed before remote-confirmation promotion",
            service="x",
        )
    recovery = bind_confirmed_transport_source(
        journal_path=journal_path_for_receipt(path),
        receipt_path=path,
        validator_id=TRANSPORT_SOURCE_VALIDATOR_ID,
        validator=transport_source_semantic_validator,
    )
    if (
        recovery.details.lane != str(attempt["lane"])
        or recovery.details.post_id != str(post_id)
        or recovery.details.confirmation_epoch != int(confirmation_epoch)
        or recovery.source_binding.receipt_document != attempt
        or recovery.source_binding.receipt_bytes
        != canonical_atomic_json_bytes(attempt)
    ):
        raise TransportJournalError(
            "confirmed main-post transport/source lineage changed"
        )
    try:
        replace_bound_source_receipt(
            recovery.source_binding,
            canonical_atomic_json_bytes(pending),
            mutation_authority=transaction_mutation_authority(
                "confirmed main-post source receipt promotion"
            ),
        )
    except BaseException as write_error:
        # ``atomic_write_json`` replaces the receipt before synchronising its
        # parent directory.  A failure at that final boundary can therefore
        # leave the exact pending receipt visible even though the writer did
        # not return.  Latch first: every inspection and recovery operation
        # below is fallible, and no unrelated remote lane may proceed while
        # durability is uncertain.
        latch_was_already_set = remote_write_safety_incident_is_latched()
        _set_ambiguous_remote_post_seen(True)

        if isinstance(write_error, BoundSourceReceiptTransitionError):
            raise ConfirmedPendingScheduleDurabilityUncertain(
                "Confirmed main-post source receipt changed or its exact "
                "promotion did not complete; the transport journal remains "
                "a durable global barrier",
                durable_barrier=True,
            ) from write_error

        if atomic_json_file_exactly_matches(path, pending):
            try:
                fsync_parent_dir(path, strict=True)
                if not atomic_json_file_exactly_matches(path, pending):
                    raise RuntimeError(
                        "Pending-schedule receipt changed during durability recheck"
                    )
            except BaseException as durability_error:
                durable_barrier = latch_confirmed_post_persistence_failure(
                    lane=str(attempt["lane"]),
                    post_id=str(post_id),
                    failure_components=[
                        "pending_schedule_parent_fsync",
                        type(durability_error).__name__,
                    ],
                )
                raise ConfirmedPendingScheduleDurabilityUncertain(
                    "Confirmed main-post pending-schedule receipt is visible but "
                    "its parent-directory durability could not be re-established",
                    durable_barrier=durable_barrier,
                ) from write_error

            if not latch_was_already_set:
                _set_ambiguous_remote_post_seen(False)
            log.warning(
                "Re-established confirmed pending-schedule receipt durability "
                "after its initial parent-directory fsync failed lane=%s "
                "attempt_id=%s post_id=%s path=%s",
                attempt["lane"],
                attempt["attempt_id"],
                post_id,
                path,
            )
        elif atomic_json_file_exactly_matches(path, attempt):
            # The replace did not occur.  The previously durable sending
            # attempt remains the restart-safe barrier, so the caller's
            # existing confirmed-state fallback may proceed.
            if not latch_was_already_set:
                _set_ambiguous_remote_post_seen(False)
            raise
        else:
            durable_barrier = latch_confirmed_post_persistence_failure(
                lane=str(attempt["lane"]),
                post_id=str(post_id),
                failure_components=[
                    "pending_schedule_receipt_identity",
                    type(write_error).__name__,
                ],
            )
            raise ConfirmedPendingScheduleDurabilityUncertain(
                "Confirmed main-post receipt identity changed or could not be "
                "verified after pending-schedule promotion failed",
                durable_barrier=durable_barrier,
            ) from write_error
    log.warning(
        "Promoted main-post attempt to confirmed pending-schedule receipt "
        "lane=%s attempt_id=%s post_id=%s path=%s",
        attempt["lane"],
        attempt["attempt_id"],
        post_id,
        path,
    )
    return pending


def save_regular_post_protected_state(
    lines_used: set,
    images_used: set,
    state: dict,
    *,
    durable: bool,
    IMAGES_USED_FILE: Any,
    LINES_USED_FILE: Any,
    save_image_used_basenames: Any,
    save_quote_used_hashes: Any,
    save_state: Any,
) -> StateCommitProof:
    """Save regular post protected state."""
    from mrs_bot_state_generation import canonical_bytes, protect_history_files

    save_quote_used_hashes(LINES_USED_FILE, lines_used, durable=durable)
    save_image_used_basenames(IMAGES_USED_FILE, {str(item) for item in images_used}, durable=durable)
    proof = save_state(state, durable=durable)
    return protect_history_files(proof, (
        (LINES_USED_FILE, canonical_bytes(sorted({str(item) for item in lines_used})) + b'\n'),
        (IMAGES_USED_FILE, canonical_bytes(sorted({str(item) for item in images_used})) + b'\n'),
    ))


@dataclass(frozen=True)
class RegularPostPersistenceResult:
    """Emergency component failures and the exact composite restart authority."""

    failures: tuple[str, ...]
    commit_proof: StateCommitProof | None


def emergency_persist_confirmed_regular_post(
    lines_used: set,
    images_used: set,
    state: dict,
    *,
    IMAGES_USED_FILE: Any,
    LINES_USED_FILE: Any,
    STATE_FILE: Any,
    StateBackupWriteError: Any,
    json_file_matches: Any,
    log: Any,
    save_image_used_basenames: Any,
    save_quote_used_hashes: Any,
    save_state: Any,
) -> RegularPostPersistenceResult:
    """Persist emergency confirmed-post effects and return exact restart authority."""
    from mrs_bot_state_generation import canonical_bytes, protect_history_files

    failures: list[str] = []
    proof = None
    for name, func in (
        ("quote_history", lambda: save_quote_used_hashes(LINES_USED_FILE, lines_used, durable=True)),
        ("image_history", lambda: save_image_used_basenames(IMAGES_USED_FILE, {str(item) for item in images_used}, durable=True)),
        ("state", lambda: save_state(state, durable=True)),
    ):
        try:
            result = func()
            if name == "state":
                proof = result
        except Exception as exc:
            if (
                name == "state"
                and isinstance(exc, StateBackupWriteError)
                and getattr(exc, 'commit_proof', None) is not None
                and json_file_matches(STATE_FILE, state, commit_proof=exc.commit_proof)
            ):
                proof = exc.commit_proof
                log.warning(
                    "Emergency canonical state was committed after confirmed regular "
                    "post, but a later backup/finalisation step failed; treating the "
                    "canonical durable state as the recovery representation",
                    exc_info=True,
                )
                continue
            failures.append(name)
            log.critical("Emergency persistence component failed after confirmed regular post: %s", name, exc_info=True)
    if not failures:
        try:
            proof = protect_history_files(proof, (
                (LINES_USED_FILE, canonical_bytes(sorted({str(item) for item in lines_used})) + b'\n'),
                (IMAGES_USED_FILE, canonical_bytes(sorted({str(item) for item in images_used})) + b'\n'),
            ))
        except Exception:
            failures.append("protected_commit_proof")
            proof = None
            log.critical("Emergency protected state proof failed", exc_info=True)
    return RegularPostPersistenceResult(tuple(failures), proof if not failures else None)
