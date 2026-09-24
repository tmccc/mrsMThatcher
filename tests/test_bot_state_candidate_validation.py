from __future__ import annotations

import inspect
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import mrs_bot_reply_assembly as assembly

import pytest

import mrs_bot_state_candidate_validation as validation
import mrs_bot_state_value_normalisation as state_values
import mrs_bot_mention_authority as authority
import mrs_bot_author_quarantines as quarantines
import mrs_bot_reply_evaluation_state as evaluations
import mrs_bot_tweet_lookup_cache as tweet_cache
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401


VALUE_METHODS = {
    'strings', 'epochs', 'string_map', 'integer_map', 'record_map',
    'optional_scalar', 'optional_id', 'integer', 'epoch',
}

AUTHORITY_METHODS = {'canonical_mention_pending_candidates': 'canonical_candidates', 'normalise_mention_pagination': 'normalise_pagination', 'normalise_mention_backlog_reset_guard': 'normalise_reset_guard', 'normalise_mention_backlog': 'normalise_backlog', 'validate_pending_mention_candidate_authority': 'validate_pending'}

OWNER_METHODS = {
    'normalise_tweet_cache': (tweet_cache.TweetLookupCache, 'normalise'),
    'normalise_author_evaluation_quarantines': (quarantines.AuthorQuarantines, 'normalise'),
    'prune_author_evaluation_quarantines': (quarantines.AuthorQuarantines, 'prune'),
    'prune_reply_evaluation_records': (evaluations.ReplyEvaluations, 'prune'),
}

def patch_normalization(monkeypatch, name, callback):
    """Observe the normalization owner while leaving runtime callbacks current."""
    if name in VALUE_METHODS:
        monkeypatch.setattr(
            state_values.StateValues, name,
            lambda self, *args, **kwargs: callback(*args, **kwargs),
        )
    elif name in AUTHORITY_METHODS:
        monkeypatch.setattr(
            authority.MentionAuthority, AUTHORITY_METHODS[name],
            lambda self, *args, **kwargs: callback(*args, **kwargs),
        )
    elif name in OWNER_METHODS:
        owner, method = OWNER_METHODS[name]
        monkeypatch.setattr(
            owner, method, lambda self, *args, **kwargs: callback(*args, **kwargs),
        )
    else:
        monkeypatch.setattr(bot, name, callback)


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, dataclasses, hashlib, io, json, logging, os, random, socket, stat, sys, time
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('state candidate validation import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply'} or name.startswith('mrs_bot_') and name not in {'mrs_bot_state_candidate_validation', 'mrs_bot_state_generation'}:
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
        ("normalise_state_candidate", 12),
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
                factories = {
                    "state_values": "_state_values_owner",
                    "mention_authority": "mention_authority",
                    "author_quarantines": "author_quarantines",
                    "reply_evaluations": "reply_evaluations",
                    "tweets": "_tweet_lookup_cache_owner",
                }
                for key, value in current.items():
                    if key in factories:
                        target = (
                            assembly.ReplyAssembly
                            if key in {"mention_authority", "author_quarantines", "reply_evaluations"}
                            else bot
                        )
                        patch.setattr(target, factories[key], Mock(return_value=value))
                    else:
                        patch.setattr(bot, key, value)
                assert adapter(original, **options) is result
                assert owner.call_args.args == (original,)
                assert owner.call_args.args[0] is original
                assert owner.call_args.kwargs.keys() == (options | current).keys()
                for key, value in (options | current).items():
                    supplied = owner.call_args.kwargs[key]
                    if key in factories:
                        target = (
                            assembly.ReplyAssembly
                            if key in {"mention_authority", "author_quarantines", "reply_evaluations"}
                            else bot
                        )
                        factory = getattr(target, factories[key])
                        assert supplied is value
                        if key == "tweets":
                            factory.assert_called_once_with(state_values=current["state_values"])
                        else:
                            factory.assert_called_once_with()
                    else:
                        assert supplied is value
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


def test_candidate_reader_precedes_defaults(monkeypatch, tmp_path):
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



