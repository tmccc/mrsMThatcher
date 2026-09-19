"""Exercise confirmed history recording and selection at their shared owner."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import inspect
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock, call

import pytest

import mrs_bot_reply_history as reply_history
import mrs_bot_reply_state as reply_state
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401
from tests.helpers.reply_fixtures import (
    unit_confirmed_reply_receipt,
    unit_confirmed_v4_reply_receipt,
    unit_reply_context,
)


@pytest.fixture
def make_owner():
    """Compose pure history operations with the existing isolated configuration."""
    def build(**overrides):
        options = dict(
            now_epoch=bot.now_epoch,
            valid_string_post_id=bot.valid_string_post_id,
            quoted_post_reference_id=bot.quoted_post_reference_id,
            maximum_state_epoch=bot.MAX_REASONABLE_STATE_EPOCH,
            maximum_recent_replies=bot.MAX_RECENT_ACCOUNT_REPLIES,
            default_recent_reply_limit=inspect.signature(bot.recent_confirmed_account_replies).parameters["limit"].default,
            maximum_same_author_interactions=bot.MAX_SAME_AUTHOR_INTERACTIONS,
            maximum_age_seconds=bot.AI_REPLY_HISTORY_MAX_AGE_SECONDS,
            maximum_records=bot.AI_REPLY_HISTORY_MAX_RECORDS,
        )
        return reply_history.ReplyHistory(**{**options, **overrides})

    return build


@pytest.fixture
def confirmed_row(make_owner):
    receipt = unit_confirmed_reply_receipt()
    state = {}
    make_owner().record_confirmation(
        state, receipt, receipt["ai_reply_draft"], target_id="100", reply_post_id="999",
        author_id="200", conversation_id="100", candidate_source="mention",
        reply_epoch=receipt["reply_epoch"],
    )
    return state["ai_reply_history"][0]


def test_import_needs_no_runtime_access_and_root_aliases_share_owner_objects():
    code = """
