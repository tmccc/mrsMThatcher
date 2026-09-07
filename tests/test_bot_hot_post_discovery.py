from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock, call

import pytest

import mrs_bot_hot_post_discovery as discovery
from tests.test_unit_helpers import (
    bot,
    invalid_pagination_cursor_error,
    isolate_regular_post_receipt,
    unit_approved_reply,
    unit_reply_context,
)


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, io, logging, os, random, socket, sys
from pathlib import Path
from types import ModuleType

def forbidden(*args, **kwargs):
    raise AssertionError('hot-post discovery import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'reply_evidence'} or name.startswith('mrs_bot_') and name != 'mrs_bot_hot_post_discovery':
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = forbidden
os.getenv = os._Environ.__getitem__ = forbidden
Path.home = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
before = random.getstate()
random.Random = random.seed = random.random = forbidden
import mrs_bot_hot_post_discovery
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
assert 'requests' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_adapters_forward_current_dependencies_arguments_defaults_results_and_errors(monkeypatch):
    counts = {
        "get_hot_post_reply_candidates": 26,
        "mark_hot_post_reply_skipped": 3,
        "maybe_mark_hot_post_reply_skipped": 1,
        "dedupe_reply_candidates": 2,
    }
    for name, count in counts.items():
        adapter = getattr(bot, name)
        public = inspect.signature(adapter).parameters
        dependencies = inspect.signature(getattr(discovery, name)).parameters.keys() - public.keys()
        assert len(dependencies) == count
        args = tuple(object() for p in public.values() if p.kind == p.POSITIONAL_OR_KEYWORD)
        kwargs = {p.name: object() for p in public.values() if p.kind == p.KEYWORD_ONLY}
        result = object()
        owner = Mock(return_value=result)
        with monkeypatch.context() as patch:
            patch.setattr(discovery, name, owner)
            for _ in range(2):
                current = {key: object() for key in dependencies}
                for key, value in current.items():
                    patch.setattr(bot, key, value)
                assert adapter(*args, **kwargs) is result
                actual_args, actual_kwargs = owner.call_args
                assert len(actual_args) == len(args)
                assert all(actual is expected for actual, expected in zip(actual_args, args))
                expected_kwargs = {**kwargs, **current}
                assert actual_kwargs.keys() == expected_kwargs.keys()
                assert all(actual_kwargs[key] is value for key, value in expected_kwargs.items())
            failure = TypeError(name)
            owner.side_effect = failure
            with pytest.raises(TypeError) as caught:
                adapter(*args, **kwargs)
            assert caught.value is failure

    owner = Mock()
    monkeypatch.setattr(discovery, "mark_hot_post_reply_skipped", owner)
    state = {}
    bot.mark_hot_post_reply_skipped(state, "101")
    assert owner.call_args.args == (state, "101")
    assert {key: owner.call_args.kwargs[key] for key in ("reason", "original_post_id", "retryable")} == {
        "reason": "unspecified", "original_post_id": None, "retryable": None,
    }
    monkeypatch.setattr(discovery, "maybe_mark_hot_post_reply_skipped", owner)
    candidate = {"id": "101"}
    bot.maybe_mark_hot_post_reply_skipped(state, candidate)
    assert owner.call_args.args == (state, candidate, "unspecified")


def test_early_flags_and_watch_failures_precede_tracking_changes(monkeypatch):
    state = {"hot_post_reply_since_ids": {"unwatched": "99"}, "hot_post_reply_check_counts": []}
    before = copy.deepcopy(state)
    trace = Mock()
    for label, name, result in (
        ("pause", "lane_paused", True),
        ("cooldown", "in_api_cooldown", True),
        ("watch", "load_extra_quote_watch_post_ids", []),
    ):
        callback = Mock(return_value=result)
        trace.attach_mock(callback, label)
        monkeypatch.setattr(bot, name, callback)
    monkeypatch.setattr(bot, "x_paginated_get", Mock(side_effect=AssertionError("must not fetch")))
    monkeypatch.setattr(bot, "save_state", Mock(side_effect=AssertionError("must not save")))
    monkeypatch.setattr(bot, "ENABLE_HOT_POST_REPLY_CHECKS", False)
    assert bot.get_hot_post_reply_candidates(state) == []
    assert trace.mock_calls == []
    monkeypatch.setattr(bot, "ENABLE_HOT_POST_REPLY_CHECKS", True)
    assert bot.get_hot_post_reply_candidates(state) == []
    trace.pause.return_value = False
    assert bot.get_hot_post_reply_candidates(state) == []
    trace.cooldown.return_value = False
    trace.watch.side_effect = OSError("watch unavailable")
    assert bot.get_hot_post_reply_candidates(state) == []
    trace.watch.side_effect = None
    assert bot.get_hot_post_reply_candidates(state) == []
    pause = call.pause("disable_replies", "disable_hot_post_replies")
    cooldown = call.cooldown(state, scope="quote")
    assert trace.mock_calls == [pause, pause, cooldown, pause, cooldown, call.watch(), pause, cooldown, call.watch()]
    assert state == before


def _configure_watch(tmp_path, monkeypatch, text="700\n"):
    path = tmp_path / "watch.txt"
    path.write_text(text)
    monkeypatch.setattr(bot, "EXTRA_QUOTE_WATCH_FILE", path)
    monkeypatch.setattr(bot, "ENABLE_HOT_POST_REPLY_CHECKS", True)
    monkeypatch.setattr(bot, "HOT_POST_REPLY_FULL_RESCAN_EVERY_CHECKS", 100)
    monkeypatch.setattr(bot, "HOT_POST_REPLY_USE_SINCE_ID", True)
    monkeypatch.setattr(bot, "MAX_HOT_POST_REPLIES_PER_CHECK", 1)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "MY_USERNAME", "MrsMThatcher")
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)


