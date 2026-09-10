from __future__ import annotations

import inspect
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock, call

import pytest

import mrs_bot_api_cooldowns as cooldowns
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_regular_post_receipt  # noqa: F401


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, io, logging, os, random, socket, sys, time
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('API cooldown import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply'} or name.startswith('mrs_bot_') and name != 'mrs_bot_api_cooldowns':
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = os.lstat = os.stat = forbidden
os.getenv = os._Environ.__getitem__ = forbidden
Path.home = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
time.time = time.monotonic = forbidden
before = random.getstate()
random.Random = random.seed = random.random = forbidden
import mrs_bot_api_cooldowns
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


def test_adapters_forward_current_dependencies_arguments_references_and_errors(monkeypatch):
    for name, count in (
        ("in_api_cooldown", 3), ("clear_expired_api_cooldowns", 2),
        ("prune_error_epochs", 3), ("cooldown_until_for_rate_limit", 1),
        ("record_api_error", 10),
    ):
        adapter = getattr(bot, name)
        public = inspect.signature(adapter).parameters
        dependencies = inspect.signature(getattr(cooldowns, name)).parameters.keys() - public.keys()
        assert len(dependencies) == count, name
        args = tuple(object() for param in public.values() if param.kind == param.POSITIONAL_OR_KEYWORD)
        result = object()
        owner = Mock(return_value=result)
        with monkeypatch.context() as patch:
            patch.setattr(cooldowns, name, owner)
            for options in ({}, {key: object() for key, param in public.items() if param.kind == param.KEYWORD_ONLY}):
                current = {key: object() for key in dependencies}
                for key, value in current.items():
                    patch.setattr(bot, key, value)
                assert adapter(*args, **options) is result
                expected = {key: options.get(key, param.default) for key, param in public.items() if param.kind == param.KEYWORD_ONLY} | current
                actual_args, actual_kwargs = owner.call_args
                assert len(actual_args) == len(args)
                assert all(actual is original for actual, original in zip(actual_args, args))
                assert actual_kwargs.keys() == expected.keys()
                assert all(actual_kwargs[key] is value for key, value in expected.items())
            failure = TypeError("current owner failure")
            owner.side_effect = failure
            with pytest.raises(TypeError) as caught:
                adapter(*args, **options)
            assert caught.value is failure


@pytest.mark.parametrize("scope,prefix,label", [
    ("api", "api", "X read API cooldown"),
    ("unknown", "api", "X read API cooldown"),
    ("write", "x_write_api", "X write API cooldown"),
    ("openai", "openai_api", "OpenAI API cooldown"),
    ("quote", "quote_api", "Quote API cooldown"),
])
def test_active_scope_uses_current_clock_datetime_reason_reference_and_native_coercion(monkeypatch, scope, prefix, label):
    trace = Mock()
    trace.clock.return_value = 1000
    trace.datetime.fromtimestamp.return_value.strftime.return_value = "local time"
    monkeypatch.setattr(bot, "now_epoch", trace.clock)
    monkeypatch.setattr(bot, "datetime", trace.datetime)
    monkeypatch.setattr(bot, "log", trace.log)
    reason = object()
    state = {f"{prefix}_cooldown_until_epoch": "1001", f"{prefix}_cooldown_reason": reason}
    assert bot.in_api_cooldown(state, scope=scope) is True
    assert trace.mock_calls == [
        call.clock(), call.datetime.fromtimestamp(1001),
        call.datetime.fromtimestamp().strftime("%Y-%m-%d %H:%M:%S"),
        call.log.warning("%s active until %s: %s", label, "local time", reason),
    ]
    del state[f"{prefix}_cooldown_reason"]
    assert bot.in_api_cooldown(state, scope=scope) is True
    assert trace.log.warning.call_args.args[-1] == label
    trace.reset_mock()
    state[f"{prefix}_cooldown_until_epoch"] = "1000"
    assert bot.in_api_cooldown(state, scope=scope) is False
    assert trace.mock_calls == [call.clock()]
    trace.reset_mock()
    state[f"{prefix}_cooldown_until_epoch"] = "bad epoch"
    with pytest.raises(ValueError):
        bot.in_api_cooldown(state, scope=scope)
    assert trace.mock_calls == []


