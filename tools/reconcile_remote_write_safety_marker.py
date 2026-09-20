#!/usr/bin/env python3
"""Reconcile reviewed remote-write barriers while the bot is offline.

This command does not decide whether an ambiguous X outcome has been reconciled.
An operator must establish that separately and supply exact reviewed identities.
The default operation archives the active ambiguity marker.  The narrower
``--reconcile-unattached-media-upload`` operation accepts only the exact
media-only, pre-tweet incident: it proves that no local tweet-create authority
exists, archives one externally bound sending receipt/fence pair, and retires
that pair while deliberately preserving the ambiguity marker.  The default
marker operation must then be run separately.  The mutually exclusive
``--adopt-externally-confirmed-reply`` and
``--adopt-externally-confirmed-main-post`` operations accept exact
operator-reviewed evidence that one tweet was published, durably adopt its
attempting transport as the ordinary confirmed pair, and archive the marker
under the same continuously held stopped-daemon lock set.

All mutating operations prove that the bot's process-lifetime lock is available
and make private read-only archives and audit receipts durable before removing
an active name.  The last active barrier in each operation is retired last.  A
hard process loss before archival is complete therefore leaves a fail-closed
active marker, receipt or fence present.

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
from typing import Any, Callable, Mapping, Sequence

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mrs_bot_instance_lock_checks import (  # noqa: E402
    instance_lock_abstract_socket_name_for_identity,
    ofd_lock_record as _ofd_lock_record,
    _OFD_LOCK_FORMAT as OFD_LOCK_FORMAT,
)
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
import remote_write_transport_journal as transport_journal  # noqa: E402
from transaction_mutation_authority import (  # noqa: E402
    issue_transaction_mutation_authority,
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
CONFIRMED_REPLY_RECEIPT_BASENAME = "confirmed_reply_receipt.json"
REGULAR_POST_RECEIPT_BASENAME = "regular_post_receipt.json"
TRANSPORT_JOURNAL_BASENAME = transport_journal.JOURNAL_BASENAME
TRANSPORT_FENCE_BASENAME = transport_journal.FENCE_BASENAME
EXTERNAL_ADOPTION_SCHEMA_VERSION = 1
EXTERNAL_ADOPTION_OPERATION = "offline_external_confirmed_reply_adoption"
EXTERNAL_ADOPTION_AUDIT_KIND = (
    "mrsMThatcher_external_confirmed_reply_adoption_audit"
)
EXTERNAL_MAIN_ADOPTION_OPERATION = "offline_external_confirmed_main_post_adoption"
EXTERNAL_MAIN_ADOPTION_AUDIT_KIND = (
    "mrsMThatcher_external_confirmed_main_post_adoption_audit"
)
EXTERNAL_EVIDENCE_MAX_BYTES = 1024 * 1024
EXTERNAL_AUDIT_MAX_BYTES = 256 * 1024
EXTERNAL_EVIDENCE_ARCHIVE_MODE = 0o400
SUPPORTED_REPLY_CANDIDATE_LANES = frozenset(
    {"mention", "hot_post_reply", "quote_tweet"}
)
MAX_MARKER_BYTES = 64 * 1024
MAX_LOCK_RECORD_BYTES = 128
SHA256_RE = re.compile(r"[0-9a-f]{64}")
SAFE_BASENAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
LOCK_RECORD_RE = re.compile(rb"pid=([1-9][0-9]{0,18})\n")
RENAME_NOREPLACE = 1


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


class ExternalReplyAdoptionError(MarkerReconciliationError):
    """The externally proven publication cannot be adopted exactly."""


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


@dataclass(frozen=True)
class ExternalReplyAdoptionResult:
    """Describe one checked or completed external publication adoption."""

    schema_version: int
    operation: str
    execution: str
    adoption_state: str
    project_root: str
    project_device: int
    project_inode: int
    transaction_id: str
    transport_lane: str
    candidate_lane: str
    target_id: str
    text_sha256: str
    canonical_payload_sha256: str
    confirmed_post_id: str
    confirmation_epoch: int
    remote_outcome: str
    operator_confirmed_external_publication_reviewed: bool
    reconciliation_reference: str
    source_receipt: dict[str, object]
    old_transport: dict[str, object]
    new_transport: dict[str, object] | None
    evidence_archive_path: str
    evidence_sha256: str
    prepared_audit_path: str
    prepared_audit_sha256: str | None
    completed_audit_path: str
    completed_audit_sha256: str | None
    marker_archive_path: str | None
    marker_audit_path: str | None
    durability: dict[str, bool]
    final_marker_presence: dict[str, bool]
    final_transport_classification: str
    final_source_receipt_present: bool
    planned_transport_classification: str
    no_network_request_performed: bool
    check_only_no_mutation: bool

    def to_dict(self) -> dict[str, object]:
        """Return a deterministic JSON-compatible representation."""

        return asdict(self)


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


@dataclass(frozen=True)
class _OpenedStableFile:
    """One held descriptor and its byte-exact stable generation."""

    descriptor: int
    metadata: os.stat_result
    data: bytes
    sha256: str
    basename: str


@dataclass(frozen=True)
class _HeldExternalEvidence:
    """No-follow evidence path held through adoption preconditions."""

    path: Path
    parent: Path
    parent_fd: int
    parent_identity: os.stat_result
    file: _OpenedStableFile

    def close(self) -> None:
        for descriptor in (self.file.descriptor, self.parent_fd):
            try:
                os.close(descriptor)
            except OSError:
                pass


def _stable_metadata_identity(value: os.stat_result) -> tuple[int, ...]:
    """Return every metadata field whose drift invalidates one held read."""

    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_mode),
        int(value.st_nlink),
        int(value.st_uid),
        int(value.st_gid),
        int(value.st_size),
        int(value.st_ctime_ns),
        int(value.st_mtime_ns),
    )


def _read_opened_stable_file(
    directory_fd: int,
    basename: str,
    *,
    label: str,
    maximum: int,
    expected_mode: int | None,
    expected_links: int,
) -> _OpenedStableFile:
    """Open and byte-bind one owned regular direct-child entry."""

    descriptor, opened = _open_verified_regular(
        directory_fd,
        basename,
        label=label,
        flags=os.O_RDONLY,
        require_single_link=expected_links == 1,
    )
    try:
        data = _read_all(descriptor, maximum=maximum)
        after_fd = os.fstat(descriptor)
        after_path = _require_regular_entry(
            directory_fd,
            basename,
            label=label,
        )
        if (
            _stable_metadata_identity(opened)
            != _stable_metadata_identity(after_fd)
            or _stable_metadata_identity(opened)
            != _stable_metadata_identity(after_path)
            or opened.st_uid != os.geteuid()
            or opened.st_nlink != expected_links
            or len(data) != opened.st_size
            or opened.st_size <= 0
            or opened.st_size > maximum
            or (
                expected_mode is not None
                and stat.S_IMODE(opened.st_mode) != expected_mode
            )
        ):
            raise UnsafeReconciliationPathError(
                f"{label} metadata or bytes changed while inspected"
            )
        return _OpenedStableFile(
            descriptor=descriptor,
            metadata=opened,
            data=data,
            sha256=hashlib.sha256(data).hexdigest(),
            basename=basename,
        )
    except BaseException:
        os.close(descriptor)
        raise


def _revalidate_opened_stable_file(
    directory_fd: int,
    opened: _OpenedStableFile,
    *,
    label: str,
    maximum: int,
) -> None:
    """Re-prove held bytes, metadata and pathname-to-descriptor binding."""

    current_fd = os.fstat(opened.descriptor)
    current_path = _require_regular_entry(
        directory_fd,
        opened.basename,
        label=label,
    )
    data = _read_all(opened.descriptor, maximum=maximum)
    after_fd = os.fstat(opened.descriptor)
    expected = _stable_metadata_identity(opened.metadata)
    if (
        _stable_metadata_identity(current_fd) != expected
        or _stable_metadata_identity(after_fd) != expected
        or _stable_metadata_identity(current_path) != expected
        or data != opened.data
        or hashlib.sha256(data).hexdigest() != opened.sha256
    ):
        raise ExternalReplyAdoptionError(f"{label} changed after review")


def _open_external_evidence(path: Path) -> _HeldExternalEvidence:
    """Open an external evidence file through a complete no-follow path walk."""

    supplied = Path(path)
    if not supplied.is_absolute() or ".." in supplied.parts or supplied.name in {
        "",
        ".",
        "..",
    }:
        raise UnsafeReconciliationPathError(
            "external evidence path must be an absolute ordinary pathname"
        )
    try:
        parent, parent_fd = _open_project_directory_without_symlinks(
            supplied.parent
        )
    except OSError as exc:
        raise UnsafeReconciliationPathError(
            "external evidence parent could not be opened without following links"
        ) from exc
    parent_identity = os.fstat(parent_fd)
    try:
        evidence = _read_opened_stable_file(
            parent_fd,
            supplied.name,
            label="external publication evidence",
            maximum=EXTERNAL_EVIDENCE_MAX_BYTES,
            expected_mode=None,
            expected_links=1,
        )
        mode = stat.S_IMODE(evidence.metadata.st_mode)
        if mode & 0o022:
            raise UnsafeReconciliationPathError(
                "external publication evidence must not be group/world writable"
            )
        return _HeldExternalEvidence(
            path=supplied,
            parent=parent,
            parent_fd=parent_fd,
            parent_identity=parent_identity,
            file=evidence,
        )
    except BaseException:
        os.close(parent_fd)
        raise


def _revalidate_external_evidence(evidence: _HeldExternalEvidence) -> None:
    """Require the reviewed external pathname and descriptor to remain exact."""

    _require_project_path_identity(
        evidence.parent,
        evidence.parent_fd,
        evidence.parent_identity,
    )
    _revalidate_opened_stable_file(
        evidence.parent_fd,
        evidence.file,
        label="external publication evidence",
        maximum=EXTERNAL_EVIDENCE_MAX_BYTES,
    )
    if stat.S_IMODE(evidence.file.metadata.st_mode) & 0o022:
        raise UnsafeReconciliationPathError(
            "external publication evidence permissions changed"
        )


def _strict_json_object_bytes(data: bytes, *, label: str) -> dict[str, Any]:
    """Parse one strict object, rejecting duplicate keys and constants."""

    def strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise ExternalReplyAdoptionError(
                    f"{label} contains duplicate JSON key: {key}"
                )
            value[key] = item
        return value

    def reject_constant(value: str) -> None:
        raise ExternalReplyAdoptionError(
            f"{label} contains invalid JSON constant: {value}"
        )

    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=strict_object,
            parse_constant=reject_constant,
        )
    except ExternalReplyAdoptionError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ExternalReplyAdoptionError(
            f"{label} is not strict UTF-8 JSON"
        ) from exc
    if not isinstance(value, dict):
        raise ExternalReplyAdoptionError(f"{label} must be a JSON object")
    return value


def _canonical_json_object_bytes(data: bytes, *, label: str) -> dict[str, Any]:
    """Parse and require the repository's canonical receipt representation."""

    value = _strict_json_object_bytes(data, label=label)
    if _canonical_json_bytes(value) != data:
        raise ExternalReplyAdoptionError(f"{label} is not canonical JSON")
    return value


