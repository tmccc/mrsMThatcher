"""Contracts for historical-context startup extraction and preflight ordering."""
from __future__ import annotations

import builtins
from contextlib import contextmanager
import inspect
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import historical_context_formatter as formatter
import mrsMThatcher2 as bot
import mrs_bot_historical_context_runtime as owner
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401
from tests.helpers.historical_context_fixtures import isolated_incident_paths  # noqa: F401

DEPENDENCIES = {'reconcile_runtime_historical_context_state': ['HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE',
                                                '_set_historical_context_outbox_unavailable_reason',
                                                'confirmed_context_outbox_matches_receipt',
                                                'global_remote_writes_paused',
                                                'historical_context_outbox_store',
                                                'historical_context_reply_store',
                                                'inspect_transport_state',
                                                'journal_path_for_receipt',
                                                'log',
                                                'log_event',
                                                'now_epoch',
                                                'receipt_namespace_entry_exists',
                                                'recover_interrupted_historical_context_attempt',
                                                'resume_source_receipt_retirement_for_control_snapshot'],
 'require_historical_context_outbox_writable': ['_HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON',
                                                '_get_historical_context_outbox_unavailable_reason',
                                                '_set_historical_context_outbox_unavailable_reason',
                                                'historical_context_outbox_store',
                                                'historical_context_reply']}


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, datetime, io, logging, os, random, socket, sys, time, typing, zoneinfo
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('Historical-context-runtime import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'historical_context_formatter', 'historical_context_outbox', 'transaction_mutation_authority', 'remote_write_transport_journal', 'remote_media_upload_receipt'} or name.startswith('mrs_bot_') and name != 'mrs_bot_historical_context_runtime':
        forbidden()
    return original_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
builtins.open = io.open = os.open = os.lstat = os.stat = forbidden
os.getenv = os._Environ.__getitem__ = os.urandom = forbidden
Path.home = logging.getLogger = forbidden
socket.socket = socket.create_connection = socket.getaddrinfo = forbidden
time.time = time.monotonic = forbidden
datetime.datetime = type("ForbiddenDatetime", (), dict(now=forbidden, fromtimestamp=forbidden, today=forbidden))
zoneinfo.ZoneInfo = forbidden
before = random.getstate()
random.Random = random.seed = random.random = forbidden
import mrs_bot_historical_context_runtime
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
assert 'requests' not in sys.modules
assert 'single_call_reply' not in sys.modules
assert 'transaction_mutation_authority' not in sys.modules
assert mrs_bot_historical_context_runtime.reconcile_runtime_historical_context_state.__annotations__['return'] == 'None'
assert mrs_bot_historical_context_runtime.require_historical_context_outbox_writable.__annotations__['return'] == 'None'
assert 'historical_context_formatter' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout


@pytest.mark.parametrize("name", DEPENDENCIES)
def test_adapters_keep_signatures_current_dependencies_references_and_errors(monkeypatch, name):
    adapter = getattr(bot, name)
    implementation = getattr(owner, name)
    assert str(inspect.signature(adapter)) == "() -> 'None'"
    signature = inspect.signature(implementation)
    assert list(signature.parameters) == DEPENDENCIES[name]
    assert all(p.kind is inspect.Parameter.KEYWORD_ONLY
               and p.default is inspect.Parameter.empty
               for p in signature.parameters.values())
    assert signature.return_annotation == "None"
    assert implementation.__doc__ == adapter.__doc__
    for _ in range(2):
        with monkeypatch.context() as patch:
            current = {dep: object() for dep in DEPENDENCIES[name]}
            for dep, value in current.items():
                patch.setattr(bot, dep, value)
            result = {"original": []}

            def capture(*args, **kwargs):
                assert args == ()
                assert kwargs.keys() == current.keys()
                assert all(kwargs[key] is value for key, value in current.items())
                return result

            patch.setattr(bot, "_historical_context_runtime", SimpleNamespace(**{name: capture}))
            assert adapter() is result
            with pytest.raises(TypeError, match="not_a_public_option"):
                adapter(not_a_public_option={})
            failure = TypeError("current owner failure")
            patch.setattr(bot, "_historical_context_runtime", SimpleNamespace(**{name: Mock(side_effect=failure)}))
            with pytest.raises(TypeError) as caught:
                adapter()
            assert caught.value is failure
    assert not hasattr(owner, "_HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON")


@pytest.mark.parametrize("enabled,unavailable", [(False, None), (False, "reason"), (True, "reason")])
def test_preflight_short_circuits_before_shared_latch_and_store(monkeypatch, enabled, unavailable):
    config = Mock()
    config.get.return_value = enabled
    getter = Mock(side_effect=AssertionError("unexpected latch read"))
    factory = Mock(side_effect=AssertionError("unexpected store acquisition"))
    reason = object()
    monkeypatch.setattr(bot, "historical_context_reply", config)
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON", unavailable)
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON", reason)
    monkeypatch.setattr(bot, "_get_historical_context_outbox_unavailable_reason", getter)
    monkeypatch.setattr(bot, "historical_context_outbox_store", factory)
    assert bot.require_historical_context_outbox_writable() is None
    config.get.assert_called_once_with("enabled")
    getter.assert_not_called()
    factory.assert_not_called()
    assert bot._HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON is reason


def test_preflight_observes_both_current_shared_latch_reads(monkeypatch):
    class ReplacedOnObservation:
        def __bool__(self):
            bot._set_historical_context_outbox_unavailable_reason("current replacement")
            return True

        def __str__(self):
            pytest.fail("preflight retained the first latch observation")

    monkeypatch.setattr(bot, "historical_context_reply", {"enabled": True})
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON", ReplacedOnObservation())
    factory = Mock(side_effect=AssertionError("latched preflight acquired a store"))
    monkeypatch.setattr(bot, "historical_context_outbox_store", factory)
    with pytest.raises(RuntimeError) as caught:
        bot.require_historical_context_outbox_writable()
    assert str(caught.value) == "historical-context outbox is unavailable: current replacement"
    assert caught.value.__cause__ is None
    assert bot._get_historical_context_outbox_unavailable_reason() == "current replacement"
    factory.assert_not_called()


def test_preflight_success_keeps_implicit_none_and_does_not_clear_shared_state(monkeypatch):
    reason = []
    factory = Mock()
    monkeypatch.setattr(bot, "historical_context_reply", {"enabled": True})
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON", reason)
    monkeypatch.setattr(bot, "historical_context_outbox_store", factory)
    assert bot.require_historical_context_outbox_writable() is None
    factory.assert_called_once_with()
    factory.return_value.verify_writable.assert_called_once_with()
    assert bot._get_historical_context_outbox_unavailable_reason() is reason


@pytest.mark.parametrize("failure_at", ["factory", "verify"])
@pytest.mark.parametrize("exception_type", [OSError, KeyboardInterrupt])
def test_preflight_preserves_store_try_scope_latch_before_raise_and_cause(monkeypatch, failure_at, exception_type):
    failure = exception_type("durability unavailable")
    factory = Mock()
    target = factory if failure_at == "factory" else factory.return_value.verify_writable
    target.side_effect = failure
    monkeypatch.setattr(bot, "historical_context_reply", {"enabled": True})
    monkeypatch.setattr(bot, "historical_context_outbox_store", factory)

    class ObservedRuntimeError(RuntimeError):
        def __init__(self, message):
            assert bot._get_historical_context_outbox_unavailable_reason() == "OSError: durability unavailable"
            super().__init__(message)

    monkeypatch.setattr(owner, "RuntimeError", ObservedRuntimeError, raising=False)
    expected = ObservedRuntimeError if exception_type is OSError else KeyboardInterrupt
    with pytest.raises(expected) as caught:
        bot.require_historical_context_outbox_writable()
    if exception_type is OSError:
        assert str(caught.value) == "historical-context outbox failed the pre-post durability check"
        assert caught.value.__cause__ is failure
    else:
        assert caught.value is failure
        assert bot._get_historical_context_outbox_unavailable_reason() is None
    factory.assert_called_once_with()
    if failure_at == "factory":
        factory.return_value.verify_writable.assert_not_called()
    else:
        factory.return_value.verify_writable.assert_called_once_with()


@pytest.fixture
def reconciliation_runtime(monkeypatch):
    trace = Mock()
    trace.pause.return_value = False
    trace.namespace.return_value = False
    trace.context_factory.return_value = trace.context
    trace.outbox_factory.return_value = trace.outbox
    trace.context._load_receipt_safely.return_value = None
    trace.valid.return_value = False
    trace.sending.return_value = False
    trace.inspect.return_value = SimpleNamespace(classification="absent")
    trace.matches.return_value = True
    trace.set_reason.side_effect = bot._set_historical_context_outbox_unavailable_reason
    for name, callback in {
        "global_remote_writes_paused": trace.pause,
        "resume_source_receipt_retirement_for_control_snapshot": trace.resume,
        "historical_context_reply_store": trace.context_factory,
        "historical_context_outbox_store": trace.outbox_factory,
        "receipt_namespace_entry_exists": trace.namespace,
        "journal_path_for_receipt": trace.journal_path,
        "inspect_transport_state": trace.inspect,
        "confirmed_context_outbox_matches_receipt": trace.matches,
        "recover_interrupted_historical_context_attempt": trace.recover,
        "now_epoch": trace.clock,
        "log": trace.log,
        "log_event": trace.event,
        "_set_historical_context_outbox_unavailable_reason": trace.set_reason,
    }.items():
        monkeypatch.setattr(bot, name, callback)
    monkeypatch.setattr(formatter.HistoricalContextReplyStore, "_valid_receipt", trace.valid)
    monkeypatch.setattr(formatter.HistoricalContextReplyStore, "_valid_sending_receipt", trace.sending)
    return trace


@pytest.mark.parametrize("paused,present", [(False, False), (True, False), (True, True)])
def test_startup_keeps_local_import_pause_retirement_store_and_receipt_order(monkeypatch, reconciliation_runtime, paused, present):
    trace = reconciliation_runtime
    trace.pause.return_value = paused
    trace.namespace.return_value = present
    original_import = builtins.__import__

    def observe_import(name, *args, **kwargs):
        if name == "historical_context_formatter":
            trace.formatter_import()
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", observe_import)
    assert bot.reconcile_runtime_historical_context_state() is None
    expected = [
        call.formatter_import(), call.pause(),
        call.resume(maintenance_paused=paused), call.context_factory(),
        call.outbox_factory(), call.outbox.snapshot(),
    ]
    if paused:
        expected.append(call.namespace(bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE))
        if present:
            expected.append(call.log.warning(
                "Global maintenance pause is active; leaving the historical-"
                "context transaction untouched until an unpaused loop tick"
            ))
    else:
        expected += [call.context._load_receipt_safely(), call.valid(None),
                     call.sending(None), call.sending(None)]
    if not present:
        expected.append(call.context.reconcile_receipt())
    assert trace.mock_calls == expected


@pytest.mark.parametrize("failure_at", ["resume", "context_factory", "outbox_factory"])
def test_retirement_and_store_errors_escape_snapshot_catch(reconciliation_runtime, failure_at):
    trace = reconciliation_runtime
    failure = OSError("before snapshot")
    getattr(trace, failure_at).side_effect = failure
    with pytest.raises(OSError) as caught:
        bot.reconcile_runtime_historical_context_state()
    assert caught.value is failure
    assert trace.mock_calls[-1][0] == failure_at
    trace.outbox.snapshot.assert_not_called()
    trace.set_reason.assert_not_called()
    trace.log.critical.assert_not_called()
    trace.event.assert_not_called()
    trace.context._load_receipt_safely.assert_not_called()


@pytest.mark.parametrize("exception_type", [OSError, KeyboardInterrupt])
def test_snapshot_exception_scope_and_shared_state_before_diagnostics(reconciliation_runtime, exception_type):
    trace = reconciliation_runtime
    failure = exception_type("unavailable " + "x" * 600)
    trace.outbox.snapshot.side_effect = failure

    def observe_reason(*args, **kwargs):
        assert bot._get_historical_context_outbox_unavailable_reason() == f"OSError: {failure}"

    trace.log.critical.side_effect = observe_reason
    trace.event.side_effect = observe_reason
    if exception_type is KeyboardInterrupt:
        with pytest.raises(KeyboardInterrupt) as caught:
            bot.reconcile_runtime_historical_context_state()
        assert caught.value is failure
        assert trace.mock_calls[-1] == call.outbox.snapshot()
        trace.set_reason.assert_not_called()
        return
    assert bot.reconcile_runtime_historical_context_state() is None
    assert trace.mock_calls[5:8] == [
        call.set_reason(f"OSError: {failure}"),
        call.log.critical(
            "Historical-context outbox is invalid or unavailable; the quote "
            "lane will fail its pre-post check while unrelated lanes continue",
            exc_info=True,
        ),
        call.event("historical_context_outbox", status="unavailable",
                   error_type="OSError", reason=str(failure)[:500],
                   unrelated_lanes_available=True),
    ]
    assert trace.mock_calls[-1] == call.context.reconcile_receipt()


@pytest.mark.parametrize("preloaded", [False, True])
@pytest.mark.parametrize("exception_type", [None, OSError, KeyboardInterrupt])
def test_final_local_reconciliation_keeps_tuple_identity_and_exception_scope(reconciliation_runtime, preloaded, exception_type):
    trace = reconciliation_runtime
    loaded = ({"parent_post_id": "parent", "quote_id": "quote"}, object())
    if preloaded:
        trace.context._load_receipt_safely.return_value = loaded
        trace.valid.return_value = True
        trace.outbox.get.return_value = {
            "context_reply": {"quote_id": "quote", "state": "context_reply_confirmed"}
        }
    callback = (trace.context.reconcile_receipt_disposition if preloaded
                else trace.context.reconcile_receipt)
    failure = exception_type("local reconciliation") if exception_type else None
    callback.side_effect = failure
    if exception_type:
        with pytest.raises(exception_type) as caught:
            bot.reconcile_runtime_historical_context_state()
        assert caught.value is failure
    else:
        assert bot.reconcile_runtime_historical_context_state() is None
    if preloaded:
        callback.assert_called_once_with(preloaded_receipt=loaded)
        assert callback.call_args.kwargs["preloaded_receipt"] is loaded
        assert trace.valid.call_args.args[0] is loaded[0]
        assert all(c.args[0] is loaded[0] for c in trace.sending.call_args_list)
        assert trace.matches.call_args.args[1] is loaded[0]
        trace.context.reconcile_receipt.assert_not_called()
    else:
        callback.assert_called_once_with()
        trace.context.reconcile_receipt_disposition.assert_not_called()
    if exception_type is OSError:
        trace.log.critical.assert_called_once_with(
            "Historical-context durable receipt could not be reconciled; "
            "refusing production startup to preserve the ambiguity barrier",
            exc_info=True,
        )
    else:
        trace.log.critical.assert_not_called()
    trace.journal_path.assert_not_called()
    trace.inspect.assert_not_called()
    trace.clock.assert_not_called()
    trace.set_reason.assert_not_called()


@pytest.mark.parametrize("failure_at", [None, "missing_obligation", "recover"])
def test_deferred_recovery_keeps_lock_validation_lazy_clock_and_event_expansion(reconciliation_runtime, failure_at):
    trace = reconciliation_runtime
    receipt = {"parent_post_id": "parent", "quote_id": "quote"}
    obligation = {"context_reply": {"quote_id": "quote", "state": "context_reply_attempting"}}
    trace.context._load_receipt_safely.return_value = (receipt, object())
    trace.sending.return_value = True
    trace.outbox.get.side_effect = [obligation, None if failure_at == "missing_obligation" else obligation]
    epoch, status = object(), object()
    trace.clock.return_value = epoch

    @contextmanager
    def worker_lock():
        trace.lock_enter()
        try:
            yield
        finally:
            trace.lock_exit()

    class Recovered:
        def keys(self):
            assert trace.mock_calls[-1] == call.lock_exit()
            trace.expand_event()
            return ["status"]

        def __getitem__(self, key):
            assert key == "status"
            return status

    trace.outbox.worker_lock.side_effect = worker_lock
    trace.recover.return_value = Recovered()
    failure = OSError("recovery unavailable")
    if failure_at == "recover":
        trace.recover.side_effect = failure
    if failure_at:
        expected = RuntimeError if failure_at == "missing_obligation" else OSError
        with pytest.raises(expected) as caught:
            bot.reconcile_runtime_historical_context_state()
        if failure_at == "recover":
            assert caught.value is failure
        else:
            assert str(caught.value) == "historical-context attempting outbox record disappeared"
    else:
        assert bot.reconcile_runtime_historical_context_state() is None
    start = trace.mock_calls.index(call.outbox.worker_lock())
    expected_calls = [call.outbox.worker_lock(), call.lock_enter(), call.outbox.get("parent")]
    if failure_at == "missing_obligation":
        trace.clock.assert_not_called()
        trace.recover.assert_not_called()
    else:
        expected_calls += [call.clock(), call.recover(
            trace.outbox, obligation, recovered_epoch=epoch, receipt_was_observed=True
        )]
        assert trace.recover.call_args.args[0] is trace.outbox
        assert trace.recover.call_args.args[1] is obligation
        assert trace.recover.call_args.kwargs["recovered_epoch"] is epoch
    expected_calls.append(call.lock_exit())
    if failure_at is None:
        expected_calls += [call.expand_event(), call.event("historical_context_obligation", status=status)]
        assert trace.event.call_args.kwargs["status"] is status
    else:
        trace.event.assert_not_called()
    assert trace.mock_calls[start:] == expected_calls
    trace.context.reconcile_receipt.assert_not_called()
    trace.context.reconcile_receipt_disposition.assert_not_called()
    trace.log.critical.assert_not_called()
    trace.set_reason.assert_not_called()
