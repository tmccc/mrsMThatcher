"""Coordinate ineligible-draft retirement and retain reply-state compatibility names.

Reply lanes keep candidate bookkeeping and durable saves around the shared
retirement operation. Draft lifecycle and confirmed-history behaviour live in
mrs_bot_reply_drafts and mrs_bot_reply_history; their pure helper names are
re-exported here for compatibility. Import performs no runtime access and no
callbacks or caller state are retained.
"""

from __future__ import annotations

from collections.abc import Callable

from mrs_bot_reply_drafts import pending_ai_reply_draft_key
from mrs_bot_reply_history import (
    CONVERSATIONAL_REPLY_HISTORY_LANES,
    _confirmed_history_sort_key,
    _reply_target_epoch,
)


def retire_ineligible_reply_draft(
    state: dict,
    target_id: str,
    candidate_source: str,
    *,
    reason: str,
    pending_ai_reply_draft_key: Callable,
    log_event: Callable,
    clear_pending_ai_reply: Callable,
    record_terminal_reply_evaluation: Callable,
) -> None:
    """Log and clear a dict-shaped draft, then record ineligible evaluation.

    Missing or malformed drafts still receive the terminal evaluation. Caller
    callbacks retain their order and failures propagate before later callbacks;
    candidate bookkeeping and durable saves remain with the calling lane.
    """
    drafts = state.get("pending_ai_reply_drafts", {})
    pending_key = pending_ai_reply_draft_key(target_id, candidate_source)
    pending_record = drafts.get(pending_key) if isinstance(drafts, dict) else None
    if isinstance(pending_record, dict):
        log_event(
            "single_call_reply_posting_outcome",
            status="posting_failed_terminal",
            lane=candidate_source,
            target_id=target_id,
            reply_post_id="",
            strategy_version=pending_record.get("strategy_version"),
            reply_kind=pending_record.get("reply_kind"),
            reason_code=pending_record.get("reason_code"),
            validated_draft_hash=pending_record.get("validated_draft_hash"),
            failure_reason="reply_not_permitted_preflight",
        )
        clear_pending_ai_reply(state, target_id, candidate_source)
    record_terminal_reply_evaluation(
        state,
        target_id=target_id,
        lane=candidate_source,
        reason=reason,
        outcome="reply_not_permitted",
    )
