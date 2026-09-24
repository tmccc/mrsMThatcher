"""Exercise the reply runners with explicit collaborators at the check boundary."""

from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock, call

from mrs_bot_normal_reply_cycle import NormalReplyCycle
from mrs_bot_quote_reply_cycle import QuoteReplyCycle
from mrs_bot_reply_cycle_interfaces import NormalReplyConfig, QuoteReplyConfig


def test_reply_assembly_import_is_inert():
    code = """
import builtins, collections.abc, dataclasses, io, logging, os, random, socket, sys, time
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('reply assembly import attempted runtime access')

builtins.open = io.open = os.open = forbidden
os.getenv = os._Environ.__getitem__ = forbidden
Path.home = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
time.time = time.monotonic = forbidden
before = random.getstate()
random.Random = random.seed = random.random = forbidden
import mrs_bot_reply_assembly
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


def test_normal_runner_reconciles_before_disabled_check_with_same_state():
    state = {}
    trace = Mock()
    accounting = Mock()
    delivery = Mock()
    delivery.load_receipt.return_value = ("valid", {})
    trace.attach_mock(accounting.reset, "reset")
    trace.attach_mock(delivery.load_receipt, "load")
    trace.attach_mock(delivery.reconcile_receipt, "reconcile")
    trace.attach_mock(delivery.block_ambiguous, "block")
    runner = NormalReplyCycle(
        delivery=delivery,
        author_quarantines=Mock(),
        ApiError=Exception,
        config=NormalReplyConfig(False, False, 10, 2, 0, "self", 5, 500),
        PipelineResult=object,
        RemoteOperationsPaused=RuntimeError,
        ReplyEvidenceUnavailable=RuntimeError,
        SINGLE_CALL_STRATEGY_VERSION="test",
        ValidatedReply=object,
        _log_validated_single_call_reply=Mock(),
        generation=Mock(),
        reply_contexts=Mock(),
        tweets=Mock(),
        clarifications=Mock(),
        persistence=Mock(),
        conversational_reply_pipeline_enabled=Mock(),
        accounting=accounting,
        dedupe_reply_candidates=Mock(),
        get_hot_post_reply_candidates=Mock(),
        get_mentions=Mock(),
        cooldowns=Mock(),
        is_probably_spam_or_not_worth_replying=Mock(),
        controls=Mock(),
        log=Mock(),
        log_ai_reply_posting_outcome=Mock(),
        log_event=Mock(),
        mention_queue=Mock(),
        maybe_mark_hot_post_reply_skipped=Mock(),
        now_epoch=Mock(),
        reply_evaluations=Mock(),
        history=Mock(),
        reply_evidence_repository=Mock(),
        reply_target_is_directly_eligible=Mock(),
        valid_tweets_sorted_by_id=Mock(),
    )

    assert runner.run(state) == "disabled"
    assert trace.mock_calls == [
        call.reset(state), call.load(), call.reconcile(state), call.block()
    ]
    assert trace.mock_calls[0].args[0] is state
    assert trace.mock_calls[2].args[0] is state
    runner.conversational_reply_pipeline_enabled.assert_not_called()


def test_quote_runner_reconciles_after_both_resets_before_disabled_check():
    state = {}
    trace = Mock()
    accounting = Mock()
    delivery = Mock()
    delivery.load_receipt.return_value = ("valid", {})
    trace.attach_mock(accounting.reset, "reset")
    trace.attach_mock(accounting.reset_quotes, "reset_quotes")
    trace.attach_mock(delivery.load_receipt, "load")
    trace.attach_mock(delivery.reconcile_receipt, "reconcile")
    trace.attach_mock(delivery.block_ambiguous, "block")
    runner = QuoteReplyCycle(
        delivery=delivery,
        ApiError=Exception,
        ContextValidationError=ValueError,
        config=QuoteReplyConfig(False, False, 10, 2, 0, "self", False, 0, 5, 5),
        PipelineResult=object,
        RemoteOperationsPaused=RuntimeError,
        ReplyEvidenceUnavailable=RuntimeError,
        SINGLE_CALL_STRATEGY_VERSION="test",
        ValidatedReply=object,
        _log_validated_single_call_reply=Mock(),
        generation=Mock(),
        api_error_is_permanent_target_failure=Mock(),
        watch_posts=Mock(),
        reply_contexts=Mock(),
        tweets=Mock(),
        persistence=Mock(),
        conversational_reply_pipeline_enabled=Mock(),
        accounting=accounting,
        get_quote_tweets_for_posts=Mock(),
        cooldowns=Mock(),
        is_probably_spam_or_not_worth_replying=Mock(),
        controls=Mock(),
        log=Mock(),
        log_ai_reply_posting_outcome=Mock(),
        log_event=Mock(),
        now_epoch=Mock(),
        parse_x_datetime_to_epoch=Mock(),
        reply_evaluations=Mock(),
        history=Mock(),
        reply_evidence_repository=Mock(),
        valid_tweets_sorted_by_id=Mock(),
    )

    assert runner.run(state) == "disabled"
    assert trace.mock_calls == [
        call.reset(state), call.reset_quotes(state), call.load(),
        call.reconcile(state), call.block(),
    ]
    assert all(entry.args[0] is state for entry in trace.mock_calls if entry.args)
    runner.conversational_reply_pipeline_enabled.assert_not_called()
