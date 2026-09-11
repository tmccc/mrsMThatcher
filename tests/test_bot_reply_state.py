from __future__ import annotations

import inspect
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock, call

import pytest

import mrs_bot_reply_state as reply_state
from single_call_reply import PipelineResult, ValidatedReply
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_regular_post_receipt  # noqa: F401
from tests.helpers.reply_fixtures import (
    UNIT_REPLY_REPOSITORY,
    unit_approved_reply,
    unit_confirmed_reply_receipt,
    unit_reply_context,
)


@pytest.fixture
def confirmed_row():
    state = bot.default_state()
    bot.apply_confirmed_reply_receipt(state, unit_confirmed_reply_receipt())
    return state["ai_reply_history"][0]


def test_import_needs_no_runtime_access_and_root_aliases_share_owner_objects():
    code = """
import builtins, collections.abc, copy, datetime, hashlib, io, logging, os, random, socket, sys
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('reply state import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'reply_evidence'} or name.startswith('mrs_bot_') and name != 'mrs_bot_reply_state':
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = forbidden
os.getenv = os._Environ.__getitem__ = forbidden
Path.home = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
before = random.getstate()
import mrs_bot_reply_state
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
assert 'single_call_reply' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    lanes = reply_state.CONVERSATIONAL_REPLY_HISTORY_LANES
    assert type(lanes) is frozenset
    assert lanes == {"mention", "hot_post_reply", "quote_tweet", "conversational_reply"}
    assert bot.CONVERSATIONAL_REPLY_HISTORY_LANES is lanes
    for name in ("pending_ai_reply_draft_key", "_confirmed_history_sort_key", "_reply_target_epoch"):
        assert getattr(bot, name) is getattr(reply_state, name)
    assert reply_state.copy is bot.copy and reply_state.hashlib is bot.hashlib
    assert reply_state.datetime is bot.datetime


def test_adapters_forward_current_dependencies_arguments_results_and_errors(monkeypatch):
    names = (
        "validate_current_ai_reply_draft", "store_pending_ai_reply", "recover_pending_ai_reply",
        "clear_pending_ai_reply", "_confirmed_conversational_history_rows",
        "recent_confirmed_account_replies", "_reply_context_history_excluded_post_ids",
        "recovery_comparison_account_replies", "_same_author_confirmed_history_rows",
        "recent_same_author_account_interactions", "ai_reply_receipt_draft_is_valid",
    )
    for name in names:
        adapter = getattr(bot, name)
        public = inspect.signature(adapter).parameters
        dependencies = inspect.signature(getattr(reply_state, name)).parameters.keys() - public.keys()
        args = tuple(object() for parameter in public.values() if parameter.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD)
        options = {key: object() for key, parameter in public.items() if parameter.kind == inspect.Parameter.KEYWORD_ONLY}
        result = object()
        owner = Mock(return_value=result)
        with monkeypatch.context() as patch:
            patch.setattr(reply_state, name, owner)
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


def test_validation_fetches_current_evidence_and_preserves_context_and_recent_references(monkeypatch):
    context = unit_reply_context()
    draft = unit_approved_reply(context).draft_record
    recent = ["An earlier reply."]
    result = object()
    validate = Mock(return_value=result)
    monkeypatch.setattr(bot, "validate_single_call_persisted_draft", validate)
    for _ in range(2):
        repository = object()
        evidence = Mock(return_value=repository)
        monkeypatch.setattr(bot, "reply_evidence_repository", evidence)
        for supplied in (recent, [], None):
            assert bot.validate_current_ai_reply_draft(draft, context=context, recent_replies=supplied) is result
            args, kwargs = validate.call_args
            assert args[0] is draft and kwargs["context"] is context
            assert kwargs["repository"] is repository
            actual = kwargs["recent_account_replies"]
            if supplied:
                assert actual is supplied
            else:
                assert actual == [] and actual is not supplied
        assert evidence.call_count == 3


def test_store_uses_current_class_and_key_and_deep_copies_the_validated_record(monkeypatch):
    context = unit_reply_context()
    original = unit_approved_reply(
        context, factual=True,
        text="People moved from East Germany towards West Germany in November 1989.",
    )
    validated = bot.validate_current_ai_reply_draft(original.draft_record, context=context)
    validate = Mock(return_value=validated)
    monkeypatch.setattr(bot, "validate_current_ai_reply_draft", validate)

    class CurrentReply(ValidatedReply):
        pass

    monkeypatch.setattr(bot, "ValidatedReply", CurrentReply)
    state = {}
    assert bot.store_pending_ai_reply(state, "100", "mention", original, context=context) is False
    validate.assert_not_called()
    reply = CurrentReply(original, original.draft_record, original.pipeline_metadata)
    key = Mock(return_value="current-key")
    monkeypatch.setattr(bot, "pending_ai_reply_draft_key", key)
    drafts = {"retained": object()}
    state["pending_ai_reply_drafts"] = drafts
    assert bot.store_pending_ai_reply(state, "100", "mention", reply, context=context) is True
    assert validate.call_args.args[0] is reply.draft_record
    assert validate.call_args.kwargs["context"] is context
    assert state["pending_ai_reply_drafts"] is drafts
    stored = drafts["current-key"]
    assert stored == validated and stored is not validated
    assert stored["used_fact_sources"][0] is not validated["used_fact_sources"][0]
    stored["used_fact_sources"][0]["fact_id"] = "changed"
    assert validated["used_fact_sources"][0]["fact_id"] == "F1"
    bot.clear_pending_ai_reply(state, "100", "mention")
    assert list(drafts) == ["retained"]
    key.return_value = "retained"
    bot.clear_pending_ai_reply(state, "100", "mention")
    assert "pending_ai_reply_drafts" not in state
    assert key.call_args_list == [call("100", "mention")] * 3


def test_store_keeps_warning_native_exception_boundary_and_malformed_container(monkeypatch):
    context = unit_reply_context()
    reply = unit_approved_reply(context)
    failure = TypeError("invalid draft")
    validate = Mock(side_effect=failure)
    logger = Mock()
    monkeypatch.setattr(bot, "validate_current_ai_reply_draft", validate)
    monkeypatch.setattr(bot, "log", logger)
    state = {}
    assert bot.store_pending_ai_reply(state, "100", "mention", reply, context=context) is False
    logger.warning.assert_called_once_with(
        "Refusing invalid single-call pending reply draft target_id=%s source=%s reason=%s",
        "100", "mention", failure,
    )
    assert state == {}
    validate.side_effect = None
    validate.return_value = {}
    with pytest.raises(KeyError, match="target_id"):
        bot.store_pending_ai_reply(state, "100", "mention", reply, context=context)
    assert logger.warning.call_count == 1
    validate.return_value = reply.draft_record
    malformed = []
    state["pending_ai_reply_drafts"] = malformed
    assert bot.store_pending_ai_reply(state, "100", "mention", reply, context=context) is False
    assert state["pending_ai_reply_drafts"] is malformed


def test_recovery_copies_draft_metadata_and_uses_current_telemetry_and_result_class(monkeypatch):
    context = unit_reply_context()
    reply = unit_approved_reply(
        context, factual=True,
        text="People moved from East Germany towards West Germany in November 1989.",
    )
    record = reply.draft_record
    state = {"pending_ai_reply_drafts": {"mention:100": record}}
    validate = Mock(return_value=record)
    constructor = Mock(wraps=ValidatedReply)
    event = Mock()
    monkeypatch.setattr(bot, "validate_current_ai_reply_draft", validate)
    monkeypatch.setattr(bot, "ValidatedReply", constructor)
    monkeypatch.setattr(bot, "log_event", event)
    monkeypatch.setattr(bot, "SINGLE_CALL_STRATEGY_VERSION", "current-strategy")
    monkeypatch.setattr(bot, "SINGLE_CALL_MODEL", "current-model")
    recent, outcome = ["Earlier prose."], {"retained": True}
    recovered = bot.pending_ai_reply(
        state, "100", "mention", context=context, recent_replies=recent,
        evaluation_outcome=outcome,
    )
    assert type(recovered) is ValidatedReply and recovered == reply
    assert validate.call_args.args[0] is record
    assert validate.call_args.kwargs["context"] is context
    assert validate.call_args.kwargs["recent_replies"] is recent
    _text, copied, metadata = constructor.call_args.args
    assert copied == record and copied is not record
    assert copied["used_fact_sources"][0] is not record["used_fact_sources"][0]
    assert metadata["used_fact_ids"] == record["used_fact_ids"]
    assert metadata["used_fact_ids"] is not record["used_fact_ids"]
    assert recovered.pipeline_metadata == {
        **reply.pipeline_metadata, "recovered_without_provider_call": True,
    }
    assert outcome == {"retained": True}
    assert state["pending_ai_reply_drafts"]["mention:100"] is record
    event.assert_called_once_with(
        "single_call_reply_draft_recovered", lane="mention", target_id="100",
        strategy_version="current-strategy", model="current-model",
        validated_draft_hash=record["validated_draft_hash"], model_call_count=0,
    )


@pytest.mark.parametrize("kind", ["unavailable", "validation", "obsolete"])
def test_recovery_keeps_current_exception_classes_retirement_and_exact_zero_call_result(kind, monkeypatch):
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
    recent, outcome = ["Earlier prose.", {}], {"retained": True}
    validate = Mock(side_effect=failure)
    result, constructor, logger = Mock(), Mock(wraps=PipelineResult), Mock()
    monkeypatch.setattr(bot, "ReplyEvidenceUnavailable", EvidenceFailure)
    monkeypatch.setattr(bot, "ReplyValidationError", ValidationFailure)
    monkeypatch.setattr(bot, "validate_current_ai_reply_draft", validate)
    monkeypatch.setattr(bot, "PipelineResult", constructor)
    monkeypatch.setattr(bot, "_record_single_call_result", result)
    monkeypatch.setattr(bot, "log", logger)
    if kind == "unavailable":
        with pytest.raises(EvidenceFailure) as caught:
            bot.pending_ai_reply(state, "100", "mention", context=context, recent_replies=recent, evaluation_outcome=outcome)
        assert caught.value is failure
        assert state["pending_ai_reply_drafts"] is drafts and drafts["mention:100"] is record
        logger.warning.assert_not_called()
    else:
        assert bot.pending_ai_reply(state, "100", "mention", context=context, recent_replies=recent, evaluation_outcome=outcome) is None
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
    if kind == "validation":
        assert outcome == {
            "retained": True, "status": "operational_failure",
            "reason": "persisted_draft_local_validation_failed",
            "error_category": "local_validation", "model_call_count": 0,
        }
        assert constructor.call_count == 1
        actual = result.call_args.args[0]
        assert type(actual) is PipelineResult
        assert actual == PipelineResult(
            status="operational_failure", reason="persisted_draft_local_validation_failed",
            error_category="local_validation", model_call_count=0, local_validation_status="failed",
            payload_sha256=record["model_payload_sha256"], visible_turn_count=3,
            visible_character_count=5, recent_conversational_reply_count=2, supplied_image_count=2,
        )
        result.assert_called_once_with(actual, lane="mention", target_id="100")
    else:
        assert outcome == {"retained": True}
        if kind == "obsolete":
            constructor.assert_called_once_with(
                status="draft_discarded", reason="obsolete_or_invalid_persisted_draft",
                error_category="draft_validation",
            )
        else:
            constructor.assert_not_called()
        result.assert_not_called()


def test_recovered_duplicate_draft_emits_rule_and_retires_without_provider_call(monkeypatch):
    context = unit_reply_context()
    reply = unit_approved_reply(context)
    state = {"pending_ai_reply_drafts": {"mention:100": reply.draft_record}}
    outcome, events = {}, []
    provider = Mock(side_effect=AssertionError("recovery must not call the model"))
    monkeypatch.setattr(bot, "reply_evidence_repository", lambda: UNIT_REPLY_REPOSITORY)
    monkeypatch.setattr(bot, "run_single_call_reply_pipeline", provider)
    monkeypatch.setattr(
        bot, "log_event", lambda kind, **fields: events.append((kind, fields)),
    )

    assert bot.pending_ai_reply(
        state, "100", "mention", context=context,
        recent_replies=[{"post_id": "101", "text": str(reply)}],
        evaluation_outcome=outcome,
    ) is None

    assert state == {}
    assert outcome == {
        "status": "operational_failure",
        "reason": "persisted_draft_local_validation_failed",
        "error_category": "local_validation", "model_call_count": 0,
    }
    provider.assert_not_called()
    assert len(events) == 1
    kind, decision = events[0]
    assert kind == "single_call_reply_decision"
    assert decision["validation_error_codes"] == ["exact_duplicate_reply"]
    assert decision["failure_reason"] == "persisted_draft_local_validation_failed"
    assert decision["local_validation_status"] == "failed"
    assert decision["model_call_count"] == 0
    assert str(reply) not in repr(decision)


def test_recent_default_is_fixed_while_body_reads_current_cap(confirmed_row, monkeypatch):
    default = inspect.signature(bot.recent_confirmed_account_replies).parameters["limit"].default
    assert default == bot.MAX_RECENT_ACCOUNT_REPLIES
    assert inspect.signature(reply_state.recent_confirmed_account_replies).parameters["limit"].default is inspect.Parameter.empty
    count = default + 5
    state = {"ai_reply_history": [
        {**confirmed_row, "reply_post_id": str(index), "reply_epoch": index}
        for index in range(1, count + 1)
    ]}
    monkeypatch.setattr(bot, "MAX_RECENT_ACCOUNT_REPLIES", count)
    assert len(bot.recent_confirmed_account_replies(state, before_epoch=count + 1)) == default
    assert len(bot.recent_confirmed_account_replies(state, count, before_epoch=count + 1)) == count
    monkeypatch.setattr(bot, "MAX_RECENT_ACCOUNT_REPLIES", 2)
    assert [row["post_id"] for row in bot.recent_confirmed_account_replies(state, before_epoch=count + 1)] == [str(count - 1), str(count)]
    assert bot.recent_confirmed_account_replies(state, 0, before_epoch=count + 1) == []
    assert inspect.signature(bot.recent_confirmed_account_replies).parameters["limit"].default == default


def test_confirmed_rows_keep_references_and_current_lane_id_and_epoch_rules(confirmed_row, monkeypatch):
    state = {"ai_reply_history": [confirmed_row]}
    assert bot._confirmed_conversational_history_rows(state)[0] is confirmed_row
    lanes = reply_state.CONVERSATIONAL_REPLY_HISTORY_LANES
    monkeypatch.setattr(bot, "CONVERSATIONAL_REPLY_HISTORY_LANES", frozenset({"current-lane"}))
    assert bot._confirmed_conversational_history_rows(state) == []
    confirmed_row["candidate_source"] = "current-lane"
    confirmed_row["target_id"] = "current-target"
    valid_id = Mock(return_value=True)
    monkeypatch.setattr(bot, "valid_string_post_id", valid_id)
    assert bot._confirmed_conversational_history_rows(state)[0] is confirmed_row
    assert valid_id.call_args_list == [call("current-target"), call(confirmed_row["reply_post_id"])]
    monkeypatch.setattr(bot, "MAX_REASONABLE_STATE_EPOCH", confirmed_row["reply_epoch"] - 1)
    assert bot._confirmed_conversational_history_rows(state) == []
    assert reply_state.CONVERSATIONAL_REPLY_HISTORY_LANES is lanes


def test_same_author_keeps_stable_numeric_order_row_identity_and_zero_cap_slice(confirmed_row, monkeypatch):
    first = {**confirmed_row, "reply_post_id": "2", "reply_epoch": 10}
    second = {**confirmed_row, "reply_post_id": "10", "reply_epoch": 10}
    replacement = {**first, "proposed_reply": " Replacement. "}
    replacement["incoming_contribution"] = " " + confirmed_row["incoming_contribution"] + " "
    state = {"ai_reply_history": [second, first, replacement]}
    monkeypatch.setattr(bot, "AI_REPLY_HISTORY_MAX_AGE_SECONDS", 1)
    options = dict(author_id="200", current_thread_post_ids=set(), target_id="500", before_epoch=11)
    rows = bot._same_author_confirmed_history_rows(state, **options)
    assert len(rows) == 2 and rows[0] is replacement and rows[1] is second
    assert bot._same_author_confirmed_history_rows(state, **{**options, "before_epoch": 10}) == []
    monkeypatch.setattr(bot, "MAX_SAME_AUTHOR_INTERACTIONS", 0)
    rows = bot._same_author_confirmed_history_rows(state, **options)
    assert len(rows) == 2 and rows[0] is replacement and rows[1] is second
    interactions = bot.recent_same_author_account_interactions(
        state, author_id="200", conversation_id="500", target_id="500", before_epoch=11,
    )
    assert interactions == [
        {"contributor": confirmed_row["incoming_contribution"], "account_reply": "Replacement."},
        {"contributor": confirmed_row["incoming_contribution"], "account_reply": second["proposed_reply"]},
    ]


def test_recovery_merge_keeps_string_ties_whitespace_and_current_bounded_clock(confirmed_row, monkeypatch):
    state = {"ai_reply_history": [
        {**confirmed_row, "reply_post_id": "2", "reply_epoch": 100, "proposed_reply": " Two. "},
        {**confirmed_row, "reply_post_id": "10", "reply_epoch": 100, "proposed_reply": " Ten. "},
    ]}
    context = unit_reply_context(target_id="500")
    excluded = {"500"}
    exclusions = Mock(return_value=excluded)
    recent = Mock(wraps=bot.recent_confirmed_account_replies)
    same_author = Mock(wraps=bot._same_author_confirmed_history_rows)
    clock = Mock(return_value=1000)
    monkeypatch.setattr(bot, "_reply_context_history_excluded_post_ids", exclusions)
    monkeypatch.setattr(bot, "recent_confirmed_account_replies", recent)
    monkeypatch.setattr(bot, "_same_author_confirmed_history_rows", same_author)
    monkeypatch.setattr(bot, "now_epoch", clock)
    monkeypatch.setattr(bot, "MAX_REASONABLE_STATE_EPOCH", 100)
    monkeypatch.setattr(bot, "MAX_RECENT_ACCOUNT_REPLIES", 1)
    assert bot.recovery_comparison_account_replies(state, context=context) == [
        {"post_id": "10", "text": "Ten."}, {"post_id": "2", "text": " Two. "},
    ]
    assert exclusions.call_args.args[0] is context
    assert recent.call_args.args[0] is same_author.call_args.args[0] is state
    assert recent.call_args.kwargs["excluded_post_ids"] is excluded
    assert same_author.call_args.kwargs["current_thread_post_ids"] is excluded
    assert recent.call_args.kwargs["before_epoch"] == same_author.call_args.kwargs["before_epoch"] == 101
    clock.assert_called_once_with()


def test_context_exclusions_use_current_quoted_reference_and_original_context(monkeypatch):
    context = unit_reply_context()
    context.update(thread_id=300, root_post_id=400)
    context["visible_conversation"].extend([None, {"post_id": 200}, {"post_id": ""}])
    for quoted_id in ("500", None):
        quoted = Mock(return_value=quoted_id)
        monkeypatch.setattr(bot, "quoted_post_reference_id", quoted)
        assert bot._reply_context_history_excluded_post_ids(context) == (
            {"100", "200", "300", "400"} | ({quoted_id} if quoted_id else set())
        )
        assert quoted.call_args.args[0] is context


def test_receipt_validation_keeps_short_circuit_direct_equality_and_exception_boundary(monkeypatch):
    receipt = unit_confirmed_reply_receipt()
    validate = Mock(return_value={"proposed_reply": "42"})
    monkeypatch.setattr(bot, "validate_current_ai_reply_draft", validate)
    assert bot.ai_reply_receipt_draft_is_valid({"reply_context": []}, "42") is False
    validate.assert_not_called()
    assert bot.ai_reply_receipt_draft_is_valid(receipt, 42) is False
    assert bot.ai_reply_receipt_draft_is_valid(receipt, "42") is True
    assert validate.call_args.args[0] is receipt["ai_reply_draft"]
    assert validate.call_args.kwargs["context"] is receipt["reply_context"]
    validate.side_effect = TypeError("invalid draft")
    assert bot.ai_reply_receipt_draft_is_valid(receipt, "42") is False
    validate.side_effect = None
    validate.return_value = {}
    with pytest.raises(KeyError, match="proposed_reply"):
        bot.ai_reply_receipt_draft_is_valid(receipt, "42")


def test_target_epoch_keeps_timezone_requirement_and_native_timestamp_errors(monkeypatch):
    context = unit_reply_context()
    assert bot._reply_target_epoch(context) == 1_784_548_800
    assert bot._reply_target_epoch({"target_created_at": "2026-07-20T12:00:00"}) is None
    assert bot._reply_target_epoch({"target_created_at": "invalid"}) is None
    parsed = Mock(tzinfo=object())
    failure = ValueError("native timestamp failure")
    parsed.timestamp.side_effect = failure
    parse = Mock(return_value=parsed)
    monkeypatch.setattr(reply_state, "datetime", Mock(fromisoformat=parse))
    with pytest.raises(ValueError) as caught:
        bot._reply_target_epoch(context)
    assert caught.value is failure
    parse.assert_called_once_with("2026-07-20T12:00:00+00:00")
    parse.side_effect = TypeError("native parse failure")
    with pytest.raises(TypeError, match="native parse failure"):
        bot._reply_target_epoch(context)
