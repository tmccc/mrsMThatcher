"""Focused contracts for confirmed main-post promotion and protected persistence."""
from __future__ import annotations

import inspect
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrs_bot_main_post_confirmation_persistence as owner
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401

DEPENDENCIES = {'atomic_json_file_exactly_matches': ['canonical_atomic_json_bytes'],
 'promote_main_post_attempt_to_confirmed_pending_schedule': ['AmbiguousRemotePostOutcome',
                                                             'BoundSourceReceiptTransitionError',
                                                             'ConfirmedPendingScheduleDurabilityUncertain',
                                                             'REGULAR_POST_RECEIPT_FILE',
                                                             'TRANSPORT_SOURCE_VALIDATOR_ID',
                                                             'TransportJournalError',
                                                             '_set_ambiguous_remote_post_seen',
                                                             'atomic_json_file_exactly_matches',
                                                             'bind_confirmed_transport_source',
                                                             'build_confirmed_pending_schedule_receipt',
                                                             'canonical_atomic_json_bytes',
                                                             'fsync_parent_dir',
                                                             'journal_path_for_receipt',
                                                             'latch_confirmed_post_persistence_failure',
                                                             'load_meme_post_receipt',
                                                             'load_regular_post_receipt',
                                                             'log',
                                                             'main_post_attempt_path',
                                                             'remote_write_safety_incident_is_latched',
                                                             'replace_bound_source_receipt',
                                                             'transaction_mutation_authority',
                                                             'transport_source_semantic_validator'],
 'save_regular_post_protected_state': ['IMAGES_USED_FILE',
                                       'LINES_USED_FILE',
                                       'save_image_used_basenames',
                                       'save_quote_used_hashes',
                                       'save_state'],
 'emergency_persist_confirmed_regular_post': ['IMAGES_USED_FILE',
                                              'LINES_USED_FILE',
                                              'STATE_FILE',
                                              'StateBackupWriteError',
                                              'json_file_matches',
                                              'log',
                                              'save_image_used_basenames',
                                              'save_quote_used_hashes',
                                              'save_state']}


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, datetime, io, logging, os, random, socket, sys, time, typing, zoneinfo
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('Main-post-confirmation-persistence import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'engagement_question_experiment', 'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'historical_context_formatter', 'historical_context_outbox', 'transaction_mutation_authority', 'remote_write_transport_journal', 'remote_media_upload_receipt'} or name.startswith('mrs_bot_') and name != 'mrs_bot_main_post_confirmation_persistence':
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
import mrs_bot_main_post_confirmation_persistence
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
assert 'requests' not in sys.modules
assert 'single_call_reply' not in sys.modules
assert 'transaction_mutation_authority' not in sys.modules
assert mrs_bot_main_post_confirmation_persistence.atomic_json_file_exactly_matches.__annotations__['return'] == 'bool'
assert mrs_bot_main_post_confirmation_persistence.emergency_persist_confirmed_regular_post.__annotations__['return'] == 'RegularPostPersistenceResult'
assert 'historical_context_formatter' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr + result.stdout


