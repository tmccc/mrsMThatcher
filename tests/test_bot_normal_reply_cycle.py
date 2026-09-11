from __future__ import annotations

from tests.helpers.reply_evaluation import legacy_reply_evaluator

from datetime import datetime
import inspect
import json
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock, call

import pytest

import mrs_bot_normal_reply_cycle as cycle
from tests.test_mention_backlog_author_quarantine import (
    editorial_no_reply,
    mention,
    queue_active_mention,
)
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_regular_post_receipt  # noqa: F401
from tests.helpers.reply_fixtures import (
    unit_approved_reply,
    unit_confirmed_v4_reply_receipt,
    unit_reply_context,
)


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, io, logging, os, random, socket, sys
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('normal reply cycle import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'reply_evidence'} or name.startswith('mrs_bot_') and name not in {'mrs_bot_normal_reply_cycle', 'mrs_bot_reply_cycle_interfaces', 'mrs_bot_reply_preparation', 'mrs_bot_reply_delivery'}:
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = forbidden
os.getenv = os._Environ.__getitem__ = forbidden
Path.home = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
before = random.getstate()
random.Random = random.seed = random.random = forbidden
import mrs_bot_normal_reply_cycle
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


def test_adapter_forwards_current_dependencies_arguments_results_and_errors(monkeypatch):
    adapter = bot.maybe_reply_to_mentions
    public = inspect.signature(adapter).parameters
    dependencies = inspect.signature(cycle.maybe_reply_to_mentions).parameters.keys() - public.keys()
    assert {"config", "persistence", "delivery"} <= dependencies
    state, result = {}, object()
    owner = Mock(return_value=result)
    monkeypatch.setattr(cycle, "maybe_reply_to_mentions", owner)
    for options in ({}, {"_fresh_mention_ai_evaluations": 3, "_skip_hot_post_fetch": True}):
        current = {key: object() for key in dependencies}
        for key, value in current.items():
            if key == "config":
                monkeypatch.setattr(bot._reply_cycle_interfaces, "NormalReplyConfig", Mock(return_value=value))
            elif key in {"persistence", "delivery"}:
                monkeypatch.setattr(bot, f"_reply_cycle_{key}", Mock(return_value=value))
            else:
                monkeypatch.setattr(bot, key, value)
        assert adapter(state, **options) is result
        args, kwargs = owner.call_args
        assert len(args) == 1 and args[0] is state
        expected = {
            "_fresh_mention_ai_evaluations": 0, "_skip_hot_post_fetch": False,
            **options, **current,
        }
        assert kwargs.keys() == expected.keys()
        assert all(kwargs[key] is value for key, value in expected.items())
    failure = TypeError("current owner failure")
    owner.side_effect = failure
    with pytest.raises(TypeError) as caught:
        adapter(state)
    assert caught.value is failure


def _configure_cycle(monkeypatch):
    epoch = 2_000_000_000
    monkeypatch.setattr(bot, "now_epoch", lambda: epoch)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime.fromtimestamp(epoch))
    monkeypatch.setattr(bot, "single_call_reply", {**bot.single_call_reply, "enabled": True})
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 48)
    monkeypatch.setattr(bot, "MAX_REPLIES_PER_AUTHOR_PER_DAY", 6)
    monkeypatch.setattr(bot, "MAX_MENTIONS_PER_CHECK", 5)
    monkeypatch.setattr(bot, "get_hot_post_reply_candidates", Mock(return_value=[]))
    monkeypatch.setattr(bot, "x_request", Mock(side_effect=AssertionError("unexpected provider request")))
    monkeypatch.setattr(bot, "create_post", Mock(side_effect=AssertionError("unexpected remote write")))
    monkeypatch.setattr(
        bot, "build_context_for_reply_ai",
        lambda candidate, _state: (unit_reply_context(
            target_id=candidate["id"], contribution=candidate["text"],
            target_author_id=candidate["author_id"],
        ), True),
    )
    monkeypatch.setattr(bot, "reply_media_context_for_candidate", Mock(return_value={}))
    monkeypatch.setattr(bot, "evaluate_single_call_reply", legacy_reply_evaluator(Mock(side_effect=editorial_no_reply)))


