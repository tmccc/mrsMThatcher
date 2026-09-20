from __future__ import annotations

import inspect
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrs_bot_durable_json_io as durable_io
from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, json, io, logging, os, random, socket, sys, time, typing
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('durable JSON I/O import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply'} or name.startswith('mrs_bot_') and name != 'mrs_bot_durable_json_io':
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
import mrs_bot_durable_json_io
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
        ("durable_state_namespace_is_owned_single_link_file", 1),
        ("read_stable_owned_json_bytes_no_follow", 4),
        ("fsync_parent_dir", 2),
        ("atomic_write_json", 5),
        ("_strict_receipt_json_bytes", 1),
        ("load_receipt_json_no_follow", 4),
        ("durable_create_receipt_json", 4),
    ):
        adapter = getattr(bot, name)
        public = inspect.signature(adapter).parameters
        dependencies = inspect.signature(getattr(durable_io, name)).parameters.keys() - public.keys()
        assert len(dependencies) == count
        args = tuple(object() for param in public.values() if param.kind == param.POSITIONAL_OR_KEYWORD)
        options = {key: object() for key, param in public.items() if param.kind == param.KEYWORD_ONLY}
        with monkeypatch.context() as patch:
            for _ in range(2):
                result = object()
                owner = Mock(return_value=result)
                patch.setattr(bot, "_durable_json_io", SimpleNamespace(**{name: owner}))
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


def test_namespace_default_stays_frozen_while_reader_limit_is_current(tmp_path, monkeypatch):
    path = tmp_path / "state.json"
    path.write_bytes(b"{}\n")
    path.chmod(0o644)
    default = bot.durable_state_namespace_is_owned_single_link_file.__kwdefaults__["maximum_bytes"]
    assert default == 64 * 1024 * 1024
    assert inspect.signature(durable_io.durable_state_namespace_is_owned_single_link_file).parameters["maximum_bytes"].default is inspect.Parameter.empty
    monkeypatch.setattr(bot, "DURABLE_RUNTIME_JSON_MAX_BYTES", 1)
    assert bot.durable_state_namespace_is_owned_single_link_file(path) is True
    assert bot.durable_state_namespace_is_owned_single_link_file(path, maximum_bytes=1) is False
    assert bot.durable_state_namespace_is_owned_single_link_file(path, maximum_bytes=None) is True
    with pytest.raises(bot.UnsafeDurableStateNamespace, match="identity changed while opening"):
        bot.read_stable_owned_json_bytes_no_follow(path)


def test_readers_keep_distinct_permissions_and_no_follow_namespace_policy(tmp_path):
    path = tmp_path / "authority.json"
    assert bot.durable_state_namespace_is_owned_single_link_file(path) is False
    assert bot.read_stable_owned_json_bytes_no_follow(path) == (False, None)
    assert bot.load_receipt_json_no_follow(path) == (False, None)
    path.write_bytes(b"ordinary state reader does not parse JSON")
    path.chmod(0o644)
    assert bot.read_stable_owned_json_bytes_no_follow(path) == (True, path.read_bytes())
    with pytest.raises(bot.UnsafeReceiptNamespace, match="unsafe ownership or permissions"):
        bot.load_receipt_json_no_follow(path)
    link = tmp_path / "link.json"
    link.symlink_to(path.name)
    assert bot.durable_state_namespace_is_owned_single_link_file(link) is False
    with pytest.raises(bot.UnsafeDurableStateNamespace, match="not one bounded owned ordinary file"):
        bot.read_stable_owned_json_bytes_no_follow(link)
    with pytest.raises(bot.UnsafeReceiptNamespace, match="not one single-link ordinary file"):
        bot.load_receipt_json_no_follow(link)


