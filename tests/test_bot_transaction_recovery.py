from __future__ import annotations

from contextlib import contextmanager
import inspect
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrsMThatcher2 as bot
import mrs_bot_transaction_recovery as recovery
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401


DEPENDENCIES = {'ensure_reconciled_regular_receipt_schedule_is_future': ['log', 'schedule_next_quote_post'],
 'block_if_unresolved_regular_post_receipt': ['InvalidRegularPostReceipt',
                                              'REGULAR_POST_RECEIPT_FILE',
                                              'UnresolvedRegularPostReceipt',
                                              'load_regular_post_receipt'],
 'reconcile_startup_main_post_receipts': ['global_remote_writes_paused',
                                          'log',
                                          'reconcile_main_post_receipts'],
 'reconcile_confirmed_transactions_before_global_barrier': ['CONFIRMED_REPLY_RECEIPT_FILE',
                                                            'HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE',
                                                            'MEME_POST_RECEIPT_FILE',
                                                            'REGULAR_POST_RECEIPT_FILE',
                                                            'TRANSPORT_SOURCE_VALIDATOR_ID',
                                                            'TransportJournalError',
                                                            '_legacy_conversational_transport_source_semantic_validator',
                                                            '_promote_legacy_sending_reply_receipt_from_confirmed_transport',
                                                            '_reply_confirmation_epoch_after_remote_success',
                                                            'bind_confirmed_transport_source',
                                                            'confirmation_epoch_for_main_attempt',
                                                            'emit_historical_context_store_observation',
                                                            'global_remote_writes_paused',
                                                            'hashlib',
                                                            'historical_context_outbox_remote_attempt_parent_for_local_reconciliation',
                                                            'historical_context_outbox_store',
                                                            'historical_context_reply_store',
                                                            'inspect_confirmed_transport_transaction',
                                                            'inspect_transport_state',
                                                            'journal_path_for_receipt',
                                                            'load_confirmed_reply_receipt',
                                                            'load_meme_post_receipt',
                                                            'load_regular_post_receipt',
                                                            'log_event',
                                                            'now_epoch',
                                                            'promote_main_post_attempt_to_confirmed_pending_schedule',
                                                            'promote_sending_reply_receipt',
                                                            'receipt_namespace_entry_exists',
                                                            'reconcile_confirmed_reply_receipt',
                                                            'reconcile_main_post_receipts',
                                                            'recover_interrupted_historical_context_attempt',
                                                            'remote_write_safety_incident_is_latched',
                                                            'remote_write_safety_marker_path_present_or_unsafe',
                                                            'remote_write_safety_protocol_is_active',
                                                            'transport_source_semantic_validator']}

SIGNATURES = {'ensure_reconciled_regular_receipt_schedule_is_future': "(receipt: 'dict', state: 'dict', "
                                                         "current: 'int') -> 'bool'",
 'block_if_unresolved_regular_post_receipt': "() -> 'None'",
 'reconcile_startup_main_post_receipts': "(lines_used: 'set', images_used: 'set', state: 'dict', "
                                         "current: 'int') -> 'dict[str, bool]'",
 'reconcile_confirmed_transactions_before_global_barrier': "(lines_used: 'set', images_used: "
                                                           "'set', state: 'dict', current: 'int | "
                                                           "None' = None) -> 'dict[str, bool]'"}

