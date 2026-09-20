from __future__ import annotations

from tests.helpers.reply_evaluation import legacy_reply_evaluator

import inspect
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock, call

import pytest

import mrs_bot_quote_reply_cycle as cycle
import mrs_bot_reply_state as reply_state
import mrs_bot_daily_reply_accounting as accounting_owner
import mrs_bot_reply_context as context_owner
import mrs_bot_reply_cycle_interfaces as interfaces
import mrs_bot_reply_evaluation_state as evaluation_state
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401
from tests.helpers.reply_fixtures import (
    restore_tweet_lookup_fetch,
    configure_quote_cycle as _configure_cycle,
    patch_reply_owner_method,
    patch_reply_draft_method,
    patch_reply_history_method,
    unit_approved_reply,
    unit_confirmed_v4_reply_receipt,
)


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, dataclasses, json, io, logging, os, random, socket, sys
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('quote reply cycle import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'reply_evidence'} or name.startswith('mrs_bot_') and name not in {'mrs_bot_quote_reply_cycle', 'mrs_bot_runtime_state_helpers', 'mrs_bot_reply_context', 'mrs_bot_reply_cycle_interfaces', 'mrs_bot_reply_preparation', 'mrs_bot_reply_generation', 'mrs_bot_reply_native_media', 'mrs_bot_reply_delivery', 'mrs_bot_reply_evaluation_state', 'mrs_bot_author_quarantines', 'mrs_bot_daily_reply_accounting', 'mrs_bot_reply_state', 'mrs_bot_reply_drafts', 'mrs_bot_reply_history', 'mrs_bot_durable_json_io'}:
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
        "quote_tweet_directly_quotes_original": 0,
        "mark_quote_tweet_skipped": 0,
        "mark_quote_tweet_replied": 0,
        "mark_quote_spam_author": 1,
        "maybe_reply_to_quote_tweets": None,
    }
    owner_factories = {
        "reply_evaluations": "_reply_evaluation_owner",
        "accounting": "_daily_reply_accounting_owner",
    }
    for name, count in names.items():
        adapter = getattr(bot, name)
        public = inspect.signature(adapter).parameters
        parameters = inspect.signature(getattr(cycle, name)).parameters
        dependencies = parameters.keys() - public.keys()
        if count is None:
            assert tuple(public) == ("state",)
            assert public["state"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
            assert len(parameters) == 41
            assert sum(param.kind is inspect.Parameter.KEYWORD_ONLY for param in parameters.values()) == 40
            removed = {
                key for key in vars(interfaces) if key.startswith("QUOTE_CHECK_STATUS_")
            } | {
                "_is_terminal_candidate_local_failure",
                "mark_quote_tweet_skipped",
                "terminal_reply_evaluation", "quote_author_profile_text",
                "clean_text_for_reply_context",
                "quote_tweet_directly_quotes_original", "daily_author_reply_count",
                "daily_author_reply_counts", "record_terminal_reply_evaluation",
                "reset_daily_quote_reply_count_if_needed", "reset_daily_reply_count_if_needed",
            }
            for helper_name, function in inspect.getmembers(cycle, inspect.isfunction):
                if function.__module__ == cycle.__name__:
                    assert removed.isdisjoint(inspect.signature(function).parameters), helper_name
            assert {"config", "persistence", "delivery", *owner_factories} <= dependencies
        else:
            assert len(dependencies) == count
        args = tuple(object() for _ in public)
        result = object()
        owner = Mock(return_value=result)
        with monkeypatch.context() as patch:
            patch.setattr(cycle, name, owner)
            for _ in range(2):
                current = {key: object() for key in dependencies}
                factories = {}
                for key, value in current.items():
                    if key == "config":
                        patch.setattr(bot._reply_cycle_interfaces, "QuoteReplyConfig", Mock(return_value=value))
                    elif key in {"persistence", "delivery"}:
                        patch.setattr(bot, f"_reply_cycle_{key}", Mock(return_value=value))
                    elif key in owner_factories:
                        factories[key] = Mock(return_value=value)
                        patch.setattr(bot, owner_factories[key], factories[key])
                    elif key == "recovery_comparison_account_replies":
                        history = Mock(recovery_replies=value)
                        patch.setattr(bot, "_reply_history_owner", Mock(return_value=history))
                    else:
                        patch.setattr(bot, key, value)
                assert adapter(*args) is result, name
                for factory in factories.values():
                    factory.assert_called_once_with()
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


def test_fixed_statuses_and_terminal_lookup_use_their_owners():
    statuses = {
        "QUOTE_CHECK_STATUS_CHECKED": "checked",
        "QUOTE_CHECK_STATUS_POSTED": "posted",
        "QUOTE_CHECK_STATUS_SKIPPED_SPACING": "skipped_spacing",
        "QUOTE_CHECK_STATUS_SKIPPED_CAP": "skipped_cap",
        "QUOTE_CHECK_STATUS_SKIPPED_COOLDOWN": "skipped_cooldown",
        "QUOTE_CHECK_STATUS_DISABLED": "disabled",
    }
    for name, expected in statuses.items():
        value = getattr(interfaces, name)
        assert value == expected
        assert getattr(cycle, name) is value
        assert getattr(bot, name) is value
    assert cycle.terminal_reply_evaluation is evaluation_state.terminal_reply_evaluation
    assert bot.terminal_reply_evaluation is evaluation_state.terminal_reply_evaluation
    assert cycle.clean_text_for_reply_context is context_owner.clean_text_for_reply_context
    assert bot.clean_text_for_reply_context is context_owner.clean_text_for_reply_context
    assert cycle.daily_author_reply_counts is accounting_owner.daily_author_reply_counts
    assert bot.daily_author_reply_counts is accounting_owner.daily_author_reply_counts


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


def test_direct_quote_uses_only_structured_references_and_retweet_veto(monkeypatch):
    cleaner = Mock(wraps=bot.clean_text_for_reply_context)
    monkeypatch.setattr(cycle, "clean_text_for_reply_context", cleaner)
    monkeypatch.setattr(context_owner, "clean_text_for_reply_context", cleaner)
    quoted = {"type": "quoted", "id": 900}
    retweeted = {"type": "retweeted", "id": "800"}
    for refs in ([quoted, retweeted], [retweeted, quoted]):
        assert not bot.quote_tweet_directly_quotes_original({"referenced_tweets": refs}, "900")
    assert bot.quote_tweet_directly_quotes_original(
        {"referenced_tweets": [quoted], "text": "RT @someone: legacy text"}, "900",
    )
    for quote in (
        {},
        {"text": "RT @someone: legacy text"},
        {"text": "Ambiguous commentary"},
        {"text": object(), "referenced_tweets": None},
        {"referenced_tweets": []},
        {"referenced_tweets": [{"type": "quoted", "id": "901"}]},
        {"referenced_tweets": [{"type": "replied_to", "id": "900"}]},
    ):
        assert not bot.quote_tweet_directly_quotes_original(quote, 900)
    cleaner.assert_not_called()
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


def test_markers_preserve_bounded_and_durable_lists_and_mutation_before_failure(monkeypatch):
    state = {
        "seen_quote_post_ids": [str(i) for i in range(2000)],
        "skipped_quote_post_ids": [str(i) for i in range(2000)],
        "replied_to_quote_post_ids": [str(i) for i in range(2500)],
        "quote_spam_author_ids": [str(i) for i in range(2000)],
    }
    capped, durable = Mock(wraps=bot.append_unique_capped), Mock(wraps=bot.append_unique_durable)
    monkeypatch.setattr(reply_state, "append_unique_capped", capped)
    monkeypatch.setattr(reply_state, "append_unique_durable", durable)
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


def test_both_daily_resets_and_confirmed_reconciliation_precede_barrier_when_disabled(monkeypatch):
    _configure_cycle(monkeypatch)
    receipt = unit_confirmed_v4_reply_receipt(lane="quote_tweet", confirmation_epoch=bot.now_epoch())
    bot.write_confirmed_reply_receipt(receipt)
    state = bot.default_state()
    state.update(daily_reply_date="2000-01-01", daily_reply_count=48,
                 daily_quote_reply_date="2000-01-01", daily_quote_reply_count=12)
    monkeypatch.setattr(bot, "ENABLE_QUOTE_TWEET_CHECKS", False)
    trace = Mock()
    accounting = bot._daily_reply_accounting_owner()
    resets = {
        "reset_daily_reply_count_if_needed": "reset",
        "reset_daily_quote_reply_count_if_needed": "reset_quotes",
    }
    for name in (*resets, "reconcile_confirmed_reply_receipt"):
        callback = Mock(wraps=getattr(accounting, resets[name]) if name in resets else getattr(bot, name))
        trace.attach_mock(callback, name)
        if name in resets:
            patch_reply_owner_method(monkeypatch, accounting_owner.DailyReplyAccounting, resets[name], callback)
        else:
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
    monkeypatch.setattr(bot, "evaluate_single_call_reply", legacy_reply_evaluator(generate))
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


@pytest.mark.parametrize("first_is_spam", [False, True])
def test_quote_scan_keeps_fixed_ledgers_but_shares_newly_classified_spam_authors(
    monkeypatch, first_is_spam,
):
    _original, quotes = _configure_cycle(monkeypatch)
    quotes[:] = [dict(quotes[0], id=target, conversation_id=target)
                 for target in ("910", "911")]
    state = bot.default_state()
    author_id = str(quotes[0]["author_id"])
    classifier_calls = []

    def classify(text):
        classifier_calls.append(text)
        if len(classifier_calls) == 1:
            # Mutating durable ledgers during the first candidate does not
            # rebuild this scan's admission history for the second candidate.
            for key in ("seen_quote_post_ids", "replied_to_quote_post_ids",
                        "skipped_quote_post_ids", "replied_to_ids"):
                state.setdefault(key, []).append("911")
            state.setdefault("quote_spam_author_ids", []).append(author_id)
        return first_is_spam

    generate = Mock(return_value=bot.PipelineResult(
        status="no_reply", reason="model_selected_no_reply", model_call_count=1,
    ))
    monkeypatch.setattr(bot, "is_probably_spam_or_not_worth_replying", classify)
    monkeypatch.setattr(bot, "evaluate_single_call_reply", generate)
    monkeypatch.setattr(bot, "MAX_QUOTE_POSTS_PER_CHECK", 2)

    assert bot.maybe_reply_to_quote_tweets(state) == bot.QUOTE_CHECK_STATUS_CHECKED
    if first_is_spam:
        # Classification updates the scan's mutable spam set immediately, so
        # the second quote is stopped before another classifier/model call.
        assert len(classifier_calls) == 1
        generate.assert_not_called()
    else:
        assert len(classifier_calls) == 2
        assert [entry.args[0]["target_id"] for entry in generate.call_args_list] == ["910", "911"]
    assert author_id in state["quote_spam_author_ids"]
    bot.x_request.assert_not_called()
    bot.create_post.assert_not_called()


def test_reused_draft_keeps_context_references_durability_and_pre_send_availability(monkeypatch):
    original, quotes = _configure_cycle(monkeypatch)
    state = bot.default_state()
    prepared_context = bot.build_quote_tweet_reply_context(original, quotes[0])
    assert prepared_context is not None
    context = prepared_context.context
    reply = unit_approved_reply(context)
    assert bot.store_pending_ai_reply(state, "910", "quote_tweet", reply, context=context)
    bot.save_state(state, durable=True)
    bot.reply_media_context_for_candidate.reset_mock()
    trace = Mock()
    draft_methods = {"recover_pending_ai_reply": "recover", "store_pending_ai_reply": "store"}
    for name in ("cache_tweet", "save_state", "build_quote_tweet_reply_context",
                 "reply_evidence_repository", "recovery_comparison_account_replies",
                 "recover_pending_ai_reply", "store_pending_ai_reply", "bind_conversational_reply_attempt_time"):
        original = (
            getattr(bot._reply_draft_owner(), draft_methods[name])
            if name in draft_methods else getattr(bot, name)
        )
        if name == "recovery_comparison_account_replies":
            original = bot._reply_history_owner().recovery_replies
        callback = Mock(wraps=original)
        trace.attach_mock(callback, name)
        if name in draft_methods:
            patch_reply_draft_method(monkeypatch, draft_methods[name], callback)
        elif name == "recovery_comparison_account_replies":
            patch_reply_history_method(monkeypatch, "recovery_replies", callback)
        else:
            monkeypatch.setattr(bot, name, callback)
    monkeypatch.setattr(bot, "evaluate_single_call_reply", legacy_reply_evaluator(Mock(side_effect=AssertionError("draft must be reused"))))

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
    assert names.index("recovery_comparison_account_replies") < names.index("recover_pending_ai_reply")
    store_index = names.index("store_pending_ai_reply")
    send_names = [name for name in names[store_index:] if name != "reply_evidence_repository"]
    assert send_names[:4] == [
        "store_pending_ai_reply", "save_state", "bind_conversational_reply_attempt_time", "available",
    ]
    assert [c.kwargs for c in trace.save_state.call_args_list] == [{}, {"durable": True}, {"durable": True}]
    live_context = trace.recover_pending_ai_reply.call_args.kwargs["context"]
    assert trace.recovery_comparison_account_replies.call_args.kwargs["context"] is live_context
    assert trace.store_pending_ai_reply.call_args.kwargs["context"] is live_context
    template = trace.bind_conversational_reply_attempt_time.call_args.args[0]
    assert template["original_post_id"] == "900"
    assert template["reply_context"] == live_context and template["reply_context"] is not live_context
    assert template["ai_reply_draft"] is not template["reply_text"].draft_record
    assert "_prepared_media_context" not in live_context
    assert bot.reply_media_context_for_candidate.call_count == 1
    bot.evaluate_single_call_reply.assert_not_called()
    bot.create_post.assert_not_called()
    assert not state.get("pending_ai_reply_drafts")
    assert state["daily_reply_count"] == state["daily_quote_reply_count"] == 0
    assert state["reply_evaluation_records"]["910"]["reason"] == "x_target_unavailable_pre_send"


@pytest.mark.parametrize("boundary", ["original", "discovery"])
@pytest.mark.parametrize("api_failure", [False, True])
def test_lookup_failure_continues_only_when_fetching_the_original(monkeypatch, boundary, api_failure):
    original, _ = _configure_cycle(monkeypatch)
    state = bot.default_state()
    failure = (bot.ApiError("lookup failed", service="x", status_code=503)
               if api_failure else ValueError("lookup failed"))
    bot.build_quote_lookup_post_ids.return_value = ["900", "901"]
    bot.get_quote_tweets_for_posts.return_value = {
        parent: [{"id": target, "referenced_tweets": [{"type": "quoted", "id": parent}]}]
        for parent, target in [("900", "910"), ("901", "911")]
    }
    monkeypatch.setattr(bot, "quote_tweet_is_old_enough", lambda _quote: False)
    if boundary == "original":
        bot.get_tweet_by_id_cached.side_effect = [failure, original]
    else:
        bot.get_quote_tweets_for_posts.side_effect = failure
    save, health, generate = Mock(), Mock(), Mock()
    monkeypatch.setattr(bot, "save_state", save)
    monkeypatch.setattr(bot, "record_api_error", health)
    monkeypatch.setattr(bot, "evaluate_single_call_reply", legacy_reply_evaluator(generate))

    assert bot.maybe_reply_to_quote_tweets(state) == bot.QUOTE_CHECK_STATUS_CHECKED
    expected_originals = ["900", "901"] if boundary == "original" else []
    assert bot.get_tweet_by_id_cached.call_args_list == [call(target, state) for target in expected_originals]
    bot.get_quote_tweets_for_posts.assert_called_once_with(["900", "901"], state)
    assert save.called
    assert health.call_args_list == ([call(state, failure, "x", scope="quote")] if api_failure else [])
    generate.assert_not_called()
    assert not state["skipped_quote_post_ids"]


@pytest.mark.parametrize("status,expected_calls,expected_cooldown", [
    (404, 4, False), (429, 1, True), (503, 3, True),
])
def test_original_http_errors_skip_targets_or_stop_at_shared_cooldown(
    monkeypatch, status, expected_calls, expected_cooldown,
):
    """Exercise classification and cooldown through uncached original lookups."""
    monkeypatch.setattr(bot, "now_epoch", lambda: 2_000_000_000)
    monkeypatch.setattr(bot, "single_call_reply", {**bot.single_call_reply, "enabled": True})
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "ENABLE_QUOTE_TWEET_CHECKS", True)
    monkeypatch.setattr(bot, "build_quote_lookup_post_ids", lambda _state: ["900", "901", "902", "903"])
    restore_tweet_lookup_fetch(monkeypatch)
    discoveries = Mock(return_value={target: [{"id": "910"}] for target in ["900", "901", "902", "903"]})
    monkeypatch.setattr(bot, "get_quote_tweets_for_posts", discoveries)
    health = Mock(wraps=bot.record_api_error)
    monkeypatch.setattr(bot, "record_api_error", health)

    def respond(method, url, **kwargs):
        assert method == "GET"
        assert url.startswith("http://127.0.0.1:9/2/tweets/")
        target = url.rsplit("/", 1)[-1]
        response = bot.requests.Response()
        response.status_code = status
        if status == 404 and target == "903":
            response.status_code = 200
            document = {"data": {"id": target, "text": "Available own post", "author_id": str(bot.MY_USER_ID)}}
        elif status == 404:
            document = {"errors": [{
                "resource_type": "tweet", "parameter": "id", "resource_id": target,
                "title": "Not Found Error", "detail": f"Could not find tweet with id: [{target}].",
                "type": "https://api.twitter.com/2/problems/resource-not-found",
            }]}
        else:
            document = {"title": "Too Many Requests" if status == 429 else "Service Unavailable"}
            if status == 429:
                response.headers["x-rate-limit-reset"] = "2000003600"
        response._content = json.dumps(document).encode()
        return response

    transport = Mock(side_effect=respond)
    monkeypatch.setattr(bot.requests, "request", transport)
    state = bot.default_state()
    assert bot.maybe_reply_to_quote_tweets(state) == bot.QUOTE_CHECK_STATUS_CHECKED
    assert transport.call_count == expected_calls
    assert bot.in_api_cooldown(state, scope="quote") is expected_cooldown
    persisted = json.loads(bot.STATE_FILE.read_text())
    assert persisted["quote_api_cooldown_until_epoch"] == state["quote_api_cooldown_until_epoch"]
    if status == 404:
        health.assert_not_called()
        discoveries.assert_called_once_with(["900", "901", "902", "903"], state)
    else:
        assert health.call_count == expected_calls
        discoveries.assert_called_once_with(["900", "901", "902", "903"], state)


