from __future__ import annotations

import copy
import inspect
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrs_bot_state_persistence as persistence
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, io, logging, os, random, socket, sys, time
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('state persistence import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply'} or name.startswith('mrs_bot_') and name != 'mrs_bot_state_persistence':
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
import mrs_bot_state_persistence
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
        ("state_document_for_persistence", 6), ("copy_state_backup", 6),
        ("rotate_state_backups_before_commit", 4), ("write_latest_state_backup", 4),
        ("save_state", 14),
    ):
        adapter = getattr(bot, name)
        public = inspect.signature(adapter).parameters
        dependencies = inspect.signature(getattr(persistence, name)).parameters.keys() - public.keys()
        assert len(dependencies) == count
        args = tuple(object() for param in public.values() if param.kind == param.POSITIONAL_OR_KEYWORD)
        options = {key: object() for key, param in public.items() if param.kind == param.KEYWORD_ONLY}
        with monkeypatch.context() as patch:
            for _ in range(2):
                result = object()
                owner = Mock(return_value=result)
                patch.setattr(bot, "_state_persistence", SimpleNamespace(**{name: owner}))
                current = {key: object() for key in dependencies}
                for key, value in current.items():
                    patch.setattr(bot, key, value)
                assert adapter(*args, **options) is result
                actual_args, actual_kwargs = owner.call_args
                assert len(actual_args) == len(args)
                assert all(actual is original for actual, original in zip(actual_args, args))
                assert actual_kwargs.keys() == (options | current).keys()
                assert all(actual_kwargs[key] is value for key, value in (options | current).items())
            failure = TypeError("current owner failure")
            owner.side_effect = failure
            with pytest.raises(TypeError) as caught:
                adapter(*args, **options)
            assert caught.value is failure


def test_document_preserves_retired_state_without_loading_trial_code(monkeypatch):
    trace = Mock()
    trace.reader.return_value = 7
    trace.deepcopy.side_effect = copy.deepcopy
    current, previous = {"current": {"version": [9]}}, {"previous": 8}
    monkeypatch.setattr(bot, "require_compatible_state_reader", trace.reader)
    monkeypatch.setattr(bot, "copy", SimpleNamespace(deepcopy=trace.deepcopy))
    monkeypatch.setattr(bot, "STATE_MINIMUM_READER_VERSION", 9)
    monkeypatch.setattr(bot, "STATE_READER_COMPATIBILITY_FENCE", current)
    monkeypatch.setattr(bot, "STATE_PREVIOUS_READER_COMPATIBILITY_FENCES", (previous,))
    extension, experiment = {"shared": []}, {"experiment": []}
    for legacy in (None, {}, copy.deepcopy(current), copy.deepcopy(previous)):
        state = {"pending_reply_drafts": legacy, "extension": extension,
                 "engagement_question_experiment": experiment}
        trace.reset_mock()
        result = bot.state_document_for_persistence(state)
        assert [c[0] for c in trace.mock_calls] == ["reader", "deepcopy"]
        trace.reader.assert_called_once_with(state, path=bot.STATE_FILE)
        assert trace.reader.call_args.args[0] is state
        assert trace.deepcopy.call_args.args[0] is current
        assert result is not state and result["extension"] is extension
        assert result["engagement_question_experiment"] is experiment
        assert result["minimum_reader_version"] == 9
        assert result["pending_reply_drafts"] == current
        assert result["pending_reply_drafts"]["current"]["version"] is not current["current"]["version"]
        assert state["pending_reply_drafts"] is legacy and "minimum_reader_version" not in state
    for state in ({}, {"engagement_question_experiment": None}):
        trace.reset_mock()
        trace.reader.return_value = 12
        result = bot.state_document_for_persistence(state)
        assert result["minimum_reader_version"] == 12
        assert ("engagement_question_experiment" in result) == ("engagement_question_experiment" in state)


def test_document_reader_then_legacy_errors_precede_copy_and_version_selection(monkeypatch):
    failure = ValueError("current validation failure")
    reader, copier = Mock(side_effect=failure), Mock()
    monkeypatch.setattr(bot, "require_compatible_state_reader", reader)
    monkeypatch.setattr(bot, "copy", SimpleNamespace(deepcopy=copier))
    with pytest.raises(ValueError) as caught:
        bot.state_document_for_persistence(object())
    assert caught.value is failure
    reader.side_effect = None
    reader.return_value = object()  # Comparing versions would fail before validation.
    with pytest.raises(RuntimeError, match="Legacy V1 reply drafts remain"):
        bot.state_document_for_persistence({"pending_reply_drafts": {"unretired": True}})
    copier.assert_not_called()


