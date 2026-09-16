"""Focused contracts for historical-context delivery and interrupted recovery."""
from __future__ import annotations

from contextlib import contextmanager
import inspect
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import historical_context_formatter as formatter
import historical_context_outbox as outbox_module
import mrs_bot_historical_context_delivery as owner
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_regular_post_receipt  # noqa: F401
from tests.helpers.historical_context_fixtures import (
    _bind_context_attempt_to_source_receipt,
    _formatted,
    _gate,
    _install_bot_context,
    isolated_incident_paths,
)


DEPENDENCIES = {'maybe_post_historical_context_reply': ['HISTORICAL_CONTEXT_RESEARCH_DIR',
                                         'RemoteOperationsPaused',
                                         '_HISTORICAL_CONTEXT_CORPUS_SNAPSHOT',
                                         '_HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON',
                                         '_HISTORICAL_CONTEXT_SEMANTIC_GATE',
                                         'begin_confirmed_post_sigint_deferral',
                                         'block_if_ambiguous_remote_post',
                                         'create_post',
                                         'emit_historical_context_reply_posted',
                                         'end_confirmed_post_sigint_deferral',
                                         'historical_context_reply',
                                         'historical_context_reply_store',
                                         'initialise_historical_context_semantic_gate',
                                         'log',
                                         'log_event',
                                         'now_epoch',
                                         're'],
 'historical_context_reply_store': ['HISTORICAL_CONTEXT_REPLY_HISTORY_FILE',
                                    'HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE',
                                    'TEST_MODE',
                                    'latch_source_receipt_retirement_uncertainty',
                                    'transaction_mutation_authority'],
 'historical_context_outbox_store': ['HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE', 'TEST_MODE'],
 'canonical_context_obligation_quote_id': ['_HISTORICAL_CONTEXT_CORPUS_SNAPSHOT'],
 '_record_context_outbox_failure': [],
 '_record_or_verify_proved_context_failure': ['_record_context_outbox_failure', 're'],
 'recover_interrupted_historical_context_attempt': ['HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE',
                                                    '_record_context_outbox_failure',
                                                    'emit_historical_context_history_observation',
                                                    'hashlib',
                                                    'historical_context_reply_store',
                                                    'journal_path_for_receipt',
                                                    're',
                                                    'transport_journal_is_blocking'],
 'process_due_historical_context_obligations': ['AmbiguousRemotePostOutcome',
                                                'InvalidMemePostReceipt',
                                                'InvalidRegularPostReceipt',
                                                '_process_due_historical_context_obligations',
                                                'block_if_ambiguous_remote_post',
                                                'historical_context_outbox_remote_attempt_parent_for_local_reconciliation',
                                                'historical_context_outbox_store',
                                                'historical_context_receipt_parent_for_local_reconciliation',
                                                'historical_context_receipt_path_present_or_unsafe',
                                                'log']}

