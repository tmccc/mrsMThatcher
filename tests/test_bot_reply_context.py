from __future__ import annotations

import copy
from datetime import datetime
import hashlib
import inspect
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrs_bot_reply_context as reply_context
from tests.helpers.bot_runtime import SCENARIOS, bot
from tests.helpers.bot_fixtures import isolate_regular_post_receipt  # noqa: F401
from tests.fake_api_server import load_scenario


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, io, logging, os, random, socket, sys, time
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('reply context import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'reply_evidence'} or name.startswith('mrs_bot_') and name != 'mrs_bot_reply_context':
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


def test_adapters_forward_current_dependencies_defaults_references_and_native_errors(monkeypatch):
    for name, count in (
        ("get_immediate_parent_id", 2), ("clean_text_for_reply_context", 2),
        ("tweet_context_text", 1), ("trim_context_text", 1),
        ("build_parent_chain", 10), ("is_our_auto_reply", 1),
        ("_reply_context_post", 3), ("_log_single_call_context_summary", 3),
        ("_directly_quoted_tweet_for_reply_context", 3),
        ("_quoted_post_for_reply_context", 2), ("_parent_path_is_contiguous", 1),
        ("_parent_path_is_chronological", 1), ("build_context_for_reply_ai", 20),
    ):
        adapter = getattr(bot, name)
        public = inspect.signature(adapter).parameters
        dependencies = inspect.signature(getattr(reply_context, name)).parameters.keys() - public.keys()
        assert len(dependencies) == count, name
        args = tuple(object() for param in public.values() if param.kind == param.POSITIONAL_OR_KEYWORD)
        result = object()
        owner = Mock(return_value=result)
        with monkeypatch.context() as patch:
            patch.setattr(reply_context, name, owner)
            for use_defaults in (True, False):
                current = {key: object() for key in dependencies}
                for key, value in current.items():
                    patch.setattr(bot, key, value)
                options = {
                    key: object() for key, param in public.items()
                    if param.kind == param.KEYWORD_ONLY
                    and (not use_defaults or param.default is param.empty)
                }
                expected = {
                    key: param.default for key, param in public.items()
                    if param.kind == param.KEYWORD_ONLY and param.default is not param.empty
                } | options | current
                assert adapter(*args, **options) is result
                actual_args, actual_kwargs = owner.call_args
                assert len(actual_args) == len(args)
                assert all(actual is original for actual, original in zip(actual_args, args))
                assert actual_kwargs.keys() == expected.keys()
                assert all(actual_kwargs[key] is value for key, value in expected.items())
            failure = TypeError(name)
            owner.side_effect = failure
            with pytest.raises(TypeError) as caught:
                adapter(*args, **options)
            assert caught.value is failure


def test_visible_post_omitted_maximum_keeps_definition_time_default_after_config_rebind(monkeypatch):
    fixed = inspect.signature(bot._reply_context_post).parameters["maximum_chars"].default
    assert fixed == bot.MAX_VISIBLE_TEXT_CHARACTERS
    assert inspect.signature(reply_context._reply_context_post).parameters["maximum_chars"].default is inspect.Parameter.empty
    tweet = {"id": 100, "author_id": 200, "text": "x" * (fixed + 20)}
    trim = Mock(wraps=bot.trim_context_text)
    monkeypatch.setattr(bot, "trim_context_text", trim)
    monkeypatch.setattr(bot, "MAX_VISIBLE_TEXT_CHARACTERS", 7)
    monkeypatch.setattr(bot, "MY_USER_ID", "200")

    omitted = bot._reply_context_post(tweet, principal_author_id="200")
    explicit = bot._reply_context_post(tweet, principal_author_id="200", maximum_chars=7)

    assert omitted == {"post_id": "100", "author_role": "account", "text": "x" * (fixed - 3) + "..."}
    assert explicit == {"post_id": "100", "author_role": "account", "text": "xxxx..."}
    assert [entry.args[1] for entry in trim.call_args_list] == [fixed, 7]
    assert inspect.signature(bot._reply_context_post).parameters["maximum_chars"].default == fixed


def test_parent_chain_keeps_current_callback_order_and_original_parent_references(monkeypatch):
    mention = {"id": "3", "referenced_tweets": [{"type": "replied_to", "id": "2"}]}
    parent = {"id": "2", "text_is_complete": True, "referenced_tweets": [{"type": "replied_to", "id": "1"}]}
    root = {"id": "1"}
    state = {"tweet_cache": {"2": parent}}
    trace = Mock()
    trace.attach_mock(Mock(wraps=bot.get_immediate_parent_id), "parent_id")
    trace.lookup.side_effect = [parent, root]
    for name, callback in {
        "get_immediate_parent_id": trace.parent_id, "prune_tweet_cache": trace.prune,
        "get_tweet_by_id_cached": trace.lookup, "log_json_debug": trace.debug,
    }.items():
        monkeypatch.setattr(bot, name, callback)
    monkeypatch.setattr(bot, "THREAD_CONTEXT_MAX_DEPTH", 2)
    monkeypatch.setattr(bot, "THREAD_CONTEXT_MAX_NETWORK_FETCHES", 1)

    chain = bot.build_parent_chain(mention, state)

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
        bot.build_parent_chain({"referenced_tweets": [None]}, state)
    trace.prune.assert_not_called()
    trace.lookup.assert_not_called()


def test_quote_alias_and_lookup_keep_distinct_container_rules_and_first_result(monkeypatch):
    assert bot._direct_quote_id is reply_context._direct_quote_id
    first = {"type": "quoted", "id": 900}
    candidate = {"referenced_tweets": (first, {"type": "quoted", "id": "901"})}
    state, quoted = {}, {"id": "900", "text": "Original"}
    lookup = Mock(return_value=quoted)
    monkeypatch.setattr(bot, "get_tweet_by_id_cached", lookup)
    assert bot._direct_quote_id(candidate) is None
    assert bot._directly_quoted_tweet_for_reply_context(candidate, state) is quoted
    lookup.assert_called_once_with("900", state, include_media=True)
    assert lookup.call_args.args[1] is state
    candidate["referenced_tweets"] = list(candidate["referenced_tweets"])
    assert bot._direct_quote_id(candidate) == "900"
    lookup.reset_mock()
    lookup.return_value = None
    assert bot._directly_quoted_tweet_for_reply_context(candidate, state, include_media=False) is None
    lookup.assert_called_once_with("900", state, include_media=False)


def test_cached_observation_time_does_not_become_verified_chronology(monkeypatch):
    older = {"created_at": "observed", "cached_epoch": 20}
    target = {"created_at": "target"}
    parser = Mock(side_effect=lambda value: {"observed": 20, "target": 10}[value])
    monkeypatch.setattr(bot, "parse_x_datetime_to_epoch", parser)
    assert bot._parent_path_is_chronological([older], target) is True
    assert parser.call_args_list == [call("observed"), call("target")]
    older["cached_epoch"] = 20.0
    assert bot._parent_path_is_chronological([older], target) is False


def test_context_keeps_usable_suffix_raw_ancestor_quote_and_media_copy_metadata_order(monkeypatch):
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
    monkeypatch.setattr(bot, "build_parent_chain", Mock(return_value=chain))
    monkeypatch.setattr(bot, "get_tweet_by_id_cached", Mock(return_value=quoted))
    monkeypatch.setattr(bot, "ALWAYS_FETCH_PARENT_FOR_CONTEXT", True)
    monkeypatch.setattr(bot, "SKIP_REPLIES_TO_OWN_AUTO_REPLIES", False)
    trace = Mock()
    for name in ("bound_visible_conversation", "get_immediate_parent_id", "_log_single_call_context_summary"):
        callback = Mock(wraps=getattr(bot, name))
        trace.attach_mock(callback, name)
        monkeypatch.setattr(bot, name, callback)
    trace.attach_mock(Mock(wraps=copy.deepcopy), "copy")
    monkeypatch.setattr(bot, "copy", SimpleNamespace(deepcopy=trace.copy))
    media_results = []
    original_media = bot.reply_media_context_for_candidate

    def prepare_media(*args, **kwargs):
        result = original_media(*args, **kwargs)
        media_results.append(result)
        return result

    trace.media.side_effect = prepare_media
    monkeypatch.setattr(bot, "reply_media_context_for_candidate", trace.media)
    trace.clock.return_value = datetime(2030, 2, 3)
    monkeypatch.setattr(bot, "current_datetime", trace.clock)

    context, proceed = bot.build_context_for_reply_ai(target, state)

    assert proceed is True
    assert [entry[0] for entry in trace.mock_calls][-7:] == [
        "bound_visible_conversation", "copy", "media", "get_immediate_parent_id",
        "clock", "get_immediate_parent_id", "_log_single_call_context_summary",
    ]
    assert bot.build_parent_chain.call_args.args[0] is target
    assert bot.build_parent_chain.call_args.args[1] is state
    bot.get_tweet_by_id_cached.assert_called_once_with("900", state, include_media=True)
    assert [row["post_id"] for row in context["visible_conversation"]] == ["120", "130"]
    assert context["parent_thread"] == context["visible_conversation"][:-1]
    assert context["parent_thread"][0] is not context["visible_conversation"][0]
    assert context["quoted_post"] == {"post_id": "900", "author_role": "other_user", "text": "Quoted"}
    assert context["quoted_post_relationship"] == "root_quote"
    assert context["target_id"] == "130" and context["root_post_id"] == context["thread_id"] == "100"
    assert context["parent_post_id"] == "120" and context["lane"] == "hot_post"
    assert context["target_author_id"] == "200" and context["target_created_at"] == "source-date"
    assert context["current_date"] == "2030-02-03" and context["incoming_contribution"] == "Incoming"
    assert context["_prepared_media_context"] is media_results[0]
    assert media_results[0]["photos"][0]["source_post_id"] == "900"
    assert trace.media.call_args.args[0] is target
    assert trace.media.call_args.kwargs == {"lane": "hot_post", "target_id": "130", "quoted_candidate": quoted}
    assert trace.media.call_args.kwargs["quoted_candidate"] is quoted
    assert trace._log_single_call_context_summary.call_args.args[1] is context
    context["parent_thread"][0]["text"] = "Changed copy"
    assert context["visible_conversation"][0]["text"] == "Parent"
    assert (chain, target, quoted, state) == before


@pytest.mark.parametrize("boundary", ["canonical", "native_bound", "media"])
def test_context_preserves_canonical_rejection_and_native_bound_media_errors(monkeypatch, boundary):
    target = load_scenario(SCENARIOS / "normal_mention_reply.json")["mentions"][0]
    failure = bot.ContextValidationError("canonical") if boundary == "canonical" else TypeError(boundary)
    bound = Mock(wraps=bot.bound_visible_conversation)
    media, summary = Mock(), Mock()
    (media if boundary == "media" else bound).side_effect = failure
    monkeypatch.setattr(bot, "build_parent_chain", Mock(return_value=[]))
    monkeypatch.setattr(bot, "bound_visible_conversation", bound)
    monkeypatch.setattr(bot, "reply_media_context_for_candidate", media)
    monkeypatch.setattr(bot, "_log_single_call_context_summary", summary)
    if boundary == "canonical":
        assert bot.build_context_for_reply_ai(target, bot.default_state()) == ({}, False)
    else:
        with pytest.raises(TypeError) as caught:
            bot.build_context_for_reply_ai(target, bot.default_state())
        assert caught.value is failure
    assert media.call_count == (1 if boundary == "media" else 0)
    summary.assert_not_called()


def test_summary_keeps_canonical_encoding_exact_hash_and_native_encoder_errors(monkeypatch):
    context = {"target_id": "100", "visible_conversation": [{"text": "Private é"}],
               "quoted_post_id": "900", "_prepared_media_context": {"photos": [{"url": "https://example.invalid/private"}]}}
    logger = Mock()
    encoder = Mock(wraps=json.dumps)
    monkeypatch.setattr(bot, "log", logger)
    monkeypatch.setattr(bot, "json", SimpleNamespace(dumps=encoder))
    bot._log_single_call_context_summary("Context", context)
    encoder.assert_called_once_with(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    assert encoder.call_args.args[0] is context
    digest = hashlib.sha256(json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()
    assert logger.debug.call_args.args == (
        "%s summary target_id=%s visible_turn_count=%d visible_character_count=%d quoted_subject_present=%s media_count=%d context_sha256=%s",
        "Context", "100", 1, 9, True, 1, digest,
    )
    failure = RuntimeError("encoder failed")
    encoder.side_effect = failure
    logger.reset_mock()
    with pytest.raises(RuntimeError) as caught:
        bot._log_single_call_context_summary("Context", context)
    assert caught.value is failure
    logger.debug.assert_not_called()


@pytest.mark.parametrize("value", [object(), float("nan"), "\ud800"])
def test_summary_keeps_canonical_fallback_for_original_encoding_failures(monkeypatch, value):
    logger = Mock()
    monkeypatch.setattr(bot, "log", logger)
    bot._log_single_call_context_summary("Context", {"noncanonical": value})
    assert logger.debug.call_args.args[-1] == hashlib.sha256(b"non-canonical-single-call-context").hexdigest()