@pytest.mark.parametrize("initial_count,model_calls", [(2, 1), (2, 0), (4, 1)])
def test_backlog_continuation_uses_current_root_state_and_budget(monkeypatch, initial_count, model_calls):
    _configure_cycle(monkeypatch)
    adapter = bot.maybe_reply_to_mentions

    def generate(context, *args, evaluation_outcome, **kwargs):
        editorial_no_reply(context, *args, evaluation_outcome=evaluation_outcome, **kwargs)
        evaluation_outcome["model_call_count"] = model_calls

    monkeypatch.setattr(bot, "evaluate_single_call_reply", legacy_reply_evaluator(generate))
    for _ in range(2):
        state = bot.default_state()
        queue_active_mention(state, mention(105, 205), base_since_id="99")
        result = object()

        def continue_backlog(actual_state, **kwargs):
            assert actual_state is state
            assert kwargs == {
                "_fresh_mention_ai_evaluations": initial_count + model_calls,
                "_skip_hot_post_fetch": True,
            }
            saved = json.loads(bot.STATE_FILE.read_text())
            assert saved["mention_pending_candidates"] == {}
            assert saved["mention_backlog"] == state["mention_backlog"]
            assert saved["reply_evaluation_records"]["105"]["outcome"] == "no_reply"
            return result

        current_callback = Mock(side_effect=continue_backlog)
        monkeypatch.setattr(bot, "maybe_reply_to_mentions", current_callback)
        actual = adapter(state, _fresh_mention_ai_evaluations=initial_count)
        if initial_count + model_calls < bot.MAX_MENTIONS_PER_CHECK:
            assert actual is result
            current_callback.assert_called_once_with(
                state, _fresh_mention_ai_evaluations=initial_count + model_calls,
                _skip_hot_post_fetch=True,
            )
        else:
            assert actual == bot.NORMAL_CHECK_STATUS_CHECKED
            current_callback.assert_not_called()
        assert state["mention_pending_candidates"] == {}
        assert state["last_seen_mention_id"] == "99"
    assert bot.get_hot_post_reply_candidates.call_count == 2
    bot.x_request.assert_not_called()
    bot.create_post.assert_not_called()


@pytest.mark.parametrize("callback", ["recovery_comparison_account_replies", "recover_pending_ai_reply"])
def test_draft_recovery_errors_propagate_before_generation(monkeypatch, callback):
    _configure_cycle(monkeypatch)
    state = bot.default_state()
    queue_active_mention(state, mention(105, 205), base_since_id="99")
    failure = RuntimeError("draft recovery failed locally")
    monkeypatch.setattr(bot, callback, Mock(side_effect=failure))
    saved = Mock(wraps=bot.save_state)
    accounted = Mock(wraps=bot.record_api_error)
    monkeypatch.setattr(bot, "save_state", saved)
    monkeypatch.setattr(bot, "record_api_error", accounted)

    with pytest.raises(RuntimeError) as caught:
        bot.maybe_reply_to_mentions(state)

    assert caught.value is failure
    assert "105" in state["mention_pending_candidates"]
    assert state["daily_reply_count"] == 0
    assert state.get("reply_evaluation_records", {}) == {}
    saved.assert_not_called()
    accounted.assert_not_called()
    bot.evaluate_single_call_reply.assert_not_called()
    bot.x_request.assert_not_called()
    bot.create_post.assert_not_called()


