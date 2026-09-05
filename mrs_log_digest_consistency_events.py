"""Passive production-consistency event projections and prepared reporting.

The coordinator supplies current field helpers, timestamps, statistics and event
insertion. Displayed IDs confer no publication authority; this owner performs no
I/O, source selection or clock sampling.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional


def record_reply_evidence_unavailable(
    event_obj: Dict[str, Any],
    ts: datetime,
    stats: Counter,
    *,
    add_event: Callable[..., Dict[str, Any]],
    bounded_event_text: Callable[..., str],
    valid_string_public_post_id: Callable[[Any], bool],
) -> None:
    """Project reply evidence unavailable fields and update supplied counts."""
    lane = bounded_event_text(
        event_obj.get("lane"),
        default="unavailable",
        max_characters=100,
    )
    add_event(
        "reply_evidence_unavailable",
        ts,
        lane=lane,
        target_id=(
            event_obj.get("target_id")
            if valid_string_public_post_id(
                event_obj.get("target_id")
            )
            else ""
        ),
    )
    stats[f"reply_evidence_unavailable_lane_{lane}"] += 1


def record_runtime_control_pause(
    event_obj: Dict[str, Any],
    ts: datetime,
    stats: Counter,
    *,
    add_event: Callable[..., Dict[str, Any]],
    bounded_event_string_list: Callable[..., List[str]],
    bounded_event_text: Callable[..., str],
    bounded_event_nonnegative_integer: Callable[..., Optional[int]],
) -> None:
    """Project runtime control pause fields and update supplied counts."""
    control_lanes = bounded_event_string_list(
        event_obj.get("lanes"), limit=20, item_max_characters=100
    )
    add_event(
        "runtime_control_pause",
        ts,
        key=bounded_event_text(
            event_obj.get("key"),
            default="unavailable",
            max_characters=200,
        ),
        lanes=", ".join(control_lanes),
        control_lanes=control_lanes,
        until_epoch=bounded_event_nonnegative_integer(
            event_obj.get("until_epoch")
        ),
    )
    stats["runtime_control_pause"] += 1


def record_runtime_control_clear(
    event_obj: Dict[str, Any],
    ts: datetime,
    stats: Counter,
    *,
    add_event: Callable[..., Dict[str, Any]],
    bounded_event_string_list: Callable[..., List[str]],
    bounded_event_text: Callable[..., str],
) -> None:
    """Project runtime control clear fields and update supplied counts."""
    control_lanes = bounded_event_string_list(
        event_obj.get("lanes"), limit=20, item_max_characters=100
    )
    add_event(
        "runtime_control_clear",
        ts,
        key=bounded_event_text(
            event_obj.get("key"),
            default="unavailable",
            max_characters=200,
        ),
        lanes=", ".join(control_lanes),
        control_lanes=control_lanes,
    )
    stats["runtime_control_clear"] += 1


def record_clarification_reply_cap_override(
    event_obj: Dict[str, Any],
    ts: datetime,
    stats: Counter,
    *,
    add_event: Callable[..., Dict[str, Any]],
    valid_string_public_post_id: Callable[[Any], bool],
    bounded_event_text: Callable[..., str],
) -> None:
    """Project clarification reply cap override fields and update supplied counts."""
    add_event(
        "clarification_reply_cap_override",
        ts,
        target_id=(
            event_obj.get("target_id")
            if valid_string_public_post_id(
                event_obj.get("target_id")
            )
            else ""
        ),
        thread_id=(
            event_obj.get("thread_id")
            if valid_string_public_post_id(
                event_obj.get("thread_id")
            )
            else ""
        ),
        author_id=(
            event_obj.get("author_id")
            if valid_string_public_post_id(
                event_obj.get("author_id")
            )
            else ""
        ),
        bypassed_cap=bounded_event_text(
            event_obj.get("bypassed_cap"),
            default="",
            max_characters=100,
        ),
    )
    stats["clarification_reply_cap_override"] += 1


def record_clarification_reply_used(
    event_obj: Dict[str, Any],
    ts: datetime,
    stats: Counter,
    *,
    add_event: Callable[..., Dict[str, Any]],
    valid_string_public_post_id: Callable[[Any], bool],
    bounded_event_text: Callable[..., str],
) -> None:
    """Project clarification reply used fields and update supplied counts."""
    add_event(
        "clarification_reply_used",
        ts,
        target_id=(event_obj.get("target_id") if valid_string_public_post_id(event_obj.get("target_id")) else ""),
        thread_id=(event_obj.get("thread_id") if valid_string_public_post_id(event_obj.get("thread_id")) else ""),
        author_id=(event_obj.get("author_id") if valid_string_public_post_id(event_obj.get("author_id")) else ""),
        reply_post_id=(event_obj.get("reply_post_id") if valid_string_public_post_id(event_obj.get("reply_post_id")) else ""),
        trigger=bounded_event_text(
            event_obj.get("trigger"),
            default="",
            max_characters=100,
        ),
    )
    stats["clarification_reply_used"] += 1


def record_repair_reply_completed(
    event_obj: Dict[str, Any],
    ts: datetime,
    stats: Counter,
    *,
    add_event: Callable[..., Dict[str, Any]],
    valid_string_public_post_id: Callable[[Any], bool],
) -> None:
    """Project repair reply completed fields and update supplied counts."""
    add_event(
        "repair_reply_completed",
        ts,
        target_id=(event_obj.get("target_id") if valid_string_public_post_id(event_obj.get("target_id")) else ""),
        thread_id=(event_obj.get("thread_id") if valid_string_public_post_id(event_obj.get("thread_id")) else ""),
        author_id=(event_obj.get("author_id") if valid_string_public_post_id(event_obj.get("author_id")) else ""),
        reply_post_id=(event_obj.get("reply_post_id") if valid_string_public_post_id(event_obj.get("reply_post_id")) else ""),
    )
    stats["repair_reply_completed"] += 1


def record_posting_transaction_state(
    event_obj: Dict[str, Any],
    ts: datetime,
    stats: Counter,
    *,
    add_event: Callable[..., Dict[str, Any]],
    bounded_event_text: Callable[..., str],
    valid_string_public_post_id: Callable[[Any], bool],
    bounded_event_boolean: Callable[[Any], Optional[bool]],
) -> None:
    """Project posting transaction state fields and update supplied counts."""
    context_state = bounded_event_text(
        event_obj.get("context_reply_state"),
        default="unavailable",
        max_characters=100,
    )
    add_event(
        "posting_transaction_state",
        ts,
        parent_post_id=(
            event_obj.get("parent_post_id")
            if valid_string_public_post_id(
                event_obj.get("parent_post_id")
            )
            else ""
        ),
        main_post_state=bounded_event_text(
            event_obj.get("main_post_state"),
            default="unavailable",
            max_characters=100,
        ),
        context_reply_state=context_state,
        context_state_persisted=bounded_event_boolean(
            event_obj.get("context_state_persisted")
        ),
        reason=bounded_event_text(
            event_obj.get("reason"),
            default="",
            max_characters=1000,
        ),
    )
    stats[f"context_transaction_state_{context_state}"] += 1


def record_daily_meme_failure(
    event_obj: Dict[str, Any],
    ts: datetime,
    stats: Counter,
    *,
    add_event: Callable[..., Dict[str, Any]],
    bounded_event_text: Callable[..., str],
    valid_string_public_post_id: Callable[[Any], bool],
) -> None:
    """Project daily meme failure fields and update supplied counts."""
    stage = bounded_event_text(
        event_obj.get("stage"),
        default="unavailable",
        max_characters=100,
    )
    add_event(
        "daily_meme_failure",
        ts,
        status=bounded_event_text(
            event_obj.get("status"),
            default="failed",
            max_characters=100,
        ),
        stage=stage,
        post_id=(
            event_obj.get("post_id")
            if valid_string_public_post_id(
                event_obj.get("post_id")
            )
            else ""
        ),
        error_type=bounded_event_text(
            event_obj.get("error_type"),
            default="",
            max_characters=200,
        ),
        reason=bounded_event_text(
            event_obj.get("reason"),
            default="",
            max_characters=1000,
        ),
    )
    stats[f"daily_meme_failure_stage_{stage}"] += 1


def production_consistency_report(
    events: List[Dict[str, Any]], stats: Counter,
) -> Dict[str, Any]:
    """Select shared event rows and take the five ordered counter observations."""
    return {
        "events": [
            item
            for item in events
            if item.get("kind")
            in {
                "historical_context_runtime",
                "reply_evidence_unavailable",
                "runtime_control_pause",
                "runtime_control_clear",
                "clarification_reply_cap_override",
                "clarification_reply_used",
                "repair_reply_completed",
                "posting_transaction_state",
                "historical_context_obligation",
                "historical_context_outbox",
                "daily_meme_failure",
            }
        ],
        "context_transaction_state_counts": {
            key.removeprefix("context_transaction_state_"): value
            for key, value in sorted(stats.items())
            if key.startswith("context_transaction_state_")
        },
        "context_obligation_state_counts": {
            key.removeprefix("context_obligation_state_"): value
            for key, value in sorted(stats.items())
            if key.startswith("context_obligation_state_")
        },
        "daily_meme_failure_stage_counts": {
            key.removeprefix("daily_meme_failure_stage_"): value
            for key, value in sorted(stats.items())
            if key.startswith("daily_meme_failure_stage_")
        },
        "historical_context_runtime_status_counts": {
            key.removeprefix("historical_context_runtime_status_"): value
            for key, value in sorted(stats.items())
            if key.startswith("historical_context_runtime_status_")
        },
        "reply_evidence_unavailable_lane_counts": {
            key.removeprefix("reply_evidence_unavailable_lane_"): value
            for key, value in sorted(stats.items())
            if key.startswith("reply_evidence_unavailable_lane_")
        },
    }
