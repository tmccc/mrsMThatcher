from __future__ import annotations

import copy
import inspect
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock, call

import pytest

from tests.helpers.adapter_assertions import assert_adapters_forward_current_dependencies

import mrs_bot_reply_reconciliation as reconciliation
import mrs_bot_reply_history as reply_history
import mrs_bot_reply_drafts as reply_drafts
import mrs_bot_reply_clarifications as reply_clarifications
import mrs_bot_daily_reply_accounting as daily_accounting
from tests.helpers.mention_fixtures import mention, queue_active_mention
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401
from tests.helpers.reply_fixtures import (
    patch_reply_history_method,
    patch_reply_draft_method,
    unit_confirmed_reply_receipt,
    unit_confirmed_v4_reply_receipt,
)


def patch_accounting_method(monkeypatch, method, callback):
    """Observe an owned accounting operation without changing callback arguments."""
    def invoke(_owner, *args, **kwargs):
        return callback(*args, **kwargs)

    monkeypatch.setattr(daily_accounting.DailyReplyAccounting, method, invoke)


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
        "reconcile_confirmed_reply_receipt",
    )
    assert_adapters_forward_current_dependencies(
        monkeypatch, bot=bot, implementation=reconciliation, names=names,
    )


def test_application_adapter_binds_current_owners_and_preserves_other_dependencies(monkeypatch):
    adapter = bot.apply_confirmed_reply_receipt
    public = inspect.signature(adapter).parameters
    parameters = inspect.signature(reconciliation.apply_confirmed_reply_receipt).parameters
    assert tuple(public) == ("state", "receipt")
    assert all(parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD for parameter in public.values())
    assert {
        "now_epoch", "AI_REPLY_HISTORY_MAX_AGE_SECONDS", "valid_string_post_id",
        "AI_REPLY_HISTORY_MAX_RECORDS", "_advance_reply_counters_to_confirmation_date",
        "mark_daily_author_replied",
    }.isdisjoint(parameters)
    assert {"clarifications", "accounting"} <= parameters.keys()
    assert "clear_pending_ai_reply" not in parameters
    dependencies = parameters.keys() - public.keys() - {"record_reply_history", "clarifications", "accounting", "clear_target_drafts"}
    implementation = Mock(return_value=object())
    history_factory = Mock(wraps=bot._reply_history_owner)
    clarification_factory = Mock(wraps=bot._clarification_reply_owner)
    accounting_factory = Mock(wraps=bot._daily_reply_accounting_owner)
    draft_factory = Mock(wraps=bot._reply_draft_owner)
    monkeypatch.setattr(reconciliation, "apply_confirmed_reply_receipt", implementation)
    monkeypatch.setattr(bot, "_reply_history_owner", history_factory)
    monkeypatch.setattr(bot, "_clarification_reply_owner", clarification_factory)
    monkeypatch.setattr(bot, "_daily_reply_accounting_owner", accounting_factory)
    monkeypatch.setattr(bot, "_reply_draft_owner", draft_factory)
    state, receipt, histories, clarification_owners, accounting_owners, draft_owners = {}, {}, [], [], [], []
    for index in range(2):
        current = {name: object() for name in dependencies}
        for name, value in current.items():
            monkeypatch.setattr(bot, name, value)
        clock = Mock()
        monkeypatch.setattr(bot, "now_epoch", clock)
        monkeypatch.setattr(bot, "AI_REPLY_HISTORY_MAX_AGE_SECONDS", 100 + index)
        monkeypatch.setattr(bot, "AI_REPLY_HISTORY_MAX_RECORDS", 10 + index)
        monkeypatch.setattr(bot, "CLARIFICATION_REPLY_WINDOW_SECONDS", 1000 + index)
        assert adapter(state, receipt) is implementation.return_value
        assert history_factory.call_count == index + 1
        assert clarification_factory.call_count == index + 1
        assert accounting_factory.call_count == index + 1
        assert draft_factory.call_count == index + 1
        args, supplied = implementation.call_args
        assert len(args) == 2 and args[0] is state and args[1] is receipt
        assert supplied.keys() == {*current, "record_reply_history", "clarifications", "accounting", "clear_target_drafts"}
        assert all(supplied[name] is value for name, value in current.items())
        callback = supplied["record_reply_history"]
        assert callback.__func__ is reply_history.ReplyHistory.record_confirmation
        history = callback.__self__
        assert history.now_epoch is clock
        assert history.maximum_age_seconds == 100 + index
        assert history.maximum_records == 10 + index
        clarification_owner = supplied["clarifications"]
        assert isinstance(clarification_owner, reply_clarifications.ClarificationReplies)
        assert clarification_owner.invalid_receipt is current["InvalidConfirmedReplyReceipt"]
        assert clarification_owner.log_event is current["log_event"]
        assert clarification_owner.window_seconds == 1000 + index
        accounting_owner = supplied["accounting"]
        assert isinstance(accounting_owner, daily_accounting.DailyReplyAccounting)
        for field in ("datetime", "log", "reply_cap_date_str", "append_unique_capped"):
            assert getattr(accounting_owner, field) is current[field]
        accounting_owners.append(accounting_owner)
        draft_callback = supplied["clear_target_drafts"]
        assert draft_callback.__func__ is reply_drafts.ReplyDrafts.clear_target
        assert draft_callback.__self__.log_event is current["log_event"]
        draft_owners.append(draft_callback.__self__)
        clock.assert_not_called()
        histories.append(history)
        clarification_owners.append(clarification_owner)
    assert histories[0] is not histories[1]
    assert histories[0].now_epoch is not histories[1].now_epoch
    assert clarification_owners[0] is not clarification_owners[1]
    assert clarification_owners[0].log_event is not clarification_owners[1].log_event
    assert clarification_owners[0].window_seconds == 1000
    assert accounting_owners[0] is not accounting_owners[1]
    assert accounting_owners[0].reply_cap_date_str is not accounting_owners[1].reply_cap_date_str
    assert draft_owners[0] is not draft_owners[1]
    failure = TypeError("current application failure")
    implementation.side_effect = failure
    with pytest.raises(TypeError) as caught:
        adapter(state, receipt)
    assert caught.value is failure