@pytest.mark.parametrize("name", list(DEPENDENCIES))
def test_public_signatures_and_current_adapter_references(monkeypatch, name):
    signatures = {
        "atomic_json_file_exactly_matches": "(path: 'Path', value: 'object') -> 'bool'",
        "promote_main_post_attempt_to_confirmed_pending_schedule": (
            "(attempt: 'dict', *, post_id: 'str', confirmation_epoch: 'int', image_summary: 'str' = '') -> 'dict'"
        ),
        "save_regular_post_protected_state": (
            "(lines_used: 'set', images_used: 'set', state: 'dict', *, durable: 'bool') -> 'StateCommitProof'"
        ),
        "emergency_persist_confirmed_regular_post": "(lines_used: 'set', images_used: 'set', state: 'dict') -> 'RegularPostPersistenceResult'",
    }
    public, extracted = getattr(bot, name), getattr(owner, name)
    assert bot._main_post_confirmation_persistence is owner
    assert str(inspect.signature(public)) == signatures[name]
    assert public.__doc__ == extracted.__doc__
    signature = inspect.signature(extracted)
    assert signature.replace(parameters=[
        p for key, p in signature.parameters.items() if key not in DEPENDENCIES[name]
    ]) == inspect.signature(public)
    for dep in DEPENDENCIES[name]:
        assert signature.parameters[dep].kind is inspect.Parameter.KEYWORD_ONLY
        assert signature.parameters[dep].default is inspect.Parameter.empty
    args = tuple(object() for _ in inspect.signature(public).parameters.values()
                 if _.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD)
    kwargs = {key: object() for key, p in inspect.signature(public).parameters.items()
              if p.kind is inspect.Parameter.KEYWORD_ONLY and p.default is inspect.Parameter.empty}
    for explicit in (False, True):
        if name.startswith("promote_") and explicit:
            kwargs["image_summary"] = object()
        bound = inspect.signature(public).bind(*args, **kwargs)
        bound.apply_defaults()
        forwarded = {key: value for key, value in bound.arguments.items()
                     if inspect.signature(public).parameters[key].kind is inspect.Parameter.KEYWORD_ONLY}
        dependencies = {dep: object() for dep in DEPENDENCIES[name]}
        for dep, value in dependencies.items():
            monkeypatch.setattr(bot, dep, value)
        result = object()
        callback = Mock(return_value=result)
        monkeypatch.setattr(owner, name, callback)
        assert public(*args, **kwargs) is result
        assert callback.call_args.args == args
        assert all(a is b for a, b in zip(callback.call_args.args, args))
        assert callback.call_args.kwargs == {**forwarded, **dependencies}
        assert all(callback.call_args.kwargs[key] is value
                   for key, value in {**forwarded, **dependencies}.items())


def test_exact_canonical_bytes_require_secure_file_authority(tmp_path):
    import os

    path, value = tmp_path / "receipt.json", {"a": 1}
    path.write_bytes(bot.canonical_atomic_json_bytes(value))
    assert bot.atomic_json_file_exactly_matches(path, value) is True
    path.write_bytes(b'{"a":1}\n')
    assert bot.atomic_json_file_exactly_matches(path, value) is False
    path.write_bytes(bot.canonical_atomic_json_bytes(value))
    alias = tmp_path / "alias"
    alias.symlink_to(path)
    assert bot.atomic_json_file_exactly_matches(alias, value) is False
    alias.unlink()
    os.link(path, alias)
    assert bot.atomic_json_file_exactly_matches(path, value) is False
    alias.unlink()
    path.chmod(0o622)
    assert bot.atomic_json_file_exactly_matches(path, value) is False
    path.chmod(0o600)
    assert bot.atomic_json_file_exactly_matches(path, value) is True
    tmp_path.chmod(0o777)
    try:
        assert bot.atomic_json_file_exactly_matches(path, value) is False
    finally:
        tmp_path.chmod(0o700)


@pytest.mark.parametrize("boundary", ["canonical", "directory_identity", "file_identity", "require_current"])
@pytest.mark.parametrize("error_type", [ValueError, KeyboardInterrupt])
def test_exact_comparison_exception_scope(monkeypatch, tmp_path, boundary, error_type):
    import mrs_bot_state_generation as generation

    path, value = tmp_path / "receipt.json", {"a": 1}
    path.write_bytes(bot.canonical_atomic_json_bytes(value))
    error = error_type("comparison boundary")
    callback = Mock(side_effect=error)
    if boundary == "canonical":
        monkeypatch.setattr(bot, "canonical_atomic_json_bytes", callback)
    elif boundary == "require_current":
        monkeypatch.setattr(generation.StateCommitProof, boundary, callback)
    else:
        monkeypatch.setattr(generation, boundary, callback)
    if isinstance(error, Exception):
        assert bot.atomic_json_file_exactly_matches(path, value) is False
    else:
        with pytest.raises(error_type) as caught:
            bot.atomic_json_file_exactly_matches(path, value)
        assert caught.value is error
    callback.assert_called_once()


