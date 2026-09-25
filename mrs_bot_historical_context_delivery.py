"""Historical-context delivery and interrupted-attempt recovery.

The root supplies current runtime dependencies explicitly on each call. This
module performs no runtime work at import and retains no runtime authority.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from mrs_bot_core_contracts import BotState


def historical_context_formatter_options(config: dict) -> dict[str, int | bool]:
    """Project the same ordered options for semantic review and public formatting."""
    return {
        "maximum_length": int(config["maximum_length"]),
        "include_meaning": bool(config["include_meaning"]),
        "include_source": bool(config["include_source"]),
        "include_verification": bool(config["include_verification"]),
    }


def context_reply_research_is_complete(packet: dict[str, Any]) -> bool:
    """Require supported attribution and a usable source before public delivery.

    Reuse the formatter's evidence classification independently of section-display
    settings. Keep this delivery policy here: historical evidence pins the
    formatter's source bytes, and offline rendering must remain available for
    incomplete packets. A source need not have a public URL, and an unknown
    occasion alone does not make otherwise supported research incomplete.
    """
    from historical_context_formatter import (
        _audited_confidence,
        _audited_public_sources,
        _public_verification_label,
        _v2_clean,
    )

    sources = _audited_public_sources(packet)
    if not any(
        _v2_clean(source.get("title"))
        and source.get("source_type") != "unavailable"
        for source in sources
    ):
        return False
    return _public_verification_label(
        packet, _audited_confidence(packet), sources,
    ) != "Research incomplete"


def maybe_post_historical_context_reply(
    *,
    quote_hash: str,
    quote_text: str,
    parent_post_id: str,
    dry_run: bool = False,
    on_source_receipt_published: Callable[[str, int], None] | None = None,
    on_remote_transaction_started: Callable[[], None] | None = None,
    on_definite_non_success: Callable[[BaseException], str] | None = None,
    on_confirmed_receipt: Callable[[dict, int], None] | None = None,
    HISTORICAL_CONTEXT_RESEARCH_DIR: Any,
    RemoteOperationsPaused: Any,
    _HISTORICAL_CONTEXT_CORPUS_SNAPSHOT: Any,
    _HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON: Any,
    _HISTORICAL_CONTEXT_SEMANTIC_GATE: Any,
    begin_confirmed_post_sigint_deferral: Any,
    block_if_ambiguous_remote_post: Any,
    create_post: Any,
    emit_historical_context_reply_posted: Any,
    end_confirmed_post_sigint_deferral: Any,
    historical_context_reply: Any,
    historical_context_reply_store: Any,
    initialise_historical_context_semantic_gate: Any,
    log: Any,
    log_event: Any,
    now_epoch: Any,
    re: Any,
) -> dict:
    """Post an optional canonical context reply without affecting the main post."""
    if not dry_run:
        block_if_ambiguous_remote_post()
    try:
        from historical_context_formatter import (
            HistoricalContextReplyStore,
            format_context_reply_public,
            load_and_validate_corpus,
            packet_for_posted_quote,
            x_weighted_length,
        )

        store = historical_context_reply_store()
        if not dry_run:
            # Reconcile before every policy/configuration exit. A durable
            # receipt describes an earlier remote attempt and must not be
            # hidden merely because the lane is now disabled, the packet is
            # unavailable, or the current quote is blocked by policy.
            store.reconcile_receipt()
        if _HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON and not dry_run:
            return {
                "status": "failed_terminal",
                "reason": _HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON,
            }
        if not historical_context_reply.get("enabled") and not dry_run:
            return {"status": "disabled"}

        if _HISTORICAL_CONTEXT_CORPUS_SNAPSHOT is None:
            packets, unresolved = load_and_validate_corpus(
                HISTORICAL_CONTEXT_RESEARCH_DIR,
                require_source_role_audit=True,
            )
        else:
            packets, unresolved = _HISTORICAL_CONTEXT_CORPUS_SNAPSHOT
        packet = packet_for_posted_quote(packets, unresolved, quote_hash, quote_text)
        if packet is None:
            log.warning("No completed canonical research packet for quote_hash=%s; context reply skipped", quote_hash)
            log_event(
                "historical_context_reply", status="skipped_no_completed_packet",
                parent_post_id=str(parent_post_id), quote_id=str(quote_hash),
                reason="no_completed_canonical_packet",
            )
            return {"status": "skipped_no_completed_packet"}

        gate = _HISTORICAL_CONTEXT_SEMANTIC_GATE
        if gate is None:
            gate = initialise_historical_context_semantic_gate(packets)

        gate_disposition = gate.disposition(packet["quote_id"])
        reviewed_gate_disposition = gate.reviewed_disposition(packet["quote_id"])
        if not gate.available or gate_disposition is not None:
            reason = (
                "semantic_review_gate_unavailable"
                if not gate.available
                else "open_semantic_review"
            )
            log.warning(
                "Historical context reply blocked by reviewed semantic gate. "
                "quote_id=%s reason=%s disposition=%s ledger_sha256=%s",
                packet["quote_id"],
                reason,
                gate_disposition or "unavailable",
                gate.ledger_sha256 or "unavailable",
            )
            log_event(
                "historical_context_reply",
                status="skipped_future_policy",
                parent_post_id=str(parent_post_id),
                quote_id=str(packet["quote_id"]),
                reason=reason,
                semantic_review_disposition=gate_disposition,
                semantic_review_ledger_sha256=gate.ledger_sha256,
                semantic_review_projection_sha256=gate.projection_sha256,
            )
            return {
                "status": "skipped_future_policy",
                "quote_id": str(packet["quote_id"]),
                "reason": reason,
                "semantic_review_disposition": gate_disposition,
                "semantic_review_ledger_sha256": gate.ledger_sha256,
                "semantic_review_projection_sha256": gate.projection_sha256,
            }
        if not context_reply_research_is_complete(packet):
            log.info(
                "Historical research is incomplete for quote_id=%s; context reply skipped",
                packet["quote_id"],
            )
            log_event(
                "historical_context_reply", status="skipped_incomplete_research",
                parent_post_id=str(parent_post_id), quote_id=str(packet["quote_id"]),
                reason="incomplete_historical_research",
            )
            return {
                "status": "skipped_incomplete_research",
                "quote_id": str(packet["quote_id"]),
                "reason": "incomplete_historical_research",
            }
        formatted = format_context_reply_public(
            packet,
            **historical_context_formatter_options(historical_context_reply),
        )
        if formatted is None:
            log.warning("Canonical packet could not produce a safe context reply quote_id=%s", packet["quote_id"])
            log_event(
                "historical_context_reply", status="skipped_unformattable_packet",
                parent_post_id=str(parent_post_id), quote_id=str(packet["quote_id"]),
                reason="unformattable_canonical_packet",
            )
            return {"status": "skipped_unformattable_packet"}
        if dry_run:
            print(formatted["text"])
            print(
                f"Character count: raw={formatted['raw_character_count']} "
                f"x_weighted={formatted['character_count']}/{formatted['maximum_length']}"
            )
        formatter_metadata = {
            "formatter_version": formatted["formatter_version"],
            "template_variant": formatted["template_variant"],
            "meaning_included": formatted["meaning_included"],
            "meaning_decision_reason": formatted["meaning_decision_reason"],
            "raw_character_count": formatted["raw_character_count"],
            "weighted_character_count": formatted["weighted_character_count"],
            "verification_label": formatted["verification_label"],
            "source_class": formatted["source_class"],
            "historical_confidence": formatted["historical_confidence"],
            "confidence_dimensions": formatted["confidence_dimensions"],
            "source_role_audit_version": formatted["source_role_audit_version"],
            "rendering_mode": formatted["rendering_mode"],
            "shortening_applied": formatted["shortening_applied"],
        }
        historical_sigint_guard = (
            None if dry_run else begin_confirmed_post_sigint_deferral()
        )
        try:
            result = store.post(
                parent_post_id=str(parent_post_id),
                quote_id=str(packet["quote_id"]),
                reply_text=str(formatted["text"]),
                create_post=create_post,
                now_epoch=now_epoch,
                dry_run=dry_run,
                formatter_metadata=formatter_metadata,
                # The store retires its journal only after completed history is
                # durable, so the prepared source-removal guard is always safe
                # to resume in a later process.
                on_confirmed_receipt=(
                    None if dry_run else on_confirmed_receipt
                ),
                remote_failure_is_definite_non_success=(
                    lambda error: isinstance(error, RemoteOperationsPaused)
                ),
                require_confirmed_transport=not dry_run,
                on_source_receipt_published=(
                    None if dry_run else on_source_receipt_published
                ),
                on_remote_transaction_started=(
                    None if dry_run else on_remote_transaction_started
                ),
                on_definite_non_success=(
                    None if dry_run else on_definite_non_success
                ),
            )
        finally:
            # Every post-start exit retains either the sending receipt, its
            # transport journal, an exact retirement guard, or completed
            # history.  A local pre-transport pause is also definite.
            end_confirmed_post_sigint_deferral(historical_sigint_guard)
        event_text = str(result.get("reply_text") or formatted["text"])
        event_metadata = formatter_metadata
        if result.get("status") == "already_completed":
            stored_metadata = result.get("formatter_metadata")
            if isinstance(stored_metadata, dict):
                event_metadata = stored_metadata
            else:
                # Receipts created before formatter metadata was introduced are v1. The
                # existing reply remains authoritative and must not be relabelled as v2.
                event_metadata = {
                    "formatter_version": "historical_context_reply_schema_v1",
                    "template_variant": "legacy_v1",
                    "meaning_included": "\n\nMeaning:" in event_text,
                    "meaning_decision_reason": "Legacy formatter metadata unavailable.",
                    "raw_character_count": len(event_text),
                    "weighted_character_count": x_weighted_length(event_text),
                    "verification_label": formatted["verification_label"],
                    "source_class": formatted["source_class"],
                    "historical_confidence": formatted["historical_confidence"],
                    "shortening_applied": False,
                }
        log_event(
            "historical_context_reply",
            status=result.get("status"),
            parent_post_id=str(parent_post_id),
            quote_id=str(packet["quote_id"]),
            character_count=event_metadata["weighted_character_count"],
            raw_character_count=event_metadata["raw_character_count"],
            verification_label=event_metadata["verification_label"],
            source_class=event_metadata["source_class"],
            historical_confidence=event_metadata["historical_confidence"],
            confidence_dimensions=event_metadata.get("confidence_dimensions"),
            source_role_audit_version=event_metadata.get("source_role_audit_version"),
            rendering_mode=event_metadata.get("rendering_mode"),
            shortening_applied=event_metadata["shortening_applied"],
            meaning_omitted=not event_metadata["meaning_included"],
            meaning_included=event_metadata["meaning_included"],
            meaning_decision_reason=event_metadata["meaning_decision_reason"],
            source_omitted="Source:" not in event_text and "Source —" not in event_text,
            verification_omitted="Verification:" not in event_text and "Verification —" not in event_text,
            formatter_version=event_metadata["formatter_version"],
            template_variant=event_metadata["template_variant"],
            semantic_review_disposition=(
                reviewed_gate_disposition or "unavailable"
            ),
            semantic_review_ledger_sha256=gate.ledger_sha256,
            semantic_review_projection_sha256=gate.projection_sha256,
            reply_preview=event_text[:160],
            reason="post_failed" if result.get("status") == "failed" else "",
        )
        if (
            result.get("status") in {"completed", "already_completed"}
            and re.fullmatch(r"\d{1,30}", str(result.get("reply_post_id") or ""))
        ):
            emit_historical_context_reply_posted(
                parent_post_id=parent_post_id,
                reply_post_id=result["reply_post_id"],
                reply_text=event_text,
                quote_id=packet["quote_id"],
            )
        return {**result, "formatted": formatted}
    except Exception as exc:
        if type(exc).__name__ == "AmbiguousContextReplyOutcome":
            log_event(
                "historical_context_reply", status="failed",
                parent_post_id=str(parent_post_id), quote_id=str(quote_hash),
                reason="ambiguous_outcome",
            )
            raise
        # The caller owns an independent durable outbox obligation. Propagate so it can
        # record a retryable or terminal context-only outcome without relabelling the
        # already-confirmed main post.
        log.error(
            "Historical context reply failed independently for parent_post_id=%s quote_hash=%s: %s",
            parent_post_id,
            quote_hash,
            exc,
            exc_info=True,
        )
        log_event(
            "historical_context_reply", status="failed",
            parent_post_id=str(parent_post_id), quote_id=str(quote_hash),
            reason=type(exc).__name__,
        )
        raise


def historical_context_reply_store(
    *,
    allow_missing_history: bool = False,
    HISTORICAL_CONTEXT_REPLY_HISTORY_FILE: Any,
    HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE: Any,
    TEST_MODE: Any,
    latch_source_receipt_retirement_uncertainty: Any,
    transaction_mutation_authority: Any,
):
    """Return the reply store with production history-loss protection."""
    from historical_context_formatter import HistoricalContextReplyStore

    return HistoricalContextReplyStore(
        HISTORICAL_CONTEXT_REPLY_HISTORY_FILE,
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
        mutation_authority_provider=transaction_mutation_authority,
        retirement_uncertainty_callback=(
            latch_source_receipt_retirement_uncertainty
        ),
        require_existing_history=(
            not TEST_MODE and not allow_missing_history
        ),
    )


def historical_context_outbox_store(
    *,
    HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE: Any,
    TEST_MODE: Any,
):
    """Return the durable store for auxiliary context-reply obligations."""
    from historical_context_outbox import HistoricalContextOutbox

    return HistoricalContextOutbox(
        HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE,
        # Production installation creates this authority explicitly.  Focused
        # test fixtures may still construct isolated stores lazily; production
        # must never reinterpret later disappearance as a fresh empty outbox.
        require_existing=not TEST_MODE,
    )


def canonical_context_obligation_quote_id(
    quote_hash: str,
    quote_text: str,
    *,
    _HISTORICAL_CONTEXT_CORPUS_SNAPSHOT: Any,
) -> str:
    """Resolve a stable canonical packet identity without fuzzy matching."""
    if _HISTORICAL_CONTEXT_CORPUS_SNAPSHOT is None:
        return str(quote_hash)
    from historical_context_formatter import packet_for_posted_quote

    packets, unresolved = _HISTORICAL_CONTEXT_CORPUS_SNAPSHOT
    packet = packet_for_posted_quote(
        packets,
        unresolved,
        str(quote_hash),
        str(quote_text),
    )
    return str(packet["quote_id"]) if packet is not None else str(quote_hash)


def _record_context_outbox_failure(
    store,
    *,
    parent_post_id: str,
    attempt_number: int,
    error: BaseException | str,
    failed_epoch: int,
    force_terminal: bool = False,
    proved_remote_non_success: bool = False,
) -> str:
    """Record one bounded context failure and return its durable state."""
    if force_terminal or attempt_number >= store.max_attempts:
        obligation = store.record_terminal_failure(
            parent_post_id,
            attempt_number=attempt_number,
            error=error,
            failed_epoch=failed_epoch,
            proved_remote_non_success=proved_remote_non_success,
        )
    else:
        obligation = store.record_retryable_failure(
            parent_post_id,
            attempt_number=attempt_number,
            error=error,
            failed_epoch=failed_epoch,
            proved_remote_non_success=proved_remote_non_success,
        )
    return str(obligation["context_reply"]["state"])


def _record_or_verify_proved_context_failure(
    store,
    *,
    parent_post_id: str,
    attempt_number: int,
    error,
    failed_epoch: int,
) -> str:
    """Preserve or verify one exact proved-non-success outbox outcome."""

    source_sha256 = getattr(error, "source_receipt_sha256", None)
    source_attempt = getattr(error, "source_receipt_attempt_number", None)
    remote_error = getattr(error, "remote_error", None)
    if (
        type(source_sha256) is not str
        or not re.fullmatch(r"[0-9a-f]{64}", source_sha256)
        or type(source_attempt) is not int
        or source_attempt < 1
        or not isinstance(remote_error, BaseException)
    ):
        raise RuntimeError(
            "definite historical-context persistence error lacks exact source proof"
        )
    obligation = store.get(parent_post_id)
    context = (
        obligation.get("context_reply") if isinstance(obligation, dict) else None
    )
    if not isinstance(context, dict) or context.get("attempt_count") != attempt_number:
        raise RuntimeError("historical-context outbox attempt changed after failure")
    if context.get("state") in {
        "context_reply_failed_retryable",
        "context_reply_failed_terminal",
    }:
        failure = context.get("failure")
        if not (
            isinstance(failure, dict)
            and failure.get("remote_outcome") == "proved_non_success"
            and failure.get("source_receipt_sha256") == source_sha256
            and failure.get("source_receipt_attempt_number") == source_attempt
        ):
            raise RuntimeError(
                "durable historical-context failure does not match source proof"
            )
        return str(context["state"])
    if (
        context.get("state") != "context_reply_attempting"
        or context.get("source_receipt_sha256") != source_sha256
        or context.get("source_receipt_attempt_number") != source_attempt
    ):
        raise RuntimeError(
            "historical-context source binding changed before failure persistence"
        )
    return _record_context_outbox_failure(
        store,
        parent_post_id=parent_post_id,
        attempt_number=attempt_number,
        error=remote_error,
        failed_epoch=failed_epoch,
        force_terminal=attempt_number >= store.max_attempts,
        proved_remote_non_success=True,
    )


def recover_interrupted_historical_context_attempt(
    store,
    obligation: dict,
    *,
    recovered_epoch: int,
    receipt_was_observed: bool = False,
    HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE: Any,
    emit_historical_context_history_observation: Any,
    hashlib: Any,
    historical_context_reply_store: Any,
    journal_path_for_receipt: Any,
    re: Any,
    transport_journal_is_blocking: Any,
) -> dict:
    """Resolve a durable interrupted claim without repeating its remote work."""
    from historical_context_formatter import (
        AmbiguousContextReplyOutcome,
        HistoricalContextReplyStore,
    )

    parent_id = str(obligation["parent_post_id"])
    context = obligation["context_reply"]
    if context.get("state") != "context_reply_attempting":
        raise ValueError("only an interrupted attempting state can be recovered")
    attempt_number = int(context["attempt_count"])
    context_store = historical_context_reply_store()
    transport_journal_path = journal_path_for_receipt(
        HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE
    )
    journal_was_observed = transport_journal_is_blocking(
        transport_journal_path
    )
    bound_source_sha256 = context.get("source_receipt_sha256")
    bound_source_attempt = context.get("source_receipt_attempt_number")
    source_binding_present = bool(
        type(bound_source_sha256) is str
        and re.fullmatch(r"[0-9a-f]{64}", bound_source_sha256)
        and type(bound_source_attempt) is int
        and bound_source_attempt >= 1
    )
    loaded_before_reconciliation = context_store._load_receipt_safely()
    exact_current_sending_receipt = False
    if loaded_before_reconciliation is not None:
        source_receipt, source_receipt_bytes = loaded_before_reconciliation
        if HistoricalContextReplyStore._valid_sending_receipt(source_receipt):
            observed_source_sha256 = hashlib.sha256(
                source_receipt_bytes
            ).hexdigest()
            observed_source_attempt = source_receipt["attempt_number"]
            if (
                not source_binding_present
                and context.get("remote_transaction_started") is False
                and not journal_was_observed
                and not transport_journal_is_blocking(
                    transport_journal_path
                )
            ):
                history = context_store.history()
                previous = history["items"].get(parent_id)
                expected_source_attempt = (
                    int(previous.get("attempt_count", 0)) + 1
                    if isinstance(previous, dict)
                    else 1
                )
                if (
                    (
                        isinstance(previous, dict)
                        and previous.get("status") != "failed"
                    )
                    or
                    source_receipt.get("parent_post_id") != parent_id
                    or source_receipt.get("quote_id")
                    != context.get("quote_id")
                    or source_receipt.get("attempt_number")
                    != expected_source_attempt
                ):
                    raise AmbiguousContextReplyOutcome(
                        "unbound historical-context source receipt does not "
                        "match the exact pre-transport attempt",
                        parent_post_id=parent_id,
                        reply_text=str(source_receipt.get("reply_text") or ""),
                    )
                rebound = store.bind_attempt_source_receipt(
                    parent_id,
                    attempt_number=attempt_number,
                    source_receipt_sha256=observed_source_sha256,
                    source_receipt_attempt_number=observed_source_attempt,
                )
                context = rebound["context_reply"]
                bound_source_sha256 = observed_source_sha256
                bound_source_attempt = observed_source_attempt
                source_binding_present = True
            exact_current_sending_receipt = bool(
                source_binding_present
                and bound_source_sha256 == observed_source_sha256
                and bound_source_attempt == observed_source_attempt
            )
        elif HistoricalContextReplyStore._valid_receipt(source_receipt):
            observed_source_sha256 = source_receipt.get(
                "source_receipt_sha256"
            )
            observed_source_attempt = source_receipt.get("attempt_number")
        else:
            raise RuntimeError("invalid historical context reply receipt")
        if (
            not source_binding_present
            or bound_source_sha256 != observed_source_sha256
            or bound_source_attempt != observed_source_attempt
        ):
            raise AmbiguousContextReplyOutcome(
                "historical-context receipt does not match the exact current "
                "outbox source binding",
                parent_post_id=parent_id,
                reply_text=str(source_receipt.get("reply_text") or ""),
            )
    journal_is_blocking = transport_journal_is_blocking(
        transport_journal_path
    )
    if (
        context.get("remote_transaction_started") is False
        and exact_current_sending_receipt
        and not journal_was_observed
        and not journal_is_blocking
    ):
        # The exact source receipt exists, but the durable outbox still proves
        # that journal arming and transport never began.  Persist that proof
        # before writing failed history and retiring only this receipt.  Calling
        # generic receipt reconciliation first would incorrectly classify the
        # safely pre-transport crash as an ambiguous remote outcome.
        state_name = _record_context_outbox_failure(
            store,
            parent_post_id=parent_id,
            attempt_number=attempt_number,
            error="context attempt was interrupted before remote transaction start",
            failed_epoch=recovered_epoch,
            force_terminal=attempt_number >= store.max_attempts,
            proved_remote_non_success=True,
        )
        updated = store.get(parent_id)
        if updated is None or updated["context_reply"]["state"] != state_name:
            raise RuntimeError("pre-remote context recovery was not durable")
        context_store.ensure_proved_failure_history_from_outbox(
            updated["context_reply"]
        )
        if context_store.reconcile_receipt_disposition() != "definite_failure":
            raise RuntimeError(
                "exact pre-remote context receipt changed before retirement"
            )
        return {
            "parent_post_id": parent_id,
            "status": "recovered_pre_remote_interruption",
            "context_reply_state": state_name,
            "attempt_number": attempt_number,
            "remote_work_repeated": False,
        }
    # The sending receipt is the authoritative ambiguity barrier.  It must be
    # reconciled (confirmed, definitely failed, or left ambiguous) before an
    # interrupted outbox claim can be downgraded or retried.
    receipt_disposition = context_store.reconcile_receipt_disposition(
        retain_definite_failure_receipt=True,
        preloaded_receipt=loaded_before_reconciliation,
    )
    journal_is_blocking = transport_journal_is_blocking(
        transport_journal_path
    )
    history = context_store.history()
    previous = history["items"].get(parent_id)
    previous_source_matches = bool(
        source_binding_present
        and isinstance(previous, dict)
        and previous.get("source_receipt_sha256") == bound_source_sha256
        and (
            previous.get("source_receipt_attempt_number")
            if previous.get("status") == "failed"
            else previous.get("attempt_number")
        )
        == bound_source_attempt
    )
    if (
        receipt_was_observed
        and not (
            isinstance(previous, dict)
            and previous.get("quote_id") == context["quote_id"]
            and previous.get("status") in {"completed", "failed"}
            and previous_source_matches
        )
    ):
        raise AmbiguousContextReplyOutcome(
            "an observed historical-context receipt disappeared before its "
            "outcome could be reconciled",
            parent_post_id=parent_id,
            reply_text=str(context.get("reply_text") or ""),
        )
    if isinstance(previous, dict) and previous.get("quote_id") != context["quote_id"]:
        raise RuntimeError(
            "context history identity conflicts with interrupted outbox attempt"
        )
    if (
        context.get("remote_transaction_started") is False
        and receipt_disposition == "absent"
        and not source_binding_present
        and not receipt_was_observed
        and not journal_was_observed
        and not journal_is_blocking
        and isinstance(previous, dict)
        and previous.get("status") == "completed"
        and previous.get("quote_id") == context["quote_id"]
    ):
        # store.post() returns ``already_completed`` before publishing a new
        # source receipt.  A crash before the worker copies that durable result
        # into its newly claimed outbox must recover the existing confirmed
        # identity, not recast it as a terminal pre-transport failure.
        updated = store.record_confirmed(
            parent_id,
            attempt_number=attempt_number,
            reply_post_id=str(previous["reply_post_id"]),
            confirmed_epoch=recovered_epoch,
        )
        emit_historical_context_history_observation(previous)
        return {
            "parent_post_id": parent_id,
            "status": "recovered_confirmed_history",
            "context_reply_state": str(updated["context_reply"]["state"]),
            "attempt_number": attempt_number,
            "remote_work_repeated": False,
        }
    proved_pre_remote = bool(
        context.get("remote_transaction_started") is False
        and receipt_disposition == "absent"
        and not previous_source_matches
        and not journal_was_observed
        and not journal_is_blocking
    )
    if proved_pre_remote:
        state_name = _record_context_outbox_failure(
            store,
            parent_post_id=parent_id,
            attempt_number=attempt_number,
            error="context attempt was interrupted before remote transaction start",
            failed_epoch=recovered_epoch,
            force_terminal=attempt_number >= store.max_attempts,
        )
        updated = store.get(parent_id)
        if updated is None or updated["context_reply"]["state"] != state_name:
            raise RuntimeError("pre-remote context recovery was not durable")
        return {
            "parent_post_id": parent_id,
            "status": "recovered_pre_remote_interruption",
            "context_reply_state": state_name,
            "attempt_number": attempt_number,
            "remote_work_repeated": False,
        }
    exact_current_failure = bool(
        receipt_disposition == "definite_failure"
        and previous_source_matches
        and not journal_was_observed
        and not journal_is_blocking
    )
    if (
        isinstance(previous, dict)
        and not previous_source_matches
        and not proved_pre_remote
    ):
        # A failed history row is keyed only by parent/quote identity.  The
        # reply store and outbox deliberately have independent attempt
        # ordinals, so an older definite failure cannot prove the outcome of a
        # later attempt which may have reached transport.  Only an explicit
        # current-schema pre-remote phase or the exact matching sending receipt
        # reconciled above may consume the row safely.
        raise AmbiguousContextReplyOutcome(
            "an interrupted historical-context attempt may have reached its "
            "remote transaction but only an older failed history outcome is "
            "available",
            parent_post_id=parent_id,
            reply_text=str(context.get("reply_text") or ""),
        )
    if not isinstance(previous, dict):
        # Missing phase metadata denotes a legacy record whose remote boundary
        # is unknown. True denotes the current schema's explicit remote phase.
        # A journal object or unsafe journal namespace likewise remains an
        # independent fail-closed barrier even if the source receipt vanished.
        raise AmbiguousContextReplyOutcome(
            "an interrupted historical-context attempt may have reached its "
            "remote transaction but has neither a durable transaction receipt "
            "nor a recorded terminal outcome",
            parent_post_id=parent_id,
            reply_text=str(context.get("reply_text") or ""),
        )
    if isinstance(previous, dict) and previous.get("status") == "completed":
        if not previous_source_matches:
            raise AmbiguousContextReplyOutcome(
                "completed historical-context history does not match the exact "
                "current outbox source binding",
                parent_post_id=parent_id,
                reply_text=str(context.get("quote_text") or ""),
            )
        # HistoricalContextReplyStore counts only attempts which reached its
        # remote-write transaction.  The outbox also counts formatter and
        # policy failures, so the two ordinals are deliberately not compared.
        # Matching parent and canonical quote identity plus a validated
        # completed history record is the durable proof required here.
        updated = store.record_confirmed(
            parent_id,
            attempt_number=attempt_number,
            reply_post_id=str(previous["reply_post_id"]),
            confirmed_epoch=recovered_epoch,
        )
        status = "recovered_confirmed_history"
    else:
        failure = "context attempt was interrupted before a durable outcome"
        if isinstance(previous, dict) and previous.get("status") == "failed":
            if not previous_source_matches:
                raise AmbiguousContextReplyOutcome(
                    "failed historical-context history does not match the exact "
                    "current outbox source binding",
                    parent_post_id=parent_id,
                    reply_text=str(context.get("quote_text") or ""),
                )
            # The failed history may describe this remote attempt or an older
            # one because pre-transaction failures are counted only by the
            # outbox.  Either way, no confirmed reply exists; consuming the
            # claimed outbox attempt without repeating remote work is the
            # conservative recovery action.
            failure = str(previous["failure"])
        state_name = _record_context_outbox_failure(
            store,
            parent_post_id=parent_id,
            attempt_number=attempt_number,
            error=failure,
            failed_epoch=recovered_epoch,
            force_terminal=attempt_number >= store.max_attempts,
            proved_remote_non_success=exact_current_failure,
        )
        updated = store.get(parent_id)
        status = "recovered_interrupted_attempt"
        if updated is None or updated["context_reply"]["state"] != state_name:
            raise RuntimeError("interrupted context recovery was not durable")
        if exact_current_failure:
            if (
                context_store.reconcile_receipt_disposition()
                != "definite_failure"
            ):
                raise RuntimeError(
                    "exact context failure receipt changed before retirement"
                )
    if status == "recovered_confirmed_history":
        emit_historical_context_history_observation(previous)
    return {
        "parent_post_id": parent_id,
        "status": status,
        "context_reply_state": str(updated["context_reply"]["state"]),
        "attempt_number": attempt_number,
        "remote_work_repeated": False,
    }


def process_due_historical_context_obligations(
    *,
    parent_post_id: str | None = None,
    limit: int = 1,
    runtime_state: BotState | dict | None = None,
    AmbiguousRemotePostOutcome: Any,
    InvalidMemePostReceipt: Any,
    InvalidRegularPostReceipt: Any,
    _process_due_historical_context_obligations: Any,
    block_if_ambiguous_remote_post: Any,
    historical_context_outbox_remote_attempt_parent_for_local_reconciliation: Any,
    historical_context_outbox_store: Any,
    historical_context_receipt_parent_for_local_reconciliation: Any,
    historical_context_receipt_path_present_or_unsafe: Any,
    log: Any,
) -> list[dict]:
    """Serialise complete context attempts across claims and remote outcomes."""
    from historical_context_outbox import OutboxWorkerBusy

    receipt_reconciliation_only = (
        historical_context_receipt_path_present_or_unsafe()
    )
    reconciliation_parent_id = (
        historical_context_receipt_parent_for_local_reconciliation()
        if receipt_reconciliation_only
        else None
    )
    outbox_reconciliation_parent_id = (
        historical_context_outbox_remote_attempt_parent_for_local_reconciliation()
    )
    outbox_reconciliation_only = outbox_reconciliation_parent_id is not None
    if (
        receipt_reconciliation_only
        and reconciliation_parent_id is not None
        and outbox_reconciliation_parent_id is not None
        and reconciliation_parent_id != outbox_reconciliation_parent_id
    ):
        log.critical(
            "Historical-context receipt and risky outbox rows identify "
            "different parents; no local reconciliation was attempted"
        )
        return []
    if reconciliation_parent_id is None:
        reconciliation_parent_id = outbox_reconciliation_parent_id
    try:
        block_if_ambiguous_remote_post(
            allow_historical_context_receipt_reconciliation=(
                receipt_reconciliation_only
            ),
            allow_historical_context_outbox_reconciliation_parent_id=(
                outbox_reconciliation_parent_id
            ),
        )
    except (
        AmbiguousRemotePostOutcome,
        InvalidRegularPostReceipt,
        InvalidMemePostReceipt,
    ):
        log.critical(
            "Historical-context auxiliary worker deferred by the unresolved "
            "global remote-write ambiguity barrier"
        )
        return []
    if receipt_reconciliation_only or outbox_reconciliation_only:
        if reconciliation_parent_id is None:
            return []
        if (
            parent_post_id is not None
            and str(parent_post_id) != reconciliation_parent_id
        ):
            log.critical(
                "Historical-context local reconciliation was requested for "
                "a different parent; no outbox work was performed. "
                "reconciliation_parent_id=%s requested_parent_id=%s",
                reconciliation_parent_id,
                parent_post_id,
            )
            return []
        parent_post_id = reconciliation_parent_id
    store = historical_context_outbox_store()
    try:
        with store.worker_lock():
            return _process_due_historical_context_obligations(
                store=store,
                parent_post_id=parent_post_id,
                limit=limit,
                runtime_state=runtime_state,
                historical_context_receipt_reconciliation_only=(
                    receipt_reconciliation_only
                ),
                historical_context_outbox_reconciliation_only=(
                    outbox_reconciliation_only
                ),
            )
    except OutboxWorkerBusy:
        log.info(
            "Historical-context auxiliary worker already active; this tick "
            "will not inspect or repeat its claimed work"
        )
        return []