def test_emergency_adapter_uses_current_draft_owner_and_preserves_arguments_and_errors(monkeypatch):
    adapter = bot.confirmed_reply_emergency_representation_is_complete
    implementation = Mock(return_value=object())
    parameters = inspect.signature(reconciliation.confirmed_reply_emergency_representation_is_complete).parameters
    assert "pending_ai_reply_draft_key" not in parameters
    dependencies = parameters.keys() - {"receipt", "state", "has_target_draft"}
    monkeypatch.setattr(reconciliation, "confirmed_reply_emergency_representation_is_complete", implementation)
    factory = Mock(wraps=bot._reply_draft_owner)
    monkeypatch.setattr(bot, "_reply_draft_owner", factory)
    receipt, state, owners = {}, {}, []
    for index in range(2):
        current = {name: object() for name in dependencies}
        for name, value in current.items():
            monkeypatch.setattr(bot, name, value)
        evidence = Mock()
        monkeypatch.setattr(bot, "reply_evidence_repository", evidence)
        assert adapter(receipt, state) is implementation.return_value
        assert factory.call_count == index + 1
        args, supplied = implementation.call_args
        assert args[0] is receipt and args[1] is state
        assert supplied.keys() == {*current, "has_target_draft"}
        assert all(supplied[name] is value for name, value in current.items())
        callback = supplied["has_target_draft"]
        assert callback.__func__ is reply_drafts.ReplyDrafts.has_target
        assert callback.__self__.evidence_repository is evidence
        evidence.assert_not_called()
        owners.append(callback.__self__)
    assert owners[0] is not owners[1]
    failure = TypeError("current emergency check failure")
    implementation.side_effect = failure
    with pytest.raises(TypeError) as caught:
        adapter(receipt, state)
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
        ("cache", "cache_tweet"),
    ):
        callback = Mock(wraps=getattr(bot, name))
        trace.attach_mock(callback, label)
        monkeypatch.setattr(bot, name, callback)
    trace.clear = Mock(wraps=bot._reply_draft_owner().clear_target)
    patch_reply_draft_method(monkeypatch, "clear_target", trace.clear)
    trace.advance = Mock(wraps=bot._daily_reply_accounting_owner().advance)
    patch_accounting_method(monkeypatch, "advance", trace.advance)
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


