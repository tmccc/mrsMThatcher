"""Local transaction recovery and startup receipt gates.

The root supplies current runtime dependencies explicitly on each call. This
module owns fixed receipt hashing and performs no runtime work at import; it
retains no runtime authority.
"""
from __future__ import annotations

import hashlib
from typing import Any

from mrs_bot_receipt_retirement import confirmed_context_outbox_matches_receipt


def ensure_reconciled_regular_receipt_schedule_is_future(
    receipt: dict,
    state: dict,
    current: int,
    *,
    log: Any,
    schedule_next_quote_post: Any,
) -> bool:
    """Persist a future quote schedule before completing current-receipt replay."""
    if int(state.get("last_quote_post_epoch", 0) or 0) != int(
        receipt["quote_post_epoch"]
    ):
        return False
    next_quote_epoch = int(state.get("next_quote_post_epoch", 0) or 0)
    if next_quote_epoch > current:
        return False
    log.warning(
        "Reconciled regular receipt has a due quote schedule; deferring the next "
        "regular post before completing receipt replay"
    )
    schedule_next_quote_post(state, current, save=False)
    return True


def block_if_unresolved_regular_post_receipt(
    *,
    InvalidRegularPostReceipt: Any,
    REGULAR_POST_RECEIPT_FILE: Any,
    UnresolvedRegularPostReceipt: Any,
    load_regular_post_receipt: Any,
) -> None:
    """Refuse a new regular post while a prior receipt is unresolved."""
    status, _receipt = load_regular_post_receipt()
    if status == "absent":
        return
    if status == "invalid":
        raise InvalidRegularPostReceipt(f"Invalid regular-post receipt blocks main posting: {REGULAR_POST_RECEIPT_FILE}")
    raise UnresolvedRegularPostReceipt(f"Unresolved regular-post receipt must be reconciled before another main post: {REGULAR_POST_RECEIPT_FILE}")


def reconcile_startup_main_post_receipts(
    lines_used: set,
    images_used: set,
    state: dict,
    current: int,
    *,
    global_remote_writes_paused: Any,
    log: Any,
    reconcile_main_post_receipts: Any,
) -> dict[str, bool]:
    """Reconcile main receipts unless a global maintenance pause is active."""
    if global_remote_writes_paused():
        log.warning(
            "Global runtime control pause is active; leaving main-post receipts "
            "untouched during startup"
        )
        return {"regular": False, "meme": False}
    return reconcile_main_post_receipts(
        lines_used,
        images_used,
        state,
        minimum_next_quote_epoch=current,
    )


