from __future__ import annotations

import errno
import fcntl
import hashlib
import inspect
import os
from pathlib import Path
import stat
import struct
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

import mrsMThatcher2 as bot
from tests.helpers.bot_fixtures import isolate_regular_post_receipt  # noqa: F401
from transaction_mutation_authority import require_transaction_mutation_authority

DEPENDENCIES = {'instance_lock_abstract_socket_name': ['BASE_DIR',
                                        'instance_lock_abstract_socket_name_for_identity',
                                        'os',
                                        'stat'],
 'instance_lock_abstract_socket_name_for_identity': ['hashlib'],
 'ofd_lock_record': ['_OFD_LOCK_FORMAT', 'os', 'struct'],
 'descriptor_owns_exclusive_flock': ['Path', 'os'],
 'test_mode_excludes_live_remote_writes': ['LIVE_ENDPOINT_TEST_OVERRIDE_PHRASE',
                                           'OPENAI_BASE',
                                           'TEST_MODE',
                                           'X_BASE',
                                           'X_UPLOAD_BASE',
                                           '_configured_x_request_is_sealed_test_loopback',
                                           'endpoint_is_loopback',
                                           'os',
                                           'single_call_reply'],
 'require_instance_lock_for_remote_write': ['BASE_DIR',
                                            'LOCK_FILE',
                                            '_LOCK_ACQUISITION_IDENTITY',
                                            '_LOCK_FH',
                                            '_LOCK_SOCKET',
                                            '_LOCK_SOCKET_NAME',
                                            '_OFD_LOCK_FORMAT',
                                            '_STATE_DIR_LOCK_FD',
                                            '_STATE_DIR_LOCK_IDENTITY',
                                            'descriptor_owns_exclusive_flock',
                                            'errno',
                                            'fcntl',
                                            'ofd_lock_record',
                                            'os',
                                            'stat',
                                            'struct',
                                            'test_mode_excludes_live_remote_writes'],
 'transaction_mutation_authority': ['issue_transaction_mutation_authority',
                                    'require_instance_lock_for_remote_write']}
SIGNATURES = {'instance_lock_abstract_socket_name': "(base_dir: 'Path | None' = None) -> 'bytes'",
 'instance_lock_abstract_socket_name_for_identity': "(device: 'int', inode: 'int') -> 'bytes'",
 'ofd_lock_record': "(lock_type: 'int') -> 'bytes'",
 'descriptor_owns_exclusive_flock': "(descriptor: 'int', *, expected_device: 'int', "
                                    "expected_inode: 'int') -> 'bool'",
 'test_mode_excludes_live_remote_writes': "() -> 'bool'",
 'require_instance_lock_for_remote_write': "(operation: 'str') -> 'None'",
 'transaction_mutation_authority': "(operation: 'str') -> 'TransactionMutationAuthority'"}