@pytest.mark.parametrize("lane", ["mention", "quote_tweet"])
@pytest.mark.parametrize("version,failure_stage", [(2, None), (4, None), (4, "advance"), (4, "record")])
def test_accounting_calls_preserve_positions_snapshots_and_native_failures(monkeypatch, lane, version, failure_stage):
    receipt = (unit_confirmed_reply_receipt(lane=lane) if version == 2
               else unit_confirmed_v4_reply_receipt(lane=lane))
    target_id, reply_post_id = receipt["target_id"], receipt["reply_post_id"]
    author_id = receipt["author_id"]
    reply_date = receipt["daily_reply_date"]
    quote_date = str(receipt.get("daily_quote_reply_date") or reply_date)
    state = bot.default_state()
    already_recorded = lane == "quote_tweet"
    if already_recorded:
        state["replied_to_quote_post_ids"] = [target_id]
    owner = Mock(spec=daily_accounting.DailyReplyAccounting)
    factory = Mock(return_value=owner)
    monkeypatch.setattr(bot, "_daily_reply_accounting_owner", factory)
    trace = Mock()
    trace.attach_mock(owner.advance, "advance")
    trace.attach_mock(owner.record_confirmed, "record")
    original_append = bot.append_unique_capped

    def append_ids(values, value, cap):
        if value == reply_post_id:
            trace.identities()
            receipt.update(author_id="changed", candidate_source="hot_post_reply",
                           daily_reply_date="changed", daily_quote_reply_date="changed")
        return original_append(values, value, cap)

    monkeypatch.setattr(bot, "append_unique_capped", append_ids)
    trace.cache = Mock(wraps=bot.cache_tweet)
    monkeypatch.setattr(bot, "cache_tweet", trace.cache)
    monkeypatch.setattr(bot, "log_event", trace.event)
    failure = TypeError("accounting operation failed")
    if failure_stage:
        getattr(trace, failure_stage).side_effect = failure
        with pytest.raises(TypeError) as caught:
            bot.apply_confirmed_reply_receipt(state, receipt)
        assert caught.value is failure
    else:
        bot.apply_confirmed_reply_receipt(state, receipt)
    factory.assert_called_once_with()
    order = (["advance"] if version == 4 else []) + ["identities", "record", "cache", "event"]
    if failure_stage:
        order = order[:order.index(failure_stage) + 1]
    assert [entry[0] for entry in trace.mock_calls] == order
    if version == 4:
        owner.advance.assert_called_once_with(state, reply_date, include_quote_lane=lane == "quote_tweet")
        assert owner.advance.call_args.args[0] is state
    else:
        owner.advance.assert_not_called()
    if failure_stage != "advance":
        owner.record_confirmed.assert_called_once_with(
            state, already_recorded=already_recorded, candidate_source=lane,
            author_id=author_id, receipt_reply_date=reply_date,
            receipt_quote_reply_date=quote_date,
        )
        assert owner.record_confirmed.call_args.args[0] is state
        assert reply_post_id in state["own_auto_reply_ids"]
    else:
        owner.record_confirmed.assert_not_called()


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
        ("remove", "remove_pending_mention_candidate"),
        ("event", "log_event"),
    ):
        callback = Mock(wraps=getattr(bot, name))
        trace.attach_mock(callback, label)
        monkeypatch.setattr(bot, name, callback)
    trace.clear = Mock(wraps=bot._reply_draft_owner().clear_target)
    patch_reply_draft_method(monkeypatch, "clear_target", trace.clear)
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
    trace.history = Mock(wraps=bot._reply_history_owner().record_confirmation)
    patch_reply_history_method(monkeypatch, "record_confirmation", trace.history)
    bot.apply_confirmed_reply_receipt(state, receipt)
    assert [entry[0] for entry in trace.mock_calls] == [
        "authority", "ownership", "clear", "remove", "cache", "history", "event",
    ]
    history_args, history_options = trace.history.call_args
    assert len(history_args) == 3
    assert history_args[0] is state and history_args[1] is receipt
    assert history_args[2] is receipt["ai_reply_draft"]
    assert history_options == {
        "target_id": "105", "reply_post_id": "999", "author_id": "200",
        "conversation_id": "105", "candidate_source": "mention",
        "reply_epoch": receipt["confirmation_epoch"],
    }
    assert trace.ownership.call_args.args[0] is state
    assert trace.ownership.call_args.args[1] is pagination
    assert trace.ownership.call_args.kwargs == {"target_id": "105"}
    trace.clear.assert_called_once_with(state, "105", "mention")
    assert trace.clear.call_args.args[0] is state
    assert drafts == {"mention:104": sibling_draft}
    assert trace.remove.call_args.args == (state, "105")
    assert "105" in pending and "mention:105" not in drafts
    assert state["mention_pending_candidates"] is not pending
    assert state["pending_ai_reply_drafts"] is drafts
    current_datetime.fromtimestamp.assert_called_once_with(receipt["confirmation_epoch"])
    current_datetime.fromtimestamp.return_value.isoformat.assert_called_once_with()
    watermark.assert_not_called()


