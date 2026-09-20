from __future__ import annotations

import inspect
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrs_bot_reply_lane_policy as policy
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, dataclasses, io, logging, os, random, re, socket, sys, time
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('reply lane policy import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply'} or name.startswith('mrs_bot_') and name not in {'mrs_bot_daily_reply_accounting', 'mrs_bot_reply_clarifications', 'mrs_bot_reply_lane_policy', 'mrs_bot_reply_native_media', 'mrs_bot_runtime_state_helpers', 'mrs_bot_tweet_lookup_cache'}:
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