def _promotion(monkeypatch, lane="quote_image"):
    trace = Mock()
    attempt = {"lane": lane, "attempt_id": "synthetic-attempt"}
    pending = {"receipt_type": "confirmed_pending_schedule"}
    path = bot.REGULAR_POST_RECEIPT_FILE if lane == "quote_image" else bot.MEME_POST_RECEIPT_FILE
    binding = SimpleNamespace(receipt_document=attempt, receipt_bytes=b"attempt bytes")
    recovery = SimpleNamespace(
        details=SimpleNamespace(lane=lane, post_id="950001", confirmation_epoch=1800000010),
        source_binding=binding,
    )
    for name in DEPENDENCIES["promote_main_post_attempt_to_confirmed_pending_schedule"]:
        if name.isupper() or name in {
            "AmbiguousRemotePostOutcome", "BoundSourceReceiptTransitionError",
            "ConfirmedPendingScheduleDurabilityUncertain", "TransportJournalError",
        }:
            continue
        original = getattr(bot, name)
        callback = getattr(trace, name)
        if name in {"_set_ambiguous_remote_post_seen", "remote_write_safety_incident_is_latched"}:
            callback.side_effect = original
        monkeypatch.setattr(bot, name, callback)
    trace.build_confirmed_pending_schedule_receipt.return_value = pending
    trace.main_post_attempt_path.return_value = path
    trace.load_regular_post_receipt.return_value = ("sending", attempt)
    trace.load_meme_post_receipt.return_value = ("sending", attempt)
    trace.bind_confirmed_transport_source.return_value = recovery

    def canonical(value):
        assert value is attempt or value is pending
        return b"attempt bytes" if value is attempt else b"pending bytes"

    trace.canonical_atomic_json_bytes.side_effect = canonical
    return SimpleNamespace(trace=trace, attempt=attempt, pending=pending, path=path,
                           binding=binding, recovery=recovery, invoke=lambda: (
        bot.promote_main_post_attempt_to_confirmed_pending_schedule(
            attempt, post_id="950001", confirmation_epoch=1800000010,
        )
    ))


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_promotion_success_order_and_binding_references(monkeypatch, lane):
    p = _promotion(monkeypatch, lane)
    t = p.trace
    assert p.invoke() is p.pending
    assert t.mock_calls == [
        call.build_confirmed_pending_schedule_receipt(
            p.attempt, post_id="950001", confirmation_epoch=1800000010, image_summary=""),
        call.main_post_attempt_path(p.attempt),
        getattr(call, "load_regular_post_receipt" if lane == "quote_image" else "load_meme_post_receipt")(),
        call.journal_path_for_receipt(p.path),
        call.bind_confirmed_transport_source(
            journal_path=t.journal_path_for_receipt.return_value, receipt_path=p.path,
            validator_id=bot.TRANSPORT_SOURCE_VALIDATOR_ID, validator=bot.transport_source_semantic_validator),
        call.canonical_atomic_json_bytes(p.attempt), call.canonical_atomic_json_bytes(p.pending),
        call.transaction_mutation_authority("confirmed main-post source receipt promotion"),
        call.replace_bound_source_receipt(p.binding, b"pending bytes",
            mutation_authority=t.transaction_mutation_authority.return_value),
        call.log.warning(
            "Promoted main-post attempt to confirmed pending-schedule receipt "
            "lane=%s attempt_id=%s post_id=%s path=%s",
            lane, "synthetic-attempt", "950001", p.path),
    ]
    assert t.build_confirmed_pending_schedule_receipt.call_args.args[0] is p.attempt
    assert t.main_post_attempt_path.call_args.args[0] is p.attempt
    assert t.bind_confirmed_transport_source.call_args.kwargs["receipt_path"] is p.path
    assert t.replace_bound_source_receipt.call_args.args[0] is p.binding
    assert t.replace_bound_source_receipt.call_args.kwargs["mutation_authority"] is t.transaction_mutation_authority.return_value
    assert not hasattr(owner, "_AMBIGUOUS_REMOTE_POST_SEEN")


