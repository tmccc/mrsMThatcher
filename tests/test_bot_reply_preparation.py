from __future__ import annotations

import copy
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrs_bot_normal_reply_cycle as normal_cycle
import mrs_bot_quote_reply_cycle as quote_cycle
from mrs_bot_reply_cycle_interfaces import (
    NORMAL_CHECK_STATUS_API_ERROR,
    QUOTE_CHECK_STATUS_CHECKED,
    ReplyCycleDelivery,
    ReplyCyclePersistence,
)


class ApprovedReply(str):
    """Carry a draft without importing application or validation runtime."""


class CopyProbe(dict):
    """Expose receipt-copy order while retaining normal nested-copy behaviour."""

    def __init__(self, fields, callback):
        super().__init__(fields)
        self.callback = callback

    def __deepcopy__(self, memo):
        self.callback()
        return copy.deepcopy(dict(self), memo)


class StringProbe:
    """Observe identity coercion relative to persistence and receipt copying."""

    def __init__(self, value, callback):
        self.value, self.callback = value, callback

    def __str__(self):
        self.callback()
        return self.value


@pytest.fixture(params=["mention", "hot_post_reply", "quote_tweet"])
def preparation(request, monkeypatch):
    lane = request.param
    trace = Mock()
    monkeypatch.setattr(normal_cycle, "clear_author_evaluation_quarantine_history", trace.clear_quarantine)
    shared = {"values": []}
    context = CopyProbe({"shared": shared, "alias": shared}, trace.copy_context)
    reply = ApprovedReply("A validated reply.")
    reply.draft_record = CopyProbe(
        {
            "reply_kind": "social", "reason_code": "useful_reply",
            "validated_draft_hash": "draft-hash", "shared": shared,
        },
        trace.copy_draft,
    )
    pagination = CopyProbe({"base_since_id": "99", "cursor": "next"}, trace.copy_pagination)
    state = {"last_seen_mention_id": "99", "mention_pagination": pagination}
    target = {
        "id": "105", "author_id": "205",
        "conversation_id": StringProbe("100", trace.conversation_id),
        "_pagination_truncated": True, "_mention_pagination": pagination,
    }
    clarification = {
        key: object()
        for key in ("thread_id", "prior_bot_reply_id", "original_question_id", "trigger", "extra")
    }
    trace.store.return_value = True
    trace.valid_pagination.return_value = True
    bound = object()
    trace.bind.return_value = bound
    persistence = ReplyCyclePersistence(
        save=trace.save, store=trace.store, recover=Mock(), clear=Mock(),
    )
    delivery = ReplyCycleDelivery(
        load_receipt=Mock(), reconcile_receipt=Mock(), block_ambiguous=Mock(),
        bind_attempt=trace.bind, target_available=Mock(), post=Mock(),
        retire_rejected=Mock(), finalise=Mock(),
    )
    case = SimpleNamespace(
        lane=lane, trace=trace, shared=shared, context=context, reply=reply,
        state=state, target=target, pagination=pagination, clarification=clarification,
        bound=bound, persistence=persistence, delivery=delivery,
    )

    def run():
        common = {
            "SINGLE_CALL_STRATEGY_VERSION": "strategy-version",
            "_log_validated_single_call_reply": trace.validated,
            "delivery": delivery, "persistence": persistence,
            "log": SimpleNamespace(error=trace.error), "log_event": trace.event,
        }
        if lane == "quote_tweet":
            decision = quote_cycle._resolve_reply_evaluation(
                "105", SimpleNamespace(reply=case.reply), state,
                ValidatedReply=ApprovedReply,
                _is_terminal_candidate_local_failure=Mock(),
                log=common["log"], mark_quote_tweet_skipped=Mock(),
                reply_evaluations=SimpleNamespace(record=Mock()), persistence=persistence,
            )
            if decision is not None:
                return decision
            return quote_cycle._prepare_reply_receipt(
                quote_cycle._QuoteCandidate(target, "105", "205", "Incoming text"),
                StringProbe("900", trace.original_id), case.reply, context, state,
                **common,
            )
        return normal_cycle._prepare_reply_receipt(
            state,
            normal_cycle._ReplyCandidate(target, "105", "205", "Incoming text", lane, lane),
            case.reply, context, clarification,
            ValidatedReply=ApprovedReply,
            mention_pagination_provenance_is_valid=trace.valid_pagination,
            **common,
        )

    case.run = run
    case.steps = (
        (["clear_quarantine"] if lane == "mention" else [])
        + ["validated", "store", "save", "conversation_id"]
        + (["original_id"] if lane == "quote_tweet" else [])
        + ["copy_context", "copy_draft"]
        + (["valid_pagination", "valid_pagination", "copy_pagination"] if lane == "mention" else [])
        + ["bind"]
    )
    return case


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, io, logging, os, random, socket, sys
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('reply preparation import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'reply_evidence'} or name.startswith('mrs_bot_') and name != 'mrs_bot_reply_preparation':
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = forbidden
os.getenv = os._Environ.__getitem__ = forbidden
Path.home = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
before = random.getstate()
random.Random = random.seed = random.random = forbidden
import mrs_bot_reply_preparation
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
assert 'single_call_reply' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_lanes_preserve_persistence_copy_provenance_and_binding_order(preparation):
    case = preparation

    def save(actual_state, *, durable):
        assert actual_state is case.state and durable is True
        case.shared["values"].append("saved")

    case.trace.save.side_effect = save
    assert case.run() is case.bound
    assert [item[0] for item in case.trace.mock_calls] == case.steps
    case.trace.validated.assert_called_once_with(
        target_description="quote tweet" if case.lane == "quote_tweet" else "target",
        target_id="105", reply=case.reply,
    )
    args, kwargs = case.trace.store.call_args
    assert args[0] is case.state and args[1:3] == ("105", case.lane)
    assert args[3] is case.reply and kwargs["context"] is case.context
    receipt = case.trace.bind.call_args.args[0]
    assert receipt["schema_version"] == 4 and receipt["lifecycle_state"] == "sending"
    assert receipt["target_id"] == "105" and receipt["author_id"] == "205"
    assert receipt["candidate_source"] == case.lane and receipt["conversation_id"] == "100"
    assert receipt["reply_text"] is case.reply
    assert receipt["reply_context"] == case.context and receipt["reply_context"] is not case.context
    assert receipt["ai_reply_draft"] == case.reply.draft_record
    assert receipt["ai_reply_draft"] is not case.reply.draft_record
    copied_shared = receipt["reply_context"]["shared"]
    assert copied_shared is receipt["reply_context"]["alias"]
    assert copied_shared is not case.shared
    assert copied_shared is not receipt["ai_reply_draft"]["shared"]
    assert copied_shared["values"] == ["saved"]
    assert ("mention_pagination" in receipt) is (case.lane == "mention")
    if case.lane == "mention":
        assert receipt["mention_pagination"] == case.pagination
        assert receipt["mention_pagination"] is not case.pagination
        case.trace.clear_quarantine.assert_called_once_with(case.state, "205")
    if case.lane == "quote_tweet":
        assert receipt["original_post_id"] == "900"
        assert "clarification_reply" not in receipt
    else:
        assert "original_post_id" not in receipt
        assert receipt["clarification_reply"].keys() == case.clarification.keys() - {"extra"}
        assert all(receipt["clarification_reply"][key] is case.clarification[key]
                   for key in receipt["clarification_reply"])


@pytest.mark.parametrize("failed_step", ["store", "save", "copy_context", "copy_draft", "bind"])
def test_preparation_failures_propagate_without_later_steps(preparation, failed_step):
    case = preparation
    failure = RuntimeError(f"local {failed_step} failed")
    getattr(case.trace, failed_step).side_effect = failure

    with pytest.raises(RuntimeError) as caught:
        case.run()

    assert caught.value is failure
    assert [item[0] for item in case.trace.mock_calls] == case.steps[:case.steps.index(failed_step) + 1]
    case.trace.event.assert_not_called()
    case.delivery.post.assert_not_called()


@pytest.mark.parametrize("save_fails", [False, True])
def test_rejected_draft_logs_exact_lane_outcome_before_durable_save(preparation, save_fails):
    case = preparation
    case.trace.store.return_value = False
    failure = OSError("cannot save rejected draft state")
    if save_fails:
        case.trace.save.side_effect = failure
        with pytest.raises(OSError) as caught:
            case.run()
        assert caught.value is failure
    else:
        result = case.run()
        assert result.status == (QUOTE_CHECK_STATUS_CHECKED if case.lane == "quote_tweet" else NORMAL_CHECK_STATUS_API_ERROR)

    expected = case.steps[:case.steps.index("save")] + ["error", "event", "save"]
    assert [item[0] for item in case.trace.mock_calls] == expected
    message = "Single-call reply draft failed persistence validation; deferring target_id=%s source="
    case.trace.error.assert_called_once_with(
        message + ("quote_tweet" if case.lane == "quote_tweet" else "%s"),
        "105", *(() if case.lane == "quote_tweet" else (case.lane,)),
    )
    case.trace.event.assert_called_once_with(
        "single_call_reply_posting_outcome", status="draft_persistence_failed",
        lane=case.lane, target_id="105", strategy_version="strategy-version",
        reply_kind="social", reason_code="useful_reply", validated_draft_hash="draft-hash",
        failure_reason="draft_persistence_validation_failed",
    )
    case.trace.save.assert_called_once_with(case.state, durable=True)
    case.delivery.post.assert_not_called()


def test_unvalidated_reply_stops_before_quarantine_or_shared_preparation(preparation):
    case = preparation
    case.reply = "Not a validated reply object"
    result = case.run()
    assert result.status == (QUOTE_CHECK_STATUS_CHECKED if case.lane == "quote_tweet" else NORMAL_CHECK_STATUS_API_ERROR)
    assert [item[0] for item in case.trace.mock_calls] == ["error", "save"]
    if case.lane == "quote_tweet":
        expected_log = (
            "Single-call pipeline returned an unvalidated quote-tweet reply; deferring target_id=%s",
            "105",
        )
    else:
        expected_log = (
            "Single-call pipeline returned an unvalidated reply type; deferring target_id=%s source=%s",
            "105", case.lane,
        )
    assert case.trace.error.call_args == call(*expected_log)
    case.trace.save.assert_called_once_with(case.state, durable=True)


@pytest.mark.parametrize("preparation", ["mention"], indirect=True)
@pytest.mark.parametrize("invalid_provenance", ["candidate", "watermark", "active"])
def test_invalid_mention_provenance_fails_after_draft_save_and_copies(preparation, invalid_provenance):
    case = preparation
    if invalid_provenance == "candidate":
        case.trace.valid_pagination.return_value = False
    elif invalid_provenance == "watermark":
        case.state["last_seen_mention_id"] = "98"
    else:
        case.state["mention_pagination"] = {"base_since_id": "99", "cursor": "changed"}

    with pytest.raises(RuntimeError, match="Refusing to post a reply"):
        case.run()

    validation_calls = 2 if invalid_provenance == "active" else 1
    expected = case.steps[:case.steps.index("valid_pagination")] + ["valid_pagination"] * validation_calls
    assert [item[0] for item in case.trace.mock_calls] == expected
    case.trace.save.assert_called_once_with(case.state, durable=True)
    case.trace.bind.assert_not_called()