def _identity_record(opened: _OpenedStableFile) -> dict[str, object]:
    """Return the audit representation of one exact held file."""

    return {
        "basename": opened.basename,
        "sha256": opened.sha256,
        "device": int(opened.metadata.st_dev),
        "inode": int(opened.metadata.st_ino),
        "ctime_ns": int(opened.metadata.st_ctime_ns),
        "size": int(opened.metadata.st_size),
        "mode": oct(stat.S_IMODE(opened.metadata.st_mode)),
    }


def _transport_snapshot_record(
    snapshot: transport_journal.JournalSnapshot,
) -> dict[str, object]:
    """Return the audit representation of one transport snapshot."""

    return {
        "sha256": snapshot.sha256,
        "device": snapshot.device,
        "inode": snapshot.inode,
        "ctime_ns": snapshot.ctime_ns,
        "size": len(snapshot.data),
        "lifecycle_state": snapshot.document["lifecycle_state"],
    }


def _normalise_post_id(value: str, *, label: str) -> str:
    candidate = str(value)
    if not re.fullmatch(r"[0-9]{1,30}", candidate):
        raise ExternalReplyAdoptionError(f"{label} must be a numeric post ID")
    return candidate


def _normalise_confirmation_epoch(value: int) -> int:
    if (
        type(value) is not int
        or not transport_journal.MIN_CONFIRMATION_EPOCH
        <= value
        <= transport_journal.MAX_CONFIRMATION_EPOCH
    ):
        raise ExternalReplyAdoptionError(
            "confirmation epoch is outside the supported range"
        )
    return value


def _require_expected_opened_identity(
    opened: _OpenedStableFile,
    *,
    expected_sha256: str,
    expected_device: int,
    expected_inode: int,
    expected_ctime_ns: int,
    expected_size: int,
    label: str,
) -> None:
    """Match every operator-supplied generation value to one held file."""

    expected_hash = _normalise_sha256(expected_sha256)
    expected_values = (
        _normalise_identity_integer(
            expected_device,
            label=f"{label} device",
            allow_zero=True,
        ),
        _normalise_identity_integer(
            expected_inode,
            label=f"{label} inode",
            allow_zero=False,
        ),
        _normalise_identity_integer(
            expected_ctime_ns,
            label=f"{label} ctime_ns",
            allow_zero=True,
        ),
        _normalise_identity_integer(
            expected_size,
            label=f"{label} size",
            allow_zero=False,
        ),
    )
    actual_values = (
        int(opened.metadata.st_dev),
        int(opened.metadata.st_ino),
        int(opened.metadata.st_ctime_ns),
        int(opened.metadata.st_size),
    )
    if opened.sha256 != expected_hash or actual_values != expected_values:
        raise ExternalReplyAdoptionError(
            f"{label} differs from the exact reviewed identity"
        )


def _reviewed_transport_identity_record(
    *,
    sha256: str,
    device: int,
    inode: int,
    ctime_ns: int,
    size: int,
    lifecycle_state: str,
    label: str,
) -> dict[str, object]:
    """Validate and format one operator-recorded transport generation."""

    return {
        "sha256": _normalise_sha256(sha256),
        "device": _normalise_identity_integer(
            device,
            label=f"{label} device",
            allow_zero=True,
        ),
        "inode": _normalise_identity_integer(
            inode,
            label=f"{label} inode",
            allow_zero=False,
        ),
        "ctime_ns": _normalise_identity_integer(
            ctime_ns,
            label=f"{label} ctime_ns",
            allow_zero=True,
        ),
        "size": _normalise_identity_integer(
            size,
            label=f"{label} size",
            allow_zero=False,
        ),
        "lifecycle_state": lifecycle_state,
    }


def _require_transport_snapshot_matches_opened(
    snapshot: transport_journal.JournalSnapshot,
    opened: _OpenedStableFile,
    *,
    label: str,
) -> None:
    """Require module inspection and held-descriptor inspection to agree."""

    if (
        snapshot.data != opened.data
        or snapshot.sha256 != opened.sha256
        or (
            snapshot.device,
            snapshot.inode,
            snapshot.ctime_ns,
            len(snapshot.data),
        )
        != (
            int(opened.metadata.st_dev),
            int(opened.metadata.st_ino),
            int(opened.metadata.st_ctime_ns),
            int(opened.metadata.st_size),
        )
    ):
        raise ExternalReplyAdoptionError(
            f"{label} changed between strict inspections"
        )


def _require_external_reply_source_semantics(
    receipt_bytes: bytes,
    *,
    candidate_lane: str,
    target_id: str,
    text_sha256: str,
) -> tuple[dict[str, Any], str]:
    """Validate the current schema-v4 sending conversational source directly."""

    receipt = _canonical_json_object_bytes(
        receipt_bytes,
        label="confirmed reply source receipt",
    )
    text_hash = _normalise_sha256(text_sha256)
    text = receipt.get("reply_text")
    context = receipt.get("reply_context")
    draft = receipt.get("ai_reply_draft")
    if (
        receipt.get("schema_version") != 4
        or receipt.get("lifecycle_state") != "sending"
        or candidate_lane not in SUPPORTED_REPLY_CANDIDATE_LANES
        or receipt.get("candidate_source") != candidate_lane
        or receipt.get("target_id") != target_id
        or type(receipt.get("author_id")) is not str
        or not re.fullmatch(r"[0-9]{1,30}", receipt["author_id"])
        or type(receipt.get("conversation_id")) is not str
        or not re.fullmatch(r"[0-9]{1,30}", receipt["conversation_id"])
        or type(receipt.get("attempt_epoch")) is not int
        or type(receipt.get("reply_epoch")) is not int
        or receipt.get("attempt_epoch") != receipt.get("reply_epoch")
        or not transport_journal.MIN_CONFIRMATION_EPOCH
        <= receipt["attempt_epoch"]
        <= transport_journal.MAX_CONFIRMATION_EPOCH
        or "confirmation_epoch" in receipt
        or "reply_post_id" in receipt
        or type(text) is not str
        or not text
        or hashlib.sha256(text.encode("utf-8")).hexdigest() != text_hash
        or not isinstance(context, dict)
        or context.get("target_id") != target_id
        or context.get("thread_id") != receipt["conversation_id"]
        or context.get("lane") != candidate_lane
        or not isinstance(draft, dict)
        or draft.get("target_id") != target_id
        or draft.get("thread_id") != receipt["conversation_id"]
        or draft.get("candidate_source") != candidate_lane
        or draft.get("proposed_reply") != text
    ):
        raise ExternalReplyAdoptionError(
            "confirmed reply source is not the exact supported sending receipt"
        )
    return receipt, text


def _require_external_quote_image_source_semantics(
    receipt_bytes: bytes,
    *,
    text_sha256: str,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    """Validate one current schema-v5 attempting quote/image source directly."""

    receipt = _canonical_json_object_bytes(
        receipt_bytes,
        label="regular quote/image source receipt",
    )
    text_hash = _normalise_sha256(text_sha256)
    text = receipt.get("text")
    media_ids = receipt.get("media_ids")
    selected = receipt.get("selected_identity")
    recovery = receipt.get("recovery_plan")
    expected_keys = {
        "schema_version",
        "lifecycle_state",
        "lane",
        "attempt_id",
        "attempt_epoch",
        "payload_revision",
        "payload_sha256",
        "text",
        "text_sha256",
        "media_ids",
        "reply_to_id",
        "made_with_ai",
        "selected_identity",
        "recovery_plan",
    }
    expected_selected_keys = {
        "quote_hash",
        "line_no",
        "source_line_number",
        "image_basename",
        "image_no",
    }
    expected_recovery_keys = {
        "quote_delay_seconds",
        "meme_delay_seconds",
        "quote_history_after",
        "image_history_after",
        "meme_scheduling_enabled",
        "meme_trigger_after_hour",
        "meme_schedule_version",
        "meme_schedule_before",
        "schedule_timezone",
    }
    if (
        set(receipt) != expected_keys
        or receipt.get("schema_version") != 5
        or receipt.get("lifecycle_state") != "attempting"
        or receipt.get("lane") != "quote_image"
        or type(receipt.get("attempt_id")) is not str
        or not SHA256_RE.fullmatch(receipt["attempt_id"])
        or type(receipt.get("attempt_epoch")) is not int
        or not transport_journal.MIN_CONFIRMATION_EPOCH
        <= receipt["attempt_epoch"]
        <= transport_journal.MAX_CONFIRMATION_EPOCH
        or receipt.get("payload_revision") != 1
        or type(text) is not str
        or not text
        or hashlib.sha256(text.encode("utf-8")).hexdigest() != text_hash
        or receipt.get("text_sha256") != text_hash
        or not isinstance(media_ids, list)
        or not 1 <= len(media_ids) <= 4
        or any(
            type(media_id) is not str
            or re.fullmatch(r"[0-9]{1,30}", media_id) is None
            for media_id in media_ids
        )
        or len(set(media_ids)) != len(media_ids)
        or receipt.get("reply_to_id") != ""
        or type(receipt.get("made_with_ai")) is not bool
        or not isinstance(selected, dict)
        or set(selected) != expected_selected_keys
        or type(selected.get("quote_hash")) is not str
        or not SHA256_RE.fullmatch(selected["quote_hash"])
        or hashlib.sha256(" ".join(text.split()).encode("utf-8")).hexdigest()
        != selected["quote_hash"]
        or type(selected.get("line_no")) is not int
        or selected["line_no"] < 0
        or selected.get("source_line_number") != selected["line_no"] + 1
        or type(selected.get("image_no")) is not int
        or selected["image_no"] < 0
        or type(selected.get("image_basename")) is not str
        or not SAFE_BASENAME_RE.fullmatch(selected["image_basename"])
        or not isinstance(recovery, dict)
        or set(recovery) != expected_recovery_keys
        or type(recovery.get("quote_delay_seconds")) is not int
        or recovery["quote_delay_seconds"] <= 0
        or (
            recovery.get("meme_delay_seconds") is not None
            and (
                type(recovery["meme_delay_seconds"]) is not int
                or recovery["meme_delay_seconds"] <= 0
            )
        )
        or type(recovery.get("meme_scheduling_enabled")) is not bool
        or type(recovery.get("meme_trigger_after_hour")) is not int
        or not 0 <= recovery["meme_trigger_after_hour"] <= 23
        or type(recovery.get("meme_schedule_version")) is not int
        or recovery["meme_schedule_version"] < 1
        or not isinstance(recovery.get("meme_schedule_before"), dict)
        or recovery.get("schedule_timezone") != "Europe/London"
    ):
        raise ExternalReplyAdoptionError(
            "regular quote/image source is not the exact supported attempting receipt"
        )
    quote_history = recovery.get("quote_history_after")
    image_history = recovery.get("image_history_after")
    if (
        not isinstance(quote_history, list)
        or any(
            type(value) is not str or not SHA256_RE.fullmatch(value)
            for value in quote_history
        )
        or not isinstance(image_history, list)
        or any(
            type(value) is not str or not SAFE_BASENAME_RE.fullmatch(value)
            for value in image_history
        )
    ):
        raise ExternalReplyAdoptionError(
            "regular quote/image recovery histories contain invalid identities"
        )
    if (
        quote_history != sorted(set(quote_history))
        or selected["quote_hash"] not in quote_history
        or image_history != sorted(set(image_history))
        or selected["image_basename"] not in image_history
    ):
        raise ExternalReplyAdoptionError(
            "regular quote/image recovery histories are not exact sorted identities"
        )
    payload: dict[str, Any] = {
        "text": text,
        "media": {"media_ids": list(media_ids)},
    }
    if receipt["made_with_ai"]:
        payload["made_with_ai"] = True
    compact_payload = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if (
        type(receipt.get("payload_sha256")) is not str
        or hashlib.sha256(compact_payload).hexdigest()
        != receipt["payload_sha256"]
    ):
        raise ExternalReplyAdoptionError(
            "regular quote/image source does not bind its canonical payload"
        )
    return receipt, text, payload


def _open_existing_archive_directory(
    project_fd: int,
    archive_basename: str,
) -> tuple[int, os.stat_result] | None:
    """Open an existing private archive without creating any namespace entry."""

    if _entry_absent(project_fd, archive_basename):
        return None
    metadata = _entry_stat(project_fd, archive_basename)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) & 0o077
    ):
        raise UnsafeReconciliationPathError(
            "marker archive directory is not owned and private"
        )
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0)
    )
    descriptor = os.open(archive_basename, flags, dir_fd=project_fd)
    opened = os.fstat(descriptor)
    current = _entry_stat(project_fd, archive_basename)
    if not _same_inode(metadata, opened) or not _same_inode(opened, current):
        os.close(descriptor)
        raise UnsafeReconciliationPathError(
            "marker archive identity changed while opened"
        )
    return descriptor, opened


