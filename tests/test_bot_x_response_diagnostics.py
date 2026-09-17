from __future__ import annotations

import inspect
import base64
import hashlib
import json
import math
from datetime import timedelta
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrs_bot_x_response_diagnostics as diagnostics
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401
from tests.helpers.x_response_fixtures import _raw_x_response


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, io, logging, os, random, socket, sys, time, typing
from types import ModuleType
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('X response diagnostics import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply'} or name.startswith('mrs_bot_') and name != 'mrs_bot_x_response_diagnostics':
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
import mrs_bot_x_response_diagnostics
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
    for name in ("_x_create_diagnostic_json_type", "_bounded_x_create_diagnostic_text"):
        assert getattr(bot, name) is getattr(diagnostics, name)
    for name in vars(diagnostics):
        if name.startswith(("X_CREATE_RESPONSE_", "_X_CREATE_RESPONSE_")):
            assert getattr(bot, name) is getattr(diagnostics, name)
    for name, count in (
        ("x_create_response_anomaly_reason", 1),
        ("_x_create_response_elapsed_ms", 1),
        ("emit_x_create_response_anomaly", 17),
    ):
        adapter = getattr(bot, name)
        public = inspect.signature(adapter).parameters
        dependencies = inspect.signature(getattr(diagnostics, name)).parameters.keys() - public.keys()
        assert len(dependencies) == count, name
        args = tuple(object() for param in public.values() if param.kind == param.POSITIONAL_OR_KEYWORD)
        result = object()
        owner = Mock(return_value=result)
        with monkeypatch.context() as patch:
            patch.setattr(bot, "_x_response_diagnostics", SimpleNamespace(**{name: owner}))
            required = {key: object() for key, param in public.items()
                        if param.kind == param.KEYWORD_ONLY and param.default is param.empty}
            for options in (required, {key: object() for key, param in public.items() if param.kind == param.KEYWORD_ONLY}):
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


def test_classification_type_distinctions_and_native_text_conversion(monkeypatch):
    class Mapping(dict):
        pass

    class Text(str):
        pass

    value = Text(" ")
    valid = Mock(return_value=True)
    monkeypatch.setattr(bot, "valid_post_id", valid)
    assert bot.x_create_response_anomaly_reason({"data": {"id": " "}}) == "data_id_blank"
    valid.assert_not_called()
    assert bot.x_create_response_anomaly_reason(Mapping(data=Mapping(id=value))) is None
    assert valid.call_args.args[0] is value
    assert bot._x_create_diagnostic_json_type(value) == f"python:{Text.__module__}.{Text.__qualname__}"
    assert bot._x_create_diagnostic_json_type(True) == "boolean"

    failure = RuntimeError("native diagnostic conversion")
    valid.side_effect = failure
    with pytest.raises(RuntimeError) as caught:
        bot.x_create_response_anomaly_reason({"data": {"id": value}})
    assert caught.value is failure

    class Unprintable:
        def __str__(self):
            raise failure

    bounded = bot._bounded_x_create_diagnostic_text
    assert bounded(None, maximum_characters=-1) is None
    assert bounded("é\x80", maximum_characters=2) == "é\x80"
    assert bounded("é\x80", maximum_characters=1) is None
    assert bounded("\x7f", maximum_characters=1) is None
    with pytest.raises(RuntimeError) as caught:
        bounded(Unprintable(), maximum_characters=0)
    assert caught.value is failure


def test_elapsed_uses_current_math_and_keeps_native_failure_boundaries(monkeypatch):
    finite = Mock(wraps=math.isfinite)
    monkeypatch.setattr(bot, "math", SimpleNamespace(isfinite=finite))
    seconds = Mock(return_value=0.0025)
    response = SimpleNamespace(elapsed=SimpleNamespace(total_seconds=seconds))
    assert bot._x_create_response_elapsed_ms(response) == 2
    finite.assert_called_once_with(0.0025)
    assert bot._x_create_response_elapsed_ms(object()) is None
    seconds.side_effect = OverflowError("conversion overflow")
    assert bot._x_create_response_elapsed_ms(response) is None
    finite.assert_called_once()
    failure = RuntimeError("unusual elapsed object")
    seconds.side_effect = failure
    with pytest.raises(RuntimeError) as caught:
        bot._x_create_response_elapsed_ms(response)
    assert caught.value is failure
    seconds.side_effect = None
    seconds.return_value = 1e308
    with pytest.raises(OverflowError):
        bot._x_create_response_elapsed_ms(response)
    finite.assert_called_with(1e308)
    seconds.return_value = -1
    assert bot._x_create_response_elapsed_ms(response) is None
    seconds.return_value = 0
    finite.return_value = False
    assert bot._x_create_response_elapsed_ms(response) is None


