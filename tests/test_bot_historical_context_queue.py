"""Contracts for queue extraction, shared state and native callback order."""
from __future__ import annotations

import builtins
import inspect
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrsMThatcher2 as bot
import mrs_bot_historical_context_queue as owner
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401
from tests.helpers.historical_context_fixtures import isolated_incident_paths  # noqa: F401

DEPENDENCIES = {'enqueue_historical_context_obligation': ['_HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON',
                                           '_set_historical_context_outbox_unavailable_reason',
                                           'canonical_context_obligation_quote_id',
                                           'historical_context_outbox_store',
                                           'historical_context_reply',
                                           'log',
                                           'log_event'],
 '_process_due_historical_context_obligations': ['ApiError',
                                                 'HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE',
                                                 '_HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON',
                                                 '_get_historical_context_outbox_unavailable_reason',
                                                 '_set_historical_context_outbox_unavailable_reason',
                                                 'api_error_is_reply_not_allowed',
                                                 'historical_context_receipt_path_present_or_unsafe',
                                                 'in_api_cooldown',
                                                 'inspect_transport_state',
                                                 'journal_path_for_receipt',
                                                 'lane_paused',
                                                 'log',
                                                 'log_event',
                                                 'maybe_post_historical_context_reply',
                                                 'now_epoch',
                                                 'record_ambiguous_remote_post',
                                                 'record_api_error',
                                                 'recover_interrupted_historical_context_attempt',
                                                 'save_state'],
 'safely_process_due_historical_context_obligations': ['_set_historical_context_outbox_unavailable_reason',
                                                       'log',
                                                       'log_event',
                                                       'process_due_historical_context_obligations']}