import builtins, collections.abc, dataclasses, datetime, hashlib, io, os, random, socket, sys
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('reply history import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'reply_evidence'} or name.startswith('mrs_bot_') and name != 'mrs_bot_reply_history':
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = forbidden
os.getenv = os._Environ.__getitem__ = forbidden
Path.home = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
before = random.getstate()
import mrs_bot_reply_history
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
assert 'single_call_reply' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    for name in ("CONVERSATIONAL_REPLY_HISTORY_LANES", "_confirmed_history_sort_key", "_reply_target_epoch"):
        assert getattr(bot, name) is getattr(reply_history, name)
        assert getattr(reply_state, name) is getattr(reply_history, name)
    assert reply_history.hashlib is bot.hashlib and reply_history.datetime is bot.datetime
    assert reply_history.CONVERSATIONAL_REPLY_HISTORY_LANES == frozenset({
        "mention", "hot_post_reply", "quote_tweet", "conversational_reply",
    })


def test_root_owner_binds_current_dependencies_and_preserves_original_default(monkeypatch):
    names = {
        "now_epoch": "now_epoch", "valid_string_post_id": "valid_string_post_id",
        "quoted_post_reference_id": "quoted_post_reference_id",
        "maximum_state_epoch": "MAX_REASONABLE_STATE_EPOCH",
        "maximum_recent_replies": "MAX_RECENT_ACCOUNT_REPLIES",
        "maximum_same_author_interactions": "MAX_SAME_AUTHOR_INTERACTIONS",
        "maximum_age_seconds": "AI_REPLY_HISTORY_MAX_AGE_SECONDS",
        "maximum_records": "AI_REPLY_HISTORY_MAX_RECORDS",
    }
    default = inspect.signature(bot.recent_confirmed_account_replies).parameters["limit"].default
    snapshots = []
    for _ in range(2):
        current = {field: object() for field in names}
        for field in ("now_epoch", "valid_string_post_id", "quoted_post_reference_id"):
            current[field] = Mock()
        for field, root_name in names.items():
            monkeypatch.setattr(bot, root_name, current[field])
        owner = bot._reply_history_owner()
        assert isinstance(owner, reply_history.ReplyHistory)
        assert all(getattr(owner, field) is value for field, value in current.items())
        assert owner.default_recent_reply_limit == default
        for field in ("now_epoch", "valid_string_post_id", "quoted_post_reference_id"):
            current[field].assert_not_called()
        snapshots.append((owner, current))
    first, first_inputs = snapshots[0]
    assert all(getattr(first, field) is value for field, value in first_inputs.items())
    assert first is not snapshots[1][0]
    with pytest.raises(FrozenInstanceError):
        first.maximum_records = 1


def test_root_adapters_preserve_arguments_result_identity_and_errors(monkeypatch):
    methods = {
        "_confirmed_conversational_history_rows": "confirmed_rows",
        "recent_confirmed_account_replies": "recent_replies",
        "_reply_context_history_excluded_post_ids": "context_excluded_post_ids",
        "recovery_comparison_account_replies": "recovery_replies",
        "_same_author_confirmed_history_rows": "same_author_rows",
        "recent_same_author_account_interactions": "recent_same_author_interactions",
    }
    for root_name, method_name in methods.items():
        adapter = getattr(bot, root_name)
        public = inspect.signature(adapter).parameters
        args = tuple(object() for parameter in public.values()
                     if parameter.kind == inspect.Parameter.POSITIONAL_OR_KEYWORD)
        options = {name: object() for name, parameter in public.items()
                   if parameter.kind == inspect.Parameter.KEYWORD_ONLY}
        for _ in range(2):
            owner = Mock(spec=reply_history.ReplyHistory)
            factory = Mock(return_value=owner)
            monkeypatch.setattr(bot, "_reply_history_owner", factory)
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


def test_recent_default_is_fixed_while_body_reads_current_cap(confirmed_row, monkeypatch, make_owner):
    default = inspect.signature(bot.recent_confirmed_account_replies).parameters["limit"].default
    assert default == bot.MAX_RECENT_ACCOUNT_REPLIES
    assert inspect.signature(reply_history.ReplyHistory.recent_replies).parameters["limit"].default is inspect.Parameter.empty
    count = default + 5
    state = {"ai_reply_history": [
        {**confirmed_row, "reply_post_id": str(index), "reply_epoch": index}
        for index in range(1, count + 1)
    ]}
    owner = make_owner(maximum_recent_replies=count)
    assert len(owner.recent_replies(state, default, before_epoch=count + 1)) == default
    assert len(owner.recent_replies(state, count, before_epoch=count + 1)) == count
    assert owner.recent_replies(state, count) == []
    limited = replace(owner, maximum_recent_replies=2)
    assert [row["post_id"] for row in limited.recent_replies(state, count, before_epoch=count + 1)] == [str(count - 1), str(count)]
    assert limited.recent_replies(state, 0, before_epoch=count + 1) == []
    monkeypatch.setattr(bot, "MAX_RECENT_ACCOUNT_REPLIES", count)
    assert len(bot.recent_confirmed_account_replies(state, before_epoch=count + 1)) == default
    monkeypatch.setattr(bot, "MAX_RECENT_ACCOUNT_REPLIES", 2)
    assert len(bot.recent_confirmed_account_replies(state, before_epoch=count + 1)) == 2
    assert inspect.signature(bot.recent_confirmed_account_replies).parameters["limit"].default == default


def test_confirmed_rows_keep_references_and_lane_id_and_epoch_rules(confirmed_row, make_owner):
    state = {"ai_reply_history": [confirmed_row]}
    owner = make_owner()
    assert owner.confirmed_rows(state)[0] is confirmed_row
    confirmed_row["candidate_source"] = "unconfirmed-lane"
    assert owner.confirmed_rows(state) == []
    confirmed_row["candidate_source"] = "mention"
    confirmed_row["target_id"] = "current-target"
    valid_id = Mock(return_value=True)
    owner = make_owner(valid_string_post_id=valid_id)
    assert owner.confirmed_rows(state)[0] is confirmed_row
    assert valid_id.call_args_list == [call("current-target"), call(confirmed_row["reply_post_id"])]
    assert replace(owner, maximum_state_epoch=confirmed_row["reply_epoch"] - 1).confirmed_rows(state) == []


def test_same_author_keeps_stable_numeric_order_row_identity_and_zero_cap_slice(confirmed_row, make_owner):
    first = {**confirmed_row, "reply_post_id": "2", "reply_epoch": 10}
    second = {**confirmed_row, "reply_post_id": "10", "reply_epoch": 10}
    replacement = {**first, "proposed_reply": " Replacement. "}
    replacement["incoming_contribution"] = " " + confirmed_row["incoming_contribution"] + " "
    state = {"ai_reply_history": [second, first, replacement]}
    owner = make_owner(maximum_age_seconds=1)
    options = dict(author_id="200", current_thread_post_ids=set(), target_id="500", before_epoch=11)
    rows = owner.same_author_rows(state, **options)
    assert len(rows) == 2 and rows[0] is replacement and rows[1] is second
    assert owner.same_author_rows(state, **{**options, "before_epoch": 10}) == []
    owner = replace(owner, maximum_same_author_interactions=0)
    rows = owner.same_author_rows(state, **options)
    assert len(rows) == 2 and rows[0] is replacement and rows[1] is second
    interactions = owner.recent_same_author_interactions(
        state, author_id="200", conversation_id="500", target_id="500", before_epoch=11,
    )
    assert interactions == [
        {"contributor": confirmed_row["incoming_contribution"], "account_reply": "Replacement."},
        {"contributor": confirmed_row["incoming_contribution"], "account_reply": second["proposed_reply"]},
    ]


def test_recovery_merge_keeps_string_ties_whitespace_and_current_bounded_clock(confirmed_row, make_owner):
    state = {"ai_reply_history": [
        {**confirmed_row, "reply_post_id": "2", "reply_epoch": 100, "proposed_reply": " Two. "},
        {**confirmed_row, "reply_post_id": "10", "reply_epoch": 100, "proposed_reply": " Ten. "},
        {**confirmed_row, "reply_post_id": "11", "reply_epoch": 101, "proposed_reply": " Future. "},
    ]}
    clock = Mock(return_value=1000)
    owner = make_owner(now_epoch=clock, maximum_state_epoch=100, maximum_recent_replies=1)
    assert owner.recovery_replies(state, context=unit_reply_context(target_id="500")) == [
        {"post_id": "10", "text": "Ten."}, {"post_id": "2", "text": " Two. "},
    ]
    clock.assert_called_once_with()


def test_context_exclusions_use_supplied_quoted_reference_and_original_context(make_owner):
    context = unit_reply_context()
    context.update(thread_id=300, root_post_id=400)
    context["visible_conversation"].extend([None, {"post_id": 200}, {"post_id": ""}])
    for quoted_id in ("500", None):
        quoted = Mock(return_value=quoted_id)
        owner = make_owner(quoted_post_reference_id=quoted)
        assert owner.context_excluded_post_ids(context) == (
            {"100", "200", "300", "400"} | ({quoted_id} if quoted_id else set())
        )
        assert quoted.call_args.args[0] is context


def test_target_epoch_keeps_timezone_requirement_and_native_timestamp_errors(monkeypatch):
    context = unit_reply_context()
    assert reply_history._reply_target_epoch(context) == 1_784_548_800
    assert reply_history._reply_target_epoch({"target_created_at": "2026-07-20T12:00:00"}) is None
    assert reply_history._reply_target_epoch({"target_created_at": "invalid"}) is None
    parsed = Mock(tzinfo=object())
    failure = ValueError("native timestamp failure")
    parsed.timestamp.side_effect = failure
    parse = Mock(return_value=parsed)
    monkeypatch.setattr(reply_history, "datetime", Mock(fromisoformat=parse))
    with pytest.raises(ValueError) as caught:
        reply_history._reply_target_epoch(context)
    assert caught.value is failure
    parse.assert_called_once_with("2026-07-20T12:00:00+00:00")
    parse.side_effect = TypeError("native parse failure")
    with pytest.raises(TypeError, match="native parse failure"):
        reply_history._reply_target_epoch(context)


def test_evaluation_selection_preserves_order_cutoff_and_argument_references(make_owner, monkeypatch):
    owner = make_owner()
    context, state, excluded = unit_reply_context(), {}, {"100"}
    rows = [{"incoming_contribution": " Earlier contribution. ", "proposed_reply": " Earlier response. ", "reply_post_id": "prior"}]
    recent = [{"post_id": "other", "text": "Another earlier response."}]
    trace = Mock()
    trace.epoch.return_value = 2_000_000_000
    trace.exclusions.return_value = excluded
    trace.same_author.return_value = rows
    trace.recent.return_value = recent
    monkeypatch.setattr(reply_history, "_reply_target_epoch", trace.epoch)
    monkeypatch.setattr(reply_history.ReplyHistory, "context_excluded_post_ids", trace.exclusions)
    monkeypatch.setattr(reply_history.ReplyHistory, "same_author_rows", trace.same_author)
    monkeypatch.setattr(reply_history.ReplyHistory, "recent_replies", trace.recent)
    same_author, actual_recent = owner.for_evaluation(state, context=context, target_id="snapshot-target")
    assert actual_recent is recent
    assert same_author == [{"contributor": "Earlier contribution.", "account_reply": "Earlier response."}]
    assert rows[0]["incoming_contribution"] == " Earlier contribution. "
    assert [entry[0] for entry in trace.mock_calls] == ["epoch", "exclusions", "same_author", "recent"]
    assert trace.epoch.call_args.args[0] is context
    assert trace.exclusions.call_args.args[0] is context
    for callback in (trace.same_author, trace.recent):
        assert callback.call_args.args[0] is state
        assert callback.call_args.kwargs["before_epoch"] == 2_000_000_000
    assert trace.same_author.call_args.kwargs["current_thread_post_ids"] is excluded
    assert trace.same_author.call_args.kwargs["target_id"] == "snapshot-target"
    assert trace.recent.call_args.args[1] == owner.default_recent_reply_limit
    assert trace.recent.call_args.kwargs["excluded_post_ids"] is excluded
    assert trace.recent.call_args.kwargs["excluded_reply_post_ids"] == {"prior"}


def test_evaluation_and_recovery_use_distinct_target_and_current_cutoffs(make_owner, confirmed_row):
    context = unit_reply_context(target_id="500")
    target_epoch = reply_history._reply_target_epoch(context)
    earlier_same = {**confirmed_row, "reply_post_id": "1", "reply_epoch": target_epoch - 2}
    earlier_other = {**confirmed_row, "author_id": "201", "reply_post_id": "2", "reply_epoch": target_epoch - 1}
    at_target = {**confirmed_row, "reply_post_id": "3", "reply_epoch": target_epoch}
    future = {**confirmed_row, "reply_post_id": "4", "reply_epoch": target_epoch + 11}
    quoted = {**confirmed_row, "reply_post_id": "5", "reply_epoch": target_epoch - 1}
    state = {"ai_reply_history": [earlier_same, earlier_other, at_target, future, quoted]}
    clock = Mock(return_value=target_epoch + 10)
    owner = make_owner(now_epoch=clock, quoted_post_reference_id=lambda _context: "5")
    same_author, recent = owner.for_evaluation(state, context=context, target_id="500")
    assert same_author == [{"contributor": confirmed_row["incoming_contribution"], "account_reply": confirmed_row["proposed_reply"]}]
    assert recent == [{"post_id": "2", "text": confirmed_row["proposed_reply"]}]
    clock.assert_not_called()
    assert [row["post_id"] for row in owner.recovery_replies(state, context=context)] == ["1", "2", "3"]
    clock.assert_called_once_with()
    assert owner.for_evaluation(state, context={**context, "target_created_at": "invalid"}, target_id="500") == ([], [])


@pytest.mark.parametrize("schema", [2, 4])
def test_recording_keeps_expansion_order_strict_epochs_sort_cap_and_references(make_owner, schema):
    receipt = unit_confirmed_reply_receipt() if schema == 2 else unit_confirmed_v4_reply_receipt()
    epoch = receipt["reply_epoch"]
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
    state = {"ai_reply_history": history}
    clock = Mock(return_value=epoch + 10)
    owner = make_owner(now_epoch=clock, maximum_age_seconds=10, maximum_records=4)
    options = dict(target_id="100", reply_post_id="999", author_id="200",
                   conversation_id="100", candidate_source="mention", reply_epoch=epoch)
    assert owner.record_confirmation(state, receipt, draft, **options) is None
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
    clock.assert_called_once_with()
    replace(owner, maximum_records=2).record_confirmation(state, receipt, draft, **options)
    assert len(state["ai_reply_history"]) == 2
    assert state["ai_reply_history"][0] is retained


@pytest.mark.parametrize("dependency", ["now_epoch", "valid_string_post_id"])
def test_recording_keeps_native_callback_errors_and_original_history(make_owner, dependency):
    receipt = unit_confirmed_reply_receipt()
    retained = {"target_id": "101", "reply_post_id": "10", "reply_epoch": receipt["reply_epoch"]}
    history = [retained]
    state = {"ai_reply_history": history}
    failure = TypeError("history dependency failed")
    owner = make_owner(**{dependency: Mock(side_effect=failure)})
    with pytest.raises(TypeError) as caught:
        owner.record_confirmation(
            state, receipt, receipt["ai_reply_draft"], target_id="100", reply_post_id="999",
            author_id="200", conversation_id="100", candidate_source="mention",
            reply_epoch=receipt["reply_epoch"],
        )
    assert caught.value is failure
    assert state["ai_reply_history"] is history and history == [retained]


def test_recording_uses_later_confirmation_for_retention_and_keeps_zero_cap_slice(make_owner):
    receipt = unit_confirmed_reply_receipt()
    epoch = receipt["reply_epoch"]
    stale = {"target_id": "101", "reply_post_id": "10", "reply_epoch": epoch - 11}
    boundary = {"target_id": "102", "reply_post_id": "11", "reply_epoch": epoch - 10}
    state = {"ai_reply_history": [stale, boundary]}
    owner = make_owner(now_epoch=lambda: epoch - 20, maximum_age_seconds=10, maximum_records=0)
    owner.record_confirmation(
        state, receipt, receipt["ai_reply_draft"], target_id="100", reply_post_id="999",
        author_id="200", conversation_id="100", candidate_source="mention", reply_epoch=epoch,
    )
    assert len(state["ai_reply_history"]) == 2
    assert state["ai_reply_history"][0] is boundary
