from __future__ import annotations

import inspect
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrsMThatcher2 as bot
from tests.helpers.bot_fixtures import isolate_regular_post_receipt  # noqa: F401

DEPENDENCIES = {'remote_write_safety_marker_path_present_or_unsafe': ['AMBIGUOUS_POST_OUTCOME_FILE',
                                                       'AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE',
                                                       'latch_remote_write_safety_marker_observation',
                                                       'log',
                                                       'os'],
 'require_remote_write_marker_removal_protocol': ['require_instance_lock_for_remote_write'],
 'read_remote_write_safety_marker_snapshot': ['AMBIGUOUS_POST_OUTCOME_FILE',
                                              'Path',
                                              'REMOTE_WRITE_SAFETY_MARKER_MAX_BYTES',
                                              'latch_remote_write_safety_marker_observation',
                                              'os',
                                              'stat'],
 'read_remote_write_safety_barrier_snapshot': ['AMBIGUOUS_POST_OUTCOME_FILE',
                                               'AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE',
                                               'latch_remote_write_safety_marker_observation',
                                               'os',
                                               'read_remote_write_safety_marker_snapshot',
                                               'stat'],
 'acknowledge_durable_remote_write_safety_marker': ['AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE',
                                                    'fsync_parent_dir',
                                                    'read_remote_write_safety_barrier_snapshot',
                                                    'require_remote_write_marker_removal_protocol'],
 'ensure_durable_remote_write_safety_marker': ['AMBIGUOUS_POST_OUTCOME_FILE',
                                               'AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE',
                                               'acknowledge_durable_remote_write_safety_marker',
                                               'atomic_write_json',
                                               'canonical_atomic_json_bytes',
                                               'log',
                                               'os',
                                               'remote_write_safety_protocol_is_active'],
 'durable_remote_write_safety_barrier_exists': ['MEDIA_UPLOAD_RECEIPT_FILE',
                                                'durable_remote_write_safety_marker_exists',
                                                'historical_context_reply_store',
                                                'load_confirmed_reply_receipt',
                                                'load_meme_post_receipt',
                                                'load_regular_post_receipt',
                                                'media_upload_has_valid_restart_barrier',
                                                'release_retained_sigint_deferral_after_durable_barrier',
                                                'remote_write_safety_protocol_is_active',
                                                'remote_write_transport_journal_paths',
                                                'transport_journal_has_valid_restart_barrier']}
