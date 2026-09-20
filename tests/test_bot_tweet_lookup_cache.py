from __future__ import annotations


import copy
from dataclasses import FrozenInstanceError
import inspect
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrs_bot_tweet_lookup_cache as lookup_cache
import mrs_bot_state_value_normalisation as state_values
from tests.helpers.bot_runtime import bot
from tests.helpers.reply_fixtures import patch_tweet_lookup_method, restore_tweet_lookup_fetch
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, copy, dataclasses, io, logging, os, random, socket, sys, time
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('tweet lookup cache import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'reply_evidence'} or name.startswith('mrs_bot_') and name not in {'mrs_bot_reply_native_media', 'mrs_bot_request_route_values', 'mrs_bot_tweet_lookup_cache'}:
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


OWNER_INPUTS = {
    "state_values": "_state_values_owner",
    "maximum_age_seconds": "TWEET_CACHE_MAX_AGE_SECONDS", "maximum_items": "TWEET_CACHE_MAX_ITEMS",
    "log": "log", "now_epoch": "now_epoch", "maximum_recent_own_posts": "RECENT_OWN_POST_IDS_MAX",
    "user_id": "MY_USER_ID", "state_file": "STATE_FILE", "current_datetime": "current_datetime",
    "api_error": "ApiError",
    "log_json_debug": "log_json_debug", "request": "x_request",
    "is_permanent_target_failure": "api_error_is_permanent_target_failure", "save_state": "save_state",
}


def test_owner_binds_fresh_boundaries_without_runtime_access(monkeypatch):
    owners = []
    for _ in range(2):
        current = {field: Mock() for field in OWNER_INPUTS}
        for field, name in OWNER_INPUTS.items():
            if field == "state_values":
                monkeypatch.setattr(bot, name, Mock(return_value=current[field]))
            else:
                monkeypatch.setattr(bot, name, current[field])
        owner = bot._tweet_lookup_cache_owner()
        assert isinstance(owner, lookup_cache.TweetLookupCache)
        for field, value in current.items():
            assert getattr(owner, field) is value
            value.assert_not_called()
        bot._state_values_owner.assert_called_once_with()
        owners.append(owner)
    assert owners[0] is not owners[1]
    assert all(getattr(owners[0], field) is not getattr(owners[1], field) for field in OWNER_INPUTS)
    with pytest.raises(FrozenInstanceError):
        owners[0].maximum_items = 1
    assert bot.normalise_tweet_text is lookup_cache.normalise_tweet_text
    assert bot.tweet_text_is_complete is lookup_cache.tweet_text_is_complete


def test_adapters_preserve_defaults_arguments_results_and_native_errors(monkeypatch):
    restore_tweet_lookup_fetch(monkeypatch)
    for name, method in (
        ("normalise_tweet_cache_entry", "normalise_entry"), ("normalise_tweet_cache", "normalise"),
        ("prune_tweet_cache", "prune"), ("record_recent_own_post", "record_recent_own_post"),
        ("seed_recent_own_post_ids_from_cache", "seed_recent_own_posts"), ("cache_tweet", "store"),
        ("_verified_tweet_lookup_row", "verified_row"), ("get_tweet_by_id", "fetch"),
        ("reply_target_is_available_immediately_before_send", "target_is_available"),
        ("get_tweet_by_id_cached", "get_cached"),
    ):
        adapter = getattr(bot, name)
        public = inspect.signature(adapter).parameters
        owned = inspect.signature(getattr(lookup_cache.TweetLookupCache, method)).parameters
        assert [(p.name, p.kind, p.default) for p in public.values()] == [
            (p.name, p.kind, p.default) for p in list(owned.values())[1:]
        ]
        args = tuple(object() for param in public.values() if param.kind == param.POSITIONAL_OR_KEYWORD)
        for use_defaults in (True, False):
            owner = Mock(spec=lookup_cache.TweetLookupCache)
            factory = Mock(return_value=owner)
            monkeypatch.setattr(bot, "_tweet_lookup_cache_owner", factory)
            implementation = getattr(owner, method)
            options = {
                key: object() for key, param in public.items()
                if param.kind == param.KEYWORD_ONLY
                and (not use_defaults or param.default is param.empty)
            }
            expected = {
                key: param.default for key, param in public.items()
                if param.kind == param.KEYWORD_ONLY and param.default is not param.empty
            } | options
            assert adapter(*args, **options) is implementation.return_value
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
    epoch = Mock(wraps=bot._state_values_owner().epoch)
    monkeypatch.setattr(
        state_values.StateValues, "epoch",
        lambda self, *args, **kwargs: epoch(*args, **kwargs),
    )
    root_epoch = Mock(side_effect=AssertionError("cache used obsolete root epoch relay"))
    monkeypatch.setattr(bot, "normalise_state_epoch", root_epoch)
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
    root_epoch.assert_not_called()
    assert source == before


def test_normalization_epoch_failure_precedes_refs_and_map_stops_at_first_rejection(monkeypatch, tmp_path):
    epoch, logger = Mock(return_value=None), Mock()
    monkeypatch.setattr(
        state_values.StateValues, "epoch",
        lambda self, *args, **kwargs: epoch(*args, **kwargs),
    )
    monkeypatch.setattr(bot, "log", logger)
    assert bot.normalise_tweet_cache_entry(1, {"referenced_tweets": "bad"}, path=tmp_path) is None
    epoch.assert_called_once_with(0, key="tweet_cache.1.cached_epoch", path=tmp_path)
    logger.error.assert_not_called()
    normalizer = Mock(side_effect=[{"id": "different"}, None])
    patch_tweet_lookup_method(monkeypatch, "normalise_entry", normalizer)
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
        owned_method = {"prune_tweet_cache": "prune", "normalise_tweet_cache_entry": "normalise_entry"}.get(name)
        if owned_method is not None:
            patch_tweet_lookup_method(monkeypatch, owned_method, callback)
        else:
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
    trace.attach_mock(Mock(wraps=bot._tweet_lookup_cache_owner().verified_row), "verify")

    def attach(rows, actual_includes):
        assert rows[0] is row and actual_includes is includes
        row["_attached_media"] = includes["media"]

    trace.attach_mock(Mock(side_effect=attach), "media")
    trace.attach_mock(Mock(), "debug")
    restore_tweet_lookup_fetch(monkeypatch)
    for name, callback in (("x_request", trace.request), ("_verified_tweet_lookup_row", trace.verify),
                           ("attach_media_to_tweets", trace.media), ("log_json_debug", trace.debug)):
        if name == "_verified_tweet_lookup_row":
            patch_tweet_lookup_method(monkeypatch, "verified_row", callback)
        elif name == "attach_media_to_tweets":
            monkeypatch.setattr(lookup_cache, name, callback)
        else:
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
    patch_tweet_lookup_method(monkeypatch, "fetch", fetch)
    store = Mock(side_effect=AssertionError("cache hit must not write"))
    patch_tweet_lookup_method(monkeypatch, "store", store)
    monkeypatch.setattr(bot, "save_state", Mock(side_effect=AssertionError("cache hit must not save")))
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
    store.assert_not_called()
    bot.save_state.assert_not_called()


def test_media_refresh_rejects_cached_identity_before_fresh_lookup(monkeypatch):
    monkeypatch.setattr(bot, "now_epoch", lambda: 100)
    state = {"tweet_cache": {"123": {"id": "124", "cached_epoch": 100}}}
    fetch = Mock(side_effect=AssertionError("bad cache identity precedes media lookup"))
    patch_tweet_lookup_method(monkeypatch, "fetch", fetch)
    with pytest.raises(bot.ApiError, match="mismatched post") as caught:
        bot.get_tweet_by_id_cached("123", state, include_media=True)
    assert caught.value.request_path == "/2/tweets/123"
    fetch.assert_not_called()


def test_cache_miss_saves_canonical_record_before_current_copy_and_media_decoration(monkeypatch):
    state = bot.default_state()
    fresh = {"id": "123", "text": "fresh", "author_id": "456", "created_at": "provided",
             "attachments": {"media_keys": ["photo"]}, "_attached_media": [{"media_key": "photo"}]}
    patch_tweet_lookup_method(monkeypatch, "fetch", Mock(return_value=fresh))
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
    monkeypatch.setattr(lookup_cache, "copy", SimpleNamespace(deepcopy=current_copy))
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
    patch_tweet_lookup_method(monkeypatch, "fetch", fetch)
    monkeypatch.setattr(bot, "save_state", save)
    monkeypatch.setattr(lookup_cache, "copy", SimpleNamespace(deepcopy=copier))
    with pytest.raises(ValueError) as caught:
        bot.get_tweet_by_id_cached("123", state)
    assert caught.value is failure
    assert list(state["tweet_cache"]) == (["123"] if boundary == "save" else [])
    fetch.assert_called_once_with("123")
    assert save.call_count == (1 if boundary == "save" else 0)
    copier.assert_not_called()


def test_cached_lookup_uses_owned_steps_and_pre_send_bypasses_stored_row(monkeypatch):
    restore_tweet_lookup_fetch(monkeypatch)
    state = bot.default_state()
    row = {"id": "123", "author_id": "456", "text": "verified", "created_at": "provided"}
    request = Mock(return_value={"data": row})
    monkeypatch.setattr(bot, "x_request", request)
    saves = Mock()
    monkeypatch.setattr(bot, "save_state", saves)
    clock = Mock(return_value=100)
    monkeypatch.setattr(bot, "now_epoch", clock)
    forbidden = Mock(side_effect=AssertionError("cache operation returned through a root-owned step"))
    for name in ("prune_tweet_cache", "normalise_tweet_cache_entry", "cache_tweet",
                 "_verified_tweet_lookup_row", "get_tweet_by_id"):
        monkeypatch.setattr(bot, name, forbidden)
    result = bot.get_tweet_by_id_cached("123", state)
    cached = state["tweet_cache"]["123"]
    assert result == cached and result is not cached
    assert clock.call_count == 3  # Initial prune, store prune, then the stored epoch.
    saves.assert_called_once_with(state)
    assert saves.call_args.args[0] is state
    assert request.call_count == 1
    assert bot.get_tweet_by_id_cached("123", state) is cached
    assert request.call_count == 1 and saves.call_count == 1
    assert bot.reply_target_is_available_immediately_before_send("123") is True
    assert request.call_count == 2  # Pre-send availability always performs a fresh request.
    assert saves.call_count == 1
    forbidden.assert_not_called()