def test_import_needs_no_runtime_access():
    code = """
import builtins, collections.abc, io, logging, os, random, socket, sys, time, typing
from pathlib import Path

def forbidden(*args, **kwargs):
    raise AssertionError('Instance-lock checks import attempted runtime access')

original_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name in {'mrsMThatcher2', 'requests', 'openai', 'single_call_reply', 'historical_context_formatter', 'historical_context_outbox', 'transaction_mutation_authority'} or name.startswith('mrs_bot_') and name != 'mrs_bot_instance_lock_checks':
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
import mrs_bot_instance_lock_checks
assert random.getstate() == before
assert 'mrsMThatcher2' not in sys.modules
assert 'requests' not in sys.modules
assert 'single_call_reply' not in sys.modules
assert 'transaction_mutation_authority' not in sys.modules
assert mrs_bot_instance_lock_checks.transaction_mutation_authority.__annotations__['return'] == 'TransactionMutationAuthority'
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

            patch.setattr(bot, "_instance_lock_checks", SimpleNamespace(**{name: capture}))
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
            patch.setattr(bot, "_instance_lock_checks", SimpleNamespace(**{name: Mock(side_effect=failure)}))
            with pytest.raises(TypeError) as caught:
                adapter(**provided)
            assert caught.value is failure


@pytest.mark.parametrize("base_dir", [None, "", object()])
def test_directory_name_uses_current_path_identity_and_sibling(monkeypatch, base_dir):
    trace = Mock()
    configured, path, absolute, result = object(), object(), object(), object()
    trace.fspath.return_value = path
    trace.abspath.return_value = absolute
    trace.stat.return_value = SimpleNamespace(st_mode=stat.S_IFDIR, st_dev="8", st_ino="123")
    trace.sibling.return_value = result
    monkeypatch.setattr(bot, "BASE_DIR", configured)
    monkeypatch.setattr(bot, "os", SimpleNamespace(
        fspath=trace.fspath, path=SimpleNamespace(abspath=trace.abspath), stat=trace.stat,
    ))
    monkeypatch.setattr(bot, "instance_lock_abstract_socket_name_for_identity", trace.sibling)
    assert bot.instance_lock_abstract_socket_name(base_dir) is result
    assert trace.mock_calls == [
        call.fspath(base_dir or configured), call.abspath(path),
        call.stat(absolute, follow_symlinks=True), call.sibling(8, 123),
    ]
    trace.reset_mock()
    trace.stat.return_value.st_mode = stat.S_IFREG
    with pytest.raises(RuntimeError, match="not a directory"):
        bot.instance_lock_abstract_socket_name(base_dir)
    trace.sibling.assert_not_called()
    failure = OSError("current directory lookup")
    trace.stat.side_effect = failure
    with pytest.raises(OSError) as caught:
        bot.instance_lock_abstract_socket_name(base_dir)
    assert caught.value is failure


def test_identity_name_keeps_integer_order_ascii_and_exact_hash_bytes(monkeypatch):
    conversions = []

    class Number:
        def __init__(self, name, value):
            self.name, self.value = name, value

        def __int__(self):
            conversions.append(self.name)
            return self.value

    sha256 = Mock(wraps=hashlib.sha256)
    monkeypatch.setattr(bot, "hashlib", SimpleNamespace(sha256=sha256))
    result = bot.instance_lock_abstract_socket_name_for_identity(Number("device", 8), Number("inode", 123))
    assert conversions == ["device", "inode"]
    sha256.assert_called_once_with(b"dev=8;ino=123")
    assert result == b"\0mrsMThatcher-instance-" + hashlib.sha256(b"dev=8;ino=123").hexdigest()[:40].encode("ascii")
    failure = ValueError("current hash")
    sha256.side_effect = failure
    with pytest.raises(ValueError) as caught:
        bot.instance_lock_abstract_socket_name_for_identity(8, 123)
    assert caught.value is failure


def test_ofd_record_keeps_current_pack_arguments_and_native_errors(monkeypatch):
    assert bot._OFD_LOCK_FORMAT == "hhqqi"
    assert struct.unpack("hhqqi", bot.ofd_lock_record(fcntl.F_WRLCK)) == (fcntl.F_WRLCK, os.SEEK_SET, 0, 1, 0)
    record, layout, lock_type, seek = object(), object(), object(), object()
    pack = Mock(return_value=record)
    monkeypatch.setattr(bot, "struct", SimpleNamespace(pack=pack))
    monkeypatch.setattr(bot, "_OFD_LOCK_FORMAT", layout)
    monkeypatch.setattr(bot, "os", SimpleNamespace(SEEK_SET=seek))
    assert bot.ofd_lock_record(lock_type) is record
    pack.assert_called_once_with(layout, lock_type, seek, 0, 1, 0)
    failure = struct.error("native pack")
    pack.side_effect = failure
    with pytest.raises(struct.error) as caught:
        bot.ofd_lock_record(lock_type)
    assert caught.value is failure


def _fdinfo_trace(monkeypatch, text):
    trace = Mock()
    trace.read_text.return_value = text
    trace.path.return_value = SimpleNamespace(read_text=trace.read_text)
    trace.major.return_value, trace.minor.return_value = 8, 10
    trace.getpid.return_value = 77
    monkeypatch.setattr(bot, "Path", trace.path)
    monkeypatch.setattr(bot, "os", SimpleNamespace(
        major=trace.major, minor=trace.minor, getpid=trace.getpid,
    ))
    return trace


@pytest.mark.parametrize("text, expected", [
    ("lock: unrestricted FLOCK ADVISORY WRITE 77 08:0a:123 0 EOF", True),
    ("pos: 0\nlock:\t9: FLOCK ADVISORY WRITE 77 8:A:123 0 EOF\n", True),
    ("lock: 1: FLOCK ADVISORY WRITE 77 08:0a:123 0 EOF extra", False),
    ("LOCK: 1: FLOCK ADVISORY WRITE 77 08:0a:123 0 EOF", False),
    ("lock: 1: OFDLCK ADVISORY WRITE 77 08:0a:123 0 EOF", False),
    ("lock: 1: FLOCK MANDATORY WRITE 77 08:0a:123 0 EOF", False),
    ("lock: 1: FLOCK ADVISORY READ 77 08:0a:123 0 EOF", False),
    ("lock: 1: FLOCK ADVISORY WRITE 77 08:0a:123 1 EOF", False),
    ("lock: 1: FLOCK ADVISORY WRITE 77 08:0a:123 0 1", False),
    ("lock: 1: FLOCK ADVISORY WRITE bad 08:0a:123 0 EOF", False),
    ("lock: 1: FLOCK ADVISORY WRITE 77 08:0a:123:extra 0 EOF", False),
    ("lock: 1: FLOCK ADVISORY WRITE 77 08:zz:123 0 EOF\n"
     "lock: any FLOCK ADVISORY WRITE 77 08:0a:123 0 EOF", True),
])
def test_fdinfo_exact_shape_literals_bases_and_unrestricted_counter(monkeypatch, text, expected):
    trace = _fdinfo_trace(monkeypatch, text)
    assert bot.descriptor_owns_exclusive_flock("11", expected_device="2048", expected_inode="123") is expected
    assert trace.mock_calls[:4] == [
        call.path("/proc/self/fdinfo/11"), call.read_text(encoding="ascii"),
        call.major(2048), call.minor(2048),
    ]


@pytest.mark.parametrize("failure, caught", [(OSError("read"), True), (UnicodeError("ascii"), True), (TypeError("read"), False)])
def test_fdinfo_read_catches_only_original_error_types(monkeypatch, failure, caught):
    trace = _fdinfo_trace(monkeypatch, "")
    trace.read_text.side_effect = failure
    if caught:
        assert bot.descriptor_owns_exclusive_flock(11, expected_device=0, expected_inode=0) is False
    else:
        with pytest.raises(type(failure)) as result:
            bot.descriptor_owns_exclusive_flock(11, expected_device=0, expected_inode=0)
        assert result.value is failure
    trace.major.assert_not_called()


def test_fdinfo_conversion_catch_scopes_and_inode_short_circuit(monkeypatch):
    trace = _fdinfo_trace(monkeypatch, "lock: any FLOCK ADVISORY WRITE 77 08:0a:123 0 EOF")
    failure = OSError("integer conversion")

    class InvalidInteger:
        def __int__(self):
            raise failure

    invalid = InvalidInteger()
    assert bot.descriptor_owns_exclusive_flock(invalid, expected_device=0, expected_inode=0) is False
    trace.path.assert_not_called()
    failure = ValueError("descriptor conversion")
    with pytest.raises(ValueError) as caught:
        bot.descriptor_owns_exclusive_flock(invalid, expected_device=0, expected_inode=0)
    assert caught.value is failure
    failure = OSError("device conversion is outside read catch")
    with pytest.raises(OSError) as caught:
        bot.descriptor_owns_exclusive_flock(11, expected_device=invalid, expected_inode=0)
    assert caught.value is failure
    for pid, major, minor in [(78, 8, 10), (77, 9, 10), (77, 8, 11)]:
        trace.getpid.return_value, trace.major.return_value, trace.minor.return_value = pid, major, minor
        assert bot.descriptor_owns_exclusive_flock(11, expected_device=0, expected_inode=invalid) is False
    trace.getpid.return_value, trace.major.return_value, trace.minor.return_value = 77, 8, 10
    with pytest.raises(OSError) as caught:
        bot.descriptor_owns_exclusive_flock(11, expected_device=0, expected_inode=invalid)
    assert caught.value is failure
    failure = TypeError("current getpid is outside parser catch")
    trace.getpid.side_effect = failure
    with pytest.raises(TypeError) as caught:
        bot.descriptor_owns_exclusive_flock(11, expected_device=0, expected_inode=123)
    assert caught.value is failure


@pytest.mark.parametrize("mode,sealed,override,enabled,endpoint_results,expected,steps", [
    ([], True, None, True, [], [], ["config"]),
    (True, [], None, True, [], [], ["config", "sealed"]),
    (True, True, "allow", True, [], False, ["config", "sealed", "getenv"]),
    (True, True, None, True, [True, True, True], True, ["config", "sealed", "getenv", "endpoint", "endpoint", "endpoint"]),
    (True, True, None, 1, [True, True], True, ["config", "sealed", "getenv", "endpoint", "endpoint"]),
    (True, True, None, False, [False], False, ["config", "sealed", "getenv", "endpoint"]),
    (True, True, "ALLOW", True, [True, False], False, ["config", "sealed", "getenv", "endpoint", "endpoint"]),
])
def test_bypass_keeps_eager_config_exact_true_raw_results_and_lazy_endpoints(
    monkeypatch, mode, sealed, override, enabled, endpoint_results, expected, steps,
):
    trace = Mock()
    trace.config.return_value, trace.sealed.return_value, trace.getenv.return_value = enabled, sealed, override
    trace.endpoint.side_effect = endpoint_results
    endpoints = [object(), object(), object()]
    for name, value in zip(("X_BASE", "X_UPLOAD_BASE", "OPENAI_BASE"), endpoints):
        monkeypatch.setattr(bot, name, value)
    monkeypatch.setattr(bot, "TEST_MODE", mode)
    monkeypatch.setattr(bot, "single_call_reply", SimpleNamespace(get=trace.config))
    monkeypatch.setattr(bot, "_configured_x_request_is_sealed_test_loopback", trace.sealed)
    monkeypatch.setattr(bot, "os", SimpleNamespace(getenv=trace.getenv))
    monkeypatch.setattr(bot, "LIVE_ENDPOINT_TEST_OVERRIDE_PHRASE", "allow")
    monkeypatch.setattr(bot, "endpoint_is_loopback", trace.endpoint)
    result = bot.test_mode_excludes_live_remote_writes()
    assert result is (mode if not mode else sealed if not sealed else expected)
    assert [c[0] for c in trace.mock_calls] == steps
    trace.config.assert_called_once_with("enabled")
    if trace.getenv.called:
        trace.getenv.assert_called_once_with("MRS_ALLOW_LIVE_ENDPOINTS_IN_TEST")
    assert trace.endpoint.call_args_list == [call(value) for value in endpoints[:len(endpoint_results)]]
    failure = TypeError("eager config still fails with test mode disabled")
    trace.config.side_effect = failure
    monkeypatch.setattr(bot, "TEST_MODE", False)
    with pytest.raises(TypeError) as caught:
        bot.test_mode_excludes_live_remote_writes()
    assert caught.value is failure


def _lock_trace(monkeypatch):
    trace = Mock()
    directory = SimpleNamespace(st_mode=stat.S_IFDIR, st_dev=10, st_ino=20)
    regular = SimpleNamespace(st_mode=stat.S_IFREG, st_nlink=1, st_dev=30, st_ino=40)
    base, lock_path, own_query = object(), object(), object()
    trace.bypass.return_value = False
    trace.getsockname.return_value = b"\0stage50-synthetic"
    trace.fileno.return_value = 21
    trace.fstat.side_effect = [directory, directory, regular, regular]
    trace.stat.return_value = directory
    trace.lstat.side_effect = [regular, regular]
    trace.descriptor.return_value = True
    trace.open.side_effect = [12, 22]
    trace.flock.side_effect = BlockingIOError(errno.EAGAIN, "directory held")
    trace.pread.return_value = b"pid=77\n"
    trace.getpid.return_value = 77
    trace.record.side_effect = lambda lock_type: ("record", lock_type)
    trace.fcntl.side_effect = [own_query, OSError(errno.EACCES, "write held"), OSError(errno.EAGAIN, "read held")]
    trace.unpack.return_value = (fcntl.F_UNLCK,)
    fake_os = SimpleNamespace(**{name: getattr(os, name) for name in ("O_RDONLY", "O_DIRECTORY", "O_CLOEXEC", "O_RDWR", "O_NOFOLLOW")})
    for name in ("fstat", "stat", "lstat", "open", "close", "pread", "getpid"):
        setattr(fake_os, name, getattr(trace, name))
    fake_fcntl = SimpleNamespace(**{name: getattr(fcntl, name) for name in ("LOCK_EX", "LOCK_NB", "LOCK_UN", "F_OFD_GETLK", "F_OFD_SETLK", "F_WRLCK", "F_RDLCK", "F_UNLCK")})
    fake_fcntl.flock, fake_fcntl.fcntl = trace.flock, trace.fcntl
    values = dict(
        BASE_DIR=base, LOCK_FILE=lock_path, _LOCK_FH=SimpleNamespace(fileno=trace.fileno),
        _LOCK_ACQUISITION_IDENTITY=(30, 40, 77), _STATE_DIR_LOCK_FD=11,
        _STATE_DIR_LOCK_IDENTITY=(10, 20), _LOCK_SOCKET=SimpleNamespace(getsockname=trace.getsockname),
        _LOCK_SOCKET_NAME=b"\0stage50-synthetic", _OFD_LOCK_FORMAT="current-layout",
        os=fake_os, fcntl=fake_fcntl, struct=SimpleNamespace(unpack=trace.unpack),
        descriptor_owns_exclusive_flock=trace.descriptor, ofd_lock_record=trace.record,
        test_mode_excludes_live_remote_writes=trace.bypass,
    )
    for name, value in values.items():
        monkeypatch.setattr(bot, name, value)
    return SimpleNamespace(trace=trace, directory=directory, regular=regular, base=base, lock_path=lock_path, own_query=own_query)


def test_lock_proof_exact_order_flags_bytes_and_separate_excluded_probes(monkeypatch):
    lock = _lock_trace(monkeypatch)
    assert bot.require_instance_lock_for_remote_write("synthetic write") is None
    assert lock.trace.mock_calls == [
        call.bypass(), call.getsockname(), call.fstat(11), call.stat(lock.base, follow_symlinks=True),
        call.descriptor(11, expected_device=10, expected_inode=20),
        call.open(lock.base, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC), call.fstat(12),
        call.flock(12, fcntl.LOCK_EX | fcntl.LOCK_NB), call.close(12),
        call.fileno(), call.fstat(21), call.lstat(lock.lock_path), call.pread(21, 8, 0), call.getpid(),
        call.descriptor(21, expected_device=30, expected_inode=40), call.record(fcntl.F_WRLCK),
        call.fcntl(21, fcntl.F_OFD_GETLK, ("record", fcntl.F_WRLCK)), call.unpack("current-layout", lock.own_query),
        call.open(lock.lock_path, os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW), call.fstat(22),
        call.record(fcntl.F_WRLCK), call.fcntl(22, fcntl.F_OFD_SETLK, ("record", fcntl.F_WRLCK)),
        call.record(fcntl.F_RDLCK), call.fcntl(22, fcntl.F_OFD_SETLK, ("record", fcntl.F_RDLCK)),
        call.close(22), call.lstat(lock.lock_path),
    ]


@pytest.mark.parametrize("missing", ["_LOCK_FH", "_LOCK_ACQUISITION_IDENTITY", "_STATE_DIR_LOCK_FD", "_STATE_DIR_LOCK_IDENTITY", "_LOCK_SOCKET", "_LOCK_SOCKET_NAME"])
def test_lock_missing_gates_follow_bypass_without_syscalls(monkeypatch, missing):
    lock = _lock_trace(monkeypatch)
    monkeypatch.setattr(bot, missing, None)
    with pytest.raises(RuntimeError, match="synthetic write requires"):
        bot.require_instance_lock_for_remote_write("synthetic write")
    assert lock.trace.mock_calls == [call.bypass()]
    lock.trace.bypass.return_value = True
    assert bot.require_instance_lock_for_remote_write("synthetic write") is None
    assert lock.trace.mock_calls == [call.bypass(), call.bypass()]


@pytest.mark.parametrize("failure", [OSError("singleton unavailable"), TypeError("singleton native")])
def test_singleton_wraps_only_oserror_with_original_cause(monkeypatch, failure):
    lock = _lock_trace(monkeypatch)
    lock.trace.getsockname.side_effect = failure
    with pytest.raises(RuntimeError if isinstance(failure, OSError) else TypeError) as caught:
        bot.require_instance_lock_for_remote_write("synthetic write")
    assert (caught.value.__cause__ if isinstance(failure, OSError) else caught.value) is failure
    assert lock.trace.mock_calls == [call.bypass(), call.getsockname()]


@pytest.mark.parametrize("gate, message, last", [
    ("singleton", "singleton identity", "getsockname"),
    ("directory", "state-directory lock identity", "stat"),
    ("directory_flock", "state-directory descriptor", "descriptor"),
    ("directory_probe", "state-directory path was replaced", "close"),
    ("file_bytes", "acquisition identity", "getpid"),
    ("file_flock", "instance-lock descriptor", "descriptor"),
    ("own_ofd", "another open file description", "unpack"),
    ("nofollow", "O_NOFOLLOW", "unpack"),
    ("file_probe", "instance-lock path was replaced", "close"),
    ("final_path", "path changed during", "lstat"),
])
def test_lock_refusal_gates_stop_at_original_boundary(monkeypatch, gate, message, last):
    lock = _lock_trace(monkeypatch)
    trace = lock.trace
    if gate == "singleton":
        trace.getsockname.return_value = b"changed"
    elif gate == "directory":
        trace.stat.return_value = SimpleNamespace(st_mode=stat.S_IFREG)
    elif gate == "directory_flock":
        trace.descriptor.return_value = False
    elif gate == "directory_probe":
        trace.fstat.side_effect = [lock.directory, SimpleNamespace(st_dev=99)]
    elif gate == "file_bytes":
        trace.pread.return_value = b"pid=77\nextra"
    elif gate == "file_flock":
        trace.descriptor.side_effect = [True, False]
    elif gate == "own_ofd":
        trace.unpack.return_value = (fcntl.F_WRLCK,)
    elif gate == "nofollow":
        del bot.os.O_NOFOLLOW
    elif gate == "file_probe":
        trace.fstat.side_effect = [lock.directory, lock.directory, lock.regular, SimpleNamespace(st_mode=stat.S_IFREG, st_nlink=2)]
    elif gate == "final_path":
        trace.lstat.side_effect = [lock.regular, SimpleNamespace(st_mode=stat.S_IFLNK)]
    with pytest.raises(RuntimeError, match=message):
        bot.require_instance_lock_for_remote_write("synthetic write")
    assert trace.mock_calls[-1][0] == last
    assert trace.close.call_args_list == ([call(12), call(22)] if gate in {"file_probe", "final_path"} else [call(12)] if gate not in {"singleton", "directory", "directory_flock"} else [])


@pytest.mark.parametrize("probe", ["directory", "write", "read"])
def test_unexpected_probe_acquisition_unlocks_before_refusal_and_finally_close(monkeypatch, probe):
    lock = _lock_trace(monkeypatch)
    trace = lock.trace
    if probe == "directory":
        trace.flock.side_effect = None
        expected_tail = [call.flock(12, fcntl.LOCK_UN), call.close(12)]
    else:
        trace.fcntl.side_effect = [lock.own_query] + ([OSError(errno.EACCES, "held")] if probe == "read" else []) + [None, None]
        expected_tail = [call.record(fcntl.F_UNLCK), call.fcntl(22, fcntl.F_OFD_SETLK, ("record", fcntl.F_UNLCK)), call.close(22)]
    with pytest.raises(RuntimeError, match="not held|continuously own"):
        bot.require_instance_lock_for_remote_write("synthetic write")
    assert trace.mock_calls[-len(expected_tail):] == expected_tail
    assert trace.lstat.call_count == (0 if probe == "directory" else 1)


@pytest.mark.parametrize("boundary", ["directory_open", "directory_flock", "directory_unlock", "own_query", "file_open", "read_probe", "file_unlock", "file_close"])
def test_native_probe_errors_and_close_precedence(monkeypatch, boundary):
    lock = _lock_trace(monkeypatch)
    trace = lock.trace
    failure = OSError(errno.EIO, boundary)
    if boundary == "directory_open":
        trace.open.side_effect = failure
    elif boundary == "directory_flock":
        trace.flock.side_effect = failure
    elif boundary == "directory_unlock":
        trace.flock.side_effect = [None, failure]
    elif boundary == "own_query":
        trace.fcntl.side_effect = failure
    elif boundary == "file_open":
        trace.open.side_effect = [12, failure]
    elif boundary == "read_probe":
        trace.fcntl.side_effect = [lock.own_query, OSError(errno.EAGAIN, "held"), failure]
    elif boundary == "file_unlock":
        trace.fcntl.side_effect = [lock.own_query, None, failure]
    elif boundary == "file_close":
        trace.fcntl.side_effect = [lock.own_query, OSError(errno.EPERM, "probe failure")]
        trace.close.side_effect = [None, failure]
    with pytest.raises(OSError) as caught:
        bot.require_instance_lock_for_remote_write("synthetic write")
    assert caught.value is failure
    assert trace.close.call_args_list == ([] if boundary == "directory_open" else [call(12), call(22)] if boundary in {"read_probe", "file_unlock", "file_close"} else [call(12)])
    assert trace.lstat.call_count <= 1


def test_directory_close_error_overrides_unlock_refusal(monkeypatch):
    lock = _lock_trace(monkeypatch)
    lock.trace.flock.side_effect = None
    failure = OSError(errno.EIO, "directory close")
    lock.trace.close.side_effect = failure
    with pytest.raises(OSError) as caught:
        bot.require_instance_lock_for_remote_write("synthetic write")
    assert caught.value is failure
    assert isinstance(caught.value.__context__, RuntimeError)
    assert lock.trace.mock_calls[-2:] == [call.flock(12, fcntl.LOCK_UN), call.close(12)]


def test_authority_preserves_current_issuer_operation_and_bound_live_verifier(monkeypatch):
    operation, result = object(), object()
    issuer, verifier = Mock(return_value=result), Mock()
    with monkeypatch.context() as patch:
        patch.setattr(bot, "issue_transaction_mutation_authority", issuer)
        patch.setattr(bot, "require_instance_lock_for_remote_write", verifier)
        assert bot.transaction_mutation_authority(operation) is result
        issuer.assert_called_once_with(verifier, operation=operation)
        verifier.assert_not_called()
        failure = TypeError("current issuer")
        issuer.side_effect = failure
        with pytest.raises(TypeError) as caught:
            bot.transaction_mutation_authority(operation)
        assert caught.value is failure
    lock = _lock_trace(monkeypatch)
    authority = bot.transaction_mutation_authority("synthetic mutation")
    assert lock.trace.lstat.call_count == 2
    replacement = Mock()
    monkeypatch.setattr(bot, "require_instance_lock_for_remote_write", replacement)
    monkeypatch.setattr(bot, "_LOCK_SOCKET", None)
    with pytest.raises(RuntimeError, match="later mutation requires the non-replaceable"):
        require_transaction_mutation_authority(authority, operation="later mutation")
    replacement.assert_not_called()
    assert lock.trace.mock_calls[-1] == call.bypass()
