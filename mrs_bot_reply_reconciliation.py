"""Apply and reconcile already-confirmed conversational reply state.

Root adapters supply current receipt-value, draft, tweet, mention-authority,
mention-queue, history, clarification, accounting and receipt owners with
settings, paths, logger and application exception classes on every call.
Confirmed application calls those owners directly; receipt fallback dates call
the shared ReceiptDates owner directly.
ReplyCompletion owns durable commit and ordered cleanup for fresh, restarted and
emergency completion, preserving their distinct error boundaries. Bodies retain
mutation, callback, reference and error order, including source
lineage before state application and durable state saving before journal
retirement and receipt removal.

Fixed mention continuation grammar comes directly from its owner; canonical
page ownership and recovery reporting retain current callbacks. Receipt I/O,
transport journals, persistence and posting keep their existing boundaries.
Receipt values use the supplied owner directly. Import uses the standard library
and inert state owners, with no file, environment, provider or RNG work;
no caller state is retained.
"""

from __future__ import annotations

import copy
import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime as DateTime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from mrs_bot_runtime_state_helpers import append_unique_capped, append_unique_durable
from mrs_bot_reply_state import mark_quote_tweet_replied
from mrs_bot_mention_authority import (
    active_mention_backlog_reset_guard,
    mention_pagination_provenance_is_valid,
    _reset_mention_candidate_authority,
)

if TYPE_CHECKING:
    from mrs_bot_core_contracts import BotState
    from mrs_bot_core_contracts import ConfirmedReplyReceipt, ValidatedConfirmedReplyReceipt
    from mrs_bot_reply_clarifications import ClarificationReplies
    from mrs_bot_daily_reply_accounting import DailyReplyAccounting
    from mrs_bot_mention_authority import MentionAuthority
    from mrs_bot_mention_discovery import MentionQueue
    from mrs_bot_receipt_primitives import ReceiptDates
    from mrs_bot_reply_delivery import ReplyReceipts
    from mrs_bot_reply_drafts import ReplyDrafts
    from mrs_bot_reply_history import ReplyHistory
    from mrs_bot_reply_receipt_values import ReplyReceiptValues
    from mrs_bot_state_generation import StateCommitProof
    from mrs_bot_reply_cycle_interfaces import SaveReplyState
    from mrs_bot_main_post_assembly import (
        RetireMainPostTransportJournal, VerifyMainPostTransportLineage,
    )
    from mrs_bot_tweet_lookup_cache import TweetLookupCache