@pytest.fixture
def observed_io(monkeypatch):
    """Observe the real temporary file and serializer without replacing the writer."""
    trace, handles = Mock(), []

    class ObservedFile:
        def __init__(self, handle):
            self.handle = handle

        def __enter__(self):
            self.handle.__enter__()
            return self

        def __exit__(self, *args):
            result = self.handle.__exit__(*args)
            trace.close()
            return result

        def fileno(self):
            return self.handle.fileno()

        def write(self, data):
            trace.write(data)
            return self.handle.write(data)

        def flush(self):
            trace.flush()
            return self.handle.flush()

    def fdopen(*args, **kwargs):
        handle = os.fdopen(*args, **kwargs)
        handles.append(handle)
        return ObservedFile(handle)

    proxy = SimpleNamespace(**vars(os))
    for name in ("fchmod", "fsync", "replace"):
        getattr(trace, name).side_effect = getattr(os, name)
        setattr(proxy, name, getattr(trace, name))
    trace.fdopen.side_effect = fdopen
    proxy.fdopen = trace.fdopen
    trace.mkstemp.side_effect = tempfile.mkstemp
    trace.path.side_effect = Path
    trace.dump.side_effect = json.dump
    monkeypatch.setattr(bot, "os", proxy)
    monkeypatch.setattr(bot, "Path", trace.path)
    monkeypatch.setattr(bot, "tempfile", SimpleNamespace(mkstemp=trace.mkstemp))
    monkeypatch.setattr(bot, "json", SimpleNamespace(dump=trace.dump))
    monkeypatch.setattr(bot, "fsync_parent_dir", trace.parent)
    return trace, handles


@pytest.mark.parametrize("durable", [False, True])
def test_backup_copies_exact_stable_bytes_with_close_replace_and_optional_fsync(durable, tmp_path, monkeypatch, observed_io):
    trace, handles = observed_io
    source, destination = tmp_path / "source.json", tmp_path / "nested" / "backup.json"
    data = b'{ "z": 1, "a": "\xc3\xa9" }\n\n'
    source.write_bytes(data)
    trace.read.side_effect = bot.read_stable_owned_json_bytes_no_follow
    monkeypatch.setattr(bot, "read_stable_owned_json_bytes_no_follow", trace.read)
    bot.copy_state_backup(source, destination, durable=durable)
    assert destination.read_bytes() == data
    assert destination.stat().st_mode & 0o777 == 0o600
    assert [c[0] for c in trace.mock_calls] == (
        ["read", "mkstemp", "path", "fdopen", "fchmod", "write"]
        + (["flush", "fsync"] if durable else [])
        + ["close", "replace"] + (["parent"] if durable else [])
    )
    trace.read.assert_called_once_with(source)
    trace.mkstemp.assert_called_once_with(prefix=".backup.json.", dir=destination.parent)
    trace.write.assert_called_once_with(data)
    descriptor = trace.fdopen.call_args.args[0]
    trace.fdopen.assert_called_once_with(descriptor, "wb")
    trace.fchmod.assert_called_once_with(descriptor, 0o600)
    assert handles[0].closed
    if durable:
        trace.fsync.assert_called_once_with(descriptor)
        trace.parent.assert_called_once_with(destination, strict=True)
    assert not list(destination.parent.glob(".backup.json.*"))


def test_backup_missing_source_and_native_reader_errors_precede_destination_io(tmp_path, monkeypatch):
    class CurrentUnsafe(Exception):
        pass

    destination = tmp_path / "absent" / "backup.json"
    reader, temporary = Mock(), Mock()
    monkeypatch.setattr(bot, "UnsafeDurableStateNamespace", CurrentUnsafe)
    monkeypatch.setattr(bot, "read_stable_owned_json_bytes_no_follow", reader)
    monkeypatch.setattr(bot, "tempfile", SimpleNamespace(mkstemp=temporary))
    for result in ((False, b"{}"), (True, None)):
        reader.return_value = result
        with pytest.raises(CurrentUnsafe, match="state backup source disappeared before copying"):
            bot.copy_state_backup(tmp_path / "source", destination)
    failure = OSError("native stable reader failure")
    reader.side_effect = failure
    with pytest.raises(OSError) as caught:
        bot.copy_state_backup(tmp_path / "source", destination)
    assert caught.value is failure
    assert not destination.parent.exists()
    temporary.assert_not_called()


