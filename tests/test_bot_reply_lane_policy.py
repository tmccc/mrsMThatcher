from __future__ import annotations

import copy
import inspect
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrs_bot_reply_lane_policy as policy
from tests.helpers.mention_fixtures import mention
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_regular_post_receipt  # noqa: F401
from tests.helpers.reply_fixtures import unit_confirmed_reply_receipt


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, io, logging, os, random, re, socket, sys, time
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('reply lane policy import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply'} or name.startswith('mrs_bot_') and name != 'mrs_bot_reply_lane_policy':
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
import mrs_bot_reply_lane_policy
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


def test_adapters_forward_current_dependencies_arguments_references_and_errors(monkeypatch):
    for name, count in (
        ("reset_daily_reply_count_if_needed", 2),
        ("reset_daily_quote_reply_count_if_needed", 2),
        ("daily_author_reply_count", 1), ("mark_daily_author_replied", 2),
        ("clarification_thread_is_terminal", 1),
        ("author_used_clarification_recently", 1), ("_clarification_tokens", 3),
        ("clarification_reply_context", 12),
        ("reply_target_is_directly_eligible", 3),
        ("is_probably_spam_or_not_worth_replying", 3),
    ):
        adapter = getattr(bot, name)
        public = inspect.signature(adapter).parameters
        dependencies = inspect.signature(getattr(policy, name)).parameters.keys() - public.keys()
        assert len(dependencies) == count, name
        args = tuple(object() for param in public.values() if param.kind == param.POSITIONAL_OR_KEYWORD)
        options = {key: object() for key, param in public.items() if param.kind == param.KEYWORD_ONLY}
        result = object()
        owner = Mock(return_value=result)
        with monkeypatch.context() as patch:
            patch.setattr(policy, name, owner)
            for _ in range(2):
                current = {key: object() for key in dependencies}
                for key, value in current.items():
                    patch.setattr(bot, key, value)
                assert adapter(*args, **options) is result
                actual_args, actual_kwargs = owner.call_args
                assert len(actual_args) == len(args)
                assert all(actual is original for actual, original in zip(actual_args, args))
                assert actual_kwargs.keys() == (options | current).keys()
                assert all(actual_kwargs[key] is value for key, value in (options | current).items())
            failure = TypeError("current owner failure")
            owner.side_effect = failure
            with pytest.raises(TypeError) as caught:
                adapter(*args, **options)
            assert caught.value is failure


def test_aliases_share_fixed_objects_and_tokens_use_current_regex_and_stopwords(monkeypatch):
    for name in ("daily_author_reply_counts", "clarification_thread_id",
                 "CLARIFICATION_CUE_RE", "CLARIFICATION_TOKEN_RE", "CLARIFICATION_TOKEN_STOPWORDS"):
        assert getattr(bot, name) is getattr(policy, name)
    assert type(bot.CLARIFICATION_CUE_RE) is type(bot.CLARIFICATION_TOKEN_RE) is re.Pattern
    assert type(bot.CLARIFICATION_TOKEN_STOPWORDS) is set
    stopwords = policy.CLARIFICATION_TOKEN_STOPWORDS
    assert "stage25token" not in stopwords
    try:
        stopwords.add("stage25token")
        assert bot._clarification_tokens("@Someone STAGE25TOKEN Berlin berlin?") == {"berlin"}
    finally:
        stopwords.remove("stage25token")
    monkeypatch.setattr(bot, "CLARIFICATION_TOKEN_RE", re.compile(r"\d+"))
    monkeypatch.setattr(bot, "CLARIFICATION_TOKEN_STOPWORDS", {"25"})
    assert bot._clarification_tokens("@user99 stage 25 or 26 or 26?") == {"26"}
    current_re = SimpleNamespace(sub=Mock(return_value="27 25"))
    monkeypatch.setattr(bot, "re", current_re)
    assert bot._clarification_tokens(None) == {"27"}
    current_re.sub.assert_called_once_with(r"(?<![A-Za-z0-9_])@[A-Za-z0-9_]+", " ", "")