def test_discovery_keeps_draft_terminal_media_cache_and_save_order_and_references(tmp_path, monkeypatch):
    _configure_watch(tmp_path, monkeypatch)
    state = bot.default_state()
    old_since = {"700": "50", "removed": "9"}
    state["hot_post_reply_since_ids"] = old_since
    context = unit_reply_context(target_id="101", thread_id="700", lane="hot_post_reply")
    draft = unit_approved_reply(context)
    assert bot.store_pending_ai_reply(state, "101", "hot_post_reply", draft, context=context)
    pending = state["pending_ai_reply_drafts"]
    rows = [
        {
            "id": target, "author_id": "200", "conversation_id": "700", "text": "A contribution.",
            "created_at": "2026-07-20T12:00:00Z",
            "referenced_tweets": [{"type": "replied_to", "id": "700"}],
            "entities": {"mentions": [{"id": "12345", "username": "MrsMThatcher"}] if target == "102" else []},
        }
        for target in ("102", "101")
    ]
    includes = {"media": []}
    paginate = Mock(return_value={"data": rows, "includes": includes, "_pagination": {}})
    monkeypatch.setattr(bot, "x_paginated_get", paginate)
    trace, events, calls = [], [], {}
    for label, name in (
        ("media", "attach_media_to_tweets"), ("sort", "valid_tweets_sorted_by_id"),
        ("eligible", "reply_target_is_directly_eligible"), ("clear", "clear_pending_ai_reply"),
        ("terminal", "record_terminal_reply_evaluation"), ("mark", "mark_hot_post_reply_skipped"),
        ("cache", "cache_tweet"), ("save", "save_state"),
    ):
        original = getattr(bot, name)

        def observe(*args, _label=label, _callback=original, **kwargs):
            trace.append(_label)
            if _label == "cache":
                assert rows[0]["_source"] == "hot_post_reply"
                assert rows[0]["_hot_original_post_id"] == "700"
                assert kwargs["referenced_tweets"] is rows[0]["referenced_tweets"]
                assert state["hot_post_reply_since_ids"] is old_since
            return _callback(*args, **kwargs)

        callback = Mock(side_effect=observe)
        calls[label] = callback
        monkeypatch.setattr(bot, name, callback)

    def event(name, **values):
        trace.append(name)
        events.append((name, values))

    monkeypatch.setattr(bot, "log_event", event)
    result = bot.get_hot_post_reply_candidates(state)
    assert len(result) == 1 and result[0] is rows[0]
    assert trace == [
        "media", "sort", "eligible", "single_call_reply_posting_outcome", "clear", "terminal",
        "mark", "candidate_skipped", "reply_target_terminal", "eligible", "cache", "hot_search", "save",
    ]
    assert calls["media"].call_args.args[0] is rows
    assert calls["media"].call_args.args[1] is includes
    assert calls["sort"].call_args.args[0] is rows
    assert pending == {} and "pending_ai_reply_drafts" not in state
    assert events[0][1]["validated_draft_hash"] == draft.draft_record["validated_draft_hash"]
    assert events[0][1]["failure_reason"] == "reply_not_permitted_preflight"
    assert state["reply_evaluation_records"]["101"]["outcome"] == "reply_not_permitted"
    assert state["skipped_hot_reply_ids"] == ["101"]
    assert old_since == {"700": "50", "removed": "9"}
    assert state["hot_post_reply_since_ids"] == {"700": "50"}
    assert state["hot_post_reply_check_counts"] == {"700": 1}
    assert state["hot_post_reply_pagination_tokens"] == {}
    assert events[-1] == ("hot_search", {
        "lane": "hot_post", "original_post_id": "700", "raw_count": 2, "usable_count": 1,
        "since_id_used": "50", "full_rescan": False, "watermark_updated": False, "watermark": "50",
    })
    calls["save"].assert_called_once_with(state)
    saved = json.loads(bot.STATE_FILE.read_text())
    assert saved["hot_post_reply_check_counts"] == {"700": 1}
    assert saved["tweet_cache"]["102"]["post_type"] == "hot_post_reply"


