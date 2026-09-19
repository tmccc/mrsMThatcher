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

import mrs_bot_tick_coordination as coordination
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401
from tests.helpers.reply_fixtures import unit_sending_reply_receipt


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, io, logging, os, random, socket, sys, time, typing
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('tick coordination import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply'} or name.startswith('mrs_bot_') and name != 'mrs_bot_tick_coordination':
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = os.lstat = os.stat = forbidden
os.getenv = os._Environ.__getitem__ = forbidden
Path.home = logging.getLogger = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
time.time = time.monotonic = forbidden
before = random.getstate()
random.Random = random.seed = random.random = forbidden
import mrs_bot_tick_coordination
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


def test_adapters_forward_current_dependencies_references_and_native_errors(monkeypatch):
    for name, count in (
        ("sanitize_next_reply_lane_priority", 1),
        ("run_reply_lane_checks_for_tick", 19),
        ("maintain_global_remote_write_barrier_tick", 4),
    ):
        adapter = getattr(bot, name)
        public = inspect.signature(adapter).parameters
        dependencies = inspect.signature(getattr(coordination, name)).parameters.keys() - public.keys()
        assert len(dependencies) == count
        args = tuple(object() for param in public.values() if param.kind == param.POSITIONAL_OR_KEYWORD)
        options = {key: object() for key, param in public.items() if param.kind == param.KEYWORD_ONLY}
        with monkeypatch.context() as patch:
            for _ in range(2):
                result = object()
                owner = Mock(return_value=result)
                patch.setattr(bot, "_tick_coordination", SimpleNamespace(**{name: owner}))
                current = {key: object() for key in dependencies}
                for key, value in current.items():
                    patch.setattr(bot, key, value)
                assert adapter(*args, **options) is result
                actual_args, actual_kwargs = owner.call_args
                assert len(actual_args) == len(args)
                assert all(actual is original for actual, original in zip(actual_args, args))
                assert actual_kwargs.keys() == (options | current).keys()
                assert all(actual_kwargs[key] is value for key, value in (options | current).items())
            failure = TypeError("current owner failure")
            owner.side_effect = failure
            with pytest.raises(TypeError) as caught:
                adapter(*args, **options)
            assert caught.value is failure


def test_sanitizer_keeps_warning_assignment_and_load_state_identity(monkeypatch):
    state = {"next_reply_lane_priority": "sideways"}
    trace = Mock()
    trace.load.return_value = state

    def warning(*_args):
        assert state["next_reply_lane_priority"] == "sideways"

    trace.log.warning.side_effect = warning
    monkeypatch.setattr(bot, "load_state", trace.load)
    monkeypatch.setattr(bot, "clear_expired_api_cooldowns", trace.clear)
    monkeypatch.setattr(bot, "log", trace.log)
    monkeypatch.setattr(bot, "save_state", trace.save)
    assert bot.load_runtime_state() is state
    assert trace.mock_calls == [
        call.load(), call.clear(state),
        call.log.warning("Invalid next_reply_lane_priority=%r; using normal", "sideways"),
    ]
    assert state == {"next_reply_lane_priority": "normal"}

    class QuoteValue:
        def __str__(self):
            return "quote"

        def __eq__(self, other):
            return False

    state["next_reply_lane_priority"] = QuoteValue()
    assert bot.sanitize_next_reply_lane_priority(state) is True
    assert state["next_reply_lane_priority"] == "quote"
    assert bot.sanitize_next_reply_lane_priority(state) is False
    for value in (None, False, 0):
        state["next_reply_lane_priority"] = value
        assert bot.sanitize_next_reply_lane_priority(state) is True
        assert state["next_reply_lane_priority"] == "normal"

    failure = RuntimeError("warning failed")
    state["next_reply_lane_priority"] = "invalid"
    trace.log.warning.side_effect = failure
    with pytest.raises(RuntimeError) as caught:
        bot.sanitize_next_reply_lane_priority(state)
    assert caught.value is failure
    assert state["next_reply_lane_priority"] == "invalid"
    trace.save.assert_not_called()


