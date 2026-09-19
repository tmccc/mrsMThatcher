from __future__ import annotations

import copy
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock, call

import pytest

import mrs_bot_reply_state as reply_state
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401


def test_import_needs_no_runtime_access_and_root_aliases_share_owner_objects():
    code = """
import builtins, collections.abc, dataclasses, datetime, hashlib, io, logging, os, random, socket, sys
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('reply state import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'reply_evidence'} or name.startswith('mrs_bot_') and name not in {'mrs_bot_reply_state', 'mrs_bot_reply_drafts', 'mrs_bot_reply_history'}:
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