@pytest.mark.parametrize("boundary", ["save", "api", "unexpected"])
def test_invalid_cursor_cleanup_and_request_errors_keep_partial_state_boundaries(tmp_path, monkeypatch, boundary):
    _configure_watch(tmp_path, monkeypatch, "700\n701\n")
    state = bot.default_state()
    since = {"700": "250", "obsolete": "9"}
    counts = {"700": 3, "obsolete": 4}
    tokens = {"700": "expired-token", "701": "other-token", "obsolete": "stale"}
    state.update(hot_post_reply_since_ids=since, hot_post_reply_check_counts=counts, hot_post_reply_pagination_tokens=tokens)
    failure = bot.ApiError("local API failure", service="x", status_code=503) if boundary == "api" else OSError(boundary)
    requests, saves = [], []
    original_save = bot.save_state

    def save(current, *, durable=False):
        assert current is state and durable is True
        assert current["hot_post_reply_since_ids"] is since
        assert current["hot_post_reply_check_counts"] is counts
        assert current["hot_post_reply_pagination_tokens"] == {"701": "other-token"}
        saves.append(copy.deepcopy(current))
        if boundary == "save":
            raise failure
        original_save(current, durable=durable)

    def request(path, params):
        assert path == "/2/tweets/search/recent"
        requests.append(dict(params))
        if len(requests) == 1:
            raise invalid_pagination_cursor_error()
        assert len(saves) == 1
        raise failure

    log = Mock()
    monkeypatch.setattr(bot, "save_state", save)
    monkeypatch.setattr(bot, "x_quote_lookup_request", request)
    monkeypatch.setattr(bot, "log", log)
    with pytest.raises(type(failure)) as caught:
        bot.get_hot_post_reply_candidates(state)
    assert caught.value is failure
    assert len(requests) == (1 if boundary == "save" else 2)
    assert requests[0]["pagination_token"] == "expired-token"
    for params in requests:
        assert params["since_id"] == "250"
        assert params["query"] == "conversation_id:700 -from:12345 -is:retweet"
    if boundary != "save":
        assert "pagination_token" not in requests[1]
        assert json.loads(bot.STATE_FILE.read_text())["hot_post_reply_check_counts"] == counts
    assert state["hot_post_reply_since_ids"] is since
    assert state["hot_post_reply_check_counts"] is counts
    assert state["hot_post_reply_pagination_tokens"] is not tokens
    assert tokens["700"] == "expired-token"
    message = "Failed to fetch hot-post replies for post %s" if boundary == "api" else "Unexpected failure fetching hot-post replies for post %s"
    log.exception.assert_called_once_with(message, "700")


def test_skip_marker_keeps_record_identity_limits_clock_and_raw_event_values(monkeypatch):
    clock, event = Mock(return_value=3000), Mock()
    monkeypatch.setattr(bot, "now_epoch", clock)
    monkeypatch.setattr(bot, "log_event", event)
    state = {}
    bot.mark_hot_post_reply_skipped(state, "")
    assert state == {}
    clock.assert_not_called()
    event.assert_not_called()
    records = {str(i): {"skipped_epoch": i} for i in range(2500)}
    ids = [str(i) for i in range(2000)]
    state.update(skipped_hot_reply_ids=ids, skipped_hot_reply_records=records)
    bot.mark_hot_post_reply_skipped(state, 0, original_post_id=0)
    assert state["skipped_hot_reply_records"] is records
    assert records["0"] == {"reason": "unspecified", "retryable": False, "skipped_epoch": 3000}
    assert state["skipped_hot_reply_ids"] == ids[1:] + ["0"]
    event.assert_called_once_with("candidate_skipped", lane="hot_post", id="0", reason="unspecified", original_post_id=0, retryable=False)
    bot.mark_hot_post_reply_skipped(state, "new", reason="handled", original_post_id=700, retryable=1)
    trimmed = state["skipped_hot_reply_records"]
    assert trimmed is not records and len(trimmed) == 2000 and len(records) == 2501
    assert list(trimmed) == [str(i) for i in range(502, 2500)] + ["0", "new"]
    assert all(trimmed[key] is records[key] for key in trimmed)
    assert records["new"] == {"reason": "handled", "retryable": True, "skipped_epoch": 3000, "original_post_id": "700"}
    assert ids == [str(i) for i in range(2000)]
    assert len(state["skipped_hot_reply_ids"]) == 2000