def _observe_tick(monkeypatch, state):
    trace = Mock()
    trace.scheduler = Mock(wraps=bot.scheduler_epoch_from_state)
    snapshots = []

    def save(current_state):
        assert current_state is state
        snapshots.append(copy.deepcopy(current_state))

    trace.save.side_effect = save
    trace.barrier.return_value = False
    trace.normal.return_value = bot.NORMAL_CHECK_STATUS_CHECKED
    trace.quote.return_value = bot.QUOTE_CHECK_STATUS_CHECKED
    for name, value in {
        "ENABLE_AUTO_REPLIES": True, "ENABLE_QUOTE_TWEET_CHECKS": True,
        "MIN_SECONDS_BETWEEN_REPLIES": 50, "REPLY_CHECK_EVERY_SECONDS": 60,
        "QUOTE_CHECK_EVERY_SECONDS": 80, "QUOTE_CHECK_SPACING_RETRY_SECONDS": 13,
        "scheduler_epoch_from_state": trace.scheduler, "save_state": trace.save,
        "maybe_reply_to_mentions": trace.normal, "maybe_reply_to_quote_tweets": trace.quote,
        "ambiguous_remote_post_is_blocking": trace.barrier,
        "log_event": trace.event, "log": trace.log,
    }.items():
        monkeypatch.setattr(bot, name, value)
    return trace, snapshots


@pytest.mark.parametrize("priority", ["normal", "quote"])
def test_repair_precedes_posted_lane_and_separate_canonical_saves(monkeypatch, priority):
    state = bot.default_state()
    state.update(last_reply_check_epoch="5", last_quote_tweet_check_epoch="7",
                 last_reply_epoch=0, next_reply_lane_priority=priority)
    cache = state["tweet_cache"]
    real_save = bot.save_state
    trace, snapshots = _observe_tick(monkeypatch, state)

    def save(current_state):
        assert current_state is state
        assert current_state["tweet_cache"] is cache
        real_save(current_state)
        snapshots.append(json.loads(bot.STATE_FILE.read_text()))

    trace.save.side_effect = save
    posted = object()
    monkeypatch.setattr(bot, "NORMAL_CHECK_STATUS_POSTED", posted)
    monkeypatch.setattr(bot, "QUOTE_CHECK_STATUS_POSTED", posted)
    trace.normal.return_value = trace.quote.return_value = posted
    result = bot.run_reply_lane_checks_for_tick(state, 100)
    # Canonical scheduler epochs are repaired and committed once before lanes.
    expected = [
        call.scheduler(state, "last_reply_check_epoch", current=100),
        call.scheduler(state, "last_quote_tweet_check_epoch", current=100),
        call.save(state),
    ]
    if priority == "normal":
        expected += [
            call.log.info("Due to check mentions"), call.normal(state),
            call.log.info("Normal/hot-post reply check status=%s", posted), call.barrier(),
            call.save(state), call.save(state),
            call.log.info("Normal/hot-post reply lane posted; next reply-lane priority=quote"),
        ]
        assert result == (100, 7)
        expected_schedules = [(5, 7, "normal"), (100, 7, "normal"), (100, 7, "quote")]
        trace.quote.assert_not_called()
    else:
        expected += [
            call.log.info("Due to check quote tweets"), call.quote(state),
            call.log.info("Quote-tweet check status=%s", posted), call.barrier(),
            call.event("quote_check_status", status=posted, priority="quote"), call.save(state),
            call.log.info("Quote-tweet reply lane posted; next reply-lane priority=normal"),
            call.save(state),
        ]
        assert result == (5, 100)
        expected_schedules = [(5, 7, "quote"), (5, 7, "normal"), (5, 100, "normal")]
        trace.normal.assert_not_called()
        assert trace.event.call_args.kwargs["status"] is posted
    assert [item for item in trace.mock_calls if not item[0].startswith("log.debug")
            and item[0] != "log.isEnabledFor"
            and not (item[0] == "log.info" and str(item.args[0]).startswith("State candidate"))] == expected
    assert [(s["last_reply_check_epoch"], s["last_quote_tweet_check_epoch"],
             s["next_reply_lane_priority"]) for s in snapshots] == expected_schedules
    for callback in (trace.scheduler, trace.save, trace.normal, trace.quote):
        assert all(args.args[0] is state for args in callback.call_args_list)