def test_candidate_keeps_group_order_callback_references_and_history_children(monkeypatch, tmp_path):
    trace = Mock()
    child, experiment = [], object()
    defaults = {"default_child": child, "last_regular_image_filename": "original.jpg"}
    state = {"extension": child, "engagement_question_experiment": experiment,
             "reply_strategy_history": [{"index": i} for i in range(1002)],
             "ai_reply_history": [{"index": i} for i in range(1001)]}
    # One representative per group observes orchestration without duplicating normalizers.
    groups = [
        ("replied_to_ids", "strings", []),
        ("x_error_epochs", "epochs", []),
        ("hot_post_reply_since_ids", "string_map", {}),
        ("quote_lookup_repeated_cursor_suppressions", "normalise_quote_repeated_cursor_suppressions", {}),
        ("hot_post_reply_check_counts", "integer_map", {}),
        ("pending_ai_reply_drafts", "record_map", {}),
        ("mention_pending_candidates", "canonical_mention_pending_candidates", {}),
        ("daily_reply_date", "optional_scalar", ""),
        ("last_seen_mention_id", "optional_id", ""),
        ("tweet_cache", "normalise_tweet_cache", {}),
        ("mention_pagination", "normalise_mention_pagination", {}),
        ("mention_backlog_reset_guard", "normalise_mention_backlog_reset_guard", {}),
        ("mention_backlog", "normalise_mention_backlog", {}),
        ("author_evaluation_quarantines", "normalise_author_evaluation_quarantines", {}),
        ("daily_reply_count", "integer", 3),
        ("last_reply_epoch", "epoch", 9),
    ]
    for key, name, returned in groups:
        state[key] = object()
        callback = getattr(trace, name)
        callback.return_value = (returned, 2) if key == "quote_lookup_repeated_cursor_suppressions" else returned
        patch_normalization(monkeypatch, name, callback)
    callbacks = {
        "require_compatible_state_reader": 6, "default_state": defaults,
        "prune_reply_evaluation_records": None,
        "validate_pending_mention_candidate_authority": (True, False),
        "validate_meme_schedule_version_for_candidate": True,
        "prune_author_evaluation_quarantines": False,
    }
    for name, returned in callbacks.items():
        getattr(trace, name).return_value = returned
        patch_normalization(monkeypatch, name, getattr(trace, name))
    obsolete_relays = {
        name: Mock(side_effect=AssertionError(f"candidate used obsolete root relay: {name}"))
        for name in (
            "normalise_tweet_cache",
            "normalise_author_evaluation_quarantines",
            "prune_author_evaluation_quarantines",
        )
    }
    for name, relay in obsolete_relays.items():
        monkeypatch.setattr(bot, name, relay)
    monkeypatch.setattr(bot, "STATE_MINIMUM_READER_VERSION", 7)
    events = [{"earlier": True}]
    result = bot.normalise_state_candidate(state, path=tmp_path, recovery_events=events)
    assert [c[0] for c in trace.mock_calls] == [
        "require_compatible_state_reader", "default_state",
        *[name for _, name, _ in groups[:13]],
        "prune_reply_evaluation_records", "validate_pending_mention_candidate_authority",
        "normalise_author_evaluation_quarantines", "integer",
        "epoch", "validate_meme_schedule_version_for_candidate",
        "prune_author_evaluation_quarantines",
    ]
    assert result is defaults and result is not state
    assert result["extension"] is result["default_child"] is child
    assert result["minimum_reader_version"] == 7
    assert result["engagement_question_experiment"] is experiment
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
    for relay in obsolete_relays.values():
        relay.assert_not_called()