def _read_private_archive_entry(
    archive_fd: int,
    basename: str,
    *,
    label: str,
    maximum: int,
) -> _OpenedStableFile | None:
    """Read one optional exact single-link mode-0400 archive entry."""

    if _entry_absent(archive_fd, basename):
        return None
    return _read_opened_stable_file(
        archive_fd,
        basename,
        label=label,
        maximum=maximum,
        expected_mode=EXTERNAL_EVIDENCE_ARCHIVE_MODE,
        expected_links=1,
    )


def _publish_private_archive_bytes(
    archive_fd: int,
    basename: str,
    data: bytes,
    *,
    label: str,
    maximum: int,
) -> tuple[_OpenedStableFile, bool]:
    """Publish exact private bytes no-replace, or accept an exact existing file."""

    if type(data) is not bytes or not data or len(data) > maximum:
        raise ArchiveCommitError(f"{label} bytes are invalid")
    existing = _read_private_archive_entry(
        archive_fd,
        basename,
        label=label,
        maximum=maximum,
    )
    if existing is not None:
        if existing.data != data:
            os.close(existing.descriptor)
            raise ArchiveCommitError(
                f"{label} archive-name collision contains different bytes"
            )
        return existing, False

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
    renamed = False
    try:
        view = memoryview(data)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError(f"short write while publishing {label}")
            view = view[written:]
        os.fsync(descriptor)
        os.fchmod(descriptor, EXTERNAL_EVIDENCE_ARCHIVE_MODE)
        os.fsync(descriptor)
        identity = os.fstat(descriptor)
        temporary_stat = _require_regular_entry(
            archive_fd,
            temporary,
            label=f"temporary {label}",
        )
        if (
            not _same_inode(identity, temporary_stat)
            or identity.st_nlink != 1
            or identity.st_uid != os.geteuid()
            or stat.S_IMODE(identity.st_mode)
            != EXTERNAL_EVIDENCE_ARCHIVE_MODE
        ):
            raise ArchiveCommitError(f"temporary {label} identity changed")
        _rename_noreplace(
            temporary,
            basename,
            source_directory_fd=archive_fd,
            destination_directory_fd=archive_fd,
            label=label,
        )
        renamed = True
        _fsync_directory(archive_fd)
        final = _require_regular_entry(archive_fd, basename, label=label)
        after_fd = os.fstat(descriptor)
        if (
            not _same_inode(identity, final)
            or not _same_inode(identity, after_fd)
            or final.st_nlink != 1
            or final.st_uid != os.geteuid()
            or stat.S_IMODE(final.st_mode)
            != EXTERNAL_EVIDENCE_ARCHIVE_MODE
            or _read_all(descriptor, maximum=maximum) != data
        ):
            raise ArchiveCommitError(f"{label} changed during durable publication")
        return (
            _OpenedStableFile(
                descriptor=descriptor,
                metadata=after_fd,
                data=data,
                sha256=hashlib.sha256(data).hexdigest(),
                basename=basename,
            ),
            True,
        )
    except BaseException:
        if not renamed:
            try:
                current = _entry_stat(archive_fd, temporary)
            except OSError:
                pass
            else:
                if identity is None or _same_inode(identity, current):
                    try:
                        os.unlink(temporary, dir_fd=archive_fd)
                        _fsync_directory(archive_fd)
                    except OSError:
                        pass
        os.close(descriptor)
        raise


def _validate_existing_audit(
    opened: _OpenedStableFile,
    *,
    expected_without_epoch: Mapping[str, object],
    epoch_field: str,
    expected_state: str,
    label: str,
) -> tuple[dict[str, Any], int]:
    """Validate one canonical idempotent audit whose creation time is retained."""

    audit = _canonical_json_object_bytes(opened.data, label=label)
    epoch = audit.get(epoch_field)
    if (
        type(epoch) is not int
        or epoch < transport_journal.MIN_CONFIRMATION_EPOCH
        or epoch > transport_journal.MAX_CONFIRMATION_EPOCH
        or audit.get("state") != expected_state
    ):
        raise ExternalReplyAdoptionError(f"{label} epoch or state is invalid")
    comparable = dict(audit)
    comparable.pop(epoch_field, None)
    if comparable != dict(expected_without_epoch):
        raise ExternalReplyAdoptionError(
            f"{label} conflicts with the reviewed adoption inputs"
        )
    return audit, epoch


def _external_adoption_archive_names(
    transaction_id: str,
    post_id: str,
) -> tuple[str, str, str]:
    stem = f"external_transport_confirmation.{transaction_id}.{post_id}"
    return (
        f"{stem}.evidence.json",
        f"{stem}.prepared.json",
        f"{stem}.completed.json",
    )


def _external_prepared_audit_without_epoch(
    *,
    adoption_operation: str,
    audit_kind: str,
    locks: _OfflineInstanceLocks,
    transaction_id: str,
    transport_lane: str,
    candidate_lane: str,
    target_id: str,
    text_sha256: str,
    payload_sha256: str,
    source: _OpenedStableFile,
    attempting_journal: Mapping[str, object],
    prepared_fence: Mapping[str, object],
    marker: _ActiveBarrierSet,
    marker_sha256: str,
    evidence_archive_path: str,
    evidence_sha256: str,
    confirmed_post_id: str,
    confirmation_epoch: int,
    reconciliation_reference: str,
) -> dict[str, object]:
    """Build every deterministic prepared-audit field except its creation time."""

    return {
        "schema_version": EXTERNAL_ADOPTION_SCHEMA_VERSION,
        "operation_version": EXTERNAL_ADOPTION_SCHEMA_VERSION,
        "document_kind": audit_kind,
        "operation": adoption_operation,
        "state": "prepared",
        "project_root": str(locks.project),
        "project_device": int(locks.project_identity.st_dev),
        "project_inode": int(locks.project_identity.st_ino),
        "transaction_id": transaction_id,
        "transport_lane": transport_lane,
        "candidate_lane": candidate_lane,
        "target_id": target_id,
        "text_sha256": text_sha256,
        "canonical_payload_sha256": payload_sha256,
        "source_receipt": _identity_record(source),
        "source_validator_id": transport_journal.LANE_SOURCE_VALIDATOR_ID,
        "attempting_journal": dict(attempting_journal),
        "prepared_fence": dict(prepared_fence),
        "marker": {
            "active_names": list(marker.names),
            "sha256": marker_sha256,
            "device": int(marker.identity.st_dev),
            "inode": int(marker.identity.st_ino),
            "ctime_ns": int(marker.identity.st_ctime_ns),
            "size": int(marker.identity.st_size),
            "mode": oct(stat.S_IMODE(marker.identity.st_mode)),
            "same_inode_and_bytes": True,
        },
        "confirmed_post_id": confirmed_post_id,
        "confirmation_epoch": confirmation_epoch,
        "external_evidence_archive_path": evidence_archive_path,
        "external_evidence_sha256": evidence_sha256,
        "reconciliation_reference": reconciliation_reference,
        "offline_lock_boundary": {
            "state_directory_flock_acquired": True,
            "abstract_socket_acquired": True,
            "file_flock_acquired": True,
            "ofd_lock_acquired": True,
        },
    }


