"""Share validated-draft persistence and schema-v4 sending-receipt copies.

The lanes retain validation guards, log wording, status mapping, provenance and
attempt binding. Supplied callbacks run at the existing persistence boundaries;
local failures propagate to the caller before transport handling. Import does
no runtime I/O and retains no configuration, callbacks or caller state.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mrs_bot_reply_cycle_interfaces import ReplyCyclePersistence


def persist_validated_reply_draft(
    state: dict,
    target_id: str,
    candidate_source: str,
    reply_text: object,
    reply_context: dict,
    *,
    SINGLE_CALL_STRATEGY_VERSION: str,
    persistence: ReplyCyclePersistence,
    log_validation_failure: Callable[[], None],
    log_event: Callable,
) -> bool:
    """Store and durably save a draft, reporting validation failure before saving."""
    draft_stored = persistence.store(
        state,
        target_id,
        str(candidate_source),
        reply_text,
        context=reply_context,
    )
    if not draft_stored:
        log_validation_failure()
        log_event(
            "single_call_reply_posting_outcome",
            status="draft_persistence_failed",
            lane=str(candidate_source),
            target_id=target_id,
            strategy_version=SINGLE_CALL_STRATEGY_VERSION,
            reply_kind=reply_text.draft_record.get("reply_kind"),
            reason_code=reply_text.draft_record.get("reason_code"),
            validated_draft_hash=reply_text.draft_record.get(
                "validated_draft_hash"
            ),
            failure_reason="draft_persistence_validation_failed",
        )
        persistence.save(state, durable=True)
        return False
    persistence.save(state, durable=True)
    return True


def build_sending_reply_receipt(receipt_fields: dict, reply_context: dict) -> dict:
    """Add the common sending schema and independent context/draft snapshots.

    The lane builds its identity fields only after the durable draft save. Keep
    those fields, including the validated reply object, by reference and copy
    context before draft with separate deepcopy calls.
    """
    return {
        "schema_version": 4,
        "lifecycle_state": "sending",
        **receipt_fields,
        "reply_context": copy.deepcopy(reply_context),
        "ai_reply_draft": copy.deepcopy(receipt_fields["reply_text"].draft_record),
    }
