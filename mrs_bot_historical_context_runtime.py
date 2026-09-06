"""Historical-context startup reconciliation and preflight with current root dependencies.

The root supplies current runtime dependencies explicitly on each call. This
module performs no runtime work at import and retains no runtime authority.
"""
from __future__ import annotations

from typing import Any


def reconcile_runtime_historical_context_state(
    *,
    HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE: Any,
    _set_historical_context_outbox_unavailable_reason: Any,
    confirmed_context_outbox_matches_receipt: Any,
    global_remote_writes_paused: Any,
    historical_context_outbox_store: Any,
    historical_context_reply_store: Any,
    inspect_transport_state: Any,
    journal_path_for_receipt: Any,
    log: Any,
    log_event: Any,
    now_epoch: Any,
    receipt_namespace_entry_exists: Any,
    recover_interrupted_historical_context_attempt: Any,
    resume_source_receipt_retirement_for_control_snapshot: Any,
) -> None:
    """Validate and reconcile durable context state while holding the process lock."""
    from historical_context_formatter import HistoricalContextReplyStore

    maintenance_paused = global_remote_writes_paused()
    resume_source_receipt_retirement_for_control_snapshot(
        maintenance_paused=maintenance_paused,
    )
    context_store = historical_context_reply_store()
    outbox_store = historical_context_outbox_store()
    outbox_available = True
    try:
        outbox_store.snapshot()
    except Exception as exc:
        outbox_available = False
        _set_historical_context_outbox_unavailable_reason(
            f"{type(exc).__name__}: {exc}"
        )
        log.critical(
            "Historical-context outbox is invalid or unavailable; the quote "
            "lane will fail its pre-post check while unrelated lanes continue",
            exc_info=True,
        )
        log_event(
            "historical_context_outbox",
            status="unavailable",
            error_type=type(exc).__name__,
            reason=str(exc)[:500],
            unrelated_lanes_available=True,
        )
    defer_to_confirmed_transport_recovery = False
    defer_to_outbox_recovery = False
    reconciliation_parent_id: str | None = None
    preloaded_confirmed_context_receipt = None
    leave_receipt_untouched = False
    if maintenance_paused:
        leave_receipt_untouched = receipt_namespace_entry_exists(
            HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
        )
        if leave_receipt_untouched:
            log.warning(
                "Global maintenance pause is active; leaving the historical-"
                "context transaction untouched until an unpaused loop tick"
            )
    else:
        loaded_receipt = context_store._load_receipt_safely()
        loaded_receipt_document = (
            loaded_receipt[0] if loaded_receipt is not None else None
        )
        loaded_is_confirmed_receipt = bool(
            HistoricalContextReplyStore._valid_receipt(
                loaded_receipt_document
            )
        )
        if loaded_is_confirmed_receipt:
            preloaded_confirmed_context_receipt = loaded_receipt
        loaded_is_confirmed_lineage = bool(
            loaded_is_confirmed_receipt
            and loaded_receipt_document.get("lifecycle_state")
            == "confirmed"
            and "source_receipt_sha256" in loaded_receipt_document
        )
        loaded_requires_outbox_authority = bool(
            HistoricalContextReplyStore._valid_sending_receipt(
                loaded_receipt_document
            )
            or loaded_is_confirmed_receipt
        )
        loaded_has_source_lineage = bool(
            HistoricalContextReplyStore._valid_sending_receipt(
                loaded_receipt_document
            )
            or loaded_is_confirmed_lineage
        )
        loaded_journal_classification = (
            inspect_transport_state(
                journal_path_for_receipt(
                    HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
                )
            ).classification
            if loaded_has_source_lineage
            else "absent"
        )
        if (
            loaded_receipt is not None
            and not outbox_available
            and loaded_requires_outbox_authority
        ):
            leave_receipt_untouched = True
            log.critical(
                "Leaving the historical-context transaction receipt untouched "
                "because its outbox authority is unavailable"
            )
        elif (
            loaded_has_source_lineage
            and loaded_journal_classification == "confirmed_pair"
        ):
            defer_to_confirmed_transport_recovery = True
            log.warning(
                "Deferring a transport-confirmed historical-context receipt "
                "to the pre-barrier local recovery path"
            )
        elif loaded_is_confirmed_receipt:
            reconciliation_parent_id = str(
                loaded_receipt_document["parent_post_id"]
            )
            obligation = outbox_store.get(reconciliation_parent_id)
            context = (
                obligation.get("context_reply")
                if isinstance(obligation, dict)
                else None
            )
            if not isinstance(obligation, dict):
                raise RuntimeError(
                    "confirmed historical-context receipt has no matching "
                    "outbox obligation"
                )
            if (
                not isinstance(context, dict)
                or context.get("quote_id")
                != loaded_receipt_document["quote_id"]
            ):
                raise RuntimeError(
                    "confirmed historical-context receipt conflicts with "
                    "its outbox identity"
                )
            if context.get("state") == "context_reply_attempting":
                if loaded_is_confirmed_lineage:
                    defer_to_outbox_recovery = True
                    log.warning(
                        "Deferring a confirmed historical-context receipt to "
                        "its exact attempting outbox recovery"
                    )
                else:
                    if {
                        "source_receipt_sha256",
                        "source_receipt_attempt_number",
                    } & set(context):
                        raise RuntimeError(
                            "legacy confirmed historical-context receipt "
                            "conflicts with a source-bound attempting outbox"
                        )
                    if context.get("attempt_count") != (
                        loaded_receipt_document.get("attempt_number")
                    ):
                        raise RuntimeError(
                            "legacy confirmed historical-context receipt "
                            "conflicts with its outbox attempt"
                        )
                    outbox_store.record_confirmed(
                        reconciliation_parent_id,
                        attempt_number=int(context["attempt_count"]),
                        reply_post_id=loaded_receipt_document[
                            "reply_post_id"
                        ],
                        confirmed_epoch=int(
                            loaded_receipt_document["reply_epoch"]
                        ),
                    )
            elif not confirmed_context_outbox_matches_receipt(
                context,
                loaded_receipt_document,
            ):
                raise RuntimeError(
                    "confirmed historical-context receipt conflicts with "
                    "its durable outbox outcome"
                )
        elif (
            loaded_receipt is not None
            and HistoricalContextReplyStore._valid_sending_receipt(
                loaded_receipt[0]
            )
        ):
            sending_receipt = loaded_receipt[0]
            reconciliation_parent_id = str(
                sending_receipt["parent_post_id"]
            )
            obligation = outbox_store.get(reconciliation_parent_id)
            context = (
                obligation.get("context_reply")
                if isinstance(obligation, dict)
                else None
            )
            if not isinstance(obligation, dict):
                disposition = context_store.reconcile_receipt_disposition(
                    retain_definite_failure_receipt=True,
                )
                if disposition != "definite_failure":
                    raise RuntimeError(
                        "historical-context sending receipt had an "
                        f"unexpected local disposition: {disposition}"
                    )
                raise RuntimeError(
                    "historical-context sending receipt has no matching "
                    "outbox obligation"
                )
            if (
                not isinstance(context, dict)
                or context.get("quote_id") != sending_receipt["quote_id"]
            ):
                raise RuntimeError(
                    "historical-context sending receipt conflicts with "
                    "its outbox identity"
                )
            if context.get("state") == "context_reply_attempting":
                defer_to_outbox_recovery = True
                log.warning(
                    "Deferring a historical-context sending receipt to "
                    "its exact attempting outbox recovery"
                )
            elif context.get("state") in {
                "context_reply_failed_retryable",
                "context_reply_failed_terminal",
            }:
                context_store.ensure_proved_failure_history_from_outbox(
                    context
                )
            else:
                raise RuntimeError(
                    "historical-context failure receipt conflicts with "
                    "its outbox state"
                )
    if not (
        defer_to_confirmed_transport_recovery
        or defer_to_outbox_recovery
        or leave_receipt_untouched
    ):
        try:
            if preloaded_confirmed_context_receipt is not None:
                context_store.reconcile_receipt_disposition(
                    preloaded_receipt=preloaded_confirmed_context_receipt,
                )
            else:
                context_store.reconcile_receipt()
        except Exception:
            log.critical(
                "Historical-context durable receipt could not be reconciled; "
                "refusing production startup to preserve the ambiguity barrier",
                exc_info=True,
            )
            raise
    if defer_to_outbox_recovery:
        if reconciliation_parent_id is None:
            raise RuntimeError(
                "historical-context outbox recovery has no parent identity"
            )
        with outbox_store.worker_lock():
            obligation = outbox_store.get(reconciliation_parent_id)
            if not isinstance(obligation, dict):
                raise RuntimeError(
                    "historical-context attempting outbox record disappeared"
                )
            recovered = recover_interrupted_historical_context_attempt(
                outbox_store,
                obligation,
                recovered_epoch=now_epoch(),
                receipt_was_observed=True,
            )
        log_event(
            "historical_context_obligation",
            **recovered,
        )


def require_historical_context_outbox_writable(
    *,
    _HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON: Any,
    _get_historical_context_outbox_unavailable_reason: Any,
    _set_historical_context_outbox_unavailable_reason: Any,
    historical_context_outbox_store: Any,
    historical_context_reply: Any,
) -> None:
    """Fail before a main X post when its context state cannot be persisted."""
    if (
        not historical_context_reply.get("enabled")
        or _HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON
    ):
        return
    if _get_historical_context_outbox_unavailable_reason():
        raise RuntimeError(
            "historical-context outbox is unavailable: "
            f"{_get_historical_context_outbox_unavailable_reason()}"
        )
    try:
        historical_context_outbox_store().verify_writable()
    except Exception as exc:
        _set_historical_context_outbox_unavailable_reason(
            f"{type(exc).__name__}: {exc}"
        )
        raise RuntimeError(
            "historical-context outbox failed the pre-post durability check"
        ) from exc