def _promote_confirmed_transport_source(
    owning_path: Any,
    journal_path: Any,
    *,
    legacy_conversational_transport_promotion: bool,
    historical_outbox_store: Any,
    historical_outbox_context: Any,
    TRANSPORT_SOURCE_VALIDATOR_ID: Any,
    TransportJournalError: Any,
    _legacy_conversational_transport_source_semantic_validator: Any,
    _promote_legacy_sending_reply_receipt_from_confirmed_transport: Any,
    _reply_confirmation_epoch_after_remote_success: Any,
    bind_confirmed_transport_source: Any,
    confirmation_epoch_for_main_attempt: Any,
    historical_context_reply_store: Any,
    promote_main_post_attempt_to_confirmed_pending_schedule: Any,
    promote_sending_reply_receipt: Any,
    transport_source_semantic_validator: Any,
) -> None:
    """Preserve the shared bound-source dispatch for every receipt owner.

    Dispatch follows the bound transport's lane, including its native errors,
    while historical recovery supplies its exact already-checked outbox values.
    """
    source_validator = (
        _legacy_conversational_transport_source_semantic_validator
        if legacy_conversational_transport_promotion
        else transport_source_semantic_validator
    )
    recovery = bind_confirmed_transport_source(
        journal_path=journal_path,
        receipt_path=owning_path,
        validator_id=TRANSPORT_SOURCE_VALIDATOR_ID,
        validator=source_validator,
    )
    source_receipt = recovery.source_binding.receipt_document
    details = recovery.details
    if details.lane in {"quote_image", "daily_meme"}:
        image_summary = (
            str(source_receipt["recovery_plan"].get("image_summary") or "")
            if details.lane == "daily_meme"
            else ""
        )
        promote_main_post_attempt_to_confirmed_pending_schedule(
            source_receipt,
            post_id=details.post_id,
            confirmation_epoch=confirmation_epoch_for_main_attempt(
                source_receipt,
                details.confirmation_epoch,
            ),
            image_summary=image_summary,
        )
    elif details.lane == "conversational_reply":
        confirmation_epoch = _reply_confirmation_epoch_after_remote_success(
            source_receipt,
            details.confirmation_epoch,
        )
        if legacy_conversational_transport_promotion:
            _promote_legacy_sending_reply_receipt_from_confirmed_transport(
                source_receipt,
                reply_post_id=details.post_id,
                confirmation_epoch=confirmation_epoch,
            )
        else:
            promote_sending_reply_receipt(
                source_receipt,
                reply_post_id=details.post_id,
                confirmation_epoch=confirmation_epoch,
            )
    elif details.lane == "historical_context_reply":
        from historical_context_formatter import HistoricalContextReplyStore

        if (
            historical_outbox_store is None
            or not isinstance(historical_outbox_context, dict)
        ):
            raise TransportJournalError(
                "confirmed historical-context transport has no exact "
                "outbox authority"
            )
        historical_context_reply_store().promote_sending_receipt_from_confirmed_transport(
            source_receipt,
            reply_post_id=details.post_id,
            confirmation_epoch=details.confirmation_epoch,
            require_confirmed_transport=True,
        )
        historical_outbox_store.record_confirmed(
            str(source_receipt["parent_post_id"]),
            attempt_number=int(
                historical_outbox_context["attempt_count"]
            ),
            reply_post_id=details.post_id,
            confirmed_epoch=details.confirmation_epoch,
        )
    else:
        raise TransportJournalError(
            "confirmed journal has no supported recovery lane"
        )