def test_skip_marker_preserves_native_trim_error_after_list_and_record_mutation(monkeypatch):
    records = {str(i): {"skipped_epoch": i} for i in range(2500)}
    records["0"]["skipped_epoch"] = "malformed"
    state = {"skipped_hot_reply_records": records}
    event = Mock()
    monkeypatch.setattr(bot, "now_epoch", lambda: 3000)
    monkeypatch.setattr(bot, "log_event", event)
    with pytest.raises(ValueError):
        bot.mark_hot_post_reply_skipped(state, "new")
    assert state["skipped_hot_reply_ids"] == ["new"]
    assert state["skipped_hot_reply_records"] is records and "new" in records
    event.assert_not_called()


def test_handoff_marker_uses_current_root_callback_for_either_hot_annotation(monkeypatch):
    state = {}
    marker = Mock()
    monkeypatch.setattr(bot, "mark_hot_post_reply_skipped", marker)
    bot.maybe_mark_hot_post_reply_skipped(state, {"id": "101"})
    marker.assert_not_called()
    bot.maybe_mark_hot_post_reply_skipped(state, {"id": 101, "_source": "hot_post_reply", "_hot_original_post_id": 0})
    marker.assert_called_once_with(state, "101", reason="unspecified", original_post_id=None)
    current = Mock()
    monkeypatch.setattr(bot, "mark_hot_post_reply_skipped", current)
    bot.maybe_mark_hot_post_reply_skipped(state, {"id": 101, "_also_hot_post_reply": True, "_hot_original_post_id": 700}, "handled")
    current.assert_called_once_with(state, "101", reason="handled", original_post_id="700")


def test_merge_keeps_first_objects_annotation_precedence_and_nested_copy_boundary(monkeypatch):
    first = {"id": 101, "referenced_tweets": []}
    second = {"id": "102", "referenced_tweets": [{"type": "replied_to", "id": "1"}]}
    existing_refs = second["referenced_tweets"]
    refs = [{"type": "replied_to", "id": "700", "nested": {"a": []}}]
    unique = {"id": "103"}
    mentions = [first, {"id": "101"}, second, {}]
    hot = [
        {"id": "101", "_hot_original_post_id": 700, "referenced_tweets": refs},
        {"id": "101", "_hot_original_post_id": 701, "referenced_tweets": []},
        {"id": "101", "_hot_original_post_id": ""},
        {"id": "102", "referenced_tweets": refs}, unique, {"id": "103"}, {},
    ]
    log, copier = Mock(), Mock(wraps=copy)
    monkeypatch.setattr(bot, "log", log)
    monkeypatch.setattr(bot, "copy", copier)
    result = bot.dedupe_reply_candidates(mentions, hot)
    assert len(result) == 3 and all(a is b for a, b in zip(result, [first, second, unique]))
    assert first["_also_hot_post_reply"] is second["_also_hot_post_reply"] is True
    assert first["_hot_original_post_id"] == "701"
    assert second["referenced_tweets"] is existing_refs
    assert first["referenced_tweets"] == refs and first["referenced_tweets"] is not refs
    assert first["referenced_tweets"][0]["nested"] is not refs[0]["nested"]
    copier.deepcopy.assert_called_once_with(refs)
    log.info.assert_called_with(
        "Reply candidate de-duplication: mentions=%d hot_post_replies=%d combined=%d "
        "duplicate_mentions=%d duplicate_hot_post_replies=%d", 4, 7, 3, 1, 5,
    )
    log.reset_mock()
    assert bot.dedupe_reply_candidates([first], [unique]) == [first, unique]
    log.info.assert_not_called()


def test_discovery_skips_legacy_quote_only_target_before_eligibility(tmp_path, monkeypatch):
    _configure_watch(tmp_path, monkeypatch)
    state = bot.default_state()
    state["replied_to_quote_post_ids"] = ["101"]
    monkeypatch.setattr(bot, "x_paginated_get", Mock(return_value={"data": [{
        "id": "101", "author_id": "200", "conversation_id": "700", "text": "Synthetic reply",
        "referenced_tweets": [{"type": "replied_to", "id": "700"}],
    }], "includes": {}, "_pagination": {}}))
    eligible = Mock(side_effect=AssertionError("confirmed target reached eligibility"))
    monkeypatch.setattr(bot, "reply_target_is_directly_eligible", eligible)
    assert bot.get_hot_post_reply_candidates(state) == []
    eligible.assert_not_called()
    assert state["replied_to_ids"] == []
    assert state["daily_reply_count"] == state["daily_quote_reply_count"] == 0
