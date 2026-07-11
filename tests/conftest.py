from __future__ import annotations

from pathlib import Path

import pytest


PRODUCTION_LOG = Path("/disks/disk1/etc/mrsMThatcher/mrsMThatcher.log")
TEST_LOG_MARKERS = (
    b"/tmp/pytest-",
    b"/pytest-of-",
    b"127.0.0.1:",
    b"X_CONSUMER_KEY=dummy",
)


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