def test_expiry_logs_before_mutation_in_lane_order_with_one_clock_and_no_save(monkeypatch):
    prefixes = ["api", "x_write_api", "openai_api", "quote_api"]
    labels = ["X read API cooldown", "X write API cooldown", "OpenAI API cooldown", "Quote API cooldown"]
    state = {f"{prefix}_cooldown_{suffix}": value for prefix in prefixes
             for suffix, value in (("until_epoch", "1000"), ("reason", prefix))}
    trace = Mock()
    trace.clock.return_value = 1000
    monkeypatch.setattr(bot, "now_epoch", trace.clock)
    monkeypatch.setattr(bot, "log", trace.log)
    monkeypatch.setattr(bot, "save_state", trace.save)
    failure = RuntimeError("expiry log failed")

    def before_clear(_message, label, until, reason):
        index = labels.index(label)
        assert (until, reason) == (1000, prefixes[index])
        assert [state[f"{prefix}_cooldown_until_epoch"] for prefix in prefixes] == [0] * index + ["1000"] * (4 - index)
        if index == 2:
            raise failure

    trace.log.info.side_effect = before_clear
    with pytest.raises(RuntimeError) as caught:
        bot.clear_expired_api_cooldowns(state)
    assert caught.value is failure
    assert state["x_write_api_cooldown_reason"] == ""
    assert state["openai_api_cooldown_reason"] == "openai_api"
    trace.clock.assert_called_once_with()
    trace.log.info.side_effect = None
    assert bot.clear_expired_api_cooldowns(state) is True
    assert [entry.args[1] for entry in trace.log.info.call_args_list] == labels[:3] + labels[2:]
    assert bot.clear_expired_api_cooldowns(state) is False
    trace.save.assert_not_called()


def test_prune_keeps_inclusive_cutoff_order_and_native_conversion_multiplicity(monkeypatch):
    trace = Mock()
    trace.clock.return_value = 1000
    monkeypatch.setattr(bot, "now_epoch", trace.clock)
    monkeypatch.setattr(bot, "ERROR_WINDOW_SECONDS", 100)
    monkeypatch.setattr(bot, "log", trace.log)

    class Epoch:
        def __int__(self):
            return trace.convert()

    boundary = Epoch()
    trace.convert.side_effect = [900, 901]
    epochs = [1001, boundary, "899", 1001]
    assert bot.prune_error_epochs(epochs) == [1001, 901, 1001]
    assert epochs[1] is boundary and epochs[2] == "899"
    assert trace.mock_calls == [call.clock(), call.convert(), call.convert(), call.log.debug("Pruned error epochs from %d to %d", 4, 3)]
    trace.reset_mock()
    failure = TypeError("epoch conversion failed")
    trace.convert.side_effect = failure
    with pytest.raises(TypeError) as caught:
        bot.prune_error_epochs([boundary])
    assert caught.value is failure
    assert trace.mock_calls == [call.clock(), call.convert()]


def test_rate_limit_uses_current_fallback_and_keeps_native_reset_types(monkeypatch):
    monkeypatch.setattr(bot, "COOLDOWN_AFTER_429_SECONDS", 17)
    assert bot.cooldown_until_for_rate_limit(1000, 1000) == 1017
    assert bot.cooldown_until_for_rate_limit(1000, 1000.5) == 1060.5
    assert bot.cooldown_until_for_rate_limit(1000, "") == 1017
    with pytest.raises(TypeError):
        bot.cooldown_until_for_rate_limit(1000, "1001")


def test_terminal_classification_precedes_clock_and_unknown_service_fails_after_clock(monkeypatch):
    trace = Mock()
    trace.classify.return_value = True
    monkeypatch.setattr(bot, "api_error_is_reply_not_allowed", trace.classify)
    monkeypatch.setattr(bot, "now_epoch", trace.clock)
    monkeypatch.setattr(bot, "prune_error_epochs", trace.prune)
    monkeypatch.setattr(bot, "log", trace.log)
    monkeypatch.setattr(bot, "save_state", trace.save)
    state, error = {}, RuntimeError("restriction")
    assert bot.record_api_error(state, error, "x", scope="write") is None
    assert state == {}
    assert trace.mock_calls == [call.classify(error), call.log.warning(
        "Not recording terminal target-specific X reply restriction in the transient write-error window: %s", error,
    )]
    trace.reset_mock()
    with pytest.raises(ValueError, match="^unsupported API error service: unknown$"):
        bot.record_api_error(state, error, "unknown", scope="write")
    assert state == {} and trace.mock_calls == [call.clock()]


def test_record_assigns_current_prune_list_before_diagnostic_failure(monkeypatch):
    old, retained, reads = [1], [2], []
    state = {"quote_x_error_epochs": old}
    trace = Mock()
    trace.clock.return_value = 1000
    trace.prune.return_value = retained
    monkeypatch.setattr(bot, "now_epoch", trace.clock)
    monkeypatch.setattr(bot, "prune_error_epochs", trace.prune)
    monkeypatch.setattr(bot, "log", trace.log)
    monkeypatch.setattr(bot, "save_state", trace.save)
    failure = RuntimeError("diagnostic property failed")

    class BrokenDiagnostic(Exception):
        def __getattribute__(self, name):
            if name in {"status_code", "reset_epoch", "diagnostic_event"}:
                reads.append(name)
                assert state["quote_x_error_epochs"] is retained
                assert retained == [2, 1000]
                if name == "diagnostic_event":
                    raise failure
            return super().__getattribute__(name)

    with pytest.raises(RuntimeError) as caught:
        bot.record_api_error(state, BrokenDiagnostic(), "x", scope="quote")
    assert caught.value is failure and old == [1]
    assert reads == ["status_code", "reset_epoch", "diagnostic_event"]
    assert trace.mock_calls == [call.clock(), call.prune(old)]
    assert trace.prune.call_args.args[0] is old