@pytest.mark.parametrize("boundary", ["attempt_binding", "confirmed_state"])
def test_receipt_preparation_and_confirmed_state_errors_are_not_transport_errors(
    monkeypatch, boundary,
):
    _configure_cycle(monkeypatch)
    state = bot.default_state()
    queue_active_mention(state, mention(105, 205), base_since_id="99")
    monkeypatch.setattr(
        bot, "evaluate_single_call_reply",
        legacy_reply_evaluator(lambda context, *_args, **_kwargs: unit_approved_reply(context)),
    )
    failure = RuntimeError("receipt boundary failed locally")
    callback = (
        "bind_conversational_reply_attempt_time"
        if boundary == "attempt_binding" else "apply_confirmed_reply_receipt"
    )
    monkeypatch.setattr(bot, callback, Mock(side_effect=failure))
    transport = Mock(return_value=({}, {"reply_post_id": "900"}))
    monkeypatch.setattr(bot, "post_conversational_reply_with_durable_identity", transport)
    accounted = Mock(wraps=bot.record_api_error)
    outcomes = Mock(wraps=bot.log_ai_reply_posting_outcome)
    cleanup = Mock(wraps=bot.remove_confirmed_reply_receipt)
    monkeypatch.setattr(bot, "record_api_error", accounted)
    monkeypatch.setattr(bot, "log_ai_reply_posting_outcome", outcomes)
    monkeypatch.setattr(bot, "remove_confirmed_reply_receipt", cleanup)

    with pytest.raises(RuntimeError) as caught:
        bot.maybe_reply_to_mentions(state)

    assert caught.value is failure
    saved = json.loads(bot.STATE_FILE.read_text())
    assert "mention:105" in saved["pending_ai_reply_drafts"]
    assert "105" in saved["mention_pending_candidates"]
    assert saved["daily_reply_count"] == state["daily_reply_count"] == 0
    assert transport.call_count == int(boundary == "confirmed_state")
    accounted.assert_not_called()
    outcomes.assert_not_called()
    cleanup.assert_not_called()
    bot.x_request.assert_not_called()
    bot.create_post.assert_not_called()


@pytest.mark.parametrize("fail_first_save", [False, True])
def test_mixed_quarantine_retirements_are_durable_before_later_context(
    monkeypatch, fail_first_save,
):
    _configure_cycle(monkeypatch)
    state = bot.default_state()
    candidates = [mention(105, 205), mention(106, 206), mention(107, 205), mention(108, 208)]
    candidates[1]["entities"] = {"mentions": []}
    queue_active_mention(state, candidates[0], base_since_id="99")
    state["mention_pending_candidates"].update({item["id"]: item for item in candidates[1:]})
    state["mention_backlog"]["highest_mention_id"] = "108"
    for offset in range(3):
        bot.record_qualifying_author_no_reply(state, "205", current_epoch=bot.now_epoch() - 3 + offset)
    original_save = bot.save_state
    snapshots = []
    failure = OSError("retirement save failed")

    def save(actual_state, *, durable=False):
        assert durable is True
        if fail_first_save:
            raise failure
        original_save(actual_state, durable=durable)
        snapshots.append(json.loads(bot.STATE_FILE.read_text()))

    context_failure = RuntimeError("stop at later context")
    context = Mock(side_effect=context_failure)
    monkeypatch.setattr(bot, "save_state", save)
    monkeypatch.setattr(bot, "build_context_for_reply_ai", context)

    with pytest.raises((OSError, RuntimeError)) as caught:
        bot.maybe_reply_to_mentions(state)

    if fail_first_save:
        assert caught.value is failure
        context.assert_not_called()
        assert set(state["mention_pending_candidates"]) == {"107", "108"}
        assert snapshots == []
    else:
        assert caught.value is context_failure
        assert [set(item["mention_pending_candidates"]) for item in snapshots] == [
            {"107", "108"}, {"108"},
        ]
        assert set(snapshots[-1]["reply_evaluation_records"]) == {"105", "106", "107"}
        assert snapshots[-1]["reply_evaluation_records"]["106"]["outcome"] == "reply_not_permitted"
        assert snapshots[-1]["last_seen_mention_id"] == "99"
        assert context.call_count == 1
        assert context.call_args.args[0]["id"] == "108"
    assert state["daily_reply_count"] == 0
    bot.evaluate_single_call_reply.assert_not_called()
    bot.x_request.assert_not_called()
    bot.create_post.assert_not_called()


