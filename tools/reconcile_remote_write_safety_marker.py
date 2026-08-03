#!/usr/bin/env python3
"""Reconcile reviewed remote-write barriers while the bot is offline.

This command does not decide whether an ambiguous X outcome has been reconciled.
An operator must establish that separately and supply exact reviewed identities.
The default operation archives the active ambiguity marker.  The narrower
``--reconcile-unattached-media-upload`` operation accepts only the exact
media-only, pre-tweet incident: it proves that no local tweet-create authority
exists, archives one externally bound sending receipt/fence pair, and retires
that pair while deliberately preserving the ambiguity marker.  The default
marker operation must then be run separately.

Both operations prove that the bot's process-lifetime lock is available and
make private read-only hard-linked archives and audit receipts durable before
removing an active name.  The last active barrier in each operation is retired
last.  A hard process loss before archival is complete therefore leaves a
fail-closed active marker, receipt or fence present.

The live daemon owns an exclusive lock on the state-directory inode, a
supplementary directory-identity-bound Linux abstract socket, and BSD plus
open-file-description locks on ``mrsMThatcher.lock`` for its complete lifetime.
This tool deliberately acquires every boundary before touching the marker.
The directory lock survives pathname aliases and separate network namespaces,
so replacing the lock-file pathname cannot create a second supported
reconciliation namespace.  It also rejects symbolic links at the project,
lock, marker and archive boundaries.
"""

from __future__ import annotations

import argparse
import ctypes
import errno
import fcntl
import hashlib
import json
import os
import re
import socket
import stat
import struct
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from remote_media_upload_receipt import (  # noqa: E402
    DOCUMENT_KIND as MEDIA_RECEIPT_DOCUMENT_KIND,
    FENCE_DOCUMENT_KIND as MEDIA_FENCE_DOCUMENT_KIND,
    RECEIPT_MAX_BYTES as MAX_MEDIA_RECEIPT_BYTES,
    RECEIPT_MODE as MEDIA_RECEIPT_MODE,
    MediaUploadReceiptError,
    _required_fence_snapshot,
    fence_path_for_receipt,
    inspect_media_upload_receipt,
)


LOCK_BASENAME = "mrsMThatcher.lock"
MARKER_BASENAME = "ambiguous_post_outcome.json"
RESTART_BARRIER_BASENAME = "ambiguous_post_outcome.restart_barrier.json"
DEFAULT_ARCHIVE_BASENAME = "remote_write_safety_marker_archive"
MEDIA_RECEIPT_BASENAME = "remote_media_upload_receipt.json"
MEDIA_FENCE_BASENAME = "remote_media_upload_receipt.json.fence.json"
TWEET_AUTHORITY_BASENAMES = (
    "regular_post_receipt.json",
    "meme_post_receipt.json",
    "confirmed_reply_receipt.json",
    "historical_context_reply_receipt.json",
    "remote_write_transport_journal.json",
    "remote_write_transport_fence.json",
)
TWEET_AUTHORITY_PREFIXES = (
    ".remote_write_transport_journal.json.transition.",
    ".remote_write_transport_journal.json.retirement-guard.",
)
MAX_MARKER_BYTES = 64 * 1024
MAX_LOCK_RECORD_BYTES = 128
SHA256_RE = re.compile(r"[0-9a-f]{64}")
SAFE_BASENAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
LOCK_RECORD_RE = re.compile(rb"pid=([1-9][0-9]{0,18})\n")
RENAME_NOREPLACE = 1
OFD_LOCK_FORMAT = "hhqqi"


class MarkerReconciliationError(RuntimeError):
    """Base class for an offline marker-reconciliation refusal."""


class BotStillRunningError(MarkerReconciliationError):
    """The bot's process-lifetime instance lock is still held."""


class UnsafeReconciliationPathError(MarkerReconciliationError):
    """A required path is symbolic, special, ambiguous or escapes policy."""


class MarkerIdentityError(MarkerReconciliationError):
    """The marker identity or content differs from the reviewed marker."""


class ArchiveCommitError(MarkerReconciliationError):
    """The marker could not be durably committed to its archive."""


class UnattachedMediaReconciliationError(MarkerReconciliationError):
    """The media incident is not the exact offline-abandonment case."""


@dataclass(frozen=True)
class MarkerArchiveResult:
    """Describe one completed byte-preserving offline archive operation."""

    schema_version: int
    operation: str
    project_root: str
    source_marker: str
    active_barrier_names: tuple[str, ...]
    restart_barrier_present_before_reconciliation: bool
    archive_path: str
    receipt_path: str
    marker_sha256: str
    marker_size: int
    marker_device: int
    marker_inode: int
    original_mode: str
    archived_mode: str
    reconciliation_reference: str
    archived_at_epoch: int
    bot_instance_lock_acquired: bool
    archive_inode_preserved: bool
    archive_and_receipt_durable_before_source_removal: bool
    active_marker_removal_is_final_transition: bool
    restart_barrier_retired_last: bool
    successful_return_requires_source_absent: bool
    successful_return_requires_all_active_barriers_absent: bool

    def to_dict(self) -> dict[str, object]:
        """Return a deterministic JSON-compatible representation."""

        value = asdict(self)
        value["active_barrier_names"] = list(self.active_barrier_names)
        return value


@dataclass(frozen=True)
class UnattachedMediaArchiveResult:
    """Describe one exact, evidence-preserving media abandonment."""

    schema_version: int
    operation: str
    project_root: str
    marker_sha256: str
    marker_names_preserved: tuple[str, ...]
    media_transaction_id: str
    lane: str
    image_basename: str
    image_sha256: str
    receipt_archive_path: str
    receipt_sha256: str
    receipt_size: int
    receipt_device: int
    receipt_inode: int
    fence_archive_path: str
    fence_sha256: str
    fence_size: int
    fence_device: int
    fence_inode: int
    audit_receipt_path: str
    reconciliation_reference: str
    archived_at_epoch: int
    accepted_media_disposition: str
    no_tweet_create_authority_present: bool
    operator_confirmed_no_tweet_create_attempted: bool
    operator_confirmed_unattached_media_abandoned: bool
    remote_media_id_absent: bool
    bot_instance_lock_acquired: bool
    media_archives_and_audit_durable_before_active_removal: bool
    media_receipt_retired_before_fence: bool
    active_marker_preserved_after_media_reconciliation: bool
    successful_return_requires_active_media_pair_absent: bool

    def to_dict(self) -> dict[str, object]:
        """Return a deterministic JSON-compatible representation."""

        value = asdict(self)
        value["marker_names_preserved"] = list(self.marker_names_preserved)
        return value


def instance_lock_abstract_socket_name(project_root: Path) -> bytes:
    """Return the daemon/reconciler singleton name for one state directory."""

    root = os.path.abspath(os.fspath(project_root))
    identity = os.stat(root, follow_symlinks=True)
    if not stat.S_ISDIR(identity.st_mode):
        raise UnsafeReconciliationPathError("project root is not a directory")
    return instance_lock_abstract_socket_name_for_identity(
        int(identity.st_dev),
        int(identity.st_ino),
    )


def instance_lock_abstract_socket_name_for_identity(
    device: int,
    inode: int,
) -> bytes:
    """Return the supplementary singleton name for one directory inode."""

    identity_bytes = (
        f"dev={int(device)};ino={int(inode)}"
    ).encode("ascii")
    digest = hashlib.sha256(identity_bytes).hexdigest()[:40].encode("ascii")
    return b"\0mrsMThatcher-instance-" + digest


def _ofd_lock_record(lock_type: int) -> bytes:
    """Return one one-byte-range Linux open-file-description lock request."""

    return struct.pack(
        OFD_LOCK_FORMAT,
        lock_type,
        os.SEEK_SET,
        0,
        1,
        0,
    )