SIGNATURES = {'remote_write_safety_marker_path_present_or_unsafe': "() -> 'bool'",
 'require_remote_write_marker_removal_protocol': "() -> 'None'",
 'read_remote_write_safety_marker_snapshot': "(path: 'Path | None' = None, *, "
                                             'accepted_link_counts: '
                                             "'frozenset[int]' = "
                                             "frozenset({1})) -> 'tuple[int, "
                                             "int, int, int, bytes]'",
 'read_remote_write_safety_barrier_snapshot': "() -> 'tuple[Path, tuple[int, "
                                              "int, int, int, bytes]]'",
 'acknowledge_durable_remote_write_safety_marker': "(*, expected_bytes: 'bytes "
                                                   "| None' = None) -> 'bool'",
 'ensure_durable_remote_write_safety_marker': "(marker: 'dict') -> 'bool'",
 'durable_remote_write_safety_barrier_exists': "() -> 'bool'"}


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, io, logging, os, random, socket, sys, time, typing
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('Safety-marker snapshots import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'historical_context_formatter', 'historical_context_outbox'} or name.startswith('mrs_bot_') and name != 'mrs_bot_safety_marker_snapshots':
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
import mrs_bot_safety_marker_snapshots
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


@pytest.mark.parametrize("name", list(DEPENDENCIES))
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

            patch.setattr(bot, "_safety_marker_snapshots", SimpleNamespace(**{name: capture}))
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
            patch.setattr(bot, "_safety_marker_snapshots", SimpleNamespace(**{name: Mock(side_effect=failure)}))
            with pytest.raises(TypeError) as caught:
                adapter(**provided)
            assert caught.value is failure


def test_path_probe_inspects_original_then_successor_and_latches_before_logging(monkeypatch):
    events = Mock()
    original = bot.AMBIGUOUS_POST_OUTCOME_FILE
    successor = bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE
    events.lstat.side_effect = [object(), OSError("second namespace")]
    monkeypatch.setattr(bot, "os", SimpleNamespace(lstat=events.lstat))
    monkeypatch.setattr(bot, "latch_remote_write_safety_marker_observation", events.latch)
    monkeypatch.setattr(bot, "log", events)
    assert bot.remote_write_safety_marker_path_present_or_unsafe() is True
    assert events.mock_calls[:4] == [
        call.lstat(original), call.latch(), call.lstat(successor), call.latch(),
    ]
    assert events.critical.call_args.args[1] is successor
    assert events.critical.call_args.kwargs == {"exc_info": True}

    events.reset_mock()
    events.lstat.side_effect = [FileNotFoundError(), FileNotFoundError()]
    assert bot.remote_write_safety_marker_path_present_or_unsafe() is False
    assert events.mock_calls == [call.lstat(original), call.lstat(successor)]


@pytest.mark.parametrize("source", ["probe", "latch", "logger"])
def test_path_probe_preserves_native_interrupt_and_callback_errors(monkeypatch, source):
    events = Mock()
    failure = KeyboardInterrupt() if source == "probe" else TypeError(source)
    events.lstat.side_effect = failure if source == "probe" else OSError("inspect")
    if source != "probe":
        getattr(events, "latch" if source == "latch" else "critical").side_effect = failure
    monkeypatch.setattr(bot, "os", SimpleNamespace(lstat=events.lstat))
    monkeypatch.setattr(bot, "latch_remote_write_safety_marker_observation", events.latch)
    monkeypatch.setattr(bot, "log", events)
    with pytest.raises(type(failure)) as caught:
        bot.remote_write_safety_marker_path_present_or_unsafe()
    assert caught.value is failure
    assert events.lstat.call_args_list == [call(bot.AMBIGUOUS_POST_OUTCOME_FILE)]
    assert events.latch.call_count == (source != "probe")
    assert events.critical.call_count == (source == "logger")


def test_removal_protocol_uses_current_lock_callback_and_exact_operation(monkeypatch):
    callback = Mock()
    monkeypatch.setattr(bot, "require_instance_lock_for_remote_write", callback)
    assert bot.require_remote_write_marker_removal_protocol() is None
    callback.assert_called_once_with("Remote-write safety marker acknowledgement")
    failure = OSError("lock proof lost")
    callback.side_effect = failure
    with pytest.raises(OSError) as caught:
        bot.require_remote_write_marker_removal_protocol()
    assert caught.value is failure


def _metadata(**changes):
    values = dict(st_dev=11, st_ino=22, st_mode=stat.S_IFREG | 0o600,
                  st_nlink=1, st_size=3, st_ctime_ns=33, st_mtime_ns=44)
    return SimpleNamespace(**(values | changes))


def _snapshot_trace(monkeypatch):
    events = Mock()
    metadata = _metadata()
    events.lstat.return_value = metadata
    events.fstat.return_value = metadata
    events.open.return_value = 19
    events.read.side_effect = [b"a", b"bc", b""]
    filesystem = SimpleNamespace(
        O_RDONLY=1, O_CLOEXEC=2, O_NOFOLLOW=4, O_NONBLOCK=8,
        **{name: getattr(events, name) for name in
           ("lstat", "open", "fstat", "read", "fsync", "close")},
    )
    monkeypatch.setattr(bot, "os", filesystem)
    monkeypatch.setattr(bot, "REMOTE_WRITE_SAFETY_MARKER_MAX_BYTES", 4)
    monkeypatch.setattr(bot, "latch_remote_write_safety_marker_observation", events.latch)
    return events, filesystem, metadata


@pytest.mark.parametrize("explicit_path", [False, True])
def test_snapshot_preserves_resolution_flags_bounded_reads_sync_and_close_order(monkeypatch, explicit_path):
    events, filesystem, _ = _snapshot_trace(monkeypatch)
    path = bot.AMBIGUOUS_POST_OUTCOME_FILE
    prefix = []
    if explicit_path:
        del filesystem.O_CLOEXEC
        supplied = object()
        events.resolve.return_value = path
        monkeypatch.setattr(bot, "Path", events.resolve)
        result = bot.read_remote_write_safety_marker_snapshot(supplied)
        prefix = [call.resolve(supplied)]
    else:
        result = bot.read_remote_write_safety_marker_snapshot()
    assert result == (11, 22, stat.S_IFREG, 33, b"abc")
    assert events.mock_calls == prefix + [
        call.lstat(path), call.latch(), call.open(path, 5 if explicit_path else 7),
        call.fstat(19), call.read(19, 5), call.read(19, 4), call.read(19, 2),
        call.fstat(19), call.fsync(19), call.fstat(19), call.lstat(path), call.close(19),
    ]


@pytest.mark.parametrize("failure_at", ["nofollow", "open", "read", "read_metadata", "fsync", "final_path", "length"])
def test_snapshot_latches_before_failure_and_closes_only_open_descriptors(monkeypatch, failure_at):
    events, filesystem, metadata = _snapshot_trace(monkeypatch)
    failure = OSError(failure_at)
    native = failure_at in {"open", "read", "fsync", "final_path"}
    if failure_at == "nofollow":
        filesystem.O_NOFOLLOW = 0
    elif failure_at == "read_metadata":
        events.fstat.side_effect = [metadata, _metadata(st_mtime_ns=45)]
    elif failure_at == "final_path":
        events.lstat.side_effect = [metadata, failure]
    elif failure_at == "length":
        metadata.st_size = 4
    else:
        getattr(events, failure_at).side_effect = failure
    with pytest.raises(OSError if native else RuntimeError) as caught:
        bot.read_remote_write_safety_marker_snapshot()
    if native:
        assert caught.value is failure
    assert events.mock_calls[:2] == [call.lstat(bot.AMBIGUOUS_POST_OUTCOME_FILE), call.latch()]
    assert events.close.call_count == (failure_at not in {"nofollow", "open"})
    if events.close.called:
        assert events.mock_calls[-1] == call.close(19)
    assert events.fsync.call_count == (failure_at in {"fsync", "final_path", "length"})


@pytest.mark.parametrize("pair", [False, True])
def test_barrier_snapshot_preserves_pair_order_or_successor_default_and_references(monkeypatch, pair):
    events = Mock()
    original = bot.AMBIGUOUS_POST_OUTCOME_FILE
    successor = bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE
    snapshot = (11, 22, stat.S_IFREG, 33, b"abc")
    events.lstat.side_effect = [_metadata(st_nlink=2) if pair else FileNotFoundError(),
                                _metadata(st_nlink=2 if pair else 1)]
    events.snapshot.side_effect = [snapshot, tuple(list(snapshot))]
    monkeypatch.setattr(bot, "os", SimpleNamespace(lstat=events.lstat))
    monkeypatch.setattr(bot, "latch_remote_write_safety_marker_observation", events.latch)
    monkeypatch.setattr(bot, "read_remote_write_safety_marker_snapshot", events.snapshot)
    path, result = bot.read_remote_write_safety_barrier_snapshot()
    assert path is (original if pair else successor)
    assert result is snapshot
    expected = [call.lstat(original), call.lstat(successor), call.latch()]
    expected += ([call.snapshot(original, accepted_link_counts=frozenset({2})),
                  call.snapshot(successor, accepted_link_counts=frozenset({2}))]
                 if pair else [call.snapshot(successor)])
    assert events.mock_calls == expected


@pytest.mark.parametrize("namespace", ["absent", "legacy", "second_error", "unequal_pair"])
def test_barrier_snapshot_latches_before_interpretation_and_rejects_unsupported_states(monkeypatch, namespace):
    events = Mock()
    failure = OSError("second namespace")
    observations = {
        "absent": [FileNotFoundError(), FileNotFoundError()],
        "legacy": [_metadata(), FileNotFoundError()],
        "second_error": [_metadata(), failure],
        "unequal_pair": [_metadata(st_nlink=2), _metadata(st_nlink=2)],
    }
    events.lstat.side_effect = observations[namespace]
    events.snapshot.side_effect = [(11, 22, stat.S_IFREG, 33, b"abc"),
                                   (11, 22, stat.S_IFREG, 33, b"def")]
    monkeypatch.setattr(bot, "os", SimpleNamespace(lstat=events.lstat))
    monkeypatch.setattr(bot, "latch_remote_write_safety_marker_observation", events.latch)
    monkeypatch.setattr(bot, "read_remote_write_safety_marker_snapshot", events.snapshot)
    expected_error = {"absent": FileNotFoundError, "second_error": OSError}.get(namespace, RuntimeError)
    with pytest.raises(expected_error) as caught:
        bot.read_remote_write_safety_barrier_snapshot()
    if namespace == "second_error":
        assert caught.value is failure
    prefix = [call.lstat(bot.AMBIGUOUS_POST_OUTCOME_FILE),
              call.lstat(bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE)]
    if namespace != "absent":
        prefix.append(call.latch())
    assert events.mock_calls[:len(prefix)] == prefix
    assert events.snapshot.call_count == (2 if namespace == "unequal_pair" else 0)
    if namespace != "unequal_pair":
        assert events.mock_calls == prefix


def _acknowledgement_trace(monkeypatch):
    events = Mock()
    snapshot = (11, 22, stat.S_IFREG, 33, b"abc")
    events.snapshot.return_value = (bot.AMBIGUOUS_POST_OUTCOME_FILE, snapshot)
    monkeypatch.setattr(bot, "require_remote_write_marker_removal_protocol", events.lock)
    monkeypatch.setattr(bot, "read_remote_write_safety_barrier_snapshot", events.snapshot)
    monkeypatch.setattr(bot, "fsync_parent_dir", events.sync)
    return events, snapshot


@pytest.mark.parametrize("successor_survives", [False, True])
def test_acknowledgement_checks_lock_expected_bytes_strict_sync_and_lock_again(monkeypatch, successor_survives):
    events, before = _acknowledgement_trace(monkeypatch)
    original = bot.AMBIGUOUS_POST_OUTCOME_FILE
    after_path = bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE if successor_survives else original
    after = before[:3] + (99, before[-1]) if successor_survives else before
    events.snapshot.side_effect = [(original, before), (after_path, after)]
    assert bot.acknowledge_durable_remote_write_safety_marker(expected_bytes=before[-1]) is True
    assert events.mock_calls == [
        call.lock(), call.snapshot(), call.sync(original, strict=True), call.snapshot(), call.lock(),
    ]


@pytest.mark.parametrize("failure_at", ["lock_before", "snapshot_before", "expected", "sync", "snapshot_after", "changed", "lock_after"])
def test_acknowledgement_stops_at_each_native_failure_or_identity_gate(monkeypatch, failure_at):
    events, before = _acknowledgement_trace(monkeypatch)
    path = bot.AMBIGUOUS_POST_OUTCOME_FILE
    failure = OSError(failure_at)
    expected = before[-1]
    if failure_at == "expected":
        expected = b"other incident"
    elif failure_at == "changed":
        events.snapshot.side_effect = [(path, before), (path, before[:3] + (99, before[-1]))]
    elif failure_at.startswith("lock_"):
        events.lock.side_effect = [failure] if failure_at == "lock_before" else [None, failure]
    elif failure_at.startswith("snapshot_"):
        events.snapshot.side_effect = [failure] if failure_at == "snapshot_before" else [(path, before), failure]
    else:
        events.sync.side_effect = failure
    native = failure_at not in {"expected", "changed"}
    with pytest.raises(OSError if native else RuntimeError) as caught:
        bot.acknowledge_durable_remote_write_safety_marker(expected_bytes=expected)
    if native:
        assert caught.value is failure
    stop = {"lock_before": 1, "snapshot_before": 2, "expected": 2, "sync": 3,
            "snapshot_after": 4, "changed": 4, "lock_after": 5}[failure_at]
    assert events.mock_calls == [
        call.lock(), call.snapshot(), call.sync(path, strict=True), call.snapshot(), call.lock(),
    ][:stop]


def _ensure_trace(monkeypatch):
    events = Mock()
    events.protocol.return_value = True
    events.canonical.return_value = b"exact incident bytes"
    events.lstat.side_effect = [FileNotFoundError(), FileNotFoundError()]
    monkeypatch.setattr(bot, "remote_write_safety_protocol_is_active", events.protocol)
    monkeypatch.setattr(bot, "canonical_atomic_json_bytes", events.canonical)
    monkeypatch.setattr(bot, "os", SimpleNamespace(lstat=events.lstat, link=events.link))
    monkeypatch.setattr(bot, "atomic_write_json", events.write)
    monkeypatch.setattr(bot, "acknowledge_durable_remote_write_safety_marker", events.ack)
    monkeypatch.setattr(bot, "log", events)
    return events


@pytest.mark.parametrize("link_error", [None, FileExistsError, OSError])
def test_ensure_acknowledges_successor_before_optional_link_and_returns_final_authority(monkeypatch, link_error):
    events = _ensure_trace(monkeypatch)
    marker = {"nested": []}
    result = {"raw": []}
    expected_bytes = events.canonical.return_value
    events.ack.side_effect = [False, result]
    if link_error is not None:
        events.write.side_effect = OSError("writer returned after replacement")
        events.link.side_effect = link_error("optional legacy name")
    original = bot.AMBIGUOUS_POST_OUTCOME_FILE
    successor = bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE
    assert bot.ensure_durable_remote_write_safety_marker(marker) is result
    expected = [
        call.protocol(), call.canonical(marker), call.lstat(original), call.lstat(successor),
        call.write(successor, marker, durable=True), call.ack(expected_bytes=expected_bytes),
        call.link(successor, original, follow_symlinks=False),
    ]
    if link_error is OSError:
        expected.append(call.warning(
            "Could not add the legacy remote-write safety marker name; retaining the successor-only barrier",
            exc_info=True,
        ))
    expected.append(call.ack(expected_bytes=expected_bytes))
    assert events.mock_calls == expected
    assert events.canonical.call_args.args[0] is marker
    assert events.write.call_args.args[1] is marker
    assert all(c.kwargs["expected_bytes"] is expected_bytes for c in events.ack.call_args_list)


@pytest.mark.parametrize("existing", ["original", "successor"])
def test_ensure_observes_both_existing_names_without_writing_or_linking(monkeypatch, existing):
    events = _ensure_trace(monkeypatch)
    marker = {}
    events.lstat.side_effect = ([object(), FileNotFoundError()] if existing == "original"
                                else [FileNotFoundError(), object()])
    result = object()
    events.ack.return_value = result
    assert bot.ensure_durable_remote_write_safety_marker(marker) is result
    assert events.mock_calls == [
        call.protocol(), call.canonical(marker), call.lstat(bot.AMBIGUOUS_POST_OUTCOME_FILE),
        call.lstat(bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE),
        call.ack(expected_bytes=events.canonical.return_value),
    ]


@pytest.mark.parametrize("failure_at", ["inactive", "second_probe", "first_ack"])
def test_ensure_preserves_protocol_probe_and_acknowledgement_failure_boundaries(monkeypatch, failure_at):
    events = _ensure_trace(monkeypatch)
    marker = {}
    failure = PermissionError(failure_at)
    if failure_at == "inactive":
        events.protocol.return_value = False
    elif failure_at == "second_probe":
        events.lstat.side_effect = [object(), failure]
    else:
        events.ack.side_effect = failure
    with pytest.raises(RuntimeError if failure_at == "inactive" else PermissionError) as caught:
        bot.ensure_durable_remote_write_safety_marker(marker)
    if failure_at == "inactive":
        assert events.mock_calls == [call.protocol()]
    else:
        assert caught.value is failure
        assert events.lstat.call_args_list == [call(bot.AMBIGUOUS_POST_OUTCOME_FILE),
                                              call(bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE)]
        assert events.ack.call_count == (failure_at == "first_ack")
    events.link.assert_not_called()


def _durable_trace(monkeypatch):
    events = Mock()
    events.protocol.return_value = True
    events.marker.return_value = False
    events.paths.return_value = (Path("first.transport.json"), Path("second.transport.json"))
    events.journal.return_value = events.media.return_value = False
    events.store.return_value = SimpleNamespace(_load_receipt_safely=events.load)
    events.load.return_value = None
    events.sending.return_value = events.valid.return_value = False
    events.regular.return_value = events.meme.return_value = events.reply.return_value = ("invalid", None)
    callbacks = {
        "remote_write_safety_protocol_is_active": "protocol",
        "durable_remote_write_safety_marker_exists": "marker",
        "remote_write_transport_journal_paths": "paths",
        "transport_journal_has_valid_restart_barrier": "journal",
        "media_upload_has_valid_restart_barrier": "media",
        "historical_context_reply_store": "store",
        "load_regular_post_receipt": "regular",
        "load_meme_post_receipt": "meme",
        "load_confirmed_reply_receipt": "reply",
        "release_retained_sigint_deferral_after_durable_barrier": "release",
    }
    for name, callback in callbacks.items():
        monkeypatch.setattr(bot, name, getattr(events, callback))
    monkeypatch.setitem(sys.modules, "historical_context_formatter", SimpleNamespace(
        HistoricalContextReplyStore=SimpleNamespace(
            _valid_sending_receipt=events.sending, _valid_receipt=events.valid,
        ),
    ))
    return events


def _durable_prefix(events):
    first, second = events.paths.return_value
    return [call.protocol(), call.marker(), call.paths(), call.journal(first), call.journal(second),
            call.media(bot.MEDIA_UPLOAD_RECEIPT_FILE), call.store(), call.load()]


@pytest.mark.parametrize("active", [False, True])
def test_durable_evidence_keeps_marker_first_and_inactive_protocol_still_checks_journals(monkeypatch, active):
    events = _durable_trace(monkeypatch)
    events.protocol.return_value = active
    events.marker.return_value = events.journal.return_value = True
    assert bot.durable_remote_write_safety_barrier_exists() is True
    assert events.mock_calls == ([call.protocol(), call.marker()] if active else [
        call.protocol(), call.paths(), call.journal(events.paths.return_value[0]), call.release(),
    ])


@pytest.mark.parametrize("during_iteration", [False, True])
def test_durable_journal_iterable_failures_remain_outside_per_item_catches(monkeypatch, during_iteration):
    events = _durable_trace(monkeypatch)
    failure = OSError("journal iterable")

    def broken_iterator():
        raise failure
        yield  # The original for-loop must advance the iterable outside its try.

    if during_iteration:
        events.paths.return_value = broken_iterator()
    else:
        events.paths.side_effect = failure
    with pytest.raises(OSError) as caught:
        bot.durable_remote_write_safety_barrier_exists()
    assert caught.value is failure
    assert events.mock_calls == [call.protocol(), call.marker(), call.paths()]


def test_durable_evidence_keeps_per_object_catches_and_call_time_historical_short_circuit(monkeypatch):
    events = _durable_trace(monkeypatch)
    receipt = {"original": []}
    for sending in (True, False):
        events.reset_mock()
        events.journal.side_effect = [OSError("first journal"), False]
        events.media.side_effect = OSError("media receipt")
        events.load.return_value = (receipt, object())
        events.sending.return_value = sending
        events.valid.return_value = True
        monkeypatch.setitem(sys.modules, "historical_context_formatter", SimpleNamespace(
            HistoricalContextReplyStore=SimpleNamespace(
                _valid_sending_receipt=events.sending, _valid_receipt=events.valid,
            ),
        ))
        assert bot.durable_remote_write_safety_barrier_exists() is True
        expected = _durable_prefix(events) + [call.sending(receipt)]
        if not sending:
            expected.append(call.valid(receipt))
        assert events.mock_calls == expected + [call.release()]
        assert events.sending.call_args.args[0] is receipt


@pytest.mark.parametrize("lane,status,accepted", [
    *[(lane, status, True) for lane in ("regular", "meme")
      for status in ("sending", "pending_schedule", "valid")],
    *[("reply", status, True) for status in ("sending", "legacy_sending", "valid")],
    ("regular", "legacy_sending", False), ("meme", "legacy_sending", False),
    ("reply", "pending_schedule", False),
])
def test_durable_receipt_status_sets_and_eager_main_reader_order_remain_exact(monkeypatch, lane, status, accepted):
    events = _durable_trace(monkeypatch)
    getattr(events, lane).return_value = (status, {"original": []})
    assert bot.durable_remote_write_safety_barrier_exists() is accepted
    expected = _durable_prefix(events) + [call.regular(), call.meme()]
    if lane == "reply" or not accepted:
        expected.append(call.reply())
    if accepted:
        expected.append(call.release())
    assert events.mock_calls == expected


def test_durable_main_readers_share_the_catch_before_final_reply_reader(monkeypatch):
    events = _durable_trace(monkeypatch)
    events.regular.return_value = ("sending", {})
    events.meme.side_effect = OSError("meme read")
    events.reply.return_value = ("valid", {})
    assert bot.durable_remote_write_safety_barrier_exists() is True
    assert events.mock_calls == _durable_prefix(events) + [
        call.regular(), call.meme(), call.reply(), call.release(),
    ]


@pytest.mark.parametrize("authority,error_type", [
    ("journal", RuntimeError), ("media", RuntimeError), ("historical", RuntimeError),
    ("regular", RuntimeError), ("reply", RuntimeError), ("journal", KeyboardInterrupt),
])
def test_durable_confirmation_keeps_original_sigint_release_exception_scope(monkeypatch, authority, error_type):
    events = _durable_trace(monkeypatch)
    failure = error_type("release")
    events.release.side_effect = failure
    if authority == "journal":
        events.journal.side_effect = [True, False]
    elif authority == "media":
        events.media.return_value = True
    elif authority == "historical":
        events.load.return_value = ({}, b"bytes")
        events.sending.return_value = True
    else:
        getattr(events, authority).return_value = ("sending", {})
    if authority == "reply" or error_type is KeyboardInterrupt:
        with pytest.raises(error_type) as caught:
            bot.durable_remote_write_safety_barrier_exists()
        assert caught.value is failure
        assert events.mock_calls[-1] == call.release()
    else:
        assert bot.durable_remote_write_safety_barrier_exists() is False
        assert events.mock_calls[-1] == call.reply()
    events.release.assert_called_once_with()
