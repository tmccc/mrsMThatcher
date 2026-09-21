from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError
from datetime import datetime, timezone
import hashlib
import inspect
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from mrs_bot_reply_cycle_interfaces import PreparedReplyContext
import mrs_bot_reply_context as reply_context
from mrs_bot_reply_native_media import ReplyMedia
from mrs_bot_tweet_lookup_cache import TweetLookupCache
from tests.helpers.bot_runtime import SCENARIOS, bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401
from tests.helpers.reply_fixtures import patch_reply_context_method, patch_tweet_lookup_method
from tests.fake_api_server import load_scenario


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, copy, dataclasses, hashlib, html, io, json, logging, os, random, re, socket, sys, time
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('reply context import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'reply_evidence'} or name.startswith('mrs_bot_') and name not in {'mrs_bot_reply_context', 'mrs_bot_reply_cycle_interfaces', 'mrs_bot_reply_delivery', 'mrs_bot_durable_json_io', 'mrs_bot_reply_native_media', 'mrs_bot_request_route_values', 'mrs_bot_tweet_lookup_cache'}:
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = forbidden
os.getenv = os._Environ.__getitem__ = forbidden
Path.home = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
time.time = time.monotonic = forbidden
before = random.getstate()
random.Random = random.seed = random.random = forbidden
import mrs_bot_reply_context
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


OWNER_INPUTS = {
    "api_error": "ApiError", "parse_tweet_id": "parse_tweet_id",
    "maximum_parent_depth": "THREAD_CONTEXT_MAX_DEPTH",
    "maximum_parent_network_fetches": "THREAD_CONTEXT_MAX_NETWORK_FETCHES",
    "is_permanent_target_failure": "api_error_is_permanent_target_failure",
    "log": "log", "log_json_debug": "log_json_debug",
    "user_id": "MY_USER_ID",
    "parse_x_datetime_to_epoch": "parse_x_datetime_to_epoch",
    "always_fetch_parent": "ALWAYS_FETCH_PARENT_FOR_CONTEXT",
    "context_validation_error": "ContextValidationError",
    "incoming_maximum_chars": "REPLY_INCOMING_MAX_CHARS",
    "maximum_visible_chars": "MAX_VISIBLE_TEXT_CHARACTERS",
    "skip_own_auto_replies": "SKIP_REPLIES_TO_OWN_AUTO_REPLIES",
    "bound_visible_conversation": "bound_visible_conversation",
    "current_utc_datetime": "current_utc_datetime",
}


@pytest.fixture
def make_owner():
    """Compose context operations using isolated runtime boundaries."""
    def build(**overrides):
        current = {field: getattr(bot, name) for field, name in OWNER_INPUTS.items()}
        current["default_post_maximum_chars"] = inspect.signature(bot._reply_context_post).parameters["maximum_chars"].default
        current["tweets"] = bot._tweet_lookup_cache_owner()
        current["media"] = bot._reply_media_owner()
        return reply_context.ReplyContext(**{**current, **overrides})
    return build


def test_owner_composition_binds_current_dependencies_without_calling_them(monkeypatch):
    default = inspect.signature(bot._reply_context_post).parameters["maximum_chars"].default
    parameters = inspect.signature(reply_context.ReplyContext).parameters
    assert len(parameters) == 19
    assert parameters.keys() == OWNER_INPUTS.keys() | {"default_post_maximum_chars", "tweets", "media"}
    assert {"get_tweet_by_id_cached", "prune_tweet_cache", "reply_media_context_for_candidate"}.isdisjoint(parameters)
    snapshots = []
    for _ in range(2):
        current = {field: Mock() for field in OWNER_INPUTS}
        for field, name in OWNER_INPUTS.items():
            monkeypatch.setattr(bot, name, current[field])
        tweets = Mock(spec=TweetLookupCache)
        media = Mock(spec=ReplyMedia)
        tweets_factory = Mock(return_value=tweets)
        media_factory = Mock(return_value=media)
        monkeypatch.setattr(bot, "_tweet_lookup_cache_owner", tweets_factory)
        monkeypatch.setattr(bot, "_reply_media_owner", media_factory)
        owner = bot._reply_context_owner()
        tweets_factory.assert_called_once_with()
        media_factory.assert_called_once_with()
        assert owner.tweets is tweets
        assert owner.media is media
        assert tweets.mock_calls == []
        assert isinstance(owner, reply_context.ReplyContext)
        assert owner.default_post_maximum_chars == default
        for field, value in current.items():
            assert getattr(owner, field) is value
            value.assert_not_called()
        snapshots.append((owner, {**current, "tweets": tweets, "media": media}))
    first, values = snapshots[0]
    assert first is not snapshots[1][0]
    assert first.tweets is not snapshots[1][0].tweets
    assert all(getattr(first, field) is value for field, value in values.items())
    with pytest.raises(FrozenInstanceError):
        first.user_id = "different"


def test_adapters_preserve_defaults_argument_result_identity_and_native_errors(monkeypatch):
    methods = {
        "get_immediate_parent_id": "parent_id", "build_parent_chain": "parent_chain",
        "is_our_auto_reply": "is_our_auto_reply", "_reply_context_post": "post",
        "_log_single_call_context_summary": "log_summary",
        "_directly_quoted_tweet_for_reply_context": "directly_quoted_tweet",
        "_quoted_post_for_reply_context": "quoted_post",
        "_parent_path_is_contiguous": "parent_path_is_contiguous",
        "_parent_path_is_chronological": "parent_path_is_chronological",
        "build_context_for_reply_ai": "build",
        "build_quote_tweet_reply_context": "build_quote",
    }
    for name, method_name in methods.items():
        adapter = getattr(bot, name)
        public = inspect.signature(adapter).parameters
        if method_name == "build_quote":
            assert tuple(public) == ("original_tweet", "quote_tweet")
            assert all(param.kind is param.POSITIONAL_OR_KEYWORD for param in public.values())
        elif method_name in {"build", "parent_chain"}:
            assert tuple(public) == ("mention", "state")
            assert all(param.kind is param.POSITIONAL_OR_KEYWORD for param in public.values())
        elif method_name == "directly_quoted_tweet":
            assert tuple(public) == ("candidate", "state", "include_media")
            assert public["include_media"].kind is inspect.Parameter.KEYWORD_ONLY
            assert public["include_media"].default is True
        args = tuple(object() for param in public.values() if param.kind == param.POSITIONAL_OR_KEYWORD)
        for use_defaults in (True, False):
            owner = Mock(spec=reply_context.ReplyContext)
            factory = Mock(return_value=owner)
            monkeypatch.setattr(bot, "_reply_context_owner", factory)
            implementation = getattr(owner, method_name)
            result = object()
            implementation.return_value = result
            options = {
                key: object() for key, param in public.items()
                if param.kind == param.KEYWORD_ONLY
                and (not use_defaults or param.default is param.empty)
            }
            expected = {
                key: param.default for key, param in public.items()
                if param.kind == param.KEYWORD_ONLY and param.default is not param.empty
            } | options
            assert adapter(*args, **options) is result
            factory.assert_called_once_with()
            actual_args, actual_kwargs = implementation.call_args
            assert len(actual_args) == len(args)
            assert all(actual is original for actual, original in zip(actual_args, args))
            assert actual_kwargs.keys() == expected.keys()
            assert all(actual_kwargs[key] is value for key, value in expected.items())
            failure = TypeError(name)
            implementation.side_effect = failure
            with pytest.raises(TypeError) as caught:
                adapter(*args, **options)
            assert caught.value is failure


def test_pure_text_helpers_are_compatible_aliases_with_local_composition(monkeypatch):
    for name in ("clean_text_for_reply_context", "tweet_context_text", "trim_context_text", "_direct_quote_id"):
        assert getattr(bot, name) is getattr(reply_context, name)
    assert reply_context.clean_text_for_reply_context(" A &amp; B https://example.invalid  ") == "A & B"
    cleaner = Mock(return_value="")
    monkeypatch.setattr(reply_context, "clean_text_for_reply_context", cleaner)
    assert reply_context.tweet_context_text({"text": "a", "image_summary": "b"}) == ""
    assert cleaner.call_args_list == [call("a"), call("b")]
    cleaner.side_effect = ["", "a picture"]
    assert reply_context.tweet_context_text({"text": "a", "image_summary": "b"}) == "[Image/meme summary: a picture]"
    cleaner.side_effect = TypeError("native cleaning failure")
    with pytest.raises(TypeError, match="native cleaning failure"):
        reply_context.trim_context_text("text", 0)


def test_visible_post_omitted_maximum_keeps_definition_time_default_after_config_rebind(monkeypatch):
    fixed = inspect.signature(bot._reply_context_post).parameters["maximum_chars"].default
    assert fixed == bot.MAX_VISIBLE_TEXT_CHARACTERS
    assert inspect.signature(reply_context.ReplyContext.post).parameters["maximum_chars"].default is inspect.Parameter.empty
    tweet = {"id": 100, "author_id": 200, "text": "x" * (fixed + 20)}
    trim = Mock(wraps=reply_context.trim_context_text)
    monkeypatch.setattr(reply_context, "trim_context_text", trim)
    monkeypatch.setattr(bot, "MAX_VISIBLE_TEXT_CHARACTERS", 7)
    monkeypatch.setattr(bot, "MY_USER_ID", "200")
    owner = bot._reply_context_owner()
    assert owner.maximum_visible_chars == 7
    assert owner.default_post_maximum_chars == fixed

    omitted = bot._reply_context_post(tweet, principal_author_id="200")
    explicit = bot._reply_context_post(tweet, principal_author_id="200", maximum_chars=7)

    assert omitted == {"post_id": "100", "author_role": "account", "text": "x" * (fixed - 3) + "..."}
    assert explicit == {"post_id": "100", "author_role": "account", "text": "xxxx..."}
    assert [entry.args[1] for entry in trim.call_args_list] == [fixed, 7]
    assert inspect.signature(bot._reply_context_post).parameters["maximum_chars"].default == fixed


def test_quote_context_preserves_budget_roles_reference_boundaries_and_media_before_summary(monkeypatch, make_owner):
    original = {"id": 900, "text": "original account text " * 20}
    quote = {
        "id": 910, "author_id": 310, "conversation_id": 911,
        "created_at": "2026-06-30T10:00:00Z", "text": "target commentary " * 10,
    }
    before = copy.deepcopy((original, quote))
    trace = Mock()
    for name in ("tweet_context_text", "trim_context_text"):
        callback = Mock(wraps=getattr(reply_context, name))
        trace.attach_mock(callback, name)
        monkeypatch.setattr(reply_context, name, callback)
    trace.attach_mock(Mock(wraps=bot.bound_visible_conversation), "bound_visible_conversation")
    media = {"photos": []}
    trace.attach_mock(Mock(return_value=media), "media")
    owner = make_owner(
        incoming_maximum_chars=30, maximum_visible_chars=60,
        bound_visible_conversation=trace.bound_visible_conversation,
        media=SimpleNamespace(context=trace.media),
        current_utc_datetime=lambda: datetime(2030, 2, 3, tzinfo=timezone.utc),
    )
    trace.attach_mock(Mock(wraps=owner.post), "post")
    trace.attach_mock(Mock(wraps=owner.log_summary), "log_summary")
    patch_reply_context_method(monkeypatch, "post", trace.post)
    patch_reply_context_method(monkeypatch, "log_summary", trace.log_summary)

    prepared_context = owner.build_quote(original, quote)
    assert prepared_context is not None
    context = prepared_context.context

    assert [c[0] for c in trace.mock_calls] == [
        "post", "tweet_context_text", "trim_context_text",
        "tweet_context_text", "trim_context_text", "bound_visible_conversation",
        "media", "log_summary",
    ]
    assert trace.post.call_args.args[0] is quote
    assert trace.post.call_args.kwargs == {"principal_author_id": "310", "maximum_chars": 30}
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
    assert prepared_context.media_context is media
    assert trace.media.call_args.args[0] is quote
    assert trace.media.call_args.kwargs["quoted_candidate"] is original
    assert trace.log_summary.call_args.args[1] is prepared_context
    context["quoted_post"]["text"] = "changed copy"
    assert context["parent_thread"][0]["text"] == original_turn["text"]
    assert (original, quote) == before


def test_quote_context_keeps_image_only_original_as_media_subject(make_owner):
    original = {
        "id": "900",
        "text": "https://t.co/photo",
        "attachments": {"media_keys": ["photo-1"]},
        "_attached_media": [{
            "media_key": "photo-1",
            "type": "photo",
            "url": "https://pbs.twimg.com/media/photo.jpg",
        }],
    }
    quote = {
        "id": "910",
        "author_id": "310",
        "conversation_id": "910",
        "text": "What do you make of this?",
    }

    prepared = make_owner().build_quote(original, quote)
    context = prepared.context

    assert context["visible_conversation"] == [{
        "post_id": "910",
        "author_role": "user",
        "text": "What do you make of this?",
    }]
    assert context["parent_thread"] == []
    assert context["quoted_post"] is None
    assert context["quoted_post_id"] == "900"
    assert context["quoted_post_relationship"] == "target_quote"
    assert prepared.media_context["photos"] == [{
        "media_key": "photo-1",
        "url": "https://pbs.twimg.com/media/photo.jpg",
        "attachment_role": "quoted_subject",
        "source_post_id": "900",
    }]


def test_parent_chain_keeps_current_callback_order_and_original_parent_references(monkeypatch, make_owner):
    mention = {"id": "3", "referenced_tweets": [{"type": "replied_to", "id": "2"}]}
    parent = {"id": "2", "text_is_complete": True, "referenced_tweets": [{"type": "replied_to", "id": "1"}]}
    root = {"id": "1"}
    state = {"tweet_cache": {"2": parent}}
    trace = Mock()
    trace.lookup.side_effect = [parent, root]
    owner = make_owner(
        tweets=Mock(spec=TweetLookupCache, prune=trace.prune, get_cached=trace.lookup),
        log_json_debug=trace.debug, maximum_parent_depth=2,
        maximum_parent_network_fetches=1,
    )
    trace.attach_mock(Mock(wraps=owner.parent_id), "parent_id")
    monkeypatch.setattr(reply_context.ReplyContext, "parent_id", trace.parent_id)

    chain = owner.parent_chain(mention, state)

    assert [entry[0] for entry in trace.mock_calls] == [
        "parent_id", "prune", "lookup", "parent_id", "lookup", "parent_id", "debug",
    ]
    assert chain[0] is root and chain[1] is parent
    assert trace.parent_id.call_args_list == [call(mention), call(parent), call(root)]
    assert trace.prune.call_args.args[0] is state
    assert all(entry.args[1] is state for entry in trace.lookup.call_args_list)
    assert trace.debug.call_args.args[1] is chain
    trace.reset_mock()
    with pytest.raises(bot.ApiError, match="malformed referenced_tweets"):
        owner.parent_chain({"referenced_tweets": [None]}, state)
    trace.prune.assert_not_called()
    trace.lookup.assert_not_called()


def test_context_lookup_handoff_bounds_parent_reads_and_refreshes_quoted_media(monkeypatch):
    """Traverse real cache operations and quoted-media refresh without root relays."""
    monkeypatch.setattr(bot, "now_epoch", lambda: 100)
    monkeypatch.setattr(bot, "TWEET_CACHE_MAX_AGE_SECONDS", 10)
    monkeypatch.setattr(bot, "THREAD_CONTEXT_MAX_DEPTH", 3)
    monkeypatch.setattr(bot, "THREAD_CONTEXT_MAX_NETWORK_FETCHES", 1)
    parent = {
        "id": "2", "text": "Cached parent", "text_is_complete": True,
        "author_id": "202", "conversation_id": "1", "cached_epoch": 100,
        "referenced_tweets": [{"type": "replied_to", "id": "1"}],
    }
    root = {
        "id": "1", "text": "Fetched ancestor", "author_id": "201",
        "conversation_id": "1", "referenced_tweets": [{"type": "replied_to", "id": "99"}],
    }
    target = {"id": "3", "referenced_tweets": [{"type": "replied_to", "id": "2"}]}
    state = {"tweet_cache": {"2": parent, "expired": {"cached_epoch": 0}}}
    saved = []

    def save(current):
        assert current is state
        saved.append(copy.deepcopy(current))

    monkeypatch.setattr(bot, "save_state", Mock(side_effect=save))
    fresh = {
        **root, "text": "Refreshed quoted text",
        "attachments": {"media_keys": ["photo"]},
        "_attached_media": [{"media_key": "photo", "type": "photo", "url": "https://example.invalid/photo"}],
    }
    fetch = Mock(side_effect=[root, fresh])
    patch_tweet_lookup_method(monkeypatch, "fetch", fetch)
    relays = {}
    for name in ("get_tweet_by_id_cached", "prune_tweet_cache"):
        relays[name] = Mock(side_effect=AssertionError(f"root relay used: {name}"))
        monkeypatch.setattr(bot, name, relays[name])
    owner = bot._reply_context_owner()
    assert isinstance(owner.tweets, TweetLookupCache)

    chain = owner.parent_chain(target, state)

    assert [row["id"] for row in chain] == ["1", "2"]
    assert chain[1] is parent
    assert chain[0] == state["tweet_cache"]["1"]
    assert chain[0] is not state["tweet_cache"]["1"]
    fetch.assert_called_once_with("1")
    assert len(saved) == 1 and set(saved[0]["tweet_cache"]) == {"1", "2"}
    stored = copy.deepcopy(state["tweet_cache"]["1"])
    quoted = owner.directly_quoted_tweet(
        {"referenced_tweets": [{"type": "quoted", "id": "1"}]}, state,
    )

    assert fetch.call_args_list == [call("1"), call("1", include_media=True)]
    assert quoted["text"] == fresh["text"]
    assert quoted["_attached_media"] == fresh["_attached_media"]
    assert quoted["_attached_media"] is not fresh["_attached_media"]
    assert quoted is not state["tweet_cache"]["1"]
    assert state["tweet_cache"]["1"] == stored
    assert len(saved) == 1
    for relay in relays.values():
        relay.assert_not_called()


def test_quote_alias_and_lookup_keep_distinct_container_rules_and_first_result(make_owner):
    assert bot._direct_quote_id is reply_context._direct_quote_id
    first = {"type": "quoted", "id": 900}
    candidate = {"referenced_tweets": (first, {"type": "quoted", "id": "901"})}
    state, quoted = {}, {"id": "900", "text": "Original"}
    lookup = Mock(return_value=quoted)
    owner = make_owner(tweets=Mock(spec=TweetLookupCache, get_cached=lookup))
    assert bot._direct_quote_id(candidate) is None
    assert owner.directly_quoted_tweet(candidate, state) is quoted
    lookup.assert_called_once_with("900", state, include_media=True)
    assert lookup.call_args.args[1] is state
    candidate["referenced_tweets"] = list(candidate["referenced_tweets"])
    assert bot._direct_quote_id(candidate) == "900"
    lookup.reset_mock()
    lookup.return_value = None
    assert owner.directly_quoted_tweet(candidate, state, include_media=False) is None
    lookup.assert_called_once_with("900", state, include_media=False)


def test_cached_observation_time_does_not_become_verified_chronology(make_owner):
    older = {"created_at": "observed", "cached_epoch": 20}
    target = {"created_at": "target"}
    parser = Mock(side_effect=lambda value: {"observed": 20, "target": 10}[value])
    owner = make_owner(parse_x_datetime_to_epoch=parser)
    assert owner.parent_path_is_chronological([older], target) is True
    assert parser.call_args_list == [call("observed"), call("target")]
    older["cached_epoch"] = 20.0
    assert owner.parent_path_is_chronological([older], target) is False


def test_context_keeps_usable_suffix_raw_ancestor_quote_and_media_copy_metadata_order(monkeypatch, make_owner):
    root = {"id": "100", "author_id": "12345", "text": "Root", "referenced_tweets": [{"type": "quoted", "id": "900"}]}
    unusable = {"id": "110", "text": "", "referenced_tweets": [{"type": "replied_to", "id": "100"}]}
    parent = {"id": "120", "author_id": "201", "text": "Parent", "referenced_tweets": [{"type": "replied_to", "id": "110"}]}
    target = {"id": "130", "author_id": "200", "text": "Incoming", "conversation_id": "100",
              "created_at": "source-date", "_source": "hot_post",
              "referenced_tweets": [{"type": "replied_to", "id": "120"}]}
    quoted = {"id": "900", "author_id": "300", "text": "Quoted",
              "attachments": {"media_keys": ["photo"]},
              "_attached_media": [{"media_key": "photo", "type": "photo", "url": "https://pbs.twimg.com/media/example.jpg"}]}
    chain, state = [root, unusable, parent], bot.default_state()
    before = copy.deepcopy((chain, target, quoted, state))
    parents, lookup = Mock(return_value=chain), Mock(return_value=quoted)
    trace = Mock()
    trace.attach_mock(Mock(wraps=bot.bound_visible_conversation), "bound_visible_conversation")
    trace.attach_mock(Mock(wraps=copy.deepcopy), "copy")
    monkeypatch.setattr(reply_context, "copy", SimpleNamespace(deepcopy=trace.copy))
    monkeypatch.setattr(reply_context.ReplyContext, "parent_chain", parents)
    media_results = []
    original_media = bot.reply_media_context_for_candidate

    def prepare_media(*args, **kwargs):
        result = original_media(*args, **kwargs)
        media_results.append(result)
        return result

    trace.media.side_effect = prepare_media
    trace.clock.return_value = datetime(2030, 2, 3, tzinfo=timezone.utc)
    owner = make_owner(
        tweets=Mock(spec=TweetLookupCache, get_cached=lookup),
        always_fetch_parent=True, skip_own_auto_replies=False,
        bound_visible_conversation=trace.bound_visible_conversation,
        media=SimpleNamespace(context=trace.media), current_utc_datetime=trace.clock,
    )
    trace.attach_mock(Mock(wraps=owner.parent_id), "get_immediate_parent_id")
    trace.attach_mock(Mock(wraps=owner.log_summary), "_log_single_call_context_summary")
    monkeypatch.setattr(reply_context.ReplyContext, "parent_id", trace.get_immediate_parent_id)
    monkeypatch.setattr(reply_context.ReplyContext, "log_summary", trace._log_single_call_context_summary)

    prepared_context = owner.build(target, state)
    assert prepared_context is not None
    context = prepared_context.context

    assert [entry[0] for entry in trace.mock_calls][-7:] == [
        "bound_visible_conversation", "copy", "media", "get_immediate_parent_id",
        "clock", "get_immediate_parent_id", "_log_single_call_context_summary",
    ]
    assert parents.call_args.args[0] is target
    assert parents.call_args.args[1] is state
    lookup.assert_called_once_with("900", state, include_media=True)
    assert [row["post_id"] for row in context["visible_conversation"]] == ["120", "130"]
    assert context["parent_thread"] == context["visible_conversation"][:-1]
    assert context["parent_thread"][0] is not context["visible_conversation"][0]
    assert context["quoted_post"] == {"post_id": "900", "author_role": "other_user", "text": "Quoted"}
    assert context["quoted_post_relationship"] == "root_quote"
    assert context["target_id"] == "130" and context["root_post_id"] == context["thread_id"] == "100"
    assert context["parent_post_id"] == "120" and context["lane"] == "hot_post"
    assert context["target_author_id"] == "200" and context["target_created_at"] == "source-date"
    assert context["current_date"] == "2030-02-03" and context["incoming_contribution"] == "Incoming"
    assert prepared_context.media_context is media_results[0]
    assert media_results[0]["photos"][0]["source_post_id"] == "900"
    assert trace.media.call_args.args[0] is target
    assert trace.media.call_args.kwargs == {"lane": "hot_post", "target_id": "130", "quoted_candidate": quoted}
    assert trace.media.call_args.kwargs["quoted_candidate"] is quoted
    assert trace._log_single_call_context_summary.call_args.args[1] is prepared_context
    context["parent_thread"][0]["text"] = "Changed copy"
    assert context["visible_conversation"][0]["text"] == "Parent"
    assert (chain, target, quoted, state) == before


@pytest.mark.parametrize("boundary", ["canonical", "native_bound", "media"])
def test_context_preserves_canonical_rejection_and_native_bound_media_errors(monkeypatch, make_owner, boundary):
    target = load_scenario(SCENARIOS / "normal_mention_reply.json")["mentions"][0]
    failure = bot.ContextValidationError("canonical") if boundary == "canonical" else TypeError(boundary)
    bound = Mock(wraps=bot.bound_visible_conversation)
    media, summary = Mock(), Mock()
    (media if boundary == "media" else bound).side_effect = failure
    monkeypatch.setattr(reply_context.ReplyContext, "parent_chain", Mock(return_value=[]))
    monkeypatch.setattr(reply_context.ReplyContext, "log_summary", summary)
    owner = make_owner(bound_visible_conversation=bound, media=SimpleNamespace(context=media))
    if boundary == "canonical":
        assert owner.build(target, bot.default_state()) is None
    else:
        with pytest.raises(TypeError) as caught:
            owner.build(target, bot.default_state())
        assert caught.value is failure
    assert media.call_count == (1 if boundary == "media" else 0)
    summary.assert_not_called()

    bound.reset_mock()
    media.reset_mock()
    with pytest.raises(type(failure)) as caught:
        owner.build_quote({"id": "900", "text": "Quoted account post"}, target)
    assert caught.value is failure
    assert media.call_count == (1 if boundary == "media" else 0)
    summary.assert_not_called()


def test_summary_keeps_canonical_encoding_exact_hash_and_native_encoder_errors(monkeypatch, make_owner):
    context = {"target_id": "100", "visible_conversation": [{"text": "Private é"}],
               "quoted_post_id": "900"}
    media = {"photos": [{"url": "https://example.invalid/private"}]}
    prepared = PreparedReplyContext(context, media)
    legacy_context = {**context, "_prepared_media_context": media}
    logger = Mock()
    encoder = Mock(wraps=json.dumps)
    owner = make_owner(log=logger)
    monkeypatch.setattr(reply_context, "json", SimpleNamespace(dumps=encoder))
    owner.log_summary("Context", prepared)
    encoder.assert_called_once_with(legacy_context, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    assert "_prepared_media_context" not in context
    digest = hashlib.sha256(json.dumps(legacy_context, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    assert logger.debug.call_args.args == (
        "%s summary target_id=%s visible_turn_count=%d visible_character_count=%d quoted_subject_present=%s media_count=%d context_sha256=%s",
        "Context", "100", 1, 9, True, 1, digest,
    )
    failure = RuntimeError("encoder failed")
    encoder.side_effect = failure
    logger.reset_mock()
    with pytest.raises(RuntimeError) as caught:
        owner.log_summary("Context", prepared)
    assert caught.value is failure
    logger.debug.assert_not_called()


@pytest.mark.parametrize("value", [object(), float("nan"), "\ud800"])
def test_summary_keeps_canonical_fallback_for_original_encoding_failures(make_owner, value):
    logger = Mock()
    make_owner(log=logger).log_summary("Context", PreparedReplyContext({"noncanonical": value}, {}))
    assert logger.debug.call_args.args[-1] == hashlib.sha256(b"non-canonical-single-call-context").hexdigest()


def test_unusable_rendered_target_stops_before_quote_lookup_and_media(monkeypatch, make_owner):
    target = {"id": "100", "author_id": "200", "text": "Incoming"}
    rendered = {"post_id": "100", "author_role": "user", "text": ""}
    render = Mock(return_value=rendered)
    lookup, media, clock = Mock(), Mock(), Mock()
    monkeypatch.setattr(reply_context.ReplyContext, "post", render)
    owner = make_owner(
        always_fetch_parent=False, skip_own_auto_replies=False,
        tweets=Mock(spec=TweetLookupCache, get_cached=lookup), media=SimpleNamespace(context=media),
        current_utc_datetime=clock,
    )

    assert owner.build(target, {}) is None
    render.assert_called_once_with(
        target, principal_author_id="200", maximum_chars=owner.incoming_maximum_chars,
    )
    assert render.call_args.args[0] is target
    lookup.assert_not_called()
    media.assert_not_called()
    clock.assert_not_called()


@pytest.mark.parametrize("contiguous", [False, True])
def test_invalid_parent_admission_stops_before_turn_projection_and_media(monkeypatch, make_owner, contiguous):
    target = {"id": "200", "author_id": "300", "text": "Incoming", "conversation_id": "100"}
    chain = [{"id": "100", "author_id": "300", "text": "Root"}]
    chronology = Mock(return_value=False)
    media, render = Mock(), Mock()
    logger = Mock()
    parent_path = Mock(return_value=contiguous)
    monkeypatch.setattr(reply_context.ReplyContext, "parent_chain", Mock(return_value=chain))
    monkeypatch.setattr(reply_context.ReplyContext, "parent_path_is_contiguous", parent_path)
    monkeypatch.setattr(reply_context.ReplyContext, "parent_path_is_chronological", chronology)
    monkeypatch.setattr(reply_context.ReplyContext, "post", render)
    owner = make_owner(
        always_fetch_parent=True, skip_own_auto_replies=False,
        media=SimpleNamespace(context=media), log=logger,
    )

    assert owner.build(target, {}) is None
    assert parent_path.call_args.args[0] is chain
    assert parent_path.call_args.args[1] is target
    assert chronology.call_count == int(contiguous)
    render.assert_not_called()
    media.assert_not_called()
    logger.warning.assert_called_once_with(
        "Verified parent path contains a post later than its child target_id=%s"
        if contiguous else "Verified parent path is not contiguous target_id=%s",
        "200",
    )