def test_rotation_moves_reverse_generations_before_copy_and_latest_is_independent(tmp_path, monkeypatch):
    state = bot.STATE_FILE
    state.write_bytes(b'{ "canonical": true }')
    for index in range(1, 5):
        state.with_name(f"{state.name}.bak{index}").write_bytes(str(index).encode())
    trace = Mock()
    trace.move.side_effect = Path.replace
    trace.copy.side_effect = bot.copy_state_backup
    monkeypatch.setattr(Path, "replace", lambda path, target: trace.move(path, target))
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 4)
    monkeypatch.setattr(bot, "copy_state_backup", trace.copy)
    monkeypatch.setattr(bot, "log", trace.log)
    bot.rotate_state_backups_before_commit(durable=True)
    bak = lambda index: state.with_name(f"{state.name}.bak{index}")
    assert trace.mock_calls == [
        call.move(bak(3), bak(4)), call.move(bak(2), bak(3)),
        call.copy(state, bak(2), durable=True),
        call.log.debug("Previous state backup written: %s", bak(2)),
    ]
    assert [bak(i).read_bytes() for i in range(1, 5)] == [b"1", state.read_bytes(), b"2", b"3"]
    trace.reset_mock()
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 1)
    bot.write_latest_state_backup()
    assert trace.mock_calls == [
        call.copy(state, bak(1), durable=False),
        call.log.debug("Latest committed state backup written: %s", bak(1)),
    ]
    assert bak(1).read_bytes() == state.read_bytes()


def test_backup_counts_skip_path_lookup_and_missing_state_skips_copy(monkeypatch):
    path, copier = Mock(), Mock()
    monkeypatch.setattr(bot, "STATE_FILE", path)
    monkeypatch.setattr(bot, "copy_state_backup", copier)
    for name, threshold in (("rotate_state_backups_before_commit", 1), ("write_latest_state_backup", 0)):
        path.reset_mock()
        monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", threshold)
        getattr(bot, name)()
        assert path.mock_calls == []
        monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", threshold + 1)
        path.exists.return_value = False
        getattr(bot, name)()
        assert path.mock_calls == [call.exists()]
        failure = OSError("native exists failure outside rotation catch")
        path.exists.side_effect = failure
        with pytest.raises(OSError) as caught:
            getattr(bot, name)()
        assert caught.value is failure
        path.exists.side_effect = None
    copier.assert_not_called()


@pytest.mark.parametrize("error_type", [OSError, KeyboardInterrupt])
def test_rotation_suppresses_only_ordinary_copy_errors_and_latest_propagates(error_type, monkeypatch):
    bot.STATE_FILE.write_bytes(b"{}")
    failure, logger = error_type("backup copy failed"), Mock()
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 2)
    monkeypatch.setattr(bot, "copy_state_backup", Mock(side_effect=failure))
    monkeypatch.setattr(bot, "log", logger)
    if error_type is OSError:
        assert bot.rotate_state_backups_before_commit() is None
        logger.exception.assert_called_once_with("Failed rotating state backups; continuing with state save")
    else:
        with pytest.raises(error_type) as caught:
            bot.rotate_state_backups_before_commit()
        assert caught.value is failure
        logger.exception.assert_not_called()
    logger.debug.assert_not_called()
    with pytest.raises(error_type) as caught:
        bot.write_latest_state_backup()
    assert caught.value is failure
    logger.debug.assert_not_called()


@pytest.mark.parametrize("durable", [False, True])
def test_save_security_summary_write_close_rotate_replace_latest_order(durable, monkeypatch, observed_io):
    trace, handles = observed_io
    state_file = bot.STATE_FILE
    state_file.write_bytes(b"previous canonical")
    state, summary = {"runtime": []}, {"key_count": 1}
    document = {"z": float("inf"), "é": ["value"]}
    trace.guard.return_value = False
    trace.summary.return_value = summary
    trace.document.return_value = document
    trace.rotate.side_effect = lambda **kwargs: state_file.read_bytes() == b"previous canonical" or pytest.fail("rotation ran after canonical commit")
    trace.latest.side_effect = lambda **kwargs: state_file.read_bytes() == json.dumps(document, indent=2, sort_keys=True).encode() or pytest.fail("latest ran before canonical commit")
    for name, callback in (
        ("test_process_production_state_write_blocked", trace.guard),
        ("state_debug_summary", trace.summary), ("log_json_debug", trace.summary_log),
        ("state_document_for_persistence", trace.document),
        ("rotate_state_backups_before_commit", trace.rotate),
        ("write_latest_state_backup", trace.latest),
    ):
        monkeypatch.setattr(bot, name, callback)
    monkeypatch.setattr(bot, "log", trace.log)
    bot.save_state(state, durable=durable)
    assert [c[0] for c in trace.mock_calls if c[0] != "write"] == (
        ["guard", "log.debug", "summary", "summary_log", "document", "mkstemp", "path", "fdopen", "fchmod", "dump"]
        + (["flush", "fsync"] if durable else [])
        + ["close", "rotate", "replace"] + (["parent"] if durable else []) + ["latest"]
    )
    trace.guard.assert_called_once_with(state_file)
    trace.log.debug.assert_called_once_with("Saving state to %s", state_file)
    assert trace.summary.call_args.args[0] is trace.document.call_args.args[0] is state
    trace.summary_log.assert_called_once_with("State summary being saved", summary)
    assert trace.summary_log.call_args.args[1] is summary
    assert trace.dump.call_args.args[0] is document
    assert trace.dump.call_args.kwargs == {"indent": 2, "sort_keys": True}
    descriptor = trace.fdopen.call_args.args[0]
    trace.fdopen.assert_called_once_with(descriptor, "w", encoding="utf-8")
    trace.fchmod.assert_called_once_with(descriptor, 0o600)
    trace.rotate.assert_called_once_with(durable=durable)
    trace.latest.assert_called_once_with(durable=durable)
    assert handles[0].closed and state_file.stat().st_mode & 0o777 == 0o600
    assert state_file.read_bytes() == json.dumps(document, indent=2, sort_keys=True).encode()
    if durable:
        trace.fsync.assert_called_once_with(descriptor)
        trace.parent.assert_called_once_with(state_file, strict=True)


