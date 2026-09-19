from __future__ import annotations

from dataclasses import FrozenInstanceError
import inspect
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock, call

import pytest

import mrs_bot_reply_evaluation_state as evaluation_state
from tests.helpers.reply_fixtures import configure_normal_cycle as _configure_cycle
from tests.helpers.mention_fixtures import mention, queue_active_mention
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401
from tests.helpers.reply_fixtures import reply_evaluation_record, patch_reply_owner_method


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, dataclasses, io, logging, os, random, socket, sys, time
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('reply evaluation state import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'reply_evidence'} or name.startswith('mrs_bot_') and name not in {'mrs_bot_reply_evaluation_state', 'mrs_bot_author_quarantines'}:
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
import mrs_bot_reply_evaluation_state
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
assert 'requests' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout


OWNER_INPUTS = {
    "now_epoch": "now_epoch", "log": "log",
    "quarantine_evidence_policy": "AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY",
    "maximum_state_epoch": "MAX_REASONABLE_STATE_EPOCH",
    "maximum_records": "REPLY_EVALUATION_MAX_RECORDS",
    "minimum_retention_seconds": "REPLY_EVALUATION_MIN_RETENTION_SECONDS",
}


@pytest.fixture
def make_owner():
    """Compose terminal ledger operations with isolated runtime boundaries."""
    def build(**overrides):
        current = {field: getattr(bot, name) for field, name in OWNER_INPUTS.items()}
        return evaluation_state.ReplyEvaluations(**{**current, **overrides})
    return build


def test_owner_composition_binds_current_dependencies_without_calling_them(monkeypatch):
    snapshots = []
    for _ in range(2):
        current = {field: Mock() for field in OWNER_INPUTS}
        for field, name in OWNER_INPUTS.items():
            monkeypatch.setattr(bot, name, current[field])
        owner = bot._reply_evaluation_owner()
        assert isinstance(owner, evaluation_state.ReplyEvaluations)
        for field, value in current.items():
            assert getattr(owner, field) is value
            value.assert_not_called()
        snapshots.append((owner, current))
    first, inputs = snapshots[0]
    assert first is not snapshots[1][0]
    assert all(getattr(first, field) is value for field, value in inputs.items())
    with pytest.raises(FrozenInstanceError):
        first.maximum_records = 1


def test_adapters_preserve_defaults_argument_result_identity_and_native_errors(monkeypatch):
    methods = {
        "prune_completed_mention_quarantine_evaluations": "prune_completed_mentions",
        "prune_reply_evaluation_records": "prune",
        "record_terminal_reply_evaluation": "record",
    }
    for name, method_name in methods.items():
        adapter = getattr(bot, name)
        public = inspect.signature(adapter).parameters
        args = tuple(object() for param in public.values() if param.kind == param.POSITIONAL_OR_KEYWORD)
        for use_defaults in (True, False):
            owner = Mock(spec=evaluation_state.ReplyEvaluations)
            factory = Mock(return_value=owner)
            monkeypatch.setattr(bot, "_reply_evaluation_owner", factory)
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
    for name in ("completed_mention_watermark_covers_target",
                 "clear_author_evaluation_quarantine_history", "terminal_reply_evaluation"):
        assert getattr(bot, name) is getattr(evaluation_state, name)


def test_completed_watermark_keeps_original_digit_and_pending_contract():
    state = {"last_seen_mention_id": str(10**90), "mention_pending_candidates": {}}
    assert evaluation_state.completed_mention_watermark_covers_target(state, 10**89)
    state["mention_pending_candidates"]["1"] = {}
    assert not evaluation_state.completed_mention_watermark_covers_target(state, 1)
    assert not evaluation_state.completed_mention_watermark_covers_target(state, " 2")
    state["mention_pending_candidates"] = None
    assert not evaluation_state.completed_mention_watermark_covers_target(state, 2)
    state["mention_pending_candidates"] = {}
    with pytest.raises(ValueError):
        evaluation_state.completed_mention_watermark_covers_target(state, "²")


def test_completed_pruning_keeps_record_references_and_assigns_before_log_failure(monkeypatch, make_owner):
    policy = bot.AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY
    retained = dict(reply_evaluation_record("3", 950),
                    reason="author_evaluation_quarantine", evidence_policy=policy)
    unrelated = reply_evaluation_record("4", 950)
    original = {
        1: dict(retained, target_id="1", evidence_policy="old-policy"),
        2: dict(retained, target_id="2"), "3": retained, 4: unrelated,
    }
    state = {"reply_evaluation_records": original}
    covered = Mock(side_effect=lambda received, target: received is state and target == "2")
    monkeypatch.setattr(evaluation_state, "completed_mention_watermark_covers_target", covered)
    failure = RuntimeError("retirement log")
    logger = Mock()
    logger.info.side_effect = failure
    owner = make_owner(log=logger)
    with pytest.raises(RuntimeError) as caught:
        owner.prune_completed_mentions(state)
    assert caught.value is failure
    assert covered.call_args_list == [call(state, "2"), call(state, "3")]
    records = state["reply_evaluation_records"]
    assert records is not original and list(records) == ["3", "4"]
    assert records["3"] is retained and records["4"] is unrelated
    assert list(original) == [1, 2, "3", 4]
    assert logger.info.call_args.args[1] == 2
    assert owner.prune_completed_mentions(state) == 0
    assert state["reply_evaluation_records"] is records
    assert logger.info.call_count == 1


def test_retention_keeps_bad_epochs_and_shallow_copies_with_original_log_order(monkeypatch, make_owner):
    nested = {"shared": []}
    records = {key: dict(reply_evaluation_record(key, epoch), extra=nested)
               for key, epoch in (("cutoff", 900), ("recent", 950), ("skip", 970),
                                  ("bool", True), ("future", 1001))}
    for key in ("skip", "bool"):
        records[key]["reason"] = "author_evaluation_quarantine"
    state = {"reply_evaluation_records": records}
    trace = []
    monkeypatch.setattr(evaluation_state.ReplyEvaluations, "prune_completed_mentions",
                        Mock(side_effect=lambda received: trace.append(("completed", received))))

    def warning(*args):
        assert state["reply_evaluation_records"] is records
        trace.append(("warning", args))

    def info(*args):
        assert state["reply_evaluation_records"] is not records
        trace.append(("info", args))

    owner = make_owner(
        maximum_records=2, minimum_retention_seconds=100, maximum_state_epoch=1000,
        now_epoch=lambda: trace.append(("clock", state)) or 1000,
        log=Mock(warning=warning, info=info),
    )
    owner.prune(state)
    assert trace[:2] == [("completed", state), ("clock", state)]
    assert [event[0] for event in trace] == ["completed", "clock", "warning", "warning", "info"]
    retained = state["reply_evaluation_records"]
    assert list(retained) == ["recent", "bool", "future"]
    for key, record in retained.items():
        assert record == records[key] and record is not records[key]
        assert record["extra"] is nested
    assert list(records) == ["cutoff", "recent", "skip", "bool", "future"]


def test_completed_pruning_precedes_native_epoch_failure(monkeypatch, make_owner):
    state = {"reply_evaluation_records": {"1": reply_evaluation_record("1", 1)}}
    trace = Mock()
    monkeypatch.setattr(evaluation_state.ReplyEvaluations, "prune_completed_mentions", trace.completed)
    owner = make_owner(now_epoch=trace.clock)
    with pytest.raises(ValueError):
        owner.prune(state, current_epoch="invalid")
    assert trace.mock_calls == [call.completed(state)]


@pytest.mark.parametrize("prune_records", [True, False])
def test_terminal_recording_keeps_batch_identity_and_assignment_before_prune_error(monkeypatch, make_owner, prune_records):
    existing = reply_evaluation_record("existing", 1)
    records = {"existing": existing}
    state = {"reply_evaluation_records": records}
    clock = Mock(return_value=1000)
    owner = make_owner(now_epoch=clock)
    failure = RuntimeError("terminal pruning")
    prune = Mock(side_effect=failure)
    monkeypatch.setattr(evaluation_state.ReplyEvaluations, "prune", prune)
    options = dict(target_id=7, lane=8, reason="", prune_records=prune_records)
    if prune_records:
        with pytest.raises(RuntimeError) as caught:
            owner.record(state, **options)
        assert caught.value is failure
        prune.assert_called_once_with(state)
        assert state["reply_evaluation_records"] is not records
        assert "7" not in records
    else:
        assert owner.record(state, **options) is None
        prune.assert_not_called()
        assert state["reply_evaluation_records"] is records
    assert state["reply_evaluation_records"]["existing"] is existing
    record = state["reply_evaluation_records"]["7"]
    assert record == {"target_id": "7", "lane": "8", "outcome": "no_reply",
                      "reason": "model_selected_no_reply", "evaluated_epoch": 1000}
    clock.assert_called_once_with()
    for outcome in ("no_reply", "operational_failure", "reply_not_permitted"):
        record["outcome"] = outcome
        assert evaluation_state.terminal_reply_evaluation(state, 7) is record
    record["outcome"] = "reply"
    assert evaluation_state.terminal_reply_evaluation(state, 7) is None


def test_invalid_terminal_outcome_precedes_state_and_clock_access(make_owner):
    clock = Mock(side_effect=AssertionError("terminal clock"))
    owner = make_owner(now_epoch=clock)
    with pytest.raises(ValueError, match="Unsupported terminal reply outcome"):
        owner.record(object(), target_id="1", lane="mention",
                     reason="invalid", outcome="reply")
    clock.assert_not_called()


def test_recording_and_pruning_sample_clock_separately(make_owner):
    previous = reply_evaluation_record("old", 995)
    original = {"old": previous}
    state = {"reply_evaluation_records": original}
    clock = Mock(side_effect=[1000, 1100])
    owner = make_owner(now_epoch=clock, maximum_records=1, minimum_retention_seconds=50)
    owner.record(state, target_id="new", lane="mention", reason="completed_exchange")
    assert clock.call_args_list == [call(), call()]
    assert list(state["reply_evaluation_records"]) == ["new"]
    assert state["reply_evaluation_records"]["new"]["evaluated_epoch"] == 1000
    assert original == {"old": previous} and original["old"] is previous


def test_quarantine_skip_batch_is_durable_across_real_state_reload(monkeypatch):
    _configure_cycle(monkeypatch)
    state = bot.default_state()
    queue_active_mention(state, mention(105, 200), base_since_id="99")
    state["mention_pending_candidates"]["104"] = mention(104, 200)
    for epoch in (1_999_999_997, 1_999_999_998, 1_999_999_999):
        bot.record_qualifying_author_no_reply(state, "200", current_epoch=epoch)
    save = Mock(wraps=bot.save_state)
    owner = bot._reply_evaluation_owner()
    record = Mock(wraps=owner.record)
    prune = Mock(wraps=owner.prune)
    monkeypatch.setattr(bot, "save_state", save)
    patch_reply_owner_method(monkeypatch, evaluation_state.ReplyEvaluations, "record", record)
    patch_reply_owner_method(monkeypatch, evaluation_state.ReplyEvaluations, "prune", prune)

    assert bot.maybe_reply_to_mentions(
        state, _fresh_mention_ai_evaluations=bot.MAX_MENTIONS_PER_CHECK,
    ) == bot.NORMAL_CHECK_STATUS_CHECKED

    save.assert_called_once_with(state, durable=True)
    assert [item.kwargs["target_id"] for item in record.call_args_list] == ["104", "105"]
    assert all(item.kwargs["prune_records"] is False for item in record.call_args_list)
    # Batch pruning mutates runtime state once; publication also validates
    # detached candidate documents through the same reader contract.
    assert sum(item.args[0] is state for item in prune.call_args_list) == 1
    loaded = bot.load_state()
    assert loaded["mention_pending_candidates"] == {}
    assert loaded["mention_backlog"] == state["mention_backlog"]
    assert loaded["last_seen_mention_id"] == "99"
    assert loaded["reply_evaluation_records"] == state["reply_evaluation_records"]
    assert set(loaded["reply_evaluation_records"]) == {"104", "105"}
    assert all(item["reason"] == "author_evaluation_quarantine"
               and item["evidence_policy"] == bot.AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY
               for item in loaded["reply_evaluation_records"].values())
    assert loaded["daily_reply_count"] == 0
    bot.evaluate_single_call_reply.assert_not_called()
    bot.x_request.assert_not_called()
    bot.create_post.assert_not_called()
