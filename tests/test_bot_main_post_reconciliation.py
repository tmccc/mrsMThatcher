"""Focused contracts for main-post receipt application and local recovery."""
from __future__ import annotations

import copy
import inspect
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrs_bot_main_post_reconciliation as owner
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import (
    isolate_bot_runtime,  # noqa: F401
    schema_current_main_attempt,
    valid_regular_receipt_v2,
)


DEPENDENCIES = {'apply_meme_post_receipt': ['MEME_POST_TEXT',
                             'MEME_SCHEDULE_VERSION',
                             'MY_USER_ID',
                             'cache_tweet',
                             'log',
                             'meme_schedule_date_str',
                             'record_recent_own_post'],
 'reconcile_meme_post_receipt': ['InvalidMemePostReceipt',
                                 'MEME_POST_RECEIPT_FILE',
                                 'MEME_POST_TEXT',
                                 'apply_meme_post_receipt',
                                 'emit_account_root_posted',
                                 'finalize_confirmed_pending_schedule_receipt',
                                 'load_meme_post_receipt',
                                 'log',
                                 'remove_meme_post_receipt',
                                 'retire_lane_transport_journal_if_present',
                                 'save_state',
                                 'verify_lane_transport_source_lineage_if_present'],
 'apply_regular_post_receipt': ['MEME_SCHEDULE_VERSION',
                                'MY_USER_ID',
                                'cache_tweet',
                                'log',
                                'maybe_schedule_meme_after_quote_post',
                                'meme_schedule_date_str',
                                'record_recent_own_post'],
 'confirmed_regular_emergency_representation_is_complete': ['build_confirmed_pending_schedule_receipt',
                                                            'materialize_bound_regular_schedule_receipt',
                                                            'receipt_int',
                                                            'valid_post_id',
                                                            'valid_receipt_epoch'],
 'confirmed_meme_emergency_representation_is_complete': ['build_confirmed_pending_schedule_receipt',
                                                         'materialize_bound_meme_schedule_receipt',
                                                         'receipt_int',
                                                         'safe_bound_schedule_date_str',
                                                         'valid_post_id',
                                                         'valid_receipt_epoch'],
 'reconcile_regular_post_receipt': ['InvalidRegularPostReceipt',
                                    'REGULAR_POST_RECEIPT_FILE',
                                    'apply_regular_post_receipt',
                                    'emit_account_root_posted',
                                    'enqueue_historical_context_obligation',
                                    'ensure_reconciled_regular_receipt_schedule_is_future',
                                    'finalize_confirmed_pending_schedule_receipt',
                                    'load_regular_post_receipt',
                                    'log',
                                    'remove_regular_post_receipt',
                                    'retire_lane_transport_journal_if_present',
                                    'safely_process_due_historical_context_obligations',
                                    'save_regular_post_protected_state',
                                    'verify_lane_transport_source_lineage_if_present'],
 'reconcile_main_post_receipts': ['InvalidRegularPostReceipt',
                                  'MEME_POST_RECEIPT_FILE',
                                  'REGULAR_POST_RECEIPT_FILE',
                                  'both_main_post_receipts_exist',
                                  'log',
                                  'reconcile_meme_post_receipt',
                                  'reconcile_regular_post_receipt']}

SIGNATURES = {'apply_meme_post_receipt': "(receipt: 'dict', state: 'dict') -> 'None'",
 'reconcile_meme_post_receipt': "(state: 'dict') -> 'bool'",
 'apply_regular_post_receipt': "(receipt: 'dict', lines_used: 'set', "
                               "images_used: 'set', state: 'dict') -> 'None'",
 'confirmed_regular_emergency_representation_is_complete': '(*, post_id: '
                                                           "'str', post_epoch: "
                                                           "'int | None', "
                                                           "quote_hash: 'str', "
                                                           'image_basename: '
                                                           "'str', lines_used: "
                                                           "'set', "
                                                           'images_used: '
                                                           "'set', state: "
                                                           "'dict', "
                                                           'main_post_attempt: '
                                                           "'dict') -> 'bool'",
 'confirmed_meme_emergency_representation_is_complete': "(*, post_id: 'str', "
                                                        "post_epoch: 'int | "
                                                        "None', meme_basename: "
                                                        "'str', state: 'dict', "
                                                        'main_post_attempt: '
                                                        "'dict') -> 'bool'",
 'reconcile_regular_post_receipt': "(lines_used: 'set', images_used: 'set', "
                                   "state: 'dict', *, "
                                   "minimum_next_quote_epoch: 'int | None' = "
                                   "None, process_auxiliary_context: 'bool' = "
                                   "True) -> 'bool'",
 'reconcile_main_post_receipts': "(lines_used: 'set', images_used: 'set', "
                                 "state: 'dict', *, minimum_next_quote_epoch: "
                                 "'int | None' = None, "
                                 "process_auxiliary_context: 'bool' = True) -> "
                                 "'dict[str, bool]'"}

