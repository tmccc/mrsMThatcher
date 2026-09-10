"""Legacy reply success keeps pending identity and lane-specific public fields."""

from collections import Counter
from datetime import datetime

import pytest

import mrs_log_digest_legacy_posts as legacy_posts
from mrs_log_digest_records import Record


@pytest.fixture(params=("mention", "hot_post_reply", "quote_tweet"))
def success_case(request):
    lane = request.param
    if lane == "quote_tweet":
        return (
            legacy_posts.handle_legacy_quote_reply,
            "pending_qt",
            "Quote-tweet reply posted successfully",
            {"quote_tweet_id": "123", "original_post_id": "456", "source": "retained public source"},
            "quote_tweet_reply_posted",
        )
    pending = {"mention_id": "123", "source": lane}
    if lane == "hot_post_reply":
        pending["hot_post_reply_id"] = "123"
    return (
        legacy_posts.handle_legacy_mention_reply,
        "pending_mention",
        "Reply posted successfully",
        pending,
        "hot_post_reply_posted" if lane == "hot_post_reply" else "mention_reply_posted",
    )


def test_fallback_identity_is_mutated_before_emission_and_pending_replacement(success_case):
    handler, pending_key, message, pending, expected_kind = success_case
    record = Record(
        datetime(2026, 9, 10, 12), "INFO", "fixture", 1, message,
        "mrsMThatcher.log", 1,
    )
    shared, context, events, authority = [], {"attempt": []}, [], {999}
    pending.update(shared=shared, considered_seq=7, _private="retained internally",
                   _identity_production=True, _reply_post_id_production=False)
    created = {"post_id": "901", "canonical_post_id": False, "production_identity": True}
    original_created = dict(created)

    def add_event(kind, timestamp, **fields):
        assert pending["reply_post_id"] == "901"
        assert pending["_reply_post_id_production"] is True
        assert pending["_private"] == "retained internally"
        assert fields["shared"] is shared
        event = {"kind": kind, "time": timestamp, **fields}
        events.append(event)
        authority.add(id(event))
        return event

    handled, replacement, returned_context = handler(
        record, message, **{pending_key: pending}, record_index=8,
        production_record=True, active_xai_context=context,
        last_created_post=created, production_event_object_ids=authority,
        routine_skip_counts=Counter(), add_event=add_event, lit=lambda value: value,
    )

    event, = events
    assert handled is True and replacement == {} and replacement is not pending
    assert returned_context is context
    assert pending["reply_post_id"] == "901" and pending["shared"] is shared
    assert created == original_created
    assert event["kind"] == expected_kind and event["time"] is record.ts
    assert event["reply_post_id"] == "901" and event["considered_seq"] == 7
    assert not any(key.startswith("_") for key in event)
    assert authority == {999}
    if expected_kind == "quote_tweet_reply_posted":
        assert event["quote_tweet_id"] == "123" and event["original_post_id"] == "456"
        assert event["source"] == "retained public source"
    elif expected_kind == "hot_post_reply_posted":
        assert event["hot_post_reply_id"] == "123"
        assert "mention_id" not in event and "source" not in event
        assert pending["mention_id"] == "123" and pending["source"] == "hot_post_reply"
    else:
        assert event["mention_id"] == "123" and "source" not in event
        assert pending["source"] == "mention"


@pytest.mark.parametrize("untrusted_field", ("_identity_production", "_reply_post_id_production"))
def test_later_created_post_does_not_promote_cached_reply_identity(success_case, untrusted_field):
    handler, pending_key, message, pending, expected_kind = success_case
    record = Record(
        datetime(2026, 9, 10, 12), "INFO", "fixture", 1, message,
        "mrsMThatcher.log", 1,
    )
    pending.update(reply_post_id="888", _identity_production=True, _reply_post_id_production=True)
    pending[untrusted_field] = False
    original_pending = dict(pending)
    events, authority = [], {999}

    def add_event(kind, timestamp, **fields):
        assert pending == original_pending
        event = {"kind": kind, **fields}
        events.append(event)
        authority.add(id(event))
        return event

    handled, replacement, context = handler(
        record, message, **{pending_key: pending}, record_index=8,
        production_record=True, active_xai_context=None,
        last_created_post={"post_id": "901", "canonical_post_id": True, "production_identity": True},
        production_event_object_ids=authority, routine_skip_counts=Counter(),
        add_event=add_event, lit=lambda value: value,
    )

    event, = events
    assert handled is True and replacement == {} and replacement is not pending
    assert context is None and pending == original_pending
    assert event["kind"] == expected_kind and event["reply_post_id"] == "888"
    assert not any(key.startswith("_") for key in event)
    assert authority == {999}
