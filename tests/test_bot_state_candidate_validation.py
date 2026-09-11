from __future__ import annotations

import inspect
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrs_bot_state_candidate_validation as validation
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_regular_post_receipt  # noqa: F401


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, io, logging, os, random, socket, sys, time
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('state candidate validation import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply'} or name.startswith('mrs_bot_') and name != 'mrs_bot_state_candidate_validation':
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = os.lstat = os.stat = forbidden
os.getenv = os._Environ.__getitem__ = forbidden
Path.home = logging.getLogger = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
time.time = time.monotonic = forbidden
before = random.getstate()
random.Random = random.seed = random.random = forbidden
import mrs_bot_state_candidate_validation
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


def test_adapters_forward_current_dependencies_references_and_native_errors(monkeypatch):
    for name, count in (
        ("validate_meme_schedule_state", 5),
        ("validate_meme_schedule_version_for_candidate", 3),
        ("require_compatible_state_reader", 2),
        ("normalise_state_candidate", 28),
    ):
        adapter = getattr(bot, name)
        public = inspect.signature(adapter).parameters
        dependencies = inspect.signature(getattr(validation, name)).parameters.keys() - public.keys()
        assert len(dependencies) == count
        original = object()
        options = {key: object() for key, param in public.items() if param.kind == param.KEYWORD_ONLY}
        with monkeypatch.context() as patch:
            for _ in range(2):
                result = object()
                owner = Mock(return_value=result)
                patch.setattr(bot, "_state_candidate_validation", SimpleNamespace(**{name: owner}))
                current = {key: object() for key in dependencies}
                for key, value in current.items():
                    patch.setattr(bot, key, value)
                assert adapter(original, **options) is result
                assert owner.call_args.args == (original,)
                assert owner.call_args.args[0] is original
                assert owner.call_args.kwargs.keys() == (options | current).keys()
                assert all(owner.call_args.kwargs[key] is value for key, value in (options | current).items())
            failure = TypeError("current owner failure")
            owner.side_effect = failure
            with pytest.raises(TypeError) as caught:
                adapter(original, **options)
            assert caught.value is failure


def test_reader_none_uses_current_version_and_preserves_exact_error_order(monkeypatch, tmp_path):
    class CurrentReaderError(RuntimeError):
        pass

    class Integer(int):
        pass

    monkeypatch.setattr(bot, "IncompatibleStateReaderError", CurrentReaderError)
    monkeypatch.setattr(bot, "STATE_READER_VERSION", 7)
    state = {"minimum_reader_version": 7}
    assert bot.require_compatible_state_reader(state, path=tmp_path) == 7
    monkeypatch.setattr(bot, "STATE_READER_VERSION", 6)
    for options in ({}, {"reader_version": None}):
        with pytest.raises(CurrentReaderError) as caught:
            bot.require_compatible_state_reader(state, path=tmp_path, **options)
        assert str(caught.value) == (
            f"State candidate {tmp_path} requires minimum reader version 7, "
            "but this executable supports 6; refusing state mutation and backup fallback"
        )
    assert bot.require_compatible_state_reader(state, path=tmp_path, reader_version=8) == 7
    assert bot.require_compatible_state_reader({}, path=tmp_path) == 1
    for minimum in (True, 0, Integer(1)):
        with pytest.raises(CurrentReaderError) as caught:
            bot.require_compatible_state_reader(
                {"minimum_reader_version": minimum}, path=tmp_path, reader_version=False,
            )
        assert str(caught.value) == f"State candidate {tmp_path} has invalid minimum reader version {minimum!r}"
    for supported in (False, 0, Integer(8)):
        with pytest.raises(ValueError, match="^reader_version must be a positive integer$"):
            bot.require_compatible_state_reader(state, path=tmp_path, reader_version=supported)
    assert state == {"minimum_reader_version": 7}