def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, io, logging, os, random, socket, sys, time, typing
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('Main-post reconciliation import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'historical_context_formatter'} or name.startswith('mrs_bot_') and name not in {'mrs_bot_main_post_reconciliation', 'mrs_bot_regular_post_completion'}:
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = os.lstat = os.stat = forbidden
os.getenv = os._Environ.__getitem__ = os.urandom = forbidden
Path.home = logging.getLogger = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
time.time = time.monotonic = forbidden
before = random.getstate()
random.Random = random.seed = random.random = forbidden
import mrs_bot_main_post_reconciliation
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


@pytest.mark.parametrize("name", DEPENDENCIES)
def test_adapters_preserve_signatures_current_dependencies_references_and_errors(monkeypatch, name):
    adapter = getattr(bot, name)
    signature = inspect.signature(adapter)
    assert str(signature) == SIGNATURES[name]
    positional = [p.name for p in signature.parameters.values()
                  if p.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD]
    keyword_only = [p.name for p in signature.parameters.values()
                    if p.kind is inspect.Parameter.KEYWORD_ONLY]
    for _ in range(2):
        with monkeypatch.context() as patch:
            current = {dep: object() for dep in DEPENDENCIES[name]}
            for dep, value in current.items():
                patch.setattr(bot, dep, value)
            result = {"original": []}
            expected = {}

            def capture(*args, **kwargs):
                assert len(args) == len(positional)
                assert all(value is expected[key] for key, value in zip(positional, args))
                supplied = {key: expected[key] for key in keyword_only} | current
                assert kwargs.keys() == supplied.keys()
                assert all(kwargs[key] is value for key, value in supplied.items())
                return result

            patch.setattr(bot, "_main_post_reconciliation", SimpleNamespace(**{name: capture}))
            for include_defaults in (True, False):
                provided = {key: object() for key, param in signature.parameters.items()
                            if include_defaults or param.default is inspect.Parameter.empty}
                bound = signature.bind(**provided)
                bound.apply_defaults()
                expected = bound.arguments
                assert adapter(**provided) is result
            with pytest.raises(TypeError, match="not_a_public_option"):
                adapter(**provided, not_a_public_option={})
            failure = TypeError("current owner failure")
            patch.setattr(bot, "_main_post_reconciliation", SimpleNamespace(**{name: Mock(side_effect=failure)}))
            with pytest.raises(TypeError) as caught:
                adapter(**provided)
            assert caught.value is failure


def _callbacks(monkeypatch, *names):
    events = Mock()
    for name in names:
        monkeypatch.setattr(bot, name, getattr(events, name))
    return events


@pytest.mark.parametrize("schema_version", [3])
def test_regular_application_preserves_histories_empty_schedule_and_callback_order(monkeypatch, schema_version):
    receipt = valid_regular_receipt_v2(
        schema_version=schema_version, next_meme_post_epoch=0,
        meme_schedule_version=0, next_meme_schedule_mode="",
        next_meme_schedule_date="", meme_anchor_quote_post_epoch=0,
    )
    lines, images, state = {"old quote"}, {"old image"}, {"next_meme_post_epoch": 99}
    events = _callbacks(
        monkeypatch,
        "maybe_schedule_meme_after_quote_post", "cache_tweet", "record_recent_own_post",
    )
    assert bot.apply_regular_post_receipt(receipt, lines, images, state) is None
    assert lines == set(receipt["quote_history_after"])
    assert images == set(receipt["image_history_after"])
    assert [state[key] for key in (
        "next_meme_post_epoch", "meme_schedule_version", "next_meme_schedule_mode",
        "next_meme_schedule_date", "meme_anchor_quote_post_epoch",
    )] == [0, 0, "", "", 0]
    assert events.mock_calls == [
        call.cache_tweet(
            state, tweet_id=receipt["post_id"], text=receipt["text"],
            author_id=str(bot.MY_USER_ID), conversation_id=receipt["post_id"],
            referenced_tweets=[], post_type="quote",
        ),
        call.record_recent_own_post(state, receipt["post_id"]),
    ]
    assert events.cache_tweet.call_args.args[0] is state
    # A stale receipt still reaches the final experiment callback.
    events.reset_mock()
    state["last_quote_post_epoch"] += 1
    bot.apply_regular_post_receipt(receipt, lines, images, state)


@pytest.mark.parametrize("last_id", ["", "970001", "970002"])
def test_meme_tied_application_keeps_schedule_guard_and_current_cache_order(monkeypatch, last_id):
    receipt = {
        "schema_version": 2, "source_attempt": {"schema_version": 5},
        "post_id": "970001", "meme_basename": "001_meme.png",
        "meme_post_epoch": 100, "next_meme_post_epoch": 200,
        "meme_schedule_version": 0, "next_meme_schedule_date": "bound date",
        "text": "", "image_summary": 7,
    }
    state = {
        "last_main_post_id": last_id, "last_meme_post_epoch": 100,
        "next_meme_post_epoch": 150, "posted_meme_filenames": ["002_meme.png"],
    }
    events = _callbacks(monkeypatch, "meme_schedule_date_str", "cache_tweet", "record_recent_own_post")
    monkeypatch.setattr(bot, "MEME_POST_TEXT", "current default")
    assert bot.apply_meme_post_receipt(receipt, state) is None
    assert state["posted_meme_filenames"] == ["001_meme.png", "002_meme.png"]
    if last_id == "970002":
        assert state["last_main_post_id"] == last_id
        assert state["next_meme_post_epoch"] == 150
        assert events.mock_calls == []
    else:
        assert state["next_meme_post_epoch"] == 200
        assert state["meme_schedule_version"] == 0
        assert state["next_meme_schedule_date"] == "bound date"
        assert events.mock_calls == [
            call.cache_tweet(
                state, tweet_id="970001", text="",
                author_id=str(bot.MY_USER_ID), conversation_id="970001",
                referenced_tweets=[], image_summary="7", post_type="daily_meme",
            ),
            call.record_recent_own_post(state, "970001"),
        ]
        assert events.cache_tweet.call_args.args[0] is state


def test_application_native_errors_keep_original_partial_mutation(monkeypatch):
    receipt = valid_regular_receipt_v2(quote_history_after=None)
    lines, images, state = {"old quote"}, {"old image"}, {}
    with pytest.raises(TypeError):
        bot.apply_regular_post_receipt(receipt, lines, images, state)
    assert lines == set() and images == {"old image"} and state == {}
    with pytest.raises(KeyError, match="meme_basename"):
        bot.apply_meme_post_receipt({"post_id": "970001"}, state)
    assert state == {}




def _emergency_case(lane):
    attempt = schema_current_main_attempt(lane)
    attempt["lifecycle_state"] = "attempting"
    epoch = attempt["attempt_epoch"] + 60
    pending = bot.build_confirmed_pending_schedule_receipt(
        attempt, post_id="950001", confirmation_epoch=epoch,
    )
    state = {}
    kwargs = dict(post_id="950001", post_epoch=epoch, state=state, main_post_attempt=attempt)
    if lane == "quote_image":
        receipt = bot.materialize_bound_regular_schedule_receipt(pending)
        lines, images = set(), set()
        bot.apply_regular_post_receipt(receipt, lines, images, state)
        kwargs.update(quote_hash=receipt["quote_hash"], image_basename=receipt["image_basename"], lines_used=lines, images_used=images)
    else:
        receipt = bot.materialize_bound_meme_schedule_receipt(pending)
        bot.apply_meme_post_receipt(receipt, state)
        kwargs["meme_basename"] = receipt["meme_basename"]
    return kwargs, pending, receipt


@pytest.mark.parametrize("lane,kind", [("quote_image", "regular"), ("daily_meme", "meme")])
def test_emergency_predicates_keep_eager_work_outside_narrow_exception_boundary(monkeypatch, lane, kind):
    kwargs, pending, receipt = _emergency_case(lane)
    predicate = getattr(bot, f"confirmed_{kind}_emergency_representation_is_complete")
    materializer_name = f"materialize_bound_{kind}_schedule_receipt"
    events = _callbacks(monkeypatch, "build_confirmed_pending_schedule_receipt", materializer_name)
    builder = events.build_confirmed_pending_schedule_receipt
    materializer = getattr(events, materializer_name)
    builder.return_value, materializer.return_value = pending, receipt
    before = copy.deepcopy(kwargs)
    assert predicate(**kwargs) is True
    assert kwargs == before
    assert builder.call_args.args[0] is kwargs["main_post_attempt"]
    assert materializer.call_args.args[0] is pending
    events.reset_mock()
    with pytest.raises(AttributeError):
        predicate(**{**kwargs, "state": None})
    assert events.mock_calls == []
    builder.side_effect = ValueError("build failed")
    assert predicate(**kwargs) is False
    materializer.assert_not_called()
    builder.side_effect = KeyboardInterrupt("build interrupted")
    with pytest.raises(KeyboardInterrupt):
        predicate(**kwargs)
    builder.side_effect = None
    materializer.side_effect = ValueError("materialize failed")
    assert predicate(**kwargs) is False
    materializer.side_effect = None
    materializer.return_value = {**receipt, "next_meme_post_epoch": "not an epoch"}
    with pytest.raises(ValueError):
        predicate(**kwargs)


def test_meme_emergency_eager_summary_and_late_cache_errors_are_native(monkeypatch):
    kwargs, pending, receipt = _emergency_case("daily_meme")
    builder = Mock(return_value=pending)
    monkeypatch.setattr(bot, "build_confirmed_pending_schedule_receipt", builder)
    monkeypatch.setattr(bot, "materialize_bound_meme_schedule_receipt", Mock(return_value=receipt))
    predicate = bot.confirmed_meme_emergency_representation_is_complete
    with pytest.raises(TypeError):
        predicate(**{**kwargs, "state": {**kwargs["state"], "posted_meme_filenames": None}})
    with pytest.raises(AttributeError):
        predicate(**{**kwargs, "main_post_attempt": {"recovery_plan": None}})
    builder.assert_not_called()
    with pytest.raises(AttributeError):
        predicate(**{**kwargs, "state": {**kwargs["state"], "tweet_cache": None}})
    builder.assert_called_once()




@pytest.mark.parametrize("process_auxiliary", [True, False])
def test_regular_pending_recovery_orders_retirement_and_auxiliary(monkeypatch, process_auxiliary):
    pending = {"post_id": 950001}
    receipt = valid_regular_receipt_v2(quote_text="", line_no=0, image_no=0)
    lines, images, state = set(), set(), {}
    events = _callbacks(monkeypatch, *[
        name for name in DEPENDENCIES["reconcile_regular_post_receipt"]
        if not name.isupper() and not name.startswith("Invalid")
    ])
    events.load_regular_post_receipt.return_value = ("pending_schedule", pending)
    events.finalize_confirmed_pending_schedule_receipt.return_value = receipt
    assert bot.reconcile_regular_post_receipt(
        lines, images, state, minimum_next_quote_epoch=0,
        process_auxiliary_context=process_auxiliary,
    ) is True
    assert [entry[0] for entry in events.mock_calls] == [
        "load_regular_post_receipt", "verify_lane_transport_source_lineage_if_present",
        "log.warning", "finalize_confirmed_pending_schedule_receipt", "log.warning",
        "apply_regular_post_receipt", "ensure_reconciled_regular_receipt_schedule_is_future",
        "save_regular_post_protected_state",
        "enqueue_historical_context_obligation",
        "retire_lane_transport_journal_if_present", "remove_regular_post_receipt",
        "emit_account_root_posted", "log.info",
        *(["safely_process_due_historical_context_obligations"] if process_auxiliary else []),
    ]
    events.verify_lane_transport_source_lineage_if_present.assert_called_once_with(
        receipt_path=bot.REGULAR_POST_RECEIPT_FILE, receipt=pending, lane="quote_image", post_id="950001",
    )
    assert events.verify_lane_transport_source_lineage_if_present.call_args.kwargs["receipt"] is pending
    assert all(actual is expected for actual, expected in zip(
        events.apply_regular_post_receipt.call_args.args, (receipt, lines, images, state),
    ))
    events.ensure_reconciled_regular_receipt_schedule_is_future.assert_called_once_with(receipt, state, 0)
    events.save_regular_post_protected_state.assert_called_once_with(lines, images, state, durable=True)
    events.retire_lane_transport_journal_if_present.assert_called_once_with(
        receipt_path=bot.REGULAR_POST_RECEIPT_FILE, receipt=receipt, lane="quote_image", post_id="950001",
    )
    for name in ("enqueue_historical_context_obligation", "remove_regular_post_receipt"):
        assert getattr(events, name).call_args.args[0] is receipt
    events.emit_account_root_posted.assert_called_once_with(
        lane="quote_image", post_id="950001", public_text=receipt["text"],
        quote_id=receipt["quote_hash"], quote_text="",
    )
    if process_auxiliary:
        events.safely_process_due_historical_context_obligations.assert_called_once_with(
            parent_post_id="950001", runtime_state=state,
        )


def test_meme_pending_recovery_preserves_references_and_save_failure_boundary(monkeypatch):
    pending = {"post_id": 970001}
    receipt = {"post_id": "970001", "meme_basename": "001_meme.png", "text": "", "image_summary": None}
    state = {}
    events = _callbacks(monkeypatch, *[
        name for name in DEPENDENCIES["reconcile_meme_post_receipt"]
        if not name.isupper() and not name.startswith("Invalid")
    ])
    events.load_meme_post_receipt.return_value = ("pending_schedule", pending)
    events.finalize_confirmed_pending_schedule_receipt.return_value = receipt
    assert bot.reconcile_meme_post_receipt(state) is True
    expected_order = [
        "load_meme_post_receipt", "verify_lane_transport_source_lineage_if_present",
        "log.warning", "finalize_confirmed_pending_schedule_receipt", "log.warning",
        "apply_meme_post_receipt", "save_state", "retire_lane_transport_journal_if_present",
        "remove_meme_post_receipt", "emit_account_root_posted",
    ]
    assert [entry[0] for entry in events.mock_calls] == expected_order
    assert events.verify_lane_transport_source_lineage_if_present.call_args.kwargs["receipt"] is pending
    assert events.finalize_confirmed_pending_schedule_receipt.call_args.args[0] is pending
    assert events.apply_meme_post_receipt.call_args.args[0] is receipt
    assert events.apply_meme_post_receipt.call_args.args[1] is state
    events.save_state.assert_called_once_with(state, durable=True)
    events.retire_lane_transport_journal_if_present.assert_called_once_with(
        receipt_path=bot.MEME_POST_RECEIPT_FILE, receipt=receipt, lane="daily_meme", post_id="970001",
    )
    assert events.remove_meme_post_receipt.call_args.args[0] is receipt
    events.emit_account_root_posted.assert_called_once_with(
        lane="daily_meme", post_id="970001", public_text="", image_summary=None,
    )
    events.reset_mock()
    failure = OSError("durable save failed")
    events.save_state.side_effect = failure
    with pytest.raises(OSError) as caught:
        bot.reconcile_meme_post_receipt(state)
    assert caught.value is failure
    assert [entry[0] for entry in events.mock_calls] == expected_order[:7]


@pytest.mark.parametrize("caption", ["", "Published caption"])
def test_bound_meme_replay_preserves_caption_in_cache_and_observation(monkeypatch, caption):
    monkeypatch.setattr(bot, "MEME_POST_TEXT", caption)
    attempt = schema_current_main_attempt("daily_meme")
    attempt["lifecycle_state"] = "attempting"
    epoch = attempt["attempt_epoch"] + 1
    pending = bot.build_confirmed_pending_schedule_receipt(
        attempt, post_id="970001", confirmation_epoch=epoch,
    )
    receipt = bot.materialize_bound_meme_schedule_receipt(pending)
    assert bot.meme_post_receipt_is_semantically_valid(receipt)
    bot.write_meme_post_receipt(receipt)
    monkeypatch.setattr(bot, "MEME_POST_TEXT", "New caption for future memes")
    observed = Mock()
    monkeypatch.setattr(bot, "emit_account_root_posted", observed)
    state = bot.default_state()

    assert bot.reconcile_meme_post_receipt(state) is True

    assert state["tweet_cache"]["970001"]["text"] == caption
    observed.assert_called_once_with(
        lane="daily_meme", post_id="970001", public_text=caption,
        image_summary=receipt.get("image_summary"),
    )
    assert bot.confirmed_meme_emergency_representation_is_complete(
        post_id="970001", post_epoch=epoch, meme_basename="001_meme.png",
        state=state, main_post_attempt=attempt,
    )
    assert not bot.MEME_POST_RECEIPT_FILE.exists()


def test_legacy_meme_text_fallback_requires_an_absent_field(monkeypatch):
    receipt = {
        "post_id": "970001", "meme_basename": "001_meme.png",
        "meme_post_epoch": 100, "next_meme_post_epoch": 200,
    }
    monkeypatch.setattr(bot, "MEME_POST_TEXT", "legacy default")
    monkeypatch.setattr(bot, "load_meme_post_receipt", Mock(return_value=("valid", receipt)))
    observed = Mock()
    monkeypatch.setattr(bot, "emit_account_root_posted", observed)
    monkeypatch.setattr(bot, "remove_meme_post_receipt", Mock())
    state = bot.default_state()
    assert bot.reconcile_meme_post_receipt(state) is True
    assert state["tweet_cache"]["970001"]["text"] == "legacy default"
    assert observed.call_args.kwargs["public_text"] == "legacy default"


def test_main_recovery_gate_precedes_current_regular_then_meme_callbacks(monkeypatch):
    events = _callbacks(monkeypatch, "both_main_post_receipts_exist", "log", "reconcile_regular_post_receipt", "reconcile_meme_post_receipt")
    monkeypatch.setattr(bot, "InvalidRegularPostReceipt", LookupError)
    lines, images, state = set(), set(), {}
    events.both_main_post_receipts_exist.return_value = True
    with pytest.raises(LookupError, match="Both main-post receipts exist"):
        bot.reconcile_main_post_receipts(lines, images, state)
    assert [entry[0] for entry in events.mock_calls] == ["both_main_post_receipts_exist", "log.critical"]
    events.reset_mock()
    events.both_main_post_receipts_exist.return_value = False
    regular, meme = object(), object()
    events.reconcile_regular_post_receipt.return_value = regular
    events.reconcile_meme_post_receipt.return_value = meme
    result = bot.reconcile_main_post_receipts(lines, images, state, minimum_next_quote_epoch=0, process_auxiliary_context=False)
    assert result["regular"] is regular and result["meme"] is meme
    assert events.mock_calls == [
        call.both_main_post_receipts_exist(),
        call.reconcile_regular_post_receipt(lines, images, state, minimum_next_quote_epoch=0, process_auxiliary_context=False),
        call.reconcile_meme_post_receipt(state),
    ]
    assert events.reconcile_meme_post_receipt.call_args.args[0] is state
    events.reset_mock()
    failure = TypeError("regular recovery failed")
    events.reconcile_regular_post_receipt.side_effect = failure
    with pytest.raises(TypeError) as caught:
        bot.reconcile_main_post_receipts(lines, images, state)
    assert caught.value is failure
    events.reconcile_meme_post_receipt.assert_not_called()