def test_save_security_and_document_failure_precede_io(tmp_path, monkeypatch):
    trace = Mock()
    state_file = tmp_path / "absent" / "state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "test_process_production_state_write_blocked", trace.guard)
    monkeypatch.setattr(bot, "log", trace.log)
    monkeypatch.setattr(bot, "state_debug_summary", trace.summary)
    monkeypatch.setattr(bot, "log_json_debug", trace.summary_log)
    monkeypatch.setattr(bot, "state_document_for_persistence", trace.document)
    trace.guard.return_value = True
    with pytest.raises(RuntimeError) as caught:
        bot.save_state(object())
    assert str(caught.value) == f"Refusing test-process write to production state: {state_file}"
    assert trace.mock_calls == [call.guard(state_file)]
    trace.guard.return_value = False
    failure = ValueError("document validation failed")
    trace.document.side_effect = failure
    with pytest.raises(ValueError) as caught:
        bot.save_state({})
    assert caught.value is failure
    assert not state_file.parent.exists()


@pytest.mark.parametrize("operation", ["save", "copy"])
def test_publication_hard_exit_closes_and_removes_temp_without_changing_target(operation, monkeypatch, observed_io):
    trace, handles = observed_io
    target = bot.STATE_FILE
    target.write_bytes(b"previous canonical")
    failure = KeyboardInterrupt("publication interrupted")
    trace.replace.side_effect = failure
    latest = Mock()
    monkeypatch.setattr(bot, "write_latest_state_backup", latest)
    with pytest.raises(KeyboardInterrupt) as caught:
        if operation == "save":
            bot.save_state({})
        else:
            bot.copy_state_backup(target, target)
    assert caught.value is failure
    assert handles[0].closed
    assert target.read_bytes() == b"previous canonical"
    assert not list(target.parent.glob(f".{target.name}.*"))
    trace.parent.assert_not_called()
    latest.assert_not_called()


def test_native_serialization_failure_cleans_temp_and_does_not_rotate(monkeypatch, observed_io):
    trace, handles = observed_io
    failure = TypeError("native serializer failure")
    trace.dump.side_effect = failure
    rotate = Mock()
    monkeypatch.setattr(bot, "rotate_state_backups_before_commit", rotate)
    with pytest.raises(TypeError) as caught:
        bot.save_state({})
    assert caught.value is failure
    assert handles[0].closed
    assert not bot.STATE_FILE.exists()
    assert not list(bot.STATE_FILE.parent.glob(f".{bot.STATE_FILE.name}.*"))
    rotate.assert_not_called()
    trace.replace.assert_not_called()


@pytest.mark.parametrize("error_type", [OSError, KeyboardInterrupt])
def test_latest_failure_follows_canonical_commit_and_only_ordinary_error_is_wrapped(error_type, monkeypatch):
    class CurrentBackupError(Exception):
        pass

    failure = error_type("latest backup failed")
    monkeypatch.setattr(bot, "StateBackupWriteError", CurrentBackupError)
    monkeypatch.setattr(bot, "write_latest_state_backup", Mock(side_effect=failure))
    expected = CurrentBackupError if error_type is OSError else KeyboardInterrupt
    state = {"extension": ["preserved"]}
    with pytest.raises(expected) as caught:
        bot.save_state(state, durable=True)
    assert bot.STATE_FILE.read_bytes() == json.dumps(bot.state_document_for_persistence(state), indent=2, sort_keys=True).encode()
    if error_type is OSError:
        assert str(caught.value) == f"Canonical state committed but latest backup write failed: {bot.STATE_FILE}"
        assert caught.value.__cause__ is failure
    else:
        assert caught.value is failure
    assert not list(bot.STATE_FILE.parent.glob(f".{bot.STATE_FILE.name}.*"))