SIGNATURES = {'enqueue_historical_context_obligation': "(receipt: 'dict') -> 'dict'",
 '_process_due_historical_context_obligations': '(*, store, parent_post_id: '
                                                "'str | None' = None, limit: "
                                                "'int' = 1, runtime_state: "
                                                "'dict | None' = None, "
                                                'historical_context_receipt_reconciliation_only: '
                                                "'bool' = False, "
                                                'historical_context_outbox_reconciliation_only: '
                                                "'bool' = False) -> "
                                                "'list[dict]'",
 'safely_process_due_historical_context_obligations': '(*, parent_post_id: '
                                                      "'str | None' = None, "
                                                      "limit: 'int' = 1, "
                                                      "runtime_state: 'dict | "
                                                      "None' = None) -> "
                                                      "'list[dict]'"}


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, re, json, datetime, io, logging, os, random, socket, sys, time, typing, zoneinfo
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('Historical-context-queue import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'historical_context_formatter', 'historical_context_outbox', 'transaction_mutation_authority', 'remote_write_transport_journal', 'remote_media_upload_receipt'} or name.startswith('mrs_bot_') and name not in {'mrs_bot_historical_context_queue', 'mrs_bot_historical_context_delivery', 'mrs_bot_receipt_retirement', 'mrs_bot_durable_json_io'}:
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
import mrs_bot_historical_context_queue
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
assert 'requests' not in sys.modules
assert 'single_call_reply' not in sys.modules
assert 'transaction_mutation_authority' not in sys.modules
assert mrs_bot_historical_context_queue.enqueue_historical_context_obligation.__annotations__['return'] == 'dict'
assert mrs_bot_historical_context_queue._process_due_historical_context_obligations.__annotations__['runtime_state'] == 'dict | None'
assert 'historical_context_formatter' not in sys.modules
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

            patch.setattr(bot, "_historical_context_queue", SimpleNamespace(**{name: capture}))
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
            patch.setattr(bot, "_historical_context_queue", SimpleNamespace(**{name: Mock(side_effect=failure)}))
            with pytest.raises(TypeError) as caught:
                adapter(**provided)
            assert caught.value is failure


@pytest.fixture
def quiet_runtime(monkeypatch):
    runtime = SimpleNamespace(
        log=Mock(), event=Mock(), clock=Mock(return_value=100),
        paused=Mock(return_value=False), cooldown=Mock(return_value=False),
    )
    for name, value in {
        "log": runtime.log, "log_event": runtime.event,
        "now_epoch": runtime.clock, "lane_paused": runtime.paused,
        "in_api_cooldown": runtime.cooldown,
        "historical_context_receipt_path_present_or_unsafe": Mock(return_value=False),
    }.items():
        monkeypatch.setattr(bot, name, value)
    return runtime


def test_shared_accessors_preserve_live_root_identity_without_coercion(monkeypatch):
    class Uncoercible:
        def __str__(self):
            raise AssertionError("The shared reason must not be coerced")

    for reason in (Uncoercible(), "", None):
        monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON", reason)
        assert bot._get_historical_context_outbox_unavailable_reason() is reason
        replacement = Uncoercible()
        assert bot._set_historical_context_outbox_unavailable_reason(replacement) is None
        assert bot._HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON is replacement
        assert bot._get_historical_context_outbox_unavailable_reason() is replacement
    assert not hasattr(owner, "_HISTORICAL_CONTEXT_OUTBOX_UNAVAILABLE_REASON")


@pytest.mark.parametrize("boundary", ["ok", "factory", "parent", "epoch"])
def test_enqueue_converts_before_policy_and_preserves_native_errors(monkeypatch, quiet_runtime, boundary):
    events = []
    failure = TypeError("native pre-gate failure")
    obligation = {"main_post": {"state": "main"}, "context_reply": {"state": "context"}}

    def observe(name):
        events.append(name)
        if name == boundary:
            raise failure

    class Parent:
        def __str__(self):
            observe("parent")
            return "123"

    class Epoch:
        def __int__(self):
            observe("epoch")
            return 81

    class Policy:
        def get(self, key):
            assert key == "enabled"
            observe("policy")
            return False

    def enqueue(parent, **kwargs):
        observe("enqueue")
        assert parent == "123"
        assert kwargs == {
            "main_post_confirmed_epoch": 81,
            "not_required_reason": "historical_context_reply_disabled",
        }
        return obligation

    def factory():
        observe("factory")
        return SimpleNamespace(enqueue=enqueue)

    monkeypatch.setattr(bot, "historical_context_outbox_store", factory)
    monkeypatch.setattr(bot, "historical_context_reply", Policy())
    quiet_runtime.event.side_effect = lambda *args, **kwargs: observe("event")
    receipt = {"post_id": Parent(), "quote_post_epoch": Epoch()}
    if boundary == "ok":
        assert bot.enqueue_historical_context_obligation(receipt) is obligation
        assert events == ["factory", "parent", "epoch", "policy", "enqueue", "event"]
    else:
        with pytest.raises(TypeError) as caught:
            bot.enqueue_historical_context_obligation(receipt)
        assert caught.value is failure
        assert events == ["factory", "parent", "epoch"][:["factory", "parent", "epoch"].index(boundary) + 1]
    assert bot._get_historical_context_outbox_unavailable_reason() is None


@pytest.mark.parametrize("unavailable,boundary", [
    (None, "ok"), ("runtime failed", "ok"), (None, "log"), (None, "event"),
])
def test_enqueue_fallback_sets_shared_reason_before_diagnostics(monkeypatch, quiet_runtime, unavailable, boundary):
    events = []
    failure = OSError("outbox unavailable")
    diagnostic_error = TypeError("native diagnostic error")
    reason = "historical_context_runtime_unavailable" if unavailable else "historical_context_reply_disabled"
    enqueue = Mock(side_effect=failure)
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON", unavailable)
    monkeypatch.setattr(bot, "historical_context_outbox_store", lambda: SimpleNamespace(enqueue=enqueue))
    setter = bot._set_historical_context_outbox_unavailable_reason

    def set_reason(value):
        events.append("set")
        assert value == "OSError: outbox unavailable"
        setter(value)

    def observe(name):
        def callback(*args, **kwargs):
            events.append(name)
            assert bot._get_historical_context_outbox_unavailable_reason() == "OSError: outbox unavailable"
            if name == boundary:
                raise diagnostic_error
        return callback

    monkeypatch.setattr(bot, "_set_historical_context_outbox_unavailable_reason", set_reason)
    quiet_runtime.log.error.side_effect = observe("log")
    quiet_runtime.event.side_effect = observe("event")
    receipt = {"post_id": 123, "quote_post_epoch": "81"}
    if boundary == "ok":
        assert bot.enqueue_historical_context_obligation(receipt) == {
            "parent_post_id": "123",
            "main_post": {"state": "main_post_confirmed", "confirmed_epoch": 81},
            "context_reply": {
                "state": "context_reply_not_required", "reason": reason, "updated_epoch": 81,
            },
        }
    else:
        with pytest.raises(TypeError) as caught:
            bot.enqueue_historical_context_obligation(receipt)
        assert caught.value is diagnostic_error
    assert events == (["set", "log"] if boundary == "log" else ["set", "log", "event"])
    enqueue.assert_called_once_with("123", main_post_confirmed_epoch=81, not_required_reason=reason)


def test_enqueue_keeps_canonical_callback_and_store_result_references(monkeypatch, quiet_runtime):
    quote_id = object()
    canonical = Mock(return_value=quote_id)
    obligation = {"main_post": {"state": object()}, "context_reply": {"state": object()}}
    enqueue = Mock(return_value=obligation)
    monkeypatch.setattr(bot, "historical_context_reply", {"enabled": True})
    monkeypatch.setattr(bot, "canonical_context_obligation_quote_id", canonical)
    monkeypatch.setattr(bot, "historical_context_outbox_store", lambda: SimpleNamespace(enqueue=enqueue))
    receipt = {"post_id": 123, "quote_post_epoch": "81", "quote_hash": "hash",
               "quote_text": "Canonical quotation", "text": "Engagement question"}
    assert bot.enqueue_historical_context_obligation(receipt) is obligation
    canonical.assert_called_once_with("hash", receipt["quote_text"])
    enqueue.assert_called_once_with("123", main_post_confirmed_epoch=81,
                                    quote_id=quote_id, quote_text=receipt["quote_text"])
    assert enqueue.call_args.kwargs["quote_id"] is quote_id
    assert quiet_runtime.event.call_args.kwargs["main_post_state"] is obligation["main_post"]["state"]
    assert quiet_runtime.event.call_args.kwargs["context_reply_state"] is obligation["context_reply"]["state"]


@pytest.mark.parametrize("boundary", ["runtime", "latch", "pause", "cooldown", "due", "requested", "import"])
def test_worker_keeps_import_live_latch_and_eager_gate_order(monkeypatch, quiet_runtime, boundary):
    events = []
    failure = ImportError("local outbox import")
    original_import = builtins.__import__

    def current_import(name, *args, **kwargs):
        if name == "historical_context_outbox":
            events.append("import")
            if boundary == "import":
                raise failure
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", current_import)
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON",
                        "unavailable" if boundary in {"runtime", "import"} else None)
    observations = iter(["first", "second"] if boundary == "latch" else [None])

    def getter():
        value = next(observations)
        events.append(("get", value))
        return value

    def paused(lane):
        events.append("pause")
        assert lane == "disable_replies"
        return boundary == "pause"

    state = {}

    def cooldown(current, **kwargs):
        events.append("cooldown")
        assert current is state and kwargs == {"scope": "write"}
        return boundary == "cooldown"

    def clock():
        events.append("clock")
        return 100

    class Limit:
        def __int__(self):
            events.append("limit")
            return 0

    def due(epoch, **kwargs):
        events.append("due")
        assert epoch == 100 and kwargs == {"limit": 1}
        return []

    def get(parent):
        events.append("requested")
        assert parent == "123"
        return None

    monkeypatch.setattr(bot, "_get_historical_context_outbox_unavailable_reason", getter)
    monkeypatch.setattr(bot, "lane_paused", paused)
    monkeypatch.setattr(bot, "in_api_cooldown", cooldown)
    monkeypatch.setattr(bot, "now_epoch", clock)
    options = dict(store=SimpleNamespace(due=due, get=get), runtime_state=state,
                   limit=Limit(), parent_post_id=123 if boundary == "requested" else None)
    if boundary == "import":
        with pytest.raises(ImportError) as caught:
            bot._process_due_historical_context_obligations(**options)
        assert caught.value is failure
    else:
        assert bot._process_due_historical_context_obligations(**options) == []
    expected = {
        "import": ["import"], "runtime": ["import"],
        "latch": ["import", ("get", "first"), ("get", "second")],
        "pause": ["import", ("get", None), "pause"],
        "cooldown": ["import", ("get", None), "pause", "cooldown"],
        "due": ["import", ("get", None), "pause", "cooldown", "clock", "limit", "due"],
        "requested": ["import", ("get", None), "pause", "cooldown", "clock", "requested"],
    }
    assert events == expected[boundary]
    if boundary == "latch":
        assert quiet_runtime.log.debug.call_args.args[-1] == "second"


