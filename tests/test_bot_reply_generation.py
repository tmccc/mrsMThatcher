from __future__ import annotations

import inspect
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from tests.helpers.adapter_assertions import assert_adapters_forward_current_dependencies

import mrs_bot_reply_generation as generation
import mrs_bot_reply_history as reply_history
from tests.helpers.single_call_fixtures import (
    FakeRepository,
    context as pipeline_context,
    enabled_config,
    raw_decision,
    response_envelope,
)
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401
from tests.helpers.reply_fixtures import patch_reply_history_method


def test_import_needs_no_runtime_access_and_constants_are_shared_objects():
    code = """
import builtins, collections.abc, io, logging, math, os, random, socket, sys
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('reply generation import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'reply_evidence'} or name.startswith('mrs_bot_') and name not in {'mrs_bot_reply_generation', 'mrs_bot_reply_native_media'}:
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
        assert module._record_single_call_result.__annotations__["result"] == "PipelineResult"


def test_adapters_forward_current_dependencies_arguments_results_and_errors(monkeypatch):
    names = (
        "log_ai_reply_posting_outcome",
        "_is_openai_provider_health_failure",
        "_is_terminal_candidate_local_failure",
        "_record_single_call_result",
    )
    assert_adapters_forward_current_dependencies(
        monkeypatch, bot=bot, implementation=generation, names=names,
    )


def test_evaluation_adapter_binds_current_history_and_preserves_other_dependencies(monkeypatch):
    adapter = bot.evaluate_single_call_reply
    public = inspect.signature(adapter).parameters
    parameters = inspect.signature(generation.evaluate_single_call_reply).parameters
    assert tuple(public) == ("context", "media_context", "state")
    assert public["media_context"].default is None
    assert public["state"].kind is inspect.Parameter.KEYWORD_ONLY
    assert {
        "_reply_target_epoch", "_reply_context_history_excluded_post_ids",
        "_same_author_confirmed_history_rows", "recent_confirmed_account_replies",
    }.isdisjoint(parameters)
    dependencies = parameters.keys() - public.keys() - {"history_for_evaluation"}
    implementation = Mock(return_value=object())
    factory = Mock(wraps=bot._reply_history_owner)
    monkeypatch.setattr(generation, "evaluate_single_call_reply", implementation)
    monkeypatch.setattr(bot, "_reply_history_owner", factory)
    context, media, state = {}, {}, {}
    histories = []
    for index in range(2):
        current = {name: object() for name in dependencies}
        for name, value in current.items():
            monkeypatch.setattr(bot, name, value)
        monkeypatch.setattr(bot, "MAX_RECENT_ACCOUNT_REPLIES", 10 + index)
        assert adapter(context, media, state=state) is implementation.return_value
        assert factory.call_count == index + 1
        args, supplied = implementation.call_args
        assert len(args) == 2 and args[0] is context and args[1] is media
        assert supplied.keys() == {*current, "state", "history_for_evaluation"}
        assert supplied["state"] is state
        assert all(supplied[name] is value for name, value in current.items())
        callback = supplied["history_for_evaluation"]
        assert callback.__func__ is reply_history.ReplyHistory.for_evaluation
        history = callback.__self__
        assert history.now_epoch is current["now_epoch"]
        assert history.maximum_recent_replies == 10 + index
        histories.append(history)
    assert histories[0] is not histories[1]
    assert histories[0].now_epoch is not histories[1].now_epoch
    failure = TypeError("current evaluation failure")
    implementation.side_effect = failure
    with pytest.raises(TypeError) as caught:
        adapter(context, media, state=state)
    assert caught.value is failure


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


def test_generation_preserves_order_and_references_through_the_current_pipeline(monkeypatch):
    context = pipeline_context(turns=1)
    state, outcome, media = {}, {"retained": "value"}, {}
    images = []
    same_author = [{"contributor": "Earlier contribution.", "account_reply": "Earlier response."}]
    recent = [{"post_id": "other", "text": "Another earlier response."}]
    repository, config = FakeRepository(), enabled_config()
    trace = Mock()
    trace.images.return_value = images
    trace.history.return_value = (same_author, recent)
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
        "require_remote_operation_unpaused": trace.pause,
        "reply_evidence_repository": trace.repository,
        "openai_responses_reply_call": trace.transport,
        "run_single_call_reply_pipeline": trace.pipeline,
        "_record_single_call_result": trace.record,
        "log_event": trace.event,
        "record_api_error": trace.error,
    }.items():
        monkeypatch.setattr(bot, root_name, callback)
    patch_reply_history_method(monkeypatch, "for_evaluation", trace.history)
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
        "images", "history", "pause",
        "repository", "pipeline", "transport", "record", "event", "info", "event",
    ]
    assert trace.images.call_args.args[0] is media
    trace.history.assert_called_once_with(state, context=context, target_id="target")
    assert trace.history.call_args.args[0] is state
    assert trace.history.call_args.kwargs["context"] is context
    supplied = trace.pipeline.call_args.kwargs
    for name, value in {"context": context, "config": config, "repository": repository, "transport": trace.transport, "same_author_interactions": same_author, "recent_account_replies": recent, "supplied_images": images}.items():
        assert supplied[name] is value
    assert supplied["same_author_interactions"] == [{"contributor": "Earlier contribution.", "account_reply": "Earlier response."}]
    trace.error.assert_not_called()
    assert trace.event.call_args_list == [
        call("single_call_reply_decision", lane="mention", target_id="target", **bot.single_call_decision_telemetry(result)),
        call("single_call_reply_provider_usage", lane="mention", target_id="target",
             strategy_version=bot.SINGLE_CALL_STRATEGY_VERSION, model=bot.SINGLE_CALL_MODEL,
             provider_response_id=result.provider_response_id, provider_latency_ms=result.provider_latency_ms,
             request_attempt_count=result.provider_request_attempt_count, **result.provider_usage),
    ]


def test_history_failure_preserves_pre_image_target_and_propagates_before_provider_access(monkeypatch):
    context = pipeline_context(turns=1)
    state, media = {}, {}
    trace = Mock()
    failure = TypeError("history unavailable")

    def collect_images(actual_media):
        assert actual_media is media
        context["target_id"] = "changed-during-image-collection"
        return []

    trace.images.side_effect = collect_images
    trace.history.side_effect = failure
    monkeypatch.setattr(bot, "collect_reply_images", trace.images)
    patch_reply_history_method(monkeypatch, "for_evaluation", trace.history)
    monkeypatch.setattr(bot, "reply_evidence_repository", trace.repository)
    monkeypatch.setattr(bot, "run_single_call_reply_pipeline", trace.pipeline)
    monkeypatch.setattr(bot, "record_api_error", trace.error)
    monkeypatch.setattr(bot, "_record_single_call_result", trace.record)

    with pytest.raises(TypeError) as caught:
        bot.evaluate_single_call_reply(context, media, state=state)

    assert caught.value is failure
    assert [entry[0] for entry in trace.mock_calls] == ["images", "history"]
    trace.history.assert_called_once_with(state, context=context, target_id="target")
    assert trace.history.call_args.args[0] is state
    assert trace.history.call_args.kwargs["context"] is context
    assert context["target_id"] == "changed-during-image-collection"
    assert state == {}


def test_recorded_validation_failure_logs_reply_and_only_known_rules(monkeypatch):
    result = bot.PipelineResult(
        status="operational_failure", reason="model_response_validation_failed",
        error_category="local_validation", model_call_count=1,
        local_validation_status="failed",
        validation_error_codes=(
            "reply_contains_mention", "PRIVATE model prose", "reply_contains_mention",
        ),
        rejected_reply_text="Rejected proposed reply @name",
        rejected_reply_text_character_count=29,
    )
    logger, event = Mock(), Mock()
    monkeypatch.setattr(bot, "log", logger)
    monkeypatch.setattr(bot, "log_event", event)

    bot._record_single_call_result(result, lane="quote_tweet", target_id="100")

    logger.info.assert_called_once_with(
        "Single-call reply validation failed target_id=%s lane=%s category=%s rules=%s",
        "100", "quote_tweet", "local_validation", "reply_contains_mention",
    )
    logger.warning.assert_not_called()
    assert event.call_count == 1
    assert event.call_args.args == ("single_call_reply_decision",)
    assert event.call_args.kwargs["validation_error_codes"] == ["reply_contains_mention"]
    assert event.call_args.kwargs["rejected_reply_text"] == "Rejected proposed reply @name"
    assert event.call_args.kwargs["rejected_reply_text_status"] == "available"
    assert event.call_args.kwargs["rejected_reply_text_character_count"] == 29
    assert "PRIVATE" not in repr(event.call_args)


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
