"""Exercise the draft lifecycle at its owner and the root composition seam."""

from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError
import inspect
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock, call

import pytest

import mrs_bot_reply_drafts as reply_drafts
import mrs_bot_reply_state as reply_state
from single_call_reply import PipelineResult, ValidatedReply
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401
from tests.helpers.reply_fixtures import (
    UNIT_REPLY_REPOSITORY,
    patch_reply_draft_method,
    unit_approved_reply,
    unit_confirmed_reply_receipt,
    unit_reply_context,
)


@pytest.fixture
def make_owner():
    """Use local evidence and real validation unless a boundary is under test."""
    def build(**overrides):
        options = dict(
            validate_persisted_draft=bot.validate_single_call_persisted_draft,
            evidence_repository=lambda: UNIT_REPLY_REPOSITORY,
            record_result=bot._record_single_call_result,
            log_event=bot.log_event,
            log=bot.log,
            strategy_version=bot.SINGLE_CALL_STRATEGY_VERSION,
            model=bot.SINGLE_CALL_MODEL,
            result_type=PipelineResult,
            reply_type=ValidatedReply,
            evidence_unavailable=bot.ReplyEvidenceUnavailable,
            validation_error=bot.ReplyValidationError,
        )
        return reply_drafts.ReplyDrafts(**{**options, **overrides})

    return build


