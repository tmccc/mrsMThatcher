from __future__ import annotations

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


@pytest.mark.parametrize("boundary", [None, "retire", "terminal"])
def test_ineligible_retirement_delegates_before_terminal_record_and_preserves_errors(boundary):
    state, target, source, reason = {}, object(), object(), object()
    trace = Mock()
    failure = RuntimeError("retirement callback failed")
    if boundary:
        getattr(trace, boundary).side_effect = failure

    def retire():
        reply_state.retire_ineligible_reply_draft(
            state, target, source, reason=reason,
            retire_draft=trace.retire,
            record_terminal_reply_evaluation=trace.terminal,
        )

    if boundary:
        with pytest.raises(RuntimeError) as caught:
            retire()
        assert caught.value is failure
    else:
        retire()
    expected = [call.retire(state, target, source)]
    if boundary != "retire":
        expected.append(call.terminal(
            state, target_id=target, lane=source, reason=reason,
            outcome="reply_not_permitted",
        ))
    assert trace.mock_calls == expected
    assert trace.retire.call_args.args[0] is state
    assert state == {}