@pytest.mark.parametrize("status", ["absent", "sending"])
def test_sending_gate_short_circuits_before_binding(monkeypatch, status):
    p, compared = _promotion(monkeypatch), []

    class Current:
        def __ne__(self, other):
            compared.append(other)
            return True

    p.trace.load_regular_post_receipt.return_value = (status, Current())
    with pytest.raises(bot.AmbiguousRemotePostOutcome, match="attempt changed") as caught:
        p.invoke()
    assert caught.value.service == "x"
    assert compared == ([p.attempt] if status == "sending" else [])
    assert [entry[0] for entry in p.trace.mock_calls] == [
        "build_confirmed_pending_schedule_receipt", "main_post_attempt_path", "load_regular_post_receipt",
    ]
    assert bot._AMBIGUOUS_REMOTE_POST_SEEN is False


@pytest.mark.parametrize("mismatch", ["lane", "post_id", "confirmation_epoch", "receipt_document", "receipt_bytes"])
def test_lineage_checks_short_circuit_in_original_order(monkeypatch, mismatch):
    p, order = _promotion(monkeypatch), []
    expected = {"lane": "quote_image", "post_id": "950001", "confirmation_epoch": 1800000010,
                "receipt_document": p.attempt, "receipt_bytes": b"attempt bytes"}

    class Fields:
        def __getattr__(self, name):
            order.append(name)
            return object() if name == mismatch else expected[name]

    p.recovery.details = p.recovery.source_binding = Fields()
    with pytest.raises(bot.TransportJournalError, match="lineage changed"):
        p.invoke()
    assert order == list(expected)[:list(expected).index(mismatch) + 1]
    assert p.trace.canonical_atomic_json_bytes.call_count == int(mismatch == "receipt_bytes")
    p.trace.replace_bound_source_receipt.assert_not_called()
    assert bot._AMBIGUOUS_REMOTE_POST_SEEN is False


@pytest.mark.parametrize("boundary", [
    "build_confirmed_pending_schedule_receipt", "main_post_attempt_path", "load_regular_post_receipt",
    "bind_confirmed_transport_source", "canonical_atomic_json_bytes",
])
def test_prewrite_failures_escape_without_latching(monkeypatch, boundary):
    p, error = _promotion(monkeypatch), KeyboardInterrupt("pre-write")
    getattr(p.trace, boundary).side_effect = error
    with pytest.raises(KeyboardInterrupt) as caught:
        p.invoke()
    assert caught.value is error
    p.trace.replace_bound_source_receipt.assert_not_called()
    p.trace.remote_write_safety_incident_is_latched.assert_not_called()
    assert bot._AMBIGUOUS_REMOTE_POST_SEEN is False


def test_bound_transition_error_retains_journal_barrier_and_original_cause(monkeypatch):
    p, error = _promotion(monkeypatch), bot.BoundSourceReceiptTransitionError("exchange")
    p.trace.replace_bound_source_receipt.side_effect = error
    with pytest.raises(bot.ConfirmedPendingScheduleDurabilityUncertain) as caught:
        p.invoke()
    assert caught.value.__cause__ is error
    assert caught.value.durable_barrier is True
    assert p.trace.mock_calls[-2:] == [call.remote_write_safety_incident_is_latched(),
                                     call._set_ambiguous_remote_post_seen(True)]
    assert bot._AMBIGUOUS_REMOTE_POST_SEEN is True
    p.trace.atomic_json_file_exactly_matches.assert_not_called()
    p.trace.latch_confirmed_post_persistence_failure.assert_not_called()