@pytest.mark.parametrize("lane", ["reply", "quote_reply"])
def test_resets_sample_current_date_once_and_log_before_mutation(monkeypatch, lane):
    ids, counts, ledger = ["200"], {"200": 2}, {"700": None}
    state = {
        "daily_reply_date": "old", "daily_reply_count": 4,
        "daily_quote_reply_date": "old", "daily_quote_reply_count": 3,
        "daily_replied_author_ids": ids, "daily_replied_author_counts": counts,
        "clarification_reply_records": ledger,
    }
    before = copy.deepcopy(state)
    trace = Mock()
    trace.date.return_value = "current date"
    monkeypatch.setattr(bot, "reply_cap_date_str", trace.date)
    monkeypatch.setattr(bot, "log", trace.log)
    reset = getattr(bot, f"reset_daily_{lane}_count_if_needed")
    failure = RuntimeError("current logger failed")
    trace.log.info.side_effect = failure
    with pytest.raises(RuntimeError) as caught:
        reset(state)
    assert caught.value is failure and state == before
    assert [entry[0] for entry in trace.mock_calls] == ["date", "log.info"]
    assert trace.log.info.call_args.args[1:] == ("old", "current date", before[f"daily_{lane}_count"])
    trace.reset_mock()

    def before_reset(*_args):
        assert state == before

    trace.log.info.side_effect = before_reset
    assert reset(state) is None
    assert [entry[0] for entry in trace.mock_calls] == ["date", "log.info"]
    assert state[f"daily_{lane}_date"] == "current date"
    assert state[f"daily_{lane}_count"] == 0
    assert state["clarification_reply_records"] is ledger
    if lane == "reply":
        assert state["daily_replied_author_ids"] == [] and state["daily_replied_author_ids"] is not ids
        assert state["daily_replied_author_counts"] == {} and state["daily_replied_author_counts"] is not counts
        assert state["daily_quote_reply_count"] == 3
    else:
        assert state["daily_replied_author_ids"] is ids and state["daily_replied_author_counts"] is counts
        assert state["daily_reply_count"] == 4
    retained = dict(state)
    trace.reset_mock()
    reset(state)
    assert trace.mock_calls == [call.date()]
    assert all(state[key] is value for key, value in retained.items())


def test_legacy_counts_keep_permissive_cleaning_fallback_and_fresh_mapping():
    class BadCount:
        def __int__(self):
            raise RuntimeError("legacy count cannot convert")

    original = {7: "2", "negative": -3, "fraction": 2.9, "bool": True, "broken": BadCount()}
    state = {"daily_replied_author_counts": original, "daily_replied_author_ids": ["legacy"]}
    cleaned = bot.daily_author_reply_counts(state)
    assert cleaned == {"7": 2, "negative": 0, "fraction": 2, "bool": 1}
    assert cleaned is state["daily_replied_author_counts"] and cleaned is not original
    again = bot.daily_author_reply_counts(state)
    assert again == cleaned and again is not cleaned and again is state["daily_replied_author_counts"]
    for legacy_counts in ({"broken": BadCount()}, None):
        state = {"daily_replied_author_counts": legacy_counts, "daily_replied_author_ids": [7, "7", None, False]}
        result = bot.daily_author_reply_counts(state)
        assert result == {"7": 1, "None": 1, "False": 1}
        assert result is state["daily_replied_author_counts"]


def test_author_increment_precedes_current_capped_helper_failure(monkeypatch):
    counts, ids = {"7": 2}, ["older"]
    state = {"daily_replied_author_ids": ids}
    cleaner = Mock(return_value=counts)
    monkeypatch.setattr(bot, "daily_author_reply_counts", cleaner)
    assert bot.daily_author_reply_count(state, 7) == 2
    assert cleaner.call_args.args[0] is state
    failure = ValueError("capped ID helper failed")

    def capped(actual_ids, author_id, maximum):
        assert actual_ids is ids and author_id == "7" and maximum == 1000
        assert state["daily_replied_author_counts"] is counts and counts["7"] == 3
        raise failure

    monkeypatch.setattr(bot, "append_unique_capped", capped)
    with pytest.raises(ValueError) as caught:
        bot.mark_daily_author_replied(state, 7)
    assert caught.value is failure
    assert counts == {"7": 3} and state["daily_replied_author_ids"] is ids
    replacement = ["current"]
    monkeypatch.setattr(bot, "append_unique_capped", Mock(return_value=replacement))
    bot.mark_daily_author_replied(state, 7)
    assert counts == {"7": 4} and state["daily_replied_author_ids"] is replacement


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


