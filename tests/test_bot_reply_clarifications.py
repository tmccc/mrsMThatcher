"""Exercise clarification eligibility and its confirmed repair ledger together."""

from __future__ import annotations


import copy
from dataclasses import FrozenInstanceError, replace
import inspect
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrs_bot_reply_clarifications as clarifications
import mrs_bot_reply_lane_policy as policy
from tests.helpers.mention_fixtures import mention
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401
from tests.helpers.reply_fixtures import unit_confirmed_reply_receipt, patch_tweet_lookup_method


OWNER_INPUTS = {
    "pipeline_enabled": "conversational_reply_pipeline_enabled",
    "parent_id": "get_immediate_parent_id",
    "get_tweet_by_id_cached": "get_tweet_by_id_cached",
    "api_error_is_permanent_target_failure": "api_error_is_permanent_target_failure",
    "is_our_auto_reply": "is_our_auto_reply", "api_error": "ApiError",
    "invalid_receipt": "InvalidConfirmedReplyReceipt",
    "window_seconds": "CLARIFICATION_REPLY_WINDOW_SECONDS",
    "log_event": "log_event",
}


@pytest.fixture
def make_owner():
    """Compose clarification operations with isolated runtime boundaries."""
    def build(**overrides):
        current = {field: getattr(bot, name) for field, name in OWNER_INPUTS.items()}
        return clarifications.ClarificationReplies(**{**current, **overrides})
    return build


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, dataclasses, io, logging, os, random, re, socket, sys, time
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('clarification replies import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'reply_evidence'} or name.startswith('mrs_bot_') and name not in {'mrs_bot_reply_clarifications', 'mrs_bot_reply_native_media', 'mrs_bot_tweet_lookup_cache'}:
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = forbidden
os.getenv = os._Environ.__getitem__ = forbidden
Path.home = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
time.time = time.monotonic = forbidden
before = random.getstate()
random.Random = random.seed = random.random = forbidden
import mrs_bot_reply_clarifications
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
assert 'requests' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout


def test_owner_composition_binds_current_dependencies_without_calling_them(monkeypatch):
    snapshots = []
    for _ in range(2):
        current = {field: Mock() for field in OWNER_INPUTS}
        for field, name in OWNER_INPUTS.items():
            monkeypatch.setattr(bot, name, current[field])
        owner = bot._clarification_reply_owner()
        assert isinstance(owner, clarifications.ClarificationReplies)
        for field, value in current.items():
            assert getattr(owner, field) is value
            value.assert_not_called()
        snapshots.append((owner, current))
    first, inputs = snapshots[0]
    assert first is not snapshots[1][0]
    assert all(getattr(first, field) is value for field, value in inputs.items())
    with pytest.raises(FrozenInstanceError):
        first.window_seconds = 1


def test_root_adapters_preserve_arguments_result_identity_and_errors(monkeypatch):
    methods = {
        "clarification_thread_is_terminal": "thread_is_terminal",
        "author_used_clarification_recently": "author_used_recently",
        "clarification_reply_context": "context",
    }
    for name, method_name in methods.items():
        adapter = getattr(bot, name)
        public = inspect.signature(adapter).parameters
        args = tuple(object() for param in public.values() if param.kind == param.POSITIONAL_OR_KEYWORD)
        options = {key: object() for key, param in public.items() if param.kind == param.KEYWORD_ONLY}
        for _ in range(2):
            owner = Mock(spec=clarifications.ClarificationReplies)
            factory = Mock(return_value=owner)
            monkeypatch.setattr(bot, "_clarification_reply_owner", factory)
            implementation = getattr(owner, method_name)
            result = object()
            implementation.return_value = result
            assert adapter(*args, **options) is result
            factory.assert_called_once_with()
            actual_args, actual_kwargs = implementation.call_args
            assert len(actual_args) == len(args)
            assert all(actual is original for actual, original in zip(actual_args, args))
            assert actual_kwargs.keys() == options.keys()
            assert all(actual_kwargs[key] is value for key, value in options.items())
            failure = TypeError(name)
            implementation.side_effect = failure
            with pytest.raises(TypeError) as caught:
                adapter(*args, **options)
            assert caught.value is failure


def test_aliases_share_fixed_objects_and_tokens_use_owned_regex_and_stopwords(monkeypatch, make_owner):
    for name in ("clarification_thread_id",
                 "CLARIFICATION_CUE_RE", "CLARIFICATION_TOKEN_RE", "CLARIFICATION_TOKEN_STOPWORDS"):
        assert getattr(bot, name) is getattr(policy, name) is getattr(clarifications, name)
    assert type(bot.CLARIFICATION_CUE_RE) is type(bot.CLARIFICATION_TOKEN_RE) is re.Pattern
    assert type(bot.CLARIFICATION_TOKEN_STOPWORDS) is set
    owner = make_owner()
    stopwords = clarifications.CLARIFICATION_TOKEN_STOPWORDS
    assert "stage25token" not in stopwords
    try:
        stopwords.add("stage25token")
        assert owner.tokens("@Someone STAGE25TOKEN Berlin berlin?") == {"berlin"}
    finally:
        stopwords.remove("stage25token")
    assert bot._clarification_tokens is clarifications._clarification_tokens
    monkeypatch.setattr(bot, "_clarification_reply_owner", Mock(side_effect=AssertionError("pure tokens built runtime owner")))
    monkeypatch.setattr(clarifications, "CLARIFICATION_TOKEN_RE", re.compile(r"\d+"))
    monkeypatch.setattr(clarifications, "CLARIFICATION_TOKEN_STOPWORDS", {"25"})
    assert owner.tokens("@user99 stage 25 or 26 or 26?") == {"26"}
    current_re = SimpleNamespace(sub=Mock(return_value="27 25"))
    monkeypatch.setattr(clarifications, "re", current_re)
    assert bot._clarification_tokens(None) == {"27"}
    current_re.sub.assert_called_once_with(r"(?<![A-Za-z0-9_])@[A-Za-z0-9_]+", " ", "")


@pytest.fixture
def confirmed_question(monkeypatch):
    monkeypatch.setattr(bot, "now_epoch", lambda: 2_000_000_000)
    monkeypatch.setattr(bot, "MY_USER_ID", "12345")
    monkeypatch.setattr(bot, "single_call_reply", {**bot.single_call_reply, "enabled": True})
    question_text = "@MrsMThatcher Where did people move when the Berlin Wall fell?"
    state = bot.default_state()
    bot.cache_tweet(state, tweet_id="100", author_id="200", conversation_id="700",
                    text=question_text, referenced_tweets=[])
    bot.apply_confirmed_reply_receipt(state, unit_confirmed_reply_receipt(
        target_id="100", reply_post_id="900", conversation_id="700", contribution=question_text,
    ))
    candidate = mention(101, 200, "@MrsMThatcher You did not answer my question: where did Berlin people move?")
    candidate.update(conversation_id="700", referenced_tweets=[{"type": "replied_to", "id": "900"}])
    return state, candidate


def test_cached_clarification_keeps_original_references_and_current_correction_token_gate(monkeypatch, make_owner, confirmed_question):
    state, candidate = confirmed_question
    before = copy.deepcopy((state, candidate))
    prior, question = state["tweet_cache"]["900"], state["tweet_cache"]["100"]
    trace = Mock()
    trace.parent = Mock(wraps=bot.get_immediate_parent_id)
    trace.own = Mock(wraps=bot.is_our_auto_reply)
    trace.cue.search = Mock(wraps=bot.CLARIFICATION_CUE_RE.search)
    monkeypatch.setattr(clarifications, "CLARIFICATION_CUE_RE", trace.cue)
    owner = make_owner(parent_id=trace.parent, is_our_auto_reply=trace.own)
    trace.tokens = Mock(wraps=owner.tokens)
    monkeypatch.setattr(clarifications.ClarificationReplies, "tokens", trace.tokens)
    result = owner.context(state, candidate, current=2_000_000_001)
    assert result == {
        "thread_id": "700", "prior_bot_reply_id": "900", "original_question_id": "100",
        "question_text": question["text"], "trigger": "explicit_correction",
    }
    assert result["question_text"] is question["text"]
    assert [entry[0] for entry in trace.mock_calls] == ["parent", "own", "parent", "cue.search", "tokens", "tokens"]
    assert trace.parent.call_args_list[0].args[0] is candidate
    assert trace.parent.call_args_list[1].args[0] is prior
    assert trace.own.call_args.args[0] is prior and trace.own.call_args.args[1] is state
    assert trace.tokens.call_args_list == [call(question["text"]), call(candidate["text"])]
    monkeypatch.setattr(clarifications, "CLARIFICATION_CUE_RE", re.compile(r"(?!)"))
    assert owner.context(state, candidate, current=2_000_000_001)["trigger"] == "restated_question"
    monkeypatch.setattr(clarifications.ClarificationReplies, "tokens", Mock(return_value=set()))
    assert owner.context(state, candidate, current=2_000_000_001) is None
    assert (state, candidate) == before


def test_clarification_requires_ledger_and_cache_proof_and_catches_only_current_parent_error(monkeypatch, make_owner, confirmed_question):
    state, candidate = confirmed_question
    parent = Mock(wraps=bot.get_immediate_parent_id)
    owner = make_owner(parent_id=parent)
    with monkeypatch.context() as patch:
        patch.setitem(state, "own_auto_reply_ids", [])
        assert owner.context(state, candidate, current=2_000_000_001) is None
        assert parent.call_count == 1
    with monkeypatch.context() as patch:
        patch.delitem(state["tweet_cache"], "900")
        assert owner.context(state, candidate, current=2_000_000_001) is None
    with monkeypatch.context() as patch:
        patch.setitem(state["tweet_cache"]["100"], "author_id", "other")
        assert owner.context(state, candidate, current=2_000_000_001) is None

    class CurrentApiError(Exception):
        pass

    failure = CurrentApiError("current parent error")
    owner = replace(owner, api_error=CurrentApiError)
    for outcomes in ([failure], ["900", failure]):
        parent.side_effect = outcomes
        assert owner.context(state, candidate, current=2_000_000_001) is None
    native_failure = ValueError("native parent failure")
    parent.side_effect = native_failure
    with pytest.raises(ValueError) as caught:
        owner.context(state, candidate, current=2_000_000_001)
    assert caught.value is native_failure
    owner = replace(owner, pipeline_enabled=Mock(side_effect=failure))
    with pytest.raises(CurrentApiError) as caught:
        owner.context(state, candidate, current=2_000_000_001)
    assert caught.value is failure


def test_terminal_thread_and_recent_author_keep_current_callback_window_and_native_errors(monkeypatch, make_owner):
    owner = make_owner(window_seconds=10)
    state = {"clarification_reply_records": {"legacy": None}}
    candidate = {"conversation_id": 0, "id": 7}
    assert bot.clarification_thread_id(candidate) == "7"
    current_thread = Mock(return_value="legacy")
    monkeypatch.setattr(clarifications, "clarification_thread_id", current_thread)
    assert owner.thread_is_terminal(state, candidate) is True
    assert current_thread.call_args.args[0] is candidate
    records = {"bad": {"author_id": 200, "completed_epoch": "broken"},
               "good": {"author_id": 200, "completed_epoch": 90}}
    state["clarification_reply_records"] = records
    assert owner.author_used_recently(state, "200", current=100) is False
    assert owner.author_used_recently(state, "200", current=99) is True
    with pytest.raises(ValueError):
        owner.author_used_recently(state, "200", current="broken")


def test_clarification_refreshes_legacy_question_before_looking_for_question_mark(monkeypatch, make_owner, confirmed_question):
    owner = make_owner()
    state, candidate = confirmed_question
    original = state["tweet_cache"]["100"]
    full = "The earlier explanation is background. " * 10 + "Where did people move when the Berlin Wall fell?"
    original["text"] = full[:280]
    original.pop("text_is_complete")
    fresh = {**original, "note_tweet": {"text": full}}
    fetch = Mock(return_value=fresh)
    patch_tweet_lookup_method(monkeypatch, "fetch", fetch)
    result = owner.context(state, candidate, current=2_000_000_001)
    assert result["question_text"] == full
    assert result["trigger"] == "explicit_correction"
    assert state["tweet_cache"]["100"]["text_is_complete"] is True
    assert owner.context(state, candidate, current=2_000_000_001) == result
    fetch.assert_called_once_with("100")


@pytest.mark.parametrize("status", [404, 503])
def test_clarification_legacy_refresh_distinguishes_deleted_from_transient(monkeypatch, make_owner, confirmed_question, status):
    owner = make_owner()
    state, candidate = confirmed_question
    state["tweet_cache"]["100"].pop("text_is_complete")
    error = bot.ApiError("lookup failed", service="x", status_code=status, request_method="GET", request_path="/2/tweets/100")
    patch_tweet_lookup_method(monkeypatch, "fetch", Mock(side_effect=error))
    if status == 404:
        assert owner.context(state, candidate, current=2_000_000_001) is None
    else:
        with pytest.raises(bot.ApiError) as caught:
            owner.context(state, candidate, current=2_000_000_001)
        assert caught.value is error


def test_conflict_check_preserves_records_and_supplied_receipt_error(make_owner):
    class CurrentReceiptError(Exception):
        pass

    existing = {"reply_post_id": "999"}
    records = {"700": existing}
    state = {"clarification_reply_records": records}
    owner = make_owner(invalid_receipt=CurrentReceiptError)
    clarification = {"thread_id": 700}
    assert owner.assert_no_conflict(state, clarification, reply_post_id="999") is None
    with pytest.raises(CurrentReceiptError, match="clarification thread 700 already has a different completed repair"):
        owner.assert_no_conflict(state, clarification, reply_post_id="1000")
    assert state["clarification_reply_records"] is records and records["700"] is existing
    records["700"] = None
    assert owner.assert_no_conflict(state, clarification, reply_post_id="1000") is None


def test_recording_keeps_shallow_references_and_assigns_before_ordered_events(make_owner):
    sibling = {"reply_post_id": "800"}
    previous = {"600": sibling}
    state = {"clarification_reply_records": previous}
    clarification = {"thread_id": 700, "prior_bot_reply_id": 900,
                     "original_question_id": 100, "trigger": "explicit_correction"}
    event = Mock()
    owner = make_owner(log_event=event)
    options = dict(author_id="200", target_id="101", reply_post_id="999", reply_epoch=2000)

    def after_assignment(*_args, **_kwargs):
        assert state["clarification_reply_records"]["700"]["reply_post_id"] == "999"

    event.side_effect = after_assignment
    assert owner.record_completed(state, clarification, **options) is None
    records = state["clarification_reply_records"]
    assert records is not previous and records["600"] is sibling
    assert previous == {"600": sibling}
    assert records["700"] == {
        "thread_id": "700", "author_id": "200", "target_id": "101", "reply_post_id": "999",
        "prior_bot_reply_id": "900", "original_question_id": "100", "trigger": "explicit_correction",
        "completed_epoch": 2000, "status": "repair_reply_completed",
        "clarification_reply_used": True, "thread_terminal": True,
    }
    assert event.call_args_list == [
        call("clarification_reply_used", thread_id="700", author_id="200", target_id="101",
             reply_post_id="999", trigger="explicit_correction"),
        call("repair_reply_completed", thread_id="700", author_id="200", target_id="101", reply_post_id="999"),
    ]
    event.reset_mock()
    owner.record_completed(state, clarification, **options)
    assert state["clarification_reply_records"] is records
    event.assert_not_called()
    failure = RuntimeError("repair event failed")
    event.side_effect = failure
    unrecorded = {}
    with pytest.raises(RuntimeError) as caught:
        owner.record_completed(unrecorded, clarification, **options)
    assert caught.value is failure
    assert unrecorded["clarification_reply_records"]["700"] == records["700"]
    assert event.call_count == 1