def _external_completion_audit_without_epoch(
    *,
    adoption_operation: str,
    audit_kind: str,
    prepared_audit_path: str,
    prepared_audit_sha256: str,
    transaction_id: str,
    confirmed_post_id: str,
    confirmation_epoch: int,
    old_journal: Mapping[str, object],
    old_fence: Mapping[str, object],
    new_journal: transport_journal.JournalSnapshot,
    new_fence: transport_journal.JournalSnapshot,
    source: _OpenedStableFile,
) -> dict[str, object]:
    """Build every deterministic completion-audit field except its time."""

    return {
        "schema_version": EXTERNAL_ADOPTION_SCHEMA_VERSION,
        "operation_version": EXTERNAL_ADOPTION_SCHEMA_VERSION,
        "document_kind": audit_kind,
        "operation": adoption_operation,
        "state": "completed",
        "prepared_audit_path": prepared_audit_path,
        "prepared_audit_sha256": prepared_audit_sha256,
        "transaction_id": transaction_id,
        "confirmed_post_id": confirmed_post_id,
        "confirmation_epoch": confirmation_epoch,
        "old_attempting_journal": dict(old_journal),
        "old_prepared_fence": dict(old_fence),
        "new_confirmed_journal": _transport_snapshot_record(new_journal),
        "new_confirmed_fence": _transport_snapshot_record(new_fence),
        "source_receipt": _identity_record(source),
        "source_receipt_still_present_and_exact": True,
        "transport_classification": "confirmed_pair",
        "confirmed_pair_inspection_exact": True,
    }


