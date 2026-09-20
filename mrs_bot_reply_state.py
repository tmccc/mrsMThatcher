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
    retire_draft: Callable[[dict, str, str], None],
    record_terminal_reply_evaluation: Callable,
) -> None:
    """Retire the draft through its owner, then record ineligible evaluation.

    Missing or malformed drafts still receive the terminal evaluation. Caller
    callbacks retain their order and failures propagate before later callbacks;
    candidate bookkeeping and durable saves remain with the calling lane.
    """
    retire_draft(state, target_id, candidate_source)
    record_terminal_reply_evaluation(
        state,
        target_id=target_id,
        lane=candidate_source,
        reason=reason,
        outcome="reply_not_permitted",
    )
