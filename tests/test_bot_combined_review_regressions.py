"""Regression coverage for cross-lane duplicate delivery with the real fake-API transport."""

import copy
from datetime import datetime, timezone

import pytest

from tests.helpers.bot_runtime import bot
from tests.helpers.reply_fixtures import restore_tweet_lookup_fetch
from tests.helpers.bot_fixtures import (
    _configure_test_x_base,
    isolate_bot_runtime,  # noqa: F401
)
from tests.fake_api_server import FakeApiServer


@pytest.mark.allow_loopback_network
@pytest.mark.parametrize("case", ["fresh", "pending_distinct", "pending_identical", "legacy_quote_only"])
def test_quote_confirmation_prevents_second_public_reply(tmp_path, monkeypatch, case):
    """A confirmed quote reply must preclude a later mention create after reload."""
    clock = [2_000_000_000]
    stamp = lambda epoch: datetime.fromtimestamp(epoch, timezone.utc).isoformat()
    original = {
        "id": "900", "author_id": "12345", "conversation_id": "900",
        "text": "Public institutions require responsibility.",
        "created_at": stamp(clock[0] - 7200),
    }
    target = {
        "id": "910", "author_id": "310", "conversation_id": "910",
        "text": "@MrsMThatcher This account makes a thoughtful point about responsibility.",
        "created_at": stamp(clock[0] - 3600),
        "referenced_tweets": [{"type": "quoted", "id": "900"}],
        "entities": {"mentions": [{"id": "12345", "username": "MrsMThatcher"}]},
    }
    quote_text = "Thank you for the observation."
    pending_text = (
        "Responsibility matters."
        if case != "pending_identical" else quote_text
    )
    scenario = {
        "tweets": {"900": original, "910": target},
        "quote_tweets": {"900": {"data": [target]}},
        "mentions": [target],
        "openai_replies": (
            [quote_text, pending_text] if case == "fresh"
            else [pending_text, quote_text]
        ),
    }
    server = FakeApiServer(scenario).start()
    try:
        _configure_test_x_base(monkeypatch, server.url)
        monkeypatch.setattr(bot, "OPENAI_BASE", server.url + "/v1")
        # Restore the real lookup, replaced by the existing unit isolation fixture.
        restore_tweet_lookup_fetch(monkeypatch)
        monkeypatch.setattr(bot, "single_call_reply", {**bot.single_call_reply, "enabled": True})
        monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
        monkeypatch.setattr(bot, "ENABLE_QUOTE_TWEET_CHECKS", True)
        monkeypatch.setattr(bot, "ENABLE_HOT_POST_REPLY_CHECKS", False)
        monkeypatch.setattr(bot, "EXTRA_QUOTE_WATCH_FILE", tmp_path / "extra-watch.txt")
        monkeypatch.setattr(bot, "now_epoch", lambda: clock[0])
        monkeypatch.setattr(bot, "current_utc_datetime", lambda: datetime.fromtimestamp(clock[0], timezone.utc))
        assert bot.MIN_SECONDS_BETWEEN_REPLIES == 900
        assert (bot.MAX_AUTO_REPLIES_PER_DAY, bot.MAX_REPLIES_PER_AUTHOR_PER_DAY,
                bot.MAX_QUOTE_REPLIES_PER_DAY) == (48, 6, 12)
        state = bot.default_state()
        state["recent_own_post_ids"] = ["900"]
        if case != "fresh":
            prepared_context = bot.build_context_for_reply_ai(copy.deepcopy(target), state)
            assert prepared_context is not None
            context = prepared_context.context
            assert prepared_context is not None
            draft = bot.generate_single_call_reply(context, state=state)
            assert draft == pending_text
            assert bot.store_pending_ai_reply(state, "910", "mention", draft, context=context)
        original_drafts = copy.deepcopy(state.get("pending_ai_reply_drafts", {}))
        bot.save_state(state, durable=True)

        assert bot.maybe_reply_to_quote_tweets(state) == bot.QUOTE_CHECK_STATUS_POSTED
        assert [post["reply"]["in_reply_to_tweet_id"] for post in server.posts] == ["910"]
        assert not bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()
        quote_state = copy.deepcopy(state)
        assert state["daily_reply_count"] == state["daily_quote_reply_count"] == 1
        assert len(state["ai_reply_history"]) == 1
        assert bot.terminal_reply_evaluation(state, "910") is None
        assert not state.get("pending_ai_reply_drafts")
        assert state["replied_to_ids"] == []
        assert state["replied_to_quote_post_ids"] == ["910"]
        if case == "legacy_quote_only":
            # Pre-upgrade quote-only IDs and a persisted draft remain eligible
            # for recovery, but must never generate or publish a second reply.
            state["pending_ai_reply_drafts"] = original_drafts
            bot.save_state(state, durable=True)

        # Real durable reload plus elapsed source-default spacing, still same day.
        clock[0] += 901
        state = bot.load_state()
        assert state["ai_reply_history"] == quote_state["ai_reply_history"]
        provider_calls_before_normal = len(server.openai_requests)
        status = bot.maybe_reply_to_mentions(state)
        assert len(server.openai_requests) == provider_calls_before_normal
        assert [post["reply"]["in_reply_to_tweet_id"] for post in server.posts] == ["910"]
        assert state["daily_reply_count"] == state["daily_quote_reply_count"] == 1
        assert bot.daily_author_reply_count(state, "310") == 1
        assert bot.terminal_reply_evaluation(state, "910") is None
    finally:
        server.stop()
