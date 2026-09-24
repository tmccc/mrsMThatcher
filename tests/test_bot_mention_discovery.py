from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock, call

import mrs_bot_reply_assembly as assembly
import mrs_bot_reply_context as reply_context

import pytest

import mrs_bot_mention_discovery as discovery
import mrs_bot_mention_authority as authority_owner
import mrs_bot_reply_evaluation_state as evaluation_state
import mrs_bot_reply_state as reply_state
import mrs_bot_tweet_lookup_cache as tweet_cache_owner
from tests.helpers.mention_fixtures import (
    install_mention_pages,
    mention,
    mention_backlog,
    queue_active_mention,
)
from tests.helpers.bot_runtime import bot
from tests.helpers.reply_fixtures import (
    patch_reply_owner_method,
    restore_tweet_lookup_fetch,
)
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
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'reply_evidence'} or name.startswith('mrs_bot_') and name not in {'mrs_bot_author_quarantines', 'mrs_bot_mention_authority', 'mrs_bot_mention_discovery', 'mrs_bot_receipt_primitives', 'mrs_bot_reply_drafts', 'mrs_bot_reply_evaluation_state', 'mrs_bot_reply_history', 'mrs_bot_reply_native_media', 'mrs_bot_reply_state', 'mrs_bot_request_route_values', 'mrs_bot_runtime_state_helpers', 'mrs_bot_state_value_normalisation', 'mrs_bot_tweet_lookup_cache'}:
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


def test_queue_remove_alias_uses_discovery_implementation():
    assert bot.remove_pending_mention_candidate is discovery.remove_pending_mention_candidate


def test_get_mentions_compatibility_entry_point_assembles_each_call(monkeypatch):
    """The compatibility entry point keeps fresh, late-bound assembly per call."""
    state = {}
    first = Mock(return_value=[{"id": "101"}])
    second = Mock(return_value=[{"id": "102"}])
    assemble = Mock(side_effect=[first, second])
    monkeypatch.setattr(assembly.ReplyAssembly, "_mention_discovery_callback", assemble)

    assert bot._reply_assembly()._mention_discovery_callback()(state) == [{"id": "101"}]
    assert bot._reply_assembly()._mention_discovery_callback()(state) == [{"id": "102"}]

    assert assemble.call_args_list == [call(), call()]
    first.assert_called_once_with(state)
    second.assert_called_once_with(state)




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
        original = (
            bot._reply_assembly().mention_authority().validate_pending
            if label == "authority" else
            reply_context.valid_tweets_sorted_by_id if label == "sort" else getattr(bot, name)
        )
        trace.attach_mock(Mock(wraps=original), label)
        if label == "authority":
            patch_reply_owner_method(
                monkeypatch, authority_owner.MentionAuthority, "validate_pending", trace.authority,
            )
        elif label == "sort":
            monkeypatch.setattr(reply_context, name, getattr(trace, label))
        else:
            monkeypatch.setattr(bot, name, getattr(trace, label))
    failure = OSError("queue recovery save failed")
    if save_fails:
        trace.save.side_effect = failure
        with pytest.raises(OSError) as caught:
            bot._reply_assembly()._mention_queue_owner().pending(state)
        assert caught.value is failure
        assert [entry[0] for entry in trace.mock_calls] == ["authority", "save"]
    else:
        result = bot._reply_assembly()._mention_queue_owner().pending(state)
        assert [entry[0] for entry in trace.mock_calls
                if entry[0] != "authority" or entry.args[0] is state] == ["authority", "save", "sort"]
        assert [row["id"] for row in result] == ["97", "98"]
        assert all(row is state["mention_pending_candidates"][row["id"]] for row in result)
        assert all(row is state["mention_pending_candidates"][row["id"]] for row in trace.sort.call_args.args[0])
        assert trace.sort.call_args.kwargs == {
            "context": "durable pending mention", "log": bot.log,
        }
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
        bot._reply_assembly()._mention_discovery_callback()({"last_seen_mention_id": "unbounded"})
    queue = [mention(101, 201)]
    current_queue = Mock(return_value=queue)
    patch_reply_owner_method(monkeypatch, discovery.MentionQueue, "pending", current_queue)
    state = {}
    assert bot._reply_assembly()._mention_discovery_callback()(state) is queue
    current_queue.assert_called_once_with(state)
    blocked.assert_not_called()