def test_candidate_none_keeps_earlier_recovery_event_and_stops_before_authority(monkeypatch, tmp_path):
    trace = Mock()
    trace.cursors.return_value = ({}, 3)
    trace.scalar.return_value = None
    monkeypatch.setattr(bot, "normalise_quote_repeated_cursor_suppressions", trace.cursors)
    patch_normalization(monkeypatch, "optional_scalar", trace.scalar)
    patch_normalization(monkeypatch, "prune_reply_evaluation_records", trace.prune)
    patch_normalization(monkeypatch, "validate_pending_mention_candidate_authority", trace.authority)
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
    patch_normalization(monkeypatch, "canonical_mention_pending_candidates", trace.canonical)
    patch_normalization(monkeypatch, "prune_reply_evaluation_records", trace.prune)
    patch_normalization(monkeypatch, "normalise_author_evaluation_quarantines", trace.quarantines)

    def authority(state, **options):
        trace.authority()
        assert state["mention_pending_candidates"] is pending
        assert options == {"path": tmp_path, "recover_pending_identity": True, "recovery_events": events}
        assert options["recovery_events"] is events
        events.append({"partial": True})
        return False, True

    patch_normalization(monkeypatch, "validate_pending_mention_candidate_authority", authority)
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
    patch_normalization(monkeypatch, "normalise_mention_backlog", trace.backlog)
    patch_normalization(monkeypatch, "optional_id", trace.watermark)
    monkeypatch.setattr(validation, "hashlib", SimpleNamespace(sha256=trace.sha256))
    monkeypatch.setattr(bot, "validate_meme_schedule_version_for_candidate", trace.schedule)
    patch_normalization(monkeypatch, "prune_author_evaluation_quarantines", trace.final)

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

    patch_normalization(monkeypatch, "prune_reply_evaluation_records", prune)
    patch_normalization(monkeypatch, "validate_pending_mention_candidate_authority", authority)
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
    patch_normalization(monkeypatch, "prune_author_evaluation_quarantines", trace.prune)
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


@pytest.mark.parametrize("commits", [
    None,
    [],
    {"short": {"quote_hash": "", "image_basename": ""}},
    {"A" * 64: {"quote_hash": "", "image_basename": ""}},
    {"a" * 64: []},
    {"a" * 64: {"quote_hash": ""}},
    {"a" * 64: {"quote_hash": 1, "image_basename": ""}},
])
def test_commit_record_grammar_rejects_before_defaults_without_changing_input(monkeypatch, tmp_path, commits):
    from mrs_bot_state_generation import receipt_commit_records_are_valid

    state = {"_confirmed_receipt_commits": commits}
    defaults, logger = Mock(), Mock()
    monkeypatch.setattr(bot, "default_state", defaults)
    monkeypatch.setattr(bot, "log", logger)
    assert not receipt_commit_records_are_valid(commits)
    assert bot.normalise_state_candidate(state, path=tmp_path) is None
    assert state["_confirmed_receipt_commits"] is commits
    defaults.assert_not_called()
    logger.error.assert_called_once_with(
        'State candidate %s has invalid confirmed receipt commit identities', tmp_path,
    )


def test_commit_record_writer_validator_and_normalizer_keep_same_record_references(tmp_path):
    from mrs_bot_state_generation import record_receipt_commit, receipt_commit_records_are_valid

    state = {}
    record_receipt_commit(state, {"selected_identity": {"quote_hash": "quote", "image_basename": "image.jpg"}})
    commits = state["_confirmed_receipt_commits"]
    identity = next(iter(commits.values()))
    assert receipt_commit_records_are_valid(commits)
    assert identity == {"quote_hash": "quote", "image_basename": "image.jpg"}
    normalized = bot.normalise_state_candidate(state, path=tmp_path)
    assert normalized["_confirmed_receipt_commits"] is commits
    assert next(iter(normalized["_confirmed_receipt_commits"].values())) is identity


def test_commit_record_validation_keeps_native_mapping_error_before_defaults(monkeypatch, tmp_path):
    failure = LookupError("commit-record inspection failed")

    class BrokenRecords(dict):
        def items(self):
            raise failure

    defaults = Mock()
    monkeypatch.setattr(bot, "default_state", defaults)
    with pytest.raises(LookupError) as caught:
        bot.normalise_state_candidate({"_confirmed_receipt_commits": BrokenRecords()}, path=tmp_path)
    assert caught.value is failure
    defaults.assert_not_called()