@pytest.mark.parametrize("data,message,cause", [
    (b'{"a":{"x":1,"x":2}}', "duplicate JSON object name: x", None),
    (b'{"x":NaN}', "unsupported JSON constant: NaN", None),
    (b'\xff', "not valid UTF-8 JSON", UnicodeDecodeError),
    (b'{', "not valid UTF-8 JSON", json.JSONDecodeError),
])
def test_strict_receipt_json_uses_current_exception_and_exact_causes(data, message, cause, monkeypatch):
    class CurrentReceiptError(RuntimeError):
        pass

    monkeypatch.setattr(bot, "UnsafeReceiptNamespace", CurrentReceiptError)
    with pytest.raises(CurrentReceiptError, match=message) as caught:
        bot._strict_receipt_json_bytes(data)
    assert type(caught.value) is CurrentReceiptError
    if cause is None:
        assert caught.value.__cause__ is None
    else:
        assert type(caught.value.__cause__) is cause
    failure = CurrentReceiptError("parser callback failure")
    monkeypatch.setattr(durable_io, "json", SimpleNamespace(loads=Mock(side_effect=failure)))
    with pytest.raises(CurrentReceiptError) as caught:
        bot._strict_receipt_json_bytes(b"{}")
    assert caught.value is failure


@pytest.mark.parametrize("kind", ["state", "receipt"])
def test_readers_assemble_short_reads_close_before_callbacks_and_keep_native_failures(kind, tmp_path, monkeypatch):
    path = tmp_path / "authority.json"
    data = b"{}\n"
    path.write_bytes(data)
    path.chmod(0o600)
    trace = Mock()
    proxy = SimpleNamespace(**vars(os))
    for name in ("lstat", "open", "fstat", "read", "pread", "close"):
        getattr(trace, name).side_effect = getattr(os, name)
        setattr(proxy, name, getattr(trace, name))
    trace.read.side_effect = lambda fd, size: os.read(fd, min(size, 2))
    monkeypatch.setattr(bot, "os", proxy)
    value = {"original": []}
    trace.parse.return_value = value
    trace.canonical.return_value = data
    monkeypatch.setattr(bot, "_strict_receipt_json_bytes", trace.parse)
    monkeypatch.setattr(durable_io, "canonical_atomic_json_bytes", trace.canonical)
    reader = bot.read_stable_owned_json_bytes_no_follow if kind == "state" else bot.load_receipt_json_no_follow
    present, result = reader(path)
    assert present is True
    assert result == data if kind == "state" else result is value
    names = [entry[0] for entry in trace.mock_calls]
    if kind == "state":
        assert names == ["lstat", "lstat", "open", "fstat", "read", "read", "read", "pread", "fstat", "lstat", "close"]
    else:
        assert names == ["lstat", "open", "fstat", "read", "read", "read", "fstat", "lstat", "close", "parse", "canonical"]
        assert trace.canonical.call_args.args[0] is value
    assert trace.open.call_args.args[1] == os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
    failure = OSError("native read failure")
    trace.reset_mock()
    trace.read.side_effect = failure
    with pytest.raises(OSError) as caught:
        reader(path)
    assert caught.value is failure
    assert trace.mock_calls[-1][0] == "close"
    trace.parse.assert_not_called()
    with pytest.raises(OSError):
        os.fstat(trace.close.call_args.args[0])


@pytest.mark.parametrize("durable", [False, True])
def test_atomic_json_keeps_value_encoding_flush_close_replace_and_parent_order(durable, tmp_path, monkeypatch):
    path = tmp_path / "new" / "atomic.json"
    value = {"z": 2.0, "a": 1}
    events = []
    streams = []
    proxy = SimpleNamespace(**vars(os))
    real_parent_sync = bot.fsync_parent_dir

    class Handle:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            result = self.stream.__exit__(*args)
            events.append("close")
            return result

        def fileno(self):
            return self.stream.fileno()

        def write(self, data):
            assert isinstance(data, bytes) and data.endswith(b"\n")
            events.append("write")
            return self.stream.write(data)

        def flush(self):
            events.append("flush")
            return self.stream.flush()

    def fdopen(*args, **kwargs):
        events.append("fdopen")
        stream = os.fdopen(*args, **kwargs)
        streams.append(stream)
        return Handle(stream)

    def dumps(current, **kwargs):
        assert current is value
        assert kwargs == {"indent": 2, "sort_keys": True, "allow_nan": False}
        events.append("dumps")
        return json.dumps(current, **kwargs)

    def fsync(fd):
        events.append("fsync")
        os.fsync(fd)

    def replace(source, destination):
        assert streams[0].closed
        events.append("replace")
        os.replace(source, destination)

    def parent_sync(target, *, strict=False):
        assert target is path and strict is True
        events.append("parent")
        real_parent_sync(target, strict=strict)

    proxy.fdopen, proxy.fsync, proxy.replace = fdopen, fsync, replace
    monkeypatch.setattr(bot, "os", proxy)
    monkeypatch.setattr(durable_io, "json", SimpleNamespace(dumps=dumps))
    monkeypatch.setattr(bot, "fsync_parent_dir", parent_sync)
    bot.atomic_write_json(path, value, durable=durable)
    assert events == ["dumps", "fdopen", "write"] + (["flush", "fsync"] if durable else []) + ["close", "replace"] + (["parent", "fsync"] if durable else [])
    assert path.read_bytes() == b'{\n  "a": 1,\n  "z": 2.0\n}\n'
    assert list(path.parent.iterdir()) == [path]
    monkeypatch.setattr(durable_io, "json", json)
    assert bot.load_receipt_json_no_follow(path) == (True, value)