def test_cached_clarification_keeps_original_references_and_current_correction_token_gate(monkeypatch, confirmed_question):
    state, candidate = confirmed_question
    before = copy.deepcopy((state, candidate))
    prior, question = state["tweet_cache"]["900"], state["tweet_cache"]["100"]
    trace = Mock()
    for label, name in (("parent", "get_immediate_parent_id"), ("own", "is_our_auto_reply"),
                        ("tokens", "_clarification_tokens")):
        callback = Mock(wraps=getattr(bot, name))
        trace.attach_mock(callback, label)
        monkeypatch.setattr(bot, name, callback)
    trace.cue.search = Mock(wraps=bot.CLARIFICATION_CUE_RE.search)
    monkeypatch.setattr(bot, "CLARIFICATION_CUE_RE", trace.cue)
    result = bot.clarification_reply_context(state, candidate, current=2_000_000_001)
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
    monkeypatch.setattr(bot, "CLARIFICATION_CUE_RE", re.compile(r"(?!)"))
    assert bot.clarification_reply_context(state, candidate, current=2_000_000_001)["trigger"] == "restated_question"
    monkeypatch.setattr(bot, "_clarification_tokens", Mock(return_value=set()))
    assert bot.clarification_reply_context(state, candidate, current=2_000_000_001) is None
    assert (state, candidate) == before


def test_clarification_requires_ledger_and_cache_proof_and_catches_only_current_parent_error(monkeypatch, confirmed_question):
    state, candidate = confirmed_question
    parent = Mock(wraps=bot.get_immediate_parent_id)
    monkeypatch.setattr(bot, "get_immediate_parent_id", parent)
    with monkeypatch.context() as patch:
        patch.setitem(state, "own_auto_reply_ids", [])
        assert bot.clarification_reply_context(state, candidate, current=2_000_000_001) is None
        assert parent.call_count == 1
    with monkeypatch.context() as patch:
        patch.delitem(state["tweet_cache"], "900")
        assert bot.clarification_reply_context(state, candidate, current=2_000_000_001) is None
    with monkeypatch.context() as patch:
        patch.setitem(state["tweet_cache"]["100"], "author_id", "other")
        assert bot.clarification_reply_context(state, candidate, current=2_000_000_001) is None

    class CurrentApiError(Exception):
        pass

    failure = CurrentApiError("current parent error")
    monkeypatch.setattr(bot, "ApiError", CurrentApiError)
    for outcomes in ([failure], ["900", failure]):
        parent.side_effect = outcomes
        assert bot.clarification_reply_context(state, candidate, current=2_000_000_001) is None
    native_failure = ValueError("native parent failure")
    parent.side_effect = native_failure
    with pytest.raises(ValueError) as caught:
        bot.clarification_reply_context(state, candidate, current=2_000_000_001)
    assert caught.value is native_failure
    monkeypatch.setattr(bot, "conversational_reply_pipeline_enabled", Mock(side_effect=failure))
    with pytest.raises(CurrentApiError) as caught:
        bot.clarification_reply_context(state, candidate, current=2_000_000_001)
    assert caught.value is failure


def test_terminal_thread_and_recent_author_keep_current_callback_window_and_native_errors(monkeypatch):
    state = {"clarification_reply_records": {"legacy": None}}
    candidate = {"conversation_id": 0, "id": 7}
    assert bot.clarification_thread_id(candidate) == "7"
    current_thread = Mock(return_value="legacy")
    monkeypatch.setattr(bot, "clarification_thread_id", current_thread)
    assert bot.clarification_thread_is_terminal(state, candidate) is True
    assert current_thread.call_args.args[0] is candidate
    records = {"bad": {"author_id": 200, "completed_epoch": "broken"},
               "good": {"author_id": 200, "completed_epoch": 90}}
    state["clarification_reply_records"] = records
    monkeypatch.setattr(bot, "CLARIFICATION_REPLY_WINDOW_SECONDS", 10)
    assert bot.author_used_clarification_recently(state, "200", current=100) is False
    assert bot.author_used_clarification_recently(state, "200", current=99) is True
    with pytest.raises(ValueError):
        bot.author_used_clarification_recently(state, "200", current="broken")


