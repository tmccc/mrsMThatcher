"""Exercise author quarantine state and migration rules at their owner."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
import inspect
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock, call

import pytest

import mrs_bot_author_quarantines as quarantines
import mrs_bot_reply_evaluation_state as evaluation_state
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401


OWNER_INPUTS = {
    "now_epoch": "now_epoch", "log": "log", "log_event": "log_event",
    "maximum_state_epoch": "MAX_REASONABLE_STATE_EPOCH",
    "threshold": "AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD",
    "window_seconds": "AUTHOR_NO_REPLY_QUARANTINE_WINDOW_SECONDS",
    "quarantine_seconds": "AUTHOR_NO_REPLY_QUARANTINE_SECONDS",
    "evidence_policy": "AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY",
}


@pytest.fixture
def make_owner():
    """Compose quarantine rules with the existing isolated configuration."""
    def build(**overrides):
        current = {field: getattr(bot, name) for field, name in OWNER_INPUTS.items()}
        return quarantines.AuthorQuarantines(**{**current, **overrides})
    return build


def test_import_needs_no_runtime_access_and_clear_aliases_share_owner():
    for name in (
        "AUTHOR_EVALUATION_QUARANTINE_LEGACY_EVIDENCE_POLICY",
        "AUTHOR_EVALUATION_QUARANTINE_PREVIOUS_EVIDENCE_POLICY",
        "AUTHOR_EVALUATION_QUARANTINE_SEEDED_EVIDENCE_POLICY",
        "AUTHOR_EVALUATION_QUARANTINE_SINGLE_SOL_V1_EVIDENCE_POLICY",
    ):
        assert getattr(bot, name) is getattr(quarantines, name)
    code = """
