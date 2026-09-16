from __future__ import annotations

import ipaddress
import os
from pathlib import Path
import shutil
import socket
import tempfile
from typing import Any, Callable

import pytest

from tools.check_python_documentation import collect_violations


REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_LOG = Path("/disks/disk1/etc/mrsMThatcher/mrsMThatcher.log")
TEST_LOG_MARKERS = (
    b"/tmp/pytest-",
    b"/pytest-of-",
    b"mrsMThatcher-pytest-",
    b"127.0.0.1:",
    b"X_CONSUMER_KEY=dummy",
)
LOOPBACK_NETWORK_MODULES = {
    "tests/test_integration_harness.py",
}
LOOPBACK_NETWORK_TESTS = {
    (
        "tests/test_semantic_alignment_bakeoff.py::"
        "test_blinded_review_storage_no_provider_leak"
    ),
    (
        "tests/test_semantic_alignment_calibration.py::"
        "test_review_app_shows_case_and_saves_label"
    ),
}


def initialise_isolated_test_environment() -> Path:
    """Set safe process-local defaults before pytest imports test modules."""
    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "serial")
    runtime_root = Path(
        tempfile.mkdtemp(
            prefix=f"mrsMThatcher-pytest-{worker_id}-{os.getpid()}-"
        )
    )
    state_dir = runtime_root / "state"
    state_dir.mkdir()
    # Resolve digest cache defaults before collection against a temporary home.
    # Child CLI processes inherit this too; per-test digest path overrides remain
    # available for synthetic caches. Application default-path logic is unchanged.
    home_dir = runtime_root / "home"
    home_dir.mkdir()
    # Production-mode bootstrap tests also need private health telemetry. Keep
    # test-mode telemetry opt-in while isolating the production default path.
    user_runtime_dir = runtime_root / "runtime"
    user_runtime_dir.mkdir(mode=0o700)
    os.environ.pop("MRS_BOT_HEALTH_FILE", None)
    dead_loopback_endpoint = "http://127.0.0.1:9"
    os.environ.pop("MRS_ALLOW_LIVE_ENDPOINTS_IN_TEST", None)
    os.environ.update(
        {
            "HOME": str(home_dir),
            "XDG_RUNTIME_DIR": str(user_runtime_dir),
            "MRS_TEST_MODE": "1",
            "MRS_BASE_DIR": str(state_dir),
            "MRS_LOG_FILE": str(state_dir / "test.log"),
            "MRS_PYTEST_RUNTIME_ROOT": str(runtime_root),
            "MRS_PYTEST_BOOTSTRAP_BASE_DIR": str(state_dir),
            "MRS_PYTEST_BOOTSTRAP_LOG_FILE": str(state_dir / "test.log"),
            "MRS_PYTEST_BOOTSTRAP_X_API_BASE_URL": dead_loopback_endpoint,
            "MRS_PYTEST_BOOTSTRAP_X_UPLOAD_BASE_URL": dead_loopback_endpoint,
            "MRS_PYTEST_BOOTSTRAP_OPENAI_API_BASE_URL": (
                f"{dead_loopback_endpoint}/v1"
            ),
            "MRS_PYTEST_WORKER_ID": worker_id,
            "X_API_BASE_URL": dead_loopback_endpoint,
            "X_UPLOAD_BASE_URL": dead_loopback_endpoint,
            "OPENAI_API_BASE_URL": f"{dead_loopback_endpoint}/v1",
            "X_CONSUMER_KEY": "dummy",
            "X_CONSUMER_SECRET": "dummy",
            "X_ACCESS_TOKEN": "dummy",
            "X_ACCESS_SECRET": "dummy",
            "X_MY_USER_ID": "12345",
            "OPENAI_API_KEY": "dummy",
            "X_BEARER_TOKEN": "dummy",
            # Child HTTP clients inherit a dead local proxy. Loopback fake
            # servers remain directly reachable via NO_PROXY.
            "HTTP_PROXY": dead_loopback_endpoint,
            "HTTPS_PROXY": dead_loopback_endpoint,
            "ALL_PROXY": dead_loopback_endpoint,
            "http_proxy": dead_loopback_endpoint,
            "https_proxy": dead_loopback_endpoint,
            "all_proxy": dead_loopback_endpoint,
            "NO_PROXY": "127.0.0.1,localhost,::1",
            "no_proxy": "127.0.0.1,localhost,::1",
        }
    )
    return runtime_root


PYTEST_RUNTIME_ROOT = initialise_isolated_test_environment()


class NetworkAccessBlocked(RuntimeError):
    """Raised when a test attempts a socket operation outside its policy."""


_ALLOW_LOOPBACK_NETWORK = False
_NETWORK_GUARD_INSTALLED = False
_ORIGINAL_SOCKET_FUNCTIONS: dict[str, Callable[..., Any]] = {
    "create_connection": socket.create_connection,
    "getaddrinfo": socket.getaddrinfo,
    "gethostbyname": socket.gethostbyname,
    "gethostbyname_ex": socket.gethostbyname_ex,
    "connect": socket.socket.connect,
    "connect_ex": socket.socket.connect_ex,
    "sendto": socket.socket.sendto,
}
if hasattr(socket.socket, "sendmsg"):
    _ORIGINAL_SOCKET_FUNCTIONS["sendmsg"] = socket.socket.sendmsg