@pytest.mark.parametrize("prior", [None, "_AMBIGUOUS_REMOTE_POST_SEEN", "_AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN"])
def test_exact_pending_repair_clears_only_new_latch_before_logs(monkeypatch, prior):
    p = _promotion(monkeypatch)
    if prior:
        monkeypatch.setattr(bot, prior, True)
    p.trace.replace_bound_source_receipt.side_effect = OSError("initial fsync")

    def exact(path, value):
        assert bot._AMBIGUOUS_REMOTE_POST_SEEN is True
        assert path is p.path and value is p.pending
        return True

    p.trace.atomic_json_file_exactly_matches.side_effect = exact
    assert p.invoke() is p.pending
    calls = p.trace.mock_calls
    start = next(i for i, entry in enumerate(calls) if entry[0] == "remote_write_safety_incident_is_latched")
    assert calls[start:-2] == [
        call.remote_write_safety_incident_is_latched(), call._set_ambiguous_remote_post_seen(True),
        call.atomic_json_file_exactly_matches(p.path, p.pending), call.fsync_parent_dir(p.path, strict=True),
        call.atomic_json_file_exactly_matches(p.path, p.pending),
        *([] if prior else [call._set_ambiguous_remote_post_seen(False)]),
    ]
    assert [entry[0] for entry in calls[-2:]] == ["log.warning", "log.warning"]
    assert bot._AMBIGUOUS_REMOTE_POST_SEEN is bool(prior)
    p.trace.latch_confirmed_post_persistence_failure.assert_not_called()


@pytest.mark.parametrize("prior", [False, True])
def test_exact_prior_attempt_conditionally_clears_then_reraises_same_error(monkeypatch, prior):
    p, error = _promotion(monkeypatch), KeyboardInterrupt("before replacement")
    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", prior)
    p.trace.replace_bound_source_receipt.side_effect = error
    p.trace.atomic_json_file_exactly_matches.side_effect = [False, True]
    with pytest.raises(KeyboardInterrupt) as caught:
        p.invoke()
    assert caught.value is error
    tail = [call.remote_write_safety_incident_is_latched(), call._set_ambiguous_remote_post_seen(True),
            call.atomic_json_file_exactly_matches(p.path, p.pending),
            call.atomic_json_file_exactly_matches(p.path, p.attempt),
            *([] if prior else [call._set_ambiguous_remote_post_seen(False)])]
    assert p.trace.mock_calls[-len(tail):] == tail
    assert bot._AMBIGUOUS_REMOTE_POST_SEEN is prior
    p.trace.fsync_parent_dir.assert_not_called()
    p.trace.latch_confirmed_post_persistence_failure.assert_not_called()
    p.trace.log.warning.assert_not_called()


@pytest.mark.parametrize("failure", ["fsync", "changed", "recheck"])
def test_pending_repair_failures_preserve_components_barrier_and_write_cause(monkeypatch, failure):
    p, error, barrier = _promotion(monkeypatch), OSError("write"), object()
    uncertain = Mock(wraps=bot.ConfirmedPendingScheduleDurabilityUncertain)
    monkeypatch.setattr(bot, "ConfirmedPendingScheduleDurabilityUncertain", uncertain)
    p.trace.replace_bound_source_receipt.side_effect = error
    p.trace.atomic_json_file_exactly_matches.side_effect = [True, False if failure == "changed" else KeyboardInterrupt("recheck")]
    if failure == "fsync":
        p.trace.fsync_parent_dir.side_effect = KeyboardInterrupt("sync")
    p.trace.latch_confirmed_post_persistence_failure.return_value = barrier
    with pytest.raises(bot.UnrecoverableConfirmedPostPersistenceError) as caught:
        p.invoke()
    assert caught.value.__cause__ is error
    assert uncertain.call_args.kwargs["durable_barrier"] is barrier
    p.trace.latch_confirmed_post_persistence_failure.assert_called_once_with(
        lane="quote_image", post_id="950001",
        failure_components=["pending_schedule_parent_fsync", "RuntimeError" if failure == "changed" else "KeyboardInterrupt"],
    )
    assert p.trace.atomic_json_file_exactly_matches.call_count == (1 if failure == "fsync" else 2)
    p.trace._set_ambiguous_remote_post_seen.assert_called_once_with(True)
    assert bot._AMBIGUOUS_REMOTE_POST_SEEN is True


