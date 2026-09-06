from __future__ import annotations

import copy
from datetime import datetime
import inspect
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock, call

import pytest

import mrs_bot_quote_reply_cycle as cycle
from tests.test_unit_helpers import (
    SCENARIOS,
    bot,
    isolate_regular_post_receipt,
    load_scenario,
    unit_approved_reply,
    unit_confirmed_v4_reply_receipt,
)


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, io, logging, os, random, socket, sys
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('quote reply cycle import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'reply_evidence'} or name.startswith('mrs_bot_') and name != 'mrs_bot_quote_reply_cycle':
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = forbidden
os.getenv = os._Environ.__getitem__ = forbidden
Path.home = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
before = random.getstate()
random.Random = random.seed = random.random = forbidden
import mrs_bot_quote_reply_cycle
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
assert 'requests' not in sys.modules
assert 'single_call_reply' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_adapters_forward_current_dependencies_arguments_results_and_errors(monkeypatch):
    names = {
        "quote_tweet_is_old_enough": 4,
        "quote_tweet_directly_quotes_original": 2,
        "build_quote_tweet_reply_context": 10,
        "mark_quote_tweet_skipped": 1,
        "mark_quote_tweet_replied": 2,
        "mark_quote_spam_author": 2,
        "maybe_reply_to_quote_tweets": 78,
    }
    for name, count in names.items():
        adapter = getattr(bot, name)
        public = inspect.signature(adapter).parameters
        dependencies = inspect.signature(getattr(cycle, name)).parameters.keys() - public.keys()
        assert len(dependencies) == count
        args = tuple(object() for _ in public)
        result = object()
        owner = Mock(return_value=result)
        with monkeypatch.context() as patch:
            patch.setattr(cycle, name, owner)
            for _ in range(2):
                current = {key: object() for key in dependencies}
                for key, value in current.items():
                    patch.setattr(bot, key, value)
                assert adapter(*args) is result, name
                actual_args, actual_kwargs = owner.call_args
                assert len(actual_args) == len(args)
                assert all(actual is expected for actual, expected in zip(actual_args, args))
                assert actual_kwargs.keys() == current.keys()
                assert all(actual_kwargs[key] is value for key, value in current.items())
            failure = TypeError(name)
            owner.side_effect = failure
            with pytest.raises(TypeError) as caught:
                adapter(*args)
            assert caught.value is failure


def test_age_uses_current_parser_clock_delay_and_native_errors(monkeypatch):
    quote = {"id": "910", "created_at": object()}
    parser, clock, log = Mock(return_value=100), Mock(return_value=199), Mock()
    monkeypatch.setattr(bot, "parse_x_datetime_to_epoch", parser)
    monkeypatch.setattr(bot, "now_epoch", clock)
    monkeypatch.setattr(bot, "QUOTE_REPLY_DELAY_SECONDS", 100)
    monkeypatch.setattr(bot, "log", log)
    assert bot.quote_tweet_is_old_enough(quote) is False
    clock.return_value = 200
    assert bot.quote_tweet_is_old_enough(quote) is True
    parser.assert_called_with(quote["created_at"])
    assert parser.call_args.args[0] is quote["created_at"]
    log.debug.assert_called_with(
        "Quote tweet id=%s age_seconds=%s required_delay=%s", "910", 100, 100,
    )
    parser.return_value = None
    clock.reset_mock()
    assert bot.quote_tweet_is_old_enough(quote) is False
    clock.assert_not_called()
    assert "retried later" in log.warning.call_args.args[0]
    failure = TypeError("current parser failed")
    parser.side_effect = failure
    with pytest.raises(TypeError) as caught:
        bot.quote_tweet_is_old_enough(quote)
    assert caught.value is failure


def test_direct_quote_precedence_conservative_fallback_and_malformed_references(monkeypatch):
    cleaner = Mock(wraps=bot.clean_text_for_reply_context)
    monkeypatch.setattr(bot, "clean_text_for_reply_context", cleaner)
    quoted = {"type": "quoted", "id": 900}
    retweeted = {"type": "retweeted", "id": "800"}
    for refs in ([quoted, retweeted], [retweeted, quoted]):
        assert not bot.quote_tweet_directly_quotes_original({"referenced_tweets": refs}, "900")
    assert bot.quote_tweet_directly_quotes_original(
        {"referenced_tweets": [quoted], "text": "RT @someone: legacy text"}, "900",
    )
    cleaner.assert_not_called()
    for text in ("RT @someone: legacy text", "Ambiguous commentary"):
        assert not bot.quote_tweet_directly_quotes_original({"text": text}, 900)
        cleaner.assert_called_with(text)
    with pytest.raises(AttributeError):
        bot.quote_tweet_directly_quotes_original({"referenced_tweets": [None, quoted]}, "900")


def test_profile_alias_preserves_coercion_whitespace_metrics_and_native_shape_errors():
    assert bot.quote_author_profile_text is cycle.quote_author_profile_text
    assert bot.quote_author_profile_text({"_author_user": None}) == ""
    assert bot.quote_author_profile_text({"_author_user": {
        "name": "  Reader  ", "username": None, "description": " \t",
        "public_metrics": {"followers_count": 0, "following_count": None},
    }}) == "  Reader  \nNone\nfollowers=0 following=None tweets="
    for user in ([1], {"public_metrics": [1]}):
        with pytest.raises(AttributeError):
            bot.quote_author_profile_text({"_author_user": user})


def test_context_preserves_budget_roles_reference_boundaries_and_media_before_summary(monkeypatch):
    original = {"id": 900, "text": "original account text " * 20}
    quote = {
        "id": 910, "author_id": 310, "conversation_id": 911,
        "created_at": "2026-06-30T10:00:00Z", "text": "target commentary " * 10,
    }
    before = copy.deepcopy((original, quote))
    trace = Mock()
    for name in (
        "_reply_context_post", "tweet_context_text", "trim_context_text",
        "bound_visible_conversation", "_log_single_call_context_summary",
    ):
        callback = Mock(wraps=getattr(bot, name))
        trace.attach_mock(callback, name)
        monkeypatch.setattr(bot, name, callback)
    media = {"photos": []}
    trace.attach_mock(Mock(return_value=media), "media")
    monkeypatch.setattr(bot, "reply_media_context_for_candidate", trace.media)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2030, 2, 3))
    monkeypatch.setattr(bot, "REPLY_INCOMING_MAX_CHARS", 30)
    monkeypatch.setattr(bot, "MAX_VISIBLE_TEXT_CHARACTERS", 60)

    context = bot.build_quote_tweet_reply_context(original, quote)

    assert [c[0] for c in trace.mock_calls] == [
        "_reply_context_post", "tweet_context_text", "trim_context_text",
        "tweet_context_text", "trim_context_text", "bound_visible_conversation",
        "media", "_log_single_call_context_summary",
    ]
    assert trace._reply_context_post.call_args.args[0] is quote
    assert trace._reply_context_post.call_args.kwargs == {"principal_author_id": "310", "maximum_chars": 30}
    assert trace.tweet_context_text.call_args.args[0] is original
    assert trace.trim_context_text.call_args.args[1] == 60 - len(context["incoming_contribution"])
    original_turn, target_turn = trace.bound_visible_conversation.call_args.args[0]
    assert original_turn["author_role"] == "account"
    assert target_turn["author_role"] == "user"
    assert [turn["post_id"] for turn in context["visible_conversation"]] == ["900", "910"]
    assert sum(len(turn["text"]) for turn in context["visible_conversation"]) <= 60
    assert context["quoted_post"] == context["parent_thread"][0] == original_turn
    assert context["quoted_post"] is not original_turn
    assert context["parent_thread"][0] is not original_turn
    assert context["quoted_post"] is not context["parent_thread"][0]
    assert context["current_date"] == "2030-02-03"
    assert context["thread_id"] == "911"
    assert context["target_author_id"] == "310"
    assert context["target_created_at"] == quote["created_at"]
    assert context["_prepared_media_context"] is media
    assert trace.media.call_args.args[0] is quote
    assert trace.media.call_args.kwargs["quoted_candidate"] is original
    assert trace._log_single_call_context_summary.call_args.args[1] is context
    context["quoted_post"]["text"] = "changed copy"
    assert context["parent_thread"][0]["text"] == original_turn["text"]
    assert (original, quote) == before


