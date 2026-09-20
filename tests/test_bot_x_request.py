"""Contracts for the current-root X request extraction boundary."""

from __future__ import annotations

import inspect
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrs_bot_x_request as owner
import x_api_error_semantics as error_semantics
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401
from tests.helpers.x_response_fixtures import (
    PRODUCTION_DELETED_REPLY_ERROR,
    _armed_x_create_authority,
    _x_response,
)


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, io, logging, os, random, socket, sys, time, typing
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('X request import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply'} or name.startswith('mrs_bot_') and name not in {'mrs_bot_x_request', 'mrs_bot_x_response_diagnostics', 'mrs_bot_post_creation'}:
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = os.lstat = os.stat = forbidden
os.getenv = os._Environ.__getitem__ = os.urandom = forbidden
Path.home = logging.getLogger = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
time.time = time.monotonic = forbidden
before = random.getstate()
random.Random = random.seed = random.random = forbidden
import mrs_bot_x_request
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


@pytest.mark.parametrize("name, dependencies", [
    ("x_request", (
        "AUTH AmbiguousRemotePostOutcome ApiError "
        "DeterministicReplyCreateRejectionProof MEDIA_UPLOAD_RECEIPT_FILE "
        "MediaUploadAuthority MediaUploadReceiptError Path ProvedRemotePostNonSuccess "
        "ReceiptBoundMediaPayload TransportAuthority TransportJournalError "
        "ValidatedXErrorResponse XErrorResponseValidationError "
        "_activate_coordinator_reply_create_rejection_proof "
        "_bind_transport_authority_to_configured_x_request "
        "block_if_unrelated_receipt_appeared_for_media_transport "
        "block_if_unrelated_receipt_appeared_for_tweet_transport "
        "canonical_transport_receipt_path_for_lane consume_media_upload_authority "
        "emit_x_create_response_anomaly exact_x_create_route frozen_strict_json_object "
        "invalidate_reply_create_rejection_proof json log log_json_debug "
        "parse_validated_x_error_response "
        "perform_consumed_x_request prepared_x_create_route print_rate_limit_headers "
        "report_bot_health_progress request_timeout requests require_remote_operation_unpaused "
        "sys x_create_response_anomaly_reason "
        "x_request_base_url"
    ).split()),
    ("x_bearer_request", (
        "AmbiguousRemotePostOutcome ApiError X_BASE X_BEARER_TOKEN json log "
        "log_json_debug print_rate_limit_headers report_bot_health_progress "
        "request_timeout requests"
    ).split()),
])
def test_adapters_keep_signatures_current_dependencies_and_collected_kwargs(
    monkeypatch, name, dependencies,
):
    adapter = getattr(bot, name)
    signature = inspect.signature(adapter)
    owner_signature = inspect.signature(getattr(owner, name))
    parameters = owner_signature.parameters
    assert tuple(key for key in parameters if key not in signature.parameters) == tuple(dependencies)
    assert parameters["kwargs"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["kwargs"].default is inspect.Parameter.empty
    assert parameters["kwargs"].annotation is inspect.Parameter.empty
    assert signature == owner_signature.replace(parameters=[
        p.replace(kind=inspect.Parameter.VAR_KEYWORD) if key == "kwargs" else p
        for key, p in parameters.items() if key not in dependencies
    ])
    assert all(parameters[key].kind is inspect.Parameter.KEYWORD_ONLY
               and parameters[key].default is inspect.Parameter.empty for key in dependencies)
    defaults = {} if name == "x_bearer_request" else {
        "ambiguous_write": False, "_remote_write_authorization": None,
        "_remote_media_payload": None, "_remote_media_payload_metadata": None,
    }
    assert tuple(signature.parameters) == ("method", "path", *defaults, "kwargs")
    assert {key: p.default for key, p in signature.parameters.items()
            if p.kind is inspect.Parameter.KEYWORD_ONLY} == defaults
    args = (object(), object())
    options = {key: object() for key in defaults}
    request_options = {key: object() for key in ("requests", "AUTH", "kwargs", "arbitrary")}
    request_options["json"] = {"nested": []}
    for _ in range(2):
        with monkeypatch.context() as patch:
            current = {key: object() for key in dependencies}
            result = {"original": []}

            def capture(*received, **supplied):
                collected = inspect.currentframe().f_back.f_locals["kwargs"]
                assert supplied["kwargs"] is collected
                assert collected is not request_options
                assert set(collected) == set(request_options)
                assert all(collected[key] is value for key, value in request_options.items())
                assert all(supplied[key] is value for key, value in {**options, **current}.items())
                assert len(supplied) == len(options) + len(current) + 1
                assert all(a is b for a, b in zip(received, args))
                return result

            patch.setattr(bot, "_x_request", SimpleNamespace(**{name: capture}))
            for key, value in current.items():
                patch.setattr(bot, key, value)
            assert adapter(*args, **options, **request_options) is result
            failure = TypeError("current owner failure")
            patch.setattr(bot, "_x_request", SimpleNamespace(**{name: Mock(side_effect=failure)}))
            with pytest.raises(TypeError) as caught:
                adapter(*args, **options, **request_options)
            assert caught.value is failure


@pytest.mark.parametrize("name", ["x_request", "x_bearer_request"])
def test_read_request_logging_health_transport_and_response_references(monkeypatch, name):
    trace = Mock()
    result = {"data": []}
    trace.decode.return_value = result
    response = SimpleNamespace(status_code=200, text="body", headers={}, json=trace.decode)
    trace.request.return_value = response
    trace.base.return_value = "http://current.invalid"
    trace.prepared.return_value = trace.exact.return_value = None
    token, auth, budget = "current-token", object(), object()
    trace.timeout.return_value = budget
    for key, value in {
        "x_request_base_url": trace.base, "prepared_x_create_route": trace.prepared,
        "exact_x_create_route": trace.exact, "log": trace.log, "log_json_debug": trace.json_log,
        "report_bot_health_progress": trace.health, "request_timeout": trace.timeout,
        "requests": SimpleNamespace(request=trace.request, RequestException=bot.requests.RequestException),
        "AUTH": auth, "X_BASE": "http://current.invalid", "X_BEARER_TOKEN": token,
    }.items():
        monkeypatch.setattr(bot, key, value)
    options = {"params": {}, "json": {"nested": []}, "data": {}, "files": {"part": object()},
               "requests": object(), "AUTH": object(), "kwargs": object()}
    assert getattr(bot, name)("GET", "/2/read", **options) is result
    bearer = name == "x_bearer_request"
    prefix = "X bearer" if bearer else "X"
    expected = [] if bearer else [call.base("GET", "/2/read"), call.prepared("GET", "/2/read"), call.exact("GET", "/2/read")]
    expected += [call.log.debug(f"{prefix} request: %s %s", "GET", "http://current.invalid/2/read"),
                 call.json_log(f"{prefix} request params", options["params"])]
    if not bearer:
        expected += [call.json_log("X request json", options["json"]),
                     call.json_log("X request form data", options["data"]),
                     call.log.debug("X request includes files: %s", ["part"])]
    authentication = {"headers": {"Authorization": "Bearer current-token"}} if bearer else {"auth": auth}
    expected += [call.health("x_read"), call.timeout(),
                 call.request("GET", "http://current.invalid/2/read", **authentication, timeout=budget, **options),
                 call.health("x_read"), call.log.debug(f"{prefix} response status: %s", 200),
                 call.log.debug(f"{prefix} response headers: x-rate-limit-limit=%s remaining=%s reset=%s", None, None, None),
                 call.decode(), call.json_log(f"{prefix} response json", result)]
    assert trace.mock_calls == expected
    assert all(trace.request.call_args.kwargs[key] is value for key, value in options.items())
    assert trace.json_log.call_args.args[1] is result


@pytest.mark.parametrize("name, key", [
    ("x_request", "auth"), ("x_request", "timeout"),
    ("x_bearer_request", "headers"), ("x_bearer_request", "timeout"),
])
def test_duplicate_transport_keywords_keep_native_error_and_health_finally(monkeypatch, name, key):
    request, health, timeout = Mock(), Mock(), Mock()
    monkeypatch.setattr(bot.requests, "request", request)
    monkeypatch.setattr(bot, "report_bot_health_progress", health)
    monkeypatch.setattr(bot, "request_timeout", timeout)
    with pytest.raises(TypeError, match="multiple values"):
        getattr(bot, name)("GET", "/2/read", **{key: object()})
    request.assert_not_called()
    timeout.assert_called_once_with()
    assert health.call_args_list == [call("x_read")] * (2 if name == "x_bearer_request" else 1)


@pytest.mark.parametrize("name", ["x_request", "x_bearer_request"])
@pytest.mark.parametrize("key", ["requests", "AUTH", "kwargs"])
def test_dependency_named_options_reach_native_requests_validation(monkeypatch, name, key):
    health = Mock()
    monkeypatch.setattr(bot, "report_bot_health_progress", health)
    # Session.request rejects these keys at argument binding, before transport.
    with pytest.raises(TypeError, match=f"unexpected keyword argument '{key}'"):
        getattr(bot, name)("GET", "/2/read", **{key: object()})
    assert health.call_args_list == [call("x_read")] * (2 if name == "x_bearer_request" else 1)


@pytest.mark.parametrize("name", ["x_request", "x_bearer_request"])
@pytest.mark.parametrize("boundary", ["timeout", "transport", "journal", "health_before", "health_after"])
def test_read_exception_precedence_and_health_boundaries(monkeypatch, name, boundary):
    class JournalRequestError(bot.TransportJournalError, bot.requests.RequestException):
        pass

    failure = (TypeError("timeout") if boundary == "timeout" else
               JournalRequestError("both exception types") if boundary == "journal" else
               bot.requests.RequestException(boundary))
    request = Mock(return_value=_x_response(204, {}))
    timeout = Mock(return_value=object())
    health = Mock()
    if boundary == "timeout":
        timeout.side_effect = failure
    elif boundary in {"transport", "journal"}:
        request.side_effect = failure
    else:
        health.side_effect = [failure] if boundary == "health_before" else [None, failure]
    monkeypatch.setattr(bot.requests, "request", request)
    monkeypatch.setattr(bot, "request_timeout", timeout)
    monkeypatch.setattr(bot, "report_bot_health_progress", health)
    with pytest.raises(Exception) as caught:
        getattr(bot, name)("GET", "/2/read")
    if boundary in {"transport", "journal"}:
        expected = bot.AmbiguousRemotePostOutcome if boundary == "journal" and name == "x_request" else bot.ApiError
        assert type(caught.value) is expected
        assert caught.value.__cause__ is failure
        assert (caught.value.service, caught.value.request_method, caught.value.request_path) == ("x", "GET", "/2/read")
    else:
        assert caught.value is failure
    assert request.call_count == int(boundary not in {"timeout", "health_before"})
    assert timeout.call_count == int(boundary != "health_before")
    count = 1 if boundary == "health_before" else 2 if boundary == "health_after" or name == "x_bearer_request" else 1
    assert health.call_args_list == [call("x_read")] * count


def test_bearer_write_gate_precedes_token_and_redirect_threshold_stays_distinct(monkeypatch):
    monkeypatch.setattr(bot, "X_BEARER_TOKEN", "")
    request = Mock()
    monkeypatch.setattr(bot.requests, "request", request)
    with pytest.raises(bot.AmbiguousRemotePostOutcome, match="Bearer-authenticated X writes"):
        bot.x_bearer_request("POST", "/2/tweets")
    with pytest.raises(bot.ApiError, match="X_BEARER_TOKEN is not set"):
        bot.x_bearer_request("GET", "/2/read")
    request.assert_not_called()
    monkeypatch.setattr(bot, "X_BEARER_TOKEN", "current-token")
    result = {"data": []}
    request.return_value = SimpleNamespace(status_code=302, text="{}", content=b"{}", headers={}, json=lambda: result)
    assert bot.x_bearer_request("GET", "/2/read") is result
    with pytest.raises(bot.ApiError) as caught:
        bot.x_request("GET", "/2/read")
    assert type(caught.value) is bot.ApiError and caught.value.status_code == 302


def _observe_owner_kwargs(monkeypatch):
    observed = []
    execute = owner.x_request

    def capture(*args, **supplied):
        assert supplied["kwargs"] is inspect.currentframe().f_back.f_locals["kwargs"]
        observed.append(supplied["kwargs"])
        return execute(*args, **supplied)

    monkeypatch.setattr(bot, "_x_request", SimpleNamespace(x_request=capture))
    return observed


@pytest.mark.parametrize("stop", [None, "pause", "journal", "native"])
def test_tweet_freeze_pause_binding_and_coordinator_dictionary_order(monkeypatch, stop):
    trace = Mock()
    observed = _observe_owner_kwargs(monkeypatch)
    original = {"text": "original", "reply": {"in_reply_to_tweet_id": "7"}}
    frozen = {"text": "frozen", "reply": {"in_reply_to_tweet_id": "7"}}
    authority = SimpleNamespace(lane="quote_image", journal_path=object())
    result = {"data": {"id": "123"}}
    trace.decode.return_value = result
    trace.base.return_value = "http://current.invalid"
    trace.prepared.return_value = trace.exact.return_value = "tweet"
    trace.freeze.return_value = frozen
    trace.perform.return_value = (SimpleNamespace(status_code=201, headers={}, text="body", content=b"exact body", json=trace.decode), None, None)
    trace.reason.return_value = None
    trace.exc_info.return_value = (None, None, None)
    for key, value in {
        "TransportAuthority": SimpleNamespace, "x_request_base_url": trace.base,
        "prepared_x_create_route": trace.prepared, "exact_x_create_route": trace.exact,
        "frozen_strict_json_object": trace.freeze, "log": trace.log, "log_json_debug": trace.json_log,
        "require_remote_operation_unpaused": trace.pause,
        "canonical_transport_receipt_path_for_lane": trace.canonical,
        "block_if_unrelated_receipt_appeared_for_tweet_transport": trace.block,
        "Path": trace.path, "_bind_transport_authority_to_configured_x_request": trace.bind,
        "perform_consumed_x_request": trace.perform, "x_create_response_anomaly_reason": trace.reason,
        "sys": SimpleNamespace(exc_info=trace.exc_info),
    }.items():
        monkeypatch.setattr(bot, key, value)

    failure = bot.TransportJournalError("canonical failure") if stop == "journal" else ValueError("boundary failure")

    def pause(*args, **kwargs):
        assert observed[0] == {"json": frozen}
        assert observed[0]["json"] is frozen
        if stop == "pause":
            raise failure

    trace.pause.side_effect = pause
    if stop in {"journal", "native"}:
        trace.canonical.side_effect = failure
    options = {"json": original}
    if stop is None:
        assert bot.x_request("POST", "/2/tweets", ambiguous_write=True,
                             _remote_write_authorization=authority, **options) is result
    else:
        with pytest.raises(Exception) as caught:
            bot.x_request("POST", "/2/tweets", ambiguous_write=True,
                          _remote_write_authorization=authority, **options)
        if stop == "journal":
            assert type(caught.value) is bot.AmbiguousRemotePostOutcome
            assert caught.value.__cause__ is failure
        else:
            assert caught.value is failure
    expected = [call.base("POST", "/2/tweets"), call.prepared("POST", "/2/tweets"), call.exact("POST", "/2/tweets"),
                call.freeze(original, label="X post creation payload"),
                call.log.debug("X request: %s %s", "POST", "http://current.invalid/2/tweets"),
                call.json_log("X request json", frozen),
                call.pause("X POST /2/tweets", transaction_authorization=authority)]
    if stop != "pause":
        expected.append(call.canonical("quote_image"))
        assert observed[0]["allow_redirects"] is False
    if stop is None:
        expected += [call.block(trace.canonical.return_value), call.path(authority.journal_path),
                     call.bind(authority, payload=frozen),
                     call.perform(trace.path.return_value, authority, request_authority=trace.bind.return_value,
                                  payload=frozen, expected_receipt_path=trace.canonical.return_value, request_kwargs=observed[0]),
                     call.log.debug("X response status: %s", 201),
                     call.log.debug("X response headers: x-rate-limit-limit=%s remaining=%s reset=%s", None, None, None),
                     call.decode(), call.reason(result), call.json_log("X response json", result), call.exc_info()]
        assert trace.perform.call_args.kwargs["request_kwargs"] is observed[0]
        assert trace.perform.call_args.kwargs["payload"] is trace.bind.call_args.kwargs["payload"] is frozen
    assert trace.mock_calls == expected
    assert options == {"json": original} and options["json"] is original
    assert original["text"] == "original"


@pytest.mark.parametrize("explicit_metadata", [False, True])
def test_media_metadata_references_and_consumption_precede_transport(monkeypatch, explicit_metadata):
    class MediaAuthority(SimpleNamespace):
        pass

    class MediaPayload(SimpleNamespace):
        pass

    trace = Mock()
    observed = _observe_owner_kwargs(monkeypatch)
    authority = MediaAuthority(lane="daily_meme")
    payload = MediaPayload(basename="image.png", data=b"exact bytes", mime_type="image/png")
    form, supplied_metadata, receipt_path, metadata, auth = {}, {}, object(), {}, object()
    files = {"media": (payload.basename, payload.data, payload.mime_type)}
    trace.base.return_value = "http://media.invalid"
    trace.prepared.return_value = trace.exact.return_value = "media"
    trace.derive.return_value = trace.validate.return_value = metadata
    trace.request.return_value = SimpleNamespace(status_code=204, text="", headers={})
    trace.exc_info.return_value = (None, None, None)
    for key, value in {
        "MediaUploadAuthority": MediaAuthority, "ReceiptBoundMediaPayload": MediaPayload,
        "x_request_base_url": trace.base, "prepared_x_create_route": trace.prepared,
        "exact_x_create_route": trace.exact, "log": trace.log, "log_json_debug": trace.json_log,
        "require_remote_operation_unpaused": trace.pause, "media_upload_payload_metadata": trace.derive,
        "validate_media_upload_payload_metadata": trace.validate,
        "block_if_unrelated_receipt_appeared_for_media_transport": trace.block,
        "consume_media_upload_authority": trace.consume, "MEDIA_UPLOAD_RECEIPT_FILE": receipt_path,
        "AUTH": auth, "request_timeout": trace.timeout, "report_bot_health_progress": trace.health,
        "requests": SimpleNamespace(request=trace.request, RequestException=bot.requests.RequestException),
        "sys": SimpleNamespace(exc_info=trace.exc_info),
    }.items():
        target = owner if key in {"media_upload_payload_metadata", "validate_media_upload_payload_metadata"} else bot
        monkeypatch.setattr(target, key, value)

    def pause(*args, **kwargs):
        assert set(observed[0]) == {"data", "files"}

    trace.pause.side_effect = pause
    assert bot.x_request("POST", "/2/media/upload", ambiguous_write=True,
                         _remote_write_authorization=authority, _remote_media_payload=payload,
                         _remote_media_payload_metadata=supplied_metadata if explicit_metadata else None,
                         data=form, files=files) == {}
    metadata_call = call.validate(supplied_metadata, form=form) if explicit_metadata else call.derive(form)
    assert trace.mock_calls == [
        call.base("POST", "/2/media/upload"), call.prepared("POST", "/2/media/upload"), call.exact("POST", "/2/media/upload"),
        call.log.debug("X request: %s %s", "POST", "http://media.invalid/2/media/upload"),
        call.json_log("X request form data", form), call.log.debug("X request includes files: %s", ["media"]),
        call.pause("X POST /2/media/upload", transaction_authorization=authority), metadata_call, call.block(),
        call.consume(receipt_path, authority, payload=payload, lane="daily_meme", mime_type="image/png", payload_metadata=metadata),
        call.timeout(), call.request("POST", "http://media.invalid/2/media/upload", auth=auth,
                                     timeout=trace.timeout.return_value, data=form, files=files, allow_redirects=False),
        call.log.debug("X response status: %s", 204),
        call.log.debug("X response headers: x-rate-limit-limit=%s remaining=%s reset=%s", None, None, None),
        call.log.debug("X response has empty body"), call.exc_info(),
    ]
    assert trace.consume.call_args.args[0] is receipt_path
    assert trace.consume.call_args.args[1] is authority
    assert trace.consume.call_args.kwargs["payload"] is payload
    assert trace.consume.call_args.kwargs["payload_metadata"] is metadata
    assert observed[0]["data"] is trace.request.call_args.kwargs["data"] is form
    assert observed[0]["files"] is trace.request.call_args.kwargs["files"] is files
    if explicit_metadata:
        assert trace.validate.call_args.args[0] is supplied_metadata
        assert trace.validate.call_args.kwargs["form"] is form
    else:
        assert trace.derive.call_args.args[0] is form


@pytest.mark.parametrize("escaping", ["actual", "different_proof", "probe_interruption"])
def test_actual_issued_proof_finally_uses_current_sys_and_exact_identity(monkeypatch, escaping):
    payload = {"text": "reply", "reply": {"in_reply_to_tweet_id": "100"}}
    authority = _armed_x_create_authority(payload)
    monkeypatch.setattr(bot.requests, "request", lambda *args, **kwargs: _x_response(403, PRODUCTION_DELETED_REPLY_ERROR))
    observed = []

    class InterruptedProofAccess(bot.ProvedRemotePostNonSuccess):
        def __getattribute__(self, key):
            if key == "remote_non_success_proof":
                raise KeyboardInterrupt("preservation probe interrupted")
            return super().__getattribute__(key)

    def current_exc_info():
        actual = sys.exc_info()[1]
        observed.append(actual)
        if escaping == "actual":
            selected = actual
        else:
            error_type = InterruptedProofAccess if escaping == "probe_interruption" else bot.ProvedRemotePostNonSuccess
            selected = error_type("other escaping observation", service="x",
                                  remote_non_success_proof=actual.remote_non_success_proof)
            if escaping == "different_proof":
                selected.remote_non_success_proof = object()
        return type(selected), selected, None

    monkeypatch.setattr(bot, "sys", SimpleNamespace(exc_info=current_exc_info))
    with pytest.raises(bot.ProvedRemotePostNonSuccess) as caught:
        bot.x_request("POST", "/2/tweets", json=payload, ambiguous_write=True,
                      _remote_write_authorization=authority)
    proof = caught.value.remote_non_success_proof
    assert observed == [caught.value] and observed[0] is caught.value
    assert (id(proof) in error_semantics._issued_rejection_proofs) is (escaping == "actual")
    assert Path(authority.journal_path).exists() and Path(authority.fence_path).exists()
    bot.invalidate_reply_create_rejection_proof(proof)