@pytest.mark.parametrize("boundary", ["clock", "selection", "iteration", "claim", "result", "outcome"])
def test_worker_keeps_native_error_scopes_and_latch_before_log(monkeypatch, quiet_runtime, boundary):
    events = []
    failure = TypeError("native worker failure")
    obligation = {"parent_post_id": "123", "context_reply": {
        "state": "context_reply_pending", "quote_id": "hash", "quote_text": "quote",
        "attempt_count": 1,
    }}
    store = SimpleNamespace(
        due=Mock(return_value=[obligation]), claim_attempt=Mock(return_value=obligation),
        mark_not_required=Mock(side_effect=failure),
    )
    post = Mock(return_value={"status": "disabled"})
    monkeypatch.setattr(bot, "maybe_post_historical_context_reply", post)
    if boundary == "clock":
        quiet_runtime.clock.side_effect = failure
    elif boundary == "selection":
        store.due.side_effect = failure
    elif boundary == "iteration":
        def broken_rows():
            raise failure
            yield
        store.due.return_value = broken_rows()
    elif boundary == "claim":
        store.claim_attempt.side_effect = failure
    elif boundary == "result":
        post.return_value = SimpleNamespace(get=Mock(side_effect=failure))
    setter = bot._set_historical_context_outbox_unavailable_reason

    def set_reason(value):
        events.append("set")
        assert value == "TypeError: native worker failure"
        setter(value)

    def diagnostic(*args, **kwargs):
        events.append("log")
        assert bot._get_historical_context_outbox_unavailable_reason() == "TypeError: native worker failure"

    monkeypatch.setattr(bot, "_set_historical_context_outbox_unavailable_reason", set_reason)
    quiet_runtime.log.critical.side_effect = diagnostic
    quiet_runtime.event.side_effect = lambda *args, **kwargs: events.append("event")
    if boundary in {"clock", "iteration", "result"}:
        with pytest.raises(TypeError) as caught:
            bot._process_due_historical_context_obligations(store=store)
        assert caught.value is failure
        assert events == []
        assert bot._get_historical_context_outbox_unavailable_reason() is None
    else:
        result = bot._process_due_historical_context_obligations(store=store)
        expected_status = {"claim": "outbox_claim_failed", "outcome": "outbox_persistence_failed"}
        assert result == ([] if boundary == "selection" else [{
            "parent_post_id": "123", "status": expected_status[boundary],
            **({"context_reply_state": "context_reply_pending"} if boundary == "claim"
               else {"error_type": "TypeError"}),
        }])
        assert events == (["set", "log"] if boundary == "outcome" else ["set", "log", "event"])