def test_atomic_hard_exit_closes_temp_and_preserves_existing_target(tmp_path, monkeypatch):
    path = tmp_path / "atomic" / "value.json"
    path.parent.mkdir()
    path.write_bytes(b"previous")
    failure = SystemExit(73)
    streams = []

    def fdopen(*args, **kwargs):
        stream = os.fdopen(*args, **kwargs)
        streams.append(stream)
        return stream

    proxy = SimpleNamespace(**vars(os))
    proxy.fdopen = fdopen
    monkeypatch.setattr(bot, "os", proxy)
    monkeypatch.setattr(durable_io, "json", SimpleNamespace(dumps=Mock(side_effect=failure)))
    parent = Mock()
    monkeypatch.setattr(bot, "fsync_parent_dir", parent)
    with pytest.raises(SystemExit) as caught:
        bot.atomic_write_json(path, {}, durable=True)
    assert caught.value is failure
    assert streams == []
    assert path.read_bytes() == b"previous"
    assert list(path.parent.iterdir()) == [path]
    parent.assert_not_called()


def test_parent_fsync_only_suppresses_nonstrict_open_failure_and_always_closes(tmp_path, monkeypatch):
    path = tmp_path / "value.json"
    failure = OSError("directory failure")
    proxy = SimpleNamespace(**vars(os))
    proxy.open = Mock(side_effect=failure)
    logger = Mock()
    monkeypatch.setattr(bot, "os", proxy)
    monkeypatch.setattr(bot, "log", logger)
    assert bot.fsync_parent_dir(path) is None
    logger.debug.assert_called_once_with("Could not open parent directory for fsync: %s", path.parent, exc_info=True)
    with pytest.raises(OSError) as caught:
        bot.fsync_parent_dir(path, strict=True)
    assert caught.value is failure
    assert logger.debug.call_count == 1
    proxy.open = Mock(wraps=os.open)
    proxy.fsync = Mock(side_effect=failure)
    proxy.close = Mock(wraps=os.close)
    with pytest.raises(OSError) as caught:
        bot.fsync_parent_dir(path)
    assert caught.value is failure
    fd = proxy.fsync.call_args.args[0]
    proxy.close.assert_called_once_with(fd)
    with pytest.raises(OSError):
        os.fstat(fd)