@pytest.mark.parametrize("boundary", ["pending_bytes", "authority", "replace"])
def test_write_expression_failures_latch_before_unverified_identity_recovery(monkeypatch, boundary):
    p, error = _promotion(monkeypatch), KeyboardInterrupt("write expression")
    if boundary == "pending_bytes":
        p.trace.canonical_atomic_json_bytes.side_effect = [b"attempt bytes", error]
    else:
        getattr(p.trace, "transaction_mutation_authority" if boundary == "authority" else "replace_bound_source_receipt").side_effect = error
    p.trace.atomic_json_file_exactly_matches.side_effect = [False, False]
    p.trace.latch_confirmed_post_persistence_failure.return_value = False
    with pytest.raises(bot.ConfirmedPendingScheduleDurabilityUncertain) as caught:
        p.invoke()
    assert caught.value.__cause__ is error and caught.value.durable_barrier is False
    assert p.trace.mock_calls[-5:] == [
        call.remote_write_safety_incident_is_latched(), call._set_ambiguous_remote_post_seen(True),
        call.atomic_json_file_exactly_matches(p.path, p.pending), call.atomic_json_file_exactly_matches(p.path, p.attempt),
        call.latch_confirmed_post_persistence_failure(lane="quote_image", post_id="950001",
            failure_components=["pending_schedule_receipt_identity", "KeyboardInterrupt"]),
    ]
    assert bot._AMBIGUOUS_REMOTE_POST_SEEN is True


@pytest.mark.parametrize("boundary", ["observation", "inspection", "marker", "repair_log"])
def test_recovery_diagnostic_failures_keep_native_scope(monkeypatch, boundary):
    p, error = _promotion(monkeypatch), ValueError("recovery diagnostic")
    p.trace.replace_bound_source_receipt.side_effect = OSError("initial write")
    p.trace.atomic_json_file_exactly_matches.side_effect = [boundary == "repair_log", boundary == "repair_log"]
    callback = {"observation": p.trace.remote_write_safety_incident_is_latched,
                "inspection": p.trace.atomic_json_file_exactly_matches,
                "marker": p.trace.latch_confirmed_post_persistence_failure,
                "repair_log": p.trace.log.warning}[boundary]
    callback.side_effect = error
    with pytest.raises(ValueError) as caught:
        p.invoke()
    assert caught.value is error
    assert bot._AMBIGUOUS_REMOTE_POST_SEEN is (boundary in {"inspection", "marker"})
    if boundary == "observation":
        p.trace._set_ambiguous_remote_post_seen.assert_not_called()
    if boundary == "repair_log":
        p.trace.log.warning.assert_called_once()