def is_loopback_host(host: Any) -> bool:
    """Return whether a socket host is explicitly local loopback."""
    if isinstance(host, bytes):
        host = host.decode("ascii", "replace")
    text = str(host or "").strip().lower()
    if text in {"localhost", "localhost.localdomain"} or text.endswith(
        ".localhost"
    ):
        return True
    text = text.strip("[]").split("%", 1)[0]
    try:
        return ipaddress.ip_address(text).is_loopback
    except ValueError:
        return False


def socket_host(address: Any) -> Any:
    """Return the host component from an INET socket address."""
    if isinstance(address, tuple) and address:
        return address[0]
    return address


def require_allowed_network_host(host: Any) -> None:
    """Enforce the default-deny test network policy."""
    if is_loopback_host(host):
        if _ALLOW_LOOPBACK_NETWORK:
            return
        raise NetworkAccessBlocked(
            "pytest blocked loopback socket access; request the "
            "allow_loopback_network fixture or marker for a local fake server"
        )
    raise NetworkAccessBlocked(
        f"pytest blocked non-loopback socket access to {host!r}"
    )


def guarded_create_connection(address: Any, *args: Any, **kwargs: Any) -> Any:
    """Guard the high-level socket connection helper."""
    require_allowed_network_host(socket_host(address))
    return _ORIGINAL_SOCKET_FUNCTIONS["create_connection"](
        address, *args, **kwargs
    )


def guarded_getaddrinfo(host: Any, *args: Any, **kwargs: Any) -> Any:
    """Block DNS resolution unless it is for opted-in loopback."""
    if host not in {None, ""}:
        require_allowed_network_host(host)
    return _ORIGINAL_SOCKET_FUNCTIONS["getaddrinfo"](
        host, *args, **kwargs
    )


def guarded_gethostbyname(host: Any) -> Any:
    """Guard legacy hostname resolution."""
    require_allowed_network_host(host)
    return _ORIGINAL_SOCKET_FUNCTIONS["gethostbyname"](host)


def guarded_gethostbyname_ex(host: Any) -> Any:
    """Guard expanded legacy hostname resolution."""
    require_allowed_network_host(host)
    return _ORIGINAL_SOCKET_FUNCTIONS["gethostbyname_ex"](host)


def guarded_socket_connect(
    stream: socket.socket, address: Any, *args: Any, **kwargs: Any
) -> Any:
    """Guard direct INET socket connections while preserving local IPC."""
    if stream.family in {socket.AF_INET, socket.AF_INET6}:
        require_allowed_network_host(socket_host(address))
    return _ORIGINAL_SOCKET_FUNCTIONS["connect"](
        stream, address, *args, **kwargs
    )


def guarded_socket_connect_ex(
    stream: socket.socket, address: Any, *args: Any, **kwargs: Any
) -> Any:
    """Guard direct INET connect_ex calls."""
    if stream.family in {socket.AF_INET, socket.AF_INET6}:
        require_allowed_network_host(socket_host(address))
    return _ORIGINAL_SOCKET_FUNCTIONS["connect_ex"](
        stream, address, *args, **kwargs
    )


def guarded_socket_sendto(
    stream: socket.socket, data: Any, *args: Any, **kwargs: Any
) -> Any:
    """Guard connectionless INET datagrams."""
    if stream.family in {socket.AF_INET, socket.AF_INET6}:
        address = args[-1] if args else kwargs.get("address")
        require_allowed_network_host(socket_host(address))
    return _ORIGINAL_SOCKET_FUNCTIONS["sendto"](
        stream, data, *args, **kwargs
    )


def guarded_socket_sendmsg(
    stream: socket.socket, buffers: Any, *args: Any, **kwargs: Any
) -> Any:
    """Guard connectionless INET sendmsg destinations."""
    address = kwargs.get("address")
    if address is None and len(args) >= 3:
        address = args[2]
    if (
        stream.family in {socket.AF_INET, socket.AF_INET6}
        and address is not None
    ):
        require_allowed_network_host(socket_host(address))
    return _ORIGINAL_SOCKET_FUNCTIONS["sendmsg"](
        stream, buffers, *args, **kwargs
    )


def install_network_guard() -> None:
    """Install process-wide socket guards before collection."""
    global _NETWORK_GUARD_INSTALLED
    if _NETWORK_GUARD_INSTALLED:
        return
    socket.create_connection = guarded_create_connection
    socket.getaddrinfo = guarded_getaddrinfo
    socket.gethostbyname = guarded_gethostbyname
    socket.gethostbyname_ex = guarded_gethostbyname_ex
    socket.socket.connect = guarded_socket_connect
    socket.socket.connect_ex = guarded_socket_connect_ex
    socket.socket.sendto = guarded_socket_sendto
    if "sendmsg" in _ORIGINAL_SOCKET_FUNCTIONS:
        socket.socket.sendmsg = guarded_socket_sendmsg
    _NETWORK_GUARD_INSTALLED = True


