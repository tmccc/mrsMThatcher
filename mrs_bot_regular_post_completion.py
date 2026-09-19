"""Shared durable completion of confirmed regular quote/image posts.

Callers apply their live or recovered state first and retain their own error
handling. The journal callback defers caller-specific field reads
until the matching completion stage. Imports perform no runtime work, and no
callbacks or mutable state are retained beyond a call.
"""

from __future__ import annotations

from collections.abc import Callable


def complete_regular_post_persistence(
    lines_used: set,
    images_used: set,
    state: dict,
    receipt: dict,
    *,
    save_regular_post_protected_state: Callable,
    enqueue_historical_context_obligation: Callable,
    retire_transport_journal: Callable[[], None],
    remove_regular_post_receipt: Callable,
) -> None:
    """Save confirmed state and evidence before retiring recovery authority.

    Every failure propagates unchanged to the caller, preventing later stages.
    The confirmed receipt remains available through historical-context enqueueing;
    its transport journal is retired before receipt removal.
    """
    from mrs_bot_state_generation import record_receipt_commit
    record_receipt_commit(state, receipt)
    commit_proof = save_regular_post_protected_state(lines_used, images_used, state, durable=True)
    enqueue_historical_context_obligation(receipt)
    retire_transport_journal(commit_proof)
    remove_regular_post_receipt(receipt, commit_proof=commit_proof)