def adopt_externally_confirmed_reply_offline(
    *,
    project_root: Path,
    expected_marker_sha256: str,
    expected_marker_device: int,
    expected_marker_inode: int,
    expected_marker_ctime_ns: int,
    expected_marker_size: int,
    expected_source_receipt_basename: str,
    expected_source_receipt_sha256: str,
    expected_source_receipt_device: int,
    expected_source_receipt_inode: int,
    expected_source_receipt_ctime_ns: int,
    expected_source_receipt_size: int,
    expected_source_lifecycle: str,
    expected_candidate_lane: str,
    expected_target_id: str,
    expected_text_sha256: str,
    expected_transaction_id: str,
    expected_transport_lane: str,
    expected_canonical_payload_sha256: str,
    expected_journal_sha256: str,
    expected_journal_device: int,
    expected_journal_inode: int,
    expected_journal_ctime_ns: int,
    expected_journal_size: int,
    expected_fence_sha256: str,
    expected_fence_device: int,
    expected_fence_inode: int,
    expected_fence_ctime_ns: int,
    expected_fence_size: int,
    confirmed_post_id: str,
    confirmation_epoch: int,
    external_evidence_path: Path,
    expected_external_evidence_sha256: str,
    reconciliation_reference: str,
    confirm_external_publication_reviewed: bool,
    confirm_offline_reconciliation_complete: bool,
    check_only: bool,
    archive_basename: str = DEFAULT_ARCHIVE_BASENAME,
    now: Callable[[], int] | None = None,
    _fault_injector: Callable[[str], None] | None = None,
    source_kind: str = "reply",
) -> ExternalReplyAdoptionResult:
    """Check or adopt one externally established publication, network-free."""

    if source_kind not in {"reply", "quote_image"}:
        raise ExternalReplyAdoptionError("external publication kind is unsupported")
    is_quote_image = source_kind == "quote_image"
    source_basename = (
        REGULAR_POST_RECEIPT_BASENAME
        if is_quote_image
        else CONFIRMED_REPLY_RECEIPT_BASENAME
    )
    source_label = (
        "regular quote/image source receipt"
        if is_quote_image
        else "confirmed reply source receipt"
    )
    adoption_operation = (
        EXTERNAL_MAIN_ADOPTION_OPERATION
        if is_quote_image
        else EXTERNAL_ADOPTION_OPERATION
    )
    audit_kind = (
        EXTERNAL_MAIN_ADOPTION_AUDIT_KIND
        if is_quote_image
        else EXTERNAL_ADOPTION_AUDIT_KIND
    )

    if confirm_external_publication_reviewed is not True:
        raise ExternalReplyAdoptionError(
            "external publication adoption requires explicit publication review"
        )
    if not check_only and confirm_offline_reconciliation_complete is not True:
        raise ExternalReplyAdoptionError(
            "mutating external publication adoption requires offline reconciliation acknowledgement"
        )
    if type(check_only) is not bool:
        raise ExternalReplyAdoptionError("check-only selection is invalid")
    marker_hash = _normalise_sha256(expected_marker_sha256)
    source_hash = _normalise_sha256(expected_source_receipt_sha256)
    text_hash = _normalise_sha256(expected_text_sha256)
    payload_hash = _normalise_sha256(expected_canonical_payload_sha256)
    journal_hash = _normalise_sha256(expected_journal_sha256)
    fence_hash = _normalise_sha256(expected_fence_sha256)
    evidence_hash = _normalise_sha256(expected_external_evidence_sha256)
    transaction_id = str(expected_transaction_id).strip().lower()
    if not SHA256_RE.fullmatch(transaction_id):
        raise ExternalReplyAdoptionError(
            "transport transaction ID must be 64 lowercase hex digits"
        )
    if is_quote_image:
        if (
            expected_source_receipt_basename != source_basename
            or expected_source_lifecycle != "attempting"
            or expected_transport_lane != "quote_image"
            or expected_candidate_lane != "quote_image"
            or expected_target_id != ""
        ):
            raise ExternalReplyAdoptionError(
                "external main-post adoption is limited to one attempting "
                "quote/image receipt"
            )
        target_id = ""
    else:
        if (
            expected_source_receipt_basename != source_basename
            or expected_source_lifecycle != "sending"
            or expected_transport_lane != "conversational_reply"
            or expected_candidate_lane not in SUPPORTED_REPLY_CANDIDATE_LANES
        ):
            raise ExternalReplyAdoptionError(
                "external adoption is limited to one sending conversational reply receipt"
            )
        target_id = _normalise_post_id(expected_target_id, label="reply target")
    post_id = _normalise_post_id(
        confirmed_post_id,
        label="confirmed main post" if is_quote_image else "confirmed reply",
    )
    confirmed_epoch = _normalise_confirmation_epoch(confirmation_epoch)
    reference = _normalise_reference(reconciliation_reference)
    archive_basename = _validate_basename(
        archive_basename,
        label="archive directory name",
    )
    marker_expected_values = (
        _normalise_identity_integer(
            expected_marker_device,
            label="marker device",
            allow_zero=True,
        ),
        _normalise_identity_integer(
            expected_marker_inode,
            label="marker inode",
            allow_zero=False,
        ),
        _normalise_identity_integer(
            expected_marker_ctime_ns,
            label="marker ctime_ns",
            allow_zero=True,
        ),
        _normalise_identity_integer(
            expected_marker_size,
            label="marker size",
            allow_zero=False,
        ),
    )
    old_journal_record = _reviewed_transport_identity_record(
        sha256=journal_hash,
        device=expected_journal_device,
        inode=expected_journal_inode,
        ctime_ns=expected_journal_ctime_ns,
        size=expected_journal_size,
        lifecycle_state="attempting",
        label="attempting journal",
    )
    old_fence_record = _reviewed_transport_identity_record(
        sha256=fence_hash,
        device=expected_fence_device,
        inode=expected_fence_inode,
        ctime_ns=expected_fence_ctime_ns,
        size=expected_fence_size,
        lifecycle_state="prepared",
        label="prepared fence",
    )
    evidence_archive_name, prepared_audit_name, completed_audit_name = (
        _external_adoption_archive_names(transaction_id, post_id)
    )
    evidence_archive_path = f"{archive_basename}/{evidence_archive_name}"
    prepared_audit_path = f"{archive_basename}/{prepared_audit_name}"
    completed_audit_path = f"{archive_basename}/{completed_audit_name}"

    locks = _acquire_offline_instance_locks(Path(project_root))
    active_barriers: _ActiveBarrierSet | None = None
    source: _OpenedStableFile | None = None
    current_journal_file: _OpenedStableFile | None = None
    current_fence_file: _OpenedStableFile | None = None
    evidence: _HeldExternalEvidence | None = None
    archive_fd: int | None = None
    archive_stat: os.stat_result | None = None
    archive_entries: list[_OpenedStableFile] = []
    try:
        locks.revalidate()
        active_barriers = _open_active_barrier_set(locks.project_fd)
        if active_barriers.names != (
            MARKER_BASENAME,
            RESTART_BARRIER_BASENAME,
        ):
            raise ExternalReplyAdoptionError(
                "external publication adoption requires the exact paired ambiguity marker"
            )
        marker_data = _read_all(
            active_barriers.descriptor,
            maximum=MAX_MARKER_BYTES,
        )
        marker_after = os.fstat(active_barriers.descriptor)
        if (
            _stable_metadata_identity(active_barriers.identity)
            != _stable_metadata_identity(marker_after)
            or hashlib.sha256(marker_data).hexdigest() != marker_hash
            or (
                int(active_barriers.identity.st_dev),
                int(active_barriers.identity.st_ino),
                int(active_barriers.identity.st_ctime_ns),
                int(active_barriers.identity.st_size),
            )
            != marker_expected_values
        ):
            raise ExternalReplyAdoptionError(
                "ambiguity marker pair differs from the exact reviewed identity"
            )
        marker_document = _canonical_json_object_bytes(
            marker_data,
            label="ambiguity marker",
        )
        if (
            marker_document.get("schema_version") != 1
            or marker_document.get("outcome") != "ambiguous_remote_post"
            or marker_document.get("reply_to_id") != target_id
            or marker_document.get("text_sha256") != text_hash
            or (
                not is_quote_image
                and marker_document.get("media_ids") != []
            )
        ):
            raise ExternalReplyAdoptionError(
                "ambiguity marker does not bind the reviewed publication"
            )

        source = _read_opened_stable_file(
            locks.project_fd,
            source_basename,
            label=source_label,
            maximum=transport_journal.JOURNAL_MAX_BYTES,
            expected_mode=transport_journal.JOURNAL_MODE,
            expected_links=1,
        )
        _require_expected_opened_identity(
            source,
            expected_sha256=source_hash,
            expected_device=expected_source_receipt_device,
            expected_inode=expected_source_receipt_inode,
            expected_ctime_ns=expected_source_receipt_ctime_ns,
            expected_size=expected_source_receipt_size,
            label=source_label,
        )
        if is_quote_image:
            source_document, publication_text, source_payload = (
                _require_external_quote_image_source_semantics(
                    source.data,
                    text_sha256=text_hash,
                )
            )
            if (
                marker_document.get("media_ids")
                != source_document.get("media_ids")
                or confirmed_epoch < source_document["attempt_epoch"]
            ):
                raise ExternalReplyAdoptionError(
                    "published quote/image evidence conflicts with its source attempt"
                )
        else:
            source_document, publication_text = (
                _require_external_reply_source_semantics(
                    source.data,
                    candidate_lane=expected_candidate_lane,
                    target_id=target_id,
                    text_sha256=text_hash,
                )
            )
            source_payload = None

        current_journal_file = _read_opened_stable_file(
            locks.project_fd,
            TRANSPORT_JOURNAL_BASENAME,
            label="remote-write transport journal",
            maximum=transport_journal.JOURNAL_MAX_BYTES,
            expected_mode=transport_journal.JOURNAL_MODE,
            expected_links=1,
        )
        current_fence_file = _read_opened_stable_file(
            locks.project_fd,
            TRANSPORT_FENCE_BASENAME,
            label="remote-write transport fence",
            maximum=transport_journal.JOURNAL_MAX_BYTES,
            expected_mode=transport_journal.JOURNAL_MODE,
            expected_links=1,
        )
        journal_path = locks.project / TRANSPORT_JOURNAL_BASENAME
        transport_state = transport_journal.inspect_transport_state(journal_path)
        if (
            transport_state.classification
            not in {
                "attempting_pair",
                "confirmed_pair",
                "lifecycle_transition_in_progress",
            }
            or transport_state.errors
            or transport_state.retirement_guard_names
            or transport_state.journal is None
            or transport_state.fence is None
            or (
                transport_state.classification
                == "lifecycle_transition_in_progress"
                and len(transport_state.staging_names) != 1
            )
            or (
                transport_state.classification
                != "lifecycle_transition_in_progress"
                and transport_state.staging_names
            )
        ):
            raise ExternalReplyAdoptionError(
                "transport namespace is not one exact attempting, adopted, or resumable pair"
            )
        _require_transport_snapshot_matches_opened(
            transport_state.journal,
            current_journal_file,
            label="transport journal",
        )
        _require_transport_snapshot_matches_opened(
            transport_state.fence,
            current_fence_file,
            label="transport fence",
        )
        if transport_state.journal.document.get("lifecycle_state") == "attempting":
            _require_expected_opened_identity(
                current_journal_file,
                expected_sha256=journal_hash,
                expected_device=expected_journal_device,
                expected_inode=expected_journal_inode,
                expected_ctime_ns=expected_journal_ctime_ns,
                expected_size=expected_journal_size,
                label="attempting transport journal",
            )
        _require_expected_opened_identity(
            current_fence_file,
            expected_sha256=fence_hash,
            expected_device=expected_fence_device,
            expected_inode=expected_fence_inode,
            expected_ctime_ns=expected_fence_ctime_ns,
            expected_size=expected_fence_size,
            label="prepared transport fence",
        )
        journal_document = transport_state.journal.document
        fence_document = transport_state.fence.document
        payload = journal_document.get("remote_payload")
        if not isinstance(payload, dict):
            raise ExternalReplyAdoptionError("transport payload is not an object")
        payload_bytes = transport_journal.canonical_json_bytes(payload)
        expected_payload_keys = (
            set(source_payload)
            if source_payload is not None
            else {"text", "reply"}
        )
        if source_payload is None and payload.get("made_with_ai") is True:
            expected_payload_keys.add("made_with_ai")
        for document in (journal_document, fence_document):
            bound_source = document.get("source_receipt")
            validation = document.get("source_validation")
            if (
                document.get("transaction_id") != transaction_id
                or document.get("lane") != expected_transport_lane
                or document.get("remote_payload") != payload
                or document.get("remote_payload_sha256") != payload_hash
                or not isinstance(bound_source, dict)
                or bound_source.get("basename")
                != source_basename
                or bound_source.get("sha256") != source_hash
                or bound_source.get("device")
                != int(source.metadata.st_dev)
                or bound_source.get("inode")
                != int(source.metadata.st_ino)
                or bound_source.get("ctime_ns")
                != int(source.metadata.st_ctime_ns)
                or bound_source.get("size") != len(source.data)
                or not isinstance(validation, dict)
                or validation.get("validator_id")
                != transport_journal.LANE_SOURCE_VALIDATOR_ID
                or validation.get("receipt_sha256") != source_hash
                or validation.get("payload_sha256") != payload_hash
            ):
                raise ExternalReplyAdoptionError(
                    "transport pair does not bind the exact reviewed source receipt"
                )
        if (
            fence_document.get("lifecycle_state") != "prepared"
            or set(payload) != expected_payload_keys
            or payload.get("text") != publication_text
            or (
                source_payload is not None
                and payload != source_payload
            )
            or (
                source_payload is None
                and payload.get("reply")
                != {"in_reply_to_tweet_id": target_id}
            )
            or hashlib.sha256(payload_bytes).hexdigest() != payload_hash
        ):
            raise ExternalReplyAdoptionError(
                "canonical transport payload differs from the reviewed publication"
            )
        if transport_state.journal.document.get("lifecycle_state") == "confirmed" and (
            journal_document.get("lifecycle_state") != "confirmed"
            or journal_document.get("remote_post_id") != post_id
            or journal_document.get("confirmation_epoch") != confirmed_epoch
        ):
            raise ExternalReplyAdoptionError(
                "confirmed transport pair contains another remote result"
            )

        evidence = _open_external_evidence(Path(external_evidence_path))
        if evidence.file.sha256 != evidence_hash:
            raise ExternalReplyAdoptionError(
                "external publication evidence SHA-256 differs from review"
            )

        existing_archive = _open_existing_archive_directory(
            locks.project_fd,
            archive_basename,
        )
        if existing_archive is not None:
            archive_fd, archive_stat = existing_archive
            _require_archive_path_identity(
                locks.project_fd,
                archive_basename,
                archive_fd,
                archive_stat,
            )
        existing_evidence: _OpenedStableFile | None = None
        existing_prepared: _OpenedStableFile | None = None
        existing_completed: _OpenedStableFile | None = None
        if archive_fd is not None:
            expected_external_names = {
                evidence_archive_name,
                prepared_audit_name,
                completed_audit_name,
            }
            transaction_prefix = (
                f"external_transport_confirmation.{transaction_id}."
            )
            conflicting_external_names = sorted(
                name
                for name in os.listdir(archive_fd)
                if name.startswith(transaction_prefix)
                and name not in expected_external_names
            )
            if conflicting_external_names:
                raise ExternalReplyAdoptionError(
                    "a conflicting external confirmation archive already exists "
                    "for this transaction"
                )
            existing_evidence = _read_private_archive_entry(
                archive_fd,
                evidence_archive_name,
                label="external publication evidence archive",
                maximum=EXTERNAL_EVIDENCE_MAX_BYTES,
            )
            existing_prepared = _read_private_archive_entry(
                archive_fd,
                prepared_audit_name,
                label="external confirmation prepared audit",
                maximum=EXTERNAL_AUDIT_MAX_BYTES,
            )
            existing_completed = _read_private_archive_entry(
                archive_fd,
                completed_audit_name,
                label="external confirmation completion audit",
                maximum=EXTERNAL_AUDIT_MAX_BYTES,
            )
            archive_entries.extend(
                item
                for item in (
                    existing_evidence,
                    existing_prepared,
                    existing_completed,
                )
                if item is not None
            )
            marker_archive_name = f"ambiguous_post_outcome.{marker_hash}.json"
            marker_receipt_name = f"{marker_archive_name}.reconciliation.json"
            if not _entry_absent(archive_fd, marker_archive_name) or not _entry_absent(
                archive_fd,
                marker_receipt_name,
            ):
                raise ExternalReplyAdoptionError(
                    "marker archival has already begun; preserve and review its fail-closed state"
                )
        if existing_evidence is not None and existing_evidence.data != evidence.file.data:
            raise ExternalReplyAdoptionError(
                "external evidence archive collision contains different bytes"
            )
        if existing_evidence is None and (
            existing_prepared is not None or existing_completed is not None
        ):
            raise ExternalReplyAdoptionError(
                "external adoption audit exists without its evidence archive"
            )
        if existing_prepared is None and existing_completed is not None:
            raise ExternalReplyAdoptionError(
                "external completion audit exists without its prepared audit"
            )

        prepared_without_epoch = _external_prepared_audit_without_epoch(
            adoption_operation=adoption_operation,
            audit_kind=audit_kind,
            locks=locks,
            transaction_id=transaction_id,
            transport_lane=expected_transport_lane,
            candidate_lane=expected_candidate_lane,
            target_id=target_id,
            text_sha256=text_hash,
            payload_sha256=payload_hash,
            source=source,
            attempting_journal=old_journal_record,
            prepared_fence=old_fence_record,
            marker=active_barriers,
            marker_sha256=marker_hash,
            evidence_archive_path=evidence_archive_path,
            evidence_sha256=evidence_hash,
            confirmed_post_id=post_id,
            confirmation_epoch=confirmed_epoch,
            reconciliation_reference=reference,
        )
        prepared_sha256: str | None = None
        if existing_prepared is not None:
            _validate_existing_audit(
                existing_prepared,
                expected_without_epoch=prepared_without_epoch,
                epoch_field="audit_creation_epoch",
                expected_state="prepared",
                label="external confirmation prepared audit",
            )
            prepared_sha256 = existing_prepared.sha256

        external_binding = journal_document.get("external_confirmation")
        expected_old_journal_binding = {
            key: value
            for key, value in old_journal_record.items()
            if key != "lifecycle_state"
        }
        expected_old_fence_binding = {
            key: value
            for key, value in old_fence_record.items()
            if key != "lifecycle_state"
        }
        expected_external_binding = (
            {
                "schema_version": (
                    transport_journal.EXTERNAL_CONFIRMATION_BINDING_SCHEMA_VERSION
                ),
                "document_kind": (
                    transport_journal.EXTERNAL_CONFIRMATION_BINDING_KIND
                ),
                "prepared_audit_basename": prepared_audit_name,
                "prepared_audit_sha256": prepared_sha256,
                "evidence_archive_basename": evidence_archive_name,
                "evidence_sha256": evidence_hash,
                "attempting_journal": expected_old_journal_binding,
                "prepared_fence": expected_old_fence_binding,
            }
            if prepared_sha256 is not None
            else None
        )
        torn_layout = None
        if (
            transport_state.classification
            == "lifecycle_transition_in_progress"
        ):
            if (
                existing_evidence is None
                or existing_prepared is None
                or expected_external_binding is None
            ):
                raise ExternalReplyAdoptionError(
                    "a torn external confirmation transition requires its exact evidence archive and prepared audit"
                )
            torn_layout = (
                transport_journal._inspect_exact_external_confirmation_transition(
                    path=journal_path,
                    attempting_journal_identity=expected_old_journal_binding,
                    prepared_fence_identity=expected_old_fence_binding,
                    confirmed_post_id=post_id,
                    confirmation_epoch=confirmed_epoch,
                    external_binding=expected_external_binding,
                )
            )
        elif transport_state.classification == "confirmed_pair":
            if (
                existing_prepared is None
                or prepared_sha256 is None
                or not isinstance(external_binding, dict)
                or external_binding.get("prepared_audit_basename")
                != prepared_audit_name
                or external_binding.get("prepared_audit_sha256")
                != prepared_sha256
                or external_binding.get("evidence_archive_basename")
                != evidence_archive_name
                or external_binding.get("evidence_sha256") != evidence_hash
                or external_binding.get("attempting_journal")
                != expected_old_journal_binding
                or external_binding.get("prepared_fence")
                != expected_old_fence_binding
            ):
                raise ExternalReplyAdoptionError(
                    "confirmed pair lacks the exact durable external-adoption identity"
                )
        elif external_binding is not None:
            raise ExternalReplyAdoptionError(
                "attempting pair unexpectedly contains external confirmation provenance"
            )

        completed_sha256: str | None = None
        if existing_completed is not None:
            if (
                transport_state.classification != "confirmed_pair"
                or prepared_sha256 is None
            ):
                raise ExternalReplyAdoptionError(
                    "completion audit conflicts with an unconfirmed transport pair"
                )
            completed_without_epoch = _external_completion_audit_without_epoch(
                adoption_operation=adoption_operation,
                audit_kind=audit_kind,
                prepared_audit_path=prepared_audit_path,
                prepared_audit_sha256=prepared_sha256,
                transaction_id=transaction_id,
                confirmed_post_id=post_id,
                confirmation_epoch=confirmed_epoch,
                old_journal=old_journal_record,
                old_fence=old_fence_record,
                new_journal=transport_state.journal,
                new_fence=transport_state.fence,
                source=source,
            )
            _validate_existing_audit(
                existing_completed,
                expected_without_epoch=completed_without_epoch,
                epoch_field="completion_epoch",
                expected_state="completed",
                label="external confirmation completion audit",
            )
            completed_sha256 = existing_completed.sha256

        if torn_layout is not None:
            adoption_state = torn_layout.phase
        elif existing_completed is not None:
            adoption_state = "already_complete"
        elif transport_state.classification == "confirmed_pair":
            adoption_state = "resumable_after_confirmed_transition"
        elif existing_prepared is not None:
            adoption_state = "resumable_after_prepared_audit"
        elif existing_evidence is not None:
            adoption_state = "resumable_after_evidence_archive"
        else:
            adoption_state = "first_adoption"

        planned_new_transport: dict[str, object] = {
            "classification": "confirmed_pair",
            "confirmed_post_id": post_id,
            "confirmation_epoch": confirmed_epoch,
            "journal": (
                _transport_snapshot_record(transport_state.journal)
                if transport_state.classification == "confirmed_pair"
                else {
                    "identity": "published_durably_only_in_apply_mode",
                    "lifecycle_state": "confirmed",
                }
            ),
            "fence": _transport_snapshot_record(transport_state.fence),
        }

        def build_result(
            *,
            execution: str,
            state_name: str,
            final_state: transport_journal.TransportJournalState,
            marker_result: MarkerArchiveResult | None,
            prepared_hash_value: str | None,
            completed_hash_value: str | None,
            durability: dict[str, bool],
            no_mutation: bool,
        ) -> ExternalReplyAdoptionResult:
            return ExternalReplyAdoptionResult(
                schema_version=EXTERNAL_ADOPTION_SCHEMA_VERSION,
                operation=adoption_operation,
                execution=execution,
                adoption_state=state_name,
                project_root=str(locks.project),
                project_device=int(locks.project_identity.st_dev),
                project_inode=int(locks.project_identity.st_ino),
                transaction_id=transaction_id,
                transport_lane=expected_transport_lane,
                candidate_lane=expected_candidate_lane,
                target_id=target_id,
                text_sha256=text_hash,
                canonical_payload_sha256=payload_hash,
                confirmed_post_id=post_id,
                confirmation_epoch=confirmed_epoch,
                remote_outcome="operator_attested_published",
                operator_confirmed_external_publication_reviewed=True,
                reconciliation_reference=reference,
                source_receipt=_identity_record(source),
                old_transport={
                    "classification": "attempting_pair",
                    "journal": old_journal_record,
                    "fence": old_fence_record,
                },
                new_transport=(
                    planned_new_transport
                    if execution == "check_only"
                    and final_state.classification
                    in {
                        "attempting_pair",
                        "lifecycle_transition_in_progress",
                    }
                    else (
                        {
                            "classification": final_state.classification,
                            "journal": _transport_snapshot_record(
                                final_state.journal
                            ),
                            "fence": _transport_snapshot_record(
                                final_state.fence
                            ),
                        }
                        if final_state.journal is not None
                        and final_state.fence is not None
                        else planned_new_transport
                    )
                ),
                evidence_archive_path=evidence_archive_path,
                evidence_sha256=evidence_hash,
                prepared_audit_path=prepared_audit_path,
                prepared_audit_sha256=prepared_hash_value,
                completed_audit_path=completed_audit_path,
                completed_audit_sha256=completed_hash_value,
                marker_archive_path=(
                    marker_result.archive_path if marker_result is not None else None
                ),
                marker_audit_path=(
                    marker_result.receipt_path if marker_result is not None else None
                ),
                durability=durability,
                final_marker_presence={
                    MARKER_BASENAME: not _entry_absent(
                        locks.project_fd,
                        MARKER_BASENAME,
                    ),
                    RESTART_BARRIER_BASENAME: not _entry_absent(
                        locks.project_fd,
                        RESTART_BARRIER_BASENAME,
                    ),
                },
                final_transport_classification=final_state.classification,
                final_source_receipt_present=not _entry_absent(
                    locks.project_fd,
                    source_basename,
                ),
                planned_transport_classification="confirmed_pair",
                no_network_request_performed=True,
                check_only_no_mutation=no_mutation,
            )

        if check_only:
            locks.revalidate()
            _require_active_barrier_identity(
                locks.project_fd,
                expected_names=active_barriers.names,
                expected_identity=active_barriers.identity,
                opened_descriptor=active_barriers.descriptor,
                expected_total_links=2,
            )
            _revalidate_opened_stable_file(
                locks.project_fd,
                source,
                label=source_label,
                maximum=transport_journal.JOURNAL_MAX_BYTES,
            )
            _revalidate_opened_stable_file(
                locks.project_fd,
                current_journal_file,
                label="transport journal",
                maximum=transport_journal.JOURNAL_MAX_BYTES,
            )
            _revalidate_opened_stable_file(
                locks.project_fd,
                current_fence_file,
                label="transport fence",
                maximum=transport_journal.JOURNAL_MAX_BYTES,
            )
            _revalidate_external_evidence(evidence)
            if torn_layout is not None:
                if (
                    archive_fd is None
                    or archive_stat is None
                    or existing_evidence is None
                    or existing_prepared is None
                    or expected_external_binding is None
                ):
                    raise ExternalReplyAdoptionError(
                        "torn external confirmation evidence disappeared during check-only validation"
                    )
                _require_archive_path_identity(
                    locks.project_fd,
                    archive_basename,
                    archive_fd,
                    archive_stat,
                )
                _revalidate_opened_stable_file(
                    archive_fd,
                    existing_evidence,
                    label="external publication evidence archive",
                    maximum=EXTERNAL_EVIDENCE_MAX_BYTES,
                )
                _revalidate_opened_stable_file(
                    archive_fd,
                    existing_prepared,
                    label="external confirmation prepared audit",
                    maximum=EXTERNAL_AUDIT_MAX_BYTES,
                )
                final_torn_layout = (
                    transport_journal._inspect_exact_external_confirmation_transition(
                        path=journal_path,
                        attempting_journal_identity=expected_old_journal_binding,
                        prepared_fence_identity=expected_old_fence_binding,
                        confirmed_post_id=post_id,
                        confirmation_epoch=confirmed_epoch,
                        external_binding=expected_external_binding,
                    )
                )
                if (
                    final_torn_layout.phase != torn_layout.phase
                    or final_torn_layout.staging_name
                    != torn_layout.staging_name
                    or final_torn_layout.attempting.data
                    != torn_layout.attempting.data
                    or final_torn_layout.attempting.device
                    != torn_layout.attempting.device
                    or final_torn_layout.attempting.inode
                    != torn_layout.attempting.inode
                    or final_torn_layout.attempting.ctime_ns
                    != torn_layout.attempting.ctime_ns
                    or final_torn_layout.confirmed.data
                    != torn_layout.confirmed.data
                    or final_torn_layout.confirmed.device
                    != torn_layout.confirmed.device
                    or final_torn_layout.confirmed.inode
                    != torn_layout.confirmed.inode
                    or final_torn_layout.confirmed.ctime_ns
                    != torn_layout.confirmed.ctime_ns
                ):
                    raise ExternalReplyAdoptionError(
                        "torn external confirmation layout changed during check-only validation"
                    )
            return build_result(
                execution="check_only",
                state_name=adoption_state,
                final_state=transport_state,
                marker_result=None,
                prepared_hash_value=prepared_sha256,
                completed_hash_value=completed_sha256,
                durability={
                    "evidence_archive_durable": existing_evidence is not None,
                    "prepared_audit_durable": existing_prepared is not None,
                    "confirmed_pair_durable": (
                        transport_state.classification == "confirmed_pair"
                    ),
                    "completion_audit_durable": existing_completed is not None,
                    "marker_archive_durable": False,
                    "restart_barrier_retired_last": False,
                },
                no_mutation=True,
            )

        if archive_fd is None:
            archive_fd, archive_stat = _open_or_create_archive_directory(
                locks.project_fd,
                archive_basename,
            )
        assert archive_stat is not None
        _require_archive_path_identity(
            locks.project_fd,
            archive_basename,
            archive_fd,
            archive_stat,
        )
        evidence_archive, _evidence_created = _publish_private_archive_bytes(
            archive_fd,
            evidence_archive_name,
            evidence.file.data,
            label="external publication evidence archive",
            maximum=EXTERNAL_EVIDENCE_MAX_BYTES,
        )
        archive_entries.append(evidence_archive)
        if _fault_injector is not None:
            _fault_injector("evidence_archived")

        if existing_prepared is None:
            audit_epoch = int((now or time.time)())
            _normalise_confirmation_epoch(audit_epoch)
            prepared_document = {
                **prepared_without_epoch,
                "audit_creation_epoch": audit_epoch,
            }
            prepared_bytes = _canonical_json_bytes(prepared_document)
            prepared_archive, _prepared_created = _publish_private_archive_bytes(
                archive_fd,
                prepared_audit_name,
                prepared_bytes,
                label="external confirmation prepared audit",
                maximum=EXTERNAL_AUDIT_MAX_BYTES,
            )
            archive_entries.append(prepared_archive)
            prepared_sha256 = prepared_archive.sha256
        else:
            prepared_sha256 = existing_prepared.sha256
            prepared_archive = existing_prepared
        assert prepared_sha256 is not None
        if _fault_injector is not None:
            _fault_injector("prepared_audit_published")

        transport_was_adopted = False

        def revalidate_pre_transition(operation: str) -> None:
            nonlocal transport_was_adopted
            if transport_was_adopted:
                raise ExternalReplyAdoptionError(
                    f"{operation} attempted to reuse pre-transition authority"
                )
            locks.revalidate()
            _require_archive_path_identity(
                locks.project_fd,
                archive_basename,
                archive_fd,
                archive_stat,
            )
            _require_active_barrier_identity(
                locks.project_fd,
                expected_names=active_barriers.names,
                expected_identity=active_barriers.identity,
                opened_descriptor=active_barriers.descriptor,
                expected_total_links=2,
            )
            if _read_all(
                active_barriers.descriptor,
                maximum=MAX_MARKER_BYTES,
            ) != marker_data:
                raise ExternalReplyAdoptionError(
                    "ambiguity marker bytes changed before transport adoption"
                )
            _revalidate_opened_stable_file(
                locks.project_fd,
                source,
                label=source_label,
                maximum=transport_journal.JOURNAL_MAX_BYTES,
            )
            _revalidate_opened_stable_file(
                locks.project_fd,
                current_fence_file,
                label="transport fence",
                maximum=transport_journal.JOURNAL_MAX_BYTES,
            )
            _revalidate_external_evidence(evidence)
            _revalidate_opened_stable_file(
                archive_fd,
                evidence_archive,
                label="external publication evidence archive",
                maximum=EXTERNAL_EVIDENCE_MAX_BYTES,
            )
            _revalidate_opened_stable_file(
                archive_fd,
                prepared_archive,
                label="external confirmation prepared audit",
                maximum=EXTERNAL_AUDIT_MAX_BYTES,
            )
            if operation.endswith("displaced cleanup"):
                transition_state = transport_journal.inspect_transport_state(
                    journal_path
                )
                transition_document = (
                    transition_state.journal.document
                    if transition_state.journal is not None
                    else {}
                )
                transition_binding = transition_document.get(
                    "external_confirmation"
                )
                if (
                    transition_state.classification
                    != "lifecycle_transition_in_progress"
                    or len(transition_state.staging_names) != 1
                    or transition_state.retirement_guard_names
                    or transition_state.journal is None
                    or transition_state.fence is None
                    or transition_document.get("transaction_id")
                    != transaction_id
                    or transition_document.get("lifecycle_state")
                    != "confirmed"
                    or transition_document.get("remote_post_id") != post_id
                    or transition_document.get("confirmation_epoch")
                    != confirmed_epoch
                    or not isinstance(transition_binding, dict)
                    or transition_binding.get("prepared_audit_basename")
                    != prepared_audit_name
                    or transition_binding.get("prepared_audit_sha256")
                    != prepared_sha256
                    or transition_binding.get("evidence_archive_basename")
                    != evidence_archive_name
                    or transition_binding.get("evidence_sha256")
                    != evidence_hash
                    or (
                        torn_layout is not None
                        and (
                            transition_state.staging_names
                            != (torn_layout.staging_name,)
                            or transition_state.journal.data
                            != torn_layout.confirmed.data
                            or transition_state.journal.device
                            != torn_layout.confirmed.device
                            or transition_state.journal.inode
                            != torn_layout.confirmed.inode
                        )
                    )
                ):
                    raise ExternalReplyAdoptionError(
                        "transport transition changed before displaced cleanup"
                    )
            else:
                _revalidate_opened_stable_file(
                    locks.project_fd,
                    current_journal_file,
                    label="transport journal",
                    maximum=transport_journal.JOURNAL_MAX_BYTES,
                )

        mutation_authority = issue_transaction_mutation_authority(
            revalidate_pre_transition,
            operation=f"offline external {source_kind} adoption",
        )
        adoption = transport_journal.adopt_externally_confirmed_transport_transaction(
            path=journal_path,
            receipt_path=locks.project / source_basename,
            mutation_authority=mutation_authority,
            expected_transaction_id=transaction_id,
            expected_lane=expected_transport_lane,
            expected_source_receipt_bytes=source.data,
            expected_source_receipt_sha256=source_hash,
            expected_source_receipt_device=int(source.metadata.st_dev),
            expected_source_receipt_inode=int(source.metadata.st_ino),
            expected_source_receipt_ctime_ns=int(source.metadata.st_ctime_ns),
            expected_source_receipt_size=len(source.data),
            expected_source_validator_id=transport_journal.LANE_SOURCE_VALIDATOR_ID,
            expected_payload_bytes=payload_bytes,
            expected_payload_sha256=payload_hash,
            expected_reply_target_id=target_id,
            expected_journal_sha256=journal_hash,
            expected_journal_device=expected_journal_device,
            expected_journal_inode=expected_journal_inode,
            expected_journal_ctime_ns=expected_journal_ctime_ns,
            expected_journal_size=expected_journal_size,
            expected_fence_sha256=fence_hash,
            expected_fence_device=expected_fence_device,
            expected_fence_inode=expected_fence_inode,
            expected_fence_ctime_ns=expected_fence_ctime_ns,
            expected_fence_size=expected_fence_size,
            confirmed_post_id=post_id,
            confirmation_epoch=confirmed_epoch,
            prepared_audit_basename=prepared_audit_name,
            prepared_audit_sha256=prepared_sha256,
            evidence_archive_basename=evidence_archive_name,
            evidence_sha256=evidence_hash,
        )
        transport_was_adopted = True
        if _fault_injector is not None:
            _fault_injector("transport_confirmed")

        final_transport = transport_journal.inspect_transport_state(journal_path)
        if (
            final_transport.classification != "confirmed_pair"
            or final_transport.errors
            or final_transport.staging_names
            or final_transport.retirement_guard_names
            or final_transport.journal is None
            or final_transport.fence is None
            or adoption.confirmed.post_id != post_id
            or adoption.confirmed.confirmation_epoch != confirmed_epoch
        ):
            raise ExternalReplyAdoptionError(
                "adopted transport pair failed exact completion inspection"
            )
        _revalidate_opened_stable_file(
            locks.project_fd,
            source,
            label=source_label,
            maximum=transport_journal.JOURNAL_MAX_BYTES,
        )
        completed_without_epoch = _external_completion_audit_without_epoch(
            adoption_operation=adoption_operation,
            audit_kind=audit_kind,
            prepared_audit_path=prepared_audit_path,
            prepared_audit_sha256=prepared_sha256,
            transaction_id=transaction_id,
            confirmed_post_id=post_id,
            confirmation_epoch=confirmed_epoch,
            old_journal=old_journal_record,
            old_fence=old_fence_record,
            new_journal=final_transport.journal,
            new_fence=final_transport.fence,
            source=source,
        )
        if existing_completed is None:
            completion_epoch = int((now or time.time)())
            _normalise_confirmation_epoch(completion_epoch)
            completion_document = {
                **completed_without_epoch,
                "completion_epoch": completion_epoch,
            }
            completed_archive, _completed_created = _publish_private_archive_bytes(
                archive_fd,
                completed_audit_name,
                _canonical_json_bytes(completion_document),
                label="external confirmation completion audit",
                maximum=EXTERNAL_AUDIT_MAX_BYTES,
            )
            archive_entries.append(completed_archive)
            completed_sha256 = completed_archive.sha256
        else:
            _validate_existing_audit(
                existing_completed,
                expected_without_epoch=completed_without_epoch,
                epoch_field="completion_epoch",
                expected_state="completed",
                label="external confirmation completion audit",
            )
            completed_archive = existing_completed
            completed_sha256 = existing_completed.sha256
        assert completed_sha256 is not None
        _fsync_directory(archive_fd)
        _revalidate_opened_stable_file(
            archive_fd,
            completed_archive,
            label="external confirmation completion audit",
            maximum=EXTERNAL_AUDIT_MAX_BYTES,
        )
        if _fault_injector is not None:
            _fault_injector("completed_audit_published")

        locks.revalidate()
        _revalidate_opened_stable_file(
            locks.project_fd,
            source,
            label=source_label,
            maximum=transport_journal.JOURNAL_MAX_BYTES,
        )
        marker_reference = (
            "external-confirmation-completed "
            f"path={completed_audit_path} sha256={completed_sha256}"
        )
        if _fault_injector is not None:
            _fault_injector("before_marker_archival")
        marker_result = reconcile_marker_offline(
            project_root=locks.project,
            expected_marker_sha256=marker_hash,
            reconciliation_reference=marker_reference,
            archive_basename=archive_basename,
            now=now,
            _held_locks=locks,
        )
        final_transport = transport_journal.inspect_transport_state(journal_path)
        if (
            not _entry_absent(locks.project_fd, MARKER_BASENAME)
            or not _entry_absent(locks.project_fd, RESTART_BARRIER_BASENAME)
            or final_transport.classification != "confirmed_pair"
            or final_transport.journal is None
            or final_transport.fence is None
            or _entry_absent(
                locks.project_fd,
                source_basename,
            )
        ):
            raise ExternalReplyAdoptionError(
                "external publication adoption final verification failed"
            )
        return build_result(
            execution="applied",
            state_name=(
                adoption.disposition
                if adoption.disposition
                in {
                    "resumable_before_transport_exchange",
                    "resumable_after_transport_exchange",
                }
                else (
                    "first_adoption"
                    if adoption_state == "first_adoption"
                    else "resumed"
                )
            ),
            final_state=final_transport,
            marker_result=marker_result,
            prepared_hash_value=prepared_sha256,
            completed_hash_value=completed_sha256,
            durability={
                "evidence_archive_durable": True,
                "prepared_audit_durable": True,
                "confirmed_pair_durable": True,
                "completion_audit_durable": True,
                "marker_archive_durable": True,
                "restart_barrier_retired_last": True,
            },
            no_mutation=False,
        )
    except transport_journal.TransportJournalError as exc:
        raise ExternalReplyAdoptionError(
            f"transport adoption refused: {exc}"
        ) from exc
    finally:
        seen_descriptors: set[int] = set()
        for opened in (
            *archive_entries,
            current_fence_file,
            current_journal_file,
            source,
        ):
            if opened is None or opened.descriptor in seen_descriptors:
                continue
            seen_descriptors.add(opened.descriptor)
            try:
                os.close(opened.descriptor)
            except OSError:
                pass
        if archive_fd is not None:
            try:
                os.close(archive_fd)
            except OSError:
                pass
        if evidence is not None:
            evidence.close()
        if active_barriers is not None:
            try:
                os.close(active_barriers.descriptor)
            except OSError:
                pass
        locks.close()


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
    _held_locks: _OfflineInstanceLocks | None = None,
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
    owns_locks = _held_locks is None
    locks = (
        _acquire_offline_instance_locks(Path(project_root))
        if _held_locks is None
        else _held_locks
    )
    locks.revalidate()
    project = locks.project
    project_fd = locks.project_fd
    project_identity = locks.project_identity
    instance_socket = locks.instance_socket
    socket_name = locks.socket_name
    lock_fd = locks.lock_fd
    lock_stat = locks.lock_identity
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
        ):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        if owns_locks:
            locks.close()


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
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--reconcile-unattached-media-upload",
        action="store_true",
        help=(
            "Archive and retire one exact sending media pair while preserving "
            "the active ambiguity marker."
        ),
    )
    mode.add_argument(
        "--adopt-externally-confirmed-reply",
        action="store_true",
        help=(
            "Adopt one operator-reviewed published conversational reply into "
            "the ordinary confirmed transport pair before marker archival."
        ),
    )
    mode.add_argument(
        "--adopt-externally-confirmed-main-post",
        action="store_true",
        help=(
            "Adopt one operator-reviewed published quote/image main post into "
            "the ordinary confirmed transport pair before marker archival."
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
    parser.add_argument("--expected-marker-device", type=int)
    parser.add_argument("--expected-marker-inode", type=int)
    parser.add_argument("--expected-marker-ctime-ns", type=int)
    parser.add_argument("--expected-marker-size", type=int)
    parser.add_argument("--expected-source-receipt-basename")
    parser.add_argument("--expected-source-receipt-sha256")
    parser.add_argument("--expected-source-receipt-device", type=int)
    parser.add_argument("--expected-source-receipt-inode", type=int)
    parser.add_argument("--expected-source-receipt-ctime-ns", type=int)
    parser.add_argument("--expected-source-receipt-size", type=int)
    parser.add_argument("--expected-source-lifecycle")
    parser.add_argument("--expected-candidate-lane")
    parser.add_argument("--expected-target-id")
    parser.add_argument("--expected-text-sha256")
    parser.add_argument("--expected-transport-transaction-id")
    parser.add_argument("--expected-transport-lane")
    parser.add_argument("--expected-canonical-payload-sha256")
    parser.add_argument("--expected-journal-sha256")
    parser.add_argument("--expected-journal-device", type=int)
    parser.add_argument("--expected-journal-inode", type=int)
    parser.add_argument("--expected-journal-ctime-ns", type=int)
    parser.add_argument("--expected-journal-size", type=int)
    parser.add_argument("--expected-fence-sha256")
    parser.add_argument("--expected-fence-device", type=int)
    parser.add_argument("--expected-fence-inode", type=int)
    parser.add_argument("--expected-fence-ctime-ns", type=int)
    parser.add_argument("--expected-fence-size", type=int)
    parser.add_argument("--confirmed-post-id")
    parser.add_argument("--confirmation-epoch", type=int)
    parser.add_argument("--external-evidence-path", type=Path)
    parser.add_argument("--expected-external-evidence-sha256")
    parser.add_argument(
        "--confirm-external-publication-reviewed",
        action="store_true",
        help=(
            "Attest that authenticated read-only evidence conclusively proves "
            "the exact tweet was published."
        ),
    )
    parser.add_argument(
        "--check-only",
        action="store_true",
        help=(
            "Acquire the complete stopped-daemon boundary and validate the "
            "external adoption without changing any file."
        ),
    )
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
    external_values = {
        "--expected-marker-device": args.expected_marker_device,
        "--expected-marker-inode": args.expected_marker_inode,
        "--expected-marker-ctime-ns": args.expected_marker_ctime_ns,
        "--expected-marker-size": args.expected_marker_size,
        "--expected-source-receipt-basename": (
            args.expected_source_receipt_basename
        ),
        "--expected-source-receipt-sha256": args.expected_source_receipt_sha256,
        "--expected-source-receipt-device": args.expected_source_receipt_device,
        "--expected-source-receipt-inode": args.expected_source_receipt_inode,
        "--expected-source-receipt-ctime-ns": (
            args.expected_source_receipt_ctime_ns
        ),
        "--expected-source-receipt-size": args.expected_source_receipt_size,
        "--expected-source-lifecycle": args.expected_source_lifecycle,
        "--expected-candidate-lane": args.expected_candidate_lane,
        "--expected-target-id": args.expected_target_id,
        "--expected-text-sha256": args.expected_text_sha256,
        "--expected-transport-transaction-id": (
            args.expected_transport_transaction_id
        ),
        "--expected-transport-lane": args.expected_transport_lane,
        "--expected-canonical-payload-sha256": (
            args.expected_canonical_payload_sha256
        ),
        "--expected-journal-sha256": args.expected_journal_sha256,
        "--expected-journal-device": args.expected_journal_device,
        "--expected-journal-inode": args.expected_journal_inode,
        "--expected-journal-ctime-ns": args.expected_journal_ctime_ns,
        "--expected-journal-size": args.expected_journal_size,
        "--expected-fence-sha256": args.expected_fence_sha256,
        "--expected-fence-device": args.expected_fence_device,
        "--expected-fence-inode": args.expected_fence_inode,
        "--expected-fence-ctime-ns": args.expected_fence_ctime_ns,
        "--expected-fence-size": args.expected_fence_size,
        "--confirmed-post-id": args.confirmed_post_id,
        "--confirmation-epoch": args.confirmation_epoch,
        "--external-evidence-path": args.external_evidence_path,
        "--expected-external-evidence-sha256": (
            args.expected_external_evidence_sha256
        ),
    }
    external_adoption_requested = bool(
        args.adopt_externally_confirmed_reply
        or args.adopt_externally_confirmed_main_post
    )
    if external_adoption_requested:
        source_kind = (
            "quote_image"
            if args.adopt_externally_confirmed_main_post
            else "reply"
        )
        if any(value is not None for value in media_values.values()) or (
            args.confirm_no_tweet_create_attempted
            or args.confirm_unattached_media_abandoned
        ):
            print(
                "refusing media-specific options during external publication adoption",
                file=sys.stderr,
            )
            return 2
        missing = [name for name, value in external_values.items() if value is None]
        if missing:
            print(
                "refusing external publication adoption without: "
                + ", ".join(missing),
                file=sys.stderr,
            )
            return 2
        if not args.confirm_external_publication_reviewed:
            print(
                "refusing external publication adoption without "
                "--confirm-external-publication-reviewed",
                file=sys.stderr,
            )
            return 2
        if not args.check_only and not args.confirm_offline_reconciliation_complete:
            print(
                "refusing mutating external publication adoption without "
                "--confirm-offline-reconciliation-complete",
                file=sys.stderr,
            )
            return 2
        try:
            external_result = adopt_externally_confirmed_reply_offline(
                project_root=args.project_root,
                expected_marker_sha256=args.expected_marker_sha256,
                expected_marker_device=args.expected_marker_device,
                expected_marker_inode=args.expected_marker_inode,
                expected_marker_ctime_ns=args.expected_marker_ctime_ns,
                expected_marker_size=args.expected_marker_size,
                expected_source_receipt_basename=(
                    args.expected_source_receipt_basename
                ),
                expected_source_receipt_sha256=(
                    args.expected_source_receipt_sha256
                ),
                expected_source_receipt_device=(
                    args.expected_source_receipt_device
                ),
                expected_source_receipt_inode=args.expected_source_receipt_inode,
                expected_source_receipt_ctime_ns=(
                    args.expected_source_receipt_ctime_ns
                ),
                expected_source_receipt_size=args.expected_source_receipt_size,
                expected_source_lifecycle=args.expected_source_lifecycle,
                expected_candidate_lane=args.expected_candidate_lane,
                expected_target_id=args.expected_target_id,
                expected_text_sha256=args.expected_text_sha256,
                expected_transaction_id=(
                    args.expected_transport_transaction_id
                ),
                expected_transport_lane=args.expected_transport_lane,
                expected_canonical_payload_sha256=(
                    args.expected_canonical_payload_sha256
                ),
                expected_journal_sha256=args.expected_journal_sha256,
                expected_journal_device=args.expected_journal_device,
                expected_journal_inode=args.expected_journal_inode,
                expected_journal_ctime_ns=args.expected_journal_ctime_ns,
                expected_journal_size=args.expected_journal_size,
                expected_fence_sha256=args.expected_fence_sha256,
                expected_fence_device=args.expected_fence_device,
                expected_fence_inode=args.expected_fence_inode,
                expected_fence_ctime_ns=args.expected_fence_ctime_ns,
                expected_fence_size=args.expected_fence_size,
                confirmed_post_id=args.confirmed_post_id,
                confirmation_epoch=args.confirmation_epoch,
                external_evidence_path=args.external_evidence_path,
                expected_external_evidence_sha256=(
                    args.expected_external_evidence_sha256
                ),
                reconciliation_reference=args.reconciliation_reference,
                confirm_external_publication_reviewed=True,
                confirm_offline_reconciliation_complete=(
                    args.confirm_offline_reconciliation_complete
                ),
                check_only=args.check_only,
                archive_basename=args.archive_directory_name,
                source_kind=source_kind,
            )
        except MarkerReconciliationError as exc:
            print(
                f"external publication adoption refused: {exc}",
                file=sys.stderr,
            )
            return 2
        sys.stdout.buffer.write(_canonical_json_bytes(external_result.to_dict()))
        return 0
    if args.reconcile_unattached_media_upload:
        if any(value is not None for value in external_values.values()) or (
            args.confirm_external_publication_reviewed or args.check_only
        ):
            print(
                "refusing external-reply options with "
                "--reconcile-unattached-media-upload",
                file=sys.stderr,
            )
            return 2
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
    if any(value is not None for value in external_values.values()) or (
        args.confirm_external_publication_reviewed or args.check_only
    ):
        print(
            "refusing external-publication options without an external-adoption mode",
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
