from __future__ import annotations

import copy
import inspect
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock, call

import pytest

from tests.helpers.adapter_assertions import assert_adapters_forward_current_dependencies

import mrs_bot_reply_state as reply_state
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401
from tests.helpers.reply_fixtures import (
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
import builtins, collections.abc, dataclasses, datetime, hashlib, io, logging, os, random, socket, sys
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('reply state import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'reply_evidence'} or name.startswith('mrs_bot_') and name not in {'mrs_bot_reply_state', 'mrs_bot_reply_drafts'}:
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
    assert reply_state.hashlib is bot.hashlib
    assert reply_state.datetime is bot.datetime


def test_adapters_forward_current_dependencies_arguments_results_and_errors(monkeypatch):
    names = (
        "_confirmed_conversational_history_rows",
        "recent_confirmed_account_replies", "_reply_context_history_excluded_post_ids",
        "recovery_comparison_account_replies", "_same_author_confirmed_history_rows",
        "recent_same_author_account_interactions",
    )
    assert_adapters_forward_current_dependencies(
        monkeypatch, bot=bot, implementation=reply_state, names=names,
    )


@pytest.mark.parametrize("drafts", [None, [], {}, {"custom-key": None}, {"custom-key": "obsolete"}, {"custom-key": {}}])
def test_ineligible_draft_retirement_keeps_missing_and_malformed_draft_behavior(drafts):
    state = {} if drafts is None else {"pending_ai_reply_drafts": drafts}
    before = copy.deepcopy(state)
    trace = Mock()
    trace.key.return_value = "custom-key"

    reply_state.retire_ineligible_reply_draft(
        state, "101", "hot_post_reply",
        reason="target_does_not_directly_mention_account",
        pending_ai_reply_draft_key=trace.key,
        log_event=trace.event,
        clear_pending_ai_reply=trace.clear,
        record_terminal_reply_evaluation=trace.terminal,
    )

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
    expected.append(call.terminal(
        state, target_id="101", lane="hot_post_reply",
        reason="target_does_not_directly_mention_account", outcome="reply_not_permitted",
    ))
    assert trace.mock_calls == expected
    assert state == before  # State mutation belongs to the supplied callbacks.


@pytest.mark.parametrize("boundary", [None, "key", "event", "clear", "terminal"])
def test_ineligible_draft_retirement_preserves_metadata_order_and_callback_failures(boundary):
    draft = {
        "strategy_version": "stored-strategy", "reply_kind": "direct_reply",
        "reason_code": "answer_question", "validated_draft_hash": "stored-hash",
    }
    state = {"pending_ai_reply_drafts": {"custom-key": draft}}
    trace = Mock()
    trace.key.return_value = "custom-key"
    failure = RuntimeError("retirement callback failed")
    if boundary:
        getattr(trace, boundary).side_effect = failure

    def retire():
        reply_state.retire_ineligible_reply_draft(
            state, "101", "mention", reason="ineligible-reason",
            pending_ai_reply_draft_key=trace.key,
            log_event=trace.event,
            clear_pending_ai_reply=trace.clear,
            record_terminal_reply_evaluation=trace.terminal,
        )

    if boundary:
        with pytest.raises(RuntimeError) as caught:
            retire()
        assert caught.value is failure
    else:
        retire()

    expected = [
        call.key("101", "mention"),
        call.event(
            "single_call_reply_posting_outcome", status="posting_failed_terminal",
            lane="mention", target_id="101", reply_post_id="", **draft,
            failure_reason="reply_not_permitted_preflight",
        ),
        call.clear(state, "101", "mention"),
        call.terminal(
            state, target_id="101", lane="mention", reason="ineligible-reason",
            outcome="reply_not_permitted",
        ),
    ]
    if boundary:
        expected = expected[:["key", "event", "clear", "terminal"].index(boundary) + 1]
    assert trace.mock_calls == expected


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