@pytest.mark.parametrize("failure", [None, "quote", "convert", "image", "state"])
def test_protected_save_order_references_and_native_partial_progress(monkeypatch, failure):
    import mrs_bot_state_generation as generation

    events, lines, state, durable = [], set(), {}, object()
    state_proof, history_proof = object(), object()
    error = KeyboardInterrupt("state") if failure == "state" else ValueError("save")

    def record(name):
        events.append(name)
        if failure == name:
            raise error

    class Image:
        def __str__(self):
            record("convert")
            return "synthetic.jpg"

    images = {Image()}

    def quote(path, value, **kwargs):
        assert path is bot.LINES_USED_FILE and value is lines and kwargs["durable"] is durable
        record("quote")

    def image(path, value, **kwargs):
        assert path is bot.IMAGES_USED_FILE and value == {"synthetic.jpg"} and value is not images
        assert kwargs["durable"] is durable
        record("image")

    def save(value, **kwargs):
        assert value is state and kwargs["durable"] is durable
        record("state")
        return state_proof

    def protect(proof, files):
        assert proof is state_proof
        assert files == ((bot.LINES_USED_FILE, b"[]\n"),
                         (bot.IMAGES_USED_FILE, b'[\n  "synthetic.jpg"\n]\n'))
        record("proof")
        return history_proof

    monkeypatch.setattr(bot, "save_quote_used_hashes", quote)
    monkeypatch.setattr(bot, "save_image_used_basenames", image)
    monkeypatch.setattr(bot, "save_state", save)
    monkeypatch.setattr(generation, "protect_history_files", protect)
    if failure:
        with pytest.raises(type(error)) as caught:
            bot.save_regular_post_protected_state(lines, images, state, durable=durable)
        assert caught.value is error
    else:
        assert bot.save_regular_post_protected_state(lines, images, state, durable=durable) is history_proof
    order = ["quote", "convert", "image", "state", "convert", "proof"]
    assert events == (order[:order.index(failure) + 1] if failure else order)


def _persistence(monkeypatch):
    import mrs_bot_state_generation as generation

    trace = Mock()
    monkeypatch.setattr(generation, "protect_history_files", trace.protect_history_files)
    for name in ("save_quote_used_hashes", "save_image_used_basenames", "save_state", "json_file_matches", "log"):
        monkeypatch.setattr(bot, name, getattr(trace, name))
    return trace


@pytest.mark.parametrize("component,backup,canonical", [
    ("quote_history", True, True), ("image_history", True, True),
    ("state", False, True), ("state", True, False), ("state", True, True),
])
def test_emergency_backup_exception_is_state_only_and_exactly_checked(monkeypatch, component, backup, canonical):
    t, lines, state = _persistence(monkeypatch), set(), {}
    callbacks = {"quote_history": t.save_quote_used_hashes,
                 "image_history": t.save_image_used_basenames, "state": t.save_state}
    error = (bot.StateBackupWriteError if backup else OSError)("component")
    proof = object()
    if backup:
        error.commit_proof = proof
    callbacks[component].side_effect = error
    t.json_file_matches.return_value = canonical
    first = bot.emergency_persist_confirmed_regular_post(lines, {7, "7"}, state)
    second = bot.emergency_persist_confirmed_regular_post(lines, {7, "7"}, state)
    accepted = component == "state" and backup and canonical
    assert first == second and first is not second
    assert first.failures == (() if accepted else (component,))
    assert first.commit_proof is (t.protect_history_files.return_value if accepted else None)
    if accepted:
        t.protect_history_files.assert_called_with(proof, (
            (bot.LINES_USED_FILE, b"[]\n"),
            (bot.IMAGES_USED_FILE, b'[\n  "7"\n]\n'),
        ))
    assert t.json_file_matches.call_count == (2 if component == "state" and backup else 0)
    if component == "state" and backup:
        t.json_file_matches.assert_called_with(bot.STATE_FILE, state, commit_proof=proof)
        assert t.json_file_matches.call_args.args[1] is state
    names = ["save_quote_used_hashes", "save_image_used_basenames", "save_state"]
    position = names.index({"quote_history": "save_quote_used_hashes", "image_history": "save_image_used_basenames", "state": "save_state"}[component]) + 1
    names[position:position] = (["json_file_matches"] if component == "state" and backup else []) + ["log.warning" if accepted else "log.critical"]
    assert [entry[0] for entry in t.mock_calls] == (names + (["protect_history_files"] if accepted else [])) * 2
    assert t.save_quote_used_hashes.call_args.args[1] is lines
    t.save_image_used_basenames.assert_called_with(bot.IMAGES_USED_FILE, {"7"}, durable=True)
    assert t.save_state.call_args.args[0] is state
    assert all(callback.call_args.kwargs == {"durable": True} for callback in callbacks.values())
    assert (t.log.warning if accepted else t.log.critical).call_args.kwargs == {"exc_info": True}