def uninstall_network_guard() -> None:
    """Restore socket functions when pytest releases this process."""
    global _NETWORK_GUARD_INSTALLED
    if not _NETWORK_GUARD_INSTALLED:
        return
    socket.create_connection = _ORIGINAL_SOCKET_FUNCTIONS["create_connection"]
    socket.getaddrinfo = _ORIGINAL_SOCKET_FUNCTIONS["getaddrinfo"]
    socket.gethostbyname = _ORIGINAL_SOCKET_FUNCTIONS["gethostbyname"]
    socket.gethostbyname_ex = _ORIGINAL_SOCKET_FUNCTIONS["gethostbyname_ex"]
    socket.socket.connect = _ORIGINAL_SOCKET_FUNCTIONS["connect"]
    socket.socket.connect_ex = _ORIGINAL_SOCKET_FUNCTIONS["connect_ex"]
    socket.socket.sendto = _ORIGINAL_SOCKET_FUNCTIONS["sendto"]
    if "sendmsg" in _ORIGINAL_SOCKET_FUNCTIONS:
        socket.socket.sendmsg = _ORIGINAL_SOCKET_FUNCTIONS["sendmsg"]
    _NETWORK_GUARD_INSTALLED = False


install_network_guard()


def pytest_sessionstart(session: pytest.Session) -> None:
    """Require documented maintained Python APIs before test collection."""
    del session
    if os.environ.get("PYTEST_XDIST_WORKER"):
        return
    violations = collect_violations(REPOSITORY_ROOT)
    if not violations:
        return
    details = "\n".join(f"  {violation.render()}" for violation in violations)
    raise pytest.UsageError(
        "Python documentation prerequisite failed before test collection:\n"
        f"{details}"
    )


def pytest_configure(config: pytest.Config) -> None:
    """Register the explicit local-fake-server network opt-in."""
    config.addinivalue_line(
        "markers",
        "allow_loopback_network: permit loopback sockets for a local fake server",
    )


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Opt existing local-server suites into loopback-only networking."""
    for item in items:
        module_nodeid = item.nodeid.split("::", 1)[0]
        if (
            module_nodeid in LOOPBACK_NETWORK_MODULES
            or item.nodeid in LOOPBACK_NETWORK_TESTS
        ):
            item.add_marker(pytest.mark.allow_loopback_network)


@pytest.hookimpl(hookwrapper=True, tryfirst=True)
def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None):
    """Apply a test's loopback policy throughout setup, call and teardown."""
    global _ALLOW_LOOPBACK_NETWORK
    del nextitem
    previous = _ALLOW_LOOPBACK_NETWORK
    _ALLOW_LOOPBACK_NETWORK = (
        item.get_closest_marker("allow_loopback_network") is not None
        or "allow_loopback_network" in item.fixturenames
    )
    try:
        yield
    finally:
        _ALLOW_LOOPBACK_NETWORK = previous


def pytest_unconfigure(config: pytest.Config) -> None:
    """Release process-global guards and the process-owned temporary tree."""
    del config
    uninstall_network_guard()
    shutil.rmtree(PYTEST_RUNTIME_ROOT, ignore_errors=True)


@pytest.fixture
def allow_loopback_network() -> None:
    """Explicitly opt a test into loopback-only fake-server networking."""


def process_has_open_path(target: Path) -> bool:
    fd_dir = Path("/proc/self/fd")
    if not fd_dir.is_dir():
        return False
    target = target.resolve()
    for fd in fd_dir.iterdir():
        try:
            if fd.resolve() == target:
                return True
        except OSError:
            continue
    return False


def appended_bytes(path: Path, *, inode: int | None, offset: int) -> bytes:
    if inode is None:
        return path.read_bytes() if path.exists() else b""
    chunks: list[bytes] = []
    candidates = [path, *(sorted(path.parent.glob(path.name + ".*")))]
    matched_original = False
    for candidate in candidates:
        try:
            stat = candidate.stat()
        except OSError:
            continue
        if stat.st_ino == inode:
            matched_original = True
            with candidate.open("rb") as handle:
                handle.seek(min(offset, stat.st_size))
                chunks.append(handle.read())
        elif candidate == path:
            chunks.append(candidate.read_bytes())
    if not matched_original and path.exists():
        chunks.append(path.read_bytes())
    return b"".join(chunks)


@pytest.fixture(autouse=True)
def production_log_fd_guard():
    assert not process_has_open_path(PRODUCTION_LOG), "pytest process has production log open before test"
    yield
    assert not process_has_open_path(PRODUCTION_LOG), "pytest process has production log open after test"


@pytest.fixture(scope="session", autouse=True)
def production_log_marker_guard():
    try:
        before = PRODUCTION_LOG.stat()
        inode, offset = before.st_ino, before.st_size
    except OSError:
        inode, offset = None, 0
    yield
    data = appended_bytes(PRODUCTION_LOG, inode=inode, offset=offset)
    found = [marker.decode("ascii", "replace") for marker in TEST_LOG_MARKERS if marker in data]
    assert not found, f"test markers reached production log appended interval: {found}"
