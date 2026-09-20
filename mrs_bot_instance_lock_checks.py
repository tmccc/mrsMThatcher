"""Instance-lock checks and mutation-authority issuance.

The root supplies current runtime dependencies explicitly on each call. The
fixed device/inode socket-name encoding is shared with offline reconciliation.
This module performs no runtime work at import and retains no runtime authority.
"""
from __future__ import annotations

import hashlib

from typing import Any


def instance_lock_abstract_socket_name(
    base_dir: Path | None = None,
    *,
    BASE_DIR: Any,
    os: Any,
    stat: Any,
) -> bytes:
    """Return one Linux abstract-socket name bound to the state directory."""
    root = os.path.abspath(os.fspath(base_dir or BASE_DIR))
    identity = os.stat(root, follow_symlinks=True)
    if not stat.S_ISDIR(identity.st_mode):
        raise RuntimeError("Instance-lock state root is not a directory")
    return instance_lock_abstract_socket_name_for_identity(
        int(identity.st_dev),
        int(identity.st_ino),
    )


def instance_lock_abstract_socket_name_for_identity(
    device: int,
    inode: int,
) -> bytes:
    """Return one supplementary singleton name for a directory identity."""

    identity_bytes = (
        f"dev={int(device)};ino={int(inode)}"
    ).encode("ascii")
    digest = hashlib.sha256(identity_bytes).hexdigest()[:40].encode("ascii")
    return b"\0mrsMThatcher-instance-" + digest


def ofd_lock_record(
    lock_type: int,
    *,
    _OFD_LOCK_FORMAT: Any,
    os: Any,
    struct: Any,
) -> bytes:
    """Return one one-byte-range Linux OFD lock request."""
    return struct.pack(
        _OFD_LOCK_FORMAT,
        lock_type,
        os.SEEK_SET,
        0,
        1,
        0,
    )


def descriptor_owns_exclusive_flock(
    descriptor: int,
    *,
    expected_device: int,
    expected_inode: int,
    Path: Any,
    os: Any,
) -> bool:
    """Return whether Linux fdinfo binds an exclusive flock to this exact fd."""

    try:
        fdinfo = Path(f"/proc/self/fdinfo/{int(descriptor)}").read_text(
            encoding="ascii",
        )
    except (OSError, UnicodeError):
        return False
    expected_major = os.major(int(expected_device))
    expected_minor = os.minor(int(expected_device))
    for line in fdinfo.splitlines():
        fields = line.split()
        if (
            len(fields) != 9
            or fields[0] != "lock:"
            or fields[2:5] != ["FLOCK", "ADVISORY", "WRITE"]
            or fields[7:] != ["0", "EOF"]
        ):
            continue
        try:
            lock_pid = int(fields[5])
            major_text, minor_text, inode_text = fields[6].split(":", 2)
            lock_major = int(major_text, 16)
            lock_minor = int(minor_text, 16)
            lock_inode = int(inode_text)
        except (TypeError, ValueError):
            continue
        if (
            lock_pid == os.getpid()
            and lock_major == expected_major
            and lock_minor == expected_minor
            and lock_inode == int(expected_inode)
        ):
            return True
    return False


def test_mode_excludes_live_remote_writes(
    *,
    LIVE_ENDPOINT_TEST_OVERRIDE_PHRASE: Any,
    OPENAI_BASE: Any,
    TEST_MODE: Any,
    X_BASE: Any,
    X_UPLOAD_BASE: Any,
    _configured_x_request_is_sealed_test_loopback: Any,
    endpoint_is_loopback: Any,
    os: Any,
    single_call_reply: Any,
) -> bool:
    """Return whether test mode uses only explicitly local fake endpoints."""
    endpoints = [X_BASE, X_UPLOAD_BASE]
    if single_call_reply.get("enabled") is True:
        endpoints.append(OPENAI_BASE)
    return (
        TEST_MODE
        and _configured_x_request_is_sealed_test_loopback()
        and os.getenv("MRS_ALLOW_LIVE_ENDPOINTS_IN_TEST")
        != LIVE_ENDPOINT_TEST_OVERRIDE_PHRASE
        and all(
            endpoint_is_loopback(value)
            for value in endpoints
        )
    )


