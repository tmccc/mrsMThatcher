from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock, call

import pytest

import mrs_bot_mention_discovery as discovery
import mrs_bot_reply_state as reply_state
from tests.helpers.mention_fixtures import (
    install_mention_pages,
    mention,
    mention_backlog,
)
from tests.helpers.bot_runtime import bot
from tests.helpers.reply_fixtures import patch_reply_owner_method
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, dataclasses, io, logging, os, random, socket, sys
from pathlib import Path
from types import ModuleType

def forbidden(*args, **kwargs):
    raise AssertionError('mention discovery import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'reply_evidence'} or name.startswith('mrs_bot_') and name not in {'mrs_bot_author_quarantines', 'mrs_bot_mention_authority', 'mrs_bot_mention_discovery', 'mrs_bot_receipt_primitives', 'mrs_bot_reply_drafts', 'mrs_bot_reply_evaluation_state', 'mrs_bot_reply_history', 'mrs_bot_reply_native_media', 'mrs_bot_reply_state', 'mrs_bot_runtime_state_helpers', 'mrs_bot_state_value_normalisation', 'mrs_bot_tweet_lookup_cache'}:
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = forbidden
os.getenv = os._Environ.__getitem__ = forbidden
Path.home = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
before = random.getstate()
random.Random = random.seed = random.random = forbidden
import mrs_bot_mention_discovery
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
assert 'requests' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert discovery.normalise_tweet_text is bot.normalise_tweet_text
    assert discovery.handled_reply_target_ids is reply_state.handled_reply_target_ids
    assert discovery.terminal_reply_evaluation is bot.terminal_reply_evaluation


def test_adapters_forward_current_dependencies_arguments_results_and_errors(monkeypatch):
    for name, count in (("get_mentions", 20),):
        adapter = getattr(bot, name)
        public = inspect.signature(adapter).parameters
        dependencies = inspect.signature(getattr(discovery, name)).parameters.keys() - public.keys()
        assert len(dependencies) == count
        args = tuple(object() for _ in public)
        result = object()
        owner = Mock(return_value=result)
        with monkeypatch.context() as patch:
            patch.setattr(discovery, name, owner)
            for _ in range(2):
                current = {key: object() for key in dependencies}
                for key, value in current.items():
                    if key == "mention_queue":
                        factory = Mock(return_value=value)
                        patch.setattr(bot, "_mention_queue_owner", factory)
                    else:
                        patch.setattr(bot, key, value)
                assert adapter(*args) is result
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
    assert bot.remove_pending_mention_candidate is discovery.remove_pending_mention_candidate


def test_queue_adapters_bind_fresh_current_owners_without_runtime_access(monkeypatch):
    fields = {
        "state_file": "STATE_FILE", "validate_authority": "validate_pending_mention_candidate_authority",
        "save": "save_state", "sort_candidates": "valid_tweets_sorted_by_id", "log": "log",
    }
    for name, method in (
        ("pending_mention_candidates", "pending"),
        ("update_last_seen_mention_id", "advance_watermark"),
        ("mark_mention_seen_if_applicable", "mark_seen"),
    ):
        adapter = getattr(bot, name)
        parameters = inspect.signature(adapter).parameters
        assert tuple(parameters) == (("state",) if method == "pending" else
                                     ("state", "mention_id" if method == "advance_watermark" else "candidate"))
        args = tuple(object() for _ in parameters)
        result, captured = object(), []
        callback = Mock(return_value=result)

        def observe(owner, *actual_args):
            captured.append(owner)
            return callback(*actual_args)

        with monkeypatch.context() as patch:
            patch.setattr(discovery.MentionQueue, method, observe)
            for _ in range(2):
                current = {field: Mock() for field in fields}
                for field, root_name in fields.items():
                    patch.setattr(bot, root_name, current[field])
                assert adapter(*args) is result
                assert all(actual is expected for actual, expected in zip(callback.call_args.args, args))
                assert all(getattr(captured[-1], field) is value for field, value in current.items())
                assert all(not value.mock_calls for value in current.values())
            assert captured[0] is not captured[1]
            failure = OSError("current queue operation failed")
            callback.side_effect = failure
            with pytest.raises(OSError) as caught:
                adapter(*args)
            assert caught.value is failure


@pytest.mark.parametrize("save_fails", [False, True])
def test_queue_recovery_saves_before_sorting_and_keeps_returned_record_references(monkeypatch, save_fails):
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"
    state["mention_pending_candidates"] = {
        "98": mention(98, 198), "97": mention(97, 197), "105": mention(105, 205),
    }
    trace = Mock()
    for label, name in (
        ("authority", "validate_pending_mention_candidate_authority"),
        ("save", "save_state"), ("sort", "valid_tweets_sorted_by_id"),
    ):
        trace.attach_mock(Mock(wraps=getattr(bot, name)), label)
        monkeypatch.setattr(bot, name, getattr(trace, label))
    failure = OSError("queue recovery save failed")
    if save_fails:
        trace.save.side_effect = failure
        with pytest.raises(OSError) as caught:
            bot.pending_mention_candidates(state)
        assert caught.value is failure
        assert [entry[0] for entry in trace.mock_calls] == ["authority", "save"]
    else:
        result = bot.pending_mention_candidates(state)
        assert [entry[0] for entry in trace.mock_calls
                if entry[0] != "authority" or entry.args[0] is state] == ["authority", "save", "sort"]
        assert [row["id"] for row in result] == ["97", "98"]
        assert all(row is state["mention_pending_candidates"][row["id"]] for row in result)
        assert all(row is state["mention_pending_candidates"][row["id"]] for row in trace.sort.call_args.args[0])
        assert trace.sort.call_args.kwargs == {"context": "durable pending mention"}
        saved = json.loads(bot.STATE_FILE.read_text())
        assert set(saved["mention_pending_candidates"]) == {"97", "98"}
        assert saved["mention_backlog_reset_guard"]["head_traversal_started"] is False
    original_calls = [entry for entry in trace.authority.call_args_list if entry.args[0] is state]
    assert len(original_calls) == 1
    assert original_calls[0].kwargs == {"path": bot.STATE_FILE, "recover_pending_identity": True}
    trace.save.assert_called_once_with(state, durable=True)


def test_queue_authority_and_current_queue_precede_clock_settings_and_provider_work(monkeypatch):
    blocked = Mock(side_effect=AssertionError("queue access must precede discovery work"))
    for name in ("now_epoch", "x_paginated_get", "save_state"):
        monkeypatch.setattr(bot, name, blocked)
    monkeypatch.setattr(bot, "MENTIONS_MAX_PAGES_PER_CHECK", None)
    with pytest.raises(RuntimeError, match="without a bounded watermark"):
        bot.get_mentions({"last_seen_mention_id": "unbounded"})
    queue = [mention(101, 201)]
    current_queue = Mock(return_value=queue)
    patch_reply_owner_method(monkeypatch, discovery.MentionQueue, "pending", current_queue)
    state = {}
    assert bot.get_mentions(state) is queue
    current_queue.assert_called_once_with(state)
    blocked.assert_not_called()


@pytest.mark.parametrize("history_key", ["replied_to_ids", "replied_to_quote_post_ids"])
def test_real_pages_keep_media_cache_queue_references_and_final_commit_order(monkeypatch, history_key):
    original_save = bot.save_state
    first = mention(105, 205, "first page")
    first["attachments"] = {"media_keys": ["photo"]}
    requests = install_mention_pages(monkeypatch, {
        None: ([first, mention(104, 204), mention(103, 203)], "A"),
        "A": ([mention(105, 205, "duplicate page"), mention(101, 201)], None),
    })
    monkeypatch.setattr(bot, "MENTIONS_MAX_PAGES_PER_CHECK", 5)
    monkeypatch.setattr(bot, "save_state", original_save)
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"
    state[history_key] = ["104"]
    bot.record_terminal_reply_evaluation(state, target_id="103", lane="mention", reason="confirmed_no_reply")
    old_pending = state["mention_pending_candidates"]
    trace, responses, saved, page_queues = [], [], [], []
    original_request = bot.x_request

    def request(method, path, *, params):
        trace.append("request")
        assert method == "GET" and path == "/2/users/12345/mentions"
        response = original_request(method, path, params=params)
        response["includes"] = {"media": [{"media_key": "photo", "type": "photo", "url": "https://example.invalid/photo"}]}
        responses.append(response)
        return response

    monkeypatch.setattr(bot, "x_request", request)
    paginate = Mock(wraps=bot.x_paginated_get)
    monkeypatch.setattr(bot, "x_paginated_get", paginate)
    for label, name in (
        ("media", "attach_media_to_tweets"), ("cache", "cache_tweet"),
        ("watermark", "update_last_seen_mention_id"),
        ("prune", "prune_completed_mention_quarantine_evaluations"), ("save", "save_state"),
    ):
        original = (
            bot._mention_queue_owner().advance_watermark
            if label == "watermark" else getattr(bot, name)
        )

        def observe(*args, _label=label, _callback=original, **kwargs):
            if _label != "prune" or args[0] is state:
                trace.append(_label + (":" + kwargs["tweet_id"] if _label == "cache" else ""))
            if _label == "media":
                assert args[0] is responses[-1]["data"] and args[1] is responses[-1]["includes"]
            if _label == "cache":
                row = next(row for row in responses[-1]["data"] if row["id"] == kwargs["tweet_id"])
                assert args[0] is state and kwargs["referenced_tweets"] is row["referenced_tweets"]
            if _label == "save":
                assert args[0] is state and kwargs == {"durable": True}
                saved.append(copy.deepcopy(state))
                page_queues.append(state["mention_pending_candidates"])
            return _callback(*args, **kwargs)

        if label == "watermark":
            patch_reply_owner_method(monkeypatch, discovery.MentionQueue, "advance_watermark", observe)
        elif label == "media":
            monkeypatch.setattr(discovery, name, observe)
        else:
            monkeypatch.setattr(bot, name, observe)

    result = bot.get_mentions(state)
    assert trace == [
        "save", "request", "media", "cache:103", "cache:104", "cache:105", "save",
        "request", "media", "cache:101", "cache:105", "watermark", "prune", "save",
    ]
    assert [params.get("pagination_token") for params in requests] == [None, "A"]
    assert paginate.call_count == 1
    options = paginate.call_args.kwargs
    assert options["max_pages"] == 5 and options["label"] == "mentions"
    assert options["initial_requested_tokens"] == set()
    assert options["retry_invalid_cursor_from_head"] is False
    assert [snapshot["last_seen_mention_id"] for snapshot in saved] == ["99", "99", "105"]
    assert saved[1]["mention_pagination"] == {"base_since_id": "99", "next_token": "A"}
    assert saved[2]["mention_backlog"] == saved[2]["mention_pagination"] == {}
    assert set(saved[2]["mention_pending_candidates"]) == {"101", "105"}
    assert old_pending == {} and page_queues[0] is old_pending
    assert page_queues[1] is not page_queues[2]
    assert page_queues[1]["105"] is page_queues[2]["105"]
    assert [row["id"] for row in result] == ["101", "105"]
    assert all(row is state["mention_pending_candidates"][row["id"]] for row in result)
    queued = result[1]
    raw = responses[0]["data"][0]
    assert queued["text"] == "first page" and queued is not raw
    assert queued["_attached_media"] == raw["_attached_media"]
    assert queued["_attached_media"][0] is not raw["_attached_media"][0]
    durable = json.loads(bot.STATE_FILE.read_text())
    assert durable["mention_pending_candidates"] == state["mention_pending_candidates"]
    assert durable["tweet_cache"]["105"]["text"] == "duplicate page"


def test_continuation_limit_uses_current_exception_and_saves_reset_before_event(monkeypatch):
    original_save = bot.save_state
    requests = install_mention_pages(monkeypatch, {"A": ([mention(103, 203)], None)})
    monkeypatch.setattr(bot, "save_state", original_save)
    monkeypatch.setattr(bot, "MENTION_BACKLOG_CONTINUATION_TOKEN_LIMIT", 1)

    class CurrentContinuationLimit(RuntimeError):
        pass

    monkeypatch.setattr(bot, "_MentionBacklogContinuationLimit", CurrentContinuationLimit)
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"
    state["mention_backlog"] = mention_backlog(since_id="99")
    state["mention_backlog"]["seen_tokens"] = ["prior"]
    state["mention_pagination"] = {"base_since_id": "99", "next_token": "A"}
    events = []

    def event(name, **values):
        saved = json.loads(bot.STATE_FILE.read_text())
        assert saved["last_seen_mention_id"] == "99"
        assert saved["mention_pending_candidates"] == saved["mention_backlog"] == saved["mention_pagination"] == {}
        assert saved["mention_backlog_reset_guard"] == {"base_since_id": "99", "head_traversal_started": False}
        events.append((name, values))

    monkeypatch.setattr(bot, "log_event", event)
    assert bot.get_mentions(state) == []
    assert len(requests) == 1 and requests[0]["pagination_token"] == "A"
    assert events == [("mention_backlog_reset", {
        "reason": "continuation_token_limit", "since_id": "99", "pages_completed": 1,
        "token_fingerprint": "559aead08264d579",
    })]


def test_retirement_keeps_queue_copy_identity_and_remove_truncation_watermark_order(monkeypatch):
    first, sibling = mention(105, 205), mention(104, 204)
    pending = {"105": first, "104": sibling}
    state = {"last_seen_mention_id": "99", "mention_pending_candidates": pending}
    trace = Mock()
    trace.remove.side_effect = bot.remove_pending_mention_candidate
    monkeypatch.setattr(discovery, "remove_pending_mention_candidate", trace.remove)
    patch_reply_owner_method(monkeypatch, discovery.MentionQueue, "advance_watermark", trace.update)
    monkeypatch.setattr(bot, "log", trace.log)
    bot.mark_mention_seen_if_applicable(state, {**first, "_pagination_truncated": True})
    assert trace.mock_calls == [call.remove(state, "105")]
    assert state["mention_pending_candidates"] is not pending
    assert pending["105"] is first and state["mention_pending_candidates"]["104"] is sibling
    assert state["last_seen_mention_id"] == "99"
    trace.reset_mock()
    for source in ("mention", "hot_post_reply"):
        bot.mark_mention_seen_if_applicable(state, {"id": "105", "_source": source, "_pagination_truncated": True})
    warning = call.log.warning(
        "Not advancing mention watermark for %s because mention pagination was truncated", "105",
    )
    assert trace.mock_calls == [call.remove(state, "105"), warning, warning]
    trace.reset_mock()
    bot.mark_mention_seen_if_applicable(state, {"id": 106})
    assert trace.mock_calls == [call.remove(state, "106"), call.update(state, "106")]
    assert discovery.remove_pending_mention_candidate(state, 104) is True
    untouched = state["mention_pending_candidates"]
    assert discovery.remove_pending_mention_candidate(state, 104) is False
    assert state["mention_pending_candidates"] is untouched
    with pytest.raises(AttributeError):
        bot.mark_mention_seen_if_applicable(state, None)


def test_watermark_keeps_early_assignment_value_error_fallback_and_native_type_error(monkeypatch):
    log = Mock()
    monkeypatch.setattr(bot, "log", log)
    state = {}
    bot.update_last_seen_mention_id(state, "0009")
    assert state["last_seen_mention_id"] == "0009" and log.debug.call_count == 1
    bot.update_last_seen_mention_id(state, "8")
    assert state["last_seen_mention_id"] == "9"
    bot.update_last_seen_mention_id(state, "malformed")
    assert state["last_seen_mention_id"] == "malformed"
    bot.update_last_seen_mention_id(state, "0010")
    assert state["last_seen_mention_id"] == "0010"
    log.reset_mock()
    with pytest.raises(TypeError):
        bot.update_last_seen_mention_id(state, None)
    assert state["last_seen_mention_id"] == "0010" and log.debug.call_count == 1