def test_candidate_shares_one_value_owner_across_fields_and_tweet_composition(monkeypatch, tmp_path):
    calls = []
    first_value, second_value = [], []
    owner = SimpleNamespace()
    factory = Mock(return_value=owner)
    tweet_factory = Mock(wraps=bot._tweet_lookup_cache_owner)

    def reader(state, *, path):
        calls.append("reader")
        return 1

    def strings(value, *, key, path):
        calls.append("strings")
        assert value is first_value and key == "replied_to_ids" and path == tmp_path
        return value

    def epochs(value, *, key, path):
        calls.append("epochs")
        assert value is second_value and key == "x_error_epochs" and path == tmp_path
        return value

    owner.strings = strings
    owner.epochs = epochs
    monkeypatch.setattr(bot, "_state_values_owner", factory)
    monkeypatch.setattr(bot, "_tweet_lookup_cache_owner", tweet_factory)
    monkeypatch.setattr(bot, "require_compatible_state_reader", reader)
    result = bot.normalise_state_candidate(
        {"replied_to_ids": first_value, "x_error_epochs": second_value}, path=tmp_path,
    )
    assert calls == ["reader", "strings", "epochs"]
    factory.assert_called_once_with()
    tweet_factory.assert_called_once_with(state_values=owner)
    assert result["replied_to_ids"] is first_value
    assert result["x_error_epochs"] is second_value


def test_candidate_value_owner_failure_keeps_native_error_before_later_groups(monkeypatch, tmp_path):
    failure = TypeError("current state value owner unavailable")
    factory = Mock(side_effect=failure)
    authority = Mock()
    monkeypatch.setattr(bot, "_state_values_owner", factory)
    patch_normalization(monkeypatch, "validate_pending_mention_candidate_authority", authority)
    with pytest.raises(TypeError) as caught:
        bot.normalise_state_candidate({"replied_to_ids": []}, path=tmp_path)
    assert caught.value is failure
    factory.assert_called_once_with()
    authority.assert_not_called()


@pytest.mark.parametrize("lookup_fails", [False, True])
def test_value_owner_binds_once_before_candidate_reads_and_preserves_lookup_errors(monkeypatch, tmp_path, lookup_fails):
    failure = LookupError("state value unavailable")
    events = []
    normalise = Mock(side_effect=lambda value, **kwargs: value)
    owner = SimpleNamespace(strings=normalise)

    def bind():
        events.append("bind")
        return owner

    current = Mock(side_effect=bind)
    source = []

    class State(dict):
        def __getitem__(self, key):
            if key == "replied_to_ids":
                events.append("read")
                if lookup_fails:
                    raise failure
            return super().__getitem__(key)

    monkeypatch.setattr(bot, "_state_values_owner", current)
    state = State(replied_to_ids=source)
    if lookup_fails:
        with pytest.raises(LookupError) as caught:
            bot.normalise_state_candidate(state, path=tmp_path)
        assert caught.value is failure
        normalise.assert_not_called()
    else:
        result = bot.normalise_state_candidate(state, path=tmp_path)
        assert result["replied_to_ids"] is source
        normalise.assert_called_once_with(source, key="replied_to_ids", path=tmp_path)
    assert events == ["bind", "read"]
    current.assert_called_once_with()


def test_candidate_shares_one_mention_owner_across_normalization_and_recovery(monkeypatch, tmp_path):
    events, pending, pagination, calls = [], {}, {}, []
    owner = SimpleNamespace()
    factory = Mock(return_value=owner)

    def reader(state, *, path):
        calls.append("reader")
        return 1

    def canonical(value, *, path):
        calls.append("canonical")
        assert value is pending and path == tmp_path
        return value

    def normalise_pagination(value, *, path):
        calls.append("pagination")
        assert value is pagination and path == tmp_path
        return value

    def prune(state):
        calls.append("prune")

    def validate(state, *, path, recover_pending_identity, recovery_events):
        calls.append("validate")
        assert state["mention_pending_candidates"] is pending
        assert state["mention_pagination"] is pagination
        assert recovery_events is events and recover_pending_identity is True
        return True, False

    owner.canonical_candidates = canonical
    owner.normalise_pagination = normalise_pagination
    owner.validate_pending = validate
    monkeypatch.setattr(assembly.ReplyAssembly, "mention_authority", factory)
    monkeypatch.setattr(bot, "require_compatible_state_reader", reader)
    patch_normalization(monkeypatch, "prune_reply_evaluation_records", prune)
    result = bot.normalise_state_candidate(
        {"mention_pending_candidates": pending, "mention_pagination": pagination},
        path=tmp_path, recovery_events=events, recover_pending_identity=True,
    )
    assert result["mention_pending_candidates"] is pending
    assert calls == ["reader", "canonical", "pagination", "prune", "validate"]
    factory.assert_called_once_with()