@pytest.mark.parametrize("boundary", ["refetch", "cache"])
def test_native_context_preparation_failures_escape_without_retirement(monkeypatch, boundary):
    original, _ = _configure_cycle(monkeypatch)
    state = bot.default_state()
    failure = ValueError("outside canonical context construction")
    if boundary == "refetch":
        bot.get_tweet_by_id_cached.side_effect = [original, failure]
    else:
        monkeypatch.setattr(bot, "cache_tweet", Mock(side_effect=failure))
    save, retire, generate = Mock(), Mock(), Mock()
    monkeypatch.setattr(bot, "save_state", save)
    patch_reply_owner_method(monkeypatch, evaluation_state.ReplyEvaluations, "record", retire)
    monkeypatch.setattr(bot, "evaluate_single_call_reply", legacy_reply_evaluator(generate))

    with pytest.raises(ValueError) as caught:
        bot.maybe_reply_to_quote_tweets(state)
    assert caught.value is failure
    bot.get_tweet_by_id_cached.assert_has_calls([call("900", state), call("900", state, include_media=True)])
    save.assert_not_called()
    retire.assert_not_called()
    generate.assert_not_called()
    assert not state["skipped_quote_post_ids"]


@pytest.mark.parametrize("failure_kind", ["permanent_lookup", "missing_original", "invalid_context"])
@pytest.mark.parametrize("durable_save_fails", [False, True])
def test_context_failures_retire_in_order_before_later_model_work(
    monkeypatch, failure_kind, durable_save_fails,
):
    original, quotes = _configure_cycle(monkeypatch)
    quotes[:] = [dict(quotes[0], id=target, conversation_id=target) for target in ("910", "911", "912")]
    monkeypatch.setattr(bot, "MAX_QUOTE_POSTS_PER_CHECK", 1)
    state = bot.default_state()
    refetches = 0
    lookup_failure = bot.ApiError("quoted post unavailable", service="x", status_code=404)

    def fetch(_target, _state, **kwargs):
        nonlocal refetches
        if kwargs.get("include_media"):
            refetches += 1
            if refetches == 1:
                if failure_kind == "permanent_lookup":
                    raise lookup_failure
                if failure_kind == "missing_original":
                    return None
        return original

    bot.get_tweet_by_id_cached.side_effect = fetch
    monkeypatch.setattr(bot, "api_error_is_permanent_target_failure", lambda error: error is lookup_failure)
    build_context = bot.build_quote_tweet_reply_context

    def context_for_candidate(original_tweet, quote_tweet):
        if failure_kind == "invalid_context" and quote_tweet["id"] == "910":
            raise ValueError("invalid canonical context")
        return build_context(original_tweet, quote_tweet)

    monkeypatch.setattr(bot, "build_quote_tweet_reply_context", context_for_candidate)
    trace = Mock()
    for label, name in (
        ("decision", "_record_single_call_result"),
        ("terminal", "record_terminal_reply_evaluation"),
        ("skip", "mark_quote_tweet_skipped"),
    ):
        original_callback = (
            bot._reply_evaluation_owner().record if label == "terminal"
            else cycle.mark_quote_tweet_skipped if label == "skip"
            else getattr(bot, name)
        )
        callback = Mock(wraps=original_callback)
        trace.attach_mock(callback, label)
        if label == "terminal":
            patch_reply_owner_method(monkeypatch, evaluation_state.ReplyEvaluations, "record", callback)
        elif label == "skip":
            monkeypatch.setattr(cycle, name, callback)
        else:
            monkeypatch.setattr(bot, name, callback)
    persistence_failure = RuntimeError("durable retirement failed")
    save_state = bot.save_state

    def save(current_state, **kwargs):
        if kwargs.get("durable") and durable_save_fails:
            raise persistence_failure
        return save_state(current_state, **kwargs)

    trace.save.side_effect = save
    monkeypatch.setattr(bot, "save_state", trace.save)
    health = Mock()
    monkeypatch.setattr(bot, "record_api_error", health)

    def evaluate(context, _media, *, state):
        # The previous target must be durable before the sole model slot is used.
        saved = json.loads(bot.STATE_FILE.read_text())
        assert saved["reply_evaluation_records"]["910"]["outcome"] == "operational_failure"
        assert "910" in saved["skipped_quote_post_ids"]
        return bot.PipelineResult(status="no_reply", reason="model_selected_no_reply", model_call_count=1)

    generate = Mock(side_effect=evaluate)
    monkeypatch.setattr(bot, "evaluate_single_call_reply", generate)
    if durable_save_fails:
        with pytest.raises(RuntimeError) as caught:
            bot.maybe_reply_to_quote_tweets(state)
        assert caught.value is persistence_failure
        generate.assert_not_called()
        assert refetches == 1
    else:
        assert bot.maybe_reply_to_quote_tweets(state) == bot.QUOTE_CHECK_STATUS_CHECKED
        generate.assert_called_once()
        assert generate.call_args.args[0]["target_id"] == "911"
        assert refetches == 2

    decision = trace.decision.call_args.args[0]
    expected_reason = (
        "canonical_context_unavailable" if failure_kind == "invalid_context"
        else "quoted_post_context_unavailable"
    )
    assert decision.status == "operational_failure"
    assert decision.error_category == "context_validation"
    assert decision.local_validation_status == ("failed" if failure_kind == "invalid_context" else "not_run")
    assert decision.model_call_count == 0 and decision.reason == expected_reason
    assert trace.decision.call_args.kwargs == {"lane": "quote_tweet", "target_id": "910"}
    decision_index = next(index for index, item in enumerate(trace.mock_calls) if item[0] == "decision")
    assert trace.mock_calls[decision_index:decision_index + 4] == [
        call.decision(decision, lane="quote_tweet", target_id="910"),
        call.terminal(state, target_id="910", lane="quote_tweet", reason=expected_reason,
                      outcome="operational_failure"),
        call.skip(state, "910"),
        call.save(state, durable=True),
    ]
    assert state["daily_reply_count"] == state["daily_quote_reply_count"] == 0
    health.assert_not_called()
    bot.x_request.assert_not_called()
    bot.create_post.assert_not_called()