def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, json, io, logging, os, random, socket, sys, time, typing
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('Transaction recovery import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'historical_context_formatter', 'historical_context_outbox', 'transaction_mutation_authority'} or name.startswith('mrs_bot_') and name not in {'mrs_bot_transaction_recovery', 'mrs_bot_receipt_retirement', 'mrs_bot_durable_json_io'}:
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
import mrs_bot_transaction_recovery
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
assert 'requests' not in sys.modules
assert 'single_call_reply' not in sys.modules
assert 'transaction_mutation_authority' not in sys.modules
assert mrs_bot_transaction_recovery.reconcile_confirmed_transactions_before_global_barrier.__annotations__['return'] == 'dict[str, bool]'
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

            patch.setattr(bot, "_transaction_recovery", SimpleNamespace(**{name: capture}))
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
            patch.setattr(bot, "_transaction_recovery", SimpleNamespace(**{name: Mock(side_effect=failure)}))
            with pytest.raises(TypeError) as caught:
                adapter(**provided)
            assert caught.value is failure


@pytest.mark.parametrize("case", [
    "mismatch", "future", "due", "last_error", "receipt_error", "next_error",
    "warning_error", "schedule_error",
])
def test_schedule_conversion_short_circuit_order_references_and_native_errors(monkeypatch, case):
    events = []
    failure = ValueError("current dependency failure")

    class Epoch:
        def __init__(self, label, value):
            self.label, self.value = label, value

        def __int__(self):
            events.append(self.label)
            if case == self.label + "_error":
                raise failure
            return self.value

    receipt = {"quote_post_epoch": Epoch("receipt", 7)}
    state = {
        "last_quote_post_epoch": Epoch("last", 6 if case == "mismatch" else 7),
        "next_quote_post_epoch": Epoch("next", 12 if case == "future" else 11),
    }

    def warning(message):
        events.append("warning")
        assert message == (
            "Reconciled regular receipt has a due quote schedule; deferring the next "
            "regular post before completing receipt replay"
        )
        if case == "warning_error":
            raise failure

    def schedule(actual_state, current, *, save):
        events.append("schedule")
        assert actual_state is state and current == 11 and save is False
        if case == "schedule_error":
            raise failure

    monkeypatch.setattr(bot, "log", SimpleNamespace(warning=warning))
    monkeypatch.setattr(bot, "schedule_next_quote_post", schedule)
    if case.endswith("_error"):
        with pytest.raises(ValueError) as caught:
            bot.ensure_reconciled_regular_receipt_schedule_is_future(receipt, state, 11)
        assert caught.value is failure
    else:
        assert bot.ensure_reconciled_regular_receipt_schedule_is_future(receipt, state, 11) is (case == "due")
    ordered = ["last", "receipt", "next", "warning", "schedule"]
    stop = case.removesuffix("_error") if case.endswith("_error") else {
        "mismatch": "receipt", "future": "next", "due": "schedule",
    }[case]
    assert events == ordered[:ordered.index(stop) + 1]


@pytest.mark.parametrize("status", ["absent", "invalid", "sending", "pending_schedule", "valid", None, "loader_error"])
def test_regular_receipt_gate_keeps_exact_status_errors_and_current_path(monkeypatch, status):
    trace = Mock()
    failure = OSError("current receipt loader failed")

    class ReceiptPath:
        def __str__(self):
            trace.path()
            return "current-receipt-path"

    monkeypatch.setattr(bot, "REGULAR_POST_RECEIPT_FILE", ReceiptPath())
    monkeypatch.setattr(bot, "load_regular_post_receipt", trace.load)
    trace.load.return_value = (status, object())
    if status == "loader_error":
        trace.load.side_effect = failure
        with pytest.raises(OSError) as caught:
            bot.block_if_unresolved_regular_post_receipt()
        assert caught.value is failure
    elif status == "absent":
        assert bot.block_if_unresolved_regular_post_receipt() is None
    else:
        kind = bot.InvalidRegularPostReceipt if status == "invalid" else bot.UnresolvedRegularPostReceipt
        with pytest.raises(kind) as caught:
            bot.block_if_unresolved_regular_post_receipt()
        assert str(caught.value) == (
            "Invalid regular-post receipt blocks main posting: current-receipt-path"
            if status == "invalid" else
            "Unresolved regular-post receipt must be reconciled before another main post: current-receipt-path"
        )
    assert trace.mock_calls == [call.load()] + ([] if status in {"absent", "loader_error"} else [call.path()])


@pytest.mark.parametrize("paused", [False, True])
@pytest.mark.parametrize("fails", [False, True])
def test_startup_pause_order_result_identity_and_native_errors(monkeypatch, paused, fails):
    trace = Mock()
    trace.pause.return_value = paused
    result, lines, images, state, current = {"regular": object()}, set(), set(), {}, object()
    trace.reconcile.return_value = result
    failure = TypeError("startup callback failed")
    if fails:
        (trace.warning if paused else trace.reconcile).side_effect = failure
    monkeypatch.setattr(bot, "global_remote_writes_paused", trace.pause)
    monkeypatch.setattr(bot, "log", SimpleNamespace(warning=trace.warning))
    monkeypatch.setattr(bot, "reconcile_main_post_receipts", trace.reconcile)
    if fails:
        with pytest.raises(TypeError) as caught:
            bot.reconcile_startup_main_post_receipts(lines, images, state, current)
        assert caught.value is failure
    else:
        actual = bot.reconcile_startup_main_post_receipts(lines, images, state, current)
        if paused:
            assert actual == {"regular": False, "meme": False}
        else:
            assert actual is result
    assert trace.mock_calls == [call.pause()] + ([call.warning(
        "Global runtime control pause is active; leaving main-post receipts untouched during startup"
    )] if paused else [call.reconcile(lines, images, state, minimum_next_quote_epoch=current)])
    if not paused:
        assert all(a is b for a, b in zip(trace.reconcile.call_args.args, (lines, images, state)))
        assert trace.reconcile.call_args.kwargs["minimum_next_quote_epoch"] is current
    elif not fails:
        assert bot.reconcile_startup_main_post_receipts(lines, images, state, current) is not actual


def _prebarrier(monkeypatch, present=(), classification="clear"):
    trace = Mock()
    for name in (*DEPENDENCIES["reconcile_confirmed_transactions_before_global_barrier"],
                 "confirmed_context_outbox_matches_receipt"):
        if name.isupper() or name in {"hashlib", "TransportJournalError"}:
            continue
        callback = getattr(trace, name)
        callback.side_effect = AssertionError("unexpected dependency: " + name)
        target = recovery if name == "confirmed_context_outbox_matches_receipt" else bot
        monkeypatch.setattr(target, name, callback)
    for name, value in [
        ("global_remote_writes_paused", False),
        ("remote_write_safety_protocol_is_active", True),
        ("remote_write_safety_incident_is_latched", False),
        ("remote_write_safety_marker_path_present_or_unsafe", False),
    ]:
        callback = getattr(trace, name)
        callback.side_effect, callback.return_value = None, value
    paths = (bot.REGULAR_POST_RECEIPT_FILE, bot.MEME_POST_RECEIPT_FILE,
             bot.CONFIRMED_REPLY_RECEIPT_FILE, bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE)
    journal = object()
    trace.receipt_namespace_entry_exists.side_effect = lambda path: path in present
    trace.journal_path_for_receipt.side_effect = lambda path: journal
    trace.inspect_transport_state.side_effect = lambda path: SimpleNamespace(classification=classification)
    return trace, paths, journal


def _inspection_calls(paths):
    return [
        call.global_remote_writes_paused(), call.remote_write_safety_protocol_is_active(),
        call.remote_write_safety_incident_is_latched(), call.remote_write_safety_marker_path_present_or_unsafe(),
        *[call.receipt_namespace_entry_exists(path) for path in paths],
    ]


@pytest.mark.parametrize("stop", range(4))
@pytest.mark.parametrize("fails", [False, True])
def test_prebarrier_policy_guards_short_circuit_in_order(monkeypatch, stop, fails):
    trace, paths, _ = _prebarrier(monkeypatch)
    guards = [entry[0] for entry in _inspection_calls(paths)[:4]]
    failure = OSError("current policy failed")
    guard = getattr(trace, guards[stop])
    if fails:
        guard.side_effect = failure
        with pytest.raises(OSError) as caught:
            bot.reconcile_confirmed_transactions_before_global_barrier(set(), set(), {})
        assert caught.value is failure
    else:
        guard.return_value = stop != 1
        result = bot.reconcile_confirmed_transactions_before_global_barrier(set(), set(), {})
        assert list(result.items()) == [(key, False) for key in (
            "historical_context", "conversational_reply", "regular", "meme",
        )]
    assert trace.mock_calls == _inspection_calls(paths)[:stop + 1]
    if not fails:
        assert bot.reconcile_confirmed_transactions_before_global_barrier(set(), set(), {}) is not result


@pytest.mark.parametrize("fail_index", [None, 3])
def test_all_four_namespaces_are_inspected_even_after_multiple_receipts(monkeypatch, fail_index):
    trace, paths, _ = _prebarrier(monkeypatch)
    failure = PermissionError("last namespace cannot be inspected")
    trace.receipt_namespace_entry_exists.side_effect = [True, True, False, failure if fail_index is not None else False]
    if fail_index is not None:
        with pytest.raises(PermissionError) as caught:
            bot.reconcile_confirmed_transactions_before_global_barrier(set(), set(), {})
        assert caught.value is failure
    else:
        assert not any(bot.reconcile_confirmed_transactions_before_global_barrier(set(), set(), {}).values())
    assert trace.mock_calls == _inspection_calls(paths)
    assert all(a.args[0] is p for a, p in zip(trace.receipt_namespace_entry_exists.call_args_list, paths))


@pytest.mark.parametrize("case", ["none", "missing", "recover", "failure"])
def test_empty_namespace_recovers_only_current_sole_parent_inside_worker_lock(monkeypatch, case):
    trace, paths, _ = _prebarrier(monkeypatch)
    parent, obligation, recovered, epoch = object(), {}, {"payload": []}, object()
    failure = TypeError("local persistence failed")
    trace.historical_context_outbox_remote_attempt_parent_for_local_reconciliation.side_effect = lambda: None if case == "none" else parent

    @contextmanager
    def locked():
        trace.enter()
        try:
            yield
        finally:
            trace.exit()

    trace.worker_lock.side_effect = locked
    store = SimpleNamespace(worker_lock=trace.worker_lock, get=trace.get)
    trace.historical_context_outbox_store.side_effect = lambda: store
    trace.get.return_value = None if case == "missing" else obligation
    trace.now_epoch.side_effect = lambda: epoch
    trace.recover_interrupted_historical_context_attempt.side_effect = failure if case == "failure" else lambda *a, **k: recovered
    trace.log_event.side_effect = None
    if case in {"missing", "failure"}:
        with pytest.raises(TypeError if case == "failure" else RuntimeError) as caught:
            bot.reconcile_confirmed_transactions_before_global_barrier(set(), set(), {})
        if case == "failure":
            assert caught.value is failure
        else:
            assert str(caught.value) == "risky historical-context outbox parent disappeared before local reconciliation"
    else:
        result = bot.reconcile_confirmed_transactions_before_global_barrier(set(), set(), {})
        assert result == {"historical_context": case == "recover", "conversational_reply": False, "regular": False, "meme": False}
    expected = _inspection_calls(paths) + [call.historical_context_outbox_remote_attempt_parent_for_local_reconciliation()]
    if case != "none":
        expected += [call.historical_context_outbox_store(), call.worker_lock(), call.enter(), call.get(parent)]
        if case != "missing":
            expected += [call.now_epoch(), call.recover_interrupted_historical_context_attempt(store, obligation, recovered_epoch=epoch)]
            args = trace.recover_interrupted_historical_context_attempt.call_args
            assert args.args[0] is store and args.args[1] is obligation and args.kwargs["recovered_epoch"] is epoch
        expected += [call.exit()]
        if case == "recover":
            expected += [call.log_event("historical_context_obligation", **recovered)]
            assert trace.log_event.call_args.kwargs["payload"] is recovered["payload"]
    assert trace.mock_calls == expected


def test_journal_inspection_native_error_precedes_any_lane_loader(monkeypatch):
    trace, paths, journal = _prebarrier(monkeypatch, (bot.REGULAR_POST_RECEIPT_FILE,))
    failure = ValueError("journal inspection failed")
    trace.inspect_transport_state.side_effect = failure
    with pytest.raises(ValueError) as caught:
        bot.reconcile_confirmed_transactions_before_global_barrier(set(), set(), {})
    assert caught.value is failure
    assert trace.mock_calls == _inspection_calls(paths) + [
        call.journal_path_for_receipt(paths[0]), call.inspect_transport_state(journal),
    ]


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_main_promotion_keeps_binding_source_epoch_and_image_summary_order(monkeypatch, lane):
    owning = bot.REGULAR_POST_RECEIPT_FILE if lane == "quote_image" else bot.MEME_POST_RECEIPT_FILE
    trace, paths, journal = _prebarrier(monkeypatch, (owning,), "confirmed_pair")

    class Summary:
        def __str__(self):
            trace.summary()
            return "current summary"

    loaded, source = {"lifecycle_state": "attempting"}, {"recovery_plan": {"image_summary": Summary()}}
    details = SimpleNamespace(lane=lane, post_id=object(), confirmation_epoch=object())
    bound = SimpleNamespace(source_binding=SimpleNamespace(receipt_document=source), details=details)
    loader = "load_regular_post_receipt" if lane == "quote_image" else "load_meme_post_receipt"
    getattr(trace, loader).side_effect = [("sending", loaded), ("absent", None)]
    getattr(trace, "load_meme_post_receipt" if lane == "quote_image" else "load_regular_post_receipt").side_effect = [("absent", None)]
    trace.bind_confirmed_transport_source.side_effect = lambda **kwargs: bound
    epoch = object()
    trace.confirmation_epoch_for_main_attempt.side_effect = lambda *args: epoch
    trace.promote_main_post_attempt_to_confirmed_pending_schedule.side_effect = lambda *args, **kwargs: None
    assert not any(bot.reconcile_confirmed_transactions_before_global_barrier(set(), set(), {}).values())
    assert trace.mock_calls == _inspection_calls(paths) + [
        call.journal_path_for_receipt(owning), call.inspect_transport_state(journal), getattr(call, loader)(),
        call.bind_confirmed_transport_source(journal_path=journal, receipt_path=owning,
            validator_id=bot.TRANSPORT_SOURCE_VALIDATOR_ID, validator=bot.transport_source_semantic_validator),
        *([call.summary()] if lane == "daily_meme" else []),
        call.confirmation_epoch_for_main_attempt(source, details.confirmation_epoch),
        call.promote_main_post_attempt_to_confirmed_pending_schedule(source, post_id=details.post_id,
            confirmation_epoch=epoch, image_summary="current summary" if lane == "daily_meme" else ""),
        call.load_regular_post_receipt(), call.load_meme_post_receipt(),
    ]
    assert trace.confirmation_epoch_for_main_attempt.call_args.args[0] is source
    assert trace.promote_main_post_attempt_to_confirmed_pending_schedule.call_args.args[0] is source


@pytest.mark.parametrize("statuses,current", [
    (("pending_schedule", "invalid"), None), (("invalid", "valid"), 0),
    (("valid", "pending_schedule"), 17), (("sending", "absent"), None),
])
def test_final_main_loaders_are_eager_epoch_is_lazy_and_result_coercion_is_ordered(monkeypatch, statuses, current):
    trace, paths, journal = _prebarrier(monkeypatch, (bot.MEME_POST_RECEIPT_FILE,))
    trace.load_meme_post_receipt.side_effect = [("absent", None), (statuses[1], object())]
    trace.load_regular_post_receipt.side_effect = [(statuses[0], object())]
    lines, images, state, epoch = set(), set(), {}, object()
    trace.now_epoch.side_effect = lambda: epoch

    class Outcome:
        def __init__(self, lane):
            self.lane = lane

        def __bool__(self):
            trace.coerce(self.lane)
            return self.lane == "regular"

    trace.reconcile_main_post_receipts.side_effect = lambda *args, **kwargs: {key: Outcome(key) for key in ("regular", "meme")}
    result = bot.reconcile_confirmed_transactions_before_global_barrier(lines, images, state, current)
    expected = _inspection_calls(paths) + [
        call.journal_path_for_receipt(paths[1]), call.inspect_transport_state(journal),
        call.load_meme_post_receipt(), call.load_regular_post_receipt(), call.load_meme_post_receipt(),
    ]
    eligible = any(s in {"pending_schedule", "valid"} for s in statuses)
    if eligible:
        expected += ([call.now_epoch()] if current is None else []) + [
            call.reconcile_main_post_receipts(lines, images, state,
                minimum_next_quote_epoch=epoch if current is None else current, process_auxiliary_context=False),
            call.coerce("regular"), call.coerce("meme"),
        ]
        assert all(a is b for a, b in zip(trace.reconcile_main_post_receipts.call_args.args, (lines, images, state)))
    assert trace.mock_calls == expected
    assert result == {"historical_context": False, "conversational_reply": False, "regular": eligible, "meme": False}


@pytest.mark.parametrize("status,final_status", [("sending", "valid"), ("legacy_sending", "valid"), ("valid", "invalid")])
def test_conversational_promotion_selects_current_validator_then_reloads_receipt(monkeypatch, status, final_status):
    trace, paths, journal = _prebarrier(monkeypatch, (bot.CONFIRMED_REPLY_RECEIPT_FILE,), "confirmed_pair")
    source, state, result, epoch = {}, {}, object(), object()
    details = SimpleNamespace(lane="conversational_reply", post_id=object(), confirmation_epoch=object())
    trace.load_confirmed_reply_receipt.side_effect = [(status, object()), (final_status, object())]
    trace.bind_confirmed_transport_source.side_effect = lambda **kwargs: SimpleNamespace(source_binding=SimpleNamespace(receipt_document=source), details=details)
    trace._reply_confirmation_epoch_after_remote_success.side_effect = lambda *args: epoch
    promote = ("_promote_legacy_sending_reply_receipt_from_confirmed_transport"
               if status == "legacy_sending" else "promote_sending_reply_receipt")
    getattr(trace, promote).side_effect = lambda *args, **kwargs: None
    trace.reconcile_confirmed_reply_receipt.side_effect = lambda actual: result
    actual = bot.reconcile_confirmed_transactions_before_global_barrier(set(), set(), state)
    assert actual["conversational_reply"] is (result if final_status == "valid" else False)
    expected = _inspection_calls(paths) + [call.journal_path_for_receipt(paths[2]), call.inspect_transport_state(journal), call.load_confirmed_reply_receipt()]
    if status != "valid":
        expected += [
            call.bind_confirmed_transport_source(journal_path=journal, receipt_path=paths[2],
                validator_id=bot.TRANSPORT_SOURCE_VALIDATOR_ID,
                validator=bot._legacy_conversational_transport_source_semantic_validator if status == "legacy_sending" else bot.transport_source_semantic_validator),
            call._reply_confirmation_epoch_after_remote_success(source, details.confirmation_epoch),
            getattr(call, promote)(source, reply_post_id=details.post_id, confirmation_epoch=epoch),
        ]
        assert getattr(trace, promote).call_args.args[0] is source
    expected += [call.load_confirmed_reply_receipt()]
    if final_status == "valid":
        expected += [call.reconcile_confirmed_reply_receipt(state)]
        assert trace.reconcile_confirmed_reply_receipt.call_args.args[0] is state
    assert trace.mock_calls == expected


@pytest.mark.parametrize("source_bound", [False, True])
def test_historical_reconciliation_keeps_current_formatter_and_original_preloaded_tuple(monkeypatch, source_bound):
    import historical_context_formatter as formatter

    trace, paths, journal = _prebarrier(monkeypatch, (bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,))
    receipt = {"lifecycle_state": "confirmed", "parent_post_id": 71, "quote_id": "quote", "reply_post_id": "reply"}
    if source_bound:
        receipt["source_receipt_sha256"] = "source-sha"
    source, source_bytes = {"parent_post_id": 71, "quote_id": "quote"}, b"original source"
    loaded = (receipt, b"original confirmed bytes")
    context = {"quote_id": "quote", "state": "context_reply_confirmed"}
    obligation = {"context_reply": context}
    trace.valid_sending.return_value, trace.valid.return_value = False, True
    trace.source.return_value, trace.source_bytes.return_value = source, source_bytes
    monkeypatch.setattr(formatter, "HistoricalContextReplyStore", SimpleNamespace(
        _valid_sending_receipt=trace.valid_sending, _valid_receipt=trace.valid,
        sending_receipt_from_confirmed=trace.source, source_receipt_bytes_from_confirmed=trace.source_bytes,
    ))
    initial = SimpleNamespace(_load_receipt_safely=trace.load)
    final = SimpleNamespace(reconcile_receipt_disposition=trace.disposition)
    outbox = SimpleNamespace(get=trace.get)
    trace.load.return_value, trace.get.return_value = loaded, obligation
    trace.disposition.return_value = "confirmed"
    trace.historical_context_reply_store.side_effect = [initial, final]
    trace.historical_context_outbox_store.side_effect = lambda: outbox
    trace.confirmed_context_outbox_matches_receipt.side_effect = lambda *args: True
    trace.emit_historical_context_store_observation.side_effect = lambda *args: None
    result = bot.reconcile_confirmed_transactions_before_global_barrier(set(), set(), {})
    assert result == {"historical_context": True, "conversational_reply": False, "regular": False, "meme": False}
    assert trace.disposition.call_args.kwargs["preloaded_receipt"] is loaded
    assert trace.confirmed_context_outbox_matches_receipt.call_args.args[0] is context
    assert trace.confirmed_context_outbox_matches_receipt.call_args.args[1] is receipt
    assert trace.emit_historical_context_store_observation.call_args.args[0] is final
    assert trace.mock_calls == _inspection_calls(paths) + [
        call.journal_path_for_receipt(paths[3]), call.inspect_transport_state(journal),
        call.historical_context_reply_store(), call.load(), call.valid_sending(receipt), call.valid(receipt),
        *([call.source(receipt), call.source_bytes(receipt)] if source_bound else [call.valid(receipt)]),
        call.historical_context_outbox_store(), call.get("71"),
        call.confirmed_context_outbox_matches_receipt(context, receipt),
        call.historical_context_reply_store(), call.disposition(preloaded_receipt=loaded),
        call.emit_historical_context_store_observation(final, "71"),
    ]


@pytest.mark.parametrize("case", ["recover", "changed", "failure"])
def test_historical_sending_rechecks_obligation_under_lock_before_local_recovery(monkeypatch, case):
    import historical_context_formatter as formatter

    trace, paths, journal = _prebarrier(monkeypatch, (bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,))
    receipt = {"parent_post_id": 71, "quote_id": "quote"}
    loaded = (receipt, b"original sending bytes")
    obligation = {"context_reply": {"quote_id": "quote", "state": "context_reply_attempting"}}
    current_obligation = {"context_reply": obligation["context_reply"]}
    assert current_obligation == obligation and current_obligation is not obligation
    if case == "changed":
        current_obligation = {}
    epoch, recovered = object(), {"original": []}
    failure = ValueError("worker persistence failed")

    @contextmanager
    def locked():
        trace.enter()
        try:
            yield
        finally:
            trace.exit()

    trace.worker_lock.side_effect = locked
    outbox = SimpleNamespace(get=trace.get, worker_lock=trace.worker_lock)
    historical = SimpleNamespace(_load_receipt_safely=trace.load)
    trace.load.return_value = loaded
    trace.valid_sending.return_value = True
    monkeypatch.setattr(formatter, "HistoricalContextReplyStore", SimpleNamespace(_valid_sending_receipt=trace.valid_sending))
    trace.historical_context_reply_store.side_effect = lambda: historical
    trace.historical_context_outbox_store.side_effect = lambda: outbox
    trace.get.side_effect = [obligation, current_obligation]
    trace.now_epoch.side_effect = lambda: epoch
    trace.recover_interrupted_historical_context_attempt.side_effect = failure if case == "failure" else lambda *a, **k: recovered
    trace.log_event.side_effect = None
    if case == "recover":
        assert bot.reconcile_confirmed_transactions_before_global_barrier(set(), set(), {})["historical_context"] is True
    else:
        with pytest.raises(ValueError if case == "failure" else RuntimeError) as caught:
            bot.reconcile_confirmed_transactions_before_global_barrier(set(), set(), {})
        if case == "failure":
            assert caught.value is failure
        else:
            assert str(caught.value) == "historical-context outbox changed before local recovery"
    expected = _inspection_calls(paths) + [
        call.journal_path_for_receipt(paths[3]), call.inspect_transport_state(journal),
        call.historical_context_reply_store(), call.load(), call.valid_sending(receipt),
        call.historical_context_outbox_store(), call.get("71"), call.worker_lock(), call.enter(), call.get("71"),
    ]
    if case != "changed":
        expected += [call.now_epoch(), call.recover_interrupted_historical_context_attempt(
            outbox, current_obligation, recovered_epoch=epoch, receipt_was_observed=True)]
        args = trace.recover_interrupted_historical_context_attempt.call_args
        assert args.args[0] is outbox and args.args[1] is current_obligation
        assert args.kwargs["recovered_epoch"] is epoch
    expected += [call.exit()]
    if case == "recover":
        expected += [call.log_event("historical_context_obligation", **recovered)]
        assert trace.log_event.call_args.kwargs["original"] is recovered["original"]
    assert trace.mock_calls == expected


@pytest.mark.parametrize("truthy", [False, True])
@pytest.mark.parametrize("failure_stage", [None, "bind_confirmed_transport_source", "promote", "record_confirmed"])
def test_historical_promotion_preserves_bytes_stores_outbox_order_and_raw_result(monkeypatch, truthy, failure_stage):
    import historical_context_formatter as formatter

    trace, paths, journal = _prebarrier(monkeypatch, (bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,), "confirmed_pair")
    receipt = {"parent_post_id": 71, "quote_id": "quote", "attempt_number": 21}
    receipt_bytes = b"original sending receipt"
    bound_source = {"parent_post_id": 71}
    real_sha = bot.hashlib.sha256
    digest = real_sha(receipt_bytes).hexdigest()
    details = SimpleNamespace(lane="historical_context_reply", source_receipt_sha256=digest,
                              post_id=object(), confirmation_epoch=object())
    context = {"quote_id": "quote", "state": "context_reply_attempting", "attempt_count": "1",
               "remote_transaction_started": True, "source_receipt_sha256": digest,
               "source_receipt_attempt_number": 21}
    trace.valid_sending.return_value = True
    monkeypatch.setattr(formatter, "HistoricalContextReplyStore", SimpleNamespace(_valid_sending_receipt=trace.valid_sending))
    initial = SimpleNamespace(_load_receipt_safely=trace.load)
    promotion = SimpleNamespace(promote_sending_receipt_from_confirmed_transport=trace.promote)
    final = SimpleNamespace(reconcile_confirmed_receipt_if_present=trace.reconcile)
    outbox = SimpleNamespace(get=trace.get, record_confirmed=trace.record_confirmed)
    trace.load.return_value = (receipt, receipt_bytes)
    trace.get.return_value = {"context_reply": context}
    trace.historical_context_reply_store.side_effect = [initial, promotion, final]
    trace.historical_context_outbox_store.side_effect = lambda: outbox
    trace.inspect_confirmed_transport_transaction.side_effect = lambda actual: details
    trace.sha.side_effect = real_sha
    monkeypatch.setattr(bot, "hashlib", SimpleNamespace(sha256=trace.sha))
    trace.bind_confirmed_transport_source.side_effect = lambda **kwargs: SimpleNamespace(
        source_binding=SimpleNamespace(receipt_document=bound_source), details=details)

    class Outcome:
        def __bool__(self):
            trace.truth()
            return truthy

    outcome = Outcome()
    trace.reconcile.return_value = outcome
    trace.emit_historical_context_store_observation.side_effect = lambda *args: None
    if failure_stage is None:
        actual = bot.reconcile_confirmed_transactions_before_global_barrier(set(), set(), {})
        assert actual["historical_context"] is outcome
    else:
        failure = OSError("historical confirmation persistence failed")
        getattr(trace, failure_stage).side_effect = failure
        with pytest.raises(OSError) as caught:
            bot.reconcile_confirmed_transactions_before_global_barrier(set(), set(), {})
        assert caught.value is failure
    if failure_stage != "bind_confirmed_transport_source":
        assert trace.promote.call_args.args[0] is bound_source
    assert all(entry.args[0] is receipt_bytes for entry in trace.sha.call_args_list)
    expected = _inspection_calls(paths) + [
        call.journal_path_for_receipt(paths[3]), call.inspect_transport_state(journal),
        call.historical_context_reply_store(), call.load(), call.valid_sending(receipt),
        call.historical_context_outbox_store(), call.get("71"),
        call.inspect_confirmed_transport_transaction(journal), call.sha(receipt_bytes), call.sha(receipt_bytes),
        call.bind_confirmed_transport_source(journal_path=journal, receipt_path=paths[3],
            validator_id=bot.TRANSPORT_SOURCE_VALIDATOR_ID, validator=bot.transport_source_semantic_validator),
        call.historical_context_reply_store(), call.promote(bound_source, reply_post_id=details.post_id,
            confirmation_epoch=details.confirmation_epoch, require_confirmed_transport=True),
        call.record_confirmed("71", attempt_number=1, reply_post_id=details.post_id, confirmed_epoch=details.confirmation_epoch),
        call.historical_context_reply_store(), call.reconcile(), call.truth(),
        *([call.emit_historical_context_store_observation(final, "71")] if truthy else []),
    ]
    if failure_stage is not None:
        failed_call = next(i for i, entry in enumerate(expected) if entry[0] == failure_stage)
        expected = expected[:failed_call + 1]
    assert trace.mock_calls == expected
    if truthy and failure_stage is None:
        assert trace.emit_historical_context_store_observation.call_args.args[0] is final


@pytest.mark.parametrize("lane,message", [
    ("historical_context_reply", "confirmed historical-context transport has no exact outbox authority"),
    ("unknown", "confirmed journal has no supported recovery lane"),
])
def test_bound_transport_dispatch_requires_supported_lane_and_historical_authority(monkeypatch, lane, message):
    trace, paths, journal = _prebarrier(monkeypatch, (bot.REGULAR_POST_RECEIPT_FILE,), "confirmed_pair")
    trace.load_regular_post_receipt.side_effect = [("sending", {"lifecycle_state": "attempting"})]
    trace.bind_confirmed_transport_source.side_effect = lambda **kwargs: SimpleNamespace(
        source_binding=SimpleNamespace(receipt_document={}),
        details=SimpleNamespace(lane=lane),
    )
    with pytest.raises(bot.TransportJournalError, match=message):
        bot.reconcile_confirmed_transactions_before_global_barrier(set(), set(), {})
    assert trace.mock_calls == _inspection_calls(paths) + [
        call.journal_path_for_receipt(paths[0]), call.inspect_transport_state(journal),
        call.load_regular_post_receipt(),
        call.bind_confirmed_transport_source(
            journal_path=journal, receipt_path=paths[0],
            validator_id=bot.TRANSPORT_SOURCE_VALIDATOR_ID,
            validator=bot.transport_source_semantic_validator,
        ),
    ]