def test_markers_preserve_bounded_and_durable_lists_and_mutation_before_failure(monkeypatch):
    state = {
        "seen_quote_post_ids": [str(i) for i in range(2000)],
        "skipped_quote_post_ids": [str(i) for i in range(2000)],
        "replied_to_quote_post_ids": [str(i) for i in range(2500)],
        "quote_spam_author_ids": [str(i) for i in range(2000)],
    }
    capped, durable = Mock(wraps=bot.append_unique_capped), Mock(wraps=bot.append_unique_durable)
    monkeypatch.setattr(bot, "append_unique_capped", capped)
    monkeypatch.setattr(bot, "append_unique_durable", durable)
    seen = state["seen_quote_post_ids"]
    skipped = state["skipped_quote_post_ids"]
    bot.mark_quote_tweet_skipped(state, 3000)
    assert capped.call_args_list == [call(seen, "3000", 2000), call(skipped, "3000", 2000)]
    assert capped.call_args_list[0].args[0] is seen
    assert capped.call_args_list[1].args[0] is skipped
    bot.mark_quote_tweet_replied(state, 3001)
    assert len(state["seen_quote_post_ids"]) == len(state["skipped_quote_post_ids"]) == 2000
    assert len(state["replied_to_quote_post_ids"]) == 2501
    assert state["replied_to_quote_post_ids"][0] == "0"
    failure = ValueError("durable helper failed")
    durable.side_effect = failure
    with pytest.raises(ValueError) as caught:
        bot.mark_quote_tweet_replied(state, 3002)
    assert caught.value is failure
    assert state["seen_quote_post_ids"][-1] == "3002"
    assert "3002" not in state["replied_to_quote_post_ids"]
    log = Mock()
    monkeypatch.setattr(bot, "log", log)
    bot.mark_quote_spam_author(state, 3003)
    assert len(state["quote_spam_author_ids"]) == 2000
    assert state["quote_spam_author_ids"][-1] == "3003"
    assert log.info.call_args.args[1:] == ("3003", 2000)