def test_context_rejection_is_free_but_evaluation_budget_spans_original_posts(monkeypatch):
    original, quotes = _configure_cycle(monkeypatch)
    state = bot.default_state()
    bot.build_quote_lookup_post_ids.return_value = ["900", "901", "902"]
    quotes_by_original = {
        source: [dict(quotes[0], id=target, conversation_id=target,
                      referenced_tweets=[{"type": "quoted", "id": source}]) for target in targets]
        for source, targets in [("900", ["910", "911"]), ("901", ["921"]), ("902", ["931"])]
    }
    bot.get_tweet_by_id_cached.side_effect = lambda target, _state, **kwargs: dict(original, id=target)
    bot.get_quote_tweets_for_posts.return_value = quotes_by_original
    monkeypatch.setattr(bot, "MAX_QUOTE_POSTS_PER_CHECK", 2)
    build_context = bot.build_quote_tweet_reply_context

    def context_for_candidate(original_tweet, quote_tweet):
        if quote_tweet["id"] == "910":
            raise ValueError("invalid canonical context")
        return build_context(original_tweet, quote_tweet)

    evaluated = []

    def zero_call_failure(context, media, *, state, evaluation_outcome):
        assert "_prepared_media_context" not in context
        assert media is bot.reply_media_context_for_candidate.return_value
        evaluated.append(context["target_id"])
        evaluation_outcome.update(status="operational_failure", error_category="image_input",
                                  reason="unusable_image", model_call_count=0)

    monkeypatch.setattr(bot, "build_quote_tweet_reply_context", context_for_candidate)
    monkeypatch.setattr(bot, "evaluate_single_call_reply", legacy_reply_evaluator(zero_call_failure))
    save = Mock(wraps=bot.save_state)
    monkeypatch.setattr(bot, "save_state", save)

    assert bot.maybe_reply_to_quote_tweets(state) == bot.QUOTE_CHECK_STATUS_CHECKED
    assert evaluated == ["911", "921"]
    bot.get_quote_tweets_for_posts.assert_called_once_with(["900", "901", "902"], state)
    assert bot.get_tweet_by_id_cached.call_args_list == [
        call("900", state), call("900", state, include_media=True), call("900", state, include_media=True),
        call("901", state), call("901", state, include_media=True),
    ]
    assert [c.kwargs for c in save.call_args_list] == [
        {}, {"durable": True}, {}, {"durable": True}, {}, {"durable": True}, {},
    ]
    saved = json.loads(bot.STATE_FILE.read_text())
    assert saved["skipped_quote_post_ids"] == ["910", "911", "921"]
    assert saved["reply_evaluation_records"]["910"]["reason"] == "canonical_context_unavailable"
    assert saved["daily_reply_count"] == saved["daily_quote_reply_count"] == 0
    bot.create_post.assert_not_called()