def test_forced_normal_spacing_preserves_interval_before_exact_quote_retry(monkeypatch):
    state = dict(last_reply_check_epoch=95, last_quote_tweet_check_epoch=0,
                 last_reply_epoch=0, next_reply_lane_priority="normal")
    trace, snapshots = _observe_tick(monkeypatch, state)
    normal_skip, quote_skip = object(), object()
    monkeypatch.setattr(bot, "NORMAL_CHECK_STATUS_SKIPPED_SPACING", normal_skip)
    monkeypatch.setattr(bot, "QUOTE_CHECK_STATUS_SKIPPED_SPACING", quote_skip)
    trace.normal.return_value, trace.quote.return_value = normal_skip, quote_skip
    assert bot.run_reply_lane_checks_for_tick(state, 100) == (95, 33)
    assert snapshots == [state]
    assert state["next_reply_lane_priority"] == "normal"
    assert trace.mock_calls == [
        call.scheduler(state, "last_reply_check_epoch", current=100),
        call.scheduler(state, "last_quote_tweet_check_epoch", current=100),
        call.log.info("Quote-tweet check is due, but normal/hot-post reply lane has priority; running normal reply check first"),
        call.normal(state), call.log.info("Normal/hot-post reply check status=%s", normal_skip),
        call.barrier(),
        call.log.info("Normal/hot-post reply check skipped only because of reply spacing; normal check interval not consumed"),
        call.log.info("Normal/hot-post reply lane did not post; quote-tweet lane may use this slot"),
        call.log.info("Due to check quote tweets"), call.quote(state),
        call.log.info("Quote-tweet check status=%s", quote_skip), call.barrier(),
        call.event("quote_check_status", status=quote_skip, priority="normal"), call.save(state),
        call.log.info("Quote-tweet check skipped only because of reply spacing; will retry in about %d seconds", 13),
    ]


@pytest.mark.parametrize("priority", ["normal", "quote"])
def test_returned_lane_status_rechecks_real_receipt_barrier_before_any_update(monkeypatch, priority):
    state = dict(last_reply_check_epoch=0, last_quote_tweet_check_epoch=0,
                 last_reply_epoch=0, next_reply_lane_priority=priority)
    before = copy.deepcopy(state)
    real_barrier = bot.ambiguous_remote_post_is_blocking
    trace, snapshots = _observe_tick(monkeypatch, state)
    trace.barrier.side_effect = real_barrier
    receipt = unit_sending_reply_receipt(lane="mention" if priority == "normal" else "quote_tweet")

    def lane(current_state):
        assert current_state is state
        bot.write_sending_reply_receipt(receipt)
        return bot.NORMAL_CHECK_STATUS_POSTED if priority == "normal" else bot.QUOTE_CHECK_STATUS_POSTED

    getattr(trace, priority).side_effect = lane
    assert bot.run_reply_lane_checks_for_tick(state, 100) == (0, 0)
    assert state == before and snapshots == []
    trace.event.assert_not_called()
    getattr(trace, "quote" if priority == "normal" else "normal").assert_not_called()
    assert bot.load_confirmed_reply_receipt() == ("sending", receipt)
    calls = trace.mock_calls
    status_index = next(i for i, item in enumerate(calls) if item[0] == "log.info" and "status=%s" in item.args[0])
    assert calls[status_index + 1] == call.barrier()
    assert calls[status_index + 2][0] == "log.critical"


