"""Exercise bounded Responses transport and its current runtime boundaries."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import inspect
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrs_bot_reply_model_transport as model_transport
from tests.helpers.single_call_fixtures import FakeHttpResponse, raw_decision, response_envelope
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401


OWNER_INPUTS = {
    "log": "log", "model": "SINGLE_CALL_MODEL", "reasoning_effort": "SINGLE_CALL_REASONING_EFFORT",
    "monotonic": "monotonic", "require_remote_operation_unpaused": "require_remote_operation_unpaused",
    "report_bot_health_progress": "report_bot_health_progress", "requests": "requests",
    "base_url": "OPENAI_BASE", "api_key": "OPENAI_API_KEY", "sleep": "sleep",
    "now_epoch": "now_epoch", "parsedate_to_datetime": "parsedate_to_datetime", "error_type": "ApiError",
}


@pytest.fixture
def make_owner():
    def build(**overrides):
        current = {field: getattr(bot, name) for field, name in OWNER_INPUTS.items()}
        return model_transport.ReplyModelTransport(**{**current, **overrides})
    return build


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, dataclasses, io, logging, math, os, random, re, socket, sys, time
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('reply model transport import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'reply_evidence'} or name.startswith('mrs_bot_') and name != 'mrs_bot_reply_model_transport':
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
import mrs_bot_reply_model_transport
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


def test_owner_composition_binds_current_dependencies_without_calls_or_secret_repr(monkeypatch):
    snapshots = []
    for _ in range(2):
        current = {field: Mock() for field in OWNER_INPUTS}
        for field, name in OWNER_INPUTS.items():
            monkeypatch.setattr(bot, name, current[field])
        owner = bot._reply_model_transport_owner()
        assert isinstance(owner, model_transport.ReplyModelTransport)
        for field, value in current.items():
            assert getattr(owner, field) is value
            value.assert_not_called()
        snapshots.append((owner, current))
    first, inputs = snapshots[0]
    assert first is not snapshots[1][0]
    assert all(getattr(first, field) is value for field, value in inputs.items())
    with pytest.raises(FrozenInstanceError):
        first.model = "another model"
    redacted = repr(replace(first, api_key="fixture-transport-secret"))
    assert "fixture-transport-secret" not in redacted and "api_key=" not in redacted


def test_root_adapters_preserve_signatures_defaults_references_and_errors(monkeypatch):
    for root_name, method_name in (
        ("_definite_connection_failure_before_transmission", "definite_connection_failure_before_transmission"),
        ("_openai_api_error", "error"), ("_openai_retry_metadata", "retry_metadata"),
        ("openai_responses_reply_call", "call"),
    ):
        adapter = getattr(bot, root_name)
        public = inspect.signature(adapter)
        owned = inspect.signature(getattr(model_transport.ReplyModelTransport, method_name))
        assert [(p.name, p.kind, p.default, p.annotation) for p in public.parameters.values()] == [
            (p.name, p.kind, p.default, p.annotation) for p in list(owned.parameters.values())[1:]
        ]
        assert public.return_annotation == owned.return_annotation
        args = tuple(object() for p in public.parameters.values() if p.kind == p.POSITIONAL_OR_KEYWORD)
        options = {key: object() for key, p in public.parameters.items() if p.kind == p.KEYWORD_ONLY}
        with monkeypatch.context() as patch:
            for include_defaults in (False, True):
                owner = Mock(spec=model_transport.ReplyModelTransport)
                factory = Mock(return_value=owner)
                patch.setattr(bot, "_reply_model_transport_owner", factory)
                implementation = getattr(owner, method_name)
                result = object()
                implementation.return_value = result
                supplied = {key: value for key, value in options.items()
                            if include_defaults or public.parameters[key].default is inspect.Parameter.empty}
                bound = public.bind(*args, **supplied)
                bound.apply_defaults()
                expected = {key: value for key, value in bound.arguments.items()
                            if public.parameters[key].kind == inspect.Parameter.KEYWORD_ONLY}
                assert adapter(*args, **supplied) is result
                factory.assert_called_once_with()
                actual_args, actual_kwargs = implementation.call_args
                assert len(actual_args) == len(args)
                assert all(actual is original for actual, original in zip(actual_args, args))
                assert actual_kwargs.keys() == expected.keys()
                assert all(actual_kwargs[key] is value for key, value in expected.items())
            failure = TypeError("current transport failure")
            implementation.side_effect = failure
            with pytest.raises(TypeError) as caught:
                adapter(*args, **supplied)
            assert caught.value is failure
    assert bot._definite_connection_failure_before_transmission.__annotations__["error"] == "requests.RequestException"
    assert bot._openai_retry_metadata.__annotations__["response"] == "requests.Response"
    assert bot._openai_api_error.__annotations__["return"] == "ApiError"


@pytest.mark.parametrize("malformed", [False, True])
def test_transport_keeps_request_reference_and_closes_retry_responses_in_order(make_owner, malformed):
    first = FakeHttpResponse(429, headers={"Retry-After": " 0.2 "})
    envelope = response_envelope(raw_decision())
    second = FakeHttpResponse(200, body=envelope)
    failure = TypeError("fixture malformed JSON")
    trace = Mock()
    trace.post.side_effect = [first, second]
    first.close = trace.first_close = Mock(wraps=first.close)
    second.close = trace.second_close = Mock(wraps=second.close)
    second.json = trace.json = Mock(side_effect=failure) if malformed else Mock(return_value=envelope)
    request = {"model": "fixture-model", "input": {"marker": "fixture"}}

    class CurrentApiError(bot.ApiError):
        def __init__(self, *args, **kwargs):
            assert first.closed and second.closed
            super().__init__(*args, **kwargs)

    owner = make_owner(
        error_type=CurrentApiError,
        requests=SimpleNamespace(post=trace.post, RequestException=bot.requests.RequestException),
        base_url="http://127.0.0.1:9/v1", api_key="dummy-stage11",
        require_remote_operation_unpaused=trace.pause, report_bot_health_progress=trace.health,
        sleep=trace.sleep, now_epoch=lambda: 2_000_000_000,
        monotonic=Mock(side_effect=[10.0, 10.0126]), log=Mock(),
    )
    options = dict(request=request, timeout_seconds=23, lane="quote_tweet", target_id="target")
    if malformed:
        with pytest.raises(CurrentApiError) as caught:
            owner.call(**options)
        assert caught.value.__cause__ is failure
        assert caught.value.error_category == "provider_envelope"
        assert caught.value.status_code == 429
        assert caught.value.reset_epoch == 2_000_000_001
        assert caught.value.retry_after_seconds == 1
        assert caught.value.request_attempt_count == 2
    else:
        result = owner.call(**options)
        assert result["response"] is envelope
        assert result == {
            "response": envelope, "latency_ms": 13, "request_attempt_count": 2,
            "provider_status_code": 429, "provider_reset_epoch": 2_000_000_001,
            "provider_retry_after_seconds": 1,
        }
    assert first.closed and second.closed
    assert [entry[0] for entry in trace.mock_calls] == [
        "pause", "health", "post", "health", "first_close", "sleep",
        "pause", "health", "post", "health", "json", "second_close",
    ]
    assert trace.pause.call_args_list == [call("OpenAI single-call reply target target")] * 2
    assert trace.health.call_args_list == [call("ai_call")] * 4
    trace.sleep.assert_called_once_with(1)
    for args, kwargs in trace.post.call_args_list:
        assert args == ("http://127.0.0.1:9/v1/responses",)
        assert kwargs["json"] is request
        assert kwargs == {
            "headers": {"Authorization": "Bearer dummy-stage11", "Content-Type": "application/json"},
            "json": request, "timeout": 23, "allow_redirects": False,
        }


@pytest.mark.parametrize("failure_site", ["decoder", "metadata_429", "metadata_503"])
def test_unexpected_response_read_failure_closes_without_mapping_or_retry(
    make_owner, failure_site,
):
    failure = RuntimeError("fixture unexpected response read failure")
    status_code = 200 if failure_site == "decoder" else int(failure_site.rsplit("_", 1)[1])
    response = FakeHttpResponse(status_code, headers={"Retry-After": "fixture-date"})
    response.close = Mock(wraps=response.close)
    if failure_site == "decoder":
        response.json = Mock(side_effect=failure)
    post = Mock(return_value=response)
    sleep = Mock()
    error_type = Mock()
    owner = make_owner(
        requests=SimpleNamespace(post=post, RequestException=bot.requests.RequestException),
        require_remote_operation_unpaused=Mock(), report_bot_health_progress=Mock(),
        parsedate_to_datetime=Mock(side_effect=failure), now_epoch=lambda: 2_000_000_000,
        sleep=sleep, error_type=error_type, log=Mock(),
    )

    with pytest.raises(RuntimeError) as caught:
        owner.call(request={}, timeout_seconds=23, lane="mention", target_id="target")
    assert caught.value is failure
    assert response.closed
    response.close.assert_called_once_with()
    post.assert_called_once()
    sleep.assert_not_called()
    error_type.assert_not_called()


@pytest.mark.parametrize("response_kind", ["first_429", "http_error", "json", "malformed_json"])
def test_response_close_failure_propagates_without_becoming_a_provider_error(
    make_owner, response_kind,
):
    status_code = {"first_429": 429, "http_error": 503}.get(response_kind, 200)
    response = FakeHttpResponse(status_code, headers={"Retry-After": "120"})
    json_failure = TypeError("fixture malformed JSON")
    if response_kind == "malformed_json":
        response.json = Mock(side_effect=json_failure)
    failure = ValueError("fixture response close failure")
    response.close = Mock(side_effect=failure)
    post = Mock(return_value=response)
    sleep = Mock()
    error_type = Mock()
    owner = make_owner(
        requests=SimpleNamespace(post=post, RequestException=bot.requests.RequestException),
        require_remote_operation_unpaused=Mock(), report_bot_health_progress=Mock(),
        now_epoch=lambda: 2_000_000_000, sleep=sleep, error_type=error_type, log=Mock(),
    )

    with pytest.raises(ValueError) as caught:
        owner.call(request={}, timeout_seconds=23, lane="mention", target_id="target")
    assert caught.value is failure
    if response_kind == "malformed_json":
        assert caught.value.__context__ is json_failure
    response.close.assert_called_once_with()
    post.assert_called_once()
    sleep.assert_not_called()
    error_type.assert_not_called()


def test_provider_error_factory_failure_keeps_decoder_exception_context(make_owner):
    response = FakeHttpResponse(200)
    json_failure = ValueError("fixture malformed JSON")
    response.json = Mock(side_effect=json_failure)
    response.close = Mock(wraps=response.close)
    failure = RuntimeError("fixture provider error factory failure")
    factory = Mock(side_effect=failure)
    owner = make_owner(
        requests=SimpleNamespace(post=Mock(return_value=response), RequestException=bot.requests.RequestException),
        require_remote_operation_unpaused=Mock(), report_bot_health_progress=Mock(),
        error_type=factory, log=Mock(),
    )

    with pytest.raises(RuntimeError) as caught:
        owner.call(request={}, timeout_seconds=23, lane="mention", target_id="target")
    assert caught.value is failure
    assert caught.value.__context__ is json_failure
    assert response.closed
    response.close.assert_called_once_with()
    factory.assert_called_once()


def test_connection_failure_classification_preserves_request_type_and_cause_chain(make_owner):
    owner = make_owner()
    assert owner.definite_connection_failure_before_transmission(bot.requests.ConnectTimeout("connect"))
    refused = ConnectionRefusedError("refused")
    connection = bot.requests.ConnectionError("connect")
    connection.__context__ = refused
    assert owner.definite_connection_failure_before_transmission(connection)
    timeout = bot.requests.ReadTimeout("read")
    timeout.__cause__ = refused
    assert not owner.definite_connection_failure_before_transmission(timeout)
    assert not owner.definite_connection_failure_before_transmission(bot.requests.ConnectionError("ambiguous"))


def test_error_creation_preserves_constructor_result_and_metadata_references(make_owner):
    result = SimpleNamespace()
    constructor = Mock(return_value=result)
    owner = make_owner(error_type=constructor)
    message, category, status, reset, delay, attempts = (object() for _ in range(6))
    assert owner.error(message, category=category, status_code=status, reset_epoch=reset,
                       retry_after_seconds=delay, request_attempt_count=attempts) is result
    args, kwargs = constructor.call_args
    assert args[0] is message
    assert kwargs == {"service": "openai", "status_code": status, "reset_epoch": reset}
    assert result.error_category is category
    assert result.retry_after_seconds is delay and result.request_attempt_count is attempts


def test_retry_metadata_uses_current_clock_parser_and_largest_bounded_delay(make_owner):
    clock = Mock(return_value=100)
    parsed = SimpleNamespace(tzinfo=object(), timestamp=lambda: 100.2)
    parser = Mock(return_value=parsed)
    owner = make_owner(now_epoch=clock, parsedate_to_datetime=parser)
    response = FakeHttpResponse(429, headers={"Retry-After": "fixture-date", "x-ratelimit-reset-tokens": "2m500ms"})
    assert owner.retry_metadata(response) == (221, 121)
    clock.assert_called_once_with()
    parser.assert_called_once_with("fixture-date")
    response.headers = {"Retry-After": "900000000"}
    assert owner.retry_metadata(response) == (604900, 604800)
    response.headers = {"Retry-After": "fixture-date"}
    parser.side_effect = ValueError("invalid date")
    assert owner.retry_metadata(response) == (None, None)
    failure = RuntimeError("current date parser failed")
    parser.side_effect = failure
    with pytest.raises(RuntimeError) as caught:
        owner.retry_metadata(response)
    assert caught.value is failure