def test_schedule_early_gates_preserve_reads_and_native_conversion_failure(monkeypatch, tmp_path):
    trace = Mock()

    class State(dict):
        def get(self, key, default=None):
            trace.read(key)
            return super().get(key, default)

    monkeypatch.setattr(bot, "valid_receipt_epoch", trace.receipt)
    monkeypatch.setattr(bot, "safe_bound_schedule_date_str", trace.date)
    monkeypatch.setattr(bot, "log", trace.log)
    state = State(next_meme_post_epoch=0, next_meme_schedule_mode=object())
    assert bot.validate_meme_schedule_state(state, path=tmp_path) is True
    assert trace.mock_calls == [call.read("next_meme_post_epoch")]
    trace.reset_mock()
    state["next_meme_post_epoch"] = "10"
    trace.receipt.return_value = False
    assert bot.validate_meme_schedule_state(state, path=tmp_path) is False
    assert trace.mock_calls == [
        call.read("next_meme_post_epoch"), call.receipt(10),
        call.log.error("State candidate %s has receipt-incompatible active meme target epoch %s; ignoring", tmp_path, 10),
    ]
    trace.reset_mock()
    trace.receipt.return_value = True
    state.update(next_meme_schedule_mode="invalid", meme_anchor_quote_post_epoch=object())
    with pytest.raises(TypeError):
        bot.validate_meme_schedule_state(state, path=tmp_path)
    assert trace.mock_calls == [
        call.read("next_meme_post_epoch"), call.receipt(10),
        call.read("next_meme_schedule_mode"), call.read("next_meme_schedule_date"),
        call.read("meme_anchor_quote_post_epoch"),
    ]


def test_schedule_uses_current_modes_receipt_checks_then_bound_date(monkeypatch, tmp_path):
    trace = Mock()
    trace.receipt.return_value = True
    trace.date.return_value = "bound-date"
    timezone = object()
    monkeypatch.setattr(bot, "MAIN_POST_SCHEDULE_TIMEZONE", timezone)
    monkeypatch.setattr(bot, "MEME_SCHEDULE_MODES", {"after_first_quote_after_midday", "current-mode"})
    monkeypatch.setattr(bot, "valid_receipt_epoch", trace.receipt)
    monkeypatch.setattr(bot, "safe_bound_schedule_date_str", trace.date)
    monkeypatch.setattr(bot, "log", trace.log)
    state = {"next_meme_post_epoch": 20, "meme_anchor_quote_post_epoch": 10,
             "next_meme_schedule_mode": "after_first_quote_after_midday",
             "next_meme_schedule_date": "bound-date"}
    assert bot.validate_meme_schedule_state(state, path=tmp_path) is True
    assert trace.mock_calls == [call.receipt(20), call.receipt(10), call.date(10, timezone)]
    trace.reset_mock()
    trace.receipt.side_effect = [True, False]
    assert bot.validate_meme_schedule_state(state, path=tmp_path) is False
    assert trace.mock_calls == [
        call.receipt(20), call.receipt(10),
        call.log.error("State candidate %s has receipt-incompatible meme anchor epoch %s; ignoring", tmp_path, 10),
    ]
    trace.reset_mock()
    trace.receipt.side_effect = None
    state.update(next_meme_schedule_mode="current-mode", meme_anchor_quote_post_epoch=0)
    assert bot.validate_meme_schedule_state(state, path=tmp_path) is True
    assert trace.mock_calls == [call.receipt(20), call.date(20, timezone)]
    trace.reset_mock()
    trace.date.return_value = None
    assert bot.validate_meme_schedule_state(state, path=tmp_path) is False
    trace.log.error.assert_called_once_with(
        "State candidate %s has meme schedule_date=%r expected=%r for mode=%s; ignoring",
        tmp_path, "bound-date", None, "current-mode",
    )


def test_schedule_version_gates_use_current_callback_and_exact_logs(monkeypatch, tmp_path):
    trace, result = Mock(), object()
    trace.schedule.return_value = result
    monkeypatch.setattr(bot, "MEME_SCHEDULE_VERSION", 7)
    monkeypatch.setattr(bot, "validate_meme_schedule_state", trace.schedule)
    monkeypatch.setattr(bot, "log", trace.log)
    state = {"meme_schedule_version": 8, "next_meme_post_epoch": object()}
    assert bot.validate_meme_schedule_version_for_candidate(state, path=tmp_path) is False
    state["meme_schedule_version"] = 6
    assert bot.validate_meme_schedule_version_for_candidate(state, path=tmp_path) is True
    assert trace.mock_calls == [
        call.log.error("State candidate %s has future meme_schedule_version=%s > supported=%s; ignoring", tmp_path, 8, 7),
        call.log.info("State candidate %s has old meme_schedule_version=%s; deferring schedule validation to migration", tmp_path, 6),
    ]
    monkeypatch.setattr(bot, "MEME_SCHEDULE_VERSION", 6)
    assert bot.validate_meme_schedule_version_for_candidate(state, path=tmp_path) is result
    assert trace.schedule.call_args.args[0] is state
    trace.schedule.assert_called_once_with(state, path=tmp_path)