def test_target_own_author_and_structured_entities_precede_current_text_regex(monkeypatch):
    monkeypatch.setattr(bot, "MY_USER_ID", "42")
    monkeypatch.setattr(bot, "MY_USERNAME", "Current")
    current_re = SimpleNamespace(search=Mock(wraps=re.search), escape=re.escape, IGNORECASE=re.IGNORECASE)
    monkeypatch.setattr(bot, "re", current_re)
    assert bot.reply_target_is_directly_eligible({"author_id": 42, "entities": {}}) is True
    assert bot.reply_target_is_directly_eligible({"text": "@Current", "entities": {"mentions": None}}) is False
    current_re.search.assert_not_called()
    assert bot.reply_target_is_directly_eligible({"text": "@Current", "entities": []}) is True
    current_re.search.assert_called_once_with(r"(?<![A-Za-z0-9_])@Current(?![A-Za-z0-9_])", "@Current", flags=re.IGNORECASE)
    failure = TypeError("current regex failure")
    current_re.search.side_effect = failure
    with pytest.raises(TypeError) as caught:
        bot.reply_target_is_directly_eligible({"text": "@Current"})
    assert caught.value is failure


def test_spam_preserves_current_pattern_order_raw_logs_and_thresholds(monkeypatch):
    trace = Mock()
    trace.search.side_effect = [None, True]
    monkeypatch.setattr(bot, "re", SimpleNamespace(search=trace.search))
    monkeypatch.setattr(bot, "log", trace.log)
    monkeypatch.setattr(bot, "SPAMMY_PATTERNS", ["first", "second", "unreached"])
    raw = "  CURRENT Text!!!!! "
    assert bot.is_probably_spam_or_not_worth_replying(raw) is True
    assert trace.mock_calls == [
        call.log.debug("Spam check for text=%r", raw),
        call.search("first", "current text!!!!!"), call.search("second", "current text!!!!!"),
        call.log.info("Ignoring post: matched spam pattern %s", "second"),
    ]
    monkeypatch.setattr(bot, "SPAMMY_PATTERNS", [])
    trace.reset_mock()
    assert bot.is_probably_spam_or_not_worth_replying("hello!!!!!") is True
    assert trace.mock_calls == [call.log.debug("Spam check for text=%r", "hello!!!!!"),
                                call.log.info("Ignoring post: too many exclamation marks")]
    assert bot.is_probably_spam_or_not_worth_replying("@name hello!!!!") is False
    assert bot.is_probably_spam_or_not_worth_replying("@name http://example.invalid hello") is True
    trace.log.info.assert_called_with("Ignoring post: mostly links/mentions")
    trace.reset_mock()
    assert bot.is_probably_spam_or_not_worth_replying("") is False
    assert trace.mock_calls == [call.log.debug("Spam check for text=%r", ""), call.log.debug("Post passed spam check")]
    trace.reset_mock()
    with pytest.raises(AttributeError):
        bot.is_probably_spam_or_not_worth_replying(None)
    assert trace.mock_calls == []


def test_clarification_refreshes_legacy_question_before_looking_for_question_mark(monkeypatch, confirmed_question):
    state, candidate = confirmed_question
    original = state["tweet_cache"]["100"]
    full = "The earlier explanation is background. " * 10 + "Where did people move when the Berlin Wall fell?"
    original["text"] = full[:280]
    original.pop("text_is_complete")
    fresh = {**original, "note_tweet": {"text": full}}
    fetch = Mock(return_value=fresh)
    monkeypatch.setattr(bot, "get_tweet_by_id", fetch)
    result = bot.clarification_reply_context(state, candidate, current=2_000_000_001)
    assert result["question_text"] == full
    assert result["trigger"] == "explicit_correction"
    assert state["tweet_cache"]["100"]["text_is_complete"] is True
    assert bot.clarification_reply_context(state, candidate, current=2_000_000_001) == result
    fetch.assert_called_once_with("100")


@pytest.mark.parametrize("status", [404, 503])
def test_clarification_legacy_refresh_distinguishes_deleted_from_transient(monkeypatch, confirmed_question, status):
    state, candidate = confirmed_question
    state["tweet_cache"]["100"].pop("text_is_complete")
    error = bot.ApiError("lookup failed", service="x", status_code=status, request_method="GET", request_path="/2/tweets/100")
    monkeypatch.setattr(bot, "get_tweet_by_id", Mock(side_effect=error))
    if status == 404:
        assert bot.clarification_reply_context(state, candidate, current=2_000_000_001) is None
    else:
        with pytest.raises(bot.ApiError) as caught:
            bot.clarification_reply_context(state, candidate, current=2_000_000_001)
        assert caught.value is error