@pytest.fixture
def diagnostic_arguments():
    response = _raw_x_response(201, b"\x00\xff")
    response.headers.update({"Content-Type": "é", "Content-Length": "2",
                             "Traceparent": "xy", "Request-ID": "ab"})
    response.encoding = "é"
    response.elapsed = timedelta(milliseconds=125)
    return dict(
        response=response,
        transport_authority=SimpleNamespace(
            payload_sha256="fixture-payload", transaction_id="fixture-transaction",
            lane="conversational_reply",
        ),
        request_payload={"reply": {"in_reply_to_tweet_id": "  not-numeric  "}},
        reason="data_id_non_numeric", raw_body=bytearray(b"\x00\xff"),
        json_decode_succeeded=True,
        decoded={"zz": None, "data": {"id": "é\x00", "text": "",
                                       "edit_history_tweet_ids": []}},
    )


def test_emitter_current_constants_callbacks_order_and_event_identity(monkeypatch, diagnostic_arguments):
    current = {
        "X_CREATE_RESPONSE_ANOMALY_EVENT": "fixture-anomaly",
        "X_CREATE_RESPONSE_ANOMALY_SCHEMA_VERSION": 23,
        "X_CREATE_RESPONSE_ANOMALY_BODY_MAX_BYTES": 2,
        "X_CREATE_RESPONSE_SAFE_CORRELATION_HEADERS": ("traceparent", "request-id"),
        "_X_CREATE_RESPONSE_HEADER_MAX_CHARACTERS": 2,
        "_X_CREATE_RESPONSE_ID_VALUE_MAX_CHARACTERS": 2,
        "_X_CREATE_RESPONSE_TOP_LEVEL_KEY_LIMIT": 2,
        "_X_CREATE_RESPONSE_TOP_LEVEL_KEY_MAX_CHARACTERS": 4,
    }
    for name, value in current.items():
        monkeypatch.setattr(bot, name, value)
    calls = Mock()
    for name, function in (
        ("bounded", bot._bounded_x_create_diagnostic_text),
        ("kind", bot._x_create_diagnostic_json_type),
        ("elapsed", bot._x_create_response_elapsed_ms),
        ("b64", base64.b64encode), ("sha256", hashlib.sha256),
        ("dumps", json.dumps), ("finite", math.isfinite),
    ):
        calls.attach_mock(Mock(wraps=function), name)
    calls.clock.return_value = 1234567890
    for name, value in {
        "_bounded_x_create_diagnostic_text": calls.bounded,
        "_x_create_diagnostic_json_type": calls.kind,
        "_x_create_response_elapsed_ms": calls.elapsed,
        "base64": SimpleNamespace(b64encode=calls.b64),
        "hashlib": SimpleNamespace(sha256=calls.sha256),
        "json": SimpleNamespace(dumps=calls.dumps),
        "math": SimpleNamespace(isfinite=calls.finite),
        "now_epoch": calls.clock, "log": SimpleNamespace(error=calls.error),
    }.items():
        monkeypatch.setattr(bot, name, value)

    event = bot.emit_x_create_response_anomaly(**diagnostic_arguments)
    assert [entry[0] for entry in calls.mock_calls] == [
        "bounded", "bounded", "kind", "kind", "bounded", "bounded", "kind",
        "kind", "kind", "b64", "sha256", "clock", "elapsed", "finite",
        "bounded", "dumps", "sha256", "dumps", "error",
    ]
    assert calls.bounded.call_args_list == [
        call("xy", maximum_characters=2), call("ab", maximum_characters=2),
        call("2", maximum_characters=2), call("é", maximum_characters=2),
        call("é", maximum_characters=2),
    ]
    assert calls.kind.call_args_list[0].args[0] is diagnostic_arguments["decoded"]
    assert calls.kind.call_args_list[1].args[0] is diagnostic_arguments["decoded"]["data"]
    calls.elapsed.assert_called_once_with(diagnostic_arguments["response"])
    calls.finite.assert_called_once_with(0.125)
    assert type(calls.b64.call_args.args[0]) is bytes
    assert calls.b64.call_args.args[0] == b"\x00\xff"
    assert event["event"] == "fixture-anomaly" and event["schema_version"] == 23
    assert event["raw_body_complete"] is True
    assert event["raw_body_base64"] == "AP8="
    assert event["data_id_value_complete"] is True and event["data_id_value"] == "é\x00"
    assert event["decoded_top_level_keys_complete"] is True
    assert event["decoded_top_level_keys"] == ["data", "zz"]
    assert event["safe_correlation_headers"] == {"traceparent": "xy", "request-id": "ab"}
    assert event["target_id"] is diagnostic_arguments["request_payload"]["reply"]["in_reply_to_tweet_id"]
    assert event["recorded_at"] == 1234567890 and event["response_elapsed_ms"] == 125
    first, final = calls.dumps.call_args_list
    assert final.args[0] is event
    assert first.args[0] is not event and "diagnostic_sha256" not in first.args[0]
    options = dict(ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    assert first.kwargs == final.kwargs == options
    canonical = json.dumps(first.args[0], **options).encode("utf-8")
    assert calls.sha256.call_args_list == [call(b"\x00\xff"), call(canonical)]
    assert event["diagnostic_sha256"] == hashlib.sha256(canonical).hexdigest()
    calls.error.assert_called_once_with("%s %s", "fixture-anomaly", json.dumps(event, **options))

    monkeypatch.setattr(bot, "X_CREATE_RESPONSE_ANOMALY_BODY_MAX_BYTES", 1)
    monkeypatch.setattr(bot, "_X_CREATE_RESPONSE_ID_VALUE_MAX_CHARACTERS", 1)
    monkeypatch.setattr(bot, "_X_CREATE_RESPONSE_TOP_LEVEL_KEY_LIMIT", 1)
    event = bot.emit_x_create_response_anomaly(**diagnostic_arguments)
    assert event["raw_body_complete"] is False and event["raw_body_base64"] is None
    assert event["raw_body_sha256"] == hashlib.sha256(b"\x00\xff").hexdigest()
    assert event["data_id_value_complete"] is False and event["data_id_value"] is None
    assert event["decoded_top_level_keys_complete"] is False
    assert event["decoded_top_level_keys"] is None


def test_emitter_native_json_and_log_failures_keep_final_side_effect_order(monkeypatch, diagnostic_arguments):
    calls = Mock()
    calls.attach_mock(Mock(wraps=json.dumps), "dumps")
    calls.attach_mock(Mock(wraps=hashlib.sha256), "sha256")
    monkeypatch.setattr(bot, "json", SimpleNamespace(dumps=calls.dumps))
    monkeypatch.setattr(bot, "hashlib", SimpleNamespace(sha256=calls.sha256))
    monkeypatch.setattr(bot, "log", SimpleNamespace(error=calls.error))
    monkeypatch.setattr(bot, "now_epoch", lambda: float("nan"))
    with pytest.raises(ValueError, match="Out of range float"):
        bot.emit_x_create_response_anomaly(**diagnostic_arguments)
    assert [entry[0] for entry in calls.mock_calls] == ["sha256", "dumps"]
    calls.error.assert_not_called()

    monkeypatch.setattr(bot, "now_epoch", lambda: 1234567890)
    failure = RuntimeError("final JSON serialization")
    calls.reset_mock()
    calls.dumps.side_effect = ["{}", failure]
    with pytest.raises(RuntimeError) as caught:
        bot.emit_x_create_response_anomaly(**diagnostic_arguments)
    assert caught.value is failure
    assert [entry[0] for entry in calls.mock_calls] == ["sha256", "dumps", "sha256", "dumps"]
    calls.error.assert_not_called()

    calls.reset_mock()
    calls.dumps.side_effect = None
    calls.error.side_effect = failure
    with pytest.raises(RuntimeError) as caught:
        bot.emit_x_create_response_anomaly(**diagnostic_arguments)
    assert caught.value is failure
    assert [entry[0] for entry in calls.mock_calls] == ["sha256", "dumps", "sha256", "dumps", "error"]
    calls.error.assert_called_once()