def _configure_cycle(monkeypatch):
    scenario = load_scenario(SCENARIOS / "quote_tweet_reply.json")
    original = scenario["tweets"]["900"]
    quotes = scenario["quote_tweets"]["900"]["data"]
    epoch = 2_000_000_000
    monkeypatch.setattr(bot, "now_epoch", lambda: epoch)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(epoch))
    monkeypatch.setattr(bot, "single_call_reply", {**bot.single_call_reply, "enabled": True})
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "ENABLE_QUOTE_TWEET_CHECKS", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "build_quote_lookup_post_ids", Mock(return_value=["900"]))
    monkeypatch.setattr(bot, "get_tweet_by_id_cached", Mock(return_value=original))
    monkeypatch.setattr(bot, "get_quote_tweets_for_post", Mock(return_value=quotes))
    monkeypatch.setattr(bot, "reply_media_context_for_candidate", Mock(return_value={}))
    monkeypatch.setattr(bot, "x_request", Mock(side_effect=AssertionError("unexpected provider request")))
    monkeypatch.setattr(bot, "create_post", Mock(side_effect=AssertionError("unexpected remote write")))
    return original, quotes


def test_both_daily_resets_and_confirmed_reconciliation_precede_barrier_when_disabled(monkeypatch):
    _configure_cycle(monkeypatch)
    receipt = unit_confirmed_v4_reply_receipt(lane="quote_tweet", confirmation_epoch=bot.now_epoch())
    bot.write_confirmed_reply_receipt(receipt)
    state = bot.default_state()
    state.update(daily_reply_date="2000-01-01", daily_reply_count=48,
                 daily_quote_reply_date="2000-01-01", daily_quote_reply_count=12)
    monkeypatch.setattr(bot, "ENABLE_QUOTE_TWEET_CHECKS", False)
    trace = Mock()
    for name in ("reset_daily_reply_count_if_needed", "reset_daily_quote_reply_count_if_needed",
                 "reconcile_confirmed_reply_receipt"):
        callback = Mock(wraps=getattr(bot, name))
        trace.attach_mock(callback, name)
        monkeypatch.setattr(bot, name, callback)
    original_barrier = bot.block_if_ambiguous_remote_post

    def barrier():
        saved = json.loads(bot.STATE_FILE.read_text())
        assert saved["daily_reply_count"] == state["daily_reply_count"] == 1
        assert saved["daily_quote_reply_count"] == state["daily_quote_reply_count"] == 1
        assert saved["replied_to_quote_post_ids"] == [receipt["target_id"]]
        assert not bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()
        return original_barrier()

    trace.attach_mock(Mock(side_effect=barrier), "barrier")
    monkeypatch.setattr(bot, "block_if_ambiguous_remote_post", trace.barrier)
    assert bot.maybe_reply_to_quote_tweets(state) == bot.QUOTE_CHECK_STATUS_DISABLED
    assert [c[0] for c in trace.mock_calls] == [
        "reset_daily_reply_count_if_needed", "reset_daily_quote_reply_count_if_needed",
        "reconcile_confirmed_reply_receipt", "barrier",
    ]
    bot.build_quote_lookup_post_ids.assert_not_called()