def test_queued_legacy_mention_refresh_uses_real_owners_without_root_relays(monkeypatch):
    state = bot.default_state()
    candidate = mention(105, 205, "truncated")
    candidate.pop("text_is_complete")
    queue_active_mention(state, candidate, base_since_id="99")
    fresh = mention(105, 205, "The complete contribution.")
    fresh["created_at"] = "2026-09-20T12:00:00Z"
    request = Mock(return_value={"data": fresh, "includes": {"media": []}})
    restore_tweet_lookup_fetch(monkeypatch)
    monkeypatch.setattr(bot, "x_request", request)
    relays = {
        name: Mock(side_effect=AssertionError(f"root relay used: {name}"))
        for name in (
            "validate_pending_mention_candidate_authority",
            "get_tweet_by_id",
            "cache_tweet",
        )
    }
    for name, relay in relays.items():
        monkeypatch.setattr(bot, name, relay)

    result = bot._reply_assembly()._mention_discovery_callback()(state)

    assert result == [state["mention_pending_candidates"]["105"]]
    assert result[0] is state["mention_pending_candidates"]["105"]
    assert result[0]["text"] == "The complete contribution."
    assert result[0]["text_is_complete"] is True
    assert state["tweet_cache"]["105"]["text"] == "The complete contribution."
    request.assert_called_once()
    assert request.call_args.args == ("GET", "/2/tweets/105")
    assert request.call_args.kwargs["params"]["expansions"] == "attachments.media_keys"
    assert bot.load_state()["mention_pending_candidates"]["105"]["text"] == (
        "The complete contribution."
    )
    for relay in relays.values():
        relay.assert_not_called()


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
    bot._reply_assembly().reply_evaluations().record(state, target_id="103", lane="mention", reason="confirmed_no_reply")
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
            bot._reply_assembly()._mention_queue_owner().advance_watermark
            if label == "watermark" else bot._tweet_lookup_cache_owner().store
            if label == "cache" else bot._reply_assembly().reply_evaluations().prune_completed_mentions
            if label == "prune" else getattr(bot, name)
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
        elif label == "cache":
            patch_reply_owner_method(monkeypatch, tweet_cache_owner.TweetLookupCache, "store", observe)
        elif label == "prune":
            patch_reply_owner_method(
                monkeypatch, evaluation_state.ReplyEvaluations, "prune_completed_mentions", observe,
            )
        elif label == "media":
            monkeypatch.setattr(discovery, name, observe)
        else:
            monkeypatch.setattr(bot, name, observe)

    relays = {
        name: Mock(side_effect=AssertionError(f"root relay used: {name}"))
        for name in (
            "validate_pending_mention_candidate_authority",
            "cache_tweet",
            "get_tweet_by_id",
        )
    }
    for name, relay in relays.items():
        monkeypatch.setattr(bot, name, relay)
    result = bot._reply_assembly()._mention_discovery_callback()(state)
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
    for relay in relays.values():
        relay.assert_not_called()


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
    assert bot._reply_assembly()._mention_discovery_callback()(state) == []
    assert len(requests) == 1 and requests[0]["pagination_token"] == "A"
    assert events == [("mention_backlog_reset", {
        "reason": "continuation_token_limit", "since_id": "99", "pages_completed": 1,
        "token_fingerprint": "559aead08264d579",
    })]


def test_page_cache_failure_keeps_partial_cache_without_publishing_pending_candidates(monkeypatch):
    requests = install_mention_pages(monkeypatch, {
        "A": ([mention(103, 203), mention(101, 201)], None),
    })
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"
    state["mention_backlog"] = mention_backlog(since_id="99")
    state["mention_pagination"] = {"base_since_id": "99", "next_token": "A"}
    pending = state["mention_pending_candidates"]
    backlog = state["mention_backlog"]
    pagination = state["mention_pagination"]
    before_backlog = copy.deepcopy(backlog)
    save, event = Mock(), Mock()
    monkeypatch.setattr(bot, "save_state", save)
    monkeypatch.setattr(bot, "log_event", event)
    original_cache = bot._tweet_lookup_cache_owner().store
    cached = []
    failure = OSError("second mention could not be cached")

    def cache(document, **values):
        assert document is state
        cached.append(values["tweet_id"])
        if values["tweet_id"] == "103":
            raise failure
        return original_cache(document, **values)

    patch_reply_owner_method(monkeypatch, tweet_cache_owner.TweetLookupCache, "store", cache)
    with pytest.raises(OSError) as caught:
        bot._reply_assembly()._mention_discovery_callback()(state)

    assert caught.value is failure
    assert cached == ["101", "103"]
    assert "101" in state["tweet_cache"] and "103" not in state["tweet_cache"]
    assert state["mention_pending_candidates"] is pending and pending == {}
    assert state["mention_backlog"] is backlog and backlog == before_backlog
    assert state["mention_pagination"] is pagination
    assert state["last_seen_mention_id"] == "99"
    assert [params["pagination_token"] for params in requests] == ["A"]
    save.assert_not_called()
    event.assert_not_called()