@pytest.mark.parametrize("error_type", [ValueError, KeyboardInterrupt])
def test_emergency_lazy_image_conversion_append_before_log_and_baseexception_escape(monkeypatch, error_type):
    t, events, error = _persistence(monkeypatch), [], error_type("image conversion")
    t.save_quote_used_hashes.side_effect = OSError("quote")

    class Image:
        def __str__(self):
            events.append("convert")
            raise error

    def critical(message, component, *, exc_info):
        assert exc_info is True
        assert message == "Emergency persistence component failed after confirmed regular post: %s"
        events.append((component, list(inspect.currentframe().f_back.f_locals["failures"])))

    monkeypatch.setattr(bot, "log", SimpleNamespace(critical=critical))
    if error_type is ValueError:
        result = bot.emergency_persist_confirmed_regular_post(set(), {Image()}, {})
        assert result.failures == ("quote_history", "image_history")
        assert result.commit_proof is None
        assert events == [("quote_history", ["quote_history"]), "convert",
                          ("image_history", ["quote_history", "image_history"])]
        t.save_state.assert_called_once()
    else:
        with pytest.raises(KeyboardInterrupt) as caught:
            bot.emergency_persist_confirmed_regular_post(set(), {Image()}, {})
        assert caught.value is error
        assert events == [("quote_history", ["quote_history"]), "convert"]
        t.save_state.assert_not_called()
    t.save_image_used_basenames.assert_not_called()
    t.json_file_matches.assert_not_called()


@pytest.mark.parametrize("boundary", ["match", "warning", "critical"])
def test_emergency_diagnostic_errors_escape_natively(monkeypatch, boundary):
    t, error = _persistence(monkeypatch), KeyboardInterrupt("diagnostic")
    if boundary == "critical":
        t.save_quote_used_hashes.side_effect = OSError("quote")
        t.log.critical.side_effect = error
    else:
        t.save_state.side_effect = bot.StateBackupWriteError("backup")
        t.save_state.side_effect.commit_proof = object()
        t.json_file_matches.return_value = True
        (t.json_file_matches if boundary == "match" else t.log.warning).side_effect = error
    with pytest.raises(KeyboardInterrupt) as caught:
        bot.emergency_persist_confirmed_regular_post(set(), set(), {})
    assert caught.value is error
    if boundary == "critical":
        t.save_image_used_basenames.assert_not_called()
        t.save_state.assert_not_called()


def test_emergency_backup_error_without_exact_commit_proof_is_not_success(monkeypatch):
    trace = _persistence(monkeypatch)
    trace.save_state.side_effect = bot.StateBackupWriteError("no exact commit proof")
    trace.json_file_matches.return_value = True
    result = bot.emergency_persist_confirmed_regular_post(set(), set(), {})
    assert result.failures == ("state",) and result.commit_proof is None
    trace.json_file_matches.assert_not_called()
    trace.log.critical.assert_called_once()


def test_emergency_result_carries_exact_history_proof_or_fails_closed(monkeypatch):
    trace = _persistence(monkeypatch)
    state = {}
    result = bot.emergency_persist_confirmed_regular_post(set(), set(), state)
    assert result.failures == ()
    assert result.commit_proof is trace.protect_history_files.return_value
    trace.protect_history_files.assert_called_once_with(trace.save_state.return_value, (
        (bot.LINES_USED_FILE, b"[]\n"), (bot.IMAGES_USED_FILE, b"[]\n"),
    ))
    trace.protect_history_files.side_effect = OSError("history replaced before proof")
    result = bot.emergency_persist_confirmed_regular_post(set(), set(), state)
    assert result.failures == ("protected_commit_proof",)
    assert result.commit_proof is None
    trace.log.critical.assert_called_once_with("Emergency protected state proof failed", exc_info=True)