def test_import_needs_no_runtime_access_and_key_aliases_share_owner():
    code = """
import builtins, collections.abc, copy, dataclasses, io, logging, os, random, socket, sys
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('reply drafts import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'reply_evidence'} or name.startswith('mrs_bot_') and name != 'mrs_bot_reply_drafts':
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = forbidden
os.getenv = os._Environ.__getitem__ = forbidden
Path.home = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
before = random.getstate()
import mrs_bot_reply_drafts
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
assert 'single_call_reply' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert bot.pending_ai_reply_draft_key is reply_drafts.pending_ai_reply_draft_key
    assert reply_state.pending_ai_reply_draft_key is reply_drafts.pending_ai_reply_draft_key
    assert reply_drafts.pending_ai_reply_draft_key(100, None) == "mention:100"
    assert reply_drafts.pending_ai_reply_draft_key("100", "quote_tweet") == "quote_tweet:100"


def test_root_owner_binds_current_dependencies_without_accessing_evidence(monkeypatch):
    names = {
        "validate_persisted_draft": "validate_single_call_persisted_draft",
        "evidence_repository": "reply_evidence_repository",
        "record_result": "_record_single_call_result",
        "log_event": "log_event", "log": "log",
        "strategy_version": "SINGLE_CALL_STRATEGY_VERSION", "model": "SINGLE_CALL_MODEL",
        "result_type": "PipelineResult", "reply_type": "ValidatedReply",
        "evidence_unavailable": "ReplyEvidenceUnavailable",
        "validation_error": "ReplyValidationError",
    }
    snapshots = []
    for _ in range(2):
        current = {field: object() for field in names}
        current["evidence_repository"] = Mock()
        for field, root_name in names.items():
            monkeypatch.setattr(bot, root_name, current[field])
        owner = bot._reply_draft_owner()
        assert isinstance(owner, reply_drafts.ReplyDrafts)
        assert all(getattr(owner, field) is value for field, value in current.items())
        current["evidence_repository"].assert_not_called()
        snapshots.append((owner, current))
    first, first_inputs = snapshots[0]
    assert all(getattr(first, field) is value for field, value in first_inputs.items())
    assert first is not snapshots[1][0]
    with pytest.raises(FrozenInstanceError):
        first.model = "changed"


def test_root_adapters_preserve_arguments_result_identity_and_errors(monkeypatch):
    methods = {
        "validate_current_ai_reply_draft": "validate",
        "store_pending_ai_reply": "store",
        "recover_pending_ai_reply": "recover",
        "clear_pending_ai_reply": "clear",
        "ai_reply_receipt_draft_is_valid": "receipt_draft_is_valid",
    }
    for root_name, method_name in methods.items():
        adapter = getattr(bot, root_name)
        public = inspect.signature(adapter).parameters
        args = tuple(object() for parameter in public.values()
                     if parameter.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD)
        options = {name: object() for name, parameter in public.items()
                   if parameter.kind == inspect.Parameter.KEYWORD_ONLY}
        for _ in range(2):
            owner = Mock(spec=reply_drafts.ReplyDrafts)
            factory = Mock(return_value=owner)
            monkeypatch.setattr(bot, "_reply_draft_owner", factory)
            method = getattr(owner, method_name)
            result = object()
            method.return_value = result
            assert adapter(*args, **options) is result
            factory.assert_called_once_with()
            actual_args, actual_options = method.call_args
            assert len(actual_args) == len(args)
            assert all(actual is expected for actual, expected in zip(actual_args, args))
            assert actual_options.keys() == options.keys()
            assert all(actual_options[name] is value for name, value in options.items())
            failure = TypeError(root_name)
            method.side_effect = failure
            with pytest.raises(TypeError) as caught:
                adapter(*args, **options)
            assert caught.value is failure


def test_validation_fetches_fresh_evidence_and_preserves_context_and_recent_references(make_owner):
    context = unit_reply_context()
    draft = unit_approved_reply(context).draft_record
    recent = ["An earlier reply."]
    result = object()
    validate = Mock(return_value=result)
    repositories = [object() for _ in range(3)]
    evidence = Mock(side_effect=repositories)
    owner = make_owner(validate_persisted_draft=validate, evidence_repository=evidence)
    evidence.assert_not_called()
    for supplied, repository in zip((recent, [], None), repositories):
        assert owner.validate(draft, context=context, recent_replies=supplied) is result
        args, kwargs = validate.call_args
        assert args[0] is draft and kwargs["context"] is context
        assert kwargs["repository"] is repository
        actual = kwargs["recent_account_replies"]
        if supplied:
            assert actual is supplied
        else:
            assert actual == [] and actual is not supplied
    assert evidence.call_count == 3


def test_store_respects_reply_type_and_deep_copies_validated_record(make_owner):
    context = unit_reply_context()
    original = unit_approved_reply(
        context, factual=True,
        text="People moved from East Germany towards West Germany in November 1989.",
    )
    validated = make_owner().validate(original.draft_record, context=context)
    validate = Mock(return_value=validated)

    class CurrentReply(ValidatedReply):
        pass

    owner = make_owner(validate_persisted_draft=validate, reply_type=CurrentReply)
    state = {}
    assert owner.store(state, "100", "mention", original, context=context) is False
    validate.assert_not_called()
    reply = CurrentReply(original, original.draft_record, original.pipeline_metadata)
    retained = object()
    drafts = {"mention:101": retained}
    state["pending_ai_reply_drafts"] = drafts
    assert owner.store(state, "100", "mention", reply, context=context) is True
    assert validate.call_args.args[0] is reply.draft_record
    assert validate.call_args.kwargs["context"] is context
    assert state["pending_ai_reply_drafts"] is drafts
    stored = drafts["mention:100"]
    assert stored == validated and stored is not validated
    assert stored["used_fact_sources"][0] is not validated["used_fact_sources"][0]
    stored["used_fact_sources"][0]["fact_id"] = "changed"
    assert validated["used_fact_sources"][0]["fact_id"] == "F1"
    owner.clear(state, "100", "mention")
    assert list(drafts) == ["mention:101"] and drafts["mention:101"] is retained
    owner.clear(state, "101", "mention")
    assert "pending_ai_reply_drafts" not in state


def test_store_keeps_warning_native_exception_boundary_and_malformed_container(make_owner):
    context = unit_reply_context()
    reply = unit_approved_reply(context)
    failure = TypeError("invalid draft")
    validate, logger = Mock(side_effect=failure), Mock()
    owner = make_owner(validate_persisted_draft=validate, log=logger)
    state = {}
    assert owner.store(state, "100", "mention", reply, context=context) is False
    logger.warning.assert_called_once_with(
        "Refusing invalid single-call pending reply draft target_id=%s source=%s reason=%s",
        "100", "mention", failure,
    )
    assert state == {}
    validate.side_effect = None
    validate.return_value = {}
    with pytest.raises(KeyError, match="target_id"):
        owner.store(state, "100", "mention", reply, context=context)
    assert logger.warning.call_count == 1
    validate.return_value = reply.draft_record
    malformed = []
    state["pending_ai_reply_drafts"] = malformed
    assert owner.store(state, "100", "mention", reply, context=context) is False
    assert state["pending_ai_reply_drafts"] is malformed


@pytest.mark.parametrize("drafts", [None, [], {}, {"mention:100": None}, {"mention:101": {}}])
def test_absent_draft_recovery_and_clear_preserve_state_without_evidence_access(make_owner, drafts):
    state = {} if drafts is None else {"pending_ai_reply_drafts": drafts}
    before = copy.deepcopy(state)
    evidence, validate, record, event = Mock(), Mock(), Mock(), Mock()
    owner = make_owner(evidence_repository=evidence, validate_persisted_draft=validate,
                       record_result=record, log_event=event)
    assert owner.recover(state, "100", "mention", context=unit_reply_context()) is None
    assert state == before
    if drafts is not None:
        assert state["pending_ai_reply_drafts"] is drafts
    owner.clear(state, "100", "mention")
    if isinstance(drafts, dict) and not drafts:
        assert "pending_ai_reply_drafts" not in state
    else:
        assert state == before
    for dependency in (evidence, validate, record, event):
        dependency.assert_not_called()


@pytest.mark.parametrize("source", ["mention", "custom_receipt_source"])
def test_confirmed_target_operations_cover_all_lanes_and_preserve_other_draft_references(make_owner, source):
    evidence, validate, record, event = Mock(), Mock(), Mock(), Mock()
    owner = make_owner(evidence_repository=evidence, validate_persisted_draft=validate,
                       record_result=record, log_event=event)
    lanes = {source, "mention", "hot_post_reply", "quote_tweet", "conversational_reply"}
    sibling, unrelated = {"reply": "another target"}, {"reply": "another lane"}
    drafts = {
        **{f"{lane}:100": None for lane in lanes},
        "mention:101": sibling,
        "unrelated:100": unrelated,
    }
    state = {"pending_ai_reply_drafts": drafts}
    before = drafts.copy()
    assert owner.has_target(state, "100", source) is True
    assert drafts == before
    owner.clear_target(state, "100", source)
    assert state["pending_ai_reply_drafts"] is drafts
    assert drafts == {"mention:101": sibling, "unrelated:100": unrelated}
    assert drafts["mention:101"] is sibling and drafts["unrelated:100"] is unrelated
    assert owner.has_target(state, "100", source) is False
    assert owner.has_target(state, "101", source) is True
    owner.clear_target(state, "101", source)
    owner.clear_target(state, "100", "unrelated")
    assert state == {} and drafts == {}
    for dependency in (evidence, validate, record, event):
        dependency.assert_not_called()


@pytest.mark.parametrize("drafts", [None, [], {}])
def test_confirmed_target_operations_preserve_absent_and_malformed_maps(make_owner, drafts):
    owner = make_owner(evidence_repository=Mock(side_effect=AssertionError("no evidence access")))
    state = {} if drafts is None else {"pending_ai_reply_drafts": drafts}
    assert owner.has_target(state, "100", "mention") is False
    owner.clear_target(state, "100", "mention")
    if isinstance(drafts, list):
        assert state["pending_ai_reply_drafts"] is drafts
    else:
        assert state == {}


def test_recovery_copies_draft_metadata_and_uses_supplied_telemetry_and_result_class(make_owner):
    context = unit_reply_context()
    reply = unit_approved_reply(
        context, factual=True,
        text="People moved from East Germany towards West Germany in November 1989.",
    )
    record = reply.draft_record
    state = {"pending_ai_reply_drafts": {"mention:100": record}}
    validate = Mock(return_value=record)
    constructor, event = Mock(wraps=ValidatedReply), Mock()
    result = object()
    result_constructor = Mock(return_value=result)
    owner = make_owner(validate_persisted_draft=validate, reply_type=constructor,
                       result_type=result_constructor, log_event=event,
                       strategy_version="current-strategy", model="current-model")
    recent = ["Earlier prose."]
    assert owner.recover(state, "100", "mention", context=context, recent_replies=recent) is result
    recovered = result_constructor.call_args.kwargs["reply"]
    assert type(recovered) is ValidatedReply and recovered == reply
    assert validate.call_args.args[0] is record
    assert validate.call_args.kwargs["context"] is context
    assert validate.call_args.kwargs["recent_account_replies"] is recent
    _text, copied, metadata = constructor.call_args.args
    assert copied == record and copied is not record
    assert copied["used_fact_sources"][0] is not record["used_fact_sources"][0]
    assert metadata["used_fact_ids"] == record["used_fact_ids"]
    assert metadata["used_fact_ids"] is not record["used_fact_ids"]
    assert recovered.pipeline_metadata == {
        **reply.pipeline_metadata, "recovered_without_provider_call": True,
    }
    assert state["pending_ai_reply_drafts"]["mention:100"] is record
    assert result_constructor.call_args.kwargs["model_call_count"] == 0
    event.assert_called_once_with(
        "single_call_reply_draft_recovered", lane="mention", target_id="100",
        strategy_version="current-strategy", model="current-model",
        validated_draft_hash=record["validated_draft_hash"], model_call_count=0,
    )


@pytest.mark.parametrize("kind", ["unavailable", "validation", "obsolete"])
def test_recovery_keeps_exception_identities_retirement_and_exact_zero_call_result(make_owner, kind):
    class EvidenceFailure(RuntimeError):
        pass

    class ValidationFailure(RuntimeError):
        pass

    failure = {"unavailable": EvidenceFailure, "validation": ValidationFailure, "obsolete": ValueError}[kind](kind)
    context = unit_reply_context(contribution="ABC")
    record = unit_approved_reply(context).draft_record
    context["visible_conversation"].extend([None, {"text": "de"}, {"text": None}])
    record["supplied_images"] = [{}, {}]
    drafts = {"mention:100": record}
    state = {"pending_ai_reply_drafts": drafts}
    recent = ["Earlier prose.", {}]
    telemetry, constructor, logger = Mock(), Mock(wraps=PipelineResult), Mock()
    owner = make_owner(
        evidence_unavailable=EvidenceFailure, validation_error=ValidationFailure,
        validate_persisted_draft=Mock(side_effect=failure), result_type=constructor,
        record_result=telemetry, log=logger,
    )
    if kind == "unavailable":
        with pytest.raises(EvidenceFailure) as caught:
            owner.recover(state, "100", "mention", context=context, recent_replies=recent)
        assert caught.value is failure
        assert state["pending_ai_reply_drafts"] is drafts and drafts["mention:100"] is record
        logger.warning.assert_not_called()
        constructor.assert_not_called()
        telemetry.assert_not_called()
        return

    actual = owner.recover(state, "100", "mention", context=context, recent_replies=recent)
    assert state == {} and drafts == {}
    warning = (
        "Retiring pending reply draft that fails current local validation target_id=%s source=%s reason=%s"
        if kind == "validation" else
        "Discarding obsolete or invalid pending reply draft target_id=%s source=%s reason=%s"
    )
    logger.warning.assert_called_once_with(
        warning, "100", "mention",
        "validation_details_unavailable" if kind == "validation" else failure,
    )
    assert constructor.call_count == 1
    assert type(actual) is PipelineResult
    if kind == "validation":
        assert actual is telemetry.call_args.args[0]
        assert actual == PipelineResult(
            status="operational_failure", reason="persisted_draft_local_validation_failed",
            error_category="local_validation", model_call_count=0, local_validation_status="failed",
            rejected_reply_text=record["proposed_reply"],
            rejected_reply_text_character_count=len(record["proposed_reply"]),
            payload_sha256=record["model_payload_sha256"], visible_turn_count=3,
            visible_character_count=5, recent_conversational_reply_count=2, supplied_image_count=2,
        )
        telemetry.assert_called_once_with(actual, lane="mention", target_id="100")
    else:
        assert actual == PipelineResult(
            status="draft_discarded", reason="obsolete_or_invalid_persisted_draft",
            error_category="draft_validation",
        )
        telemetry.assert_not_called()


def test_recovered_duplicate_draft_emits_rule_and_retires_without_provider_call(make_owner, monkeypatch):
    context = unit_reply_context()
    reply = unit_approved_reply(context)
    state = {"pending_ai_reply_drafts": {"mention:100": reply.draft_record}}
    events = []
    provider = Mock(side_effect=AssertionError("recovery must not call the model"))
    monkeypatch.setattr(bot, "run_single_call_reply_pipeline", provider)
    monkeypatch.setattr(bot, "log_event", lambda kind, **fields: events.append((kind, fields)))
    result = make_owner().recover(
        state, "100", "mention", context=context,
        recent_replies=[{"post_id": "101", "text": str(reply)}],
    )
    assert state == {}
    assert result.status == "operational_failure" and result.reply is None
    assert result.reason == "persisted_draft_local_validation_failed"
    assert result.error_category == "local_validation" and result.model_call_count == 0
    provider.assert_not_called()
    assert len(events) == 1
    kind, decision = events[0]
    assert kind == "single_call_reply_decision"
    assert decision["validation_error_codes"] == ["exact_duplicate_reply"]
    assert decision["failure_reason"] == "persisted_draft_local_validation_failed"
    assert decision["local_validation_status"] == "failed"
    assert decision["model_call_count"] == 0
    assert decision["rejected_reply_text"] == str(reply)
    assert decision["rejected_reply_text_status"] == "available"
    assert decision["rejected_reply_text_character_count"] == len(reply)


@pytest.mark.parametrize("status", [None, "reply", "draft_discarded", "operational_failure"])
def test_pending_prose_compatibility_preserves_reply_and_outcome_references(monkeypatch, status):
    reply = unit_approved_reply(unit_reply_context()) if status == "reply" else None
    result = None if status is None else PipelineResult(
        status=status, reason="recovery-reason", reply=reply,
        error_category="local_validation", model_call_count=0,
    )
    recover = Mock(return_value=result)
    monkeypatch.setattr(bot, "recover_pending_ai_reply", recover)
    state, context, recent, outcome = {}, {}, [], {"retained": True}
    assert bot.pending_ai_reply(
        state, "100", "mention", context=context, recent_replies=recent,
        evaluation_outcome=outcome,
    ) is reply
    assert recover.call_args.args[0] is state
    assert recover.call_args.kwargs["context"] is context
    assert recover.call_args.kwargs["recent_replies"] is recent
    assert outcome == ({
        "retained": True, "status": status, "reason": "recovery-reason",
        "error_category": "local_validation", "model_call_count": 0,
    } if status == "operational_failure" else {"retained": True})


def test_receipt_validation_keeps_short_circuit_direct_equality_and_exception_boundary(make_owner):
    receipt = unit_confirmed_reply_receipt()
    validate = Mock(return_value={"proposed_reply": "42"})
    owner = make_owner(validate_persisted_draft=validate)
    assert owner.receipt_draft_is_valid({"reply_context": []}, "42") is False
    validate.assert_not_called()
    assert owner.receipt_draft_is_valid(receipt, 42) is False
    assert owner.receipt_draft_is_valid(receipt, "42") is True
    assert validate.call_args.args[0] is receipt["ai_reply_draft"]
    assert validate.call_args.kwargs["context"] is receipt["reply_context"]
    validate.side_effect = TypeError("invalid draft")
    assert owner.receipt_draft_is_valid(receipt, "42") is False
    validate.side_effect = None
    validate.return_value = {}
    with pytest.raises(KeyError, match="proposed_reply"):
        owner.receipt_draft_is_valid(receipt, "42")


@pytest.mark.parametrize("drafts", [None, [], {}, {"custom-key": None}, {"custom-key": "obsolete"}, {"custom-key": {}}])
def test_ineligible_retirement_keeps_missing_and_malformed_draft_behavior(make_owner, monkeypatch, drafts):
    state = {} if drafts is None else {"pending_ai_reply_drafts": drafts}
    before = copy.deepcopy(state)
    trace = Mock()
    trace.key.return_value = "custom-key"
    monkeypatch.setattr(reply_drafts, "pending_ai_reply_draft_key", trace.key)
    patch_reply_draft_method(monkeypatch, "clear", trace.clear)
    evidence = Mock()
    owner = make_owner(log_event=trace.event, evidence_repository=evidence)
    owner.retire_ineligible(state, "101", "hot_post_reply")
    expected = [call.key("101", "hot_post_reply")]
    if drafts == {"custom-key": {}}:
        expected.extend([
            call.event(
                "single_call_reply_posting_outcome",
                status="posting_failed_terminal", lane="hot_post_reply",
                target_id="101", reply_post_id="", strategy_version=None,
                reply_kind=None, reason_code=None, validated_draft_hash=None,
                failure_reason="reply_not_permitted_preflight",
            ),
            call.clear(state, "101", "hot_post_reply"),
        ])
    assert trace.mock_calls == expected
    assert state == before
    evidence.assert_not_called()


@pytest.mark.parametrize("boundary", [None, "key", "event", "clear"])
def test_ineligible_retirement_preserves_metadata_order_and_failures(make_owner, monkeypatch, boundary):
    draft = {
        "strategy_version": "stored-strategy", "reply_kind": "direct_reply",
        "reason_code": "answer_question", "validated_draft_hash": "stored-hash",
    }
    state = {"pending_ai_reply_drafts": {"custom-key": draft}}
    trace = Mock()
    trace.key.return_value = "custom-key"
    monkeypatch.setattr(reply_drafts, "pending_ai_reply_draft_key", trace.key)
    patch_reply_draft_method(monkeypatch, "clear", trace.clear)
    owner = make_owner(log_event=trace.event)
    failure = RuntimeError("retirement callback failed")
    if boundary:
        getattr(trace, boundary).side_effect = failure
        with pytest.raises(RuntimeError) as caught:
            owner.retire_ineligible(state, "101", "mention")
        assert caught.value is failure
    else:
        owner.retire_ineligible(state, "101", "mention")
    expected = [
        call.key("101", "mention"),
        call.event(
            "single_call_reply_posting_outcome", status="posting_failed_terminal",
            lane="mention", target_id="101", reply_post_id="", **draft,
            failure_reason="reply_not_permitted_preflight",
        ),
        call.clear(state, "101", "mention"),
    ]
    if boundary:
        expected = expected[:["key", "event", "clear"].index(boundary) + 1]
    assert trace.mock_calls == expected


def test_failed_recovery_retires_draft_before_result_failure_and_skips_telemetry(make_owner):
    class ValidationFailure(ValueError):
        errors = ["invalid_reply"]

    failure = RuntimeError("result construction failed")
    record = {"proposed_reply": "Rejected", "model_payload_sha256": "payload"}
    drafts = {"mention:100": record}
    state = {"pending_ai_reply_drafts": drafts}
    telemetry = Mock()

    def construct(**kwargs):
        assert state == {} and drafts == {}
        assert kwargs["model_call_count"] == 0
        assert kwargs["payload_sha256"] == "payload"
        raise failure

    owner = make_owner(
        validation_error=ValidationFailure,
        validate_persisted_draft=Mock(side_effect=ValidationFailure()),
        result_type=construct, record_result=telemetry,
    )
    with pytest.raises(RuntimeError) as caught:
        owner.recover(state, "100", "mention", context={"visible_conversation": []})
    assert caught.value is failure
    telemetry.assert_not_called()