def test_tick_native_conversion_event_save_and_current_safety_exception_boundaries(monkeypatch):
    state = dict(last_reply_check_epoch="0", last_quote_tweet_check_epoch="0",
                 last_reply_epoch="invalid", next_reply_lane_priority="quote")
    trace, snapshots = _observe_tick(monkeypatch, state)
    with pytest.raises(ValueError):
        bot.run_reply_lane_checks_for_tick(state, 100)
    assert len(snapshots) == 1
    assert state["last_reply_check_epoch"] == state["last_quote_tweet_check_epoch"] == 0
    trace.normal.assert_not_called()
    trace.quote.assert_not_called()

    state["last_reply_epoch"] = 0
    trace.reset_mock()
    snapshots.clear()
    failure = RuntimeError("event failed")
    trace.quote.return_value = bot.QUOTE_CHECK_STATUS_POSTED
    trace.event.side_effect = failure
    with pytest.raises(RuntimeError) as caught:
        bot.run_reply_lane_checks_for_tick(state, 100)
    assert caught.value is failure
    assert snapshots == [] and state["next_reply_lane_priority"] == "quote"
    trace.barrier.assert_called_once_with()
    trace.normal.assert_not_called()

    state["next_reply_lane_priority"] = "normal"
    trace.reset_mock()
    trace.normal.return_value = bot.NORMAL_CHECK_STATUS_POSTED
    trace.save.side_effect = failure
    with pytest.raises(RuntimeError) as caught:
        bot.run_reply_lane_checks_for_tick(state, 100)
    assert caught.value is failure
    assert state["last_reply_check_epoch"] == 100
    assert state["next_reply_lane_priority"] == "normal"
    trace.quote.assert_not_called()

    class CurrentSafetyError(Exception):
        pass

    monkeypatch.setattr(bot, "AmbiguousRemotePostOutcome", CurrentSafetyError)
    state["last_reply_check_epoch"] = 0
    trace.reset_mock()
    trace.normal.side_effect = CurrentSafetyError("current safety authority")
    assert bot.run_reply_lane_checks_for_tick(state, 100) == (0, 0)
    trace.save.assert_not_called()
    trace.quote.assert_not_called()
    trace.barrier.assert_not_called()


def test_blocked_tick_rechecks_after_logging_and_preserves_exception_signal_order(monkeypatch):
    trace = Mock()
    monkeypatch.setattr(bot, "ambiguous_remote_post_is_blocking", trace.blocked)
    monkeypatch.setattr(bot, "remote_write_safety_protocol_is_active", trace.protocol)
    monkeypatch.setattr(bot, "durable_remote_write_safety_barrier_exists", trace.durable)
    monkeypatch.setattr(bot, "log", trace.log)
    trace.blocked.return_value = False
    assert bot.maintain_global_remote_write_barrier_tick(already_logged=True) == (False, False)
    assert trace.mock_calls == [call.blocked()]
    trace.reset_mock()
    trace.blocked.return_value = True
    trace.protocol.return_value = False
    trace.durable.return_value = False
    assert bot.maintain_global_remote_write_barrier_tick(already_logged=False) == (True, True)
    assert trace.mock_calls == [
        call.blocked(), call.protocol(), call.durable(),
        call.log.critical("All remote posting and reply lanes are paused because the restart-persistent remote-write protocol is not activated or its sentinel is invalid; stopped clean-state activation or repair is required"),
    ]
    trace.reset_mock()
    trace.durable.side_effect = OSError("inspection failed after one-shot log")
    assert bot.maintain_global_remote_write_barrier_tick(already_logged=True) == (True, True)
    assert trace.mock_calls == [
        call.blocked(), call.protocol(), call.durable(),
        call.log.critical("The remote-write safety marker could not be inspected; the process will remain latched and must not be restarted", exc_info=True),
    ]
    trace.reset_mock()
    signal = KeyboardInterrupt("retained SIGINT")
    trace.durable.side_effect = signal
    with pytest.raises(KeyboardInterrupt) as caught:
        bot.maintain_global_remote_write_barrier_tick(already_logged=True)
    assert caught.value is signal
    assert trace.mock_calls == [call.blocked(), call.protocol(), call.durable()]
    trace.reset_mock()
    failure = TypeError("protocol callback failed")
    trace.protocol.side_effect = failure
    with pytest.raises(TypeError) as caught:
        bot.maintain_global_remote_write_barrier_tick(already_logged=False)
    assert caught.value is failure
    assert trace.mock_calls == [call.blocked(), call.protocol()]
