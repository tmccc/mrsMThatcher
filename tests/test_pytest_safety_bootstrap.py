from __future__ import annotations

import os
from pathlib import Path
import socket
import subprocess

import pytest

import mrsMThatcher2 as bot


PRODUCTION_BASE = Path("/disks/disk1/etc/mrsMThatcher")
PROJECT_ROOT = Path(__file__).resolve().parents[1]
COLLECTION_ENVIRONMENT = {
    key: os.environ.get(key)
    for key in (
        "MRS_TEST_MODE",
        "MRS_BASE_DIR",
        "MRS_LOG_FILE",
        "MRS_PYTEST_RUNTIME_ROOT",
        "MRS_PYTEST_BOOTSTRAP_BASE_DIR",
        "MRS_PYTEST_BOOTSTRAP_LOG_FILE",
        "MRS_PYTEST_BOOTSTRAP_X_API_BASE_URL",
        "MRS_PYTEST_BOOTSTRAP_X_UPLOAD_BASE_URL",
        "MRS_PYTEST_BOOTSTRAP_XAI_API_BASE_URL",
        "MRS_PYTEST_WORKER_ID",
        "X_API_BASE_URL",
        "X_UPLOAD_BASE_URL",
        "XAI_API_BASE_URL",
        "X_CONSUMER_KEY",
        "XAI_API_KEY",
    )
}


def is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def test_collection_import_uses_process_local_test_environment() -> None:
    runtime_root = Path(COLLECTION_ENVIRONMENT["MRS_PYTEST_RUNTIME_ROOT"] or "")
    bootstrap_base = Path(
        COLLECTION_ENVIRONMENT["MRS_PYTEST_BOOTSTRAP_BASE_DIR"] or ""
    )
    worker_id = COLLECTION_ENVIRONMENT["MRS_PYTEST_WORKER_ID"]

    assert COLLECTION_ENVIRONMENT["MRS_TEST_MODE"] == "1"
    assert runtime_root.is_dir()
    assert bootstrap_base.parent == runtime_root
    assert worker_id in runtime_root.name
    assert str(os.getpid()) in runtime_root.name
    assert not is_relative_to(bootstrap_base.resolve(), PRODUCTION_BASE)
    assert COLLECTION_ENVIRONMENT["MRS_PYTEST_BOOTSTRAP_LOG_FILE"] == str(
        bootstrap_base / "test.log"
    )
    assert COLLECTION_ENVIRONMENT["MRS_PYTEST_BOOTSTRAP_X_API_BASE_URL"] == (
        "http://127.0.0.1:9"
    )
    assert (
        COLLECTION_ENVIRONMENT["MRS_PYTEST_BOOTSTRAP_X_UPLOAD_BASE_URL"]
        == "http://127.0.0.1:9"
    )
    assert COLLECTION_ENVIRONMENT["MRS_PYTEST_BOOTSTRAP_XAI_API_BASE_URL"] == (
        "http://127.0.0.1:9/v1"
    )
    assert not is_relative_to(
        Path(COLLECTION_ENVIRONMENT["MRS_BASE_DIR"] or "").resolve(),
        PRODUCTION_BASE,
    )
    assert not is_relative_to(
        Path(COLLECTION_ENVIRONMENT["MRS_LOG_FILE"] or "").resolve(),
        PRODUCTION_BASE,
    )
    assert COLLECTION_ENVIRONMENT["X_CONSUMER_KEY"] == "dummy"
    assert COLLECTION_ENVIRONMENT["XAI_API_KEY"] == "dummy"

    assert bot.TEST_MODE is True
    assert not is_relative_to(bot.BASE_DIR.resolve(), PRODUCTION_BASE)
    assert bot.X_BASE == "http://127.0.0.1:9"
    assert bot.X_UPLOAD_BASE == "http://127.0.0.1:9"
    assert bot.XAI_BASE == "http://127.0.0.1:9/v1"


def test_default_network_policy_denies_loopback_and_non_loopback() -> None:
    with socket.socket() as stream:
        with pytest.raises(RuntimeError, match="blocked loopback socket"):
            stream.connect_ex(("127.0.0.1", 9))
    with pytest.raises(RuntimeError, match="blocked non-loopback socket"):
        socket.create_connection(("192.0.2.1", 443), timeout=0.01)
    with pytest.raises(RuntimeError, match="blocked non-loopback socket"):
        socket.getaddrinfo("example.invalid", 443)
    if hasattr(socket.socket, "sendmsg"):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as datagram:
            with pytest.raises(RuntimeError, match="blocked non-loopback socket"):
                datagram.sendmsg(
                    [b"blocked"],
                    [],
                    0,
                    ("192.0.2.1", 443),
                )


def test_loopback_opt_in_allows_only_local_fake_server(
    allow_loopback_network: None,
) -> None:
    del allow_loopback_network
    with socket.socket() as server:
        server.bind(("127.0.0.1", 0))
        server.listen(1)
        with socket.create_connection(server.getsockname(), timeout=1) as client:
            accepted, _address = server.accept()
            accepted.close()
            assert client.getpeername() == server.getsockname()

    with pytest.raises(RuntimeError, match="blocked non-loopback socket"):
        socket.create_connection(("192.0.2.1", 443), timeout=0.01)


@pytest.mark.parametrize(
    "runtime_path",
    [
        "digest-rerun-from-20260726T105403.md.lock",
        "mrsMThatcher.control.json",
        "production_deployments/example/deployment.json",
        "production_incident_reviews/example/review.md",
    ],
)
def test_runtime_artifacts_are_ignored(runtime_path: str) -> None:
    result = subprocess.run(
        ["git", "check-ignore", "--quiet", "--no-index", runtime_path],
        cwd=PROJECT_ROOT,
        check=False,
    )
    assert result.returncode == 0, runtime_path