def test_candidate_reader_precedes_defaults_and_experiment_keeps_current_exception_boundary(monkeypatch, tmp_path):
    trace = Mock()
    failure = RuntimeError("reader stopped candidate")
    trace.reader.side_effect = failure
    monkeypatch.setattr(bot, "require_compatible_state_reader", trace.reader)
    monkeypatch.setattr(bot, "default_state", trace.defaults)
    monkeypatch.setattr(bot, "log", trace.log)
    with pytest.raises(RuntimeError) as caught:
        bot.normalise_state_candidate({}, path=tmp_path)
    assert caught.value is failure
    trace.defaults.assert_not_called()

    class ExperimentError(ValueError):
        pass

    monkeypatch.setattr(bot, "engagement_question_trial", SimpleNamespace(
        validate_experiment_state=trace.experiment, ExperimentValidationError=ExperimentError,
    ))
    monkeypatch.setattr(bot, "normalise_string_list", trace.strings)
    trace.reader.side_effect = None
    trace.reader.return_value = 1
    trace.defaults.return_value = {}
    trace.experiment.side_effect = ExperimentError("invalid fixture")
    trace.reset_mock()
    state = {"engagement_question_experiment": None, "replied_to_ids": []}
    assert bot.normalise_state_candidate(state, path=tmp_path) is None
    assert trace.mock_calls == [
        call.reader(state, path=tmp_path), call.defaults(), call.experiment(None),
        call.log.error("State candidate %s has invalid engagement-question experiment state; ignoring", tmp_path, exc_info=True),
    ]
    failure = TypeError("native experiment error")
    trace.experiment.side_effect = failure
    trace.reset_mock()
    with pytest.raises(TypeError) as caught:
        bot.normalise_state_candidate(state, path=tmp_path)
    assert caught.value is failure
    trace.log.error.assert_not_called()
    trace.strings.assert_not_called()