def test_receipt_short_writes_and_parent_fsync_precede_exact_acknowledgement(tmp_path, monkeypatch):
    path = tmp_path / "receipt.json"
    value = {"unit": 1}
    data = bot.canonical_atomic_json_bytes(value)
    trace = Mock()
    proxy = SimpleNamespace(**vars(os))
    for name in ("open", "fchmod", "write", "fsync", "fstat", "lstat", "pread", "close"):
        getattr(trace, name).side_effect = getattr(os, name)
        setattr(proxy, name, getattr(trace, name))
    trace.write.side_effect = lambda fd, block: os.write(fd, block[:3])
    trace.canonical.side_effect = bot.canonical_atomic_json_bytes
    trace.parent.side_effect = bot.fsync_parent_dir
    monkeypatch.setattr(bot, "os", proxy)
    monkeypatch.setattr(durable_io, "canonical_atomic_json_bytes", trace.canonical)
    monkeypatch.setattr(bot, "fsync_parent_dir", trace.parent)
    assert bot.durable_create_receipt_json(path, value) is None
    assert trace.canonical.call_args.args[0] is value
    assert trace.write.call_count > 1
    assert [entry[0] for entry in trace.mock_calls] == ["canonical", "open", "fchmod"] + ["write"] * trace.write.call_count + ["fsync", "fstat", "parent", "open", "fsync", "close", "lstat", "pread", "fstat", "lstat", "close"]
    assert trace.open.call_args_list[0] == call(path, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0), 0o600)
    trace.parent.assert_called_once_with(path, strict=True)
    assert path.read_bytes() == data
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    trace.reset_mock()
    with pytest.raises(FileExistsError):
        bot.durable_create_receipt_json(path, value)
    assert [entry[0] for entry in trace.mock_calls] == ["canonical", "open"]
    assert path.read_bytes() == data


@pytest.mark.parametrize("boundary", ["zero_write", "parent_fsync"])
def test_receipt_publication_failure_closes_without_acknowledgement_or_cleanup(boundary, tmp_path, monkeypatch):
    path = tmp_path / "publication" / "receipt.json"
    path.parent.mkdir()
    failure = OSError("parent fsync failed")
    proxy = SimpleNamespace(**vars(os))
    proxy.close = Mock(wraps=os.close)
    proxy.lstat = Mock(wraps=os.lstat)
    if boundary == "zero_write":
        proxy.write = Mock(return_value=0)
    parent = Mock(side_effect=failure)
    monkeypatch.setattr(bot, "os", proxy)
    monkeypatch.setattr(bot, "fsync_parent_dir", parent)
    with pytest.raises(OSError) as caught:
        bot.durable_create_receipt_json(path, {})
    if boundary == "zero_write":
        assert str(caught.value) == "short write while publishing receipt"
        parent.assert_not_called()
        assert path.read_bytes() == b""
    else:
        assert caught.value is failure
        parent.assert_called_once_with(path, strict=True)
        assert path.read_bytes() == b"{}\n"
    proxy.lstat.assert_not_called()
    proxy.close.assert_called_once()
    with pytest.raises(OSError):
        os.fstat(proxy.close.call_args.args[0])
    assert list(path.parent.iterdir()) == [path]


def test_canonical_receipt_bytes_keep_root_alias_and_wire_format():
    assert bot.canonical_atomic_json_bytes is durable_io.canonical_atomic_json_bytes
    assert bot.canonical_atomic_json_bytes({"z": False, "a": "café"}) == (
        b'{\n  "a": "caf\\u00e9",\n  "z": false\n}\n'
    )
    assert bot.canonical_atomic_json_bytes([1, None]) == b'[\n  1,\n  null\n]\n'
    with pytest.raises(ValueError, match="Out of range float values"):
        bot.canonical_atomic_json_bytes({"unsupported": float("nan")})
    with pytest.raises(TypeError):
        bot.canonical_atomic_json_bytes({"unsupported": object()})


def test_atomic_write_uses_owned_wire_encoder_before_directory_creation(tmp_path, monkeypatch):
    path = tmp_path / "missing" / "receipt.json"
    value = {"z": False, "a": "café"}
    expected = bot.canonical_atomic_json_bytes(value)
    encoder = Mock(wraps=durable_io.canonical_atomic_json_bytes)
    monkeypatch.setattr(durable_io, "canonical_atomic_json_bytes", encoder)
    bot.atomic_write_json(path, value)
    encoder.assert_called_once_with(value)
    assert encoder.call_args.args[0] is value
    assert path.read_bytes() == expected
    failure = ValueError("encoding failed")
    encoder.side_effect = failure
    absent = tmp_path / "still-missing" / "receipt.json"
    with pytest.raises(ValueError) as caught:
        bot.atomic_write_json(absent, value)
    assert caught.value is failure
    assert not absent.parent.exists()