def test_unknown_scope_preserves_two_clock_samples_and_threshold_only_ordinary_save(monkeypatch):
    trace = Mock()
    trace.clock.side_effect = [1000, 1050, 1060, 1061]
    trace.datetime.fromtimestamp.return_value.strftime.return_value = "local time"
    for name, value in {"now_epoch": trace.clock, "datetime": trace.datetime, "log": trace.log, "save_state": trace.save,
                        "ERROR_WINDOW_SECONDS": 100, "MAX_X_ERRORS_PER_WINDOW": 2, "COOLDOWN_AFTER_REPEATED_ERRORS_SECONDS": 42}.items():
        monkeypatch.setattr(bot, name, value)
    original = [900]
    state, error = {"x_error_epochs": original}, RuntimeError("transient")
    bot.record_api_error(state, error, "x", scope="custom")
    assert state == {"x_error_epochs": [1000]} and original == [900]
    trace.save.assert_not_called()
    first_window = state["x_error_epochs"]
    trace.reset_mock()
    bot.record_api_error(state, error, "x", scope="custom")
    assert first_window == [1000] and state["x_error_epochs"] == [1000, 1060]
    assert state["api_cooldown_until_epoch"] == 1102
    assert state["api_cooldown_reason"] == "too many custom/x API errors in the last hour"
    assert trace.mock_calls == [
        call.clock(), call.clock(), call.log.debug("Pruned error epochs from %d to %d", 1, 1),
        call.log.warning("Recorded %s API error. status_code=%s errors_in_window=%d/%d reset_epoch=%s error=%s", "custom/x", None, 2, 2, None, error),
        call.datetime.fromtimestamp(1102), call.datetime.fromtimestamp().strftime("%Y-%m-%d %H:%M:%S"),
        call.log.error("Entering API cooldown after repeated errors until %s", "local time"), call.save(state),
    ]
    assert trace.save.call_args.args[0] is state


def test_diagnostic_warning_precedes_rate_limit_and_save_failure_keeps_mutated_state(monkeypatch):
    trace = Mock()
    trace.clock.return_value = 1000
    trace.prune.return_value = []
    trace.rate_limit.return_value = 2222
    trace.datetime.fromtimestamp.return_value.strftime.return_value = "local time"
    for name, value in {"now_epoch": trace.clock, "prune_error_epochs": trace.prune, "cooldown_until_for_rate_limit": trace.rate_limit,
                        "datetime": trace.datetime, "log": trace.log, "save_state": trace.save, "MAX_OPENAI_ERRORS_PER_WINDOW": 1}.items():
        monkeypatch.setattr(bot, name, value)
    state, reset, diagnostic = {}, object(), object()
    error = bot.ApiError("rate limited", service="openai", status_code=429)
    error.reset_epoch, error.diagnostic_sha256 = reset, diagnostic
    failure = RuntimeError("warning failed")
    trace.log.warning.side_effect = failure
    with pytest.raises(RuntimeError) as caught:
        bot.record_api_error(state, error, "openai")
    assert caught.value is failure and state == {"openai_error_epochs": [1000]}
    trace.rate_limit.assert_not_called()
    trace.save.assert_not_called()
    trace.reset_mock()
    trace.prune.return_value = []
    trace.log.warning.side_effect = None
    failure = OSError("ordinary save failed")
    trace.save.side_effect = failure
    with pytest.raises(OSError) as caught:
        bot.record_api_error(state, error, "openai")
    assert caught.value is failure
    assert state == {"openai_error_epochs": [1000], "openai_api_cooldown_until_epoch": 2222,
                     "openai_api_cooldown_reason": "openai returned 429/rate limit"}
    assert [entry[0] for entry in trace.mock_calls] == [
        "clock", "prune", "log.warning", "rate_limit", "datetime.fromtimestamp",
        "datetime.fromtimestamp().strftime", "log.error", "save",
    ]
    trace.log.warning.assert_called_once_with(
        "Recorded %s API error. status_code=%s errors_in_window=%d/%d reset_epoch=%s diagnostic_event=%s diagnostic_sha256=%s error=%s",
        "openai", 429, 1, 1, reset, None, diagnostic, error,
    )
    trace.rate_limit.assert_called_once_with(1000, reset)
    trace.log.error.assert_called_once_with("Entering API cooldown after 429 until %s", "local time")
    trace.save.assert_called_once_with(state)
    assert trace.save.call_args.args[0] is state