def _reconcile_historical_context_receipt_before_global_barrier(
    owning_path: Any,
    journal_path: Any,
    journal_state: Any,
    result: dict[str, bool],
    *,
    HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE: Any,
    TRANSPORT_SOURCE_VALIDATOR_ID: Any,
    TransportJournalError: Any,
    _legacy_conversational_transport_source_semantic_validator: Any,
    _promote_legacy_sending_reply_receipt_from_confirmed_transport: Any,
    _reply_confirmation_epoch_after_remote_success: Any,
    bind_confirmed_transport_source: Any,
    confirmation_epoch_for_main_attempt: Any,
    emit_historical_context_store_observation: Any,
    historical_context_outbox_store: Any,
    historical_context_reply_store: Any,
    inspect_confirmed_transport_transaction: Any,
    log_event: Any,
    now_epoch: Any,
    promote_main_post_attempt_to_confirmed_pending_schedule: Any,
    promote_sending_reply_receipt: Any,
    recover_interrupted_historical_context_attempt: Any,
    transport_source_semantic_validator: Any,
) -> dict[str, bool]:
    """Recover the sole historical receipt using its exact source and outbox.

    The coordinator has already checked global policy, all receipt namespaces
    and the owning journal. Keep source bytes, preloaded receipt identity and
    outbox lock/recheck order local to this lane; propagate native outcomes.
    """
    needs_transport_promotion = False
    historical_store = None
    historical_sending_receipt = None
    historical_sending_receipt_bytes = None
    historical_confirmed_receipt = None
    historical_legacy_confirmed_receipt = None
    historical_outbox_store = None
    historical_outbox_obligation = None
    historical_outbox_context = None
    historical_loaded_receipt = None
    from historical_context_formatter import HistoricalContextReplyStore

    historical_store = historical_context_reply_store()
    loaded = historical_store._load_receipt_safely()
    historical_loaded_receipt = loaded
    needs_transport_promotion = bool(
        loaded is not None
        and HistoricalContextReplyStore._valid_sending_receipt(loaded[0])
    )
    if needs_transport_promotion:
        historical_sending_receipt = loaded[0]
        historical_sending_receipt_bytes = loaded[1]
    elif (
        loaded is not None
        and HistoricalContextReplyStore._valid_receipt(loaded[0])
        and loaded[0].get("lifecycle_state") == "confirmed"
        and "source_receipt_sha256" in loaded[0]
    ):
        historical_confirmed_receipt = loaded[0]
        historical_sending_receipt = (
            HistoricalContextReplyStore.sending_receipt_from_confirmed(
                loaded[0]
            )
        )
        historical_sending_receipt_bytes = (
            HistoricalContextReplyStore.source_receipt_bytes_from_confirmed(
                loaded[0]
            )
        )
    elif (
        loaded is not None
        and HistoricalContextReplyStore._valid_receipt(loaded[0])
    ):
        historical_legacy_confirmed_receipt = loaded[0]
    if historical_legacy_confirmed_receipt is not None:
        if journal_state.classification != "clear":
            raise RuntimeError(
                "legacy confirmed historical-context receipt conflicts with "
                "an independent transport journal"
            )
        parent_id = str(
            historical_legacy_confirmed_receipt["parent_post_id"]
        )
        historical_outbox_store = historical_context_outbox_store()
        historical_outbox_obligation = historical_outbox_store.get(parent_id)
        historical_outbox_context = (
            historical_outbox_obligation.get("context_reply")
            if isinstance(historical_outbox_obligation, dict)
            else None
        )
        if not isinstance(historical_outbox_obligation, dict):
            raise RuntimeError(
                "confirmed historical-context receipt has no matching "
                "outbox obligation"
            )
        if (
            not isinstance(historical_outbox_context, dict)
            or historical_outbox_context.get("quote_id")
            != historical_legacy_confirmed_receipt["quote_id"]
        ):
            raise RuntimeError(
                "confirmed historical-context receipt conflicts with its "
                "outbox identity"
            )
        if historical_outbox_context.get("state") == (
            "context_reply_attempting"
        ):
            if {
                "source_receipt_sha256",
                "source_receipt_attempt_number",
            } & set(historical_outbox_context):
                raise RuntimeError(
                    "legacy confirmed historical-context receipt conflicts "
                    "with a source-bound attempting outbox"
                )
            if historical_outbox_context.get("attempt_count") != (
                historical_legacy_confirmed_receipt.get("attempt_number")
            ):
                raise RuntimeError(
                    "legacy confirmed historical-context receipt conflicts "
                    "with its outbox attempt"
                )
            historical_outbox_store.record_confirmed(
                parent_id,
                attempt_number=int(
                    historical_outbox_context["attempt_count"]
                ),
                reply_post_id=historical_legacy_confirmed_receipt[
                    "reply_post_id"
                ],
                confirmed_epoch=int(
                    historical_legacy_confirmed_receipt["reply_epoch"]
                ),
            )
        elif not confirmed_context_outbox_matches_receipt(
            historical_outbox_context,
            historical_legacy_confirmed_receipt,
        ):
            raise RuntimeError(
                "confirmed historical-context receipt conflicts with its "
                "durable outbox outcome"
            )
    if (
        owning_path == HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
        and historical_sending_receipt is not None
    ):
        if (
            historical_store is None
            or historical_sending_receipt is None
            or historical_sending_receipt_bytes is None
        ):
            raise RuntimeError(
                "historical-context local recovery lost its source receipt"
            )
        parent_id = str(historical_sending_receipt["parent_post_id"])
        historical_outbox_store = historical_context_outbox_store()
        historical_outbox_obligation = historical_outbox_store.get(parent_id)
        historical_outbox_context = (
            historical_outbox_obligation.get("context_reply")
            if isinstance(historical_outbox_obligation, dict)
            else None
        )
        if not isinstance(historical_outbox_obligation, dict):
            # A confirmed receipt is durable proof that the remote operation
            # succeeded.  Without its matching outbox obligation there is no
            # authority to complete local history or retire either transport
            # barrier.  In particular, ``reconcile_receipt_disposition`` is a
            # mutating operation for confirmed receipts, so fail before
            # invoking it and preserve every byte for operator reconciliation.
            if historical_confirmed_receipt is not None:
                raise RuntimeError(
                    "confirmed historical-context receipt has no matching "
                    "outbox obligation"
                )
            disposition = historical_store.reconcile_receipt_disposition(
                retain_definite_failure_receipt=True,
            )
            if disposition != "definite_failure":
                raise RuntimeError(
                    "historical-context sending receipt had an unexpected "
                    f"local disposition: {disposition}"
                )
            raise RuntimeError(
                "historical-context sending receipt has no matching outbox "
                "obligation"
            )
        if (
            not isinstance(historical_outbox_context, dict)
            or historical_outbox_context.get("quote_id")
            != historical_sending_receipt["quote_id"]
        ):
            raise RuntimeError(
                "historical-context sending receipt conflicts with its "
                "outbox identity"
            )
        if journal_state.classification == "confirmed_pair":
            confirmed_details = inspect_confirmed_transport_transaction(
                journal_path
            )
            if (
                confirmed_details.lane != "historical_context_reply"
                or confirmed_details.source_receipt_sha256
                != hashlib.sha256(historical_sending_receipt_bytes).hexdigest()
                or (
                    historical_confirmed_receipt is not None
                    and confirmed_details.post_id
                    != historical_confirmed_receipt["reply_post_id"]
                )
            ):
                raise RuntimeError(
                    "confirmed historical-context transport conflicts with "
                    "its exact receipt lineage"
                )
            if historical_outbox_context.get("state") == (
                "context_reply_attempting"
            ):
                if (
                    historical_outbox_context.get(
                        "remote_transaction_started"
                    )
                    is not True
                    or historical_outbox_context.get(
                        "source_receipt_sha256"
                    )
                    != hashlib.sha256(
                        historical_sending_receipt_bytes
                    ).hexdigest()
                    or historical_outbox_context.get(
                        "source_receipt_attempt_number"
                    )
                    != historical_sending_receipt["attempt_number"]
                ):
                    raise RuntimeError(
                        "confirmed historical-context transport conflicts "
                        "with its exact attempting outbox source"
                    )
                if historical_confirmed_receipt is not None:
                    historical_outbox_store.record_confirmed(
                        str(historical_sending_receipt["parent_post_id"]),
                        attempt_number=int(
                            historical_outbox_context["attempt_count"]
                        ),
                        reply_post_id=confirmed_details.post_id,
                        confirmed_epoch=confirmed_details.confirmation_epoch,
                    )
                    historical_outbox_context = (
                        historical_outbox_store.get(parent_id)[
                            "context_reply"
                        ]
                    )
            elif not confirmed_context_outbox_matches_receipt(
                historical_outbox_context,
                (
                    historical_confirmed_receipt
                    if historical_confirmed_receipt is not None
                    else {
                        "quote_id": historical_sending_receipt["quote_id"],
                        "reply_post_id": confirmed_details.post_id,
                        "source_receipt_sha256": hashlib.sha256(
                            historical_sending_receipt_bytes
                        ).hexdigest(),
                        "attempt_number": historical_sending_receipt[
                            "attempt_number"
                        ],
                    }
                ),
            ):
                raise RuntimeError(
                    "confirmed historical-context transport conflicts with "
                    "its durable outbox outcome"
                )
        elif historical_confirmed_receipt is not None:
            if not confirmed_context_outbox_matches_receipt(
                historical_outbox_context,
                historical_confirmed_receipt,
            ):
                raise RuntimeError(
                    "confirmed historical-context receipt conflicts with its "
                    "durable outbox outcome"
                )
            # The journal may already have been retired after durable remote
            # confirmation.  The exact lineage-bearing receipt and terminal
            # outbox row jointly authorise the ordinary history/receipt
            # reconciliation below; neither parent/reply identity alone does.
        elif historical_outbox_context.get("state") == "context_reply_attempting":
            with historical_outbox_store.worker_lock():
                current_obligation = historical_outbox_store.get(parent_id)
                if current_obligation != historical_outbox_obligation:
                    raise RuntimeError(
                        "historical-context outbox changed before local "
                        "recovery"
                    )
                recovered = recover_interrupted_historical_context_attempt(
                    historical_outbox_store,
                    current_obligation,
                    recovered_epoch=now_epoch(),
                    receipt_was_observed=True,
                )
            log_event("historical_context_obligation", **recovered)
            result["historical_context"] = True
            return result
        elif historical_outbox_context.get("state") not in {
            "context_reply_failed_retryable",
            "context_reply_failed_terminal",
        }:
            raise RuntimeError(
                "historical-context failure receipt conflicts with its "
                "outbox state"
            )
        else:
            historical_store.ensure_proved_failure_history_from_outbox(
                historical_outbox_context
            )
            disposition = historical_store.reconcile_receipt_disposition()
            if disposition != "definite_failure":
                raise RuntimeError(
                    "historical-context sending receipt has no local recovery "
                    "disposition"
                )
            result["historical_context"] = True
            return result
    if journal_state.classification == "confirmed_pair" and needs_transport_promotion:
        _promote_confirmed_transport_source(
            owning_path,
            journal_path,
            legacy_conversational_transport_promotion=False,
            historical_outbox_store=historical_outbox_store,
            historical_outbox_context=historical_outbox_context,
            TRANSPORT_SOURCE_VALIDATOR_ID=TRANSPORT_SOURCE_VALIDATOR_ID,
            TransportJournalError=TransportJournalError,
            _legacy_conversational_transport_source_semantic_validator=_legacy_conversational_transport_source_semantic_validator,
            _promote_legacy_sending_reply_receipt_from_confirmed_transport=_promote_legacy_sending_reply_receipt_from_confirmed_transport,
            _reply_confirmation_epoch_after_remote_success=_reply_confirmation_epoch_after_remote_success,
            bind_confirmed_transport_source=bind_confirmed_transport_source,
            confirmation_epoch_for_main_attempt=confirmation_epoch_for_main_attempt,
            historical_context_reply_store=historical_context_reply_store,
            promote_main_post_attempt_to_confirmed_pending_schedule=promote_main_post_attempt_to_confirmed_pending_schedule,
            promote_sending_reply_receipt=promote_sending_reply_receipt,
            transport_source_semantic_validator=transport_source_semantic_validator,
        )

    from historical_context_formatter import HistoricalContextReplyStore

    store = historical_context_reply_store()
    if (
        historical_loaded_receipt is not None
        and (
            historical_confirmed_receipt is not None
            or historical_legacy_confirmed_receipt is not None
        )
    ):
        result["historical_context"] = (
            store.reconcile_receipt_disposition(
                preloaded_receipt=historical_loaded_receipt,
            )
            == "confirmed"
        )
    else:
        result["historical_context"] = (
            store.reconcile_confirmed_receipt_if_present()
        )
    if result["historical_context"]:
        emit_historical_context_store_observation(store, parent_id)
    return result