def test_zero_call_failures_consume_quote_candidate_limit_in_numeric_order(monkeypatch):
    original, quotes = _configure_cycle(monkeypatch)
    quotes[:] = [dict(quotes[0], id=target, conversation_id=target) for target in ("100", "9", "20")]
    monkeypatch.setattr(bot, "MAX_QUOTE_POSTS_PER_CHECK", 2)
    bot.build_quote_lookup_post_ids.return_value = ["900", "901"]
    state = bot.default_state()
    contexts, media = [], []

    def reject(context, prepared_media, *, state, evaluation_outcome):
        contexts.append(context)
        media.append(prepared_media)
        assert "_prepared_media_context" not in context
        saved = json.loads(bot.STATE_FILE.read_text())
        assert context["target_id"] in saved["tweet_cache"]
        evaluation_outcome.update(status="operational_failure", error_category="image_input",
                                  reason="unusable_image", model_call_count=0)

    generate = Mock(side_effect=reject)
    monkeypatch.setattr(bot, "generate_single_call_reply", generate)
    assert bot.maybe_reply_to_quote_tweets(state) == bot.QUOTE_CHECK_STATUS_CHECKED
    assert [c["target_id"] for c in contexts] == ["9", "20"]
    assert all(item is bot.reply_media_context_for_candidate.return_value for item in media)
    assert bot.reply_media_context_for_candidate.call_count == 2
    assert all(c.kwargs["quoted_candidate"] is original for c in bot.reply_media_context_for_candidate.call_args_list)
    assert bot.get_tweet_by_id_cached.call_args_list == [
        call("900", state), call("900", state, include_media=True), call("900", state, include_media=True),
    ]
    assert state["skipped_quote_post_ids"] == ["9", "20"]
    assert set(json.loads(bot.STATE_FILE.read_text())["reply_evaluation_records"]) == {"9", "20"}
    assert state["daily_reply_count"] == state["daily_quote_reply_count"] == 0
    bot.create_post.assert_not_called()


def test_reused_draft_keeps_context_references_durability_and_pre_send_availability(monkeypatch):
    original, quotes = _configure_cycle(monkeypatch)
    state = bot.default_state()
    context = bot.build_quote_tweet_reply_context(original, quotes[0])
    context.pop("_prepared_media_context")
    reply = unit_approved_reply(context)
    assert bot.store_pending_ai_reply(state, "910", "quote_tweet", reply, context=context)
    bot.save_state(state, durable=True)
    bot.reply_media_context_for_candidate.reset_mock()
    trace = Mock()
    for name in ("cache_tweet", "save_state", "build_quote_tweet_reply_context",
                 "reply_evidence_repository", "recovery_comparison_account_replies",
                 "pending_ai_reply", "store_pending_ai_reply", "bind_conversational_reply_attempt_time"):
        callback = Mock(wraps=getattr(bot, name))
        trace.attach_mock(callback, name)
        monkeypatch.setattr(bot, name, callback)
    monkeypatch.setattr(bot, "generate_single_call_reply", Mock(side_effect=AssertionError("draft must be reused")))

    def unavailable(target_id):
        assert target_id == "910"
        assert json.loads(bot.STATE_FILE.read_text())["pending_ai_reply_drafts"]
        assert not bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()
        return False

    trace.attach_mock(Mock(side_effect=unavailable), "available")
    monkeypatch.setattr(bot, "reply_target_is_available_immediately_before_send", trace.available)
    assert bot.maybe_reply_to_quote_tweets(state) == bot.QUOTE_CHECK_STATUS_CHECKED
    names = [c[0] for c in trace.mock_calls]
    assert names[:4] == ["cache_tweet", "save_state", "build_quote_tweet_reply_context", "reply_evidence_repository"]
    assert names.index("recovery_comparison_account_replies") < names.index("pending_ai_reply")
    store_index = names.index("store_pending_ai_reply")
    send_names = [name for name in names[store_index:] if name != "reply_evidence_repository"]
    assert send_names[:4] == [
        "store_pending_ai_reply", "save_state", "bind_conversational_reply_attempt_time", "available",
    ]
    assert [c.kwargs for c in trace.save_state.call_args_list] == [{}, {"durable": True}, {"durable": True}]
    live_context = trace.pending_ai_reply.call_args.kwargs["context"]
    assert trace.recovery_comparison_account_replies.call_args.kwargs["context"] is live_context
    assert trace.store_pending_ai_reply.call_args.kwargs["context"] is live_context
    template = trace.bind_conversational_reply_attempt_time.call_args.args[0]
    assert template["original_post_id"] == "900"
    assert template["reply_context"] == live_context and template["reply_context"] is not live_context
    assert template["ai_reply_draft"] is not template["reply_text"].draft_record
    assert "_prepared_media_context" not in live_context
    assert bot.reply_media_context_for_candidate.call_count == 1
    bot.generate_single_call_reply.assert_not_called()
    bot.create_post.assert_not_called()
    assert not state.get("pending_ai_reply_drafts")
    assert state["daily_reply_count"] == state["daily_quote_reply_count"] == 0
    assert state["reply_evaluation_records"]["910"]["reason"] == "x_target_unavailable_pre_send"