SIGNATURES = {'maybe_post_historical_context_reply': "(*, quote_hash: 'str', quote_text: 'str', parent_post_id: "
                                        "'str', dry_run: 'bool' = False, "
                                        "on_source_receipt_published: 'Callable[[str, int], None] "
                                        "| None' = None, on_remote_transaction_started: "
                                        "'Callable[[], None] | None' = None, "
                                        "on_definite_non_success: 'Callable[[BaseException], str] "
                                        "| None' = None, on_confirmed_receipt: 'Callable[[dict, "
                                        "int], None] | None' = None) -> 'dict'",
 'historical_context_reply_store': "(*, allow_missing_history: 'bool' = False)",
 'historical_context_outbox_store': '()',
 'canonical_context_obligation_quote_id': "(quote_hash: 'str', quote_text: 'str') -> 'str'",
 '_record_context_outbox_failure': "(store, *, parent_post_id: 'str', attempt_number: 'int', "
                                   "error: 'BaseException | str', failed_epoch: 'int', "
                                   "force_terminal: 'bool' = False, proved_remote_non_success: "
                                   "'bool' = False) -> 'str'",
 '_record_or_verify_proved_context_failure': "(store, *, parent_post_id: 'str', attempt_number: "
                                             "'int', error, failed_epoch: 'int') -> 'str'",
 'recover_interrupted_historical_context_attempt': "(store, obligation: 'dict', *, "
                                                   "recovered_epoch: 'int', receipt_was_observed: "
                                                   "'bool' = False) -> 'dict'",
 'process_due_historical_context_obligations': "(*, parent_post_id: 'str | None' = None, limit: "
                                               "'int' = 1, runtime_state: 'dict | None' = None) -> "
                                               "'list[dict]'"}


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, io, logging, os, random, socket, sys, time, typing
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('Historical-context delivery import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'historical_context_formatter', 'historical_context_outbox'} or name.startswith('mrs_bot_') and name != 'mrs_bot_historical_context_delivery':
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
import mrs_bot_historical_context_delivery
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



@pytest.mark.parametrize("name", [name for name, deps in DEPENDENCIES.items() if deps])
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

            patch.setattr(bot, "_historical_context_delivery", SimpleNamespace(**{name: capture}))
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
            patch.setattr(bot, "_historical_context_delivery", SimpleNamespace(**{name: Mock(side_effect=failure)}))
            with pytest.raises(TypeError) as caught:
                adapter(**provided)
            assert caught.value is failure


@pytest.mark.parametrize("attempt, force, terminal", [(1, False, False), (3, False, True), (1, True, True)])
def test_pure_failure_recorder_is_exact_alias_with_native_store_contract(attempt, force, terminal):
    function = bot._record_context_outbox_failure
    assert function is owner._record_context_outbox_failure
    assert str(inspect.signature(function)) == SIGNATURES[function.__name__]
    parent, error, epoch = object(), KeyboardInterrupt(), object()
    state = object()
    result = {"context_reply": {"state": state}}
    store = SimpleNamespace(
        record_terminal_failure=Mock(return_value=result),
        record_retryable_failure=Mock(return_value=result),
    )
    if not force:
        store.max_attempts = 3
    assert function(store, parent_post_id=parent, attempt_number=attempt, error=error,
                    failed_epoch=epoch, force_terminal=force,
                    proved_remote_non_success=True) == str(state)
    selected = store.record_terminal_failure if terminal else store.record_retryable_failure
    other = store.record_retryable_failure if terminal else store.record_terminal_failure
    selected.assert_called_once_with(parent, attempt_number=attempt, error=error,
                                    failed_epoch=epoch, proved_remote_non_success=True)
    other.assert_not_called()
    failure = TypeError("durable store failure")
    selected.side_effect = failure
    with pytest.raises(TypeError) as caught:
        function(store, parent_post_id=parent, attempt_number=attempt, error=error,
                 failed_epoch=epoch, force_terminal=force)
    assert caught.value is failure


@pytest.mark.parametrize("test_mode, allow_missing", [(True, False), (False, False), (False, True)])
def test_factories_use_call_time_classes_paths_callbacks_and_existence_flags(monkeypatch, test_mode, allow_missing):
    observations = []

    class Flag:
        def __init__(self, label, value):
            self.label, self.value = label, value

        def __bool__(self):
            observations.append(self.label)
            return self.value

    history, receipt, outbox, authority, latch = (object() for _ in range(5))
    for name, value in {
        "HISTORICAL_CONTEXT_REPLY_HISTORY_FILE": history,
        "HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE": receipt,
        "HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE": outbox,
        "transaction_mutation_authority": authority,
        "latch_source_receipt_retirement_uncertainty": latch,
        "TEST_MODE": Flag("mode", test_mode),
    }.items():
        monkeypatch.setattr(bot, name, value)
    reply_class, outbox_class = Mock(), Mock()
    monkeypatch.setattr(formatter, "HistoricalContextReplyStore", reply_class)
    monkeypatch.setattr(outbox_module, "HistoricalContextOutbox", outbox_class)
    assert bot.historical_context_reply_store(
        allow_missing_history=Flag("allow", allow_missing)
    ) is reply_class.return_value
    assert bot.historical_context_outbox_store() is outbox_class.return_value
    reply_class.assert_called_once_with(
        history, receipt, mutation_authority_provider=authority,
        retirement_uncertainty_callback=latch,
        require_existing_history=not test_mode and not allow_missing,
    )
    outbox_class.assert_called_once_with(outbox, require_existing=not test_mode)
    assert observations == (["mode", "mode"] if test_mode else ["mode", "allow", "mode"])


def test_canonical_lookup_preserves_snapshot_short_circuit_and_call_time_lookup(monkeypatch):
    class Text:
        def __init__(self, text):
            self.text = text

        def __str__(self):
            seen.append(self.text)
            return self.text

    seen = []
    quote_hash, quote_text = Text("hash"), Text("quote")
    lookup = Mock(side_effect=AssertionError("lookup before snapshot"))
    monkeypatch.setattr(formatter, "packet_for_posted_quote", lookup)
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_CORPUS_SNAPSHOT", None)
    assert bot.canonical_context_obligation_quote_id(quote_hash, quote_text) == "hash"
    assert seen == ["hash"]
    lookup.assert_not_called()
    packets, unresolved = {}, set()
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_CORPUS_SNAPSHOT", (packets, unresolved))
    for packet, expected in [(None, "hash"), ({"quote_id": Text("canonical")}, "canonical")]:
        lookup = Mock(return_value=packet)
        monkeypatch.setattr(formatter, "packet_for_posted_quote", lookup)
        assert bot.canonical_context_obligation_quote_id(quote_hash, quote_text) == expected
        lookup.assert_called_once_with(packets, unresolved, "hash", "quote")
        assert lookup.call_args.args[0] is packets and lookup.call_args.args[1] is unresolved
    failure = KeyError("current packet lookup")
    lookup.side_effect = failure
    with pytest.raises(KeyError) as caught:
        bot.canonical_context_obligation_quote_id(quote_hash, quote_text)
    assert caught.value is failure


@pytest.mark.parametrize("dry_run", [False, True])
def test_delivery_preserves_order_current_callbacks_metadata_and_result_references(monkeypatch, tmp_path, capsys, dry_run):
    packet = {"quote_id": "a" * 64}
    _install_bot_context(monkeypatch, tmp_path, packet=packet, gate=_gate())
    order, packets, unresolved = [], {}, set()
    formatted = _formatted(packet["quote_id"])
    guard, payload, parent = object(), [], 123
    result = {"status": "dry_run" if dry_run else "completed", "reply_post_id": "456", "payload": payload}
    callbacks = {name: Mock() for name in (
        "on_source_receipt_published", "on_remote_transaction_started",
        "on_definite_non_success", "on_confirmed_receipt",
    )}

    class CurrentPause(Exception):
        pass

    def step(name, value=None):
        def observe(*args, **kwargs):
            order.append(name)
            return value
        return observe

    monkeypatch.setattr(bot, "RemoteOperationsPaused", CurrentPause)
    monkeypatch.setattr(bot, "block_if_ambiguous_remote_post", step("barrier"))
    monkeypatch.setattr(bot, "begin_confirmed_post_sigint_deferral", step("begin", guard))
    end = Mock(side_effect=step("end"))
    monkeypatch.setattr(bot, "end_confirmed_post_sigint_deferral", end)
    create, clock = Mock(), Mock()
    monkeypatch.setattr(bot, "create_post", create)
    monkeypatch.setattr(bot, "now_epoch", clock)
    gate = SimpleNamespace(
        available=True, ledger_sha256="ledger", projection_sha256="projection",
        disposition=step("disposition"), reviewed_disposition=step("reviewed", "reviewed"),
    )
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_SEMANTIC_GATE", None)
    initialize = Mock(side_effect=step("gate", gate))
    monkeypatch.setattr(bot, "initialise_historical_context_semantic_gate", initialize)
    load = Mock(side_effect=step("load", (packets, unresolved)))
    lookup = Mock(side_effect=step("packet", packet))
    render = Mock(side_effect=step("format", formatted))
    monkeypatch.setattr(formatter, "load_and_validate_corpus", load)
    monkeypatch.setattr(formatter, "packet_for_posted_quote", lookup)
    monkeypatch.setattr(formatter, "format_context_reply_public", render)
    monkeypatch.setattr(bot, "historical_context_reply", {
        "enabled": not dry_run, "maximum_length": "4000", "include_meaning": 1,
        "include_source": 0, "include_verification": "yes",
    })
    if dry_run:
        monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON", "unavailable")

    def post(**kwargs):
        order.append("post")
        assert kwargs.keys() == {
            "parent_post_id", "quote_id", "reply_text", "create_post", "now_epoch",
            "dry_run", "formatter_metadata", "remote_failure_is_definite_non_success",
            "require_confirmed_transport", *callbacks,
        }
        assert (kwargs["parent_post_id"], kwargs["quote_id"], kwargs["reply_text"]) == (
            "123", packet["quote_id"], formatted["text"],
        )
        assert kwargs["create_post"] is create and kwargs["now_epoch"] is clock
        assert kwargs["dry_run"] is dry_run and kwargs["require_confirmed_transport"] is not dry_run
        assert all(kwargs[name] is (None if dry_run else callback) for name, callback in callbacks.items())
        assert kwargs["formatter_metadata"]["confidence_dimensions"] is formatted["confidence_dimensions"]
        classifier = kwargs["remote_failure_is_definite_non_success"]
        assert classifier(CurrentPause()) is True and classifier(RuntimeError()) is False
        return result

    store = SimpleNamespace(post=post, reconcile_receipt=step("reconcile"))
    monkeypatch.setattr(bot, "historical_context_reply_store", step("factory", store))
    event, emit = Mock(side_effect=step("event")), Mock(side_effect=step("emit"))
    monkeypatch.setattr(bot, "log_event", event)
    monkeypatch.setattr(bot, "emit_historical_context_reply_posted", emit)
    returned = bot.maybe_post_historical_context_reply(
        quote_hash="hash", quote_text="quote", parent_post_id=parent, dry_run=dry_run, **callbacks,
    )
    assert returned is not result and returned["formatted"] is formatted and returned["payload"] is payload
    prefix = ["factory"] if dry_run else ["barrier", "factory", "reconcile"]
    assert order == prefix + ["load", "packet", "gate", "disposition", "reviewed", "format"] + (
        ["post", "end", "event"] if dry_run else ["begin", "post", "end", "event", "emit"]
    )
    load.assert_called_once_with(bot.HISTORICAL_CONTEXT_RESEARCH_DIR, require_source_role_audit=True)
    lookup.assert_called_once_with(packets, unresolved, "hash", "quote")
    assert initialize.call_args.args[0] is packets
    render.assert_called_once_with(packet, maximum_length=4000, include_meaning=True,
                                   include_source=False, include_verification=True)
    end.assert_called_once_with(None if dry_run else guard)
    assert event.call_args.kwargs["confidence_dimensions"] is formatted["confidence_dimensions"]
    if dry_run:
        emit.assert_not_called()
        assert capsys.readouterr().out == formatted["text"] + "\nCharacter count: raw=32 x_weighted=32/4000\n"
    else:
        emit.assert_called_once_with(parent_post_id=parent, reply_post_id=result["reply_post_id"],
                                     reply_text=formatted["text"], quote_id=packet["quote_id"])


@pytest.mark.parametrize("boundary, kind", [
    ("barrier", "ordinary"), ("begin", "ordinary"), ("post", "ordinary"),
    ("post", "ambiguous"), ("post", "subclass"), ("post", "base"), ("end", "ordinary"),
])
def test_delivery_keeps_prebarrier_guard_finally_and_exact_exception_order(monkeypatch, tmp_path, boundary, kind):
    packet = {"quote_id": "b" * 64}
    _install_bot_context(monkeypatch, tmp_path, packet=packet, gate=_gate())
    monkeypatch.setattr(formatter, "format_context_reply_public", lambda *a, **k: _formatted(packet["quote_id"]))
    ambiguous = type("AmbiguousContextReplyOutcome", (Exception,), {})
    error_type = {"ordinary": TypeError, "ambiguous": ambiguous,
                  "subclass": type("DerivedAmbiguity", (ambiguous,), {}), "base": KeyboardInterrupt}[kind]
    failure, guard, events = error_type("boundary failure"), object(), []

    def visit(name, result=None):
        def callback(*args, **kwargs):
            events.append(name)
            if name == "end":
                assert args == (guard,)
            if name == boundary:
                raise failure
            return result
        return callback

    monkeypatch.setattr(bot, "block_if_ambiguous_remote_post", visit("barrier"))
    monkeypatch.setattr(bot, "historical_context_reply_store", visit("factory", SimpleNamespace(
        reconcile_receipt=visit("reconcile"), post=visit("post", {"status": "failed"}),
    )))
    monkeypatch.setattr(bot, "begin_confirmed_post_sigint_deferral", visit("begin", guard))
    monkeypatch.setattr(bot, "end_confirmed_post_sigint_deferral", visit("end"))
    monkeypatch.setattr(bot, "log", SimpleNamespace(error=visit("error")))
    event = Mock(side_effect=visit("event"))
    monkeypatch.setattr(bot, "log_event", event)
    with pytest.raises(error_type) as caught:
        bot.maybe_post_historical_context_reply(quote_hash="hash", quote_text="quote", parent_post_id="123")
    assert caught.value is failure
    expected = ["barrier"]
    if boundary != "barrier":
        expected += ["factory", "reconcile", "begin"]
        if boundary != "begin":
            expected += ["post", "end"]
        if kind != "base":
            expected += ["event"] if kind == "ambiguous" else ["error", "event"]
            assert event.call_args.kwargs["reason"] == ("ambiguous_outcome" if kind == "ambiguous" else error_type.__name__)
    assert events == expected


@pytest.mark.parametrize("case", ["hash", "ordinal", "error", "changed", "mismatch", "durable", "attempting"])
def test_proved_failure_checks_exact_proof_before_current_recorder(monkeypatch, case):
    remote_error = KeyboardInterrupt("definite failure")
    error = SimpleNamespace(source_receipt_sha256="a" * 64,
                            source_receipt_attempt_number=21, remote_error=remote_error)
    context = {"state": "context_reply_attempting", "attempt_count": 7,
               "source_receipt_sha256": "a" * 64, "source_receipt_attempt_number": 21}
    if case in {"mismatch", "durable"}:
        context.update(state="context_reply_failed_terminal", failure={
            "remote_outcome": "proved_non_success", "source_receipt_attempt_number": 21,
            "source_receipt_sha256": ("b" if case == "mismatch" else "a") * 64,
        })
    if case == "changed":
        context["attempt_count"] = 6
    if case == "hash":
        error.source_receipt_sha256 = "A" * 64
    if case == "ordinal":
        error.source_receipt_attempt_number = True
    if case == "error":
        error.remote_error = "failure"
    store = SimpleNamespace(max_attempts=7, get=Mock(return_value={"context_reply": context}))
    result = object()
    record = Mock(return_value=result)
    monkeypatch.setattr(bot, "_record_context_outbox_failure", record)
    options = dict(parent_post_id="123", attempt_number=7, error=error, failed_epoch=99)
    if case in {"hash", "ordinal", "error", "changed", "mismatch"}:
        message = ("lacks exact source proof" if case in {"hash", "ordinal", "error"}
                   else "attempt changed" if case == "changed" else "does not match source proof")
        with pytest.raises(RuntimeError, match=message):
            bot._record_or_verify_proved_context_failure(store, **options)
        record.assert_not_called()
        if case in {"hash", "ordinal", "error"}:
            store.get.assert_not_called()
    elif case == "durable":
        assert bot._record_or_verify_proved_context_failure(store, **options) == context["state"]
        record.assert_not_called()
    else:
        assert bot._record_or_verify_proved_context_failure(store, **options) is result
        record.assert_called_once_with(store, parent_post_id="123", attempt_number=7,
                                       error=remote_error, failed_epoch=99,
                                       force_terminal=True, proved_remote_non_success=True)
        failure = OSError("outbox persistence")
        record.side_effect = failure
        with pytest.raises(OSError) as caught:
            bot._record_or_verify_proved_context_failure(store, **options)
        assert caught.value is failure


@pytest.mark.parametrize("fail_at", [None, "outbox", "history", "retire"])
def test_pre_remote_recovery_orders_durable_outbox_history_and_exact_receipt_retirement(monkeypatch, fail_at):
    parent, quote_id, epoch = "800044", "5" * 64, 1_800_000_100
    store = bot.historical_context_outbox_store()
    store.enqueue(parent, main_post_confirmed_epoch=epoch - 10,
                  quote_id=quote_id, quote_text="Pre-transport crash.")
    store.claim_attempt(parent, started_epoch=epoch)
    source = {"schema_version": 1, "lifecycle_state": "sending", "parent_post_id": parent,
              "quote_id": quote_id, "reply_text": "Context — Durable before transport.",
              "reply_epoch": epoch, "started_at": "2026-08-01T12:01:40Z", "attempt_number": 21}
    formatter.atomic_write_json(bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE, source)
    digest = _bind_context_attempt_to_source_receipt(
        store, parent_id=parent, outbox_attempt=1, source_receipt=source,
    )
    receipt_bytes = bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.read_bytes()
    context_store = bot.historical_context_reply_store()
    monkeypatch.setattr(bot, "historical_context_reply_store", lambda: context_store)
    events, failure = [], OSError("ordered persistence failure")

    def wrap(name, callback):
        def observed(*args, **kwargs):
            events.append(name)
            assert bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.read_bytes() == receipt_bytes
            if name in {"history", "retire"}:
                durable = store.get(parent)["context_reply"]
                assert durable["state"] == "context_reply_failed_retryable"
                assert durable["failure"]["source_receipt_sha256"] == digest
                assert durable["failure"]["source_receipt_attempt_number"] == 21
            if name == "history":
                assert args[0] == durable
            if name == "retire":
                assert args == () and kwargs == {}
                assert context_store.history()["items"][parent]["source_receipt_sha256"] == digest
            if name == fail_at:
                raise failure
            return callback(*args, **kwargs)
        return observed

    monkeypatch.setattr(bot, "_record_context_outbox_failure", wrap("outbox", bot._record_context_outbox_failure))
    monkeypatch.setattr(context_store, "ensure_proved_failure_history_from_outbox",
                        wrap("history", context_store.ensure_proved_failure_history_from_outbox))
    monkeypatch.setattr(context_store, "reconcile_receipt_disposition",
                        wrap("retire", context_store.reconcile_receipt_disposition))
    obligation = store.get(parent)
    if fail_at is None:
        assert bot.recover_interrupted_historical_context_attempt(store, obligation, recovered_epoch=epoch + 1) == {
            "parent_post_id": parent, "status": "recovered_pre_remote_interruption",
            "context_reply_state": "context_reply_failed_retryable", "attempt_number": 1,
            "remote_work_repeated": False,
        }
        assert not bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.exists()
    else:
        with pytest.raises(OSError) as caught:
            bot.recover_interrupted_historical_context_attempt(store, obligation, recovered_epoch=epoch + 1)
        assert caught.value is failure
        assert bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.read_bytes() == receipt_bytes
        if fail_at == "outbox":
            assert store.get(parent)["context_reply"]["state"] == "context_reply_attempting"
        if fail_at in {"outbox", "history"}:
            assert not bot.HISTORICAL_CONTEXT_REPLY_HISTORY_FILE.exists()
    expected = ["outbox", "history", "retire"]
    assert events == (expected if fail_at is None else expected[:expected.index(fail_at) + 1])


@pytest.mark.parametrize("boundary", [
    "ok", "conflicting_parents", "requested_parent", "barrier_known", "barrier_native",
    "factory_busy", "worker_busy", "worker_native",
])
def test_processing_keeps_eager_observations_narrow_catches_and_worker_lock(monkeypatch, boundary):
    events, result, runtime_state = [], [], {}
    receipt_observed = object()

    class CurrentBusy(Exception):
        pass

    class CurrentBarrier(Exception):
        pass

    failure = (CurrentBusy() if boundary.endswith("busy") else
               CurrentBarrier() if boundary == "barrier_known" else TypeError("native worker boundary"))
    monkeypatch.setattr(outbox_module, "OutboxWorkerBusy", CurrentBusy)
    monkeypatch.setattr(bot, "InvalidRegularPostReceipt", CurrentBarrier)

    def observe(name, value):
        def callback(*args, **kwargs):
            events.append(name)
            return value
        return callback

    monkeypatch.setattr(bot, "historical_context_receipt_path_present_or_unsafe", observe("receipt", receipt_observed))
    monkeypatch.setattr(bot, "historical_context_receipt_parent_for_local_reconciliation", observe("receipt_parent", "123"))
    outbox_parent = "456" if boundary == "conflicting_parents" else "123"
    monkeypatch.setattr(bot, "historical_context_outbox_remote_attempt_parent_for_local_reconciliation",
                        observe("outbox_parent", outbox_parent))

    def barrier(**kwargs):
        events.append("barrier")
        assert kwargs == {"allow_historical_context_receipt_reconciliation": receipt_observed,
                          "allow_historical_context_outbox_reconciliation_parent_id": outbox_parent}
        if boundary.startswith("barrier_"):
            raise failure

    @contextmanager
    def worker_lock():
        events.append("enter")
        try:
            yield
        finally:
            events.append("exit")

    store = SimpleNamespace(worker_lock=worker_lock)

    def factory():
        events.append("factory")
        if boundary == "factory_busy":
            raise failure
        return store

    def worker(**kwargs):
        events.append("worker")
        assert kwargs == {"store": store, "parent_post_id": "123", "limit": 4,
                          "runtime_state": runtime_state,
                          "historical_context_receipt_reconciliation_only": receipt_observed,
                          "historical_context_outbox_reconciliation_only": True}
        assert kwargs["runtime_state"] is runtime_state and kwargs["store"] is store
        if boundary.startswith("worker_"):
            raise failure
        return result

    monkeypatch.setattr(bot, "block_if_ambiguous_remote_post", barrier)
    monkeypatch.setattr(bot, "historical_context_outbox_store", factory)
    monkeypatch.setattr(bot, "_process_due_historical_context_obligations", worker)
    monkeypatch.setattr(bot, "log", SimpleNamespace(critical=observe("critical", None), info=observe("info", None)))
    options = dict(parent_post_id="456" if boundary == "requested_parent" else None,
                   limit=4, runtime_state=runtime_state)
    if boundary in {"barrier_native", "factory_busy", "worker_native"}:
        with pytest.raises(type(failure)) as caught:
            bot.process_due_historical_context_obligations(**options)
        assert caught.value is failure
    else:
        returned = bot.process_due_historical_context_obligations(**options)
        assert returned == []
        if boundary == "ok":
            assert returned is result
    prefix = ["receipt", "receipt_parent", "outbox_parent"]
    suffixes = {
        "conflicting_parents": ["critical"], "requested_parent": ["barrier", "critical"],
        "barrier_known": ["barrier", "critical"], "barrier_native": ["barrier"],
        "factory_busy": ["barrier", "factory"],
    }
    suffix = suffixes.get(boundary, ["barrier", "factory", "enter", "worker", "exit"] + (
        ["info"] if boundary == "worker_busy" else []
    ))
    assert events == prefix + suffix