@pytest.mark.parametrize("boundary", [
    "reply_evidence_repository",
    "recovery_comparison_account_replies", "recover_pending_ai_reply",
])
def test_pre_generation_failures_keep_their_own_exception_boundary(monkeypatch, boundary):
    original, quotes = _configure_cycle(monkeypatch)
    prepared_context = bot.build_quote_tweet_reply_context(original, quotes[0])
    assert prepared_context is not None
    context = prepared_context.context
    monkeypatch.setattr(bot, "build_quote_tweet_reply_context", Mock(return_value=prepared_context))
    evidence_failure = boundary == "reply_evidence_repository"
    failure = (bot.ReplyEvidenceUnavailable("evidence unavailable")
               if evidence_failure else ValueError("outside generation"))
    if boundary == "recover_pending_ai_reply":
        patch_reply_draft_method(monkeypatch, "recover", Mock(side_effect=failure))
    elif boundary == "recovery_comparison_account_replies":
        patch_reply_history_method(monkeypatch, "recovery_replies", Mock(side_effect=failure))
    else:
        monkeypatch.setattr(bot, boundary, Mock(side_effect=failure))
    save, health, generate = Mock(), Mock(), Mock()
    monkeypatch.setattr(bot, "save_state", save)
    monkeypatch.setattr(bot, "record_api_error", health)
    monkeypatch.setattr(bot, "evaluate_single_call_reply", legacy_reply_evaluator(generate))
    state = bot.default_state()

    if evidence_failure:
        assert bot.maybe_reply_to_quote_tweets(state) == bot.QUOTE_CHECK_STATUS_CHECKED
    else:
        with pytest.raises(ValueError) as caught:
            bot.maybe_reply_to_quote_tweets(state)
        assert caught.value is failure
    save.assert_called_once_with(state)
    assert not state["skipped_quote_post_ids"]
    assert "reply_evaluation_records" not in state
    generate.assert_not_called()
    health.assert_not_called()


def test_empty_combined_search_needs_no_original_or_legacy_lookup(monkeypatch):
    _configure_cycle(monkeypatch)
    state = bot.default_state()
    parents = ["900", "901", "902", "903", "904"]
    bot.build_quote_lookup_post_ids.return_value = parents
    bot.get_quote_tweets_for_posts.return_value = {parent: [] for parent in parents}
    legacy = Mock(side_effect=AssertionError("legacy quote lookup must not run"))
    monkeypatch.setattr(bot, "get_quote_tweets_for_post", legacy)
    assert bot.maybe_reply_to_quote_tweets(state) == bot.QUOTE_CHECK_STATUS_CHECKED
    bot.get_quote_tweets_for_posts.assert_called_once_with(parents, state)
    bot.get_tweet_by_id_cached.assert_not_called()
    legacy.assert_not_called()
    bot.create_post.assert_not_called()
