"""Build exact source and confirmed receipt documents for all posting lanes."""
from __future__ import annotations

import copy
import hashlib

import historical_context_formatter as context
import mrsMThatcher2 as bot


def main_post_source(lane: str) -> dict:
    """Build a valid main-post attempt with a deterministic identity."""

    if lane == "quote_image":
        text = "A reviewed quotation."
        quote_hash = bot.quote_text_hash(text)
        source = bot.build_main_post_attempt(
            lane=lane,
            text=text,
            media_ids=["media-1"],
            made_with_ai=False,
            selected_identity={
                "quote_hash": quote_hash,
                "line_no": 0,
                "source_line_number": 1,
                "image_basename": "reviewed.jpg",
                "image_no": 0,
            },
            recovery_plan={
                "quote_delay_seconds": 7200,
                "meme_delay_seconds": 3600,
                "meme_scheduling_enabled": True,
                "meme_trigger_after_hour": 12,
                "meme_schedule_version": 2,
                "schedule_timezone": bot.MAIN_POST_SCHEDULE_TIMEZONE,
                "meme_schedule_before": bot.bound_meme_schedule_state(
                    {},
                    schedule_timezone=bot.MAIN_POST_SCHEDULE_TIMEZONE,
                ),
                "quote_history_after": [quote_hash],
                "image_history_after": ["reviewed.jpg"],
            },
            attempt_epoch=1_800_000_000,
        )
    else:
        source = bot.build_main_post_attempt(
            lane=lane,
            text=bot.MEME_POST_TEXT,
            media_ids=["media-1"],
            made_with_ai=False,
            selected_identity={"meme_basename": "001_meme.png"},
            recovery_plan={
                "next_schedule_mode": "fallback",
                "meme_schedule_version": 2,
                "fallback_hour": 16,
                "fallback_minute": 0,
                "image_summary": "A reviewed poster.",
                "schedule_timezone": bot.MAIN_POST_SCHEDULE_TIMEZONE,
            },
            attempt_epoch=1_800_000_000,
        )
    source["attempt_id"] = hashlib.sha256(
        f"restart-{lane}".encode("utf-8")
    ).hexdigest()
    source["lifecycle_state"] = "attempting"
    assert bot.main_post_attempt_is_semantically_valid(source)
    return source


def production_lane_documents(
    lane: str,
) -> tuple[dict, dict, bytes, bytes, str]:
    """Build source/confirmed documents through each production serializer."""

    post_id = {
        "quote_image": "951001",
        "daily_meme": "951002",
        "conversational_reply": "951003",
        "historical_context_reply": "951004",
    }[lane]
    if lane in {"quote_image", "daily_meme"}:
        source = main_post_source(lane)
        pending = bot.build_confirmed_pending_schedule_receipt(
            source,
            post_id=post_id,
            confirmation_epoch=1_800_000_010,
            image_summary=(
                str(source["recovery_plan"]["image_summary"])
                if lane == "daily_meme"
                else ""
            ),
        )
        confirmed = (
            bot.materialize_bound_regular_schedule_receipt(pending)
            if lane == "quote_image"
            else bot.materialize_bound_meme_schedule_receipt(pending)
        )
        source_bytes = bot.canonical_atomic_json_bytes(source)
        current_bytes = bot.canonical_atomic_json_bytes(confirmed)
    elif lane == "conversational_reply":
        source = {
            "schema_version": 4,
            "lifecycle_state": "sending",
            "target_id": "111",
            "author_id": "42",
            "candidate_source": "mention",
            "conversation_id": "111",
            "reply_text": "A reviewed reply.",
            "reply_context": {
                "target_id": "111",
                "thread_id": "111",
                "lane": "mention",
            },
            "ai_reply_draft": {},
            "attempt_epoch": 1_800_000_000,
            "reply_epoch": 1_800_000_000,
            "daily_reply_date": bot.epoch_date_str(1_800_000_000),
        }
        confirmed = bot._reply_assembly()._reply_receipt_values_owner().confirmed_from_sending(
            source,
            reply_post_id=post_id,
            confirmation_epoch=1_800_000_010,
        )
        source_bytes = bot.canonical_atomic_json_bytes(source)
        current_bytes = bot.canonical_atomic_json_bytes(confirmed)
    else:
        source = {
            "schema_version": 1,
            "lifecycle_state": "sending",
            "parent_post_id": "111",
            "quote_id": "a" * 64,
            "reply_text": "Verified historical context.",
            "reply_epoch": 1_800_000_000,
            "started_at": "2027-01-15T08:00:00Z",
            "attempt_number": 1,
        }
        source_bytes = context.canonical_json_bytes(source)
        confirmed = {
            **copy.deepcopy(source),
            "lifecycle_state": "confirmed",
            "reply_post_id": post_id,
            "confirmed_at": "2027-01-15T08:00:10Z",
            "source_receipt_sha256": hashlib.sha256(source_bytes).hexdigest(),
        }
        assert context.HistoricalContextReplyStore._valid_receipt(confirmed)
        current_bytes = context.canonical_json_bytes(confirmed)

    reconstructed = bot.expected_lane_transport_source_receipt_bytes(
        receipt=confirmed,
        lane=lane,
        current_receipt_bytes=current_bytes,
    )
    assert reconstructed == source_bytes
    return source, confirmed, source_bytes, current_bytes, post_id
