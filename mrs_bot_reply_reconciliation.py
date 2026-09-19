"""Apply and reconcile already-confirmed conversational reply state.

Root adapters supply current helpers, settings, paths, logger and application
exception classes on every call. Original bodies preserve mutation, callback,
reference and error order, including source lineage before state application
and durable state saving before journal retirement and receipt removal.

Mention authority, receipt validation and I/O, transport journals, persistence
and posting remain in their existing owners and are invoked through current
root callbacks. Import uses only the standard library and performs no file,
environment, provider or RNG work; no callbacks are retained.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Callable
from pathlib import Path


def _valid_iso_date(
    value: object,
    *,
    datetime: type,
) -> bool:
    """Return whether a value is a canonical calendar date."""
    if not isinstance(value, str):
        return False
    try:
        return datetime.strptime(value, "%Y-%m-%d").strftime("%Y-%m-%d") == value
    except ValueError:
        return False


def _advance_reply_counters_to_confirmation_date(
    state: dict,
    confirmation_date: str,
    *,
    include_quote_lane: bool,
    _valid_iso_date: Callable,
    log: logging.Logger,
) -> None:
    """Advance stale daily reply buckets without rolling newer state backward."""
    state_reply_date = state.get("daily_reply_date")
    if not _valid_iso_date(state_reply_date) or state_reply_date < confirmation_date:
        log.info(
            "Advancing daily reply accounting to confirmation date. "
            "previous_date=%s new_date=%s previous_count=%s",
            state_reply_date,
            confirmation_date,
            state.get("daily_reply_count"),
        )
        state["daily_reply_date"] = confirmation_date
        state["daily_reply_count"] = 0
        state["daily_replied_author_ids"] = []
        state["daily_replied_author_counts"] = {}
    if not include_quote_lane:
        return
    state_quote_date = state.get("daily_quote_reply_date")
    if not _valid_iso_date(state_quote_date) or state_quote_date < confirmation_date:
        log.info(
            "Advancing daily quote-reply accounting to confirmation date. "
            "previous_date=%s new_date=%s previous_count=%s",
            state_quote_date,
            confirmation_date,
            state.get("daily_quote_reply_count"),
        )
        state["daily_quote_reply_date"] = confirmation_date
        state["daily_quote_reply_count"] = 0


def apply_confirmed_reply_receipt(
    state: dict,
    receipt: dict,
    *,
    conversational_reply_confirmation_epoch: Callable,
    validate_pending_mention_candidate_authority: Callable,
    STATE_FILE: Path,
    InvalidConfirmedReplyReceipt: type[Exception],
    reply_cap_date_str: Callable,
    _advance_reply_counters_to_confirmation_date: Callable,
    mention_pagination_provenance_is_valid: Callable,
    mention_pagination_has_canonical_page_ownership: Callable,
    _reset_mention_candidate_authority: Callable,
    _emit_mention_authority_recovery: Callable,
    log: logging.Logger,
    clear_pending_ai_reply: Callable,
    mark_quote_tweet_replied: Callable,
    append_unique_durable: Callable,
    append_unique_capped: Callable,
    mark_daily_author_replied: Callable,
    remove_pending_mention_candidate: Callable,
    active_mention_backlog_reset_guard: Callable,
    update_last_seen_mention_id: Callable,
    cache_tweet: Callable,
    MY_USER_ID: str,
    datetime: type,
    record_reply_history: Callable,
    log_event: Callable,
) -> None:
    """Apply confirmed reply receipt."""
    target_id = str(receipt["target_id"])
    reply_post_id = str(receipt["reply_post_id"])
    author_id = str(receipt.get("author_id") or "")
    reply_epoch = conversational_reply_confirmation_epoch(receipt)
    candidate_source = str(receipt.get("candidate_source") or "mention")
    conversation_id = str(receipt.get("conversation_id") or target_id)
    reply_text = str(receipt.get("reply_text") or "")
    if candidate_source == "mention":
        authority_usable, _authority_changed = (
            validate_pending_mention_candidate_authority(
                state,
                path=STATE_FILE,
                recover_pending_identity=True,
            )
        )
        if not authority_usable:
            raise InvalidConfirmedReplyReceipt(
                "Confirmed mention receipt cannot be reconciled without a "
                "bounded pending-candidate authority base"
            )
    receipt_reply_date = str(receipt.get("daily_reply_date") or reply_cap_date_str(reply_epoch))
    receipt_quote_reply_date = str(receipt.get("daily_quote_reply_date") or receipt_reply_date)
    if receipt.get("schema_version") == 4:
        _advance_reply_counters_to_confirmation_date(
            state,
            receipt_reply_date,
            include_quote_lane=candidate_source == "quote_tweet",
        )
    clarification = receipt.get("clarification_reply")
    if isinstance(clarification, dict):
        existing_records = state.get("clarification_reply_records", {})
        existing = existing_records.get(str(clarification["thread_id"])) if isinstance(existing_records, dict) else None
        if isinstance(existing, dict) and str(existing.get("reply_post_id") or "") != reply_post_id:
            raise InvalidConfirmedReplyReceipt(
                f"clarification thread {clarification['thread_id']} already has a different completed repair"
            )
    mention_pagination_to_preserve: dict | None = None
    if "mention_pagination" in receipt:
        mention_pagination = receipt.get("mention_pagination")
        if (
            candidate_source != "mention"
            or not mention_pagination_provenance_is_valid(mention_pagination)
        ):
            raise InvalidConfirmedReplyReceipt(
                "Confirmed reply receipt has invalid mention pagination provenance"
            )
        base_since_id = str(mention_pagination["base_since_id"])
        current_since_id = str(state.get("last_seen_mention_id") or "")
        if current_since_id != base_since_id:
            raise InvalidConfirmedReplyReceipt(
                "Confirmed mention receipt pagination base does not match "
                "the current mention watermark"
            )
        if not mention_pagination_has_canonical_page_ownership(
            state,
            mention_pagination,
            target_id=target_id,
        ):
            discarded = len(state.get("mention_pending_candidates", {}))
            _reset_mention_candidate_authority(
                state,
                watermark=current_since_id,
            )
            _emit_mention_authority_recovery(
                {
                    "reason": "receipt_page_ownership_missing",
                    "since_id": current_since_id or None,
                    "discarded_candidates": discarded,
                },
                path=STATE_FILE,
                recovery_events=None,
            )
        mention_pagination_to_preserve = copy.deepcopy(mention_pagination)
    elif candidate_source == "mention":
        # Receipts written by pre-provenance versions can still be reconciled
        # safely when canonical state carries an exact continuation bound to
        # the unchanged watermark. Preserving it favours harmless deduplication
        # over skipping the unseen tail of a truncated result set.
        active_pagination = state.get("mention_pagination")
        current_since_id = str(state.get("last_seen_mention_id") or "")
        if (
            mention_pagination_provenance_is_valid(active_pagination)
            and str(active_pagination["base_since_id"]) == current_since_id
        ):
            pagination_has_page_ownership = (
                mention_pagination_has_canonical_page_ownership(
                    state,
                    active_pagination,
                    target_id=target_id,
                )
            )
            if not pagination_has_page_ownership:
                discarded = len(state.get("mention_pending_candidates", {}))
                _reset_mention_candidate_authority(
                    state,
                    watermark=current_since_id,
                )
                _emit_mention_authority_recovery(
                    {
                        "reason": "receipt_page_ownership_missing",
                        "since_id": current_since_id or None,
                        "discarded_candidates": discarded,
                    },
                    path=STATE_FILE,
                    recovery_events=None,
                )
            mention_pagination_to_preserve = copy.deepcopy(active_pagination)
            log.warning(
                "Preserving active mention pagination for a legacy confirmed "
                "reply receipt without transaction-bound provenance"
            )
    # A confirmed public reply retires drafts for this target in every lane.
    for source in {candidate_source, "mention", "hot_post_reply", "quote_tweet", "conversational_reply"}:
        clear_pending_ai_reply(state, target_id, source)

    if candidate_source == "quote_tweet":
        replied_to_ids = set(str(x) for x in state.get("replied_to_quote_post_ids", []))
        already_recorded = target_id in replied_to_ids
        mark_quote_tweet_replied(state, target_id)
    else:
        replied_to_ids = set(str(x) for x in state.get("replied_to_ids", []))
        already_recorded = target_id in replied_to_ids
        state["replied_to_ids"] = append_unique_durable(
            state.get("replied_to_ids", []),
            target_id,
        )

    state["own_auto_reply_ids"] = append_unique_capped(
        state.get("own_auto_reply_ids", []),
        reply_post_id,
        1000,
    )

    if not already_recorded and state.get("daily_reply_date") == receipt_reply_date:
        state["daily_reply_count"] = int(state.get("daily_reply_count", 0) or 0) + 1
        if author_id:
            mark_daily_author_replied(state, author_id)
    if (
        candidate_source == "quote_tweet"
        and not already_recorded
        and state.get("daily_quote_reply_date") == receipt_quote_reply_date
    ):
        state["daily_quote_reply_count"] = int(state.get("daily_quote_reply_count", 0) or 0) + 1

    try:
        state["last_reply_epoch"] = max(int(state.get("last_reply_epoch", 0) or 0), reply_epoch)
    except Exception:
        state["last_reply_epoch"] = reply_epoch

    if candidate_source == "mention":
        remove_pending_mention_candidate(state, target_id)
        if mention_pagination_to_preserve is not None:
            state["mention_pagination"] = mention_pagination_to_preserve
            log.info(
                "Preserved mention pagination continuation after confirmed "
                "reply target_id=%s base_since_id=%s",
                target_id,
                mention_pagination_to_preserve["base_since_id"] or None,
            )
        elif active_mention_backlog_reset_guard(state) is not None:
            log.warning(
                "Deferring mention watermark advancement for confirmed reply "
                "target_id=%s until a post-reset traversal from the collection "
                "head completes",
                target_id,
            )
        else:
            update_last_seen_mention_id(state, target_id)

    cache_tweet(
        state,
        tweet_id=reply_post_id,
        text=reply_text,
        author_id=str(MY_USER_ID),
        conversation_id=conversation_id,
        referenced_tweets=[
            {
                "type": "replied_to",
                "id": target_id,
            }
        ],
        created_at=(
            datetime.fromtimestamp(reply_epoch).isoformat()
            if receipt.get("schema_version") == 4
            else None
        ),
        post_type="auto_reply",
    )
    ai_reply_draft = receipt.get("ai_reply_draft")
    if isinstance(ai_reply_draft, dict):
        record_reply_history(
            state, receipt, ai_reply_draft,
            target_id=target_id,
            reply_post_id=reply_post_id,
            author_id=author_id,
            conversation_id=conversation_id,
            candidate_source=candidate_source,
            reply_epoch=reply_epoch,
        )
        log_event(
            "single_call_reply_posting_outcome",
            status="confirmed",
            lane=candidate_source,
            target_id=target_id,
            reply_post_id=reply_post_id,
            strategy_version=ai_reply_draft.get("strategy_version"),
            reply_kind=ai_reply_draft.get("reply_kind"),
            reason_code=ai_reply_draft.get("reason_code"),
            used_fact_count=len(ai_reply_draft.get("used_fact_ids") or []),
            supplied_image_count=len(ai_reply_draft.get("supplied_images") or []),
            model_call_count=ai_reply_draft.get("model_call_count"),
            validated_draft_hash=ai_reply_draft.get("validated_draft_hash"),
            failure_reason="",
        )
    if isinstance(clarification, dict):
        thread_id = str(clarification["thread_id"])
        records = state.get("clarification_reply_records", {})
        if not isinstance(records, dict):
            records = {}
        existing = records.get(thread_id)
        if not isinstance(existing, dict):
            records = dict(records)
            records[thread_id] = {
                "thread_id": thread_id,
                "author_id": author_id,
                "target_id": target_id,
                "reply_post_id": reply_post_id,
                "prior_bot_reply_id": str(clarification["prior_bot_reply_id"]),
                "original_question_id": str(clarification["original_question_id"]),
                "trigger": str(clarification["trigger"]),
                "completed_epoch": reply_epoch,
                "status": "repair_reply_completed",
                "clarification_reply_used": True,
                "thread_terminal": True,
            }
            state["clarification_reply_records"] = records
            log_event(
                "clarification_reply_used",
                thread_id=thread_id,
                author_id=author_id,
                target_id=target_id,
                reply_post_id=reply_post_id,
                trigger=clarification["trigger"],
            )
            log_event(
                "repair_reply_completed",
                thread_id=thread_id,
                author_id=author_id,
                target_id=target_id,
                reply_post_id=reply_post_id,
            )


def finalise_confirmed_reply(
    state: dict,
    receipt: dict,
    *,
    target_id: str,
    quote_reply: bool,
    CONFIRMED_REPLY_RECEIPT_FILE: Path,
    ConfirmedReplyLocalPersistenceError: type[Exception],
    apply_confirmed_reply_receipt: Callable,
    save_state: Callable,
    retire_lane_transport_journal_if_present: Callable,
    remove_confirmed_reply_receipt: Callable,
    log: logging.Logger,
) -> str:
    """Commit a new confirmation, then retire its exact recovery records.

    Application and durable-save errors retain their original types. Only
    cleanup failures are wrapped after the confirmed state is durable. Restart
    reconciliation has its own save-error policy and remains a separate entry.
    """
    own_reply_id = str(receipt["reply_post_id"])
    apply_confirmed_reply_receipt(state, receipt)
    log.info(
        "Recorded and cached own quote-tweet auto-reply id=%s"
        if quote_reply else "Recorded and cached own auto-reply id=%s",
        own_reply_id,
    )
    from mrs_bot_state_generation import record_receipt_commit
    record_receipt_commit(state, receipt)
    commit_proof = save_state(state, durable=True)
    try:
        retire_lane_transport_journal_if_present(
            commit_proof=commit_proof,
            receipt_path=CONFIRMED_REPLY_RECEIPT_FILE,
            receipt=receipt,
            lane="conversational_reply",
            post_id=own_reply_id,
        )
        remove_confirmed_reply_receipt(receipt, commit_proof=commit_proof)
    except Exception as exc:
        log.critical(
            "Confirmed quote-tweet reply id=%s to target=%s was saved but receipt removal failed"
            if quote_reply else
            "Confirmed reply id=%s to target=%s was saved but receipt removal failed",
            own_reply_id,
            target_id,
            exc_info=True,
        )
        description = "quote-tweet reply" if quote_reply else "reply"
        raise ConfirmedReplyLocalPersistenceError(
            f"Confirmed {description} {own_reply_id} to {target_id} but receipt removal failed"
        ) from exc
    return own_reply_id


def reconcile_confirmed_reply_receipt(
    state: dict,
    *,
    load_confirmed_reply_receipt: Callable,
    UnresolvedSendingReplyReceipt: type[Exception],
    InvalidConfirmedReplyReceipt: type[Exception],
    CONFIRMED_REPLY_RECEIPT_FILE: Path,
    verify_lane_transport_source_lineage_if_present: Callable,
    log: logging.Logger,
    apply_confirmed_reply_receipt: Callable,
    save_state: Callable,
    ConfirmedReplyLocalPersistenceError: type[Exception],
    retire_lane_transport_journal_if_present: Callable,
    remove_confirmed_reply_receipt: Callable,
) -> bool:
    """Reconcile a confirmed reply without duplicating the remote post."""
    status, receipt = load_confirmed_reply_receipt()
    if status == "absent":
        return False
    if status in {"sending", "legacy_sending"} and receipt is not None:
        raise UnresolvedSendingReplyReceipt(
            "A conversational reply was interrupted after its durable sending "
            "receipt was written; manual reconciliation is required before any "
            "remote write"
        )
    if status == "invalid" or receipt is None:
        raise InvalidConfirmedReplyReceipt(
            f"Invalid confirmed-reply receipt blocks auto-reply processing: {CONFIRMED_REPLY_RECEIPT_FILE}"
        )

    verify_lane_transport_source_lineage_if_present(
        receipt_path=CONFIRMED_REPLY_RECEIPT_FILE,
        receipt=receipt,
        lane="conversational_reply",
        post_id=str(receipt.get("reply_post_id") or ""),
    )

    log.warning(
        "Reconciling confirmed reply receipt source=%s target_id=%s reply_post_id=%s",
        receipt.get("candidate_source", "mention"),
        receipt.get("target_id"),
        receipt.get("reply_post_id"),
    )
    apply_confirmed_reply_receipt(state, receipt)
    try:
        from mrs_bot_state_generation import record_receipt_commit
        record_receipt_commit(state, receipt)
        commit_proof = save_state(state, durable=True)
    except Exception as exc:
        log.critical(
            "Confirmed reply receipt was applied in memory but state save failed; receipt remains for retry",
            exc_info=True,
        )
        raise ConfirmedReplyLocalPersistenceError("Confirmed reply receipt reconciliation state save failed") from exc
    try:
        retire_lane_transport_journal_if_present(
            commit_proof=commit_proof,
            receipt_path=CONFIRMED_REPLY_RECEIPT_FILE,
            receipt=receipt,
            lane="conversational_reply",
            post_id=str(receipt["reply_post_id"]),
        )
        remove_confirmed_reply_receipt(receipt, commit_proof=commit_proof)
    except Exception as exc:
        log.critical("Confirmed reply receipt state was saved but receipt removal failed", exc_info=True)
        raise ConfirmedReplyLocalPersistenceError("Confirmed reply receipt removal failed") from exc
    return True


def confirmed_reply_emergency_representation_is_complete(
    receipt: dict,
    state: dict,
    *,
    confirmed_reply_receipt_is_semantically_valid: Callable,
    conversational_reply_confirmation_epoch: Callable,
    InvalidConfirmedReplyReceipt: type[Exception],
    receipt_int: Callable,
    pending_ai_reply_draft_key: Callable,
) -> bool:
    """Return whether state alone durably suppresses a confirmed reply replay."""
    if not confirmed_reply_receipt_is_semantically_valid(receipt):
        return False
    target_id = str(receipt["target_id"])
    reply_post_id = str(receipt["reply_post_id"])
    candidate_source = str(receipt.get("candidate_source") or "mention")
    try:
        reply_epoch = conversational_reply_confirmation_epoch(receipt)
    except InvalidConfirmedReplyReceipt:
        return False
    state_reply_epoch = receipt_int(state.get("last_reply_epoch"))
    own_reply_ids = {str(item) for item in state.get("own_auto_reply_ids", [])}
    drafts = state.get("pending_ai_reply_drafts")
    pending_keys = {
        pending_ai_reply_draft_key(target_id, source)
        for source in {candidate_source, "mention", "hot_post_reply", "quote_tweet", "conversational_reply"}
    }
    if (
        state_reply_epoch is None
        or state_reply_epoch < reply_epoch
        or reply_post_id not in own_reply_ids
        or (isinstance(drafts, dict) and pending_keys.intersection(drafts))
    ):
        return False
    if candidate_source == "quote_tweet":
        return bool(
            target_id
            in {
                str(item)
                for item in state.get("replied_to_quote_post_ids", [])
            }
            and target_id
            in {str(item) for item in state.get("seen_quote_post_ids", [])}
        )
    return target_id in {
        str(item) for item in state.get("replied_to_ids", [])
    }