def test_candidate_keeps_group_order_callback_references_and_history_children(monkeypatch, tmp_path):
    trace = Mock()
    child, experiment = [], object()
    defaults = {"default_child": child, "last_regular_image_filename": "original.jpg"}
    state = {"extension": child, "engagement_question_experiment": experiment,
             "reply_strategy_history": [{"index": i} for i in range(1002)],
             "ai_reply_history": [{"index": i} for i in range(1001)]}
    # One representative per group observes orchestration without duplicating normalizers.
    groups = [
        ("replied_to_ids", "normalise_string_list", []),
        ("x_error_epochs", "normalise_epoch_list", []),
        ("hot_post_reply_since_ids", "normalise_string_map", {}),
        ("quote_lookup_repeated_cursor_suppressions", "normalise_quote_repeated_cursor_suppressions", {}),
        ("hot_post_reply_check_counts", "normalise_int_map", {}),
        ("pending_ai_reply_drafts", "normalise_record_map", {}),
        ("mention_pending_candidates", "canonical_mention_pending_candidates", {}),
        ("daily_reply_date", "normalise_optional_scalar", ""),
        ("last_seen_mention_id", "normalise_optional_numeric_id", ""),
        ("tweet_cache", "normalise_tweet_cache", {}),
        ("mention_pagination", "normalise_mention_pagination", {}),
        ("mention_backlog_reset_guard", "normalise_mention_backlog_reset_guard", {}),
        ("mention_backlog", "normalise_mention_backlog", {}),
        ("author_evaluation_quarantines", "normalise_author_evaluation_quarantines", {}),
        ("daily_reply_count", "normalise_state_int", 3),
        ("last_reply_epoch", "normalise_state_epoch", 9),
    ]
    for key, name, returned in groups:
        state[key] = object()
        callback = getattr(trace, name)
        callback.return_value = (returned, 2) if key == "quote_lookup_repeated_cursor_suppressions" else returned
        monkeypatch.setattr(bot, name, callback)
    callbacks = {
        "require_compatible_state_reader": 6, "default_state": defaults,
        "prune_reply_evaluation_records": None,
        "validate_pending_mention_candidate_authority": (True, False),
        "validate_meme_schedule_version_for_candidate": True,
        "prune_author_evaluation_quarantines": False,
    }
    for name, returned in callbacks.items():
        getattr(trace, name).return_value = returned
        monkeypatch.setattr(bot, name, getattr(trace, name))
    trace.experiment.return_value = {}
    monkeypatch.setattr(bot, "engagement_question_trial", SimpleNamespace(validate_experiment_state=trace.experiment))
    monkeypatch.setattr(bot, "STATE_MINIMUM_READER_VERSION", 7)
    monkeypatch.setattr(bot, "ENGAGEMENT_QUESTION_EXPERIMENT_STATE_MINIMUM_READER_VERSION", 8)
    events = [{"earlier": True}]
    result = bot.normalise_state_candidate(state, path=tmp_path, recovery_events=events)
    assert [c[0] for c in trace.mock_calls] == [
        "require_compatible_state_reader", "default_state", "experiment",
        *[name for _, name, _ in groups[:13]],
        "prune_reply_evaluation_records", "validate_pending_mention_candidate_authority",
        "normalise_author_evaluation_quarantines", "normalise_state_int",
        "normalise_state_epoch", "validate_meme_schedule_version_for_candidate",
        "prune_author_evaluation_quarantines",
    ]
    assert result is defaults and result is not state
    assert result["extension"] is result["default_child"] is child
    assert result["minimum_reader_version"] == 8
    assert result["engagement_question_experiment"] is trace.experiment.return_value
    assert trace.experiment.call_args.args[0] is experiment
    for key, name, returned in groups:
        assert getattr(trace, name).call_args.args[0] is state[key]
        assert result[key] is (None if key in {"daily_reply_date", "last_seen_mention_id"} else returned)
    for key in ("reply_strategy_history", "ai_reply_history"):
        assert len(result[key]) == 1000 and result[key] is not state[key]
        assert all(actual is original for actual, original in zip(result[key], state[key][-1000:]))
    assert events == [{"earlier": True}, {"kind": "quote_cursor_suppression_pruned", "discarded_entries": 2}]
    authority = trace.validate_pending_mention_candidate_authority.call_args
    assert authority.args[0] is result and authority.kwargs["recovery_events"] is events
    assert authority.kwargs == {"path": tmp_path, "recovery_events": events, "recover_pending_identity": False}
    for name in ("prune_reply_evaluation_records", "validate_meme_schedule_version_for_candidate", "prune_author_evaluation_quarantines"):
        assert getattr(trace, name).call_args.args[0] is result
    assert "original_regular_posts_since_generated_image" not in result
    assert "minimum_reader_version" not in state


def test_candidate_none_keeps_earlier_recovery_event_and_stops_before_authority(monkeypatch, tmp_path):
    trace = Mock()
    trace.cursors.return_value = ({}, 3)
    trace.scalar.return_value = None
    monkeypatch.setattr(bot, "normalise_quote_repeated_cursor_suppressions", trace.cursors)
    monkeypatch.setattr(bot, "normalise_optional_scalar", trace.scalar)
    monkeypatch.setattr(bot, "prune_reply_evaluation_records", trace.prune)
    monkeypatch.setattr(bot, "validate_pending_mention_candidate_authority", trace.authority)
    cursor, scalar, events = object(), object(), []
    state = {"quote_lookup_repeated_cursor_suppressions": cursor, "daily_reply_date": scalar}
    assert bot.normalise_state_candidate(state, path=tmp_path, recovery_events=events) is None
    assert trace.mock_calls == [call.cursors(cursor), call.scalar(scalar, key="daily_reply_date", path=tmp_path)]
    assert events == [{"kind": "quote_cursor_suppression_pruned", "discarded_entries": 3}]
    trace.cursors.return_value = ({}, 0)
    events.clear()
    assert bot.normalise_state_candidate(state, path=tmp_path, recovery_events=events) is None
    assert events == []