@pytest.mark.parametrize("draft", [None, []])
def test_application_skips_history_recording_for_non_mapping_drafts(monkeypatch, draft):
    receipt = unit_confirmed_reply_receipt()
    receipt["ai_reply_draft"] = draft
    record = Mock(side_effect=AssertionError("non-mapping draft must not enter history"))
    patch_reply_history_method(monkeypatch, "record_confirmation", record)
    state = bot.default_state()
    history = state["ai_reply_history"]

    bot.apply_confirmed_reply_receipt(state, receipt)

    record.assert_not_called()
    assert state["ai_reply_history"] is history and history == []
    assert receipt["reply_post_id"] in state["tweet_cache"]


def test_history_recording_error_propagates_after_cache_before_telemetry(monkeypatch):
    receipt = unit_confirmed_reply_receipt()
    state = bot.default_state()
    history = state["ai_reply_history"]
    trace = Mock()
    trace.cache = Mock(wraps=bot.cache_tweet)
    failure = TypeError("history recording failed")
    trace.history.side_effect = failure
    patch_reply_history_method(monkeypatch, "record_confirmation", trace.history)
    monkeypatch.setattr(bot, "cache_tweet", trace.cache)
    monkeypatch.setattr(bot, "log_event", trace.event)

    with pytest.raises(TypeError) as caught:
        bot.apply_confirmed_reply_receipt(state, receipt)

    assert caught.value is failure
    assert [entry[0] for entry in trace.mock_calls] == ["cache", "history"]
    assert receipt["reply_post_id"] in state["tweet_cache"]
    assert state["ai_reply_history"] is history and history == []
    trace.event.assert_not_called()


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


@pytest.mark.parametrize("failure_stage", [None, "check", "record"])
def test_clarification_application_keeps_call_order_references_and_resolved_identity(monkeypatch, failure_stage):
    receipt = unit_confirmed_v4_reply_receipt(lane="hot_post_reply", conversation_id="700")
    clarification = {
        "thread_id": "700", "prior_bot_reply_id": "900",
        "original_question_id": "100", "trigger": "explicit_correction",
    }
    receipt["clarification_reply"] = clarification
    state = bot.default_state()
    epoch = receipt["confirmation_epoch"]
    owner = Mock(spec=reply_clarifications.ClarificationReplies)
    factory = Mock(return_value=owner)
    monkeypatch.setattr(bot, "_clarification_reply_owner", factory)
    trace = Mock()
    trace.attach_mock(owner.assert_no_conflict, "check")
    trace.attach_mock(owner.record_completed, "record")
    trace.advance = Mock(wraps=bot._daily_reply_accounting_owner().advance)
    patch_accounting_method(monkeypatch, "advance", trace.advance)
    monkeypatch.setattr(bot, "cache_tweet", trace.cache)
    patch_reply_history_method(monkeypatch, "record_confirmation", trace.history)
    monkeypatch.setattr(bot, "log_event", trace.event)

    def cache(actual_state, **kwargs):
        assert actual_state is state
        assert kwargs["tweet_id"] == "999"
        receipt.update(
            target_id="changed-target", author_id="changed-author", reply_post_id="changed-reply",
            reply_epoch=epoch + 100, confirmation_epoch=epoch + 100,
            clarification_reply={"thread_id": "replacement"},
        )

    trace.cache.side_effect = cache
    failure = TypeError("clarification owner failure")
    if failure_stage:
        getattr(trace, failure_stage).side_effect = failure
        with pytest.raises(TypeError) as caught:
            bot.apply_confirmed_reply_receipt(state, receipt)
        assert caught.value is failure
    else:
        assert bot.apply_confirmed_reply_receipt(state, receipt) is None
    factory.assert_called_once_with()
    order = ["advance", "check", "cache", "history", "event", "record"]
    if failure_stage:
        order = order[:order.index(failure_stage) + 1]
    assert [entry[0] for entry in trace.mock_calls] == order
    check_args, check_options = owner.assert_no_conflict.call_args
    assert len(check_args) == 2 and check_args[0] is state and check_args[1] is clarification
    assert check_options == {"reply_post_id": "999"}
    if failure_stage != "check":
        record_args, record_options = owner.record_completed.call_args
        assert len(record_args) == 2 and record_args[0] is state and record_args[1] is clarification
        assert record_options == {
            "author_id": "200", "target_id": "100", "reply_post_id": "999", "reply_epoch": epoch,
        }
        assert trace.event.call_args.args == ("single_call_reply_posting_outcome",)
        assert trace.event.call_args.kwargs["target_id"] == "100"
        assert trace.event.call_args.kwargs["reply_post_id"] == "999"


