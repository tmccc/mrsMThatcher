from __future__ import annotations

import inspect
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrs_bot_reply_generation as generation
from tests.test_single_call_failure_routing import FakeHttpResponse
from tests.test_single_call_reply import (
    FakeRepository,
    context as pipeline_context,
    enabled_config,
    raw_decision,
    response_envelope,
)
from tests.test_unit_helpers import bot, isolate_regular_post_receipt


def test_import_needs_no_runtime_access_and_constants_are_shared_objects():
    code = """
import builtins, collections.abc, io, logging, math, os, random, socket, sys
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('reply generation import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'reply_evidence'} or name.startswith('mrs_bot_') and name != 'mrs_bot_reply_generation':
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = forbidden
os.getenv = os._Environ.__getitem__ = forbidden
Path.home = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
before = random.getstate()
random.Random = random.seed = random.random = forbidden
import mrs_bot_reply_generation
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
    for name, kind in (
        ("_REPLY_IMAGE_MIME_TYPES", set),
        ("_OPENAI_PROVIDER_HEALTH_FAILURE_CATEGORIES", frozenset),
        ("_TERMINAL_CANDIDATE_LOCAL_FAILURE_CATEGORIES", frozenset),
    ):
        assert type(getattr(generation, name)) is kind
        assert getattr(bot, name) is getattr(generation, name)
    assert generation._REPLY_IMAGE_MIME_TYPES == {
        "image/jpeg", "image/png", "image/webp", "image/gif",
    }
    for module in (bot, generation):
        assert module._definite_connection_failure_before_transmission.__annotations__["error"] == "requests.RequestException"
        assert module._openai_retry_metadata.__annotations__["response"] == "requests.Response"
        assert module._openai_api_error.__annotations__["return"] == "ApiError"
        assert module._record_single_call_result.__annotations__["result"] == "PipelineResult"


def test_adapters_forward_current_dependencies_arguments_results_and_errors(monkeypatch):
    names = (
        "log_ai_reply_posting_outcome", "_safe_reply_image_url", "collect_reply_images",
        "_definite_connection_failure_before_transmission", "_openai_api_error",
        "_openai_retry_metadata", "_is_openai_provider_health_failure",
        "_is_terminal_candidate_local_failure", "openai_responses_reply_call",
        "_record_single_call_result", "evaluate_single_call_reply",
    )
    for name in names:
        adapter = getattr(bot, name)
        public = inspect.signature(adapter).parameters
        dependencies = inspect.signature(getattr(generation, name)).parameters.keys() - public.keys()
        args = tuple(object() for parameter in public.values() if parameter.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD)
        options = {key: object() for key, parameter in public.items() if parameter.kind == inspect.Parameter.KEYWORD_ONLY}
        result = object()
        owner = Mock(return_value=result)
        with monkeypatch.context() as patch:
            patch.setattr(generation, name, owner)
            for _ in range(2):
                current = {key: object() for key in dependencies}
                for key, value in current.items():
                    patch.setattr(bot, key, value)
                assert adapter(*args, **options) is result, name
                actual_args, actual_kwargs = owner.call_args
                assert len(actual_args) == len(args)
                assert all(actual is expected for actual, expected in zip(actual_args, args)), name
                expected = {**options, **current}
                assert actual_kwargs.keys() == expected.keys(), name
                assert all(actual_kwargs[key] is value for key, value in expected.items()), name
            failure = TypeError(name)
            owner.side_effect = failure
            with pytest.raises(TypeError) as caught:
                adapter(*args, **options)
            assert caught.value is failure


@pytest.fixture
def image_case():
    response = FakeHttpResponse(200, headers={"Content-Type": "image/png"})
    media = bot.reply_media_context_for_candidate(
        {"id": "target", "_attached_media": [{
            "media_key": "native-photo", "type": "photo",
            "url": "http://127.0.0.1/media/native.png",
        }]},
        lane="mention", target_id="target",
    )
    return response, media


def test_image_collection_uses_current_requests_bounds_and_validation_reference(monkeypatch, image_case):
    response, media = image_case
    chunks = list(response.iter_content(chunk_size=64 * 1024))
    data = b"".join(chunks)
    response.iter_content = Mock(return_value=iter([None, b"", bytearray(b"ignored"), *chunks]))
    response.headers["Content-Length"] = str(len(data))
    trace = Mock()
    trace.get.return_value = response
    trace.timeout.return_value = 17
    response.close = trace.close = Mock(wraps=response.close)

    def validate(images):
        assert response.closed
        return images

    trace.validate.side_effect = validate
    monkeypatch.setattr(bot, "requests", SimpleNamespace(get=trace.get, RequestException=bot.requests.RequestException))
    monkeypatch.setattr(bot, "request_timeout", trace.timeout)
    monkeypatch.setattr(bot, "require_remote_operation_unpaused", trace.pause)
    monkeypatch.setattr(bot, "validate_supplied_images", trace.validate)
    monkeypatch.setattr(bot, "MAX_SUPPLIED_IMAGES", 1)
    monkeypatch.setattr(bot, "SINGLE_CALL_MAX_IMAGE_BYTES", len(data))
    monkeypatch.setattr(bot, "_REPLY_IMAGE_MIME_TYPES", {"image/png"})

    result = bot.collect_reply_images(media)
    assert result is trace.validate.call_args.args[0]
    assert result == [{
        "identity": "native-photo", "mime_type": "image/png", "data": data,
        "attachment_role": "target_contribution", "source_post_id": "target",
    }]
    assert [entry[0] for entry in trace.mock_calls] == ["pause", "timeout", "get", "close", "validate"]
    trace.pause.assert_called_once_with("candidate image collection 1/1")
    trace.get.assert_called_once_with(
        media["photos"][0]["url"], stream=True, allow_redirects=False, timeout=17,
        headers={"Accept": "image/jpeg,image/png,image/webp,image/gif"},
    )
    response.iter_content.assert_called_once_with(chunk_size=64 * 1024)


@pytest.mark.parametrize("failure_site", ["stream", "validation"])
def test_image_failure_closes_response_before_propagating_original_cause(monkeypatch, image_case, failure_site):
    response, media = image_case
    failure = bot.requests.Timeout("fixture stream failure") if failure_site == "stream" else ValueError("fixture validation failure")
    validate = Mock(side_effect=failure)
    if failure_site == "stream":
        response.iter_content = Mock(side_effect=failure)
    monkeypatch.setattr(bot, "requests", SimpleNamespace(get=Mock(return_value=response), RequestException=bot.requests.RequestException))
    monkeypatch.setattr(bot, "require_remote_operation_unpaused", Mock())
    monkeypatch.setattr(bot, "validate_supplied_images", validate)
    response.close = Mock(wraps=response.close)
    expected = bot.ReplyMediaTransientUnavailable if failure_site == "stream" else bot.ReplyMediaUnavailable
    with pytest.raises(expected) as caught:
        bot.collect_reply_images(media)
    assert type(caught.value) is expected
    assert caught.value.__cause__ is failure
    assert response.closed
    response.close.assert_called_once_with()
    assert validate.call_count == int(failure_site == "validation")


def test_url_validation_reads_current_policy_and_preserves_exception_cause(monkeypatch):
    class CurrentMediaError(bot.ReplyMediaUnavailable):
        pass

    monkeypatch.setattr(bot, "ReplyMediaUnavailable", CurrentMediaError)
    monkeypatch.setattr(bot, "TEST_MODE", False)
    assert bot._safe_reply_image_url(" https://pbs.twimg.com:443/media/photo.png ") == "https://pbs.twimg.com:443/media/photo.png"
    with pytest.raises(CurrentMediaError, match="trusted X media origin"):
        bot._safe_reply_image_url("http://127.0.0.1/media/photo.png")
    monkeypatch.setattr(bot, "TEST_MODE", True)
    assert bot._safe_reply_image_url("http://127.0.0.1/media/photo.png") == "http://127.0.0.1/media/photo.png"
    failure = ValueError("fixture invalid port")
    monkeypatch.setattr(bot, "urlsplit", Mock(side_effect=failure))
    with pytest.raises(CurrentMediaError, match="invalid port") as caught:
        bot._safe_reply_image_url("https://pbs.twimg.com:bad/media/photo.png")
    assert caught.value.__cause__ is failure


def test_failure_predicates_use_current_distinct_category_sets(monkeypatch):
    monkeypatch.setattr(bot, "_OPENAI_PROVIDER_HEALTH_FAILURE_CATEGORIES", frozenset({"current_health"}))
    monkeypatch.setattr(bot, "_TERMINAL_CANDIDATE_LOCAL_FAILURE_CATEGORIES", frozenset({"current_local"}))
    assert bot._is_openai_provider_health_failure("current_health")
    assert not bot._is_openai_provider_health_failure("provider_transport")
    assert not bot._is_openai_provider_health_failure("current_local")
    assert bot._is_openai_provider_health_failure("provider_http_503")
    assert not bot._is_openai_provider_health_failure("provider_http_5030")
    assert bot._is_terminal_candidate_local_failure({"status": "operational_failure", "error_category": "current_local"})
    assert not bot._is_terminal_candidate_local_failure({"status": "operational_failure", "error_category": "current_health"})


@pytest.mark.parametrize("malformed", [False, True])
def test_transport_keeps_request_reference_and_closes_retry_responses_in_order(monkeypatch, malformed):
    first = FakeHttpResponse(429, headers={"Retry-After": " 1.2 "})
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
        pass

    monkeypatch.setattr(bot, "ApiError", CurrentApiError)
    monkeypatch.setattr(bot, "requests", SimpleNamespace(post=trace.post, RequestException=bot.requests.RequestException))
    monkeypatch.setattr(bot, "OPENAI_BASE", "http://127.0.0.1:9/v1")
    monkeypatch.setattr(bot, "OPENAI_API_KEY", "dummy-stage11")
    monkeypatch.setattr(bot, "require_remote_operation_unpaused", trace.pause)
    monkeypatch.setattr(bot, "report_bot_health_progress", trace.health)
    monkeypatch.setattr(bot, "sleep", trace.sleep)
    monkeypatch.setattr(bot, "now_epoch", lambda: 2_000_000_000)
    monkeypatch.setattr(bot, "monotonic", Mock(side_effect=[10.0, 10.0126]))
    monkeypatch.setattr(bot, "log", Mock())
    options = dict(request=request, timeout_seconds=23, lane="quote_tweet", target_id="target")
    if malformed:
        with pytest.raises(CurrentApiError) as caught:
            bot.openai_responses_reply_call(**options)
        assert caught.value.__cause__ is failure
        assert caught.value.error_category == "provider_envelope"
        assert caught.value.status_code == 429
        assert caught.value.reset_epoch == 2_000_000_002
        assert caught.value.retry_after_seconds == 2
        assert caught.value.request_attempt_count == 2
    else:
        result = bot.openai_responses_reply_call(**options)
        assert result["response"] is envelope
        assert result == {
            "response": envelope, "latency_ms": 13, "request_attempt_count": 2,
            "provider_status_code": 429, "provider_reset_epoch": 2_000_000_002,
            "provider_retry_after_seconds": 2,
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


def test_generation_preserves_order_and_references_through_the_current_pipeline(monkeypatch):
    context = pipeline_context(turns=1)
    state, outcome, media = {}, {"retained": "value"}, {}
    images, excluded = [], {"target"}
    rows = [{"incoming_contribution": " Earlier contribution. ", "proposed_reply": " Earlier response. ", "reply_post_id": "prior"}]
    recent = [{"post_id": "other", "text": "Another earlier response."}]
    repository, config = FakeRepository(), enabled_config()
    trace = Mock()
    trace.images.return_value = images
    trace.epoch.return_value = 2_000_000_000
    trace.exclusions.return_value = excluded
    trace.same_author.return_value = rows
    trace.recent.return_value = recent
    trace.repository.return_value = repository
    trace.transport.return_value = {"response": response_envelope(raw_decision())}
    trace.pipeline = Mock(wraps=bot.run_single_call_reply_pipeline)
    original_record = bot._record_single_call_result
    before_recording = []

    def record(result, **kwargs):
        before_recording.append(dict(outcome))
        return original_record(result, **kwargs)

    trace.record.side_effect = record
    for root_name, callback in {
        "collect_reply_images": trace.images,
        "_reply_target_epoch": trace.epoch,
        "_reply_context_history_excluded_post_ids": trace.exclusions,
        "_same_author_confirmed_history_rows": trace.same_author,
        "recent_confirmed_account_replies": trace.recent,
        "require_remote_operation_unpaused": trace.pause,
        "reply_evidence_repository": trace.repository,
        "openai_responses_reply_call": trace.transport,
        "run_single_call_reply_pipeline": trace.pipeline,
        "_record_single_call_result": trace.record,
        "log_event": trace.event,
        "record_api_error": trace.error,
    }.items():
        monkeypatch.setattr(bot, root_name, callback)
    monkeypatch.setattr(bot, "single_call_reply", config)
    monkeypatch.setattr(bot, "log", SimpleNamespace(info=trace.info, warning=trace.warning))

    reply = bot.generate_single_call_reply(context, media, state=state, evaluation_outcome=outcome)
    result = trace.record.call_args.args[0]
    assert isinstance(reply, bot.ValidatedReply) and reply is result.reply
    assert before_recording == [{"retained": "value"}]
    assert outcome == {
        "retained": "value", "status": "reply", "reason": "useful_reply",
        "reason_code": "useful_reply", "reply_kind": "principle",
        "error_category": None, "model_call_count": 1,
    }
    assert [entry[0] for entry in trace.mock_calls] == [
        "images", "epoch", "exclusions", "same_author", "recent", "pause",
        "repository", "pipeline", "transport", "record", "event", "info", "event",
    ]
    assert trace.images.call_args.args[0] is media
    assert trace.epoch.call_args.args[0] is context
    assert trace.exclusions.call_args.args[0] is context
    for history in (trace.same_author, trace.recent):
        assert history.call_args.args[0] is state
        assert history.call_args.kwargs["before_epoch"] == 2_000_000_000
    assert trace.same_author.call_args.kwargs["current_thread_post_ids"] is excluded
    assert trace.recent.call_args.kwargs["excluded_post_ids"] is excluded
    assert trace.recent.call_args.kwargs["excluded_reply_post_ids"] == {"prior"}
    supplied = trace.pipeline.call_args.kwargs
    for name, value in {"context": context, "config": config, "repository": repository, "transport": trace.transport, "recent_account_replies": recent, "supplied_images": images}.items():
        assert supplied[name] is value
    assert supplied["same_author_interactions"] == [{"contributor": "Earlier contribution.", "account_reply": "Earlier response."}]
    assert rows[0]["incoming_contribution"] == " Earlier contribution. "
    trace.error.assert_not_called()
    assert trace.event.call_args_list == [
        call("single_call_reply_decision", lane="mention", target_id="target", **bot.single_call_decision_telemetry(result)),
        call("single_call_reply_provider_usage", lane="mention", target_id="target",
             strategy_version=bot.SINGLE_CALL_STRATEGY_VERSION, model=bot.SINGLE_CALL_MODEL,
             provider_response_id=result.provider_response_id, provider_latency_ms=result.provider_latency_ms,
             request_attempt_count=result.provider_request_attempt_count, **result.provider_usage),
    ]


def test_posting_outcome_uses_current_logger_and_metadata_fallback(monkeypatch):
    metadata = {"strategy_version": "fixture", "reply_kind": "principle", "reason_code": "useful_reply", "validated_draft_hash": "hash"}
    draft = {**metadata, "strategy_version": "draft"}
    reply = SimpleNamespace(pipeline_metadata=metadata, draft_record=draft)
    for supplied, expected in ((metadata, metadata), ([], draft), ({}, {})):
        reply.pipeline_metadata = supplied
        event = Mock()
        monkeypatch.setattr(bot, "log_event", event)
        bot.log_ai_reply_posting_outcome(
            reply=reply, status="posting_failed_retryable", lane="mention",
            target_id="target", failure_reason="fixture",
        )
        event.assert_called_once_with(
            "single_call_reply_posting_outcome", status="posting_failed_retryable",
            lane="mention", target_id="target", reply_post_id="",
            strategy_version=expected.get("strategy_version"), reply_kind=expected.get("reply_kind"),
            reason_code=expected.get("reason_code"), validated_draft_hash=expected.get("validated_draft_hash"),
            failure_reason="fixture",
        )