def test_pending_identity_recovery_preserves_original_value_and_events_on_authority_failure(monkeypatch, tmp_path):
    pending, trace, events = object(), Mock(), []
    trace.canonical.return_value = None
    monkeypatch.setattr(bot, "canonical_mention_pending_candidates", trace.canonical)
    monkeypatch.setattr(bot, "prune_reply_evaluation_records", trace.prune)
    monkeypatch.setattr(bot, "normalise_author_evaluation_quarantines", trace.quarantines)

    def authority(state, **options):
        trace.authority()
        assert state["mention_pending_candidates"] is pending
        assert options == {"path": tmp_path, "recover_pending_identity": True, "recovery_events": events}
        assert options["recovery_events"] is events
        events.append({"partial": True})
        return False, True

    monkeypatch.setattr(bot, "validate_pending_mention_candidate_authority", authority)
    state = {"mention_pending_candidates": pending, "author_evaluation_quarantines": object()}
    assert bot.normalise_state_candidate(state, path=tmp_path) is None
    assert [c[0] for c in trace.mock_calls] == ["canonical"]
    trace.reset_mock()
    assert bot.normalise_state_candidate(state, path=tmp_path, recovery_events=events, recover_pending_identity=True) is None
    assert [c[0] for c in trace.mock_calls] == ["canonical", "prune", "authority"]
    assert events == [{"partial": True}]
    assert state["mention_pending_candidates"] is pending


def test_overflow_hash_and_reset_precede_pruning_and_authority_with_partial_events(monkeypatch, tmp_path):
    trace, events = Mock(), []
    backlog = {"seen_tokens": ["a", "b"], "next_token": "original-token", "since_id": "", "pages_completed": 2}
    trace.backlog.return_value = {}
    trace.watermark.return_value = "99"
    trace.sha256.return_value.hexdigest.return_value = "0123456789abcdefextra"
    monkeypatch.setattr(bot, "MENTION_BACKLOG_CONTINUATION_TOKEN_LIMIT", 1)
    monkeypatch.setattr(bot, "normalise_mention_backlog", trace.backlog)
    monkeypatch.setattr(bot, "normalise_optional_numeric_id", trace.watermark)
    monkeypatch.setattr(bot, "hashlib", SimpleNamespace(sha256=trace.sha256))
    monkeypatch.setattr(bot, "validate_meme_schedule_version_for_candidate", trace.schedule)
    monkeypatch.setattr(bot, "prune_author_evaluation_quarantines", trace.final)

    def prune(state):
        trace.prune()
        assert state["mention_backlog"] is trace.backlog.return_value
        assert state["mention_pending_candidates"] == state["mention_pagination"] == {}
        assert state["mention_backlog_reset_guard"] == {"base_since_id": "99", "head_traversal_started": False}
        assert events == [{"reason": "continuation_token_limit", "since_id": None,
                           "pages_completed": 2, "token_fingerprint": "0123456789abcdef"}]
        state["legacy_pruned"] = True

    def authority(state, **options):
        trace.authority()
        assert state["legacy_pruned"] is True
        assert options["recovery_events"] is events
        events.append({"authority": True})
        return False, False

    monkeypatch.setattr(bot, "prune_reply_evaluation_records", prune)
    monkeypatch.setattr(bot, "validate_pending_mention_candidate_authority", authority)
    state = {"mention_backlog": backlog, "last_seen_mention_id": 99}
    assert bot.normalise_state_candidate(state, path=tmp_path, recovery_events=events) is None
    assert trace.mock_calls == [
        call.watermark(99, key="last_seen_mention_id", path=tmp_path),
        call.backlog(backlog, path=tmp_path, reset_token_overflow=True),
        call.sha256(b"original-token"), call.sha256().hexdigest(), call.prune(), call.authority(),
    ]
    assert events[-1] == {"authority": True}
    assert state == {"mention_backlog": backlog, "last_seen_mention_id": 99}


def test_legacy_counter_is_optional_and_schedule_gate_precedes_final_pruning(monkeypatch, tmp_path):
    trace = Mock()
    trace.schedule.return_value = False
    monkeypatch.setattr(bot, "validate_meme_schedule_version_for_candidate", trace.schedule)
    monkeypatch.setattr(bot, "prune_author_evaluation_quarantines", trace.prune)
    state = {"last_regular_image_filename": "tg_" + "a" * 64 + ".png"}
    assert bot.normalise_state_candidate(state, path=tmp_path) is None
    assert [c[0] for c in trace.mock_calls] == ["schedule"]
    assert "original_regular_posts_since_generated_image" not in trace.schedule.call_args.args[0]
    trace.reset_mock()
    trace.schedule.return_value = True
    state["original_regular_posts_since_generated_image"] = "4"
    result = bot.normalise_state_candidate(state, path=tmp_path)
    assert result["original_regular_posts_since_generated_image"] == 4
    assert [c[0] for c in trace.mock_calls] == ["schedule", "prune"]
    assert trace.schedule.call_args.args[0] is trace.prune.call_args.args[0] is result