def test_daily_reset_and_confirmed_reconciliation_precede_barrier_when_disabled(monkeypatch):
    _configure_cycle(monkeypatch)
    receipt = unit_confirmed_v4_reply_receipt(confirmation_epoch=bot.now_epoch())
    bot.write_confirmed_reply_receipt(receipt)
    state = bot.default_state()
    state.update(daily_reply_date="2000-01-01", daily_reply_count=48)
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", False)
    trace = Mock()
    for label, name in (
        ("reset", "reset_daily_reply_count_if_needed"),
        ("reconcile", "reconcile_confirmed_reply_receipt"),
    ):
        callback = Mock(wraps=getattr(bot, name))
        trace.attach_mock(callback, label)
        monkeypatch.setattr(bot, name, callback)
    original_barrier = bot.block_if_ambiguous_remote_post

    def barrier():
        saved = json.loads(bot.STATE_FILE.read_text())
        assert saved["daily_reply_count"] == state["daily_reply_count"] == 1
        assert saved["daily_reply_date"] == receipt["daily_reply_date"]
        assert saved["replied_to_ids"] == [receipt["target_id"]]
        assert not bot.CONFIRMED_REPLY_RECEIPT_FILE.exists()
        return original_barrier()

    trace.attach_mock(Mock(side_effect=barrier), "barrier")
    monkeypatch.setattr(bot, "block_if_ambiguous_remote_post", trace.barrier)
    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_DISABLED
    assert trace.mock_calls == [call.reset(state), call.reconcile(state), call.barrier()]
    bot.get_hot_post_reply_candidates.assert_not_called()
    bot.x_request.assert_not_called()
    bot.create_post.assert_not_called()


@pytest.mark.parametrize("boundary", ["context", "generation"])
def test_native_context_and_generation_errors_keep_their_distinct_boundaries(monkeypatch, boundary):
    _configure_cycle(monkeypatch)
    state = bot.default_state()
    queue_active_mention(state, mention(105, 205), base_since_id="99")
    failure = RuntimeError("local evaluation failure")
    name = "build_context_for_reply_ai" if boundary == "context" else "evaluate_single_call_reply"
    monkeypatch.setattr(bot, name, Mock(side_effect=failure))
    saved = Mock(wraps=bot.save_state)
    accounted = Mock(wraps=bot.record_api_error)
    monkeypatch.setattr(bot, "save_state", saved)
    monkeypatch.setattr(bot, "record_api_error", accounted)
    if boundary == "context":
        with pytest.raises(RuntimeError) as caught:
            bot.maybe_reply_to_mentions(state)
        assert caught.value is failure
        saved.assert_not_called()
    else:
        assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_API_ERROR
        saved.assert_called_once_with(state)
        assert "105" in json.loads(bot.STATE_FILE.read_text())["mention_pending_candidates"]
    assert "105" in state["mention_pending_candidates"]
    assert state["daily_reply_count"] == 0
    assert "105" not in state.get("reply_evaluation_records", {})
    accounted.assert_not_called()
    bot.x_request.assert_not_called()
    bot.create_post.assert_not_called()


def test_clarification_refresh_failure_defers_without_losing_candidate(monkeypatch):
    _configure_cycle(monkeypatch)
    state = bot.default_state()
    queue_active_mention(state, mention(105, 205), base_since_id="99")
    failure = bot.ApiError("temporary question lookup failure", service="x", status_code=503, request_method="GET", request_path="/2/tweets/100")
    monkeypatch.setattr(bot, "clarification_reply_context", Mock(side_effect=failure))
    assert bot.maybe_reply_to_mentions(state) == bot.NORMAL_CHECK_STATUS_API_ERROR
    assert "105" in state["mention_pending_candidates"]
    assert "105" in json.loads(bot.STATE_FILE.read_text())["mention_pending_candidates"]
    bot.evaluate_single_call_reply.assert_not_called()
    bot.create_post.assert_not_called()