def apply_confirmed_reply_receipt(
    state: BotState,
    receipt: dict,
    *,
    receipt_values: ReplyReceiptValues,
    mention_authority: MentionAuthority,
    mention_queue: MentionQueue,
    STATE_FILE: Path,
    InvalidConfirmedReplyReceipt: type[Exception],
    dates: ReceiptDates,
    accounting: DailyReplyAccounting,
    log: logging.Logger,
    drafts: ReplyDrafts,
    tweets: TweetLookupCache,
    MY_USER_ID: str,
    datetime: type[DateTime],
    history: ReplyHistory,
    clarifications: ClarificationReplies,
    log_event: Callable,
) -> None:
    """Apply confirmed reply receipt."""
    target_id = str(receipt["target_id"])
    reply_post_id = str(receipt["reply_post_id"])
    author_id = str(receipt.get("author_id") or "")
    reply_epoch = receipt_values.confirmation_epoch(receipt)
    candidate_source = str(receipt.get("candidate_source") or "mention")
    conversation_id = str(receipt.get("conversation_id") or target_id)
    reply_text = str(receipt.get("reply_text") or "")
    if candidate_source == "mention":
        authority_usable, _authority_changed = (
            mention_authority.validate_pending(
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
    receipt_reply_date = str(
        receipt.get("daily_reply_date") or dates.reply_cap_date(reply_epoch)
    )
    receipt_quote_reply_date = str(receipt.get("daily_quote_reply_date") or receipt_reply_date)
    if receipt.get("schema_version") == 4:
        accounting.advance(
            state,
            receipt_reply_date,
            include_quote_lane=candidate_source == "quote_tweet",
        )
    clarification = receipt.get("clarification_reply")
    if isinstance(clarification, dict):
        clarifications.assert_no_conflict(state, clarification, reply_post_id=reply_post_id)
    mention_pagination_to_preserve = _mention_pagination_to_preserve(
        state, receipt, target_id=target_id, candidate_source=candidate_source,
        InvalidConfirmedReplyReceipt=InvalidConfirmedReplyReceipt,
        STATE_FILE=STATE_FILE,
        mention_authority=mention_authority,
        log=log,
    )
    # A confirmed public reply retires drafts for this target in every lane.
    drafts.clear_target(state, target_id, candidate_source)

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

    accounting.record_confirmed(
        state,
        already_recorded=already_recorded,
        candidate_source=candidate_source,
        author_id=author_id,
        receipt_reply_date=receipt_reply_date,
        receipt_quote_reply_date=receipt_quote_reply_date,
    )

    try:
        state["last_reply_epoch"] = max(int(state.get("last_reply_epoch", 0) or 0), reply_epoch)
    except Exception:
        state["last_reply_epoch"] = reply_epoch

    if candidate_source == "mention":
        mention_queue.remove_pending(state, target_id)
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
            mention_queue.advance_watermark(state, target_id)

    tweets.store(
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
        history.record_confirmation(
            state, receipt, ai_reply_draft,
            target_id=target_id,
            reply_post_id=reply_post_id,
            author_id=author_id,
            conversation_id=conversation_id,
            candidate_source=candidate_source,
            reply_epoch=reply_epoch,
        )
        outcome_fields = {
            "status": "confirmed",
            "lane": candidate_source,
            "target_id": target_id,
            "reply_post_id": reply_post_id,
            "strategy_version": ai_reply_draft.get("strategy_version"),
            "reply_kind": ai_reply_draft.get("reply_kind"),
            "reason_code": ai_reply_draft.get("reason_code"),
            "used_fact_count": len(ai_reply_draft.get("used_fact_ids") or []),
            "supplied_image_count": len(ai_reply_draft.get("supplied_images") or []),
            "model_call_count": ai_reply_draft.get("model_call_count"),
            "validated_draft_hash": ai_reply_draft.get("validated_draft_hash"),
            "failure_reason": "",
        }
        if ai_reply_draft.get("call_id"):
            outcome_fields["call_id"] = ai_reply_draft["call_id"]
        log_event("single_call_reply_posting_outcome", **outcome_fields)
    if isinstance(clarification, dict):
        clarifications.record_completed(
            state, clarification, author_id=author_id, target_id=target_id,
            reply_post_id=reply_post_id, reply_epoch=reply_epoch,
        )


def _mention_pagination_to_preserve(
    state: BotState,
    receipt: dict,
    *,
    target_id: str,
    candidate_source: str,
    InvalidConfirmedReplyReceipt: type[Exception],
    STATE_FILE: Path,
    mention_authority: MentionAuthority,
    log: logging.Logger,
) -> dict | None:
    """Preserve receipt or legacy pagination after checking its canonical ownership."""
    if "mention_pagination" in receipt:
        pagination = receipt.get("mention_pagination")
        if (
            candidate_source != "mention"
            or not mention_pagination_provenance_is_valid(pagination)
        ):
            raise InvalidConfirmedReplyReceipt(
                "Confirmed reply receipt has invalid mention pagination provenance"
            )
        base_since_id = str(pagination["base_since_id"])
        current_since_id = str(state.get("last_seen_mention_id") or "")
        if current_since_id != base_since_id:
            raise InvalidConfirmedReplyReceipt(
                "Confirmed mention receipt pagination base does not match "
                "the current mention watermark"
            )
        legacy = False
    elif candidate_source == "mention":
        # Older receipts may preserve an active continuation only while it is
        # bound to the unchanged watermark, avoiding the unseen truncated tail.
        pagination = state.get("mention_pagination")
        current_since_id = str(state.get("last_seen_mention_id") or "")
        if not (
            mention_pagination_provenance_is_valid(pagination)
            and str(pagination["base_since_id"]) == current_since_id
        ):
            return None
        legacy = True
    else:
        return None

    if not mention_authority.owns_page(
        state, pagination, target_id=target_id,
    ):
        discarded = len(state.get("mention_pending_candidates", {}))
        _reset_mention_candidate_authority(state, watermark=current_since_id)
        mention_authority.emit_recovery(
            {
                "reason": "receipt_page_ownership_missing",
                "since_id": current_since_id or None,
                "discarded_candidates": discarded,
            },
            path=STATE_FILE,
            recovery_events=None,
        )
    preserved = copy.deepcopy(pagination)
    if legacy:
        log.warning(
            "Preserving active mention pagination for a legacy confirmed "
            "reply receipt without transaction-bound provenance"
        )
    return cast(dict[str, str], preserved)


@dataclass(frozen=True)
class ReplyCompletion:
    """Commit confirmed state and retire exact recovery records in one shared order.

    Fresh completion and restart recovery retain their separate error policies.
    Application, durable storage and receipt authorities remain explicit boundaries.
    """

    receipt_path: Path
    persistence_error: type[Exception]
    apply_state: Callable[[BotState, ValidatedConfirmedReplyReceipt], None]
    save_state: SaveReplyState
    retire_journal: RetireMainPostTransportJournal
    log: logging.Logger
    receipts: ReplyReceipts
    unresolved_sending_receipt: type[Exception]
    invalid_receipt: type[Exception]
    verify_lineage: VerifyMainPostTransportLineage

    def commit(self, state: BotState, receipt: Mapping[str, Any]) -> StateCommitProof:
        """Record this receipt in caller state and return its durable commit proof."""
        from mrs_bot_state_generation import record_receipt_commit
        record_receipt_commit(state, receipt)
        return self.save_state(state, durable=True)

    def retire(
        self, receipt: Mapping[str, Any], *, post_id: str, commit_proof: StateCommitProof,
        sending_disposition: str | None = None,
    ) -> None:
        """Retire the journal before the source receipt with the same commit proof."""
        self.retire_journal(
            commit_proof=commit_proof,
            receipt_path=self.receipt_path,
            receipt=receipt,
            lane="conversational_reply",
            post_id=post_id,
        )
        if sending_disposition is None:
            self.receipts.remove(receipt, commit_proof=commit_proof)
        else:
            self.receipts.remove(
                receipt, sending_disposition=sending_disposition, commit_proof=commit_proof,
            )

    def finalise(
        self, state: BotState, receipt: ConfirmedReplyReceipt, *, target_id: str, quote_reply: bool,
    ) -> str:
        """Commit a new confirmation, then retire its exact recovery records.

        Application and durable-save errors retain their original types. Only
        cleanup failures are wrapped after the confirmed state is durable. Restart
        reconciliation has its own save-error policy and remains a separate entry.
        """
        own_reply_id = str(receipt["reply_post_id"])
        self.apply_state(state, receipt)
        self.log.info(
            "Recorded and cached own quote-tweet auto-reply id=%s"
            if quote_reply else "Recorded and cached own auto-reply id=%s",
            own_reply_id,
        )
        commit_proof = self.commit(state, receipt)
        try:
            self.retire(receipt, post_id=own_reply_id, commit_proof=commit_proof)
        except Exception as exc:
            self.log.critical(
                "Confirmed quote-tweet reply id=%s to target=%s was saved but receipt removal failed"
                if quote_reply else
                "Confirmed reply id=%s to target=%s was saved but receipt removal failed",
                own_reply_id,
                target_id,
                exc_info=True,
            )
            description = "quote-tweet reply" if quote_reply else "reply"
            raise self.persistence_error(
                f"Confirmed {description} {own_reply_id} to {target_id} but receipt removal failed"
            ) from exc
        return own_reply_id

    def reconcile(self, state: BotState) -> bool:
        """Reconcile a confirmed reply without duplicating the remote post."""
        loaded = self.receipts.load()
        if loaded[0] == "absent":
            return False
        if loaded[0] in {"sending", "legacy_sending"} and loaded[1] is not None:
            raise self.unresolved_sending_receipt(
                "A conversational reply was interrupted after its durable sending "
                "receipt was written; manual reconciliation is required before any "
                "remote write"
            )
        if loaded[0] != "valid" or loaded[1] is None:
            raise self.invalid_receipt(
                f"Invalid confirmed-reply receipt blocks auto-reply processing: {self.receipt_path}"
            )
        receipt = loaded[1]

        self.verify_lineage(
            receipt_path=self.receipt_path,
            receipt=cast(dict, receipt),
            lane="conversational_reply",
            post_id=str(receipt.get("reply_post_id") or ""),
        )

        self.log.warning(
            "Reconciling confirmed reply receipt source=%s target_id=%s reply_post_id=%s",
            receipt.get("candidate_source", "mention"),
            receipt.get("target_id"),
            receipt.get("reply_post_id"),
        )
        self.apply_state(state, receipt)
        try:
            commit_proof = self.commit(state, receipt)
        except Exception as exc:
            self.log.critical(
                "Confirmed reply receipt was applied in memory but state save failed; receipt remains for retry",
                exc_info=True,
            )
            raise self.persistence_error("Confirmed reply receipt reconciliation state save failed") from exc
        try:
            self.retire(receipt, post_id=str(receipt["reply_post_id"]), commit_proof=commit_proof)
        except Exception as exc:
            self.log.critical("Confirmed reply receipt state was saved but receipt removal failed", exc_info=True)
            raise self.persistence_error("Confirmed reply receipt removal failed") from exc
        return True


def confirmed_reply_emergency_representation_is_complete(
    receipt: dict,
    state: BotState,
    *,
    receipt_values: ReplyReceiptValues,
    InvalidConfirmedReplyReceipt: type[Exception],
    receipt_int: Callable,
    has_target_draft: Callable[[BotState, str, str], bool],
) -> bool:
    """Return whether state alone durably suppresses a confirmed reply replay."""
    if not receipt_values.confirmed_is_valid(receipt):
        return False
    target_id = str(receipt["target_id"])
    reply_post_id = str(receipt["reply_post_id"])
    candidate_source = str(receipt.get("candidate_source") or "mention")
    try:
        reply_epoch = receipt_values.confirmation_epoch(receipt)
    except InvalidConfirmedReplyReceipt:
        return False
    state_reply_epoch = receipt_int(state.get("last_reply_epoch"))
    own_reply_ids = {str(item) for item in state.get("own_auto_reply_ids", [])}
    target_has_draft = has_target_draft(state, target_id, candidate_source)
    if (
        state_reply_epoch is None
        or state_reply_epoch < reply_epoch
        or reply_post_id not in own_reply_ids
        or target_has_draft
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