def test_worker_callbacks_keep_each_claim_capture_and_original_arguments(monkeypatch, quiet_runtime):
    rows = [
        {"parent_post_id": parent, "context_reply": {
            "state": "context_reply_pending", "quote_id": "hash", "quote_text": "quote",
            "attempt_count": attempt,
        }}
        for parent, attempt in (("123", 1), ("456", 2))
    ]
    by_parent = {row["parent_post_id"]: row for row in rows}
    store = SimpleNamespace(
        due=Mock(return_value=rows), get=Mock(side_effect=by_parent.get),
        claim_attempt=Mock(side_effect=lambda parent, **kwargs: by_parent[parent]),
        max_attempts=5, bind_attempt_source_receipt=Mock(),
        mark_remote_transaction_started=Mock(), record_confirmed=Mock(),
    )
    callbacks = []

    def post(**kwargs):
        callbacks.append(kwargs)
        return {"status": "failed_terminal", "reason": "reviewed"}

    failure_recorder = Mock(return_value="context_reply_failed_terminal")
    monkeypatch.setattr(bot, "maybe_post_historical_context_reply", post)
    monkeypatch.setattr(owner, "_record_context_outbox_failure", failure_recorder)
    assert len(bot._process_due_historical_context_obligations(store=store, limit=2)) == 2
    failure_recorder.reset_mock()
    quiet_runtime.clock.return_value = 777
    store.max_attempts = 2
    for callbacks_for_row, (parent, attempt) in zip(callbacks, (("123", 1), ("456", 2))):
        source, source_attempt, error, reply_id, epoch = (object() for _ in range(5))
        receipt = {"reply_post_id": reply_id}
        callbacks_for_row["on_source_receipt_published"](source, source_attempt)
        store.bind_attempt_source_receipt.assert_called_with(
            parent, attempt_number=attempt, source_receipt_sha256=source,
            source_receipt_attempt_number=source_attempt,
        )
        callbacks_for_row["on_remote_transaction_started"]()
        store.mark_remote_transaction_started.assert_called_with(parent, attempt_number=attempt)
        callbacks_for_row["on_definite_non_success"](error)
        failure_recorder.assert_called_with(
            store, parent_post_id=parent, attempt_number=attempt, error=error,
            failed_epoch=777, force_terminal=attempt >= 2, proved_remote_non_success=True,
        )
        callbacks_for_row["on_confirmed_receipt"](receipt, epoch)
        store.record_confirmed.assert_called_with(
            parent, attempt_number=attempt, reply_post_id=reply_id, confirmed_epoch=epoch,
        )


