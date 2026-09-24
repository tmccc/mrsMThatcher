"""Exercise daily reply buckets and confirmed-author accounting at their owner."""

from __future__ import annotations

import copy
from dataclasses import FrozenInstanceError, replace
import inspect
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock, call

import mrs_bot_reply_assembly as assembly

import pytest

import mrs_bot_daily_reply_accounting as accounting
import mrs_bot_reply_lane_policy as policy
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401


OWNER_INPUTS = ("log", "dates")


@pytest.fixture
def make_owner():
    """Compose daily accounting with isolated runtime boundaries."""
    def build(**overrides):
        current = {"log": bot.log, "dates": bot._receipt_dates_owner()}
        return accounting.DailyReplyAccounting(**{**current, **overrides})
    return build


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, dataclasses, io, logging, os, random, socket, sys, time
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('daily reply accounting import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'reply_evidence'} or name.startswith('mrs_bot_') and name not in {'mrs_bot_daily_reply_accounting', 'mrs_bot_runtime_state_helpers'}:
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
import mrs_bot_daily_reply_accounting
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
        current = {name: Mock() for name in OWNER_INPUTS}
        monkeypatch.setattr(bot, "log", current["log"])
        dates_factory = Mock(return_value=current["dates"])
        monkeypatch.setattr(bot, "_receipt_dates_owner", dates_factory)
        owner = bot._reply_assembly()._daily_reply_accounting_owner()
        assert isinstance(owner, accounting.DailyReplyAccounting)
        for name, value in current.items():
            assert getattr(owner, name) is value
            value.assert_not_called()
        dates_factory.assert_called_once_with()
        snapshots.append((owner, current))
    first, inputs = snapshots[0]
    assert first is not snapshots[1][0]
    assert all(getattr(first, name) is value for name, value in inputs.items())
    with pytest.raises(FrozenInstanceError):
        first.log = object()


def test_root_adapters_preserve_arguments_result_identity_and_errors(monkeypatch):
    methods = {
        "reset_daily_reply_count_if_needed": "reset",
        "reset_daily_quote_reply_count_if_needed": "reset_quotes",
        "daily_author_reply_count": "author_count", "mark_daily_author_replied": "mark_author",
        "_valid_iso_date": "valid_date", "_advance_reply_counters_to_confirmation_date": "advance",
    }
    for name, method_name in methods.items():
        adapter = getattr(bot, name)
        public = inspect.signature(adapter).parameters
        args = tuple(object() for param in public.values() if param.kind == param.POSITIONAL_OR_KEYWORD)
        options = {key: object() for key, param in public.items() if param.kind == param.KEYWORD_ONLY}
        for _ in range(2):
            owner = Mock(spec=accounting.DailyReplyAccounting)
            factory = Mock(return_value=owner)
            monkeypatch.setattr(assembly.ReplyAssembly, "_daily_reply_accounting_owner", factory)
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


def test_accounting_uses_receipt_dates_without_root_date_relays(monkeypatch):
    epoch = 1_800_000_000
    monkeypatch.setattr(bot, "now_epoch", lambda: epoch)
    for name in ("reply_cap_date_str", "safe_reply_cap_date_str"):
        monkeypatch.setattr(
            bot,
            name,
            Mock(side_effect=AssertionError(f"accounting bounced through {name}")),
        )
    owner = bot._reply_assembly()._daily_reply_accounting_owner()
    state = {"daily_reply_date": "stale", "daily_reply_count": 4}
    owner.reset(state)
    expected = bot.datetime.fromtimestamp(
        epoch,
        tz=bot.ZoneInfo(bot.MAIN_POST_SCHEDULE_TIMEZONE),
    ).strftime("%Y-%m-%d")
    assert state["daily_reply_date"] == expected
    assert state["daily_reply_count"] == 0


@pytest.mark.parametrize("lane", ["reply", "quote_reply"])
def test_resets_sample_current_date_once_and_log_before_mutation(make_owner, lane):
    ids, counts, ledger = ["200"], {"200": 2}, {"700": None}
    state = {
        "daily_reply_date": "old", "daily_reply_count": 4,
        "daily_quote_reply_date": "old", "daily_quote_reply_count": 3,
        "daily_replied_author_ids": ids, "daily_replied_author_counts": counts,
        "clarification_reply_records": ledger,
    }
    before = copy.deepcopy(state)
    trace = Mock()
    trace.dates.reply_cap_date.return_value = "current date"
    owner = make_owner(dates=trace.dates, log=trace.log)
    reset = owner.reset if lane == "reply" else owner.reset_quotes
    failure = RuntimeError("current logger failed")
    trace.log.info.side_effect = failure
    with pytest.raises(RuntimeError) as caught:
        reset(state)
    assert caught.value is failure and state == before
    assert [entry[0] for entry in trace.mock_calls] == ["dates.reply_cap_date", "log.info"]
    assert trace.log.info.call_args.args[1:] == ("old", "current date", before[f"daily_{lane}_count"])
    trace.reset_mock()

    def before_reset(*_args):
        assert state == before

    trace.log.info.side_effect = before_reset
    assert reset(state) is None
    assert [entry[0] for entry in trace.mock_calls] == ["dates.reply_cap_date", "log.info"]
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
    assert trace.mock_calls == [call.dates.reply_cap_date()]
    assert all(state[key] is value for key, value in retained.items())


def test_legacy_counts_keep_permissive_cleaning_fallback_and_fresh_mapping():
    assert bot.daily_author_reply_counts is policy.daily_author_reply_counts is accounting.daily_author_reply_counts
    class BadCount:
        def __int__(self):
            raise RuntimeError("legacy count cannot convert")

    original = {7: "2", "negative": -3, "fraction": 2.9, "bool": True, "broken": BadCount()}
    state = {"daily_replied_author_counts": original, "daily_replied_author_ids": ["legacy"]}
    cleaned = accounting.daily_author_reply_counts(state)
    assert cleaned == {"7": 2, "negative": 0, "fraction": 2, "bool": 1}
    assert cleaned is state["daily_replied_author_counts"] and cleaned is not original
    again = accounting.daily_author_reply_counts(state)
    assert again == cleaned and again is not cleaned and again is state["daily_replied_author_counts"]
    for legacy_counts in ({"broken": BadCount()}, None):
        state = {"daily_replied_author_counts": legacy_counts, "daily_replied_author_ids": [7, "7", None, False]}
        result = accounting.daily_author_reply_counts(state)
        assert result == {"7": 1, "None": 1, "False": 1}
        assert result is state["daily_replied_author_counts"]


def test_author_increment_precedes_current_capped_helper_failure(monkeypatch, make_owner):
    owner = make_owner()
    counts, ids = {"7": 2}, ["older"]
    state = {"daily_replied_author_ids": ids}
    cleaner = Mock(return_value=counts)
    monkeypatch.setattr(accounting, "daily_author_reply_counts", cleaner)
    assert owner.author_count(state, 7) == 2
    assert cleaner.call_args.args[0] is state
    failure = ValueError("capped ID helper failed")

    def capped(actual_ids, author_id, maximum):
        assert actual_ids is ids and author_id == "7" and maximum == 1000
        assert state["daily_replied_author_counts"] is counts and counts["7"] == 3
        raise failure

    monkeypatch.setattr(accounting, "append_unique_capped", capped)
    with pytest.raises(ValueError) as caught:
        owner.mark_author(state, 7)
    assert caught.value is failure
    assert counts == {"7": 3} and state["daily_replied_author_ids"] is ids
    replacement = ["current"]
    monkeypatch.setattr(accounting, "append_unique_capped", Mock(return_value=replacement))
    owner.mark_author(state, 7)
    assert counts == {"7": 4} and state["daily_replied_author_ids"] is replacement


def test_date_round_trip_catches_only_value_error(monkeypatch, make_owner):
    owner = make_owner()
    assert owner.valid_date("2024-02-29")
    assert not owner.valid_date("2024-2-29")
    assert not owner.valid_date("2023-02-29")
    assert not owner.valid_date(None)
    current = Mock()
    current.strptime.return_value.strftime.return_value = "current-date"
    monkeypatch.setattr(accounting, "datetime", current)
    assert owner.valid_date("current-date")
    current.strptime.assert_called_once_with("current-date", "%Y-%m-%d")
    current.strptime.return_value.strftime.assert_called_once_with("%Y-%m-%d")
    current.strptime.side_effect = ValueError("invalid date")
    assert not owner.valid_date("current-date")
    failure = TypeError("current parser failed")
    current.strptime.side_effect = failure
    with pytest.raises(TypeError) as caught:
        owner.valid_date("current-date")
    assert caught.value is failure


def test_confirmation_advance_never_rewinds_newer_buckets_but_daily_reset_does(make_owner):
    ids, counts = ["200"], {"200": 2}
    state = {"daily_reply_date": "2024-01-02", "daily_reply_count": 7,
             "daily_quote_reply_date": "2024-01-02", "daily_quote_reply_count": 3,
             "daily_replied_author_ids": ids, "daily_replied_author_counts": counts}
    before = dict(state)
    date, logger = Mock(return_value="2024-01-01"), Mock()
    dates = Mock(reply_cap_date=date)
    owner = make_owner(dates=dates, log=logger)
    owner.advance(state, "2024-01-01", include_quote_lane=True)
    assert all(state[key] is value for key, value in before.items())
    date.assert_not_called()
    logger.info.assert_not_called()
    owner.reset(state)
    assert state["daily_reply_date"] == "2024-01-01" and state["daily_reply_count"] == 0
    assert state["daily_quote_reply_count"] == 3
    assert state["daily_replied_author_ids"] == [] and state["daily_replied_author_ids"] is not ids
    assert state["daily_replied_author_counts"] == {} and state["daily_replied_author_counts"] is not counts
    owner.reset_quotes(state)
    assert state["daily_quote_reply_date"] == "2024-01-01" and state["daily_quote_reply_count"] == 0
    assert date.call_count == 2


@pytest.mark.parametrize("quote", [False, True])
def test_confirmation_advance_handles_invalid_buckets_and_logs_before_each_mutation(make_owner, quote):
    state = {"daily_reply_date": "invalid", "daily_reply_count": 7,
             "daily_quote_reply_date": "2023-12-31", "daily_quote_reply_count": 3,
             "daily_replied_author_ids": ["200"], "daily_replied_author_counts": {"200": 2}}
    before = copy.deepcopy(state)
    observed = []
    logger = Mock(info=lambda *args: observed.append(copy.deepcopy(state)))
    owner = make_owner(log=logger)
    owner.advance(state, "2024-01-01", include_quote_lane=quote)
    assert observed[0] == before
    assert state["daily_reply_date"] == "2024-01-01" and state["daily_reply_count"] == 0
    assert state["daily_replied_author_ids"] == [] and state["daily_replied_author_counts"] == {}
    if quote:
        assert len(observed) == 2 and observed[1]["daily_reply_count"] == 0
        assert observed[1]["daily_quote_reply_count"] == 3
        assert state["daily_quote_reply_date"] == "2024-01-01" and state["daily_quote_reply_count"] == 0
    else:
        assert len(observed) == 1 and state["daily_quote_reply_date"] == "2023-12-31"
        assert state["daily_quote_reply_count"] == 3


def test_confirmation_recording_respects_supplied_idempotency_and_date_snapshots(make_owner):
    state = {"daily_reply_date": "current", "daily_reply_count": 2,
             "daily_quote_reply_date": "quote-current", "daily_quote_reply_count": 4,
             "daily_replied_author_ids": [], "daily_replied_author_counts": {}}
    owner = make_owner()
    options = dict(already_recorded=False, candidate_source="quote_tweet", author_id="200",
                   receipt_reply_date="current", receipt_quote_reply_date="quote-current")
    owner.record_confirmed(state, **options)
    assert state["daily_reply_count"] == 3 and state["daily_quote_reply_count"] == 5
    assert state["daily_replied_author_counts"] == {"200": 1}
    assert state["daily_replied_author_ids"] == ["200"]
    before = dict(state)
    owner.record_confirmed(state, **{**options, "already_recorded": True})
    assert all(state[key] is value for key, value in before.items())
    owner.record_confirmed(state, **{**options, "receipt_reply_date": "stale", "receipt_quote_reply_date": "stale"})
    assert all(state[key] is value for key, value in before.items())
    owner.record_confirmed(state, **{**options, "receipt_reply_date": "stale"})
    assert state["daily_reply_count"] == 3 and state["daily_quote_reply_count"] == 6
    assert state["daily_replied_author_counts"] is before["daily_replied_author_counts"]


@pytest.mark.parametrize("boundary", ["author", "quote"])
def test_confirmation_count_failures_preserve_prior_mutations_and_order(monkeypatch, make_owner, boundary):
    ids = []
    state = {"daily_reply_date": "current", "daily_reply_count": 2,
             "daily_quote_reply_date": "current", "daily_quote_reply_count": "broken" if boundary == "quote" else 4,
             "daily_replied_author_ids": ids, "daily_replied_author_counts": {}}
    failure = RuntimeError("author ID append failed")
    appended = ["200"]
    append = Mock(side_effect=failure) if boundary == "author" else Mock(return_value=appended)
    monkeypatch.setattr(accounting, "append_unique_capped", append)
    owner = make_owner()
    with pytest.raises(RuntimeError if boundary == "author" else ValueError) as caught:
        owner.record_confirmed(state, already_recorded=False, candidate_source="quote_tweet",
                               author_id="200", receipt_reply_date="current", receipt_quote_reply_date="current")
    if boundary == "author":
        assert caught.value is failure and state["daily_replied_author_ids"] is ids
        assert state["daily_quote_reply_count"] == 4
    else:
        assert state["daily_replied_author_ids"] is appended
        assert state["daily_quote_reply_count"] == "broken"
    assert state["daily_reply_count"] == 3 and state["daily_replied_author_counts"] == {"200": 1}
    append.assert_called_once_with(ids, "200", 1000)
