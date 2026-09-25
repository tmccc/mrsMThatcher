"""Historical-context queue processing with current root dependencies.

The root supplies current runtime dependencies explicitly on each call. This
module performs no runtime work at import and retains no runtime authority.
The queue coordinator owns gates, recovery, claiming and loop decisions; local
helpers bind delivery callbacks and apply outcomes against the durable outbox.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from mrs_bot_core_contracts import BotState

from mrs_bot_historical_context_delivery import (
    _record_context_outbox_failure,
    _record_or_verify_proved_context_failure,
)
from mrs_bot_receipt_retirement import confirmed_context_outbox_matches_receipt


def enqueue_historical_context_obligation(
    receipt: dict,
    *,
    _HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON: Any,
    _set_historical_context_outbox_unavailable_reason: Any,
    canonical_context_obligation_quote_id: Any,
    historical_context_outbox_store: Any,
    historical_context_reply: Any,
    log: Any,
    log_event: Any,
) -> dict:
    """Persist an optional context obligation before releasing a main receipt."""
    store = historical_context_outbox_store()
    parent_post_id = str(receipt["post_id"])
    confirmed_epoch = int(receipt["quote_post_epoch"])
    if (
        not historical_context_reply.get("enabled")
        or _HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON
    ):
        reason = (
            "historical_context_runtime_unavailable"
            if _HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON
            else "historical_context_reply_disabled"
        )
        try:
            obligation = store.enqueue(
                parent_post_id,
                main_post_confirmed_epoch=confirmed_epoch,
                not_required_reason=reason,
            )
        except Exception as exc:
            _set_historical_context_outbox_unavailable_reason(
                f"{type(exc).__name__}: {exc}"
            )
            log.error(
                "Could not persist a not-required historical-context state; "
                "the confirmed main post remains independent. parent_post_id=%s "
                "reason=%s",
                parent_post_id,
                reason,
                exc_info=True,
            )
            log_event(
                "posting_transaction_state",
                parent_post_id=parent_post_id,
                main_post_state="main_post_confirmed",
                context_reply_state="context_reply_not_required",
                context_state_persisted=False,
                reason=reason,
            )
            return {
                "parent_post_id": parent_post_id,
                "main_post": {
                    "state": "main_post_confirmed",
                    "confirmed_epoch": confirmed_epoch,
                },
                "context_reply": {
                    "state": "context_reply_not_required",
                    "reason": reason,
                    "updated_epoch": confirmed_epoch,
                },
            }
    else:
        quote_text = str(receipt.get("quote_text", receipt.get("text") or ""))
        obligation = store.enqueue(
            parent_post_id,
            main_post_confirmed_epoch=confirmed_epoch,
            quote_id=canonical_context_obligation_quote_id(
                str(receipt["quote_hash"]),
                quote_text,
            ),
            quote_text=quote_text,
        )
    log_event(
        "posting_transaction_state",
        parent_post_id=parent_post_id,
        main_post_state=obligation["main_post"]["state"],
        context_reply_state=obligation["context_reply"]["state"],
    )
    return obligation


def _process_due_historical_context_obligations(
    *,
    store,
    parent_post_id: str | None = None,
    limit: int = 1,
    runtime_state: BotState | dict | None = None,
    historical_context_receipt_reconciliation_only: bool = False,
    historical_context_outbox_reconciliation_only: bool = False,
    ApiError: Any,
    HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE: Any,
    _HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON: Any,
    _get_historical_context_outbox_unavailable_reason: Any,
    _set_historical_context_outbox_unavailable_reason: Any,
    api_error_is_reply_not_allowed: Any,
    historical_context_receipt_path_present_or_unsafe: Any,
    in_api_cooldown: Any,
    inspect_transport_state: Any,
    journal_path_for_receipt: Any,
    lane_paused: Any,
    log: Any,
    log_event: Any,
    maybe_post_historical_context_reply: Any,
    now_epoch: Any,
    record_ambiguous_remote_post: Any,
    record_api_error: Any,
    recover_interrupted_historical_context_attempt: Any,
    save_state: Any,
) -> list[dict]:
    """Retry only auxiliary context work; never invoke the main-post path."""
    from historical_context_outbox import DUE_STATES

    if _HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON:
        log.debug(
            "Historical-context outbox processing deferred until a controlled "
            "restart because this process disabled the context runtime"
        )
        return []
    if _get_historical_context_outbox_unavailable_reason():
        log.debug(
            "Historical-context outbox processing is latched unavailable until "
            "a controlled restart. reason=%s",
            _get_historical_context_outbox_unavailable_reason(),
        )
        return []
    if lane_paused("disable_replies"):
        log.info(
            "Historical-context outbox processing deferred by reply runtime control"
        )
        return []
    if runtime_state is not None and in_api_cooldown(
        runtime_state,
        scope="write",
    ):
        log.info(
            "Historical-context outbox processing deferred by X write cooldown"
        )
        return []

    current = now_epoch()
    try:
        if parent_post_id is None:
            due = store.due(current, limit=max(1, int(limit)))
        else:
            requested = store.get(str(parent_post_id))
            requested_context = (
                requested.get("context_reply")
                if isinstance(requested, dict)
                else None
            )
            due = (
                [requested]
                if isinstance(requested_context, dict)
                and requested_context.get("state") in DUE_STATES
                and (
                    requested_context.get("state")
                    == "context_reply_attempting"
                    or int(requested_context.get("next_attempt_epoch") or 0)
                    <= current
                )
                else []
            )
    except Exception as exc:
        _set_historical_context_outbox_unavailable_reason(
            f"{type(exc).__name__}: {exc}"
        )
        log.critical(
            "Historical-context outbox unavailable; main-post lanes remain independent. "
            "error_type=%s error=%s",
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        log_event(
            "historical_context_outbox",
            status="unavailable",
            error_type=type(exc).__name__,
            reason=str(exc)[:500],
        )
        return []

    results: list[dict] = []
    for obligation in due:
        parent_id = str(obligation["parent_post_id"])
        context = obligation["context_reply"]
        if context.get("state") not in DUE_STATES:
            continue
        if context.get("state") == "context_reply_attempting":
            try:
                recovered = recover_interrupted_historical_context_attempt(
                    store,
                    obligation,
                    recovered_epoch=now_epoch(),
                    receipt_was_observed=(
                        historical_context_receipt_reconciliation_only
                    ),
                )
            except Exception as exc:
                if type(exc).__name__ == "AmbiguousContextReplyOutcome":
                    record_ambiguous_remote_post(
                        {
                            "text": str(getattr(exc, "reply_text", "") or ""),
                            "reply": {
                                "in_reply_to_tweet_id": str(
                                    getattr(exc, "parent_post_id", "")
                                    or parent_id
                                )
                            },
                        }
                    )
                _set_historical_context_outbox_unavailable_reason(
                    f"{type(exc).__name__}: {exc}"
                )
                log.critical(
                    "Could not reconcile an interrupted historical-context "
                    "attempt; no remote work was repeated. parent_post_id=%s",
                    parent_id,
                    exc_info=True,
                )
                results.append(
                    {
                        "parent_post_id": parent_id,
                        "status": "interrupted_attempt_recovery_failed",
                        "error_type": type(exc).__name__,
                    }
                )
                break
            log_event(
                "historical_context_obligation",
                **recovered,
            )
            results.append(recovered)
            if (
                historical_context_receipt_reconciliation_only
                or historical_context_outbox_reconciliation_only
            ):
                break
            continue
        if (
            historical_context_receipt_reconciliation_only
            or historical_context_outbox_reconciliation_only
            or historical_context_receipt_path_present_or_unsafe()
        ):
            log.critical(
                "A historical-context receipt may be reconciled only against "
                "its already-attempting outbox record; no new context attempt "
                "was claimed. parent_post_id=%s",
                parent_id,
            )
            results.append(
                {
                    "parent_post_id": parent_id,
                    "status": "blocked_by_unresolved_context_receipt",
                    "context_reply_state": str(context.get("state") or ""),
                }
            )
            break
        try:
            claimed = store.claim_attempt(
                parent_id,
                # The obligation was selected as due at ``current``.  A
                # subsequent CLOCK_REALTIME rollback must not invalidate that
                # already observed eligibility or strand the lane until a
                # restart; preserve the later of the two observations.
                started_epoch=max(current, now_epoch()),
            )
            context = claimed["context_reply"]
            attempt_number = int(context["attempt_count"])
        except Exception as exc:
            _set_historical_context_outbox_unavailable_reason(
                f"{type(exc).__name__}: {exc}"
            )
            log.critical(
                "Could not durably claim historical-context attempt; no context "
                "work was performed. parent_post_id=%s",
                parent_id,
                exc_info=True,
            )
            log_event(
                "historical_context_outbox",
                status="claim_failed",
                parent_post_id=parent_id,
                error_type=type(exc).__name__,
                reason=str(exc)[:500],
            )
            results.append(
                {
                    "parent_post_id": parent_id,
                    "status": "outbox_claim_failed",
                    "context_reply_state": str(context.get("state") or ""),
                }
            )
            break
        try:
            result = _post_claimed_context_attempt(
                store, parent_id, context, attempt_number,
                maybe_post_historical_context_reply=maybe_post_historical_context_reply,
                now_epoch=now_epoch,
            )
        except Exception as exc:
            state_name = _record_context_attempt_exception(
                store, parent_id, attempt_number, exc,
                HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE=HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
                _set_historical_context_outbox_unavailable_reason=(
                    _set_historical_context_outbox_unavailable_reason
                ),
                inspect_transport_state=inspect_transport_state,
                journal_path_for_receipt=journal_path_for_receipt,
                log=log,
                now_epoch=now_epoch,
                record_ambiguous_remote_post=record_ambiguous_remote_post,
            )
            log.error(
                "Confirmed main post remains successful; auxiliary historical-context "
                "reply failed. parent_post_id=%s state=%s error_type=%s error=%s",
                parent_id,
                state_name,
                type(exc).__name__,
                exc,
            )
            log_event(
                "historical_context_obligation",
                status="failed",
                parent_post_id=parent_id,
                attempt_number=attempt_number,
                context_reply_state=state_name,
                error_type=type(exc).__name__,
                reason=str(exc)[:500],
            )
            results.append(
                {
                    "parent_post_id": parent_id,
                    "status": "failed",
                    "context_reply_state": state_name,
                }
            )
            continue

        status = str(result.get("status") or "")
        error_status_code = result.get("error_status_code")
        error_request_method = result.get("error_request_method")
        error_request_path = result.get("error_request_path")
        failed_api_error = None
        if (
            status == "failed"
            and result.get("error_service") == "x"
            and type(error_status_code) is int
        ):
            failed_api_error = ApiError(
                str(result.get("error") or "historical-context X reply failed"),
                service="x",
                status_code=error_status_code,
                reset_epoch=(
                    result.get("error_reset_epoch")
                    if type(result.get("error_reset_epoch")) is int
                    else None
                ),
                # This worker's only X boundary is create_post(reply_to_id=...).
                # Older result schemas do not preserve request metadata, so
                # bind them to that known endpoint rather than guessing from
                # status alone.
                request_method=(
                    error_request_method
                    if isinstance(error_request_method, str)
                    else "POST"
                ),
                request_path=(
                    error_request_path
                    if isinstance(error_request_path, str)
                    else "/2/tweets"
                ),
            )
        if failed_api_error is not None and runtime_state is not None:
            try:
                record_api_error(
                    runtime_state,
                    failed_api_error,
                    "x",
                    scope="write",
                )
                save_state(runtime_state)
            except Exception:
                log.critical(
                    "Could not persist X write-cooldown metadata after a "
                    "historical-context reply failure",
                    exc_info=True,
                )
        try:
            updated = _persist_context_attempt_result(
                store, parent_id, context, attempt_number, result,
                status=status,
                failed_api_error=failed_api_error,
                api_error_is_reply_not_allowed=api_error_is_reply_not_allowed,
                now_epoch=now_epoch,
            )
        except Exception as exc:
            _set_historical_context_outbox_unavailable_reason(
                f"{type(exc).__name__}: {exc}"
            )
            log.critical(
                "Could not persist historical-context outbox outcome "
                "parent_post_id=%s; main post remains confirmed",
                parent_id,
                exc_info=True,
            )
            results.append(
                {
                    "parent_post_id": parent_id,
                    "status": "outbox_persistence_failed",
                    "error_type": type(exc).__name__,
                }
            )
            continue
        state_name = str(updated["context_reply"]["state"])
        log_event(
            "historical_context_obligation",
            status=status,
            parent_post_id=parent_id,
            attempt_number=attempt_number,
            context_reply_state=state_name,
        )
        results.append(
            {
                "parent_post_id": parent_id,
                "status": status,
                "context_reply_state": state_name,
            }
        )
    return results


def _post_claimed_context_attempt(
    store,
    parent_id: str,
    context: dict,
    attempt_number: int,
    *,
    maybe_post_historical_context_reply: Any,
    now_epoch: Any,
) -> dict:
    """Send one claim with callbacks bound to its parent and attempt identity."""
    return maybe_post_historical_context_reply(
        quote_hash=str(context["quote_id"]),
        quote_text=str(context["quote_text"]),
        parent_post_id=parent_id,
        on_source_receipt_published=(
            lambda source_sha256,
            source_attempt_number,
            parent_id=parent_id,
            attempt_number=attempt_number: (
                store.bind_attempt_source_receipt(
                    parent_id,
                    attempt_number=attempt_number,
                    source_receipt_sha256=source_sha256,
                    source_receipt_attempt_number=(
                        source_attempt_number
                    ),
                )
            )
        ),
        on_remote_transaction_started=(
            lambda parent_id=parent_id, attempt_number=attempt_number: (
                store.mark_remote_transaction_started(
                    parent_id,
                    attempt_number=attempt_number,
                )
            )
        ),
        on_definite_non_success=(
            lambda error,
            parent_id=parent_id,
            attempt_number=attempt_number: (
                _record_context_outbox_failure(
                    store,
                    parent_post_id=parent_id,
                    attempt_number=attempt_number,
                    error=error,
                    failed_epoch=now_epoch(),
                    force_terminal=attempt_number >= store.max_attempts,
                    proved_remote_non_success=True,
                )
            )
        ),
        on_confirmed_receipt=(
            lambda confirmed_receipt,
            confirmation_epoch,
            parent_id=parent_id,
            attempt_number=attempt_number: (
                store.record_confirmed(
                    parent_id,
                    attempt_number=attempt_number,
                    reply_post_id=confirmed_receipt["reply_post_id"],
                    confirmed_epoch=confirmation_epoch,
                )
            )
        ),
    )


def _record_context_attempt_exception(
    store,
    parent_id: str,
    attempt_number: int,
    exc: Exception,
    *,
    HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE: Any,
    _set_historical_context_outbox_unavailable_reason: Any,
    inspect_transport_state: Any,
    journal_path_for_receipt: Any,
    log: Any,
    now_epoch: Any,
    record_ambiguous_remote_post: Any,
) -> str:
    """Record proved failures while preserving unproved or confirmed transport.

    Re-read the durable outbox after delivery: the original claim cannot say
    whether a failed operation already started or confirmed a remote write.
    """
    if type(exc).__name__ == "AmbiguousContextReplyOutcome":
        record_ambiguous_remote_post(
            {
                "text": str(getattr(exc, "reply_text", "") or ""),
                "reply": {
                    "in_reply_to_tweet_id": str(
                        getattr(exc, "parent_post_id", "") or parent_id
                    )
                },
            }
        )
        state_name = "ambiguous_remote_outcome"
    elif type(exc).__name__ == "DefiniteContextReplyLocalPersistenceError":
        try:
            state_name = _record_or_verify_proved_context_failure(
                store,
                parent_post_id=parent_id,
                attempt_number=attempt_number,
                error=exc,
                failed_epoch=now_epoch(),
            )
        except Exception:
            _set_historical_context_outbox_unavailable_reason(
                "proved context outcome persistence failed"
            )
            log.critical(
                "Could not preserve the exact proved historical-context "
                "failure parent_post_id=%s",
                parent_id,
                exc_info=True,
            )
            state_name = "outbox_persistence_failed"
    else:
        try:
            current_obligation = store.get(parent_id)
            current_context = (
                current_obligation.get("context_reply")
                if isinstance(current_obligation, dict)
                else None
            )
            remote_phase_is_unproved = bool(
                isinstance(current_context, dict)
                and current_context.get("state")
                == "context_reply_attempting"
                and current_context.get("attempt_count")
                == attempt_number
                and current_context.get(
                    "remote_transaction_started"
                )
                is not False
            )
            confirmed_outbox_outcome = bool(
                isinstance(current_context, dict)
                and current_context.get("state")
                == "context_reply_confirmed"
            )
            if remote_phase_is_unproved or confirmed_outbox_outcome:
                # Once the durable phase says transport may have
                # started, an arbitrary local exception cannot prove a
                # remote non-success.  The same applies after the
                # outbox already holds the confirmed identity. Preserve
                # that exact outcome and the independent receipt/journal
                # barriers for local or manual reconciliation.
                state_name = (
                    "confirmed_local_reconciliation_pending"
                    if confirmed_outbox_outcome
                    or inspect_transport_state(
                        journal_path_for_receipt(
                            HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
                        )
                    ).classification == "confirmed_pair"
                    else "remote_outcome_reconciliation_pending"
                )
            else:
                state_name = _record_context_outbox_failure(
                    store,
                    parent_post_id=parent_id,
                    attempt_number=attempt_number,
                    error=exc,
                    failed_epoch=now_epoch(),
                )
        except Exception:
            _set_historical_context_outbox_unavailable_reason(
                "outbox outcome persistence failed"
            )
            log.critical(
                "Could not persist historical-context outbox failure "
                "parent_post_id=%s; main post remains confirmed",
                parent_id,
                exc_info=True,
            )
            state_name = "outbox_persistence_failed"
    return state_name


def _persist_context_attempt_result(
    store,
    parent_id: str,
    context: dict,
    attempt_number: int,
    result: dict,
    *,
    status: str,
    failed_api_error: Exception | None,
    api_error_is_reply_not_allowed: Any,
    now_epoch: Any,
) -> dict:
    """Apply a returned outcome only when the durable claim permits it.

    Completed sends require their source-bound outbox confirmation; skipped
    work after an earlier failure must remain a terminal failed obligation.
    """
    durable_outbox_failure_state = result.get(
        "durable_outbox_failure_state"
    )
    if status == "failed" and type(durable_outbox_failure_state) is str:
        updated = store.get(parent_id)
        updated_context = (
            updated.get("context_reply")
            if isinstance(updated, dict)
            else None
        )
        if (
            not isinstance(updated_context, dict)
            or updated_context.get("state")
            != durable_outbox_failure_state
        ):
            raise RuntimeError(
                "durable context outbox failure state changed after "
                "source-receipt retirement"
            )
    elif status in {"completed", "already_completed"}:
        reply_post_id = str(result.get("reply_post_id") or "")
        current_after_post = store.get(parent_id)
        current_context_after_post = (
            current_after_post.get("context_reply")
            if isinstance(current_after_post, dict)
            else None
        )
        if (
            isinstance(current_context_after_post, dict)
            and current_context_after_post.get("state")
            == "context_reply_confirmed"
            and current_context_after_post.get("reply_post_id")
            == reply_post_id
            and (
                status == "already_completed"
                or confirmed_context_outbox_matches_receipt(
                    current_context_after_post,
                    result,
                )
            )
        ):
            updated = current_after_post
        elif status == "already_completed":
            updated = store.record_confirmed(
                parent_id,
                attempt_number=attempt_number,
                reply_post_id=reply_post_id,
                confirmed_epoch=now_epoch(),
            )
        else:
            raise RuntimeError(
                "completed historical-context reply lacks its durable "
                "outbox confirmation"
            )
    elif status in {
        "disabled",
        "skipped_no_completed_packet",
        "skipped_incomplete_research",
        "skipped_future_policy",
        "skipped_unformattable_packet",
    }:
        if (
            int(context.get("attempt_count") or 0) == 1
            and "previous_failure" not in context
        ):
            updated = store.mark_not_required(
                parent_id,
                reason=status,
                decided_epoch=now_epoch(),
            )
        else:
            state_name = _record_context_outbox_failure(
                store,
                parent_post_id=parent_id,
                attempt_number=attempt_number,
                error=f"{status} after a prior retryable context attempt",
                failed_epoch=now_epoch(),
                force_terminal=True,
            )
            updated = store.get(parent_id)
    elif status == "failed_terminal":
        state_name = _record_context_outbox_failure(
            store,
            parent_post_id=parent_id,
            attempt_number=attempt_number,
            error=str(result.get("reason") or status),
            failed_epoch=now_epoch(),
            force_terminal=True,
        )
        updated = store.get(parent_id)
    else:
        state_name = _record_context_outbox_failure(
            store,
            parent_post_id=parent_id,
            attempt_number=attempt_number,
            error=str(result.get("error") or f"unexpected context status: {status}"),
            failed_epoch=now_epoch(),
            force_terminal=(
                failed_api_error is not None
                and api_error_is_reply_not_allowed(failed_api_error)
            ),
        )
        updated = store.get(parent_id)
    return updated


def safely_process_due_historical_context_obligations(
    *,
    parent_post_id: str | None = None,
    limit: int = 1,
    runtime_state: BotState | dict | None = None,
    _set_historical_context_outbox_unavailable_reason: Any,
    log: Any,
    log_event: Any,
    process_due_historical_context_obligations: Any,
) -> list[dict]:
    """Isolate auxiliary context-worker faults from confirmed main-post lanes."""
    try:
        return process_due_historical_context_obligations(
            parent_post_id=parent_post_id,
            limit=limit,
            runtime_state=runtime_state,
        )
    except Exception as exc:
        _set_historical_context_outbox_unavailable_reason(
            f"{type(exc).__name__}: {exc}"
        )
        log.critical(
            "Historical-context auxiliary worker failed at its isolation "
            "boundary; confirmed main posts remain successful and unrelated "
            "lanes remain available. parent_post_id=%s error_type=%s error=%s",
            parent_post_id or "",
            type(exc).__name__,
            exc,
            exc_info=True,
        )
        try:
            log_event(
                "historical_context_outbox",
                status="worker_failed_isolated",
                parent_post_id=str(parent_post_id or ""),
                error_type=type(exc).__name__,
                reason=str(exc)[:500],
                main_post_success_preserved=True,
            )
        except Exception:
            log.critical(
                "Could not emit the isolated historical-context worker event",
                exc_info=True,
            )
        return [
            {
                "parent_post_id": str(parent_post_id or ""),
                "status": "worker_failed_isolated",
                "error_type": type(exc).__name__,
            }
        ]