@pytest.mark.parametrize("boundary", ["ok", "worker", "event", "critical", "nested_critical", "base", "string"])
def test_safe_wrapper_preserves_exception_only_mutation_and_diagnostic_order(monkeypatch, quiet_runtime, boundary):
    events, state, result = [], {}, [{"unchanged": object()}]
    diagnostic_error = TypeError("native diagnostic error")

    class BadString(Exception):
        def __str__(self):
            raise diagnostic_error

    failure = (KeyboardInterrupt() if boundary == "base" else
               BadString() if boundary == "string" else ValueError("worker failed"))

    def worker(**kwargs):
        events.append("worker")
        assert kwargs == {"parent_post_id": "123", "limit": 4, "runtime_state": state}
        assert kwargs["runtime_state"] is state
        if boundary != "ok":
            raise failure
        return result

    setter = bot._set_historical_context_outbox_unavailable_reason

    def set_reason(reason):
        events.append("set")
        assert reason == "ValueError: worker failed"
        setter(reason)

    def critical(*args, **kwargs):
        events.append("critical")
        assert bot._get_historical_context_outbox_unavailable_reason() == "ValueError: worker failed"
        if boundary == "critical" or boundary == "nested_critical" and events.count("critical") == 2:
            raise diagnostic_error

    def event(*args, **kwargs):
        events.append("event")
        assert kwargs["main_post_success_preserved"] is True
        if boundary in {"event", "nested_critical"}:
            raise diagnostic_error

    monkeypatch.setattr(bot, "process_due_historical_context_obligations", worker)
    monkeypatch.setattr(bot, "_set_historical_context_outbox_unavailable_reason", set_reason)
    quiet_runtime.log.critical.side_effect = critical
    quiet_runtime.event.side_effect = event
    options = dict(parent_post_id="123", limit=4, runtime_state=state)
    if boundary in {"critical", "nested_critical", "base", "string"}:
        expected = failure if boundary == "base" else diagnostic_error
        with pytest.raises(type(expected)) as caught:
            bot.safely_process_due_historical_context_obligations(**options)
        assert caught.value is expected
    else:
        returned = bot.safely_process_due_historical_context_obligations(**options)
        if boundary == "ok":
            assert returned is result
        else:
            assert returned == [{"parent_post_id": "123", "status": "worker_failed_isolated",
                                 "error_type": "ValueError"}]
    expected_events = {
        "ok": ["worker"], "base": ["worker"], "string": ["worker"],
        "critical": ["worker", "set", "critical"],
        "worker": ["worker", "set", "critical", "event"],
        "event": ["worker", "set", "critical", "event", "critical"],
        "nested_critical": ["worker", "set", "critical", "event", "critical"],
    }
    assert events == expected_events[boundary]
