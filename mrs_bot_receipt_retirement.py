"""Receipt retirement and exact transport source verification.

The root supplies current runtime dependencies explicitly on each call. This
module owns fixed source hashing, digest grammar and path construction. It
performs no runtime work at import and retains no runtime authority.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any

from mrs_bot_durable_json_io import canonical_atomic_json_bytes


def confirmed_context_outbox_matches_receipt(
    context_reply: dict,
    receipt: dict,
) -> bool:
    """Match one confirmed outbox outcome to the receipt's exact lineage.

    Legacy confirmed receipts and legacy terminal outbox rows both predate the
    source-receipt hash.  They may be paired only with each other.  A current
    lineage-bearing receipt must never be reconciled through a legacy terminal
    row, because parent/quote/reply identity alone cannot distinguish a stale
    or substituted receipt generation.
    """

    if (
        not isinstance(context_reply, dict)
        or not isinstance(receipt, dict)
        or context_reply.get("state") != "context_reply_confirmed"
        or context_reply.get("quote_id") != receipt.get("quote_id")
        or context_reply.get("reply_post_id") != receipt.get("reply_post_id")
    ):
        return False
    outbox_source_fields = {
        "source_receipt_sha256",
        "source_receipt_attempt_number",
    }
    outbox_has_source = outbox_source_fields.issubset(context_reply)
    receipt_has_source = "source_receipt_sha256" in receipt
    if outbox_has_source != receipt_has_source:
        return False
    if not receipt_has_source:
        # The oldest accepted confirmed receipt predates attempt ordinals as
        # well as source hashes.  Preserve that deliberately weaker legacy
        # replay, while requiring exact ordinals for the later legacy shape
        # which does carry one.
        if "attempt_number" not in receipt:
            return True
        return context_reply.get("attempt_count") == receipt.get("attempt_number")
    return bool(
        context_reply.get("source_receipt_sha256")
        == receipt.get("source_receipt_sha256")
        and context_reply.get("source_receipt_attempt_number")
        == receipt.get("attempt_number")
    )


def require_historical_context_retirement_outbox_authority(
    *,
    ExactReceiptRetirementError: Any,
    HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE: Any,
    historical_context_outbox_store: Any,
    historical_context_reply_store: Any,
    inspect_exact_receipt_retirement: Any,
    inspect_interrupted_receipt_retirement: Any,
    journal_path_for_receipt: Any,
    now_epoch: Any,
    retirement_auxiliary_barrier_exists: Any,
    transport_journal_is_blocking: Any,
) -> None:
    """Bind an interrupted context retirement to its durable outbox outcome.

    A confirmed reply or a proved remote non-success is written to history and
    outbox before exact source retirement starts.  No retirement namespace may
    be resumed until its marker hash matches exactly one such durable outcome.
    """

    if not retirement_auxiliary_barrier_exists(
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
    ):
        return
    from historical_context_formatter import (
        HistoricalContextReplyStore,
        canonical_json_bytes,
    )

    context_store = historical_context_reply_store()
    retirement = inspect_interrupted_receipt_retirement(
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
    )
    if not retirement.valid or not re.fullmatch(
        r"[0-9a-f]{64}", retirement.expected_sha256
    ):
        raise ExactReceiptRetirementError(
            "historical-context retirement has no valid marker-bound source"
        )

    authorities: list[tuple[str, dict]] = []
    for item in context_store.history()["items"].values():
        if not isinstance(item, dict):
            continue
        if item.get("status") == "completed":
            receipt = {
                key: value for key, value in item.items() if key != "status"
            }
            receipt_bytes = canonical_json_bytes(receipt)
            inspection = inspect_exact_receipt_retirement(
                HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
                receipt_bytes,
            )
            if inspection.valid:
                authorities.append(("completed", receipt))
        elif (
            item.get("status") == "failed"
            and item.get("remote_outcome") == "proved_non_success"
            and item.get("source_receipt_sha256")
            == retirement.expected_sha256
            and item.get("source_receipt_attempt_number")
            == item.get("attempt_count")
        ):
            authorities.append(("failed", item))
    if len(authorities) != 1:
        raise ExactReceiptRetirementError(
            "historical-context retirement has no unique terminal-history "
            "authority"
        )
    authority_kind, receipt = authorities[0]
    parent_id = str(receipt["parent_post_id"])
    outbox_store = historical_context_outbox_store()
    obligation = outbox_store.get(parent_id)
    context = (
        obligation.get("context_reply")
        if isinstance(obligation, dict)
        else None
    )
    if not isinstance(obligation, dict):
        raise ExactReceiptRetirementError(
            "historical-context retirement has no matching outbox obligation"
        )
    if (
        not isinstance(context, dict)
        or context.get("quote_id") != receipt["quote_id"]
    ):
        raise ExactReceiptRetirementError(
            "historical-context retirement conflicts with its outbox identity"
        )
    if authority_kind == "failed":
        failure = context.get("failure")
        if (
            context.get("state")
            not in {
                "context_reply_failed_retryable",
                "context_reply_failed_terminal",
            }
            or not isinstance(failure, dict)
            or failure.get("remote_outcome") != "proved_non_success"
            or failure.get("source_receipt_sha256")
            != retirement.expected_sha256
            or failure.get("source_receipt_attempt_number")
            != receipt.get("source_receipt_attempt_number")
            or context.get("attempt_count") != failure.get("attempt_number")
            or transport_journal_is_blocking(
                journal_path_for_receipt(
                    HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
                )
            )
        ):
            raise ExactReceiptRetirementError(
                "historical-context retirement conflicts with its exact "
                "proved-failure outbox authority"
            )
        return

    if context.get("state") == "context_reply_attempting":
        source_sha256 = receipt.get("source_receipt_sha256")
        source_attempt = receipt.get("attempt_number")
        if source_sha256 is not None:
            if (
                context.get("remote_transaction_started") is not True
                or context.get("source_receipt_sha256") != source_sha256
                or context.get("source_receipt_attempt_number")
                != source_attempt
            ):
                raise ExactReceiptRetirementError(
                    "historical-context retirement conflicts with its exact "
                    "attempting outbox source"
                )
        elif {
            "source_receipt_sha256",
            "source_receipt_attempt_number",
        } & set(context):
            raise ExactReceiptRetirementError(
                "legacy historical-context retirement conflicts with a "
                "source-bound attempting outbox"
            )
        elif (
            "attempt_number" in receipt
            and context.get("attempt_count") != receipt.get("attempt_number")
        ):
            raise ExactReceiptRetirementError(
                "legacy historical-context retirement conflicts with its "
                "outbox attempt"
            )
        outbox_store.record_confirmed(
            parent_id,
            attempt_number=int(context["attempt_count"]),
            reply_post_id=receipt["reply_post_id"],
            # Legacy confirmed_at values were not canonical timestamps.  The
            # outbox records the local recovery observation and clamps it to
            # its already durable timeline, just like the ordinary
            # already_completed path.
            confirmed_epoch=now_epoch(),
        )
    elif not confirmed_context_outbox_matches_receipt(context, receipt):
        raise ExactReceiptRetirementError(
            "historical-context retirement conflicts with its durable outbox "
            "outcome"
        )


def resume_interrupted_source_receipt_retirement_if_present(
    *,
    CONFIRMED_REPLY_RECEIPT_FILE: Any,
    ExactReceiptRetirementError: Any,
    HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE: Any,
    MEME_POST_RECEIPT_FILE: Any,
    REGULAR_POST_RECEIPT_FILE: Any,
    historical_context_reply_store: Any,
    inspect_transport_state: Any,
    inspect_interrupted_receipt_retirement: Any,
    journal_path_for_receipt: Any,
    load_confirmed_reply_receipt: Any,
    load_meme_post_receipt: Any,
    load_regular_post_receipt: Any,
    log: Any,
    remote_write_transport_journal_paths: Any,
    require_historical_context_retirement_outbox_authority: Any,
    resume_interrupted_receipt_retirement: Any,
    retire_lane_transport_journal_if_present: Any,
    retirement_auxiliary_barrier_exists: Any,
    transaction_mutation_authority: Any,
    recover_state_receipt_commit_proof: Any,
    state_commit_mutation_authority: Any,
    transport_journal_is_blocking: Any,
) -> bool:
    """Finish one journal-free source retirement under the process lock.

    A matching transport journal must retain the source receipt until its own
    retirement has completed.  Multiple lane auxiliaries are never selected
    automatically, and every namespace inspection includes broken symlinks.
    """

    receipt_paths = (
        REGULAR_POST_RECEIPT_FILE,
        MEME_POST_RECEIPT_FILE,
        CONFIRMED_REPLY_RECEIPT_FILE,
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
    )
    active = [
        path
        for path in receipt_paths
        if retirement_auxiliary_barrier_exists(path)
    ]
    if not active:
        return False
    if len(active) != 1:
        raise ExactReceiptRetirementError(
            "multiple source-receipt retirement lanes require manual inspection"
        )
    source_path = active[0]
    retirement = (
        None if source_path == HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
        else inspect_interrupted_receipt_retirement(source_path)
    )
    if retirement is not None and not retirement.valid:
        raise ExactReceiptRetirementError(retirement.detail)
    non_success = bool(
        retirement is not None and retirement.disposition == "definite_non_success"
    )
    commit_proof = (
        None if source_path == HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE or non_success
        else recover_state_receipt_commit_proof(source_path)
    )
    if source_path == HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE:
        require_historical_context_retirement_outbox_authority()
    blocking_journals = [
        path
        for path in remote_write_transport_journal_paths()
        if transport_journal_is_blocking(path)
    ]
    if blocking_journals:
        if non_success:
            raise ExactReceiptRetirementError(
                "non-success source retirement overlaps a transport journal"
            )
        owning_journal = journal_path_for_receipt(source_path)
        if len(blocking_journals) != 1 or (
            blocking_journals[0].absolute() != owning_journal.absolute()
        ):
            raise ExactReceiptRetirementError(
                "source retirement overlaps an unrelated transport journal"
            )
        journal_state = inspect_transport_state(owning_journal)
        if journal_state.journal is None:
            raise ExactReceiptRetirementError(
                "source retirement cannot identify an incomplete owning journal"
            )
        journal_document = journal_state.journal.document
        lane = str(journal_document["lane"])
        post_id = str(journal_document.get("remote_post_id") or "")
        if not post_id:
            raise ExactReceiptRetirementError(
                "source retirement owning journal is not confirmed"
            )
        if source_path == REGULAR_POST_RECEIPT_FILE:
            _status, receipt = load_regular_post_receipt()
            receipt_bytes = (
                canonical_atomic_json_bytes(receipt)
                if isinstance(receipt, dict)
                else b""
            )
        elif source_path == MEME_POST_RECEIPT_FILE:
            _status, receipt = load_meme_post_receipt()
            receipt_bytes = (
                canonical_atomic_json_bytes(receipt)
                if isinstance(receipt, dict)
                else b""
            )
        elif source_path == CONFIRMED_REPLY_RECEIPT_FILE:
            _status, receipt = load_confirmed_reply_receipt()
            receipt_bytes = (
                canonical_atomic_json_bytes(receipt)
                if isinstance(receipt, dict)
                else b""
            )
        else:
            from historical_context_formatter import HistoricalContextReplyStore

            loaded = historical_context_reply_store()._load_receipt_safely()
            receipt = loaded[0] if loaded is not None else None
            receipt_bytes = loaded[1] if loaded is not None else b""
        if not isinstance(receipt, dict) or not receipt_bytes:
            raise ExactReceiptRetirementError(
                "prepared source receipt is unavailable or invalid"
            )
        retire_lane_transport_journal_if_present(
            commit_proof=commit_proof,
            receipt_path=source_path,
            receipt=receipt,
            lane=lane,
            post_id=post_id,
            current_receipt_bytes=receipt_bytes,
        )
    result = resume_interrupted_receipt_retirement(
        source_path,
        mutation_authority=state_commit_mutation_authority(
            commit_proof, "interrupted source receipt retirement resume"
        ),
        **({"expected_retirement": retirement} if non_success else {}),
    )
    log.warning(
        "Resumed interrupted exact source-receipt retirement path=%s phase=%s",
        source_path,
        result.initial_phase,
    )
    return True


def retire_current_source_receipt(
    receipt_path: Path,
    expected_receipt_bytes: bytes,
    *,
    disposition: str | None = None,
    latch_source_receipt_retirement_uncertainty: Any,
    retire_or_resume_exact_receipt: Any,
    transaction_mutation_authority: Any,
) -> None:
    """Resume a prepared removal, or start retirement when no journal existed."""

    retire_or_resume_exact_receipt(
        receipt_path,
        expected_receipt_bytes,
        mutation_authority=transaction_mutation_authority(
            "source receipt exact retirement"
        ),
        on_retirement_uncertainty=latch_source_receipt_retirement_uncertainty,
        **({"disposition": disposition} if disposition is not None else {}),
    )


def resume_interrupted_confirmed_media_retirement_if_present(
    *,
    MEDIA_UPLOAD_RECEIPT_FILE: Any,
    MEME_POST_RECEIPT_FILE: Any,
    MediaUploadReceiptError: Any,
    REGULAR_POST_RECEIPT_FILE: Any,
    fence_path_for_journal: Any,
    inspect_transport_state: Any,
    journal_path_for_receipt: Any,
    log: Any,
    media_fence_path_for_receipt: Any,
    os: Any,
    require_instance_lock_for_remote_write: Any,
    resume_interrupted_confirmed_media_retirement: Any,
    transaction_mutation_authority: Any,
) -> bool:
    """Finish one exact media-fence retirement before the global barrier.

    The only automatically selected state is the documented crash boundary in
    which the confirmed media receipt is absent, its immutable media fence
    survives, and exactly one canonical main-lane tweet journal/fence pair is
    still ``prepared``.  That tweet pair and its source receipt remain intact
    and continue to block every remote-write lane after this local repair; no
    retransmission or automatic transaction abort is authorised here.
    """

    require_instance_lock_for_remote_write(
        "Interrupted confirmed-media retirement recovery"
    )

    def entry_exists(path: Path) -> bool:
        try:
            os.lstat(path)
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise MediaUploadReceiptError(
                "confirmed media retirement namespace cannot be inspected"
            ) from exc
        return True

    media_receipt_path = Path(MEDIA_UPLOAD_RECEIPT_FILE)
    media_fence_path = media_fence_path_for_receipt(media_receipt_path)
    if entry_exists(media_receipt_path):
        return False
    if not entry_exists(media_fence_path):
        return False

    source_by_lane = {
        "quote_image": Path(REGULAR_POST_RECEIPT_FILE),
        "daily_meme": Path(MEME_POST_RECEIPT_FILE),
    }
    journal_paths = {
        journal_path_for_receipt(source_path).absolute()
        for source_path in source_by_lane.values()
    }
    if len(journal_paths) != 1:
        raise MediaUploadReceiptError(
            "main-lane transport journals do not share one canonical owner path"
        )
    journal_path = next(iter(journal_paths))
    owner_state = inspect_transport_state(journal_path)
    if (
        not owner_state.blocking
        or owner_state.classification != "prepared_pair"
        or owner_state.journal is None
        or owner_state.fence is None
    ):
        raise MediaUploadReceiptError(
            "orphaned media fence owner is not one intact prepared pair"
        )
    owner_document = owner_state.journal.document
    lane = str(owner_document.get("lane") or "")
    source_path = source_by_lane.get(lane)
    if (
        source_path is None
        or journal_path.absolute()
        != journal_path_for_receipt(source_path).absolute()
        or owner_document.get("source_receipt", {}).get("basename")
        != source_path.name
    ):
        raise MediaUploadReceiptError(
            "orphaned media fence owner does not bind a canonical main lane"
        )

    result = resume_interrupted_confirmed_media_retirement(
        media_receipt_path,
        mutation_authority=transaction_mutation_authority(
            "interrupted media retirement resume"
        ),
        transport_journal_path=journal_path,
        transport_fence_path=fence_path_for_journal(journal_path),
        source_receipt_path=source_path,
    )
    if result is None or result.state != "retired":
        raise MediaUploadReceiptError(
            "interrupted confirmed media retirement did not complete"
        )
    log.warning(
        "Resumed interrupted confirmed-media fence retirement lane=%s "
        "media_transaction_id=%s media_id=%s; prepared tweet transaction "
        "remains blocked",
        result.lane,
        result.media_transaction_id,
        result.media_id,
    )
    return True


def expected_lane_transport_source_receipt_bytes(
    *,
    receipt: dict,
    lane: str,
    current_receipt_bytes: bytes,
    TransportJournalError: Any,
    confirmed_pending_schedule_receipt_is_semantically_valid: Any,
    conversational_sending_receipt_from_confirmed: Any,
    current_main_post_attempt_is_semantically_valid: Any,
    sending_reply_receipt_is_semantically_valid: Any,
) -> bytes:
    """Reconstruct the exact pre-transport receipt for one public lane."""

    if type(current_receipt_bytes) is not bytes or not current_receipt_bytes:
        raise TransportJournalError("current lane receipt bytes are invalid")
    if lane in {"quote_image", "daily_meme"}:
        if (
            current_main_post_attempt_is_semantically_valid(receipt)
            and receipt.get("lifecycle_state") == "attempting"
        ):
            return current_receipt_bytes
        if (
            receipt.get("receipt_type") == "confirmed_pending_schedule"
            and confirmed_pending_schedule_receipt_is_semantically_valid(
                receipt,
                expected_lane=lane,
            )
        ):
            return canonical_atomic_json_bytes(receipt["source_attempt"])
        source_attempt = receipt.get("source_attempt")
        source_sha256 = receipt.get("source_attempt_sha256")
        if (
            not isinstance(source_attempt, dict)
            or not re.fullmatch(r"[0-9a-f]{64}", str(source_sha256 or ""))
        ):
            raise TransportJournalError(
                "current main-post journal cannot be reconciled by a receipt "
                "without exact source-attempt lineage"
            )
        source_bytes = canonical_atomic_json_bytes(source_attempt)
        if hashlib.sha256(source_bytes).hexdigest() != source_sha256:
            raise TransportJournalError(
                "main-post confirmed source-attempt lineage changed"
            )
        return source_bytes
    if lane == "conversational_reply":
        if sending_reply_receipt_is_semantically_valid(receipt):
            return current_receipt_bytes
        source = conversational_sending_receipt_from_confirmed(receipt)
        source_bytes = canonical_atomic_json_bytes(source)
        if (
            hashlib.sha256(source_bytes).hexdigest()
            != receipt.get("source_receipt_sha256")
        ):
            raise TransportJournalError(
                "conversational confirmed source-receipt lineage changed"
            )
        return source_bytes
    if lane == "historical_context_reply":
        from historical_context_formatter import HistoricalContextReplyStore

        if HistoricalContextReplyStore._valid_sending_receipt(receipt):
            return current_receipt_bytes
        try:
            return HistoricalContextReplyStore.source_receipt_bytes_from_confirmed(
                receipt
            )
        except (TypeError, ValueError) as exc:
            raise TransportJournalError(
                "historical-context confirmed source-receipt lineage changed"
            ) from exc
    raise TransportJournalError(
        "confirmed transport journal has no supported source-lineage lane"
    )


def verify_lane_transport_source_lineage_if_present(
    *,
    receipt_path: Path,
    receipt: dict,
    lane: str,
    post_id: str,
    current_receipt_bytes: bytes | None = None,
    TRANSPORT_SOURCE_VALIDATOR_ID: Any,
    TransportJournalError: Any,
    expected_lane_transport_source_receipt_bytes: Any,
    journal_path_for_receipt: Any,
    receipt_int: Any,
    transport_journal_is_blocking: Any,
    verify_confirmed_transport_source_lineage: Any,
) -> bool:
    """Validate a current journal before any derived receipt changes state."""

    journal_path = journal_path_for_receipt(receipt_path)
    if not transport_journal_is_blocking(journal_path):
        return False
    if current_receipt_bytes is None:
        if lane == "historical_context_reply":
            from historical_context_formatter import canonical_json_bytes

            current_receipt_bytes = canonical_json_bytes(receipt)
        else:
            current_receipt_bytes = canonical_atomic_json_bytes(receipt)
    source_bytes = expected_lane_transport_source_receipt_bytes(
        receipt=receipt,
        lane=lane,
        current_receipt_bytes=current_receipt_bytes,
    )
    details = verify_confirmed_transport_source_lineage(
        receipt_path=receipt_path,
        expected_source_receipt_bytes=source_bytes,
        lane=lane,
        post_id=post_id,
        validator_id=TRANSPORT_SOURCE_VALIDATOR_ID,
    )
    if lane == "quote_image":
        derived_epoch = receipt_int(
            receipt.get("confirmation_epoch")
            if receipt.get("receipt_type") == "confirmed_pending_schedule"
            else receipt.get("quote_post_epoch")
        )
    elif lane == "daily_meme":
        derived_epoch = receipt_int(
            receipt.get("confirmation_epoch")
            if receipt.get("receipt_type") == "confirmed_pending_schedule"
            else receipt.get("meme_post_epoch")
        )
    elif lane == "conversational_reply":
        derived_epoch = receipt_int(receipt.get("confirmation_epoch"))
    else:
        derived_epoch = None
    if lane != "historical_context_reply" and (
        derived_epoch is None or derived_epoch != details.confirmation_epoch
    ):
        raise TransportJournalError(
            "confirmed receipt time differs from its transport journal"
        )
    return True


def retire_lane_transport_journal_if_present(
    *,
    receipt_path: Path,
    receipt: dict,
    lane: str,
    post_id: str,
    current_receipt_bytes: bytes | None = None,
    expected_lane_transport_source_receipt_bytes: Any,
    journal_path_for_receipt: Any,
    prepare_exact_receipt_retirement: Any,
    retire_confirmed_transport_transaction: Any,
    transaction_mutation_authority: Any,
    transport_journal_is_blocking: Any,
) -> bool:
    """Retire a confirmed journal while an exact source-removal guard overlaps."""

    journal_path = journal_path_for_receipt(receipt_path)
    if not transport_journal_is_blocking(journal_path):
        return False
    if current_receipt_bytes is None:
        if lane == "historical_context_reply":
            from historical_context_formatter import canonical_json_bytes

            current_receipt_bytes = canonical_json_bytes(receipt)
        else:
            current_receipt_bytes = canonical_atomic_json_bytes(receipt)
    expected_source_receipt_bytes = expected_lane_transport_source_receipt_bytes(
        receipt=receipt,
        lane=lane,
        current_receipt_bytes=current_receipt_bytes,
    )
    prepare_exact_receipt_retirement(
        receipt_path,
        current_receipt_bytes,
        mutation_authority=transaction_mutation_authority(
            "source receipt retirement preparation"
        ),
    )
    retire_confirmed_transport_transaction(
        mutation_authority=transaction_mutation_authority(
            "confirmed transport journal retirement"
        ),
        receipt_path=receipt_path,
        expected_confirmed_receipt=receipt,
        expected_source_receipt_bytes=expected_source_receipt_bytes,
        expected_current_receipt_bytes=current_receipt_bytes,
        source_retirement_prepared=True,
        lane=lane,
        post_id=post_id,
    )
    return True