def test_candidate_mention_owner_failure_stops_before_terminal_pruning(monkeypatch, tmp_path):
    failure = RuntimeError("current mention authority unavailable")
    factory, prune = Mock(side_effect=failure), Mock()
    monkeypatch.setattr(assembly.ReplyAssembly, "mention_authority", factory)
    patch_normalization(monkeypatch, "prune_reply_evaluation_records", prune)
    with pytest.raises(RuntimeError) as caught:
        bot.normalise_state_candidate({"mention_pending_candidates": {}}, path=tmp_path)
    assert caught.value is failure
    factory.assert_called_once_with()
    prune.assert_not_called()


@pytest.mark.parametrize("lookup_fails", [False, True])
def test_mention_owner_binds_once_before_candidate_reads(monkeypatch, tmp_path, lookup_fails):
    failure = LookupError("pending identities unavailable")
    events = []
    normalize = Mock(side_effect=lambda value, **kwargs: value)
    validate = Mock(return_value=(True, False))
    owner = SimpleNamespace(canonical_candidates=normalize, validate_pending=validate)

    def bind():
        events.append("bind")
        return owner

    current = Mock(side_effect=bind)
    pending = {}

    class State(dict):
        def __getitem__(self, key):
            if key == "mention_pending_candidates":
                events.append("read")
                if lookup_fails:
                    raise failure
            return super().__getitem__(key)

    monkeypatch.setattr(assembly.ReplyAssembly, "mention_authority", current)
    state = State(mention_pending_candidates=pending)
    if lookup_fails:
        with pytest.raises(LookupError) as caught:
            bot.normalise_state_candidate(state, path=tmp_path)
        assert caught.value is failure
        normalize.assert_not_called()
        validate.assert_not_called()
    else:
        result = bot.normalise_state_candidate(state, path=tmp_path)
        assert result["mention_pending_candidates"] is pending
        normalize.assert_called_once_with(pending, path=tmp_path)
        assert validate.call_args.args[0] is result
    assert events == ["bind", "read"]
    current.assert_called_once_with()


@pytest.mark.parametrize("invalid_key", ["reply_strategy_history", "ai_reply_history"])
@pytest.mark.parametrize("invalid_history", [{}, [object()]])
def test_invalid_history_stops_in_legacy_then_current_order_before_mention_authority(
    monkeypatch, tmp_path, invalid_key, invalid_history,
):
    """History rejection keeps earlier shallow copies and later authority untouched."""
    history_reads = []

    class Candidate(dict):
        def __getitem__(self, key):
            if key in {"reply_strategy_history", "ai_reply_history"}:
                history_reads.append(key)
            return super().__getitem__(key)

    child = {"nested": []}
    histories = {"reply_strategy_history": [child], "ai_reply_history": [child]}
    histories[invalid_key] = invalid_history
    candidate = Candidate(histories)
    default, log, pruning = {}, Mock(), Mock()
    monkeypatch.setattr(bot, "default_state", lambda: default)
    monkeypatch.setattr(bot, "log", log)
    patch_normalization(monkeypatch, "prune_reply_evaluation_records", pruning)
    assert bot.normalise_state_candidate(candidate, path=tmp_path) is None
    expected_reads = ["reply_strategy_history"]
    if invalid_key == "ai_reply_history":
        expected_reads.append("ai_reply_history")
        assert default["reply_strategy_history"] is not histories["reply_strategy_history"]
        assert default["reply_strategy_history"][0] is child
    assert history_reads == expected_reads
    assert default[invalid_key] is invalid_history
    assert candidate == histories
    log.error.assert_called_once_with(
        f"State candidate %s has invalid {invalid_key}; ignoring", tmp_path,
    )
    pruning.assert_not_called()