import builtins, collections.abc, dataclasses, io, logging, os, random, socket, sys, time
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('author quarantines import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'reply_evidence'} or name.startswith('mrs_bot_') and name != 'mrs_bot_author_quarantines':
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
import mrs_bot_author_quarantines
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
assert 'requests' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert bot.clear_author_evaluation_quarantine_history is quarantines.clear_author_evaluation_quarantine_history
    assert evaluation_state.clear_author_evaluation_quarantine_history is quarantines.clear_author_evaluation_quarantine_history


def test_owner_composition_binds_current_dependencies_without_calling_them(monkeypatch):
    snapshots = []
    for _ in range(2):
        current = {field: Mock() for field in OWNER_INPUTS}
        for field, name in OWNER_INPUTS.items():
            monkeypatch.setattr(bot, name, current[field])
        owner = bot._author_quarantine_owner()
        assert isinstance(owner, quarantines.AuthorQuarantines)
        for field, value in current.items():
            assert getattr(owner, field) is value
            value.assert_not_called()
        snapshots.append((owner, current))
    first, inputs = snapshots[0]
    assert first is not snapshots[1][0]
    assert all(getattr(first, field) is value for field, value in inputs.items())
    with pytest.raises(FrozenInstanceError):
        first.threshold = 1


def test_adapters_preserve_defaults_arguments_result_identity_and_errors(monkeypatch):
    methods = {
        "author_no_reply_epoch_limit": "epoch_limit",
        "prune_author_evaluation_quarantines": "prune",
        "active_author_evaluation_quarantine": "active",
        "record_qualifying_author_no_reply": "record_no_reply",
        "normalise_author_evaluation_quarantines": "normalise",
    }
    for name, method_name in methods.items():
        adapter = getattr(bot, name)
        public = inspect.signature(adapter).parameters
        args = tuple(object() for param in public.values() if param.kind == param.POSITIONAL_OR_KEYWORD)
        for use_defaults in (True, False):
            owner = Mock(spec=quarantines.AuthorQuarantines)
            factory = Mock(return_value=owner)
            monkeypatch.setattr(bot, "_author_quarantine_owner", factory)
            implementation = getattr(owner, method_name)
            result = object()
            implementation.return_value = result
            options = {
                key: object() for key, param in public.items()
                if param.kind == param.KEYWORD_ONLY
                and (not use_defaults or param.default is param.empty)
            }
            expected = {
                key: param.default for key, param in public.items()
                if param.kind == param.KEYWORD_ONLY and param.default is not param.empty
            } | options
            assert adapter(*args, **options) is result
            factory.assert_called_once_with()
            actual_args, actual_kwargs = implementation.call_args
            assert len(actual_args) == len(args)
            assert all(actual is original for actual, original in zip(actual_args, args))
            assert actual_kwargs.keys() == expected.keys()
            assert all(actual_kwargs[key] is value for key, value in expected.items())
            failure = TypeError(name)
            implementation.side_effect = failure
            with pytest.raises(TypeError) as caught:
                adapter(*args, **options)
            assert caught.value is failure


def test_active_pruning_and_clear_history_preserve_unchanged_record_references(monkeypatch, make_owner):
    clock = Mock(return_value=1000)
    owner = make_owner(now_epoch=clock)
    state = {"author_evaluation_quarantines": {}}
    owner.record_no_reply(state, "1", current_epoch=990)
    original = state["author_evaluation_quarantines"]
    record = original["1"]
    record["quarantine_until_epoch"] = 1100
    original["2"] = record
    strikes = record["recent_no_reply_epochs"]
    assert owner.prune(state, current_epoch=1000) is False
    assert state["author_evaluation_quarantines"] is original
    prune = Mock(wraps=owner.prune)
    monkeypatch.setattr(quarantines.AuthorQuarantines, "prune", prune)
    assert owner.active(state, 1) is record
    assert record["recent_no_reply_epochs"] is strikes
    prune.assert_called_once_with(state, current_epoch=1000)
    clock.assert_called_once_with()
    assert quarantines.clear_author_evaluation_quarantine_history(state, "absent") is False
    assert state["author_evaluation_quarantines"] is original
    assert quarantines.clear_author_evaluation_quarantine_history(state, 1) is True
    assert state["author_evaluation_quarantines"] is not original
    assert state["author_evaluation_quarantines"]["2"] is record
    assert original["1"] is record


def test_expiration_event_failure_precedes_assignment_and_keeps_native_error(make_owner):
    owner = make_owner(threshold=1, quarantine_seconds=20)
    state = {"author_evaluation_quarantines": {}}
    owner.record_no_reply(state, "1", current_epoch=980)
    records = state["author_evaluation_quarantines"]
    failure = RuntimeError("expiry event")
    event = Mock(side_effect=failure)
    owner = replace(owner, log_event=event)
    with pytest.raises(RuntimeError) as caught:
        owner.prune(state, current_epoch=1000)
    assert caught.value is failure
    assert state["author_evaluation_quarantines"] is records
    assert records["1"]["recent_no_reply_epochs"] == [980]
    assert records["1"]["quarantine_until_epoch"] == 1000
    event.assert_called_once_with("author_evaluation_quarantine_expired", author_id="1",
                                  quarantine_until_epoch=1000, expired_epoch=1000)
    event.side_effect = None
    assert owner.prune(state, current_epoch=1000) is True
    assert state["author_evaluation_quarantines"] == {}


def test_strike_validation_strict_flag_and_start_event_precede_assignment(monkeypatch, make_owner):
    clock = Mock(return_value=1000)
    owner = make_owner(threshold=1, quarantine_seconds=50, evidence_policy="current-policy", now_epoch=clock)
    prune = Mock(wraps=owner.prune)
    monkeypatch.setattr(quarantines.AuthorQuarantines, "prune", prune)
    records = {}
    state = {"author_evaluation_quarantines": records}
    assert owner.record_no_reply(state, "invalid") is False
    clock.assert_not_called()
    prune.assert_not_called()
    failure = RuntimeError("start event")

    def start_event(name, **values):
        assert name == "author_evaluation_quarantine_started"
        assert values["quarantine_until_epoch"] == 1050
        assert state["author_evaluation_quarantines"] is records and records == {}
        assert prune.call_args == call(state, current_epoch=1000)
        raise failure

    event = Mock(side_effect=start_event)
    owner = replace(owner, log_event=event)
    with pytest.raises(RuntimeError) as caught:
        owner.record_no_reply(state, 1, explicit_spam_or_abuse=1)
    assert caught.value is failure
    assert state["author_evaluation_quarantines"] is records
    event.side_effect = None
    assert owner.record_no_reply(state, 1, explicit_spam_or_abuse=1) is True
    first = state["author_evaluation_quarantines"]["1"]
    assert first["latest_explicit_spam_or_abuse_epoch"] == 0
    assert first["evidence_policy"] == "current-policy"
    event.reset_mock()
    assert owner.record_no_reply(state, 1, current_epoch=1001) is False
    second = state["author_evaluation_quarantines"]["1"]
    assert second is not first and first["recent_no_reply_epochs"] == [1000]
    assert second["recent_no_reply_epochs"] == [1000, 1001]
    assert second["latest_explicit_spam_or_abuse_epoch"] == 1001
    assert second["quarantine_until_epoch"] == first["quarantine_until_epoch"] == 1050
    event.assert_not_called()


def test_normalizer_copies_all_containers_uses_current_limit_and_never_expires(monkeypatch, tmp_path, make_owner):
    owner = make_owner(now_epoch=Mock(side_effect=AssertionError("normalizer clock")))
    state = {"author_evaluation_quarantines": {}}
    owner.record_no_reply(state, "1", current_epoch=1)
    record = state["author_evaluation_quarantines"]["1"]
    record["quarantine_until_epoch"] = 5
    value = {1: record}
    limit = Mock(return_value=1)
    monkeypatch.setattr(quarantines.AuthorQuarantines, "epoch_limit", limit)
    result = owner.normalise(value, path=tmp_path / "state.json")
    assert result == {"1": record} and result is not value
    assert result["1"] is not record
    assert result["1"]["recent_no_reply_epochs"] is not record["recent_no_reply_epochs"]
    assert result["1"]["quarantine_until_epoch"] == 5
    limit.assert_called_once_with()
    limit.return_value = 0
    assert owner.normalise(value, path=tmp_path / "state.json") is None
    assert value == {1: record} and record["recent_no_reply_epochs"] == [1]


def test_normalizer_migrates_single_sol_strikes_without_current_time_or_expiry(make_owner, tmp_path):
    owner = make_owner(now_epoch=Mock(side_effect=AssertionError("migration clock")),
                       evidence_policy="current-policy")
    record = {
        "recent_no_reply_epochs": [10, 20], "quarantine_until_epoch": 90,
        "last_updated_epoch": 20, "latest_explicit_spam_or_abuse_epoch": 10,
        "evidence_policy": quarantines.AUTHOR_EVALUATION_QUARANTINE_SINGLE_SOL_V1_EVIDENCE_POLICY,
    }
    original = {"1": record}
    result = owner.normalise(original, path=tmp_path / "state.json")
    assert result == {"1": {
        **record, "recent_no_reply_epochs": [10], "quarantine_until_epoch": 0,
        "evidence_policy": "current-policy",
    }}
    assert result is not original and result["1"] is not record
    assert record["recent_no_reply_epochs"] == [10, 20] and record["quarantine_until_epoch"] == 90
    record["recent_no_reply_epochs"] = [20]
    assert owner.normalise(original, path=tmp_path / "state.json") == {}
    assert original["1"] is record
    owner.now_epoch.assert_not_called()