@pytest.mark.parametrize("failure_boundary", ["save", "completed_event"])
def test_pager_caught_page_failure_continues_only_after_final_save(monkeypatch, failure_boundary):
    requests = install_mention_pages(monkeypatch, {
        "A": ([mention(101, 201)], None),
        None: ([mention(106, 206)], None),
    })
    monkeypatch.setattr(bot, "MENTIONS_MAX_PAGES_PER_CHECK", 2)
    state = bot.default_state()
    state["last_seen_mention_id"] = "99"
    state["mention_backlog"] = mention_backlog(since_id="99")
    state["mention_pagination"] = {"base_since_id": "99", "next_token": "A"}
    failure = OSError("final mention page callback failed")
    saved, events, caught = [], [], []

    def save(document, *, durable=False):
        assert document is state and durable is True
        if failure_boundary == "save":
            raise failure
        saved.append(copy.deepcopy(document))

    def event(name, **values):
        events.append(name)
        if name == "mention_backlog_completed":
            assert saved[-1]["last_seen_mention_id"] == "105"
            assert saved[-1]["mention_backlog"] == saved[-1]["mention_pagination"] == {}
            assert set(saved[-1]["mention_pending_candidates"]) == {"101"}
            raise failure

    original_paginate = bot.x_paginated_get

    def paginate(*args, **kwargs):
        try:
            return original_paginate(*args, **kwargs)
        except OSError as exc:
            assert exc is failure
            caught.append(exc)
            return {"data": [], "_pagination": {"pages_fetched": 1}}

    monkeypatch.setattr(bot, "save_state", save)
    monkeypatch.setattr(bot, "log_event", event)
    monkeypatch.setattr(bot, "x_paginated_get", paginate)

    result = bot._reply_assembly()._mention_discovery_callback()(state)

    assert caught == [failure]
    assert all(row is state["mention_pending_candidates"][row["id"]] for row in result)
    assert state["mention_backlog"] == state["mention_pagination"] == {}
    if failure_boundary == "save":
        assert saved == events == []
        assert [row["id"] for row in result] == ["101"]
        assert state["last_seen_mention_id"] == "105"
        assert [params.get("pagination_token") for params in requests] == ["A"]
    else:
        assert events == ["mention_backlog_completed"]
        assert [row["id"] for row in result] == ["101", "106"]
        assert state["last_seen_mention_id"] == "106"
        assert [params.get("pagination_token") for params in requests] == ["A", None]
        assert [params["since_id"] for params in requests] == ["99", "105"]


def test_retirement_keeps_queue_copy_identity_and_remove_truncation_watermark_order(monkeypatch):
    first, sibling = mention(105, 205), mention(104, 204)
    pending = {"105": first, "104": sibling}
    state = {"last_seen_mention_id": "99", "mention_pending_candidates": pending}
    trace = Mock()
    trace.remove.side_effect = bot.remove_pending_mention_candidate
    monkeypatch.setattr(discovery, "remove_pending_mention_candidate", trace.remove)
    patch_reply_owner_method(monkeypatch, discovery.MentionQueue, "advance_watermark", trace.update)
    monkeypatch.setattr(bot, "log", trace.log)
    bot._reply_assembly()._mention_queue_owner().mark_seen(state, {**first, "_pagination_truncated": True})
    assert trace.mock_calls == [call.remove(state, "105")]
    assert state["mention_pending_candidates"] is not pending
    assert pending["105"] is first and state["mention_pending_candidates"]["104"] is sibling
    assert state["last_seen_mention_id"] == "99"
    trace.reset_mock()
    for source in ("mention", "hot_post_reply"):
        bot._reply_assembly()._mention_queue_owner().mark_seen(state, {"id": "105", "_source": source, "_pagination_truncated": True})
    warning = call.log.warning(
        "Not advancing mention watermark for %s because mention pagination was truncated", "105",
    )
    assert trace.mock_calls == [call.remove(state, "105"), warning, warning]
    trace.reset_mock()
    bot._reply_assembly()._mention_queue_owner().mark_seen(state, {"id": 106})
    assert trace.mock_calls == [call.remove(state, "106"), call.update(state, "106")]
    assert discovery.remove_pending_mention_candidate(state, 104) is True
    untouched = state["mention_pending_candidates"]
    assert discovery.remove_pending_mention_candidate(state, 104) is False
    assert state["mention_pending_candidates"] is untouched
    with pytest.raises(AttributeError):
        bot._reply_assembly()._mention_queue_owner().mark_seen(state, None)


def test_watermark_keeps_early_assignment_value_error_fallback_and_native_type_error(monkeypatch):
    log = Mock()
    monkeypatch.setattr(bot, "log", log)
    state = {}
    bot._reply_assembly()._mention_queue_owner().advance_watermark(state, "0009")
    assert state["last_seen_mention_id"] == "0009" and log.debug.call_count == 1
    bot._reply_assembly()._mention_queue_owner().advance_watermark(state, "8")
    assert state["last_seen_mention_id"] == "9"
    bot._reply_assembly()._mention_queue_owner().advance_watermark(state, "malformed")
    assert state["last_seen_mention_id"] == "malformed"
    bot._reply_assembly()._mention_queue_owner().advance_watermark(state, "0010")
    assert state["last_seen_mention_id"] == "0010"
    log.reset_mock()
    with pytest.raises(TypeError):
        bot._reply_assembly()._mention_queue_owner().advance_watermark(state, None)
    assert state["last_seen_mention_id"] == "0010" and log.debug.call_count == 1
