from __future__ import annotations

import inspect
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock, call

import pytest

import mrs_bot_reply_evaluation_state as evaluation_state
from tests.test_bot_normal_reply_cycle import _configure_cycle
from tests.test_mention_backlog_author_quarantine import mention, queue_active_mention
from tests.test_unit_helpers import bot, isolate_regular_post_receipt, reply_evaluation_record


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, io, logging, os, random, socket, sys, time
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('reply evaluation state import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'reply_evidence'} or name.startswith('mrs_bot_') and name != 'mrs_bot_reply_evaluation_state':
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


def test_adapters_forward_current_dependencies_defaults_references_and_native_errors(monkeypatch):
    for name, count in (
        ("prune_completed_mention_quarantine_evaluations", 3),
        ("prune_reply_evaluation_records", 6), ("author_no_reply_epoch_limit", 1),
        ("prune_author_evaluation_quarantines", 6),
        ("active_author_evaluation_quarantine", 2),
        ("record_qualifying_author_no_reply", 8),
        ("normalise_author_evaluation_quarantines", 8),
        ("record_terminal_reply_evaluation", 2),
    ):
        adapter = getattr(bot, name)
        public = inspect.signature(adapter).parameters
        dependencies = inspect.signature(getattr(evaluation_state, name)).parameters.keys() - public.keys()
        assert len(dependencies) == count
        args = tuple(object() for param in public.values() if param.kind == param.POSITIONAL_OR_KEYWORD)
        result = object()
        owner = Mock(return_value=result)
        with monkeypatch.context() as patch:
            patch.setattr(evaluation_state, name, owner)
            for use_defaults in (True, False):
                current = {key: object() for key in dependencies}
                for key, value in current.items():
                    patch.setattr(bot, key, value)
                options = {
                    key: object() for key, param in public.items()
                    if param.kind == param.KEYWORD_ONLY
                    and (not use_defaults or param.default is param.empty)
                }
                expected = {
                    key: param.default for key, param in public.items()
                    if param.kind == param.KEYWORD_ONLY and param.default is not param.empty
                } | options | current
                assert adapter(*args, **options) is result
                actual_args, actual_kwargs = owner.call_args
                assert len(actual_args) == len(args)
                assert all(actual is original for actual, original in zip(actual_args, args))
                assert actual_kwargs.keys() == expected.keys()
                assert all(actual_kwargs[key] is value for key, value in expected.items())
            failure = TypeError(name)
            owner.side_effect = failure
            with pytest.raises(TypeError) as caught:
                adapter(*args, **options)
            assert caught.value is failure
    for name in ("completed_mention_watermark_covers_target",
                 "clear_author_evaluation_quarantine_history", "terminal_reply_evaluation"):
        assert getattr(bot, name) is getattr(evaluation_state, name)


def test_completed_watermark_keeps_original_digit_and_pending_contract():
    state = {"last_seen_mention_id": str(10**90), "mention_pending_candidates": {}}
    assert bot.completed_mention_watermark_covers_target(state, 10**89)
    state["mention_pending_candidates"]["1"] = {}
    assert not bot.completed_mention_watermark_covers_target(state, 1)
    assert not bot.completed_mention_watermark_covers_target(state, " 2")
    state["mention_pending_candidates"] = None
    assert not bot.completed_mention_watermark_covers_target(state, 2)
    state["mention_pending_candidates"] = {}
    with pytest.raises(ValueError):
        bot.completed_mention_watermark_covers_target(state, "²")


def test_completed_pruning_keeps_record_references_and_assigns_before_log_failure(monkeypatch):
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
    monkeypatch.setattr(bot, "completed_mention_watermark_covers_target", covered)
    failure = RuntimeError("retirement log")
    logger = Mock()
    logger.info.side_effect = failure
    monkeypatch.setattr(bot, "log", logger)
    with pytest.raises(RuntimeError) as caught:
        bot.prune_completed_mention_quarantine_evaluations(state)
    assert caught.value is failure
    assert covered.call_args_list == [call(state, "2"), call(state, "3")]
    records = state["reply_evaluation_records"]
    assert records is not original and list(records) == ["3", "4"]
    assert records["3"] is retained and records["4"] is unrelated
    assert list(original) == [1, 2, "3", 4]
    assert logger.info.call_args.args[1] == 2
    assert bot.prune_completed_mention_quarantine_evaluations(state) == 0
    assert state["reply_evaluation_records"] is records
    assert logger.info.call_count == 1


def test_retention_keeps_bad_epochs_and_shallow_copies_with_original_log_order(monkeypatch):
    monkeypatch.setattr(bot, "REPLY_EVALUATION_MAX_RECORDS", 2)
    monkeypatch.setattr(bot, "REPLY_EVALUATION_MIN_RETENTION_SECONDS", 100)
    monkeypatch.setattr(bot, "MAX_REASONABLE_STATE_EPOCH", 1000)
    nested = {"shared": []}
    records = {key: dict(reply_evaluation_record(key, epoch), extra=nested)
               for key, epoch in (("cutoff", 900), ("recent", 950), ("skip", 970),
                                  ("bool", True), ("future", 1001))}
    for key in ("skip", "bool"):
        records[key]["reason"] = "author_evaluation_quarantine"
    state = {"reply_evaluation_records": records}
    trace = []
    monkeypatch.setattr(bot, "prune_completed_mention_quarantine_evaluations",
                        lambda received: trace.append(("completed", received)))
    monkeypatch.setattr(bot, "now_epoch", lambda: trace.append(("clock", state)) or 1000)

    def warning(*args):
        assert state["reply_evaluation_records"] is records
        trace.append(("warning", args))

    def info(*args):
        assert state["reply_evaluation_records"] is not records
        trace.append(("info", args))

    monkeypatch.setattr(bot, "log", Mock(warning=warning, info=info))
    bot.prune_reply_evaluation_records(state)
    assert trace[:2] == [("completed", state), ("clock", state)]
    assert [event[0] for event in trace] == ["completed", "clock", "warning", "warning", "info"]
    retained = state["reply_evaluation_records"]
    assert list(retained) == ["recent", "bool", "future"]
    for key, record in retained.items():
        assert record == records[key] and record is not records[key]
        assert record["extra"] is nested
    assert list(records) == ["cutoff", "recent", "skip", "bool", "future"]


def test_completed_pruning_precedes_native_epoch_failure(monkeypatch):
    state = {"reply_evaluation_records": {}}
    trace = Mock()
    monkeypatch.setattr(bot, "prune_completed_mention_quarantine_evaluations", trace.completed)
    monkeypatch.setattr(bot, "now_epoch", trace.clock)
    with pytest.raises(ValueError):
        bot.prune_reply_evaluation_records(state, current_epoch="invalid")
    assert trace.mock_calls == [call.completed(state)]


def test_active_pruning_and_clear_history_preserve_unchanged_record_references(monkeypatch):
    state = {"author_evaluation_quarantines": {}}
    bot.record_qualifying_author_no_reply(state, "1", current_epoch=990)
    original = state["author_evaluation_quarantines"]
    record = original["1"]
    record["quarantine_until_epoch"] = 1100
    original["2"] = record
    strikes = record["recent_no_reply_epochs"]
    assert bot.prune_author_evaluation_quarantines(state, current_epoch=1000) is False
    assert state["author_evaluation_quarantines"] is original
    prune = Mock(wraps=bot.prune_author_evaluation_quarantines)
    clock = Mock(return_value=1000)
    monkeypatch.setattr(bot, "prune_author_evaluation_quarantines", prune)
    monkeypatch.setattr(bot, "now_epoch", clock)
    assert bot.active_author_evaluation_quarantine(state, 1) is record
    assert record["recent_no_reply_epochs"] is strikes
    prune.assert_called_once_with(state, current_epoch=1000)
    clock.assert_called_once_with()
    assert bot.clear_author_evaluation_quarantine_history(state, "absent") is False
    assert state["author_evaluation_quarantines"] is original
    assert bot.clear_author_evaluation_quarantine_history(state, 1) is True
    assert state["author_evaluation_quarantines"] is not original
    assert state["author_evaluation_quarantines"]["2"] is record
    assert original["1"] is record


def test_expiration_event_failure_precedes_assignment_and_keeps_native_error(monkeypatch):
    monkeypatch.setattr(bot, "AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD", 1)
    monkeypatch.setattr(bot, "AUTHOR_NO_REPLY_QUARANTINE_SECONDS", 20)
    state = {"author_evaluation_quarantines": {}}
    bot.record_qualifying_author_no_reply(state, "1", current_epoch=980)
    records = state["author_evaluation_quarantines"]
    failure = RuntimeError("expiry event")
    event = Mock(side_effect=failure)
    monkeypatch.setattr(bot, "log_event", event)
    with pytest.raises(RuntimeError) as caught:
        bot.prune_author_evaluation_quarantines(state, current_epoch=1000)
    assert caught.value is failure
    assert state["author_evaluation_quarantines"] is records
    assert records["1"]["recent_no_reply_epochs"] == [980]
    assert records["1"]["quarantine_until_epoch"] == 1000
    event.assert_called_once_with("author_evaluation_quarantine_expired", author_id="1",
                                  quarantine_until_epoch=1000, expired_epoch=1000)
    event.side_effect = None
    assert bot.prune_author_evaluation_quarantines(state, current_epoch=1000) is True
    assert state["author_evaluation_quarantines"] == {}


def test_strike_validation_strict_flag_and_start_event_precede_assignment(monkeypatch):
    monkeypatch.setattr(bot, "AUTHOR_NO_REPLY_QUARANTINE_THRESHOLD", 1)
    monkeypatch.setattr(bot, "AUTHOR_NO_REPLY_QUARANTINE_SECONDS", 50)
    monkeypatch.setattr(bot, "AUTHOR_EVALUATION_QUARANTINE_EVIDENCE_POLICY", "current-policy")
    clock = Mock(return_value=1000)
    prune = Mock(wraps=bot.prune_author_evaluation_quarantines)
    monkeypatch.setattr(bot, "now_epoch", clock)
    monkeypatch.setattr(bot, "prune_author_evaluation_quarantines", prune)
    records = {}
    state = {"author_evaluation_quarantines": records}
    assert bot.record_qualifying_author_no_reply(state, "invalid") is False
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
    monkeypatch.setattr(bot, "log_event", event)
    with pytest.raises(RuntimeError) as caught:
        bot.record_qualifying_author_no_reply(state, 1, explicit_spam_or_abuse=1)
    assert caught.value is failure
    assert state["author_evaluation_quarantines"] is records
    event.side_effect = None
    assert bot.record_qualifying_author_no_reply(state, 1, explicit_spam_or_abuse=1) is True
    first = state["author_evaluation_quarantines"]["1"]
    assert first["latest_explicit_spam_or_abuse_epoch"] == 0
    assert first["evidence_policy"] == "current-policy"
    event.reset_mock()
    assert bot.record_qualifying_author_no_reply(state, 1, current_epoch=1001) is False
    second = state["author_evaluation_quarantines"]["1"]
    assert second is not first and first["recent_no_reply_epochs"] == [1000]
    assert second["recent_no_reply_epochs"] == [1000, 1001]
    assert second["latest_explicit_spam_or_abuse_epoch"] == 1001
    assert second["quarantine_until_epoch"] == first["quarantine_until_epoch"] == 1050
    event.assert_not_called()


def test_normalizer_copies_all_containers_uses_current_limit_and_never_expires(monkeypatch, tmp_path):
    state = {"author_evaluation_quarantines": {}}
    bot.record_qualifying_author_no_reply(state, "1", current_epoch=1)
    record = state["author_evaluation_quarantines"]["1"]
    record["quarantine_until_epoch"] = 5
    value = {1: record}
    monkeypatch.setattr(bot, "now_epoch", Mock(side_effect=AssertionError("normalizer clock")))
    limit = Mock(return_value=1)
    monkeypatch.setattr(bot, "author_no_reply_epoch_limit", limit)
    result = bot.normalise_author_evaluation_quarantines(value, path=tmp_path / "state.json")
    assert result == {"1": record} and result is not value
    assert result["1"] is not record
    assert result["1"]["recent_no_reply_epochs"] is not record["recent_no_reply_epochs"]
    assert result["1"]["quarantine_until_epoch"] == 5
    limit.assert_called_once_with()
    limit.return_value = 0
    assert bot.normalise_author_evaluation_quarantines(value, path=tmp_path / "state.json") is None
    assert value == {1: record} and record["recent_no_reply_epochs"] == [1]


@pytest.mark.parametrize("prune_records", [True, False])
def test_terminal_recording_keeps_batch_identity_and_assignment_before_prune_error(monkeypatch, prune_records):
    existing = reply_evaluation_record("existing", 1)
    records = {"existing": existing}
    state = {"reply_evaluation_records": records}
    clock = Mock(return_value=1000)
    monkeypatch.setattr(bot, "now_epoch", clock)
    failure = RuntimeError("terminal pruning")
    prune = Mock(side_effect=failure)
    monkeypatch.setattr(bot, "prune_reply_evaluation_records", prune)
    options = dict(target_id=7, lane=8, reason="", prune_records=prune_records)
    if prune_records:
        with pytest.raises(RuntimeError) as caught:
            bot.record_terminal_reply_evaluation(state, **options)
        assert caught.value is failure
        prune.assert_called_once_with(state)
        assert state["reply_evaluation_records"] is not records
        assert "7" not in records
    else:
        assert bot.record_terminal_reply_evaluation(state, **options) is None
        prune.assert_not_called()
        assert state["reply_evaluation_records"] is records
    assert state["reply_evaluation_records"]["existing"] is existing
    record = state["reply_evaluation_records"]["7"]
    assert record == {"target_id": "7", "lane": "8", "outcome": "no_reply",
                      "reason": "model_selected_no_reply", "evaluated_epoch": 1000}
    clock.assert_called_once_with()
    for outcome in ("no_reply", "operational_failure", "reply_not_permitted"):
        record["outcome"] = outcome
        assert bot.terminal_reply_evaluation(state, 7) is record
    record["outcome"] = "reply"
    assert bot.terminal_reply_evaluation(state, 7) is None


def test_invalid_terminal_outcome_precedes_state_and_clock_access(monkeypatch):
    clock = Mock(side_effect=AssertionError("terminal clock"))
    monkeypatch.setattr(bot, "now_epoch", clock)
    with pytest.raises(ValueError, match="Unsupported terminal reply outcome"):
        bot.record_terminal_reply_evaluation(object(), target_id="1", lane="mention",
                                             reason="invalid", outcome="reply")
    clock.assert_not_called()


def test_quarantine_skip_batch_is_durable_across_real_state_reload(monkeypatch):
    _configure_cycle(monkeypatch)
    state = bot.default_state()
    queue_active_mention(state, mention(105, 200), base_since_id="99")
    state["mention_pending_candidates"]["104"] = mention(104, 200)
    for epoch in (1_999_999_997, 1_999_999_998, 1_999_999_999):
        bot.record_qualifying_author_no_reply(state, "200", current_epoch=epoch)
    save = Mock(wraps=bot.save_state)
    record = Mock(wraps=bot.record_terminal_reply_evaluation)
    prune = Mock(wraps=bot.prune_reply_evaluation_records)
    monkeypatch.setattr(bot, "save_state", save)
    monkeypatch.setattr(bot, "record_terminal_reply_evaluation", record)
    monkeypatch.setattr(bot, "prune_reply_evaluation_records", prune)

    assert bot.maybe_reply_to_mentions(
        state, _fresh_mention_ai_evaluations=bot.MAX_MENTIONS_PER_CHECK,
    ) == bot.NORMAL_CHECK_STATUS_CHECKED

    save.assert_called_once_with(state, durable=True)
    assert [item.kwargs["target_id"] for item in record.call_args_list] == ["104", "105"]
    assert all(item.kwargs["prune_records"] is False for item in record.call_args_list)
    prune.assert_called_once_with(state)
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
    bot.generate_single_call_reply.assert_not_called()
    bot.x_request.assert_not_called()
    bot.create_post.assert_not_called()