def reconcile_confirmed_transactions_before_global_barrier(
    lines_used: set,
    images_used: set,
    state: dict,
    current: int | None = None,
    *,
    CONFIRMED_REPLY_RECEIPT_FILE: Any,
    HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE: Any,
    MEME_POST_RECEIPT_FILE: Any,
    REGULAR_POST_RECEIPT_FILE: Any,
    TRANSPORT_SOURCE_VALIDATOR_ID: Any,
    TransportJournalError: Any,
    _legacy_conversational_transport_source_semantic_validator: Any,
    _promote_legacy_sending_reply_receipt_from_confirmed_transport: Any,
    _reply_confirmation_epoch_after_remote_success: Any,
    bind_confirmed_transport_source: Any,
    confirmation_epoch_for_main_attempt: Any,
    emit_historical_context_store_observation: Any,
    global_remote_writes_paused: Any,
    historical_context_outbox_remote_attempt_parent_for_local_reconciliation: Any,
    historical_context_outbox_store: Any,
    historical_context_reply_store: Any,
    inspect_confirmed_transport_transaction: Any,
    inspect_transport_state: Any,
    journal_path_for_receipt: Any,
    load_confirmed_reply_receipt: Any,
    load_meme_post_receipt: Any,
    load_regular_post_receipt: Any,
    log_event: Any,
    now_epoch: Any,
    promote_main_post_attempt_to_confirmed_pending_schedule: Any,
    promote_sending_reply_receipt: Any,
    receipt_namespace_entry_exists: Any,
    reconcile_confirmed_reply_receipt: Any,
    reconcile_main_post_receipts: Any,
    recover_interrupted_historical_context_attempt: Any,
    remote_write_safety_incident_is_latched: Any,
    remote_write_safety_marker_path_present_or_unsafe: Any,
    remote_write_safety_protocol_is_active: Any,
    transport_source_semantic_validator: Any,
) -> dict[str, bool]:
    """Finish one exact locally recoverable transaction before global blocking.

    Transport journals are intentionally global barriers, but a confirmed
    journal plus its confirmed lane receipt is also enough local authority to
    finish state persistence without another remote request.  This narrow
    pre-barrier reconciler promotes a sending/attempting source only when its
    exact confirmed journal proves the remote identity.  It may also consume an
    exact historical-context definite-failure pair through the matching outbox
    attempt.  It never runs auxiliary remote work and refuses to choose between
    multiple lane receipts.
    """

    result = {
        "historical_context": False,
        "conversational_reply": False,
        "regular": False,
        "meme": False,
    }
    if global_remote_writes_paused():
        return result
    if (
        not remote_write_safety_protocol_is_active()
        or remote_write_safety_incident_is_latched()
        or remote_write_safety_marker_path_present_or_unsafe()
    ):
        return result

    receipt_paths = (
        REGULAR_POST_RECEIPT_FILE,
        MEME_POST_RECEIPT_FILE,
        CONFIRMED_REPLY_RECEIPT_FILE,
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
    )
    present = [
        path for path in receipt_paths if receipt_namespace_entry_exists(path)
    ]
    if not present:
        # A confirmed historical-context receipt can have completed exact
        # history and retired its source/journal immediately before a crash,
        # while the independently durable outbox still says that the remote
        # transaction was attempting.  That outbox row is correctly a global
        # barrier, so the ordinary worker below the barrier is unreachable.
        # Permit only the sole risky parent to run the existing local-only
        # reconciler here.  It requires exact source-bound completed/failed
        # history and repeats no remote work; missing, stale, multiple or
        # conflicting evidence raises and leaves the global barrier intact.
        parent_id = (
            historical_context_outbox_remote_attempt_parent_for_local_reconciliation()
        )
        if parent_id is not None:
            outbox_store = historical_context_outbox_store()
            with outbox_store.worker_lock():
                obligation = outbox_store.get(parent_id)
                if not isinstance(obligation, dict):
                    raise RuntimeError(
                        "risky historical-context outbox parent disappeared "
                        "before local reconciliation"
                    )
                recovered = recover_interrupted_historical_context_attempt(
                    outbox_store,
                    obligation,
                    recovered_epoch=now_epoch(),
                )
            log_event("historical_context_obligation", **recovered)
            result["historical_context"] = True
            return result
    if len(present) != 1:
        return result

    owning_path = present[0]
    journal_path = journal_path_for_receipt(owning_path)
    journal_state = inspect_transport_state(journal_path)
    needs_transport_promotion = False
    legacy_conversational_transport_promotion = False
    if owning_path == REGULAR_POST_RECEIPT_FILE:
        status, source = load_regular_post_receipt()
        needs_transport_promotion = bool(
            status == "sending"
            and isinstance(source, dict)
            and source.get("lifecycle_state") == "attempting"
        )
    elif owning_path == MEME_POST_RECEIPT_FILE:
        status, source = load_meme_post_receipt()
        needs_transport_promotion = bool(
            status == "sending"
            and isinstance(source, dict)
            and source.get("lifecycle_state") == "attempting"
        )
    elif owning_path == CONFIRMED_REPLY_RECEIPT_FILE:
        status, _source = load_confirmed_reply_receipt()
        needs_transport_promotion = status in {"sending", "legacy_sending"}
        legacy_conversational_transport_promotion = status == "legacy_sending"
    elif owning_path == HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE:
        return _reconcile_historical_context_receipt_before_global_barrier(
            owning_path,
            journal_path,
            journal_state,
            result,
            HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE=HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
            TRANSPORT_SOURCE_VALIDATOR_ID=TRANSPORT_SOURCE_VALIDATOR_ID,
            TransportJournalError=TransportJournalError,
            _legacy_conversational_transport_source_semantic_validator=_legacy_conversational_transport_source_semantic_validator,
            _promote_legacy_sending_reply_receipt_from_confirmed_transport=_promote_legacy_sending_reply_receipt_from_confirmed_transport,
            _reply_confirmation_epoch_after_remote_success=_reply_confirmation_epoch_after_remote_success,
            bind_confirmed_transport_source=bind_confirmed_transport_source,
            confirmation_epoch_for_main_attempt=confirmation_epoch_for_main_attempt,
            emit_historical_context_store_observation=emit_historical_context_store_observation,
            historical_context_outbox_store=historical_context_outbox_store,
            historical_context_reply_store=historical_context_reply_store,
            inspect_confirmed_transport_transaction=inspect_confirmed_transport_transaction,
            log_event=log_event,
            now_epoch=now_epoch,
            promote_main_post_attempt_to_confirmed_pending_schedule=promote_main_post_attempt_to_confirmed_pending_schedule,
            promote_sending_reply_receipt=promote_sending_reply_receipt,
            recover_interrupted_historical_context_attempt=recover_interrupted_historical_context_attempt,
            transport_source_semantic_validator=transport_source_semantic_validator,
        )
    if journal_state.classification == "confirmed_pair" and needs_transport_promotion:
        _promote_confirmed_transport_source(
            owning_path,
            journal_path,
            legacy_conversational_transport_promotion=legacy_conversational_transport_promotion,
            historical_outbox_store=None,
            historical_outbox_context=None,
            TRANSPORT_SOURCE_VALIDATOR_ID=TRANSPORT_SOURCE_VALIDATOR_ID,
            TransportJournalError=TransportJournalError,
            _legacy_conversational_transport_source_semantic_validator=_legacy_conversational_transport_source_semantic_validator,
            _promote_legacy_sending_reply_receipt_from_confirmed_transport=_promote_legacy_sending_reply_receipt_from_confirmed_transport,
            _reply_confirmation_epoch_after_remote_success=_reply_confirmation_epoch_after_remote_success,
            bind_confirmed_transport_source=bind_confirmed_transport_source,
            confirmation_epoch_for_main_attempt=confirmation_epoch_for_main_attempt,
            historical_context_reply_store=historical_context_reply_store,
            promote_main_post_attempt_to_confirmed_pending_schedule=promote_main_post_attempt_to_confirmed_pending_schedule,
            promote_sending_reply_receipt=promote_sending_reply_receipt,
            transport_source_semantic_validator=transport_source_semantic_validator,
        )

    if owning_path == CONFIRMED_REPLY_RECEIPT_FILE:
        status, _receipt = load_confirmed_reply_receipt()
        if status == "valid":
            result["conversational_reply"] = reconcile_confirmed_reply_receipt(
                state
            )
        return result

    regular_status, _regular = load_regular_post_receipt()
    meme_status, _meme = load_meme_post_receipt()
    if regular_status not in {"pending_schedule", "valid"} and meme_status not in {
        "pending_schedule",
        "valid",
    }:
        return result
    main_result = reconcile_main_post_receipts(
        lines_used,
        images_used,
        state,
        minimum_next_quote_epoch=(now_epoch() if current is None else current),
        process_auxiliary_context=False,
    )
    result["regular"] = bool(main_result["regular"])
    result["meme"] = bool(main_result["meme"])
    return result
