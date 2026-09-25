"""Own shared reply admission, quote-target markers and ineligible retirement.

Reply lanes keep candidate bookkeeping and durable saves around the shared
retirement operation. Draft lifecycle and confirmed-history behaviour live in
mrs_bot_reply_drafts and mrs_bot_reply_history; their pure helper names are
re-exported here for compatibility. Import performs no runtime access and no
callbacks or caller state are retained.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mrs_bot_core_contracts import BotState

from mrs_bot_runtime_state_helpers import append_unique_capped, append_unique_durable
from mrs_bot_reply_drafts import pending_ai_reply_draft_key
from mrs_bot_reply_history import (
    CONVERSATIONAL_REPLY_HISTORY_LANES,
    _confirmed_history_sort_key,
    _reply_target_epoch,
)


def handled_reply_target_ids(state: BotState) -> set[str]:
    """Snapshot normal and legacy quote targets already handled for admission.

    The normal ledger also contains terminal outcomes without a remote post, so
    this union is not confirmation evidence. Return a fresh mutable set for the
    caller's scan without combining or changing either durable ledger.
    """
    return {
        str(value)
        for values in (state.get("replied_to_ids", []), state.get("replied_to_quote_post_ids", []))
        for value in values
    }


def retire_ineligible_reply_draft(
    state: BotState,
    target_id: str,
    candidate_source: str,
    *,
    reason: str,
    retire_draft: Callable[[BotState, str, str], None],
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


def remove_pending_quote_candidate(state: BotState, quote_id: str) -> bool:
    """Remove a handled quote from the fetched queue before its caller saves."""
    pending = state.get("quote_pending_candidates")
    if not isinstance(pending, dict) or str(quote_id) not in pending:
        return False
    pending = dict(pending)
    pending.pop(str(quote_id))
    state["quote_pending_candidates"] = pending
    return True


def mark_quote_tweet_skipped(
    state: BotState,
    quote_id: str,
) -> None:
    """Mark quote tweet skipped."""
    quote_id = str(quote_id)

    state["seen_quote_post_ids"] = append_unique_capped(
        state.get("seen_quote_post_ids", []),
        quote_id,
        2000,
    )
    state["skipped_quote_post_ids"] = append_unique_capped(
        state.get("skipped_quote_post_ids", []),
        quote_id,
        2000,
    )
    remove_pending_quote_candidate(state, quote_id)


def mark_quote_tweet_replied(
    state: BotState,
    quote_id: str,
) -> None:
    """Mark quote tweet replied."""
    quote_id = str(quote_id)

    state["seen_quote_post_ids"] = append_unique_capped(
        state.get("seen_quote_post_ids", []),
        quote_id,
        2000,
    )
    state["replied_to_quote_post_ids"] = append_unique_durable(
        state.get("replied_to_quote_post_ids", []),
        quote_id,
    )
    remove_pending_quote_candidate(state, quote_id)