def _descriptor_owns_exclusive_flock(
    descriptor: int,
    *,
    expected_device: int,
    expected_inode: int,
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


def _canonical_json_bytes(value: object) -> bytes:
    """Return stable JSON bytes for an archive receipt."""

    return (
        json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    ).encode("utf-8")


def _normalise_sha256(value: str) -> str:
    """Validate and return a lowercase SHA-256 string."""

    candidate = str(value).strip().lower()
    if not SHA256_RE.fullmatch(candidate):
        raise MarkerIdentityError("expected marker SHA-256 must be 64 lowercase hex digits")
    return candidate


def _normalise_transaction_id(value: str) -> str:
    """Validate one exact media transaction identifier."""

    candidate = str(value).strip().lower()
    if not SHA256_RE.fullmatch(candidate):
        raise UnattachedMediaReconciliationError(
            "expected media transaction ID must be 64 lowercase hex digits"
        )
    return candidate


def _normalise_identity_integer(
    value: int,
    *,
    label: str,
    allow_zero: bool,
) -> int:
    """Validate one externally recorded filesystem identity integer."""

    minimum = 0 if allow_zero else 1
    if type(value) is not int or value < minimum or value > (2**64 - 1):
        raise UnattachedMediaReconciliationError(
            f"expected {label} is outside the supported filesystem identity range"
        )
    return value


def _normalise_reference(value: str) -> str:
    """Validate one bounded, single-line operator evidence reference."""

    reference = str(value).strip()
    if not reference or len(reference) > 500 or any(
        character in reference for character in "\r\n\x00"
    ):
        raise MarkerReconciliationError(
            "reconciliation reference must be one non-empty line of at most 500 characters"
        )
    return reference


@dataclass
class _OfflineInstanceLocks:
    """Own every supported stopped-daemon exclusion boundary."""

    project: Path
    project_fd: int
    project_identity: os.stat_result
    instance_socket: socket.socket
    socket_name: bytes
    lock_fd: int
    lock_identity: os.stat_result

    def revalidate(self) -> None:
        """Re-prove all path and lock identities immediately before mutation."""

        _revalidate_locked_instance_lock(
            self.project_fd,
            self.lock_fd,
            self.lock_identity,
            self.instance_socket,
            self.socket_name,
        )
        _require_project_path_identity(
            self.project,
            self.project_fd,
            self.project_identity,
        )

    def close(self) -> None:
        """Release the lock file, singleton, and directory lock."""

        for descriptor in (self.lock_fd, self.project_fd):
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            self.instance_socket.close()
        except OSError:
            pass


def _acquire_offline_instance_locks(project_root: Path) -> _OfflineInstanceLocks:
    """Acquire the exact complete lock set used by the live daemon."""

    try:
        project, project_fd = _open_project_directory_without_symlinks(
            Path(project_root)
        )
    except OSError as exc:
        raise UnsafeReconciliationPathError(
            "project-root component could not be opened without following links"
        ) from exc
    instance_socket: socket.socket | None = None
    lock_fd: int | None = None
    try:
        try:
            fcntl.flock(project_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BotStillRunningError(
                "bot state-directory lock is held; stop the service and wait "
                "for the process to exit"
            ) from exc
        project_identity = os.fstat(project_fd)
        _require_project_path_identity(project, project_fd, project_identity)
        if not _descriptor_owns_exclusive_flock(
            project_fd,
            expected_device=int(project_identity.st_dev),
            expected_inode=int(project_identity.st_ino),
        ):
            raise BotStillRunningError(
                "state-directory flock acquisition could not be proved"
            )

        instance_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        socket_name = instance_lock_abstract_socket_name_for_identity(
            int(project_identity.st_dev),
            int(project_identity.st_ino),
        )
        try:
            instance_socket.bind(socket_name)
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                raise BotStillRunningError(
                    "bot process singleton is active; stop the service and "
                    "wait for the process to exit"
                ) from exc
            raise

        lock_fd, lock_identity = _open_verified_regular(
            project_fd,
            LOCK_BASENAME,
            label="bot instance lock",
            flags=os.O_RDWR,
            require_single_link=True,
        )
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BotStillRunningError(
                "bot instance lock is held; stop the service and wait for the process to exit"
            ) from exc
        if not _descriptor_owns_exclusive_flock(
            lock_fd,
            expected_device=int(lock_identity.st_dev),
            expected_inode=int(lock_identity.st_ino),
        ):
            raise BotStillRunningError(
                "file-instance flock acquisition could not be proved"
            )
        try:
            fcntl.fcntl(
                lock_fd,
                fcntl.F_OFD_SETLK,
                _ofd_lock_record(fcntl.F_WRLCK),
            )
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise BotStillRunningError(
                    "bot OFD instance lock is held; stop the service and wait "
                    "for the process to exit"
                ) from exc
            raise
        locks = _OfflineInstanceLocks(
            project=project,
            project_fd=project_fd,
            project_identity=project_identity,
            instance_socket=instance_socket,
            socket_name=socket_name,
            lock_fd=lock_fd,
            lock_identity=lock_identity,
        )
        locks.revalidate()
        return locks
    except BaseException:
        if lock_fd is not None:
            try:
                os.close(lock_fd)
            except OSError:
                pass
        if instance_socket is not None:
            try:
                instance_socket.close()
            except OSError:
                pass
        try:
            os.close(project_fd)
        except OSError:
            pass
        raise


def _validate_basename(value: str, *, label: str) -> str:
    """Accept one conservative directory-entry name without path syntax."""

    candidate = str(value)
    if (
        not SAFE_BASENAME_RE.fullmatch(candidate)
        or candidate in {".", ".."}
        or os.sep in candidate
        or (os.altsep is not None and os.altsep in candidate)
    ):
        raise UnsafeReconciliationPathError(f"{label} is not a safe basename")
    return candidate


def _open_project_directory_without_symlinks(path: Path) -> tuple[Path, int]:
    """Open every absolute project component with ``openat`` and no-follow."""

    supplied = Path(path)
    if ".." in supplied.parts:
        raise UnsafeReconciliationPathError(
            "project root must not contain parent-directory traversal"
        )
    absolute = Path(os.path.abspath(os.fspath(supplied)))
    if not absolute.is_absolute():
        raise UnsafeReconciliationPathError("project root must be absolute")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = os.open(absolute.anchor, flags)
    try:
        for component in absolute.parts[1:]:
            next_descriptor = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = next_descriptor
        opened = os.fstat(descriptor)
        if not stat.S_ISDIR(opened.st_mode):
            raise UnsafeReconciliationPathError(
                "project root is not a directory"
            )
        return absolute, descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _entry_stat(directory_fd: int, basename: str) -> os.stat_result:
    """Read one directory entry without following a symbolic link."""

    return os.stat(basename, dir_fd=directory_fd, follow_symlinks=False)


def _require_regular_entry(
    directory_fd: int,
    basename: str,
    *,
    label: str,
) -> os.stat_result:
    """Require one existing non-symbolic ordinary file."""

    try:
        metadata = _entry_stat(directory_fd, basename)
    except FileNotFoundError as exc:
        raise UnsafeReconciliationPathError(f"{label} is missing") from exc
    if not stat.S_ISREG(metadata.st_mode):
        raise UnsafeReconciliationPathError(f"{label} is not an ordinary file")
    return metadata


def _same_inode(left: os.stat_result, right: os.stat_result) -> bool:
    """Return whether two stat records identify the same filesystem object."""

    return (left.st_dev, left.st_ino) == (right.st_dev, right.st_ino)


def _require_project_path_identity(
    project: Path,
    project_fd: int,
    expected: os.stat_result,
) -> None:
    """Require the reviewed pathname to remain bound to the opened directory."""

    try:
        opened = os.fstat(project_fd)
        current = os.lstat(project)
    except OSError as exc:
        raise UnsafeReconciliationPathError(
            "project-root pathname is unavailable during reconciliation"
        ) from exc
    if (
        not stat.S_ISDIR(expected.st_mode)
        or not stat.S_ISDIR(opened.st_mode)
        or not stat.S_ISDIR(current.st_mode)
        or not _same_inode(expected, opened)
        or not _same_inode(expected, current)
    ):
        raise UnsafeReconciliationPathError(
            "project-root pathname identity changed during reconciliation"
        )


def _open_verified_regular(
    directory_fd: int,
    basename: str,
    *,
    label: str,
    flags: int,
    require_single_link: bool = False,
) -> tuple[int, os.stat_result]:
    """Open an ordinary file without following links and bind its path identity."""

    before = _require_regular_entry(directory_fd, basename, label=label)
    if require_single_link and before.st_nlink != 1:
        raise UnsafeReconciliationPathError(
            f"{label} must have exactly one filesystem link"
        )
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    cloexec = getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = os.open(
            basename,
            flags | nofollow | cloexec,
            dir_fd=directory_fd,
        )
    except OSError as exc:
        raise UnsafeReconciliationPathError(f"{label} could not be opened safely") from exc
    try:
        opened = os.fstat(descriptor)
        after = _require_regular_entry(directory_fd, basename, label=label)
        if not _same_inode(before, opened) or not _same_inode(opened, after):
            raise UnsafeReconciliationPathError(
                f"{label} identity changed while it was opened"
            )
        if require_single_link and (
            opened.st_nlink != 1 or after.st_nlink != 1
        ):
            raise UnsafeReconciliationPathError(
                f"{label} must have exactly one filesystem link"
            )
        return descriptor, opened
    except BaseException:
        os.close(descriptor)
        raise


def _read_all(descriptor: int, *, maximum: int) -> bytes:
    """Read a bounded file from its beginning."""

    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, min(65536, maximum + 1 - total))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > maximum:
            raise MarkerIdentityError(
                f"remote-write safety marker exceeds {maximum} bytes"
            )


def _entry_absent(directory_fd: int, basename: str) -> bool:
    """Return whether one directory entry is absent without following links."""

    try:
        _entry_stat(directory_fd, basename)
    except FileNotFoundError:
        return True
    return False


@dataclass(frozen=True)
class _ActiveBarrierSet:
    """Bind the complete supported active-barrier namespace to one inode."""

    source_name: str
    names: tuple[str, ...]
    descriptor: int
    identity: os.stat_result


def _open_active_barrier_set(project_fd: int) -> _ActiveBarrierSet:
    """Open the legacy marker, its successor, or their exact hard-link pair.

    These are the only supported active layouts:

    * a legacy one-link marker awaiting stopped offline reconciliation;
    * the exact two-link marker/restart-barrier pair;
    * the one-link restart barrier surviving loss of the legacy name.

    A different inode, extra hard link, symbolic link or special file fails
    closed rather than being treated as reconciled evidence.
    """

    marker_present = not _entry_absent(project_fd, MARKER_BASENAME)
    successor_present = not _entry_absent(
        project_fd,
        RESTART_BARRIER_BASENAME,
    )
    if not marker_present and not successor_present:
        raise MarkerIdentityError(
            "no active remote-write safety marker or restart barrier exists"
        )

    names = tuple(
        name
        for name, present in (
            (MARKER_BASENAME, marker_present),
            (RESTART_BARRIER_BASENAME, successor_present),
        )
        if present
    )
    source_name = (
        MARKER_BASENAME if marker_present else RESTART_BARRIER_BASENAME
    )
    descriptor, identity = _open_verified_regular(
        project_fd,
        source_name,
        label="active remote-write safety barrier",
        flags=os.O_RDONLY,
        require_single_link=False,
    )
    try:
        expected_links = len(names)
        opened = os.fstat(descriptor)
        current_entries: list[os.stat_result] = []
        for name in names:
            current = _require_regular_entry(
                project_fd,
                name,
                label=f"active remote-write safety barrier {name}",
            )
            current_entries.append(current)
            if not _same_inode(identity, current):
                raise UnsafeReconciliationPathError(
                    "active remote-write safety names are not the exact expected "
                    "same-inode set"
                )
        if opened.st_nlink != expected_links or any(
            current.st_nlink != expected_links
            for current in current_entries
        ):
            raise UnsafeReconciliationPathError(
                "active remote-write safety barrier must have exactly one "
                "filesystem link per supported active name"
            )
        return _ActiveBarrierSet(
            source_name=source_name,
            names=names,
            descriptor=descriptor,
            identity=identity,
        )
    except BaseException:
        os.close(descriptor)
        raise


def _require_active_barrier_identity(
    project_fd: int,
    *,
    expected_names: tuple[str, ...],
    expected_identity: os.stat_result,
    opened_descriptor: int,
    expected_total_links: int,
) -> None:
    """Revalidate every supported active name and reject namespace drift."""

    expected = set(expected_names)
    for name in (MARKER_BASENAME, RESTART_BARRIER_BASENAME):
        if name not in expected:
            if not _entry_absent(project_fd, name):
                raise MarkerIdentityError(
                    f"unexpected active remote-write safety name appeared: {name}"
                )
            continue
        current = _require_regular_entry(
            project_fd,
            name,
            label=f"active remote-write safety barrier {name}",
        )
        if (
            not _same_inode(expected_identity, current)
            or current.st_nlink != expected_total_links
        ):
            raise MarkerIdentityError(
                "active remote-write safety barrier identity changed"
            )
    opened = os.fstat(opened_descriptor)
    if (
        not _same_inode(expected_identity, opened)
        or opened.st_nlink != expected_total_links
    ):
        raise MarkerIdentityError(
            "opened remote-write safety barrier identity changed"
        )


def _fsync_directory(descriptor: int) -> None:
    """Synchronise an already opened directory descriptor."""

    os.fsync(descriptor)


def _rename_noreplace(
    source_basename: str,
    destination_basename: str,
    *,
    source_directory_fd: int,
    destination_directory_fd: int,
    label: str,
) -> None:
    """Atomically rename one entry without ever replacing the destination."""

    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise ArchiveCommitError(
            "atomic no-replace rename is unavailable on this platform"
        )
    renameat2.argtypes = (
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    )
    renameat2.restype = ctypes.c_int
    result = renameat2(
        source_directory_fd,
        os.fsencode(source_basename),
        destination_directory_fd,
        os.fsencode(destination_basename),
        RENAME_NOREPLACE,
    )
    if result == 0:
        return

    error_number = ctypes.get_errno()
    if error_number in {errno.EEXIST, errno.ENOTEMPTY}:
        raise ArchiveCommitError(f"{label} already exists")
    if error_number in {errno.ENOSYS, errno.EINVAL, errno.EOPNOTSUPP}:
        raise ArchiveCommitError(
            "atomic no-replace rename is unavailable on this filesystem"
        )
    raise OSError(
        error_number,
        os.strerror(error_number),
        f"{source_basename} -> {destination_basename}",
    )


def _link_noreplace(
    source_basename: str,
    destination_basename: str,
    *,
    source_directory_fd: int,
    destination_directory_fd: int,
    label: str,
) -> None:
    """Atomically add one same-inode archive name without replacing a peer."""

    try:
        os.link(
            source_basename,
            destination_basename,
            src_dir_fd=source_directory_fd,
            dst_dir_fd=destination_directory_fd,
            follow_symlinks=False,
        )
    except FileExistsError as exc:
        raise ArchiveCommitError(f"{label} already exists") from exc
    except OSError as exc:
        if exc.errno in {errno.EXDEV, errno.EPERM, errno.EOPNOTSUPP}:
            raise ArchiveCommitError(
                "same-filesystem no-replace archive linking is unavailable"
            ) from exc
        raise


def _recorded_lock_pid(lock_fd: int) -> int:
    """Read one stable, canonical daemon PID record from the locked file."""

    before = os.fstat(lock_fd)
    if before.st_size > MAX_LOCK_RECORD_BYTES:
        raise UnsafeReconciliationPathError("bot instance lock record is too large")
    data = os.pread(lock_fd, MAX_LOCK_RECORD_BYTES + 1, 0)
    after = os.fstat(lock_fd)
    if (
        before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
        or before.st_ctime_ns != after.st_ctime_ns
        or before.st_mtime_ns != after.st_mtime_ns
        or len(data) != after.st_size
    ):
        raise UnsafeReconciliationPathError(
            "bot instance lock record changed while it was inspected"
        )
    match = LOCK_RECORD_RE.fullmatch(data)
    if match is None:
        raise UnsafeReconciliationPathError(
            "bot instance lock does not contain one canonical pid record"
        )
    return int(match.group(1))


def _revalidate_locked_instance_lock(
    project_fd: int,
    lock_fd: int,
    expected: os.stat_result,
    instance_socket: socket.socket,
    expected_socket_name: bytes,
) -> None:
    """Prove the locked inode still owns the path and names no live daemon."""

    project_identity = os.fstat(project_fd)
    if not _descriptor_owns_exclusive_flock(
        project_fd,
        expected_device=int(project_identity.st_dev),
        expected_inode=int(project_identity.st_ino),
    ):
        raise BotStillRunningError(
            "the reconciler no longer owns the exact state-directory lock"
        )
    try:
        current_socket_name = instance_socket.getsockname()
    except OSError as exc:
        raise BotStillRunningError(
            "the reconciler no longer owns the supplementary process singleton"
        ) from exc
    if current_socket_name != expected_socket_name:
        raise BotStillRunningError(
            "the reconciler process singleton identity changed"
        )

    directory_probe = os.open(
        ".",
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_CLOEXEC", 0),
        dir_fd=project_fd,
    )
    try:
        try:
            fcntl.flock(
                directory_probe,
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError:
            pass
        else:
            fcntl.flock(directory_probe, fcntl.LOCK_UN)
            raise BotStillRunningError(
                "the reconciler no longer owns the state-directory lock"
            )
    finally:
        os.close(directory_probe)

    opened = os.fstat(lock_fd)
    current = _require_regular_entry(
        project_fd,
        LOCK_BASENAME,
        label="bot instance lock",
    )
    if (
        not _same_inode(expected, opened)
        or not _same_inode(opened, current)
        or opened.st_nlink != 1
        or current.st_nlink != 1
    ):
        raise UnsafeReconciliationPathError(
            "bot instance lock identity changed after exclusive acquisition"
        )
    if not _descriptor_owns_exclusive_flock(
        lock_fd,
        expected_device=int(opened.st_dev),
        expected_inode=int(opened.st_ino),
    ):
        raise BotStillRunningError(
            "the reconciler no longer owns the exact file-instance flock"
        )
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise BotStillRunningError(
            "bot instance lock is no longer held exclusively"
        ) from exc
    query = fcntl.fcntl(
        lock_fd,
        fcntl.F_OFD_GETLK,
        _ofd_lock_record(fcntl.F_WRLCK),
    )
    if struct.unpack(OFD_LOCK_FORMAT, query)[0] != fcntl.F_UNLCK:
        raise BotStillRunningError(
            "the reconciler no longer owns the exact OFD instance lock"
        )
    lock_probe = os.open(
        LOCK_BASENAME,
        os.O_RDWR
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        dir_fd=project_fd,
    )
    try:
        probe_identity = os.fstat(lock_probe)
        if not _same_inode(opened, probe_identity):
            raise UnsafeReconciliationPathError(
                "bot instance lock path changed during OFD ownership proof"
            )
        for requested_type in (fcntl.F_WRLCK, fcntl.F_RDLCK):
            try:
                fcntl.fcntl(
                    lock_probe,
                    fcntl.F_OFD_SETLK,
                    _ofd_lock_record(requested_type),
                )
            except OSError as exc:
                if exc.errno not in {errno.EACCES, errno.EAGAIN}:
                    raise
            else:
                fcntl.fcntl(
                    lock_probe,
                    fcntl.F_OFD_SETLK,
                    _ofd_lock_record(fcntl.F_UNLCK),
                )
                raise BotStillRunningError(
                    "the reconciler no longer owns the exclusive OFD write lock"
                )
    finally:
        os.close(lock_probe)

    recorded_pid = _recorded_lock_pid(lock_fd)
    if recorded_pid == os.getpid():
        return
    try:
        os.kill(recorded_pid, 0)
    except ProcessLookupError:
        return
    except PermissionError as exc:
        raise BotStillRunningError(
            f"recorded bot pid {recorded_pid} is still live or cannot be excluded"
        ) from exc
    except OSError as exc:
        raise UnsafeReconciliationPathError(
            f"recorded bot pid {recorded_pid} could not be inspected safely"
        ) from exc
    raise BotStillRunningError(
        f"recorded bot pid {recorded_pid} is still live despite lock acquisition"
    )


def _open_or_create_archive_directory(
    project_fd: int,
    archive_basename: str,
) -> tuple[int, os.stat_result]:
    """Open a private direct-child archive without following symbolic links."""

    try:
        metadata = _entry_stat(project_fd, archive_basename)
    except FileNotFoundError:
        os.mkdir(archive_basename, mode=0o700, dir_fd=project_fd)
        _fsync_directory(project_fd)
        metadata = _entry_stat(project_fd, archive_basename)
    if not stat.S_ISDIR(metadata.st_mode):
        raise UnsafeReconciliationPathError(
            "marker archive entry is not an ordinary directory"
        )
    if metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise UnsafeReconciliationPathError(
            "marker archive directory must be owned by the current user and private"
        )
    descriptor = os.open(
        archive_basename,
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        dir_fd=project_fd,
    )
    try:
        opened = os.fstat(descriptor)
        if not _same_inode(metadata, opened):
            raise UnsafeReconciliationPathError(
                "marker archive directory identity changed while it was opened"
            )
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor, opened


def _require_archive_path_identity(
    project_fd: int,
    archive_basename: str,
    archive_fd: int,
    expected: os.stat_result,
) -> None:
    """Require the reported archive name to remain bound to its opened fd."""

    try:
        current = _entry_stat(project_fd, archive_basename)
        opened = os.fstat(archive_fd)
    except OSError as exc:
        raise UnsafeReconciliationPathError(
            "marker archive pathname is unavailable during reconciliation"
        ) from exc
    if (
        not stat.S_ISDIR(expected.st_mode)
        or not stat.S_ISDIR(current.st_mode)
        or not stat.S_ISDIR(opened.st_mode)
        or not _same_inode(expected, current)
        or not _same_inode(expected, opened)
        or current.st_uid != os.geteuid()
        or stat.S_IMODE(current.st_mode) & 0o077
    ):
        raise UnsafeReconciliationPathError(
            "marker archive pathname identity changed during reconciliation"
        )


def _write_receipt_temp(
    archive_fd: int,
    basename: str,
    payload: dict[str, object],
) -> tuple[str, int, os.stat_result]:
    """Write and synchronise a private receipt temporary file."""

    temporary = f".{basename}.tmp.{os.getpid()}"
    flags = (
        os.O_RDWR
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = os.open(temporary, flags, 0o600, dir_fd=archive_fd)
    identity: os.stat_result | None = None
    try:
        content = _canonical_json_bytes(payload)
        view = memoryview(content)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short write while creating reconciliation receipt")
            view = view[written:]
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o400)
        os.fsync(descriptor)
        identity = os.fstat(descriptor)
        current = _require_regular_entry(
            archive_fd,
            temporary,
            label="temporary marker archive receipt",
        )
        if (
            not _same_inode(identity, current)
            or identity.st_nlink != 1
            or current.st_nlink != 1
            or identity.st_uid != os.geteuid()
            or current.st_uid != os.geteuid()
            or stat.S_IMODE(identity.st_mode) != 0o400
            or stat.S_IMODE(current.st_mode) != 0o400
        ):
            raise ArchiveCommitError(
                "temporary marker archive receipt identity or mode changed"
            )
    except BaseException:
        try:
            identity = os.fstat(descriptor)
        except OSError:
            pass
        try:
            if identity is not None:
                try:
                    current = _entry_stat(archive_fd, temporary)
                except FileNotFoundError:
                    pass
                else:
                    if _same_inode(identity, current):
                        os.unlink(temporary, dir_fd=archive_fd)
        finally:
            os.close(descriptor)
        raise
    assert identity is not None
    return temporary, descriptor, identity


def _require_bound_readonly_receipt(
    archive_fd: int,
    basename: str,
    descriptor: int,
    expected_identity: os.stat_result,
    expected_content: bytes,
) -> None:
    """Require one exact, single-link, current-user-owned 0400 receipt."""

    opened = os.fstat(descriptor)
    current = _require_regular_entry(
        archive_fd,
        basename,
        label="marker archive receipt",
    )
    if (
        not _same_inode(expected_identity, opened)
        or not _same_inode(opened, current)
        or opened.st_nlink != 1
        or current.st_nlink != 1
        or opened.st_uid != os.geteuid()
        or current.st_uid != os.geteuid()
        or stat.S_IMODE(opened.st_mode) != 0o400
        or stat.S_IMODE(current.st_mode) != 0o400
        or _read_all(descriptor, maximum=MAX_MARKER_BYTES) != expected_content
    ):
        raise ArchiveCommitError(
            "marker archive receipt identity, mode or bytes changed"
        )


def reconcile_unattached_media_upload_offline(
    *,
    project_root: Path,
    expected_marker_sha256: str,
    expected_media_receipt_sha256: str,
    expected_media_fence_sha256: str,
    expected_media_transaction_id: str,
    expected_media_receipt_device: int,
    expected_media_receipt_inode: int,
    expected_media_receipt_ctime_ns: int,
    expected_media_fence_device: int,
    expected_media_fence_inode: int,
    expected_media_fence_ctime_ns: int,
    reconciliation_reference: str,
    confirm_no_tweet_create_attempted: bool,
    confirm_unattached_media_abandoned: bool,
    archive_basename: str = DEFAULT_ARCHIVE_BASENAME,
    now: Callable[[], int] | None = None,
) -> UnattachedMediaArchiveResult:
    """Archive and retire one exact ambiguous but unattached media upload.

    The operator supplies the external fact that no ``POST /2/tweets`` request
    was attempted and accepts that an upload which may have succeeded is
    abandoned.  The tool independently requires the corresponding local fact:
    no source receipt or tweet journal/fence exists.  It never removes the
    ambiguity marker; that remains the restart barrier until the existing
    marker reconciler archives it in a separate stopped operation.
    """

    if confirm_no_tweet_create_attempted is not True:
        raise UnattachedMediaReconciliationError(
            "offline media reconciliation requires confirmation that no tweet-create request was attempted"
        )
    if confirm_unattached_media_abandoned is not True:
        raise UnattachedMediaReconciliationError(
            "offline media reconciliation requires abandonment of any accepted unattached media"
        )
    marker_hash = _normalise_sha256(expected_marker_sha256)
    receipt_hash = _normalise_sha256(expected_media_receipt_sha256)
    fence_hash = _normalise_sha256(expected_media_fence_sha256)
    transaction_id = _normalise_transaction_id(expected_media_transaction_id)
    receipt_device = _normalise_identity_integer(
        expected_media_receipt_device,
        label="media receipt device",
        allow_zero=True,
    )
    receipt_inode = _normalise_identity_integer(
        expected_media_receipt_inode,
        label="media receipt inode",
        allow_zero=False,
    )
    receipt_ctime_ns = _normalise_identity_integer(
        expected_media_receipt_ctime_ns,
        label="media receipt ctime_ns",
        allow_zero=True,
    )
    fence_device = _normalise_identity_integer(
        expected_media_fence_device,
        label="media fence device",
        allow_zero=True,
    )
    fence_inode = _normalise_identity_integer(
        expected_media_fence_inode,
        label="media fence inode",
        allow_zero=False,
    )
    fence_ctime_ns = _normalise_identity_integer(
        expected_media_fence_ctime_ns,
        label="media fence ctime_ns",
        allow_zero=True,
    )
    reference = _normalise_reference(reconciliation_reference)
    archive_basename = _validate_basename(
        archive_basename,
        label="archive directory name",
    )

    locks = _acquire_offline_instance_locks(Path(project_root))
    active_barriers: _ActiveBarrierSet | None = None
    receipt_fd: int | None = None
    fence_fd: int | None = None
    archive_fd: int | None = None
    audit_temporary: str | None = None
    audit_verification_fd: int | None = None
    audit_identity: os.stat_result | None = None
    audit_published = False
    receipt_archive_created = False
    fence_archive_created = False
    receipt_active_removed = False
    fence_active_removed = False
    receipt_original_mode: int | None = None
    fence_original_mode: int | None = None
    receipt_archive_name = (
        f"unattached_media_upload.{transaction_id}.{receipt_hash}.receipt.json"
    )
    fence_archive_name = (
        f"unattached_media_upload.{transaction_id}.{fence_hash}.fence.json"
    )
    audit_name = f"unattached_media_upload.{transaction_id}.reconciliation.json"
    try:
        active_barriers = _open_active_barrier_set(locks.project_fd)
        if active_barriers.names != (
            MARKER_BASENAME,
            RESTART_BARRIER_BASENAME,
        ):
            raise UnattachedMediaReconciliationError(
                "unattached-media reconciliation requires the exact paired ambiguity marker"
            )
        marker_bytes = _read_all(
            active_barriers.descriptor,
            maximum=MAX_MARKER_BYTES,
        )
        if hashlib.sha256(marker_bytes).hexdigest() != marker_hash:
            raise MarkerIdentityError(
                "remote-write safety marker SHA-256 differs from the reviewed value"
            )
        try:
            marker_value = json.loads(marker_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise MarkerIdentityError(
                "remote-write safety marker is not valid UTF-8 JSON"
            ) from exc
        if (
            not isinstance(marker_value, dict)
            or set(marker_value)
            != {
                "made_with_ai",
                "media_ids",
                "outcome",
                "recorded_at_epoch",
                "reply_to_id",
                "schema_version",
                "text_sha256",
            }
            or type(marker_value.get("schema_version")) is not int
            or marker_value.get("schema_version") != 1
            or marker_value.get("outcome") != "ambiguous_remote_post"
            or marker_value.get("media_ids") != []
            or marker_value.get("text_sha256")
            != hashlib.sha256(b"").hexdigest()
            or marker_value.get("reply_to_id") != ""
            or marker_value.get("made_with_ai") is not False
            or type(marker_value.get("recorded_at_epoch")) is not int
        ):
            raise UnattachedMediaReconciliationError(
                "ambiguity marker is not the exact media-only pre-tweet incident"
            )

        receipt_path = locks.project / MEDIA_RECEIPT_BASENAME
        fence_path = fence_path_for_receipt(receipt_path)
        if fence_path.name != MEDIA_FENCE_BASENAME:
            raise UnattachedMediaReconciliationError(
                "media fence policy does not name the fixed production companion"
            )
        try:
            receipt = inspect_media_upload_receipt(receipt_path)
            fence = _required_fence_snapshot(fence_path)
        except MediaUploadReceiptError as exc:
            raise UnattachedMediaReconciliationError(
                "media receipt/fence pair is not strict canonical sending state"
            ) from exc
        if receipt is None:
            raise UnattachedMediaReconciliationError(
                "media receipt is absent"
            )
        expected_fence_document = dict(receipt.document)
        expected_fence_document["document_kind"] = MEDIA_FENCE_DOCUMENT_KIND
        if (
            receipt.sha256 != receipt_hash
            or fence.sha256 != fence_hash
            or receipt.device != receipt_device
            or receipt.inode != receipt_inode
            or receipt.ctime_ns != receipt_ctime_ns
            or fence.device != fence_device
            or fence.inode != fence_inode
            or fence.ctime_ns != fence_ctime_ns
            or receipt.document.get("document_kind")
            != MEDIA_RECEIPT_DOCUMENT_KIND
            or receipt.document.get("transaction_id") != transaction_id
            or fence.document.get("transaction_id") != transaction_id
            or receipt.document.get("lifecycle_state") != "sending"
            or fence.document.get("lifecycle_state") != "sending"
            or receipt.document.get("remote_media_id") is not None
            or fence.document.get("remote_media_id") is not None
            or fence.document != expected_fence_document
        ):
            raise UnattachedMediaReconciliationError(
                "media pair differs from the reviewed exact sending transaction"
            )

        direct_names = os.listdir(locks.project_fd)
        present_tweet_authority = sorted(
            name
            for name in direct_names
            if name in TWEET_AUTHORITY_BASENAMES
            or any(name.startswith(prefix) for prefix in TWEET_AUTHORITY_PREFIXES)
        )
        if present_tweet_authority:
            raise UnattachedMediaReconciliationError(
                "tweet-create authority exists; media is not proved unattached: "
                + ", ".join(present_tweet_authority)
            )

        receipt_fd, receipt_stat = _open_verified_regular(
            locks.project_fd,
            MEDIA_RECEIPT_BASENAME,
            label="active media receipt",
            flags=os.O_RDWR,
            require_single_link=True,
        )
        fence_fd, fence_stat = _open_verified_regular(
            locks.project_fd,
            MEDIA_FENCE_BASENAME,
            label="active media fence",
            flags=os.O_RDWR,
            require_single_link=True,
        )
        receipt_original_mode = stat.S_IMODE(receipt_stat.st_mode)
        fence_original_mode = stat.S_IMODE(fence_stat.st_mode)
        if (
            receipt_original_mode != MEDIA_RECEIPT_MODE
            or fence_original_mode != MEDIA_RECEIPT_MODE
            or receipt_stat.st_uid != os.geteuid()
            or fence_stat.st_uid != os.geteuid()
            or (receipt_stat.st_dev, receipt_stat.st_ino)
            != (receipt.device, receipt.inode)
            or (fence_stat.st_dev, fence_stat.st_ino)
            != (fence.device, fence.inode)
            or _read_all(receipt_fd, maximum=MAX_MEDIA_RECEIPT_BYTES)
            != receipt.data
            or _read_all(fence_fd, maximum=MAX_MEDIA_RECEIPT_BYTES)
            != fence.data
        ):
            raise UnattachedMediaReconciliationError(
                "opened media pair differs from the reviewed path generations"
            )

        archive_fd, archive_stat = _open_or_create_archive_directory(
            locks.project_fd,
            archive_basename,
        )
        _require_archive_path_identity(
            locks.project_fd,
            archive_basename,
            archive_fd,
            archive_stat,
        )
        if archive_stat.st_dev != receipt_stat.st_dev:
            raise UnsafeReconciliationPathError(
                "media archive is on another filesystem; same-inode archival is unavailable"
            )
        for name in (receipt_archive_name, fence_archive_name, audit_name):
            if not _entry_absent(archive_fd, name):
                raise ArchiveCommitError(
                    f"reviewed unattached-media archive entry already exists: {name}"
                )

        result = UnattachedMediaArchiveResult(
            schema_version=1,
            operation="offline_unattached_media_upload_archive",
            project_root=str(locks.project),
            marker_sha256=marker_hash,
            marker_names_preserved=active_barriers.names,
            media_transaction_id=transaction_id,
            lane=str(receipt.document["lane"]),
            image_basename=str(receipt.document["image"]["basename"]),
            image_sha256=str(receipt.document["image"]["sha256"]),
            receipt_archive_path=(
                f"{archive_basename}/{receipt_archive_name}"
            ),
            receipt_sha256=receipt_hash,
            receipt_size=len(receipt.data),
            receipt_device=int(receipt_stat.st_dev),
            receipt_inode=int(receipt_stat.st_ino),
            fence_archive_path=f"{archive_basename}/{fence_archive_name}",
            fence_sha256=fence_hash,
            fence_size=len(fence.data),
            fence_device=int(fence_stat.st_dev),
            fence_inode=int(fence_stat.st_ino),
            audit_receipt_path=f"{archive_basename}/{audit_name}",
            reconciliation_reference=reference,
            archived_at_epoch=int((now or time.time)()),
            accepted_media_disposition="unattached_and_abandoned",
            no_tweet_create_authority_present=True,
            operator_confirmed_no_tweet_create_attempted=True,
            operator_confirmed_unattached_media_abandoned=True,
            remote_media_id_absent=True,
            bot_instance_lock_acquired=True,
            media_archives_and_audit_durable_before_active_removal=True,
            media_receipt_retired_before_fence=True,
            active_marker_preserved_after_media_reconciliation=True,
            successful_return_requires_active_media_pair_absent=True,
        )
        (
            audit_temporary,
            audit_verification_fd,
            audit_identity,
        ) = _write_receipt_temp(archive_fd, audit_name, result.to_dict())
        audit_content = _canonical_json_bytes(result.to_dict())

        locks.revalidate()
        _require_active_barrier_identity(
            locks.project_fd,
            expected_names=active_barriers.names,
            expected_identity=active_barriers.identity,
            opened_descriptor=active_barriers.descriptor,
            expected_total_links=len(active_barriers.names),
        )
        _link_noreplace(
            MEDIA_RECEIPT_BASENAME,
            receipt_archive_name,
            source_directory_fd=locks.project_fd,
            destination_directory_fd=archive_fd,
            label="reviewed unattached media receipt archive",
        )
        receipt_archive_created = True
        _link_noreplace(
            MEDIA_FENCE_BASENAME,
            fence_archive_name,
            source_directory_fd=locks.project_fd,
            destination_directory_fd=archive_fd,
            label="reviewed unattached media fence archive",
        )
        fence_archive_created = True
        for descriptor in (receipt_fd, fence_fd):
            os.fchmod(descriptor, 0o400)
            os.fsync(descriptor)
        _fsync_directory(archive_fd)

        archived_receipt = _require_regular_entry(
            archive_fd,
            receipt_archive_name,
            label="archived unattached media receipt",
        )
        archived_fence = _require_regular_entry(
            archive_fd,
            fence_archive_name,
            label="archived unattached media fence",
        )
        if (
            not _same_inode(receipt_stat, archived_receipt)
            or not _same_inode(fence_stat, archived_fence)
            or archived_receipt.st_nlink != 2
            or archived_fence.st_nlink != 2
            or stat.S_IMODE(archived_receipt.st_mode) != 0o400
            or stat.S_IMODE(archived_fence.st_mode) != 0o400
            or _read_all(receipt_fd, maximum=MAX_MEDIA_RECEIPT_BYTES)
            != receipt.data
            or _read_all(fence_fd, maximum=MAX_MEDIA_RECEIPT_BYTES)
            != fence.data
        ):
            raise ArchiveCommitError(
                "media archive links did not preserve exact reviewed generations"
            )

        _require_bound_readonly_receipt(
            archive_fd,
            audit_temporary,
            audit_verification_fd,
            audit_identity,
            audit_content,
        )
        _rename_noreplace(
            audit_temporary,
            audit_name,
            source_directory_fd=archive_fd,
            destination_directory_fd=archive_fd,
            label="unattached media reconciliation audit",
        )
        audit_temporary = None
        audit_published = True
        _fsync_directory(archive_fd)

        locks.revalidate()
        _require_archive_path_identity(
            locks.project_fd,
            archive_basename,
            archive_fd,
            archive_stat,
        )
        _require_bound_readonly_receipt(
            archive_fd,
            audit_name,
            audit_verification_fd,
            audit_identity,
            audit_content,
        )
        _require_active_barrier_identity(
            locks.project_fd,
            expected_names=active_barriers.names,
            expected_identity=active_barriers.identity,
            opened_descriptor=active_barriers.descriptor,
            expected_total_links=len(active_barriers.names),
        )

        os.unlink(MEDIA_RECEIPT_BASENAME, dir_fd=locks.project_fd)
        receipt_active_removed = True
        _fsync_directory(locks.project_fd)
        if not _entry_absent(locks.project_fd, MEDIA_RECEIPT_BASENAME):
            raise ArchiveCommitError(
                "active media receipt survived reviewed retirement"
            )
        archived_receipt = _require_regular_entry(
            archive_fd,
            receipt_archive_name,
            label="archived unattached media receipt",
        )
        if (
            not _same_inode(receipt_stat, archived_receipt)
            or archived_receipt.st_nlink != 1
        ):
            raise ArchiveCommitError(
                "media receipt retirement did not preserve its sole archive"
            )

        locks.revalidate()
        _require_active_barrier_identity(
            locks.project_fd,
            expected_names=active_barriers.names,
            expected_identity=active_barriers.identity,
            opened_descriptor=active_barriers.descriptor,
            expected_total_links=len(active_barriers.names),
        )
        surviving_fence = _require_regular_entry(
            locks.project_fd,
            MEDIA_FENCE_BASENAME,
            label="surviving active media fence",
        )
        if (
            not _same_inode(fence_stat, surviving_fence)
            or surviving_fence.st_nlink != 2
            or _read_all(fence_fd, maximum=MAX_MEDIA_RECEIPT_BYTES)
            != fence.data
        ):
            raise ArchiveCommitError(
                "media fence changed before final active retirement"
            )
        os.unlink(MEDIA_FENCE_BASENAME, dir_fd=locks.project_fd)
        fence_active_removed = True
        _fsync_directory(locks.project_fd)

        archived_fence = _require_regular_entry(
            archive_fd,
            fence_archive_name,
            label="archived unattached media fence",
        )
        if (
            not _entry_absent(locks.project_fd, MEDIA_RECEIPT_BASENAME)
            or not _entry_absent(locks.project_fd, MEDIA_FENCE_BASENAME)
            or not _same_inode(fence_stat, archived_fence)
            or archived_fence.st_nlink != 1
            or stat.S_IMODE(archived_fence.st_mode) != 0o400
            or hashlib.sha256(
                _read_all(fence_fd, maximum=MAX_MEDIA_RECEIPT_BYTES)
            ).hexdigest()
            != fence_hash
        ):
            raise ArchiveCommitError(
                "final media retirement did not leave exact immutable archives"
            )
        _require_bound_readonly_receipt(
            archive_fd,
            audit_name,
            audit_verification_fd,
            audit_identity,
            audit_content,
        )
        _require_active_barrier_identity(
            locks.project_fd,
            expected_names=active_barriers.names,
            expected_identity=active_barriers.identity,
            opened_descriptor=active_barriers.descriptor,
            expected_total_links=len(active_barriers.names),
        )
        locks.revalidate()
        return result
    except BaseException as exc:
        rollback_error: BaseException | None = None
        try:
            if archive_fd is not None:
                if not receipt_archive_created and receipt_fd is not None:
                    try:
                        candidate = _require_regular_entry(
                            archive_fd,
                            receipt_archive_name,
                            label="candidate rollback media receipt archive",
                        )
                    except UnsafeReconciliationPathError:
                        pass
                    else:
                        receipt_archive_created = _same_inode(
                            candidate,
                            os.fstat(receipt_fd),
                        )
                if not fence_archive_created and fence_fd is not None:
                    try:
                        candidate = _require_regular_entry(
                            archive_fd,
                            fence_archive_name,
                            label="candidate rollback media fence archive",
                        )
                    except UnsafeReconciliationPathError:
                        pass
                    else:
                        fence_archive_created = _same_inode(
                            candidate,
                            os.fstat(fence_fd),
                        )
                if not audit_published and audit_identity is not None:
                    try:
                        candidate = _require_regular_entry(
                            archive_fd,
                            audit_name,
                            label="candidate rollback unattached-media audit",
                        )
                    except UnsafeReconciliationPathError:
                        pass
                    else:
                        audit_published = _same_inode(
                            candidate,
                            audit_identity,
                        )
            if receipt_archive_created and archive_fd is not None:
                archived = _require_regular_entry(
                    archive_fd,
                    receipt_archive_name,
                    label="rollback media receipt archive",
                )
                if _entry_absent(locks.project_fd, MEDIA_RECEIPT_BASENAME):
                    _link_noreplace(
                        receipt_archive_name,
                        MEDIA_RECEIPT_BASENAME,
                        source_directory_fd=archive_fd,
                        destination_directory_fd=locks.project_fd,
                        label="restored active media receipt",
                    )
                    receipt_active_removed = False
                if receipt_fd is not None and receipt_original_mode is not None:
                    os.fchmod(receipt_fd, receipt_original_mode)
                    os.fsync(receipt_fd)
                current = _require_regular_entry(
                    locks.project_fd,
                    MEDIA_RECEIPT_BASENAME,
                    label="restored active media receipt",
                )
                if not _same_inode(archived, current):
                    raise ArchiveCommitError(
                        "rollback restored the wrong media receipt generation"
                    )
            if fence_archive_created and archive_fd is not None:
                archived = _require_regular_entry(
                    archive_fd,
                    fence_archive_name,
                    label="rollback media fence archive",
                )
                if _entry_absent(locks.project_fd, MEDIA_FENCE_BASENAME):
                    _link_noreplace(
                        fence_archive_name,
                        MEDIA_FENCE_BASENAME,
                        source_directory_fd=archive_fd,
                        destination_directory_fd=locks.project_fd,
                        label="restored active media fence",
                    )
                    fence_active_removed = False
                if fence_fd is not None and fence_original_mode is not None:
                    os.fchmod(fence_fd, fence_original_mode)
                    os.fsync(fence_fd)
                current = _require_regular_entry(
                    locks.project_fd,
                    MEDIA_FENCE_BASENAME,
                    label="restored active media fence",
                )
                if not _same_inode(archived, current):
                    raise ArchiveCommitError(
                        "rollback restored the wrong media fence generation"
                    )
            if archive_fd is not None:
                if audit_published and audit_identity is not None:
                    current_audit = _require_regular_entry(
                        archive_fd,
                        audit_name,
                        label="rollback unattached-media audit",
                    )
                    if _same_inode(audit_identity, current_audit):
                        os.unlink(audit_name, dir_fd=archive_fd)
                        audit_published = False
                if fence_archive_created:
                    os.unlink(fence_archive_name, dir_fd=archive_fd)
                    fence_archive_created = False
                if receipt_archive_created:
                    os.unlink(receipt_archive_name, dir_fd=archive_fd)
                    receipt_archive_created = False
                _fsync_directory(archive_fd)
            _fsync_directory(locks.project_fd)
        except BaseException as rollback_exc:
            rollback_error = rollback_exc
        if rollback_error is not None:
            raise ArchiveCommitError(
                "unattached-media reconciliation failed and rollback was incomplete; preserve the active marker and inspect the archive"
            ) from rollback_error
        if not isinstance(exc, Exception):
            raise
        if isinstance(exc, MarkerReconciliationError):
            raise
        raise ArchiveCommitError(
            "offline unattached-media reconciliation failed"
        ) from exc
    finally:
        if (
            audit_temporary is not None
            and audit_identity is not None
            and archive_fd is not None
        ):
            try:
                current = _entry_stat(archive_fd, audit_temporary)
            except OSError:
                pass
            else:
                if _same_inode(current, audit_identity):
                    try:
                        os.unlink(audit_temporary, dir_fd=archive_fd)
                    except OSError:
                        pass
        for descriptor in (
            audit_verification_fd,
            archive_fd,
            fence_fd,
            receipt_fd,
            (
                active_barriers.descriptor
                if active_barriers is not None
                else None
            ),
        ):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        locks.close()


def reconcile_marker_offline(
    *,
    project_root: Path,
    expected_marker_sha256: str,
    reconciliation_reference: str,
    archive_basename: str = DEFAULT_ARCHIVE_BASENAME,
    now: Callable[[], int] | None = None,
) -> MarkerArchiveResult:
    """Retire the complete active barrier set after durable archival.

    The function is intentionally strict: any precondition, identity, hash,
    synchronisation or archive collision failure restores the original active
    names whenever rollback remains possible.  For the current paired layout,
    the restart barrier is the last active name removed, after the same-inode
    archive and receipt are durable.
    """

    expected_hash = _normalise_sha256(expected_marker_sha256)
    archive_basename = _validate_basename(
        archive_basename,
        label="archive directory name",
    )
    reference = str(reconciliation_reference).strip()
    if not reference or len(reference) > 500 or any(
        character in reference for character in "\r\n\x00"
    ):
        raise MarkerReconciliationError(
            "reconciliation reference must be one non-empty line of at most 500 characters"
        )
    try:
        project, project_fd = _open_project_directory_without_symlinks(
            Path(project_root)
        )
    except OSError as exc:
        raise UnsafeReconciliationPathError(
            "project-root component could not be opened without following links"
        ) from exc
    instance_socket: socket.socket | None = None
    lock_fd: int | None = None
    marker_fd: int | None = None
    initial_active_names: tuple[str, ...] = ()
    source_name = MARKER_BASENAME
    archive_fd: int | None = None
    receipt_temporary: str | None = None
    receipt_temporary_identity: os.stat_result | None = None
    receipt_verification_fd: int | None = None
    archive_name = f"ambiguous_post_outcome.{expected_hash}.json"
    receipt_name = f"{archive_name}.reconciliation.json"
    archive_link_created = False
    marker_mode: int | None = None
    try:
        try:
            fcntl.flock(
                project_fd,
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError as exc:
            raise BotStillRunningError(
                "bot state-directory lock is held; stop the service and wait "
                "for the process to exit"
            ) from exc
        project_identity = os.fstat(project_fd)
        _require_project_path_identity(project, project_fd, project_identity)
        if not _descriptor_owns_exclusive_flock(
            project_fd,
            expected_device=int(project_identity.st_dev),
            expected_inode=int(project_identity.st_ino),
        ):
            raise BotStillRunningError(
                "state-directory flock acquisition could not be proved"
            )
        instance_socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        socket_name = instance_lock_abstract_socket_name_for_identity(
            int(project_identity.st_dev),
            int(project_identity.st_ino),
        )
        try:
            instance_socket.bind(socket_name)
        except OSError as exc:
            if exc.errno == errno.EADDRINUSE:
                raise BotStillRunningError(
                    "bot process singleton is active; stop the service and "
                    "wait for the process to exit"
                ) from exc
            raise

        lock_fd, lock_stat = _open_verified_regular(
            project_fd,
            LOCK_BASENAME,
            label="bot instance lock",
            flags=os.O_RDWR,
            require_single_link=True,
        )
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise BotStillRunningError(
                "bot instance lock is held; stop the service and wait for the process to exit"
            ) from exc
        if not _descriptor_owns_exclusive_flock(
            lock_fd,
            expected_device=int(lock_stat.st_dev),
            expected_inode=int(lock_stat.st_ino),
        ):
            raise BotStillRunningError(
                "file-instance flock acquisition could not be proved"
            )
        try:
            fcntl.fcntl(
                lock_fd,
                fcntl.F_OFD_SETLK,
                _ofd_lock_record(fcntl.F_WRLCK),
            )
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise BotStillRunningError(
                    "bot OFD instance lock is held; stop the service and wait "
                    "for the process to exit"
                ) from exc
            raise

        active_barriers = _open_active_barrier_set(project_fd)
        marker_fd = active_barriers.descriptor
        marker_stat = active_barriers.identity
        source_name = active_barriers.source_name
        initial_active_names = active_barriers.names
        marker_mode = stat.S_IMODE(marker_stat.st_mode)
        original = _read_all(marker_fd, maximum=MAX_MARKER_BYTES)
        actual_hash = hashlib.sha256(original).hexdigest()
        if actual_hash != expected_hash:
            raise MarkerIdentityError(
                "remote-write safety marker SHA-256 differs from the reviewed value"
            )

        archive_fd, archive_stat = _open_or_create_archive_directory(
            project_fd,
            archive_basename,
        )
        _require_archive_path_identity(
            project_fd,
            archive_basename,
            archive_fd,
            archive_stat,
        )
        if archive_stat.st_dev != marker_stat.st_dev:
            raise UnsafeReconciliationPathError(
                "marker archive is on another filesystem; same-inode archival is unavailable"
            )
        if not _entry_absent(archive_fd, archive_name):
            raise ArchiveCommitError("reviewed marker already has an archive entry")
        if not _entry_absent(archive_fd, receipt_name):
            raise ArchiveCommitError("reviewed marker already has an archive receipt")

        timestamp = int((now or time.time)())
        result = MarkerArchiveResult(
            schema_version=3,
            operation="offline_remote_write_safety_marker_archive",
            project_root=str(project),
            source_marker=source_name,
            active_barrier_names=initial_active_names,
            restart_barrier_present_before_reconciliation=(
                RESTART_BARRIER_BASENAME in initial_active_names
            ),
            archive_path=f"{archive_basename}/{archive_name}",
            receipt_path=f"{archive_basename}/{receipt_name}",
            marker_sha256=expected_hash,
            marker_size=len(original),
            marker_device=int(marker_stat.st_dev),
            marker_inode=int(marker_stat.st_ino),
            original_mode=oct(marker_mode),
            archived_mode=oct(0o400),
            reconciliation_reference=reference,
            archived_at_epoch=timestamp,
            bot_instance_lock_acquired=True,
            archive_inode_preserved=True,
            archive_and_receipt_durable_before_source_removal=True,
            active_marker_removal_is_final_transition=(
                RESTART_BARRIER_BASENAME not in initial_active_names
            ),
            restart_barrier_retired_last=(
                RESTART_BARRIER_BASENAME in initial_active_names
            ),
            successful_return_requires_source_absent=True,
            successful_return_requires_all_active_barriers_absent=True,
        )
        (
            receipt_temporary,
            receipt_verification_fd,
            receipt_temporary_identity,
        ) = _write_receipt_temp(
            archive_fd,
            receipt_name,
            result.to_dict(),
        )
        receipt_content = _canonical_json_bytes(result.to_dict())

        _require_active_barrier_identity(
            project_fd,
            expected_names=initial_active_names,
            expected_identity=marker_stat,
            opened_descriptor=marker_fd,
            expected_total_links=len(initial_active_names),
        )
        if _read_all(marker_fd, maximum=MAX_MARKER_BYTES) != original:
            raise MarkerIdentityError(
                "remote-write safety marker content changed before archival"
            )
        _revalidate_locked_instance_lock(
            project_fd,
            lock_fd,
            lock_stat,
            instance_socket,
            socket_name,
        )
        _require_project_path_identity(project, project_fd, project_identity)
        _require_archive_path_identity(
            project_fd,
            archive_basename,
            archive_fd,
            archive_stat,
        )

        _link_noreplace(
            source_name,
            archive_name,
            source_directory_fd=project_fd,
            destination_directory_fd=archive_fd,
            label="reviewed marker archive entry",
        )
        archive_link_created = True
        archived_stat = _require_regular_entry(
            archive_fd,
            archive_name,
            label="archived remote-write safety marker",
        )
        if not _same_inode(marker_stat, archived_stat):
            raise ArchiveCommitError("archive link did not preserve marker identity")
        expected_links_with_archive = len(initial_active_names) + 1
        if (
            archived_stat.st_nlink != expected_links_with_archive
            or os.fstat(marker_fd).st_nlink != expected_links_with_archive
        ):
            raise ArchiveCommitError(
                "active barriers and archive do not form the exact expected "
                "same-inode link set"
            )
        _require_active_barrier_identity(
            project_fd,
            expected_names=initial_active_names,
            expected_identity=marker_stat,
            opened_descriptor=marker_fd,
            expected_total_links=expected_links_with_archive,
        )
        if _read_all(marker_fd, maximum=MAX_MARKER_BYTES) != original:
            raise ArchiveCommitError("archived marker bytes changed during linking")

        os.fchmod(marker_fd, 0o400)
        os.fsync(marker_fd)
        _fsync_directory(archive_fd)
        _require_archive_path_identity(
            project_fd,
            archive_basename,
            archive_fd,
            archive_stat,
        )
        _revalidate_locked_instance_lock(
            project_fd,
            lock_fd,
            lock_stat,
            instance_socket,
            socket_name,
        )
        _require_project_path_identity(project, project_fd, project_identity)
        _require_bound_readonly_receipt(
            archive_fd,
            receipt_temporary,
            receipt_verification_fd,
            receipt_temporary_identity,
            receipt_content,
        )
        _rename_noreplace(
            receipt_temporary,
            receipt_name,
            source_directory_fd=archive_fd,
            destination_directory_fd=archive_fd,
            label="reviewed marker archive receipt",
        )
        receipt_temporary = None
        _fsync_directory(archive_fd)
        _require_archive_path_identity(
            project_fd,
            archive_basename,
            archive_fd,
            archive_stat,
        )

        # The archive name and receipt are now both durable while every active
        # barrier still exists. Revalidate every identity before retiring the
        # legacy name first and the restart barrier last.
        archived_stat = _require_regular_entry(
            archive_fd,
            archive_name,
            label="archived remote-write safety marker",
        )
        _require_bound_readonly_receipt(
            archive_fd,
            receipt_name,
            receipt_verification_fd,
            receipt_temporary_identity,
            receipt_content,
        )
        _require_active_barrier_identity(
            project_fd,
            expected_names=initial_active_names,
            expected_identity=marker_stat,
            opened_descriptor=marker_fd,
            expected_total_links=expected_links_with_archive,
        )
        if (
            not _same_inode(marker_stat, archived_stat)
            or archived_stat.st_nlink != expected_links_with_archive
            or _read_all(marker_fd, maximum=MAX_MARKER_BYTES) != original
        ):
            raise ArchiveCommitError(
                "barrier/archive identity changed before active-barrier retirement"
            )
        _revalidate_locked_instance_lock(
            project_fd,
            lock_fd,
            lock_stat,
            instance_socket,
            socket_name,
        )
        _require_project_path_identity(project, project_fd, project_identity)
        _require_archive_path_identity(
            project_fd,
            archive_basename,
            archive_fd,
            archive_stat,
        )

        remaining_names = list(initial_active_names)
        if MARKER_BASENAME in remaining_names:
            os.unlink(MARKER_BASENAME, dir_fd=project_fd)
            remaining_names.remove(MARKER_BASENAME)
            _fsync_directory(project_fd)
            _require_active_barrier_identity(
                project_fd,
                expected_names=tuple(remaining_names),
                expected_identity=marker_stat,
                opened_descriptor=marker_fd,
                expected_total_links=len(remaining_names) + 1,
            )
            archived_stat = _require_regular_entry(
                archive_fd,
                archive_name,
                label="archived remote-write safety marker",
            )
            if (
                not _same_inode(marker_stat, archived_stat)
                or archived_stat.st_nlink != len(remaining_names) + 1
            ):
                raise ArchiveCommitError(
                    "legacy marker retirement did not preserve every successor "
                    "and archive link"
                )
        if RESTART_BARRIER_BASENAME in remaining_names:
            # This is deliberately the final active-barrier namespace
            # transition. The legacy marker is already absent and the archive
            # plus receipt are durable.
            os.unlink(RESTART_BARRIER_BASENAME, dir_fd=project_fd)
            remaining_names.remove(RESTART_BARRIER_BASENAME)
            _fsync_directory(project_fd)

        archived_stat = _require_regular_entry(
            archive_fd,
            archive_name,
            label="archived remote-write safety marker",
        )
        if (
            remaining_names
            or not _entry_absent(project_fd, MARKER_BASENAME)
            or not _entry_absent(project_fd, RESTART_BARRIER_BASENAME)
            or not _same_inode(marker_stat, archived_stat)
            or archived_stat.st_nlink != 1
            or os.fstat(marker_fd).st_nlink != 1
        ):
            raise ArchiveCommitError(
                "active barrier retirement did not leave one immutable archive inode"
            )
        _require_project_path_identity(project, project_fd, project_identity)
        _require_archive_path_identity(
            project_fd,
            archive_basename,
            archive_fd,
            archive_stat,
        )
        return result
    except BaseException as exc:
        archive_link_is_ours = False
        if (
            archive_link_created
            and archive_fd is not None
            and marker_fd is not None
        ):
            try:
                candidate_archive = _require_regular_entry(
                    archive_fd,
                    archive_name,
                    label="candidate archived remote-write safety marker",
                )
            except UnsafeReconciliationPathError:
                pass
            else:
                archive_link_is_ours = _same_inode(
                    candidate_archive,
                    os.fstat(marker_fd),
                )
        if archive_link_is_ours and archive_fd is not None:
            try:
                for active_name in initial_active_names:
                    try:
                        restored_source = _require_regular_entry(
                            project_fd,
                            active_name,
                            label=f"restored remote-write safety barrier {active_name}",
                        )
                    except UnsafeReconciliationPathError as source_error:
                        if not _entry_absent(project_fd, active_name):
                            raise source_error
                        _link_noreplace(
                            archive_name,
                            active_name,
                            source_directory_fd=archive_fd,
                            destination_directory_fd=project_fd,
                            label=(
                                "restored remote-write safety barrier "
                                f"{active_name}"
                            ),
                        )
                        _fsync_directory(project_fd)
                        restored_source = _require_regular_entry(
                            project_fd,
                            active_name,
                            label=(
                                "restored remote-write safety barrier "
                                f"{active_name}"
                            ),
                        )
                    if not _same_inode(restored_source, os.fstat(marker_fd)):
                        raise ArchiveCommitError(
                            "rollback found a changed active barrier identity"
                        )
                archived_source = _require_regular_entry(
                    archive_fd,
                    archive_name,
                    label="archived remote-write safety marker",
                )
                restored_link_count = len(initial_active_names) + 1
                if (
                    marker_fd is None
                    or not _same_inode(archived_source, os.fstat(marker_fd))
                    or archived_source.st_nlink != restored_link_count
                    or os.fstat(marker_fd).st_nlink != restored_link_count
                    or _read_all(marker_fd, maximum=MAX_MARKER_BYTES) != original
                ):
                    raise ArchiveCommitError(
                        "rollback could not prove the exact active barrier set "
                        "was restored"
                    )
                _require_active_barrier_identity(
                    project_fd,
                    expected_names=initial_active_names,
                    expected_identity=marker_stat,
                    opened_descriptor=marker_fd,
                    expected_total_links=restored_link_count,
                )
                if marker_fd is not None and marker_mode is not None:
                    os.fchmod(marker_fd, marker_mode)
                    os.fsync(marker_fd)
                _fsync_directory(project_fd)
                try:
                    final_receipt = _require_regular_entry(
                        archive_fd,
                        receipt_name,
                        label="reviewed marker archive receipt",
                    )
                except UnsafeReconciliationPathError as receipt_error:
                    if not _entry_absent(archive_fd, receipt_name):
                        raise receipt_error
                    final_receipt = None
                if (
                    final_receipt is not None
                    and receipt_temporary_identity is not None
                    and _same_inode(final_receipt, receipt_temporary_identity)
                ):
                    os.unlink(receipt_name, dir_fd=archive_fd)
                os.unlink(archive_name, dir_fd=archive_fd)
                archive_link_created = False
                _fsync_directory(archive_fd)
                _fsync_directory(project_fd)
                _require_active_barrier_identity(
                    project_fd,
                    expected_names=initial_active_names,
                    expected_identity=marker_stat,
                    opened_descriptor=marker_fd,
                    expected_total_links=len(initial_active_names),
                )
            except BaseException as rollback_error:
                if not isinstance(exc, Exception):
                    try:
                        exc.add_note(
                            "Offline marker rollback also failed; inspect "
                            f"{archive_basename}/{archive_name} before restart "
                            f"(rollback error: {type(rollback_error).__name__})"
                        )
                    except AttributeError:
                        pass
                    raise exc
                if not isinstance(rollback_error, Exception):
                    raise
                raise ArchiveCommitError(
                    "archive finalisation failed and the marker could not be restored; "
                    f"inspect {archive_basename}/{archive_name} before any restart"
                ) from rollback_error
        if not isinstance(exc, Exception):
            raise
        if isinstance(exc, MarkerReconciliationError):
            raise
        raise ArchiveCommitError("offline marker archival failed") from exc
    finally:
        if (
            receipt_temporary is not None
            and receipt_temporary_identity is not None
            and archive_fd is not None
        ):
            try:
                current_temporary = _entry_stat(
                    archive_fd,
                    receipt_temporary,
                )
            except OSError:
                pass
            else:
                if _same_inode(
                    current_temporary,
                    receipt_temporary_identity,
                ):
                    try:
                        os.unlink(receipt_temporary, dir_fd=archive_fd)
                    except OSError:
                        pass
        for descriptor in (
            receipt_verification_fd,
            archive_fd,
            marker_fd,
            lock_fd,
            project_fd,
        ):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        if instance_socket is not None:
            try:
                instance_socket.close()
            except OSError:
                pass


def build_parser() -> argparse.ArgumentParser:
    """Build the offline reconciliation command-line parser."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--project-root",
        required=True,
        type=Path,
        help="Stopped bot installation containing the marker and instance lock.",
    )
    parser.add_argument(
        "--expected-marker-sha256",
        required=True,
        help="SHA-256 independently recorded during manual reconciliation.",
    )
    parser.add_argument(
        "--reconcile-unattached-media-upload",
        action="store_true",
        help=(
            "Archive and retire one exact sending media pair while preserving "
            "the active ambiguity marker."
        ),
    )
    parser.add_argument("--expected-media-receipt-sha256")
    parser.add_argument("--expected-media-fence-sha256")
    parser.add_argument("--expected-media-transaction-id")
    parser.add_argument("--expected-media-receipt-device", type=int)
    parser.add_argument("--expected-media-receipt-inode", type=int)
    parser.add_argument("--expected-media-receipt-ctime-ns", type=int)
    parser.add_argument("--expected-media-fence-device", type=int)
    parser.add_argument("--expected-media-fence-inode", type=int)
    parser.add_argument("--expected-media-fence-ctime-ns", type=int)
    parser.add_argument(
        "--confirm-no-tweet-create-attempted",
        action="store_true",
        help="Attest that external evidence proves POST /2/tweets was not attempted.",
    )
    parser.add_argument(
        "--confirm-unattached-media-abandoned",
        action="store_true",
        help="Accept abandonment of any media object which the upload may have created.",
    )
    parser.add_argument(
        "--reconciliation-reference",
        required=True,
        help="One-line operator/audit reference; this tool does not verify its substance.",
    )
    parser.add_argument(
        "--archive-directory-name",
        default=DEFAULT_ARCHIVE_BASENAME,
        help="Private direct-child archive basename (default: %(default)s).",
    )
    parser.add_argument(
        "--confirm-offline-reconciliation-complete",
        action="store_true",
        help="Required acknowledgement that remote outcome review is already complete.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the offline marker archival command."""

    args = build_parser().parse_args(argv)
    media_values = {
        "--expected-media-receipt-sha256": args.expected_media_receipt_sha256,
        "--expected-media-fence-sha256": args.expected_media_fence_sha256,
        "--expected-media-transaction-id": args.expected_media_transaction_id,
        "--expected-media-receipt-device": args.expected_media_receipt_device,
        "--expected-media-receipt-inode": args.expected_media_receipt_inode,
        "--expected-media-receipt-ctime-ns": args.expected_media_receipt_ctime_ns,
        "--expected-media-fence-device": args.expected_media_fence_device,
        "--expected-media-fence-inode": args.expected_media_fence_inode,
        "--expected-media-fence-ctime-ns": args.expected_media_fence_ctime_ns,
    }
    if args.reconcile_unattached_media_upload:
        missing = [name for name, value in media_values.items() if value is None]
        if missing:
            print(
                "refusing unattached-media reconciliation without: "
                + ", ".join(missing),
                file=sys.stderr,
            )
            return 2
        if not args.confirm_no_tweet_create_attempted:
            print(
                "refusing unattached-media reconciliation without "
                "--confirm-no-tweet-create-attempted",
                file=sys.stderr,
            )
            return 2
        if not args.confirm_unattached_media_abandoned:
            print(
                "refusing unattached-media reconciliation without "
                "--confirm-unattached-media-abandoned",
                file=sys.stderr,
            )
            return 2
        try:
            media_result = reconcile_unattached_media_upload_offline(
                project_root=args.project_root,
                expected_marker_sha256=args.expected_marker_sha256,
                expected_media_receipt_sha256=(
                    args.expected_media_receipt_sha256
                ),
                expected_media_fence_sha256=args.expected_media_fence_sha256,
                expected_media_transaction_id=(
                    args.expected_media_transaction_id
                ),
                expected_media_receipt_device=(
                    args.expected_media_receipt_device
                ),
                expected_media_receipt_inode=args.expected_media_receipt_inode,
                expected_media_receipt_ctime_ns=(
                    args.expected_media_receipt_ctime_ns
                ),
                expected_media_fence_device=args.expected_media_fence_device,
                expected_media_fence_inode=args.expected_media_fence_inode,
                expected_media_fence_ctime_ns=(
                    args.expected_media_fence_ctime_ns
                ),
                reconciliation_reference=args.reconciliation_reference,
                confirm_no_tweet_create_attempted=True,
                confirm_unattached_media_abandoned=True,
                archive_basename=args.archive_directory_name,
            )
        except MarkerReconciliationError as exc:
            print(
                f"unattached-media reconciliation refused: {exc}",
                file=sys.stderr,
            )
            return 2
        sys.stdout.buffer.write(_canonical_json_bytes(media_result.to_dict()))
        return 0
    if any(value is not None for value in media_values.values()) or (
        args.confirm_no_tweet_create_attempted
        or args.confirm_unattached_media_abandoned
    ):
        print(
            "refusing media-specific options without "
            "--reconcile-unattached-media-upload",
            file=sys.stderr,
        )
        return 2
    if not args.confirm_offline_reconciliation_complete:
        print(
            "refusing marker archival without --confirm-offline-reconciliation-complete",
            file=sys.stderr,
        )
        return 2
    try:
        result = reconcile_marker_offline(
            project_root=args.project_root,
            expected_marker_sha256=args.expected_marker_sha256,
            reconciliation_reference=args.reconciliation_reference,
            archive_basename=args.archive_directory_name,
        )
    except MarkerReconciliationError as exc:
        print(f"marker reconciliation refused: {exc}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(_canonical_json_bytes(result.to_dict()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