def require_instance_lock_for_remote_write(
    operation: str,
    *,
    BASE_DIR: Any,
    LOCK_FILE: Any,
    _LOCK_ACQUISITION_IDENTITY: Any,
    _LOCK_FH: Any,
    _LOCK_SOCKET: Any,
    _LOCK_SOCKET_NAME: Any,
    _OFD_LOCK_FORMAT: Any,
    _STATE_DIR_LOCK_FD: Any,
    _STATE_DIR_LOCK_IDENTITY: Any,
    descriptor_owns_exclusive_flock: Any,
    errno: Any,
    fcntl: Any,
    ofd_lock_record: Any,
    os: Any,
    stat: Any,
    struct: Any,
    test_mode_excludes_live_remote_writes: Any,
) -> None:
    """Prove exact OFD, pathname and abstract-singleton process ownership.

    ``F_OFD_GETLK`` on the designated descriptor distinguishes its ownership
    from a lock held by some other open file description.  A separate probe
    must remain excluded, the pathname identity must remain bound, and the
    non-filesystem singleton must still be live.  Fake-endpoint tests may
    bypass this production boundary; the explicit live-endpoint test override
    may not.
    """
    if test_mode_excludes_live_remote_writes():
        return
    if (
        _LOCK_FH is None
        or _LOCK_ACQUISITION_IDENTITY is None
        or _STATE_DIR_LOCK_FD is None
        or _STATE_DIR_LOCK_IDENTITY is None
    ):
        raise RuntimeError(
            f"{operation} requires the established process-lifetime instance lock"
        )
    if _LOCK_SOCKET is None or _LOCK_SOCKET_NAME is None:
        raise RuntimeError(
            f"{operation} requires the non-replaceable process singleton"
        )
    try:
        socket_name = _LOCK_SOCKET.getsockname()
    except OSError as exc:
        raise RuntimeError(
            f"{operation} refused because the process singleton is unavailable"
        ) from exc
    if socket_name != _LOCK_SOCKET_NAME:
        raise RuntimeError(
            f"{operation} refused because the process singleton identity changed"
        )

    expected_directory_device, expected_directory_inode = (
        _STATE_DIR_LOCK_IDENTITY
    )
    directory_opened = os.fstat(_STATE_DIR_LOCK_FD)
    directory_current = os.stat(BASE_DIR, follow_symlinks=True)
    if (
        not stat.S_ISDIR(directory_opened.st_mode)
        or not stat.S_ISDIR(directory_current.st_mode)
        or directory_opened.st_dev != expected_directory_device
        or directory_opened.st_ino != expected_directory_inode
        or directory_current.st_dev != expected_directory_device
        or directory_current.st_ino != expected_directory_inode
    ):
        raise RuntimeError(
            f"{operation} refused because the state-directory lock identity changed"
        )
    if not descriptor_owns_exclusive_flock(
        _STATE_DIR_LOCK_FD,
        expected_device=expected_directory_device,
        expected_inode=expected_directory_inode,
    ):
        raise RuntimeError(
            f"{operation} refused because the designated state-directory "
            "descriptor does not own its exclusive lock"
        )
    directory_probe = os.open(
        BASE_DIR,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        probe_identity = os.fstat(directory_probe)
        if (
            probe_identity.st_dev != expected_directory_device
            or probe_identity.st_ino != expected_directory_inode
        ):
            raise RuntimeError(
                f"{operation} refused because the state-directory path was replaced"
            )
        try:
            fcntl.flock(
                directory_probe,
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError:
            pass
        else:
            fcntl.flock(directory_probe, fcntl.LOCK_UN)
            raise RuntimeError(
                f"{operation} refused because the process-lifetime "
                "state-directory lock is not held"
            )
    finally:
        os.close(directory_probe)

    descriptor = _LOCK_FH.fileno()
    expected_device, expected_inode, expected_pid = _LOCK_ACQUISITION_IDENTITY
    held = os.fstat(descriptor)
    current = os.lstat(LOCK_FILE)
    expected_contents = f"pid={expected_pid}\n".encode("ascii")
    contents = os.pread(descriptor, len(expected_contents) + 1, 0)
    if (
        expected_pid != os.getpid()
        or not stat.S_ISREG(held.st_mode)
        or not stat.S_ISREG(current.st_mode)
        or held.st_nlink != 1
        or current.st_nlink != 1
        or held.st_dev != expected_device
        or held.st_ino != expected_inode
        or current.st_dev != expected_device
        or current.st_ino != expected_inode
        or contents != expected_contents
    ):
        raise RuntimeError(
            f"{operation} refused because the instance-lock pathname or "
            "acquisition identity changed"
        )
    if not descriptor_owns_exclusive_flock(
        descriptor,
        expected_device=expected_device,
        expected_inode=expected_inode,
    ):
        raise RuntimeError(
            f"{operation} refused because the designated instance-lock "
            "descriptor does not own its exclusive flock"
        )

    own_query = fcntl.fcntl(
        descriptor,
        fcntl.F_OFD_GETLK,
        ofd_lock_record(fcntl.F_WRLCK),
    )
    own_conflict = struct.unpack(_OFD_LOCK_FORMAT, own_query)[0]
    if own_conflict != fcntl.F_UNLCK:
        raise RuntimeError(
            f"{operation} refused because another open file description owns "
            "the instance lock"
        )

    probe_flags = os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise RuntimeError("O_NOFOLLOW is required for instance-lock verification")
    probe = os.open(LOCK_FILE, probe_flags | nofollow)
    try:
        probe_stat = os.fstat(probe)
        if (
            not stat.S_ISREG(probe_stat.st_mode)
            or probe_stat.st_nlink != 1
            or probe_stat.st_dev != expected_device
            or probe_stat.st_ino != expected_inode
        ):
            raise RuntimeError(
                f"{operation} refused because the instance-lock path was replaced"
            )
        for requested_type in (fcntl.F_WRLCK, fcntl.F_RDLCK):
            try:
                fcntl.fcntl(
                    probe,
                    fcntl.F_OFD_SETLK,
                    ofd_lock_record(requested_type),
                )
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                    raise
            else:
                fcntl.fcntl(
                    probe,
                    fcntl.F_OFD_SETLK,
                    ofd_lock_record(fcntl.F_UNLCK),
                )
                raise RuntimeError(
                    f"{operation} refused because the designated open file "
                    "description does not continuously own the exclusive "
                    "write lock"
                )
    finally:
        os.close(probe)

    after = os.lstat(LOCK_FILE)
    if (
        not stat.S_ISREG(after.st_mode)
        or after.st_nlink != 1
        or after.st_dev != expected_device
        or after.st_ino != expected_inode
    ):
        raise RuntimeError(
            f"{operation} refused because the instance-lock path changed during "
            "the ownership probe"
        )


def transaction_mutation_authority(
    operation: str,
    *,
    issue_transaction_mutation_authority: Any,
    require_instance_lock_for_remote_write: Any,
) -> TransactionMutationAuthority:
    """Issue an authority which re-proves the live instance lock on use."""

    return issue_transaction_mutation_authority(
        require_instance_lock_for_remote_write,
        operation=operation,
    )
