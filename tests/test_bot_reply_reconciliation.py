from __future__ import annotations

import copy
import inspect
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock, call

import pytest

import mrs_bot_reply_reconciliation as reconciliation
from tests.helpers.mention_fixtures import mention, queue_active_mention
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_regular_post_receipt  # noqa: F401
from tests.helpers.reply_fixtures import (
    unit_confirmed_reply_receipt,
    unit_confirmed_v4_reply_receipt,
)


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, copy, io, logging, os, random, re, socket, sys
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('reply reconciliation import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'reply_evidence'} or name.startswith('mrs_bot_') and name != 'mrs_bot_reply_reconciliation':
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = forbidden
os.getenv = os._Environ.__getitem__ = forbidden
Path.home = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
before = random.getstate()
random.Random = random.seed = random.random = forbidden
import mrs_bot_reply_reconciliation
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
    assert reconciliation.copy is bot.copy


def test_adapters_forward_current_dependencies_arguments_results_and_errors(monkeypatch):
    names = (
        "_valid_iso_date",
        "_advance_reply_counters_to_confirmation_date",
        "apply_confirmed_reply_receipt",
        "reconcile_confirmed_reply_receipt",
        "confirmed_reply_emergency_representation_is_complete",
    )
    for name in names:
        adapter = getattr(bot, name)
        public = inspect.signature(adapter).parameters
        dependencies = inspect.signature(getattr(reconciliation, name)).parameters.keys() - public.keys()
        args = tuple(object() for parameter in public.values() if parameter.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD)
        options = {key: object() for key, parameter in public.items() if parameter.kind == inspect.Parameter.KEYWORD_ONLY}
        result = object()
        owner = Mock(return_value=result)
        with monkeypatch.context() as patch:
            patch.setattr(reconciliation, name, owner)
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


def test_date_round_trip_uses_current_datetime_and_catches_only_value_error(monkeypatch):
    assert bot._valid_iso_date("2024-02-29")
    assert not bot._valid_iso_date("2024-2-29")
    assert not bot._valid_iso_date("2023-02-29")
    assert not bot._valid_iso_date(None)
    current = Mock()
    current.strptime.return_value.strftime.return_value = "current-date"
    monkeypatch.setattr(bot, "datetime", current)
    assert bot._valid_iso_date("current-date")
    current.strptime.assert_called_once_with("current-date", "%Y-%m-%d")
    current.strptime.return_value.strftime.assert_called_once_with("%Y-%m-%d")
    current.strptime.side_effect = ValueError("invalid date")
    assert not bot._valid_iso_date("current-date")
    failure = TypeError("current parser failed")
    current.strptime.side_effect = failure
    with pytest.raises(TypeError) as caught:
        bot._valid_iso_date("current-date")
    assert caught.value is failure


def test_confirmation_and_authority_precede_counter_reset_and_clarification_conflict(monkeypatch):
    receipt = unit_confirmed_v4_reply_receipt()
    receipt["clarification_reply"] = {"thread_id": "700"}
    state = bot.default_state()
    state.update(
        daily_reply_date="2000-01-01", daily_reply_count=7,
        daily_replied_author_ids=["old"], daily_replied_author_counts={"old": 2},
        clarification_reply_records={"700": {"reply_post_id": "998"}},
    )
    before = copy.deepcopy(state)
    trace = Mock()
    for label, name in (
        ("confirmation", "conversational_reply_confirmation_epoch"),
        ("authority", "validate_pending_mention_candidate_authority"),
        ("advance", "_advance_reply_counters_to_confirmation_date"),
        ("clear", "clear_pending_ai_reply"),
        ("cache", "cache_tweet"),
    ):
        callback = Mock(wraps=getattr(bot, name))
        trace.attach_mock(callback, label)
        monkeypatch.setattr(bot, name, callback)
    trace.authority.return_value = (False, False)
    with pytest.raises(bot.InvalidConfirmedReplyReceipt, match="bounded pending-candidate"):
        bot.apply_confirmed_reply_receipt(state, receipt)
    assert state == before
    assert [entry[0] for entry in trace.mock_calls] == ["confirmation", "authority"]
    assert trace.confirmation.call_args.args[0] is receipt
    assert trace.authority.call_args.args[0] is state
    assert trace.authority.call_args.kwargs == {
        "path": bot.STATE_FILE, "recover_pending_identity": True,
    }
    assert trace.authority.call_args.kwargs["path"] is bot.STATE_FILE

    trace.reset_mock()
    trace.authority.return_value = (True, False)
    with pytest.raises(bot.InvalidConfirmedReplyReceipt, match="different completed repair"):
        bot.apply_confirmed_reply_receipt(state, receipt)
    assert [entry[0] for entry in trace.mock_calls] == ["confirmation", "authority", "advance"]
    trace.advance.assert_called_once_with(state, receipt["daily_reply_date"], include_quote_lane=False)
    assert state["daily_reply_date"] == receipt["daily_reply_date"]
    assert state["daily_reply_count"] == 0
    assert state["daily_replied_author_ids"] == []
    assert state["daily_replied_author_counts"] == {}
    assert state["clarification_reply_records"] == before["clarification_reply_records"]
    assert state["own_auto_reply_ids"] == before["own_auto_reply_ids"]


def test_application_keeps_queue_draft_pagination_and_cache_reference_order(monkeypatch):
    receipt = unit_confirmed_v4_reply_receipt(target_id="105")
    state = bot.default_state()
    queue_active_mention(state, mention(105, 200), base_since_id="99")
    sibling = mention(104, 201)
    pending = state["mention_pending_candidates"]
    pending["104"] = sibling
    pagination = state["mention_pagination"]
    receipt["mention_pagination"] = pagination
    sibling_draft = {"reply_text": "Retain this other draft."}
    drafts = {
        **{f"{lane}:105": receipt["ai_reply_draft"] for lane in bot.CONVERSATIONAL_REPLY_HISTORY_LANES},
        "mention:104": sibling_draft,
    }
    state["pending_ai_reply_drafts"] = drafts
    own_ids = [str(10_000 + number) for number in range(1000)]
    state["own_auto_reply_ids"] = own_ids
    # The original broad catch still handles malformed previous local time.
    state["last_reply_epoch"] = object()
    trace = Mock()
    for label, name in (
        ("authority", "validate_pending_mention_candidate_authority"),
        ("ownership", "mention_pagination_has_canonical_page_ownership"),
        ("clear", "clear_pending_ai_reply"),
        ("remove", "remove_pending_mention_candidate"),
        ("event", "log_event"),
    ):
        callback = Mock(wraps=getattr(bot, name))
        trace.attach_mock(callback, label)
        monkeypatch.setattr(bot, name, callback)
    watermark = Mock(side_effect=AssertionError("continuation must delay the watermark"))
    monkeypatch.setattr(bot, "update_last_seen_mention_id", watermark)
    current_datetime = Mock(wraps=bot.datetime)
    current_datetime.fromtimestamp.return_value.isoformat.return_value = "ambient-confirmed-time"
    monkeypatch.setattr(bot, "datetime", current_datetime)
    monkeypatch.setattr(bot, "MY_USER_ID", "current-account")

    def cache(actual_state, **kwargs):
        assert actual_state is state
        assert state["last_reply_epoch"] == receipt["confirmation_epoch"]
        assert "105" not in state["mention_pending_candidates"]
        assert state["mention_pending_candidates"]["104"] is sibling
        assert "mention:105" not in state["pending_ai_reply_drafts"]
        assert state["pending_ai_reply_drafts"]["mention:104"] is sibling_draft
        assert state["own_auto_reply_ids"] == own_ids[1:] + ["999"]
        assert state["mention_pagination"] == pagination
        assert state["mention_pagination"] is not pagination
        assert state["last_seen_mention_id"] == "99"
        assert kwargs == {
            "tweet_id": "999", "text": str(receipt["reply_text"]),
            "author_id": "current-account", "conversation_id": "105",
            "referenced_tweets": [{"type": "replied_to", "id": "105"}],
            "created_at": "ambient-confirmed-time", "post_type": "auto_reply",
        }

    trace.cache = Mock(side_effect=cache)
    monkeypatch.setattr(bot, "cache_tweet", trace.cache)
    bot.apply_confirmed_reply_receipt(state, receipt)
    assert [entry[0] for entry in trace.mock_calls] == [
        "authority", "ownership", "clear", "clear", "clear", "clear", "remove", "cache", "event",
    ]
    assert trace.ownership.call_args.args[0] is state
    assert trace.ownership.call_args.args[1] is pagination
    assert trace.ownership.call_args.kwargs == {"target_id": "105"}
    assert {entry.args[2] for entry in trace.clear.call_args_list} == bot.CONVERSATIONAL_REPLY_HISTORY_LANES
    assert all(entry.args[:2] == (state, "105") for entry in trace.clear.call_args_list)
    assert drafts == {"mention:104": sibling_draft}
    assert trace.remove.call_args.args == (state, "105")
    assert "105" in pending and "mention:105" not in drafts
    assert state["mention_pending_candidates"] is not pending
    assert state["pending_ai_reply_drafts"] is drafts
    current_datetime.fromtimestamp.assert_called_once_with(receipt["confirmation_epoch"])
    current_datetime.fromtimestamp.return_value.isoformat.assert_called_once_with()
    watermark.assert_not_called()


@pytest.mark.parametrize("schema", [2, 4])
def test_history_keeps_expansion_order_strict_epochs_sort_cap_and_references(monkeypatch, schema):
    receipt = (unit_confirmed_reply_receipt() if schema == 2 else unit_confirmed_v4_reply_receipt())
    epoch = receipt["reply_epoch"]
    state = bot.default_state()
    draft = receipt["ai_reply_draft"]
    draft.update(target_id="draft-target", reply_epoch=epoch + 1,
                 attempt_epoch=-1, confirmation_epoch=-2)
    visible = receipt["reply_context"]["visible_conversation"]
    visible[-1]["text"] = "  last visible contribution  " if schema == 2 else "  "
    receipt["reply_context"]["incoming_contribution"] = "  fallback contribution  "
    retained = {"target_id": "101", "reply_post_id": "10", "reply_epoch": epoch}
    fallback = {"target_id": "102", "reply_post_id": "invalid", "reply_epoch": epoch}
    numeric = {"target_id": "103", "reply_post_id": "9", "reply_epoch": epoch}
    stale = {"target_id": "104", "reply_post_id": "11", "reply_epoch": epoch - 1}
    duplicate_reply = {"target_id": "105", "reply_post_id": "999", "reply_epoch": epoch}
    duplicate_target = {"target_id": "100", "reply_post_id": "12", "reply_epoch": epoch}
    noninteger = {"target_id": "106", "reply_post_id": "13", "reply_epoch": float(epoch)}
    history = [retained, duplicate_reply, stale, noninteger, fallback, duplicate_target, numeric]
    state["ai_reply_history"] = history
    clock = Mock(return_value=epoch + 10)
    event = Mock()
    monkeypatch.setattr(bot, "now_epoch", clock)
    monkeypatch.setattr(bot, "AI_REPLY_HISTORY_MAX_AGE_SECONDS", 10)
    monkeypatch.setattr(bot, "AI_REPLY_HISTORY_MAX_RECORDS", 4)
    monkeypatch.setattr(bot, "log_event", event)
    bot.apply_confirmed_reply_receipt(state, receipt)
    result = state["ai_reply_history"]
    assert result is not history and len(history) == 7
    assert all(actual is expected for actual, expected in zip(result[:3], [fallback, numeric, retained]))
    record = result[-1]
    assert record["target_id"] == "draft-target" and record["reply_epoch"] == epoch + 1
    assert record["incoming_contribution"] == (
        "last visible contribution" if schema == 2 else "fallback contribution"
    )
    assert record["used_fact_ids"] is draft["used_fact_ids"]
    assert record["attempt_epoch"] == (-1 if schema == 2 else receipt["attempt_epoch"])
    assert record["confirmation_epoch"] == (-2 if schema == 2 else epoch)
    assert event.call_args == call(
        "single_call_reply_posting_outcome", status="confirmed", lane="mention",
        target_id="100", reply_post_id="999", strategy_version=draft.get("strategy_version"),
        reply_kind=draft.get("reply_kind"), reason_code=draft.get("reason_code"),
        used_fact_count=len(draft.get("used_fact_ids") or []),
        supplied_image_count=len(draft.get("supplied_images") or []),
        model_call_count=draft.get("model_call_count"),
        validated_draft_hash=draft.get("validated_draft_hash"), failure_reason="",
    )
    assert clock.called
    monkeypatch.setattr(bot, "AI_REPLY_HISTORY_MAX_RECORDS", 2)
    bot.apply_confirmed_reply_receipt(state, receipt)
    assert len(state["ai_reply_history"]) == 2
    assert state["ai_reply_history"][0] is retained


def test_clarification_ledger_copies_outer_mapping_once_and_preserves_records(monkeypatch):
    receipt = unit_confirmed_reply_receipt(conversation_id="700")
    receipt["clarification_reply"] = {
        "thread_id": "700", "prior_bot_reply_id": "900",
        "original_question_id": "100", "trigger": "explicit_correction",
    }
    state = bot.default_state()
    existing = {"reply_post_id": "800"}
    ledger = {"600": existing}
    state["clarification_reply_records"] = ledger
    event = Mock()
    monkeypatch.setattr(bot, "log_event", event)
    bot.apply_confirmed_reply_receipt(state, receipt)
    current = state["clarification_reply_records"]
    completed = current["700"]
    assert current is not ledger and current["600"] is existing
    assert list(ledger) == ["600"]
    assert completed["thread_terminal"] is True
    assert event.call_args_list[-2:] == [
        call("clarification_reply_used", thread_id="700", author_id="200",
             target_id="100", reply_post_id="999", trigger="explicit_correction"),
        call("repair_reply_completed", thread_id="700", author_id="200",
             target_id="100", reply_post_id="999"),
    ]
    event.reset_mock()
    bot.apply_confirmed_reply_receipt(state, receipt)
    assert state["clarification_reply_records"] is current
    assert current["700"] is completed
    assert [entry.args[0] for entry in event.call_args_list] == ["single_call_reply_posting_outcome"]


@pytest.mark.parametrize("failure_stage", [None, "save", "retire", "remove"])
def test_reconciliation_preserves_commit_order_references_and_failure_causes(monkeypatch, failure_stage):
    receipt = unit_confirmed_reply_receipt()
    state = {}
    trace = Mock()
    trace.load.return_value = ("valid", receipt)
    failure = OSError("injected local failure")

    class CurrentPersistenceError(RuntimeError):
        pass

    def apply(actual_state, actual_receipt):
        assert actual_state is state and actual_receipt is receipt
        state["applied"] = True

    trace.apply.side_effect = apply
    for label, name in (
        ("load", "load_confirmed_reply_receipt"),
        ("lineage", "verify_lane_transport_source_lineage_if_present"),
        ("apply", "apply_confirmed_reply_receipt"),
        ("save", "save_state"),
        ("retire", "retire_lane_transport_journal_if_present"),
        ("remove", "remove_confirmed_reply_receipt"),
        ("log", "log"),
    ):
        monkeypatch.setattr(bot, name, getattr(trace, label))
    monkeypatch.setattr(bot, "ConfirmedReplyLocalPersistenceError", CurrentPersistenceError)
    if failure_stage:
        getattr(trace, failure_stage).side_effect = failure
        message = (
            "Confirmed reply receipt reconciliation state save failed"
            if failure_stage == "save" else "Confirmed reply receipt removal failed"
        )
        with pytest.raises(CurrentPersistenceError, match=f"^{message}$") as caught:
            bot.reconcile_confirmed_reply_receipt(state)
        assert caught.value.__cause__ is failure
    else:
        assert bot.reconcile_confirmed_reply_receipt(state) is True
    order = ["load", "lineage", "log.warning", "apply", "save", "retire", "remove"]
    if failure_stage:
        order = order[:order.index(failure_stage) + 1] + ["log.critical"]
        expected_log = (
            "Confirmed reply receipt was applied in memory but state save failed; receipt remains for retry"
            if failure_stage == "save" else
            "Confirmed reply receipt state was saved but receipt removal failed"
        )
        trace.log.critical.assert_called_once_with(expected_log, exc_info=True)
    assert [entry[0] for entry in trace.mock_calls] == order
    assert state == {"applied": True}
    trace.load.assert_called_once_with()
    for callback in (trace.lineage, trace.retire):
        if callback.called:
            assert callback.call_args == call(
                receipt_path=bot.CONFIRMED_REPLY_RECEIPT_FILE, receipt=receipt,
                lane="conversational_reply", post_id="999",
            )
            assert callback.call_args.kwargs["receipt"] is receipt
            assert callback.call_args.kwargs["receipt_path"] is bot.CONFIRMED_REPLY_RECEIPT_FILE
    trace.save.assert_called_once_with(state, durable=True)
    assert trace.save.call_args.args[0] is state
    if trace.remove.called:
        trace.remove.assert_called_once_with(receipt)
        assert trace.remove.call_args.args[0] is receipt


@pytest.mark.parametrize("lane", ["mention", "hot_post_reply", "quote_tweet"])
def test_emergency_completeness_uses_current_lane_and_pending_key(monkeypatch, lane):
    receipt = unit_confirmed_v4_reply_receipt(lane=lane)
    state = bot.default_state()
    assert not bot.confirmed_reply_emergency_representation_is_complete(receipt, state)
    bot.apply_confirmed_reply_receipt(state, receipt)
    assert bot.confirmed_reply_emergency_representation_is_complete(receipt, state)
    pending_key = Mock(return_value="current-pending-key")
    monkeypatch.setattr(bot, "pending_ai_reply_draft_key", pending_key)
    state["pending_ai_reply_drafts"] = {"current-pending-key": {}}
    assert not bot.confirmed_reply_emergency_representation_is_complete(receipt, state)
    assert {entry.args for entry in pending_key.call_args_list} == {
        ("100", source) for source in bot.CONVERSATIONAL_REPLY_HISTORY_LANES
    }
    state["pending_ai_reply_drafts"] = {}
    if lane == "quote_tweet":
        state["seen_quote_post_ids"] = []
        assert not bot.confirmed_reply_emergency_representation_is_complete(receipt, state)
    else:
        assert bot.confirmed_reply_emergency_representation_is_complete(receipt, state)


def test_emergency_catches_only_current_confirmation_error_inside_its_try(monkeypatch):
    receipt = unit_confirmed_reply_receipt()
    state = bot.default_state()
    trace = Mock()
    trace.validate.return_value = False
    monkeypatch.setattr(bot, "confirmed_reply_receipt_is_semantically_valid", trace.validate)
    monkeypatch.setattr(bot, "conversational_reply_confirmation_epoch", trace.confirmation)

    class CurrentInvalidReceipt(ValueError):
        pass

    monkeypatch.setattr(bot, "InvalidConfirmedReplyReceipt", CurrentInvalidReceipt)
    assert not bot.confirmed_reply_emergency_representation_is_complete(receipt, state)
    trace.validate.assert_called_once_with(receipt)
    assert trace.validate.call_args.args[0] is receipt
    trace.confirmation.assert_not_called()
    trace.validate.return_value = True
    invalid = CurrentInvalidReceipt("current confirmation failed")
    trace.confirmation.side_effect = invalid
    assert not bot.confirmed_reply_emergency_representation_is_complete(receipt, state)
    assert trace.confirmation.call_args.args[0] is receipt
    unexpected = TypeError("unexpected confirmation failure")
    trace.confirmation.side_effect = unexpected
    with pytest.raises(TypeError) as caught:
        bot.confirmed_reply_emergency_representation_is_complete(receipt, state)
    assert caught.value is unexpected
    trace.validate.side_effect = invalid
    with pytest.raises(CurrentInvalidReceipt) as caught:
        bot.confirmed_reply_emergency_representation_is_complete(receipt, state)
    assert caught.value is invalid
    trace.validate.side_effect = None
    trace.confirmation.side_effect = None
    trace.confirmation.return_value = receipt["reply_epoch"]
    monkeypatch.setattr(bot, "receipt_int", Mock(side_effect=invalid))
    with pytest.raises(CurrentInvalidReceipt) as caught:
        bot.confirmed_reply_emergency_representation_is_complete(receipt, state)
    assert caught.value is invalid