@pytest.mark.parametrize("clarification", [None, []])
def test_application_skips_clarification_operations_for_non_mapping_values(monkeypatch, clarification):
    receipt = unit_confirmed_reply_receipt(lane="hot_post_reply")
    receipt["clarification_reply"] = clarification
    owner = Mock(spec=reply_clarifications.ClarificationReplies)
    monkeypatch.setattr(bot, "_clarification_reply_owner", Mock(return_value=owner))
    bot.apply_confirmed_reply_receipt(bot.default_state(), receipt)
    owner.assert_no_conflict.assert_not_called()
    owner.record_completed.assert_not_called()


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
    assert state["applied"] is True
    from mrs_bot_state_generation import canonical_bytes
    import hashlib
    receipt_hash = hashlib.sha256(canonical_bytes(receipt) + b"\n").hexdigest()
    assert state["_confirmed_receipt_commits"] == {
        receipt_hash: {"quote_hash": "", "image_basename": ""}
    }
    trace.load.assert_called_once_with()
    for callback in (trace.lineage, trace.retire):
        if callback.called:
            assert callback.call_args == call(
                receipt_path=bot.CONFIRMED_REPLY_RECEIPT_FILE, receipt=receipt,
                lane="conversational_reply", post_id="999",
                **({"commit_proof": trace.save.return_value} if callback is trace.retire else {}),
            )
            assert callback.call_args.kwargs["receipt"] is receipt
            assert callback.call_args.kwargs["receipt_path"] is bot.CONFIRMED_REPLY_RECEIPT_FILE
    trace.save.assert_called_once_with(state, durable=True)
    assert trace.save.call_args.args[0] is state
    if trace.remove.called:
        trace.remove.assert_called_once_with(receipt, commit_proof=trace.save.return_value)
        assert trace.remove.call_args.args[0] is receipt


@pytest.mark.parametrize("lane", ["mention", "hot_post_reply", "quote_tweet"])
def test_emergency_completeness_uses_current_lane_and_owned_pending_key(monkeypatch, lane):
    receipt = unit_confirmed_v4_reply_receipt(lane=lane)
    state = bot.default_state()
    assert not bot.confirmed_reply_emergency_representation_is_complete(receipt, state)
    bot.apply_confirmed_reply_receipt(state, receipt)
    assert bot.confirmed_reply_emergency_representation_is_complete(receipt, state)
    pending_key = Mock(return_value="current-pending-key")
    monkeypatch.setattr(reply_drafts, "pending_ai_reply_draft_key", pending_key)
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


@pytest.mark.parametrize("legacy", [False, True])
def test_pagination_preservation_resets_missing_ownership_before_copy_and_legacy_warning(legacy):
    trace = Mock()

    class Pagination(dict):
        def __deepcopy__(self, memo):
            trace.copy()
            return copy.deepcopy(dict(self), memo)

    pagination = Pagination(base_since_id="99", next_token="next-page")
    state = {
        "last_seen_mention_id": "99", "mention_pagination": pagination,
        "mention_pending_candidates": {"104": {}, "105": {}},
    }
    receipt = {} if legacy else {"mention_pagination": pagination}
    trace.provenance.return_value = True
    trace.ownership.return_value = False

    def reset(actual_state, *, watermark):
        assert actual_state is state and watermark == "99"
        state["mention_pending_candidates"] = {}
        state["mention_pagination"] = {}

    trace.reset.side_effect = reset
    preserved = reconciliation._mention_pagination_to_preserve(
        state, receipt, target_id="105", candidate_source="mention",
        InvalidConfirmedReplyReceipt=bot.InvalidConfirmedReplyReceipt,
        STATE_FILE=bot.STATE_FILE,
        mention_pagination_provenance_is_valid=trace.provenance,
        mention_pagination_has_canonical_page_ownership=trace.ownership,
        _reset_mention_candidate_authority=trace.reset,
        _emit_mention_authority_recovery=trace.recovery,
        log=trace.log,
    )
    assert [entry[0] for entry in trace.mock_calls] == [
        "provenance", "ownership", "reset", "recovery", "copy",
        *(["log.warning"] if legacy else []),
    ]
    assert trace.provenance.call_args.args[0] is pagination
    assert trace.ownership.call_args.args[0] is state
    assert trace.ownership.call_args.args[1] is pagination
    assert trace.ownership.call_args.kwargs == {"target_id": "105"}
    trace.recovery.assert_called_once_with(
        {"reason": "receipt_page_ownership_missing", "since_id": "99", "discarded_candidates": 2},
        path=bot.STATE_FILE, recovery_events=None,
    )
    assert preserved == pagination and preserved is not pagination
    assert state["mention_pagination"] == {}
