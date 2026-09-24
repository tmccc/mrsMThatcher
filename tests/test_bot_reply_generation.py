from __future__ import annotations

import single_call_reply as reply_pipeline_module

from dataclasses import FrozenInstanceError, replace
import inspect
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import mrs_bot_reply_assembly as assembly

import pytest

from tests.helpers.adapter_assertions import assert_adapters_forward_current_dependencies

import mrs_bot_reply_generation as generation
import mrs_bot_api_cooldowns as api_cooldowns
import mrs_bot_reply_history as reply_history
import mrs_bot_reply_model_transport as model_transport
import mrs_bot_reply_native_media as reply_media
from tests.helpers.single_call_fixtures import (
    FakeRepository,
    context as pipeline_context,
    enabled_config,
    raw_decision,
    response_envelope,
)
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401
from tests.helpers.reply_fixtures import patch_reply_history_method, patch_reply_owner_method


def test_import_needs_no_runtime_access_and_constants_are_shared_objects():
    code = """
import builtins, collections.abc, io, logging, math, os, random, socket, sys
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('reply generation import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'reply_evidence'} or name.startswith('mrs_bot_') and name not in {'mrs_bot_reply_generation', 'mrs_bot_reply_native_media', 'mrs_bot_request_route_values'}:
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
    assert generation.ReplyGeneration.record_result.__annotations__["result"] == "PipelineResult"


OWNER_INPUTS = {
    "remote_operations_paused": "RemoteOperationsPaused", "result_type": "PipelineResult", "log": "log",
    "require_remote_operation_unpaused": "require_remote_operation_unpaused",
    "run_pipeline": "run_single_call_reply_pipeline", "config": "single_call_reply",
    "evidence_repository": "reply_evidence_repository",
    "reply_type": "ValidatedReply",
    "decision_telemetry": "single_call_decision_telemetry", "log_event": "log_event",
    "strategy_version": "SINGLE_CALL_STRATEGY_VERSION",
}


def test_posting_adapter_keeps_current_callback_contract(monkeypatch):
    assert_adapters_forward_current_dependencies(
        monkeypatch, bot=bot, implementation=generation,
        names=("log_ai_reply_posting_outcome",),
    )


def test_generation_owner_binds_current_boundaries_without_runtime_access(monkeypatch):
    owners = []
    for index in range(2):
        shared_inputs = OWNER_INPUTS.keys() - {"run_pipeline", "decision_telemetry"}
        current = {field: Mock() for field in shared_inputs}
        for field in shared_inputs:
            name = OWNER_INPUTS[field]
            monkeypatch.setattr(bot, name, current[field])
        monkeypatch.setattr(bot, "MAX_RECENT_ACCOUNT_REPLIES", 10 + index)
        monkeypatch.setattr(bot, "SINGLE_CALL_MODEL", f"current-model-{index}")
        current_clock = Mock()
        cooldowns = Mock(spec=api_cooldowns.ApiCooldowns)
        monkeypatch.setattr(bot, "now_epoch", current_clock)
        owner = bot._reply_assembly()._reply_generation_owner(cooldowns=cooldowns)
        assert isinstance(owner, generation.ReplyGeneration)
        assert owner.run_pipeline is reply_pipeline_module.run_reply_pipeline
        assert owner.decision_telemetry is reply_pipeline_module.decision_telemetry
        for field, value in current.items():
            assert getattr(owner, field) is value
            value.assert_not_called()
        assert isinstance(owner.media, reply_media.ReplyMedia)
        assert owner.media.require_remote_operation_unpaused is current["require_remote_operation_unpaused"]
        assert isinstance(owner.history, reply_history.ReplyHistory)
        assert owner.history.maximum_recent_replies == 10 + index
        assert owner.history.now_epoch is current_clock
        assert isinstance(owner.model_transport, model_transport.ReplyModelTransport)
        assert owner.model_transport.model == f"current-model-{index}"
        assert owner.model_transport.now_epoch is current_clock
        assert owner.model_transport.require_remote_operation_unpaused is current["require_remote_operation_unpaused"]
        assert owner.cooldowns is cooldowns
        cooldowns.assert_not_called()
        owners.append(owner)
    assert owners[0] is not owners[1]
    for field in ("media", "history", "model_transport"):
        assert getattr(owners[0], field) is not getattr(owners[1], field)
    with pytest.raises(FrozenInstanceError):
        owners[0].history = owners[1].history




def test_failure_predicates_use_owned_distinct_category_sets(monkeypatch):
    assert bot._is_openai_provider_health_failure is generation._is_openai_provider_health_failure
    assert bot._is_terminal_candidate_local_failure is generation._is_terminal_candidate_local_failure
    monkeypatch.setattr(assembly.ReplyAssembly, "_reply_generation_owner", Mock(side_effect=AssertionError("pure classifier built runtime owner")))
    monkeypatch.setattr(generation, "_OPENAI_PROVIDER_HEALTH_FAILURE_CATEGORIES", frozenset({"current_health"}))
    monkeypatch.setattr(generation, "_TERMINAL_CANDIDATE_LOCAL_FAILURE_CATEGORIES", frozenset({"current_local"}))
    assert bot._is_openai_provider_health_failure("current_health")
    assert not bot._is_openai_provider_health_failure("provider_transport")
    assert not bot._is_openai_provider_health_failure("current_local")
    assert bot._is_openai_provider_health_failure("provider_http_503")
    assert not bot._is_openai_provider_health_failure("provider_http_5030")
    assert bot._is_terminal_candidate_local_failure({"status": "operational_failure", "error_category": "current_local"})
    assert not bot._is_terminal_candidate_local_failure({"status": "operational_failure", "error_category": "current_health"})


def test_generation_preserves_order_and_references_through_the_current_pipeline(monkeypatch):
    context = pipeline_context(turns=1)
    state, media = {}, {}
    images = []
    same_author = [{"contributor": "Earlier contribution.", "account_reply": "Earlier response."}]
    recent = [{"post_id": "other", "text": "Another earlier response."}]
    repository, config = FakeRepository(), enabled_config()
    trace = Mock()
    trace.images.return_value = images
    trace.history.return_value = (same_author, recent)
    trace.repository.return_value = repository
    trace.transport.return_value = {"response": response_envelope(raw_decision())}
    trace.pipeline = Mock(wraps=reply_pipeline_module.run_reply_pipeline)
    original_record = generation.ReplyGeneration.record_result
    def record(owner, result, **kwargs):
        trace.record(result, **kwargs)
        return original_record(owner, result, **kwargs)

    monkeypatch.setattr(generation.ReplyGeneration, "record_result", record)
    for root_name, callback in {
        "require_remote_operation_unpaused": trace.pause,
        "reply_evidence_repository": trace.repository,
        "log_event": trace.event,
    }.items():
        monkeypatch.setattr(bot, root_name, callback)
    monkeypatch.setattr(reply_pipeline_module, "run_reply_pipeline", trace.pipeline)
    patch_reply_owner_method(monkeypatch, reply_media.ReplyMedia, "collect", trace.images)
    patch_reply_history_method(monkeypatch, "for_evaluation", trace.history)
    patch_reply_owner_method(monkeypatch, model_transport.ReplyModelTransport, "call", trace.transport)
    monkeypatch.setattr(bot, "single_call_reply", config)
    monkeypatch.setattr(bot, "log", SimpleNamespace(info=trace.info, warning=trace.warning))
    result = bot._reply_assembly()._reply_generation_owner().evaluate(context, media, state=state)
    assert trace.record.call_args.args[0] is result
    assert isinstance(result.reply, bot.ValidatedReply)
    assert (result.status, result.reason, result.reason_code, result.reply_kind, result.model_call_count) == (
        "reply", "useful_reply", "useful_reply", "principle", 1,
    )
    assert [entry[0] for entry in trace.mock_calls] == [
        "images", "history", "pause",
        "repository", "pipeline", "transport", "record", "event", "info", "event",
    ]
    assert trace.images.call_args.args[0] is media
    trace.history.assert_called_once_with(state, context=context, target_id="target")
    assert trace.history.call_args.args[0] is state
    assert trace.history.call_args.kwargs["context"] is context
    supplied = trace.pipeline.call_args.kwargs
    for name, value in {"context": context, "config": config, "repository": repository, "same_author_interactions": same_author, "recent_account_replies": recent, "supplied_images": images}.items():
        assert supplied[name] is value
    assert isinstance(supplied["transport"].__self__, model_transport.ReplyModelTransport)
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
    patch_reply_owner_method(monkeypatch, reply_media.ReplyMedia, "collect", trace.images)
    patch_reply_history_method(monkeypatch, "for_evaluation", trace.history)
    monkeypatch.setattr(bot, "reply_evidence_repository", trace.repository)
    monkeypatch.setattr(reply_pipeline_module, "run_reply_pipeline", trace.pipeline)
    patch_reply_owner_method(
        monkeypatch, api_cooldowns.ApiCooldowns, "record_error", trace.error,
    )
    patch_reply_owner_method(monkeypatch, generation.ReplyGeneration, "record_result", trace.record)

    with pytest.raises(TypeError) as caught:
        bot._reply_assembly()._reply_generation_owner().evaluate(context, media, state=state)

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

    bot._reply_assembly()._reply_generation_owner().record_result(result, lane="quote_tweet", target_id="100")

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


def test_evaluation_owns_health_and_telemetry_and_fetches_evidence_per_call(monkeypatch):
    source = pipeline_context(turns=1)
    state = {}
    result = bot.PipelineResult(
        status="operational_failure", reason="unavailable", error_category="provider_transport",
    )
    repository, events, errors = Mock(return_value=object()), Mock(), Mock()
    monkeypatch.setattr(bot, "reply_evidence_repository", repository)
    patch_reply_owner_method(monkeypatch, reply_media.ReplyMedia, "collect", Mock(return_value=[]))
    monkeypatch.setattr(bot, "require_remote_operation_unpaused", Mock())
    patch_reply_history_method(monkeypatch, "for_evaluation", Mock(return_value=([], [])))
    pipeline = Mock(return_value=result)
    monkeypatch.setattr(reply_pipeline_module, "run_reply_pipeline", pipeline)
    monkeypatch.setattr(bot, "log_event", events)
    patch_reply_owner_method(
        monkeypatch, api_cooldowns.ApiCooldowns, "record_error", errors,
    )
    monkeypatch.setattr(
        bot, "record_api_error",
        Mock(side_effect=AssertionError("obsolete root cooldown relay used")),
    )
    forbidden = Mock(side_effect=AssertionError("generation bounced through a root-owned helper"))
    monkeypatch.setattr(bot, "_is_openai_provider_health_failure", forbidden)
    owner = bot._reply_assembly()._reply_generation_owner()
    repository.assert_not_called()
    for _ in range(2):
        assert owner.evaluate(source, state=state) is result
    assert repository.call_count == pipeline.call_count == errors.call_count == events.call_count == 2
    assert all(entry.kwargs["repository"] is repository.return_value for entry in pipeline.call_args_list)
    assert all(entry.args[0] is state for entry in errors.call_args_list)
    assert all(entry.args == ("single_call_reply_decision",) for entry in events.call_args_list)
    forbidden.assert_not_called()


@pytest.mark.parametrize("status,category,provider_status", [
    ("no_reply", None, 429),
    ("operational_failure", "local_validation", 429),
    ("operational_failure", "provider_transport", 503),
])
def test_health_persistence_failure_keeps_identity_and_precedes_telemetry(
    status, category, provider_status,
):
    result = bot.PipelineResult(
        status=status, reason="fixture outcome", error_category=category,
        provider_status_code=provider_status, provider_reset_epoch=2_000_000_123,
        provider_retry_after_seconds=120, provider_request_attempt_count=2,
    )
    state, trace = {}, Mock()
    failure = OSError("health persistence failed")
    provider_error = bot.ApiError("provider health", service="openai")
    trace.error.return_value = provider_error

    def fail_accounting(actual_state, error, service):
        assert actual_state is state and error is provider_error and service == "openai"
        actual_state["health_update_attempted"] = True
        raise failure

    trace.account.side_effect = fail_accounting
    owner = replace(
        bot._reply_assembly()._reply_generation_owner(),
        media=SimpleNamespace(
            collect=Mock(return_value=[]),
            media_unavailable=bot.ReplyMediaUnavailable,
            media_transient_unavailable=bot.ReplyMediaTransientUnavailable,
        ),
        history=Mock(for_evaluation=Mock(return_value=([], []))),
        require_remote_operation_unpaused=Mock(),
        evidence_repository=Mock(return_value=object()),
        run_pipeline=Mock(return_value=result),
        model_transport=SimpleNamespace(
            call=Mock(), error=trace.error, model=bot.SINGLE_CALL_MODEL,
        ),
        cooldowns=SimpleNamespace(active=Mock(return_value=False), record_error=trace.account),
        log_event=trace.telemetry,
    )
    with pytest.raises(OSError) as caught:
        owner.evaluate(pipeline_context(turns=1), state=state)
    assert caught.value is failure
    assert state == {"health_update_attempted": True}
    assert [entry[0] for entry in trace.mock_calls] == ["error", "account"]
    assert trace.error.call_args.kwargs == {
        "category": "provider_http_429" if provider_status == 429 else category,
        "status_code": provider_status, "reset_epoch": 2_000_000_123,
        "retry_after_seconds": 120, "request_attempt_count": 2,
    }
    trace.telemetry.assert_not_called()


@pytest.mark.parametrize("transient", [False, True])
def test_media_failure_keeps_original_identity_and_telemetry_before_warning(monkeypatch, transient):
    context = pipeline_context(turns=1)
    context["visible_conversation"].extend([None, {"text": "Extra"}])
    failure_type = bot.ReplyMediaTransientUnavailable if transient else bot.ReplyMediaUnavailable
    media_error = failure_type("material image unavailable")
    telemetry_error = RuntimeError("decision telemetry failed")
    logger = Mock()
    history, pipeline = Mock(), Mock()
    recorded = Mock(side_effect=telemetry_error)

    def collect(_media):
        context["target_id"] = "changed during collection"
        raise media_error

    patch_reply_owner_method(monkeypatch, reply_media.ReplyMedia, "collect", collect)
    monkeypatch.setattr(bot, "log", logger)
    monkeypatch.setattr(reply_pipeline_module, "run_reply_pipeline", pipeline)
    patch_reply_history_method(monkeypatch, "for_evaluation", history)
    patch_reply_owner_method(monkeypatch, generation.ReplyGeneration, "record_result", recorded)
    with pytest.raises(RuntimeError) as caught:
        bot._reply_assembly()._reply_generation_owner().evaluate(context, {}, state={})
    assert caught.value is telemetry_error
    assert caught.value.__context__ is media_error
    result = recorded.call_args.args[0]
    assert result.error_category == ("image_transport" if transient else "image_input")
    assert result.visible_turn_count == 2
    assert result.model_call_count == result.supplied_image_count == 0
    assert recorded.call_args.kwargs == {"lane": "mention", "target_id": "target"}
    logger.warning.assert_not_called()
    history.assert_not_called()
    pipeline.assert_not_called()
