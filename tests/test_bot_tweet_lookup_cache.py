from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrs_bot_tweet_lookup_cache as lookup_cache
from tests.test_unit_helpers import (
    SOURCE_GET_TWEET_BY_ID,
    bot,
    isolate_regular_post_receipt,
)


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, io, logging, os, random, socket, sys, time
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('tweet lookup cache import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'reply_evidence'} or name.startswith('mrs_bot_') and name != 'mrs_bot_tweet_lookup_cache':
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
import mrs_bot_tweet_lookup_cache
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
assert 'requests' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_adapters_forward_current_dependencies_defaults_references_and_native_errors(monkeypatch):
    monkeypatch.setattr(bot, "get_tweet_by_id", SOURCE_GET_TWEET_BY_ID)
    for name, count in (
        ("normalise_tweet_cache_entry", 2), ("normalise_tweet_cache", 2),
        ("prune_tweet_cache", 4), ("record_recent_own_post", 2),
        ("seed_recent_own_post_ids_from_cache", 3), ("cache_tweet", 6),
        ("_verified_tweet_lookup_row", 1), ("get_tweet_by_id", 5),
        ("reply_target_is_available_immediately_before_send", 4),
        ("get_tweet_by_id_cached", 7),
    ):
        adapter = getattr(bot, name)
        public = inspect.signature(adapter).parameters
        dependencies = inspect.signature(getattr(lookup_cache, name)).parameters.keys() - public.keys()
        assert len(dependencies) == count
        args = tuple(object() for param in public.values() if param.kind == param.POSITIONAL_OR_KEYWORD)
        result = object()
        owner = Mock(return_value=result)
        with monkeypatch.context() as patch:
            patch.setattr(lookup_cache, name, owner)
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


def test_normalization_keeps_permissive_ids_optional_scalars_and_new_reference_containers(monkeypatch, tmp_path):
    path = tmp_path / "candidate.json"
    refs = [{"type": 1, "id": 2, 3: False, "omitted": None}]
    entry = {
        "id": 99, "author_id": 0, "conversation_id": None, "text": False,
        "referenced_tweets": refs, "cached_epoch": "100",
        "image_summary": 0, "post_type": False,
    }
    source = {17: entry}
    before = copy.deepcopy(source)
    epoch = Mock(wraps=bot.normalise_state_epoch)
    monkeypatch.setattr(bot, "normalise_state_epoch", epoch)
    result = bot.normalise_tweet_cache(source, path=path)
    assert result == {"17": {
        "id": "99", "author_id": "", "conversation_id": "99", "created_at": "",
        "text": "", "referenced_tweets": [{"type": "1", "id": "2", "3": "False"}],
        "cached_epoch": 100, "image_summary": "0", "post_type": "False",
    }}
    assert result is not source and result["17"] is not entry
    assert result["17"]["referenced_tweets"] is not refs
    assert result["17"]["referenced_tweets"][0] is not refs[0]
    epoch.assert_called_once_with("100", key="tweet_cache.17.cached_epoch", path=path)
    assert source == before


def test_normalization_epoch_failure_precedes_refs_and_map_stops_at_first_rejection(monkeypatch, tmp_path):
    epoch, logger = Mock(return_value=None), Mock()
    monkeypatch.setattr(bot, "normalise_state_epoch", epoch)
    monkeypatch.setattr(bot, "log", logger)
    assert bot.normalise_tweet_cache_entry(1, {"referenced_tweets": "bad"}, path=tmp_path) is None
    epoch.assert_called_once_with(0, key="tweet_cache.1.cached_epoch", path=tmp_path)
    logger.error.assert_not_called()
    normalizer = Mock(side_effect=[{"id": "different"}, None])
    monkeypatch.setattr(bot, "normalise_tweet_cache_entry", normalizer)
    rows = {1: {}, 2: {}, 3: {}}
    assert bot.normalise_tweet_cache(rows, path=tmp_path) is None
    assert normalizer.call_args_list == [call(1, rows[1], path=tmp_path), call(2, rows[2], path=tmp_path)]


@pytest.mark.parametrize("limit,expected", [(2, ["2", "3"]), (3, ["1", "2", "3"])])
def test_prune_keeps_cutoff_stable_cap_order_record_identity_and_log_before_assignment(monkeypatch, limit, expected):
    original = {key: {"cached_epoch": epoch} for key, epoch in ((1, 90), (2, 95), (3, 95), (4, 89))}
    state = {"tweet_cache": original}
    monkeypatch.setattr(bot, "now_epoch", lambda: 100)
    monkeypatch.setattr(bot, "TWEET_CACHE_MAX_AGE_SECONDS", 10)
    monkeypatch.setattr(bot, "TWEET_CACHE_MAX_ITEMS", limit)

    def logged(*args):
        assert state["tweet_cache"] is original
        assert args == ("Pruned tweet cache from %d to %d items", 4, limit)

    logger = Mock()
    logger.info.side_effect = logged
    monkeypatch.setattr(bot, "log", logger)
    bot.prune_tweet_cache(state)
    assert list(state["tweet_cache"]) == expected
    assert state["tweet_cache"] is not original
    assert all(row is original[int(key)] for key, row in state["tweet_cache"].items())
    logger.info.assert_called_once()
    logger.reset_mock(side_effect=True)
    previous = state["tweet_cache"]
    bot.prune_tweet_cache(state)
    assert state["tweet_cache"] == previous and state["tweet_cache"] is not previous
    logger.info.assert_not_called()


def test_recent_own_index_keeps_seed_tie_order_existing_list_and_fallback(monkeypatch):
    monkeypatch.setattr(bot, "MY_USER_ID", 12)
    monkeypatch.setattr(bot, "RECENT_OWN_POST_IDS_MAX", 2)
    state = {"tweet_cache": {
        "10": {"author_id": "12", "post_type": "quote", "cached_epoch": 100},
        9: {"author_id": 12, "post_type": "daily_meme", "cached_epoch": 100},
        "11": {"author_id": "12", "post_type": "auto_reply", "cached_epoch": 101},
        "13": {"author_id": "other", "post_type": "quote", "cached_epoch": 102},
    }}
    bot.seed_recent_own_post_ids_from_cache(state)
    assert state["recent_own_post_ids"] == ["9", "10"]
    seeded = state["recent_own_post_ids"]
    bot.seed_recent_own_post_ids_from_cache(state)
    assert state["recent_own_post_ids"] is seeded
    monkeypatch.setattr(bot, "RECENT_OWN_POST_IDS_MAX", 3)
    state["recent_own_post_ids"] = [8, "8", 7, 7]
    bot.record_recent_own_post(state, 8)
    assert state["recent_own_post_ids"] == ["8", "7", "7"]
    monkeypatch.setattr(bot, "RECENT_OWN_POST_IDS_MAX", 0)
    state.update(recent_own_post_ids=[], last_main_post_id=15)
    bot.seed_recent_own_post_ids_from_cache(state)
    assert state["recent_own_post_ids"] == ["15"]


def test_cache_write_uses_post_prune_map_clock_path_and_normalized_record_reference(monkeypatch):
    state, pruned, normalized = {"tweet_cache": {"old": {}}}, {}, {"id": "123"}
    trace = Mock()

    def prune(actual):
        assert actual is state
        state["tweet_cache"] = pruned

    trace.attach_mock(Mock(side_effect=prune), "prune")
    trace.attach_mock(Mock(return_value=SimpleNamespace(isoformat=lambda: "current-time")), "date")
    trace.attach_mock(Mock(return_value=100), "epoch")
    trace.attach_mock(Mock(return_value=normalized), "normalize")
    trace.attach_mock(Mock(), "log")
    for name, callback in (("prune_tweet_cache", trace.prune), ("current_datetime", trace.date),
                           ("now_epoch", trace.epoch), ("normalise_tweet_cache_entry", trace.normalize)):
        monkeypatch.setattr(bot, name, callback)
    monkeypatch.setattr(bot, "log", SimpleNamespace(info=trace.log))
    save = Mock(side_effect=AssertionError("cache write must not save"))
    monkeypatch.setattr(bot, "save_state", save)
    result = bot.cache_tweet(state, tweet_id=123, text="hello", author_id=456)
    assert result is normalized and pruned["123"] is normalized
    assert state["tweet_cache"] is pruned
    assert [c[0] for c in trace.mock_calls] == ["prune", "date", "epoch", "normalize", "log"]
    assert trace.normalize.call_args == call("123", {
        "id": "123", "author_id": "456", "conversation_id": "123", "created_at": "current-time",
        "referenced_tweets": [], "text": "hello", "cached_epoch": 100, "text_is_complete": True,
    }, path=bot.STATE_FILE)
    trace.normalize.return_value = None
    with pytest.raises(ValueError, match="Refusing to cache malformed tweet entry id=124"):
        bot.cache_tweet(state, tweet_id=124, text="bad", author_id="456")
    assert pruned == {"123": normalized}
    save.assert_not_called()


def test_direct_lookup_keeps_provider_verify_media_debug_order_and_row_identity(monkeypatch):
    row, includes, trace = {"id": "123"}, {"media": []}, Mock()
    trace.attach_mock(Mock(return_value={"data": row, "includes": includes}), "request")
    trace.attach_mock(Mock(wraps=bot._verified_tweet_lookup_row), "verify")

    def attach(rows, actual_includes):
        assert rows[0] is row and actual_includes is includes
        row["_attached_media"] = includes["media"]

    trace.attach_mock(Mock(side_effect=attach), "media")
    trace.attach_mock(Mock(), "debug")
    monkeypatch.setattr(bot, "get_tweet_by_id", SOURCE_GET_TWEET_BY_ID)
    for name, callback in (("x_request", trace.request), ("_verified_tweet_lookup_row", trace.verify),
                           ("attach_media_to_tweets", trace.media), ("log_json_debug", trace.debug)):
        monkeypatch.setattr(bot, name, callback)
    assert bot.get_tweet_by_id("123", include_media=True) is row
    assert [c[0] for c in trace.mock_calls] == ["request", "verify", "media", "debug"]
    trace.request.assert_called_once_with("GET", "/2/tweets/123", params={
        "tweet.fields": "author_id,created_at,conversation_id,referenced_tweets,entities,note_tweet,attachments",
        "expansions": "attachments.media_keys", "media.fields": "media_key,type,url,preview_image_url",
    })
    assert trace.verify.call_args.args[0] is row and trace.debug.call_args.args[1] is row
    assert row["_attached_media"] is includes["media"]


def test_cache_hit_and_media_refresh_preserve_stored_record_without_write_or_save(monkeypatch):
    cached = {
        "id": "123", "cached_epoch": 100, "conversation_id": "thread", "text": "old",
        "referenced_tweets": [{"type": "replied_to", "id": "99"}],
        "image_summary": "retained summary", "post_type": "quote",
    }
    fresh = {
        "id": "123", "text": "fresh", "attachments": {"media_keys": ["photo"]},
        "_attached_media": [{"media_key": "photo"}], "image_summary": "ignored summary",
    }
    original = {"123": cached, "stale": {"cached_epoch": 0}}
    state = {"tweet_cache": original}
    before = copy.deepcopy(cached)
    monkeypatch.setattr(bot, "now_epoch", lambda: 100)
    monkeypatch.setattr(bot, "TWEET_CACHE_MAX_AGE_SECONDS", 10)
    fetch = Mock(return_value=fresh)
    monkeypatch.setattr(bot, "get_tweet_by_id", fetch)
    for name in ("cache_tweet", "save_state"):
        monkeypatch.setattr(bot, name, Mock(side_effect=AssertionError("cache hit must not write or save")))
    assert bot.get_tweet_by_id_cached(123, state) is cached
    fetch.assert_not_called()
    assert state["tweet_cache"] is not original and list(state["tweet_cache"]) == ["123"]
    result = bot.get_tweet_by_id_cached(123, state, include_media=True)
    fetch.assert_called_once_with("123", include_media=True)
    assert result == before | {key: fresh[key] for key in ("text", "attachments", "_attached_media", "text_is_complete")}
    assert result is not cached and state["tweet_cache"]["123"] is cached
    assert result["referenced_tweets"][0] is not cached["referenced_tweets"][0]
    assert result["attachments"]["media_keys"] is not fresh["attachments"]["media_keys"]
    assert result["_attached_media"][0] is not fresh["_attached_media"][0]
    assert cached == before
    bot.cache_tweet.assert_not_called()
    bot.save_state.assert_not_called()


def test_media_refresh_rejects_cached_identity_before_fresh_lookup(monkeypatch):
    monkeypatch.setattr(bot, "now_epoch", lambda: 100)
    state = {"tweet_cache": {"123": {"id": "124", "cached_epoch": 100}}}
    fetch = Mock(side_effect=AssertionError("bad cache identity precedes media lookup"))
    monkeypatch.setattr(bot, "get_tweet_by_id", fetch)
    with pytest.raises(bot.ApiError, match="mismatched post") as caught:
        bot.get_tweet_by_id_cached("123", state, include_media=True)
    assert caught.value.request_path == "/2/tweets/123"
    fetch.assert_not_called()


def test_cache_miss_saves_canonical_record_before_current_copy_and_media_decoration(monkeypatch):
    state = bot.default_state()
    fresh = {"id": "123", "text": "fresh", "author_id": "456", "created_at": "provided",
             "attachments": {"media_keys": ["photo"]}, "_attached_media": [{"media_key": "photo"}]}
    monkeypatch.setattr(bot, "get_tweet_by_id", Mock(return_value=fresh))
    trace, saved = [], []
    original_save = bot.save_state

    def save(actual):
        assert actual is state
        assert "attachments" not in state["tweet_cache"]["123"]
        trace.append("save")
        original_save(actual)
        saved.append(json.loads(bot.STATE_FILE.read_text()))

    def current_copy(value):
        if value is state["tweet_cache"]["123"] or value is fresh["attachments"] or value is fresh["_attached_media"]:
            assert saved
            trace.append("copy")
        return copy.deepcopy(value)

    monkeypatch.setattr(bot, "save_state", save)
    monkeypatch.setattr(bot, "copy", SimpleNamespace(deepcopy=current_copy))
    result = bot.get_tweet_by_id_cached("123", state, include_media=True)
    assert trace == ["save", "copy", "copy", "copy"]
    cached = state["tweet_cache"]["123"]
    assert saved[0]["tweet_cache"]["123"] == cached
    assert result == cached | {key: fresh[key] for key in ("attachments", "_attached_media")}
    assert result is not cached and result["referenced_tweets"] is not cached["referenced_tweets"]
    assert result["attachments"]["media_keys"] is not fresh["attachments"]["media_keys"]
    assert result["_attached_media"][0] is not fresh["_attached_media"][0]


@pytest.mark.parametrize("boundary", ["provider", "save"])
def test_cache_miss_errors_preserve_pruning_insertion_and_no_copy_boundary(monkeypatch, boundary):
    state = {"tweet_cache": {"stale": {"cached_epoch": 0}}}
    monkeypatch.setattr(bot, "now_epoch", lambda: 100)
    monkeypatch.setattr(bot, "TWEET_CACHE_MAX_AGE_SECONDS", 10)
    failure = ValueError(boundary)
    fetch = Mock(return_value={"id": "123"}, side_effect=failure if boundary == "provider" else None)
    save = Mock(side_effect=failure)
    copier = Mock(side_effect=AssertionError("copy must follow successful save"))
    monkeypatch.setattr(bot, "get_tweet_by_id", fetch)
    monkeypatch.setattr(bot, "save_state", save)
    monkeypatch.setattr(bot, "copy", SimpleNamespace(deepcopy=copier))
    with pytest.raises(ValueError) as caught:
        bot.get_tweet_by_id_cached("123", state)
    assert caught.value is failure
    assert list(state["tweet_cache"]) == (["123"] if boundary == "save" else [])
    fetch.assert_called_once_with("123")
    assert save.call_count == (1 if boundary == "save" else 0)
    copier.assert_not_called()
