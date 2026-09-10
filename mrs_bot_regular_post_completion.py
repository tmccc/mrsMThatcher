"""Shared durable completion of confirmed regular quote/image posts.

Callers apply their live or recovered state first and retain their own error
handling. The event and journal callbacks defer caller-specific field reads
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
    log_confirmed_engagement_experiment_receipt: Callable,
    emit_experiment_event: Callable[[], None],
    enqueue_historical_context_obligation: Callable,
    retire_transport_journal: Callable[[], None],
    remove_regular_post_receipt: Callable,
    publish_pending_engagement_question_notification: Callable,
) -> None:
    """Save confirmed state and evidence before retiring recovery authority.

    Every failure propagates unchanged to the caller, preventing later stages.
    The confirmed receipt remains available through experiment evidence emission
    and historical-context enqueueing; its transport journal is retired before
    receipt removal and pending notification publication.
    """
    save_regular_post_protected_state(lines_used, images_used, state, durable=True)
    log_confirmed_engagement_experiment_receipt(receipt)
    emit_experiment_event()
    enqueue_historical_context_obligation(receipt)
    retire_transport_journal()
    remove_regular_post_receipt(receipt)
    publish_pending_engagement_question_notification(state)
