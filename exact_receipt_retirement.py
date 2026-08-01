#!/usr/bin/env python3
"""Crash-resumable, identity-bound retirement of one durable receipt.

The helper deliberately does not parse the receipt.  Its caller supplies the
exact bytes whose durable outcome has already been reconciled.  Retirement is
then a small filesystem protocol:

* durably stage and atomically publish a prepare guard bound to the source
  inode and exact bytes;
* move that exact inode to a fixed cleanup pathname without replacement;
* durably stage and atomically publish a commit guard before deleting the
  displaced inode;
* retire the prepare and commit guards in turn, using the cleanup pathname as
  the barrier while either guard is being removed;
* before final commit-guard cleanup, atomically exchange a permanent,
  hash-chained completion ledger generation while the exact commit guard still
  provides the overlapping restart barrier.

Every legitimate interrupted state is recognisable and resumable.  Unknown
namespace states fail closed.  In particular, a replacement source entry --
including a same-byte replacement with a different inode -- is never accepted
as the source that the caller authorised for retirement.

Normal callers must hold the application's process/instance lock.  The stable
no-follow reads and no-replace moves additionally make namespace faults fail
closed; they are not a substitute for mutual exclusion against a malicious
same-UID process.
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Final

from transaction_mutation_authority import (
    TransactionMutationAuthority,
    require_transaction_mutation_authority,
)


RETIREMENT_SCHEMA_VERSION: Final = 1
RETIREMENT_DOCUMENT_KIND: Final = "mrsMThatcher_exact_receipt_retirement"
RETIREMENT_LEDGER_SCHEMA_VERSION: Final = 1
RETIREMENT_LEDGER_DOCUMENT_KIND: Final = (
    "mrsMThatcher_exact_receipt_retirement_ledger"
)
RETIREMENT_MODE: Final = 0o600
DEFAULT_MAXIMUM_RECEIPT_BYTES: Final = 1024 * 1024
_RENAME_NOREPLACE: Final = 1
_RENAME_EXCHANGE: Final = 2
_GENESIS_PREVIOUS_RECORD_SHA256: Final = "0" * 64
_MARKER_KEYS: Final = frozenset(
    {
        "document_kind",
        "expected_sha256",
        "expected_size",
        "phase",
        "schema_version",
        "source_basename",
        "source_identity",
    }
)
_IDENTITY_KEYS: Final = frozenset(
    {
        "ctime_ns",
        "device",
        "inode",
        "link_count",
        "mode",
        "mtime_ns",
        "owner_uid",
        "size",
    }
)
_LEDGER_KEYS: Final = frozenset(
    {
        "commit_sha256",
        "document_kind",
        "previous_record_sha256",
        "schema_version",
        "sequence",
        "source_basename",
        "source_binding",
        "state",
    }
)
_LEDGER_BINDING_KEYS: Final = frozenset(
    {"expected_sha256", "expected_size", "source_identity"}
)


class ExactReceiptRetirementError(RuntimeError):
    """Raised when exact receipt retirement cannot proceed safely."""


@dataclass(frozen=True)
class FileIdentity:
    """Filesystem identity used to reject replacements across a transition."""

    device: int
    inode: int
    ctime_ns: int
    mtime_ns: int
    size: int
    mode: int
    owner_uid: int
    link_count: int

    @classmethod
    def from_stat(cls, value: os.stat_result) -> "FileIdentity":
        return cls(
            device=int(value.st_dev),
            inode=int(value.st_ino),
            ctime_ns=int(value.st_ctime_ns),
            mtime_ns=int(value.st_mtime_ns),
            size=int(value.st_size),
            mode=stat.S_IMODE(value.st_mode),
            owner_uid=int(value.st_uid),
            link_count=int(value.st_nlink),
        )

    def to_document(self) -> dict[str, int]:
        return {
            "ctime_ns": self.ctime_ns,
            "device": self.device,
            "inode": self.inode,
            "link_count": self.link_count,
            "mode": self.mode,
            "mtime_ns": self.mtime_ns,
            "owner_uid": self.owner_uid,
            "size": self.size,
        }


@dataclass(frozen=True)
class StableEntry:
    data: bytes
    identity: FileIdentity


@dataclass(frozen=True)
class RetirementExpectation:
    """Strict source facts carried across a retirement restart."""

    sha256: str
    size: int
    identity: FileIdentity
    data: bytes | None = None


@dataclass(frozen=True)
class RetirementPaths:
    guard: Path
    commit: Path
    cleanup: Path
    guard_staging: Path
    commit_staging: Path


@dataclass(frozen=True)
class ReceiptRetirementInspection:
    """One deterministic description of the receipt retirement namespace."""

    phase: str
    valid: bool
    blocking: bool
    detail: str
    expected_sha256: str
    source_path: str
    guard_path: str
    commit_path: str
    cleanup_path: str
    guard_staging_path: str
    commit_staging_path: str


@dataclass(frozen=True)
class ReceiptRetirementResult:
    """Successful exact-retirement result."""

    completed: bool
    resumed: bool
    initial_phase: str
    transitions: tuple[str, ...]
    expected_sha256: str


@dataclass(frozen=True)
class RetirementLedgerRecord:
    """One strict permanent retirement-ledger generation."""

    sequence: int
    state: str
    previous_record_sha256: str
    expected_sha256: str
    expected_size: int
    source_identity: FileIdentity | None
    commit_sha256: str
    data: bytes


@dataclass(frozen=True)
class RetirementLedgerInspection:
    """Read-only status for one required permanent ledger namespace."""

    valid: bool
    blocking: bool
    state: str
    sequence: int
    detail: str
    ledger_path: str
    exchange_path: str
    record_sha256: str


def _absolute_path(path: Path) -> Path:
    value = Path(os.path.abspath(os.fspath(path)))
    if not value.name or value.name in {".", ".."}:
        raise ExactReceiptRetirementError("receipt path has no safe basename")
    if "\x00" in os.fspath(value):
        raise ExactReceiptRetirementError("receipt path contains a NUL byte")
    return value


def _retirement_paths(source_path: Path) -> RetirementPaths:
    source = _absolute_path(source_path)
    prefix = f".{source.name}.retirement"
    guard = source.parent / f"{prefix}.guard.json"
    commit = source.parent / f"{prefix}.commit.json"
    return RetirementPaths(
        guard=guard,
        commit=commit,
        cleanup=source.parent / f"{prefix}.cleanup",
        guard_staging=source.parent / f"{prefix}.guard.json.staging",
        commit_staging=source.parent / f"{prefix}.commit.json.staging",
    )


def retirement_ledger_paths(source_path: Path) -> tuple[Path, Path]:
    """Return the permanent ledger and its fixed atomic-exchange pathname."""

    source = _absolute_path(source_path)
    prefix = f".{source.name}.retirement.ledger.json"
    return source.parent / prefix, source.parent / f"{prefix}.exchange"


def retirement_ledger_path_for_receipt(source_path: Path) -> Path:
    """Return the canonical permanent ledger pathname for one receipt."""

    return retirement_ledger_paths(source_path)[0]


def retirement_auxiliary_paths(source_path: Path) -> tuple[Path, ...]:
    """Return every transient non-source retirement pathname.

    The first three entries retain the original guard/commit/cleanup ordering.
    The permanent ledger has a separate API because its normal idle/completed
    presence is not itself a transient auxiliary barrier.
    """

    paths = _retirement_paths(source_path)
    return (
        paths.guard,
        paths.commit,
        paths.cleanup,
        paths.guard_staging,
        paths.commit_staging,
    )


def retirement_barrier_paths(source_path: Path) -> tuple[Path, ...]:
    """Return the canonical receipt and every auxiliary barrier pathname."""

    source = _absolute_path(source_path)
    return (source, *retirement_auxiliary_paths(source))


def retirement_auxiliary_barrier_exists(source_path: Path) -> bool:
    """Return true for any auxiliary namespace entry, including a symlink."""

    for path in retirement_auxiliary_paths(source_path):
        try:
            os.lstat(path)
        except FileNotFoundError:
            continue
        except OSError:
            return True
        return True
    return False


def canonical_receipt_json_bytes(value: Any) -> bytes:
    """Return the byte representation used by the bot's durable JSON writer."""

    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _open_directory(path: Path) -> int:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise ExactReceiptRetirementError("O_NOFOLLOW is required")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_DIRECTORY", 0)
        | nofollow
        | getattr(os, "O_CLOEXEC", 0)
    )
    absolute = Path(os.path.abspath(os.fspath(path)))
    descriptor: int | None = None
    try:
        # Walk from the filesystem root so O_NOFOLLOW applies to every path
        # component rather than only the final parent directory.
        descriptor = os.open(os.path.sep, flags)
        for component in absolute.parts[1:]:
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        metadata = os.fstat(descriptor)
        if not stat.S_ISDIR(metadata.st_mode):
            raise ExactReceiptRetirementError("receipt parent is not a directory")
        return descriptor
    except ExactReceiptRetirementError:
        if descriptor is not None:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor is not None:
            os.close(descriptor)
        raise ExactReceiptRetirementError(
            "receipt retirement parent is unavailable or unsafe"
        ) from exc


def _fsync_directory(directory_fd: int) -> None:
    os.fsync(directory_fd)


def _same_identity(first: FileIdentity, second: FileIdentity) -> bool:
    return first == second


def _same_object_after_rename(
    before: FileIdentity,
    after: FileIdentity,
) -> bool:
    """Compare an inode across rename, which is allowed to advance ctime."""

    return (
        before.device == after.device
        and before.inode == after.inode
        and before.size == after.size
        and before.mode == after.mode
        and before.owner_uid == after.owner_uid
        and before.link_count == after.link_count
        and before.mtime_ns == after.mtime_ns
        and after.ctime_ns >= before.ctime_ns
    )


def _read_stable_entry(
    directory_fd: int,
    name: str,
    *,
    maximum: int,
) -> StableEntry | None:
    """Read one owned single-link regular file through a stable no-follow FD."""

    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise ExactReceiptRetirementError("O_NOFOLLOW is required")
    try:
        before_raw = os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return None
    before = FileIdentity.from_stat(before_raw)
    if (
        not stat.S_ISREG(before_raw.st_mode)
        or before.link_count != 1
        or before.owner_uid != os.geteuid()
        or before.size < 0
        or before.size > maximum
    ):
        raise ExactReceiptRetirementError(
            f"unsafe receipt-retirement namespace entry: {name}"
        )
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0),
            dir_fd=directory_fd,
        )
    except OSError as exc:
        raise ExactReceiptRetirementError(
            f"cannot open receipt-retirement namespace entry: {name}"
        ) from exc
    try:
        opened = FileIdentity.from_stat(os.fstat(descriptor))
        chunks: list[bytes] = []
        observed = 0
        while True:
            chunk = os.read(descriptor, min(65536, maximum + 1 - observed))
            if not chunk:
                break
            chunks.append(chunk)
            observed += len(chunk)
            if observed > maximum:
                raise ExactReceiptRetirementError(
                    f"receipt-retirement namespace entry is oversized: {name}"
                )
        after_fd = FileIdentity.from_stat(os.fstat(descriptor))
    finally:
        os.close(descriptor)
    try:
        after_path = FileIdentity.from_stat(
            os.stat(name, dir_fd=directory_fd, follow_symlinks=False)
        )
    except FileNotFoundError as exc:
        raise ExactReceiptRetirementError(
            f"receipt-retirement namespace changed while reading: {name}"
        ) from exc
    if not (
        _same_identity(before, opened)
        and _same_identity(opened, after_fd)
        and _same_identity(after_fd, after_path)
    ):
        raise ExactReceiptRetirementError(
            f"receipt-retirement namespace changed while reading: {name}"
        )
    data = b"".join(chunks)
    if len(data) != before.size:
        raise ExactReceiptRetirementError(
            f"receipt-retirement entry size changed while reading: {name}"
        )
    return StableEntry(data=data, identity=before)


def _strict_object(data: bytes, *, label: str) -> dict[str, Any]:
    def pairs(values: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in values:
            if key in result:
                raise ValueError(f"duplicate object name: {key}")
            result[key] = value
        return result

    def invalid_constant(value: str) -> None:
        raise ValueError(f"invalid numeric constant: {value}")

    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=pairs,
            parse_constant=invalid_constant,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise ExactReceiptRetirementError(f"invalid {label} JSON") from exc
    if not isinstance(value, dict):
        raise ExactReceiptRetirementError(f"invalid {label} document")
    return value


def _marker_document(
    *,
    source_name: str,
    expectation: RetirementExpectation,
    phase: str,
) -> dict[str, Any]:
    return {
        "document_kind": RETIREMENT_DOCUMENT_KIND,
        "expected_sha256": expectation.sha256,
        "expected_size": expectation.size,
        "phase": phase,
        "schema_version": RETIREMENT_SCHEMA_VERSION,
        "source_basename": source_name,
        "source_identity": expectation.identity.to_document(),
    }


def _canonical_json(value: Any) -> bytes:
    try:
        return (
            json.dumps(
                value,
                ensure_ascii=False,
                allow_nan=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ExactReceiptRetirementError(
            "receipt retirement marker is not canonical JSON"
        ) from exc


def _ledger_document(
    *,
    source_name: str,
    sequence: int,
    previous_record_sha256: str,
    expectation: RetirementExpectation | None,
    commit_sha256: str | None,
) -> dict[str, Any]:
    state = "idle" if expectation is None else "completed"
    binding = None
    if expectation is not None:
        binding = {
            "expected_sha256": expectation.sha256,
            "expected_size": expectation.size,
            "source_identity": expectation.identity.to_document(),
        }
    return {
        "commit_sha256": commit_sha256,
        "document_kind": RETIREMENT_LEDGER_DOCUMENT_KIND,
        "previous_record_sha256": previous_record_sha256,
        "schema_version": RETIREMENT_LEDGER_SCHEMA_VERSION,
        "sequence": sequence,
        "source_basename": source_name,
        "source_binding": binding,
        "state": state,
    }


def _parse_ledger_record(
    entry: StableEntry,
    *,
    source_name: str,
    maximum: int,
) -> RetirementLedgerRecord:
    if entry.identity.mode != RETIREMENT_MODE:
        raise ExactReceiptRetirementError("retirement ledger mode is unsafe")
    value = _strict_object(entry.data, label="retirement ledger")
    if frozenset(value) != _LEDGER_KEYS:
        raise ExactReceiptRetirementError("retirement ledger keys differ")
    sequence = value.get("sequence")
    state = value.get("state")
    previous = value.get("previous_record_sha256")
    commit_sha256 = value.get("commit_sha256")
    if (
        value.get("document_kind") != RETIREMENT_LEDGER_DOCUMENT_KIND
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != RETIREMENT_LEDGER_SCHEMA_VERSION
        or value.get("source_basename") != source_name
        or type(sequence) is not int
        or sequence < 0
        or state not in {"idle", "completed"}
        or not isinstance(previous, str)
        or re.fullmatch(r"[0-9a-f]{64}", previous) is None
        or _canonical_json(value) != entry.data
    ):
        raise ExactReceiptRetirementError(
            "retirement ledger is not a strict canonical record"
        )
    binding = value.get("source_binding")
    if state == "idle":
        if (
            sequence != 0
            or previous != _GENESIS_PREVIOUS_RECORD_SHA256
            or binding is not None
            or commit_sha256 is not None
        ):
            raise ExactReceiptRetirementError("retirement ledger idle record is invalid")
        return RetirementLedgerRecord(
            sequence=sequence,
            state=state,
            previous_record_sha256=previous,
            expected_sha256="",
            expected_size=0,
            source_identity=None,
            commit_sha256="",
            data=entry.data,
        )
    if (
        not isinstance(binding, dict)
        or frozenset(binding) != _LEDGER_BINDING_KEYS
        or not isinstance(commit_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", commit_sha256) is None
        or sequence <= 0
    ):
        raise ExactReceiptRetirementError(
            "retirement ledger completed record is invalid"
        )
    expected_sha256 = binding.get("expected_sha256")
    expected_size = binding.get("expected_size")
    identity = binding.get("source_identity")
    if (
        not isinstance(expected_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None
        or type(expected_size) is not int
        or expected_size <= 0
        or expected_size > maximum
        or not isinstance(identity, dict)
        or frozenset(identity) != _IDENTITY_KEYS
        or any(type(identity[key]) is not int for key in _IDENTITY_KEYS)
    ):
        raise ExactReceiptRetirementError(
            "retirement ledger source binding is invalid"
        )
    source_identity = FileIdentity(
        device=identity["device"],
        inode=identity["inode"],
        ctime_ns=identity["ctime_ns"],
        mtime_ns=identity["mtime_ns"],
        size=identity["size"],
        mode=identity["mode"],
        owner_uid=identity["owner_uid"],
        link_count=identity["link_count"],
    )
    if (
        source_identity.device <= 0
        or source_identity.inode <= 0
        or source_identity.ctime_ns < 0
        or source_identity.mtime_ns < 0
        or source_identity.size != expected_size
        or source_identity.mode != RETIREMENT_MODE
        or source_identity.owner_uid != os.geteuid()
        or source_identity.link_count != 1
    ):
        raise ExactReceiptRetirementError(
            "retirement ledger source identity is unsafe"
        )
    return RetirementLedgerRecord(
        sequence=sequence,
        state=state,
        previous_record_sha256=previous,
        expected_sha256=expected_sha256,
        expected_size=expected_size,
        source_identity=source_identity,
        commit_sha256=commit_sha256,
        data=entry.data,
    )


def _ledger_record_is_successor(
    predecessor: RetirementLedgerRecord,
    successor: RetirementLedgerRecord,
) -> bool:
    return bool(
        successor.state == "completed"
        and successor.sequence == predecessor.sequence + 1
        and successor.previous_record_sha256
        == hashlib.sha256(predecessor.data).hexdigest()
    )


def _parse_marker_unbound(
    entry: StableEntry | None,
    *,
    source_name: str,
    phase: str,
    expected_mode: int,
    maximum: int,
) -> tuple[dict[str, Any], RetirementExpectation] | None:
    if entry is None:
        return None
    if entry.identity.mode != RETIREMENT_MODE:
        raise ExactReceiptRetirementError("receipt retirement marker mode is unsafe")
    value = _strict_object(entry.data, label="receipt retirement marker")
    if frozenset(value) != _MARKER_KEYS:
        raise ExactReceiptRetirementError("receipt retirement marker keys differ")
    identity = value.get("source_identity")
    if not isinstance(identity, dict) or frozenset(identity) != _IDENTITY_KEYS:
        raise ExactReceiptRetirementError(
            "receipt retirement marker source identity is invalid"
        )
    if any(type(identity[key]) is not int for key in _IDENTITY_KEYS):
        raise ExactReceiptRetirementError(
            "receipt retirement marker source identity types are invalid"
        )
    marker_hash = value.get("expected_sha256")
    marker_size = value.get("expected_size")
    if (
        value.get("document_kind") != RETIREMENT_DOCUMENT_KIND
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != RETIREMENT_SCHEMA_VERSION
        or value.get("phase") != phase
        or value.get("source_basename") != source_name
        or not isinstance(marker_hash, str)
        or re.fullmatch(r"[0-9a-f]{64}", marker_hash) is None
        or type(marker_size) is not int
        or marker_size <= 0
        or marker_size > maximum
        or _canonical_json(value) != entry.data
    ):
        raise ExactReceiptRetirementError(
            "receipt retirement marker is not a strict canonical marker"
        )
    source_identity = FileIdentity(
        device=identity["device"],
        inode=identity["inode"],
        ctime_ns=identity["ctime_ns"],
        mtime_ns=identity["mtime_ns"],
        size=identity["size"],
        mode=identity["mode"],
        owner_uid=identity["owner_uid"],
        link_count=identity["link_count"],
    )
    if (
        source_identity.device <= 0
        or source_identity.inode <= 0
        or source_identity.ctime_ns < 0
        or source_identity.mtime_ns < 0
        or source_identity.size != marker_size
        or source_identity.mode != expected_mode
        or source_identity.owner_uid != os.geteuid()
        or source_identity.link_count != 1
    ):
        raise ExactReceiptRetirementError(
            "receipt retirement marker source identity is unsafe"
        )
    return value, RetirementExpectation(
        sha256=marker_hash,
        size=marker_size,
        identity=source_identity,
    )


def _parse_marker(
    entry: StableEntry | None,
    *,
    source_name: str,
    expected: bytes,
    phase: str,
    expected_mode: int = RETIREMENT_MODE,
    maximum: int = DEFAULT_MAXIMUM_RECEIPT_BYTES,
) -> tuple[dict[str, Any], FileIdentity] | None:
    """Compatibility wrapper which additionally binds exact caller bytes."""

    parsed = _parse_marker_unbound(
        entry,
        source_name=source_name,
        phase=phase,
        expected_mode=expected_mode,
        maximum=maximum,
    )
    if parsed is None:
        return None
    value, expectation = parsed
    if (
        expectation.sha256 != hashlib.sha256(expected).hexdigest()
        or expectation.size != len(expected)
    ):
        raise ExactReceiptRetirementError(
            "receipt retirement marker does not bind the expected receipt"
        )
    return value, expectation.identity


def _stage_new(
    directory_fd: int,
    name: str,
    data: bytes,
) -> StableEntry:
    """Create and durably fsync a fixed staging marker, never a torn final."""

    descriptor = os.open(
        name,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        RETIREMENT_MODE,
        dir_fd=directory_fd,
    )
    try:
        view = memoryview(data)
        written = 0
        while written < len(view):
            count = os.write(descriptor, view[written:])
            if count <= 0:
                raise OSError("short write while publishing retirement marker")
            written += count
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(directory_fd)
    staged = _read_stable_entry(
        directory_fd,
        name,
        maximum=DEFAULT_MAXIMUM_RECEIPT_BYTES,
    )
    if staged is None or staged.data != data or staged.identity.mode != RETIREMENT_MODE:
        raise ExactReceiptRetirementError(
            "staged receipt retirement marker differs after fsync"
        )
    return staged


def _rename_noreplace(
    directory_fd: int,
    source_name: str,
    destination_name: str,
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise ExactReceiptRetirementError("renameat2(RENAME_NOREPLACE) is required")
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    if (
        renameat2(
            directory_fd,
            os.fsencode(source_name),
            directory_fd,
            os.fsencode(destination_name),
            _RENAME_NOREPLACE,
        )
        != 0
    ):
        error = ctypes.get_errno()
        raise OSError(
            error,
            os.strerror(error),
            source_name,
            destination_name,
        )


def _rename_exchange(
    directory_fd: int,
    first_name: str,
    second_name: str,
) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise ExactReceiptRetirementError("renameat2(RENAME_EXCHANGE) is required")
    renameat2.argtypes = [
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_int,
        ctypes.c_char_p,
        ctypes.c_uint,
    ]
    renameat2.restype = ctypes.c_int
    if (
        renameat2(
            directory_fd,
            os.fsencode(first_name),
            directory_fd,
            os.fsencode(second_name),
            _RENAME_EXCHANGE,
        )
        != 0
    ):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), first_name, second_name)


def _publish_staged(
    directory_fd: int,
    *,
    staging_name: str,
    final_name: str,
    expected_entry: StableEntry,
    maximum: int,
) -> None:
    """Atomically publish one exact staged marker without replacement."""

    current = _read_stable_entry(directory_fd, staging_name, maximum=maximum)
    if current != expected_entry:
        raise ExactReceiptRetirementError(
            "receipt retirement staging marker changed before publication"
        )
    _rename_noreplace(directory_fd, staging_name, final_name)
    try:
        _fsync_directory(directory_fd)
    except OSError:
        # The visible final marker remains a conservative recovery barrier.
        raise
    published = _read_stable_entry(directory_fd, final_name, maximum=maximum)
    if (
        published is None
        or published.data != expected_entry.data
        or not _same_object_after_rename(expected_entry.identity, published.identity)
    ):
        # Preserve a raced replacement.  If possible, restore its original
        # staging name so the unexpected entry remains inventoried and blocking.
        try:
            if _read_stable_entry(directory_fd, staging_name, maximum=maximum) is None:
                _rename_noreplace(directory_fd, final_name, staging_name)
                _fsync_directory(directory_fd)
        except Exception:
            pass
        raise ExactReceiptRetirementError(
            "receipt retirement staging marker changed during publication"
        )


def _move_exact_to_cleanup(
    directory_fd: int,
    *,
    source_name: str,
    cleanup_name: str,
    expected_entry: StableEntry,
    maximum: int,
) -> None:
    current = _read_stable_entry(directory_fd, source_name, maximum=maximum)
    if current is None or current != expected_entry:
        raise ExactReceiptRetirementError(
            "receipt retirement source changed before its no-replace move"
        )
    _rename_noreplace(directory_fd, source_name, cleanup_name)
    try:
        _fsync_directory(directory_fd)
    except OSError:
        # The visible cleanup entry remains the fail-closed recovery point.
        raise
    moved = _read_stable_entry(directory_fd, cleanup_name, maximum=maximum)
    if (
        moved is None
        or moved.data != expected_entry.data
        or not _same_object_after_rename(expected_entry.identity, moved.identity)
    ):
        # Preserve a raced replacement.  Restore its pathname where possible;
        # never unlink it merely because it occupied the expected namespace.
        try:
            if _read_stable_entry(directory_fd, source_name, maximum=maximum) is None:
                _rename_noreplace(directory_fd, cleanup_name, source_name)
                _fsync_directory(directory_fd)
        except Exception:
            pass
        raise ExactReceiptRetirementError(
            "receipt retirement source changed during its no-replace move"
        )


def _unlink_exact_cleanup(
    directory_fd: int,
    *,
    cleanup_name: str,
    expected_entry: StableEntry,
    maximum: int,
) -> None:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise ExactReceiptRetirementError("O_NOFOLLOW is required")
    try:
        descriptor = os.open(
            cleanup_name,
            os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0),
            dir_fd=directory_fd,
        )
    except OSError as exc:
        raise ExactReceiptRetirementError(
            "receipt retirement cleanup entry cannot be opened for removal"
        ) from exc
    try:
        opened = FileIdentity.from_stat(os.fstat(descriptor))
        chunks: list[bytes] = []
        observed = 0
        while True:
            chunk = os.read(descriptor, min(65536, maximum + 1 - observed))
            if not chunk:
                break
            chunks.append(chunk)
            observed += len(chunk)
            if observed > maximum:
                raise ExactReceiptRetirementError(
                    "receipt retirement cleanup entry is oversized"
                )
        before_unlink = FileIdentity.from_stat(os.fstat(descriptor))
        try:
            path_identity = FileIdentity.from_stat(
                os.stat(
                    cleanup_name,
                    dir_fd=directory_fd,
                    follow_symlinks=False,
                )
            )
        except FileNotFoundError as exc:
            raise ExactReceiptRetirementError(
                "receipt retirement cleanup entry vanished before removal"
            ) from exc
        if (
            opened != expected_entry.identity
            or before_unlink != expected_entry.identity
            or path_identity != expected_entry.identity
            or b"".join(chunks) != expected_entry.data
        ):
            raise ExactReceiptRetirementError(
                "receipt retirement cleanup entry changed before removal"
            )
        os.unlink(cleanup_name, dir_fd=directory_fd)
        _fsync_directory(directory_fd)
        retired = os.fstat(descriptor)
        retired_chunks: list[bytes] = []
        retired_offset = 0
        while retired_offset <= maximum:
            chunk = os.pread(
                descriptor,
                min(65536, maximum + 1 - retired_offset),
                retired_offset,
            )
            if not chunk:
                break
            retired_chunks.append(chunk)
            retired_offset += len(chunk)
            if retired_offset > maximum:
                break
        if (
            int(retired.st_dev) != expected_entry.identity.device
            or int(retired.st_ino) != expected_entry.identity.inode
            or int(retired.st_nlink) != 0
            or int(retired.st_size) != expected_entry.identity.size
            or stat.S_IMODE(retired.st_mode) != expected_entry.identity.mode
            or int(retired.st_uid) != expected_entry.identity.owner_uid
            or int(retired.st_mtime_ns) != expected_entry.identity.mtime_ns
            or b"".join(retired_chunks) != expected_entry.data
        ):
            raise ExactReceiptRetirementError(
                "receipt retirement removed or changed a different namespace generation"
            )
        try:
            os.stat(
                cleanup_name,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            pass
        else:
            raise ExactReceiptRetirementError(
                "receipt retirement cleanup pathname reappeared during removal"
            )
    finally:
        os.close(descriptor)


def _read_ledger_pair(
    directory_fd: int,
    *,
    source_name: str,
    ledger_name: str,
    exchange_name: str,
    maximum: int,
) -> tuple[
    StableEntry,
    RetirementLedgerRecord,
    StableEntry | None,
    RetirementLedgerRecord | None,
    str,
]:
    ledger_entry = _read_stable_entry(directory_fd, ledger_name, maximum=maximum)
    if ledger_entry is None:
        raise ExactReceiptRetirementError("required retirement ledger is missing")
    ledger = _parse_ledger_record(
        ledger_entry,
        source_name=source_name,
        maximum=maximum,
    )
    exchange_entry = _read_stable_entry(
        directory_fd,
        exchange_name,
        maximum=maximum,
    )
    if exchange_entry is None:
        return ledger_entry, ledger, None, None, "stable"
    exchange = _parse_ledger_record(
        exchange_entry,
        source_name=source_name,
        maximum=maximum,
    )
    if _ledger_record_is_successor(ledger, exchange):
        relation = "exchange_staged"
    elif _ledger_record_is_successor(exchange, ledger):
        relation = "exchange_committed"
    else:
        raise ExactReceiptRetirementError(
            "retirement ledger exchange generations do not form one exact transition"
        )
    return ledger_entry, ledger, exchange_entry, exchange, relation


def _recover_ledger_exchange(
    directory_fd: int,
    *,
    source_name: str,
    ledger_name: str,
    exchange_name: str,
    maximum: int,
) -> RetirementLedgerRecord:
    ledger_entry, ledger, exchange_entry, exchange, relation = _read_ledger_pair(
        directory_fd,
        source_name=source_name,
        ledger_name=ledger_name,
        exchange_name=exchange_name,
        maximum=maximum,
    )
    if relation == "stable":
        return ledger
    assert exchange_entry is not None and exchange is not None
    if relation == "exchange_staged":
        _rename_exchange(directory_fd, ledger_name, exchange_name)
        _fsync_directory(directory_fd)
        published_entry = _read_stable_entry(
            directory_fd,
            ledger_name,
            maximum=maximum,
        )
        displaced_entry = _read_stable_entry(
            directory_fd,
            exchange_name,
            maximum=maximum,
        )
        if (
            published_entry is None
            or displaced_entry is None
            or published_entry.data != exchange_entry.data
            or displaced_entry.data != ledger_entry.data
        ):
            raise ExactReceiptRetirementError(
                "retirement ledger atomic exchange changed either generation"
            )
        ledger_entry, ledger = published_entry, exchange
        exchange_entry = displaced_entry
    _unlink_exact_cleanup(
        directory_fd,
        cleanup_name=exchange_name,
        expected_entry=exchange_entry,
        maximum=maximum,
    )
    final_entry = _read_stable_entry(directory_fd, ledger_name, maximum=maximum)
    if final_entry is None or final_entry.data != ledger_entry.data:
        raise ExactReceiptRetirementError(
            "retirement ledger changed while retiring its displaced generation"
        )
    return _parse_ledger_record(
        final_entry,
        source_name=source_name,
        maximum=maximum,
    )


def _ledger_matches_completion(
    record: RetirementLedgerRecord,
    *,
    expectation: RetirementExpectation,
    commit_sha256: str,
) -> bool:
    return bool(
        record.state == "completed"
        and record.expected_sha256 == expectation.sha256
        and record.expected_size == expectation.size
        and record.source_identity == expectation.identity
        and record.commit_sha256 == commit_sha256
    )


def _complete_retirement_ledger(
    source: Path,
    *,
    expectation: RetirementExpectation,
    commit_entry: StableEntry,
    directory_fd: int,
    maximum: int,
) -> RetirementLedgerRecord:
    ledger_path, exchange_path = retirement_ledger_paths(source)
    current = _recover_ledger_exchange(
        directory_fd,
        source_name=source.name,
        ledger_name=ledger_path.name,
        exchange_name=exchange_path.name,
        maximum=maximum,
    )
    commit_sha256 = hashlib.sha256(commit_entry.data).hexdigest()
    if _ledger_matches_completion(
        current,
        expectation=expectation,
        commit_sha256=commit_sha256,
    ):
        return current
    next_bytes = _canonical_json(
        _ledger_document(
            source_name=source.name,
            sequence=current.sequence + 1,
            previous_record_sha256=hashlib.sha256(current.data).hexdigest(),
            expectation=expectation,
            commit_sha256=commit_sha256,
        )
    )
    staged = _stage_new(directory_fd, exchange_path.name, next_bytes)
    next_record = _parse_ledger_record(
        staged,
        source_name=source.name,
        maximum=maximum,
    )
    if not _ledger_record_is_successor(current, next_record):
        raise ExactReceiptRetirementError(
            "retirement ledger staged generation is not the exact successor"
        )
    return _recover_ledger_exchange(
        directory_fd,
        source_name=source.name,
        ledger_name=ledger_path.name,
        exchange_name=exchange_path.name,
        maximum=maximum,
    )


def inspect_retirement_ledger(
    source_path: Path,
    *,
    maximum_receipt_bytes: int = DEFAULT_MAXIMUM_RECEIPT_BYTES,
) -> RetirementLedgerInspection:
    """Inspect the mandatory permanent ledger without changing it."""

    source = _absolute_path(source_path)
    ledger_path, exchange_path = retirement_ledger_paths(source)

    def result(
        *,
        valid: bool,
        blocking: bool,
        state: str,
        sequence: int,
        detail: str,
        record_sha256: str = "",
    ) -> RetirementLedgerInspection:
        return RetirementLedgerInspection(
            valid=valid,
            blocking=blocking,
            state=state,
            sequence=sequence,
            detail=detail,
            ledger_path=os.fspath(ledger_path),
            exchange_path=os.fspath(exchange_path),
            record_sha256=record_sha256,
        )

    try:
        directory_fd = _open_directory(source.parent)
    except ExactReceiptRetirementError as exc:
        return result(
            valid=False,
            blocking=True,
            state="invalid",
            sequence=-1,
            detail=str(exc),
        )
    try:
        try:
            _entry, ledger, _exchange_entry, _exchange, relation = _read_ledger_pair(
                directory_fd,
                source_name=source.name,
                ledger_name=ledger_path.name,
                exchange_name=exchange_path.name,
                maximum=maximum_receipt_bytes,
            )
        except ExactReceiptRetirementError as exc:
            return result(
                valid=False,
                blocking=True,
                state="invalid",
                sequence=-1,
                detail=str(exc),
            )
        return result(
            valid=True,
            blocking=relation != "stable",
            state=ledger.state if relation == "stable" else relation,
            sequence=ledger.sequence,
            detail=(
                "retirement ledger is stable"
                if relation == "stable"
                else "retirement ledger has one recoverable atomic exchange"
            ),
            record_sha256=hashlib.sha256(ledger.data).hexdigest(),
        )
    finally:
        os.close(directory_fd)


def recover_retirement_ledger_exchange_if_present(
    source_path: Path,
    *,
    mutation_authority: TransactionMutationAuthority | None = None,
    maximum_receipt_bytes: int = DEFAULT_MAXIMUM_RECEIPT_BYTES,
) -> bool:
    """Finish one exact crash-left atomic ledger exchange, if present.

    This is deliberately narrower than receipt-retirement recovery.  It never
    creates a missing ledger and it never infers a transition: the current and
    staged records must form the exact monotonic predecessor/successor pair
    accepted by :func:`inspect_retirement_ledger`.  Callers must already own
    the installation mutation boundary.
    """

    require_transaction_mutation_authority(
        mutation_authority,
        operation="retirement-ledger atomic exchange recovery",
    )
    source = _absolute_path(source_path)
    ledger_path, exchange_path = retirement_ledger_paths(source)
    directory_fd = _open_directory(source.parent)
    changed = False
    try:
        _ledger_entry, _ledger, _exchange_entry, _exchange, relation = (
            _read_ledger_pair(
                directory_fd,
                source_name=source.name,
                ledger_name=ledger_path.name,
                exchange_name=exchange_path.name,
                maximum=maximum_receipt_bytes,
            )
        )
        if relation != "stable":
            _recover_ledger_exchange(
                directory_fd,
                source_name=source.name,
                ledger_name=ledger_path.name,
                exchange_name=exchange_path.name,
                maximum=maximum_receipt_bytes,
            )
            changed = True
    finally:
        os.close(directory_fd)
    inspection = inspect_retirement_ledger(
        source,
        maximum_receipt_bytes=maximum_receipt_bytes,
    )
    if not inspection.valid or inspection.blocking:
        raise ExactReceiptRetirementError(
            "retirement ledger exchange recovery did not reach a stable generation"
        )
    return changed


def retirement_ledger_is_blocking(source_path: Path) -> bool:
    """Fail closed for a missing, unsafe, torn, or exchanging ledger."""

    inspection = inspect_retirement_ledger(source_path)
    return not inspection.valid or inspection.blocking


def retirement_ledger_inventory_sha256(
    receipt_paths: list[Path] | tuple[Path, ...],
) -> str:
    """Hash a stable basename-bound inventory for activation attestation."""

    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for supplied in receipt_paths:
        source = _absolute_path(supplied)
        ledger_path = retirement_ledger_path_for_receipt(source)
        key = (source.name, ledger_path.name)
        if key in seen:
            raise ExactReceiptRetirementError(
                "retirement ledger inventory contains a duplicate receipt"
            )
        seen.add(key)
        inspection = inspect_retirement_ledger(source)
        if not inspection.valid or inspection.blocking:
            raise ExactReceiptRetirementError(
                f"retirement ledger inventory is not stable: {source.name}"
            )
        rows.append(
            {
                "ledger_basename": ledger_path.name,
                "record_sha256": inspection.record_sha256,
                "sequence": inspection.sequence,
                "source_basename": source.name,
                "state": inspection.state,
            }
        )
    document = {
        "document_kind": "mrsMThatcher_retirement_ledger_inventory",
        "ledgers": sorted(rows, key=lambda row: row["source_basename"]),
        "schema_version": 1,
    }
    return hashlib.sha256(_canonical_json(document)).hexdigest()


def retirement_ledger_contract_sha256(
    receipt_paths: list[Path] | tuple[Path, ...],
) -> str:
    """Hash the immutable required-ledger namespace and schema contract.

    Unlike :func:`retirement_ledger_inventory_sha256`, this value deliberately
    excludes mutable record bytes, sequence numbers and states.  It is suitable
    for an immutable activation audit: the audit binds which ledger and
    exchange pathnames are mandatory, while runtime inspection independently
    validates each current generation after every completed transaction.
    """

    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for supplied in receipt_paths:
        source = _absolute_path(supplied)
        ledger_path, exchange_path = retirement_ledger_paths(source)
        key = (source.name, ledger_path.name, exchange_path.name)
        if key in seen:
            raise ExactReceiptRetirementError(
                "retirement ledger contract contains a duplicate receipt"
            )
        seen.add(key)
        rows.append(
            {
                "exchange_basename": exchange_path.name,
                "ledger_basename": ledger_path.name,
                "source_basename": source.name,
            }
        )
    document = {
        "document_kind": "mrsMThatcher_retirement_ledger_contract",
        "ledger_document_kind": RETIREMENT_LEDGER_DOCUMENT_KIND,
        "ledger_schema_version": RETIREMENT_LEDGER_SCHEMA_VERSION,
        "ledgers": sorted(rows, key=lambda row: row["source_basename"]),
        "schema_version": 1,
    }
    return hashlib.sha256(_canonical_json(document)).hexdigest()


def initialise_retirement_ledger(
    source_path: Path,
    *,
    mutation_authority: TransactionMutationAuthority | None = None,
) -> RetirementLedgerInspection:
    """Create the permanent idle ledger before installation activation.

    Established ledger-aware installations must never call this function to
    infer a missing ledger.  Their initializer/migrator must create every idle
    ledger first and only then publish the separately audited activation
    generation which requires them.
    """

    require_transaction_mutation_authority(
        mutation_authority,
        operation="permanent receipt-retirement ledger initialisation",
    )
    source = _absolute_path(source_path)
    ledger_path, exchange_path = retirement_ledger_paths(source)
    genesis = _canonical_json(
        _ledger_document(
            source_name=source.name,
            sequence=0,
            previous_record_sha256=_GENESIS_PREVIOUS_RECORD_SHA256,
            expectation=None,
            commit_sha256=None,
        )
    )
    directory_fd = _open_directory(source.parent)
    try:
        ledger = _read_stable_entry(
            directory_fd,
            ledger_path.name,
            maximum=DEFAULT_MAXIMUM_RECEIPT_BYTES,
        )
        exchange = _read_stable_entry(
            directory_fd,
            exchange_path.name,
            maximum=DEFAULT_MAXIMUM_RECEIPT_BYTES,
        )
        if ledger is None:
            if exchange is None:
                exchange = _stage_new(directory_fd, exchange_path.name, genesis)
            if exchange.data != genesis:
                raise ExactReceiptRetirementError(
                    "missing retirement ledger has a non-genesis exchange entry"
                )
            _rename_noreplace(directory_fd, exchange_path.name, ledger_path.name)
            _fsync_directory(directory_fd)
        else:
            _parse_ledger_record(
                ledger,
                source_name=source.name,
                maximum=DEFAULT_MAXIMUM_RECEIPT_BYTES,
            )
            if exchange is not None:
                _recover_ledger_exchange(
                    directory_fd,
                    source_name=source.name,
                    ledger_name=ledger_path.name,
                    exchange_name=exchange_path.name,
                    maximum=DEFAULT_MAXIMUM_RECEIPT_BYTES,
                )
    finally:
        os.close(directory_fd)
    inspection = inspect_retirement_ledger(source)
    if not inspection.valid or inspection.blocking:
        raise ExactReceiptRetirementError(inspection.detail)
    return inspection


# American spelling is retained as an explicit API alias for deployment tools.
initialize_retirement_ledger = initialise_retirement_ledger


def _entry_matches_expectation(
    entry: StableEntry | None,
    *,
    expectation: RetirementExpectation,
    expected_mode: int,
    after_rename: bool = False,
) -> bool:
    if entry is None:
        return False
    identity_matches = (
        _same_object_after_rename(expectation.identity, entry.identity)
        if after_rename
        else expectation.identity == entry.identity
    )
    return bool(
        entry.identity.mode == expected_mode
        and entry.identity.size == expectation.size
        and len(entry.data) == expectation.size
        and hashlib.sha256(entry.data).hexdigest() == expectation.sha256
        and (expectation.data is None or entry.data == expectation.data)
        and identity_matches
    )


def _expectations_agree(
    first: RetirementExpectation,
    second: RetirementExpectation,
) -> bool:
    return (
        first.sha256 == second.sha256
        and first.size == second.size
        and first.identity == second.identity
    )


def _inspection(
    *,
    source: Path,
    expected: bytes | None,
    directory_fd: int,
    expected_mode: int,
    maximum: int,
    independently_authorised_absence: bool,
) -> tuple[
    ReceiptRetirementInspection,
    dict[str, StableEntry | None],
    dict[str, bytes],
    RetirementExpectation | None,
]:
    paths = _retirement_paths(source)
    names = {
        "source": source.name,
        "guard": paths.guard.name,
        "commit": paths.commit.name,
        "cleanup": paths.cleanup.name,
        "guard_staging": paths.guard_staging.name,
        "commit_staging": paths.commit_staging.name,
    }
    entries = {
        key: _read_stable_entry(directory_fd, name, maximum=maximum)
        for key, name in names.items()
    }
    expected_hash = hashlib.sha256(expected).hexdigest() if expected is not None else ""

    def result(
        phase: str,
        valid: bool,
        blocking: bool,
        detail: str,
        expectation: RetirementExpectation | None = None,
    ):
        return ReceiptRetirementInspection(
            phase=phase,
            valid=valid,
            blocking=blocking,
            detail=detail,
            expected_sha256=(expectation.sha256 if expectation is not None else expected_hash),
            source_path=os.fspath(source),
            guard_path=os.fspath(paths.guard),
            commit_path=os.fspath(paths.commit),
            cleanup_path=os.fspath(paths.cleanup),
            guard_staging_path=os.fspath(paths.guard_staging),
            commit_staging_path=os.fspath(paths.commit_staging),
        )

    ledger_path, exchange_path = retirement_ledger_paths(source)
    try:
        _ledger_entry, ledger, _exchange_entry, _exchange, ledger_relation = (
            _read_ledger_pair(
                directory_fd,
                source_name=source.name,
                ledger_name=ledger_path.name,
                exchange_name=exchange_path.name,
                maximum=maximum,
            )
        )
    except ExactReceiptRetirementError as exc:
        return result("invalid", False, True, str(exc)), entries, {}, None
    if ledger_relation != "stable":
        return (
            result(
                "ledger_exchange_pending",
                False,
                True,
                "retirement ledger has a recoverable atomic exchange",
            ),
            entries,
            {},
            None,
        )

    def ledger_expectation() -> RetirementExpectation:
        if ledger.state != "completed" or ledger.source_identity is None:
            raise ExactReceiptRetirementError(
                "retirement ledger does not contain a completed source binding"
            )
        if expected is not None and (
            ledger.expected_sha256 != expected_hash
            or ledger.expected_size != len(expected)
        ):
            raise ExactReceiptRetirementError(
                "completed retirement ledger does not bind the expected receipt"
            )
        return RetirementExpectation(
            sha256=ledger.expected_sha256,
            size=ledger.expected_size,
            identity=ledger.source_identity,
            data=expected,
        )

    source_entry = entries["source"]
    present = {key for key, entry in entries.items() if entry is not None}
    if not present:
        if ledger.state == "completed":
            try:
                expectation = ledger_expectation()
            except ExactReceiptRetirementError as exc:
                return result("invalid", False, True, str(exc)), entries, {}, None
            return (
                result(
                    "ledger_completed",
                    True,
                    False,
                    "permanent ledger proves exact receipt retirement",
                    expectation,
                ),
                entries,
                {},
                expectation,
            )
        if independently_authorised_absence:
            return (
                result("complete", True, False, "receipt absence was independently authorised"),
                entries,
                {},
                None,
            )
        return (
            result(
                "absent_unproven",
                False,
                True,
                "receipt namespace is absent without independent idempotence authority",
            ),
            entries,
            {},
            None,
        )

    if present == {"source"}:
        if (
            expected is not None
            and source_entry is not None
            and source_entry.data == expected
            and source_entry.identity.mode == expected_mode
        ):
            expectation = RetirementExpectation(
                sha256=expected_hash,
                size=len(expected),
                identity=source_entry.identity,
                data=expected,
            )
            return (
                result("fresh", True, True, "exact source receipt is present", expectation),
                entries,
                {},
                expectation,
            )
        return (
            result(
                "fresh",
                False,
                True,
                "fresh receipt requires independently supplied exact bytes",
            ),
            entries,
            {},
            None,
        )

    marker_bytes: dict[str, bytes] = {}

    def parse(key: str, phase: str) -> RetirementExpectation:
        entry = entries[key]
        parsed = _parse_marker_unbound(
            entry,
            source_name=source.name,
            phase=phase,
            expected_mode=expected_mode,
            maximum=maximum,
        )
        assert parsed is not None
        _value, expectation = parsed
        if expected is not None and (
            expectation.sha256 != expected_hash or expectation.size != len(expected)
        ):
            raise ExactReceiptRetirementError(
                "receipt retirement marker does not bind the expected receipt"
            )
        marker_bytes[phase] = entry.data
        return RetirementExpectation(
            sha256=expectation.sha256,
            size=expectation.size,
            identity=expectation.identity,
            data=expected,
        )

    def agree(*expectations: RetirementExpectation) -> RetirementExpectation:
        first = expectations[0]
        if any(not _expectations_agree(first, other) for other in expectations[1:]):
            raise ExactReceiptRetirementError(
                "retirement markers bind different source facts"
            )
        return first

    def observed(
        expectation: RetirementExpectation,
        entry: StableEntry | None,
        *,
        after_rename: bool,
    ) -> RetirementExpectation:
        if not _entry_matches_expectation(
            entry,
            expectation=expectation,
            expected_mode=expected_mode,
            after_rename=after_rename,
        ):
            raise ExactReceiptRetirementError(
                "receipt retirement source bytes or identity changed"
            )
        assert entry is not None
        return RetirementExpectation(
            sha256=expectation.sha256,
            size=expectation.size,
            identity=expectation.identity,
            data=entry.data,
        )

    try:
        if present == {"source", "guard_staging"}:
            expectation = observed(
                parse("guard_staging", "prepared"),
                source_entry,
                after_rename=False,
            )
            return (
                result(
                    "prepare_marker_staged",
                    True,
                    True,
                    "prepare marker is durably staged",
                    expectation,
                ),
                entries,
                marker_bytes,
                expectation,
            )
        if present == {"source", "guard"}:
            expectation = observed(
                parse("guard", "prepared"),
                source_entry,
                after_rename=False,
            )
            return (
                result("prepared", True, True, "durable prepare guard is present", expectation),
                entries,
                marker_bytes,
                expectation,
            )
        if present == {"guard", "cleanup"}:
            expectation = observed(
                parse("guard", "prepared"),
                entries["cleanup"],
                after_rename=True,
            )
            return (
                result(
                    "source_moved",
                    True,
                    True,
                    "exact source is held by cleanup barrier",
                    expectation,
                ),
                entries,
                marker_bytes,
                expectation,
            )
        if present == {"guard", "commit_staging", "cleanup"}:
            expectation = agree(
                parse("guard", "prepared"),
                parse("commit_staging", "source_retired"),
            )
            expectation = observed(
                expectation,
                entries["cleanup"],
                after_rename=True,
            )
            return (
                result(
                    "commit_marker_staged",
                    True,
                    True,
                    "commit marker is durably staged",
                    expectation,
                ),
                entries,
                marker_bytes,
                expectation,
            )
        if present == {"guard", "commit", "cleanup"}:
            expectation = agree(
                parse("guard", "prepared"),
                parse("commit", "source_retired"),
            )
            expectation = observed(
                expectation,
                entries["cleanup"],
                after_rename=True,
            )
            return (
                result(
                    "source_committed",
                    True,
                    True,
                    "source retirement is committed; displaced source remains",
                    expectation,
                ),
                entries,
                marker_bytes,
                expectation,
            )
        if present == {"guard", "commit"}:
            expectation = agree(
                parse("guard", "prepared"),
                parse("commit", "source_retired"),
            )
            return (
                result(
                    "guards_committed",
                    True,
                    True,
                    "both durable retirement guards remain",
                    expectation,
                ),
                entries,
                marker_bytes,
                expectation,
            )
        if present == {"commit", "cleanup"}:
            expectation = agree(
                parse("commit", "source_retired"),
                parse("cleanup", "prepared"),
            )
            return (
                result(
                    "prepare_guard_moved",
                    True,
                    True,
                    "prepare guard is held by cleanup barrier",
                    expectation,
                ),
                entries,
                marker_bytes,
                expectation,
            )
        if present == {"commit"}:
            expectation = parse("commit", "source_retired")
            commit_entry = entries["commit"]
            assert commit_entry is not None
            if _ledger_matches_completion(
                ledger,
                expectation=expectation,
                commit_sha256=hashlib.sha256(commit_entry.data).hexdigest(),
            ):
                phase = "completed_commit_guard_only"
                detail = "permanent ledger is completed; final commit guard remains"
            else:
                phase = "commit_guard_only"
                detail = "final commit guard remains"
            return (
                result(phase, True, True, detail, expectation),
                entries,
                marker_bytes,
                expectation,
            )
        if present == {"cleanup"}:
            expectation = parse("cleanup", "source_retired")
            cleanup_entry = entries["cleanup"]
            assert cleanup_entry is not None
            if not _ledger_matches_completion(
                ledger,
                expectation=expectation,
                commit_sha256=hashlib.sha256(cleanup_entry.data).hexdigest(),
            ):
                raise ExactReceiptRetirementError(
                    "final commit cleanup is not covered by the permanent ledger"
                )
            return (
                result(
                    "commit_guard_moved",
                    True,
                    True,
                    "commit guard is held by cleanup barrier",
                    expectation,
                ),
                entries,
                marker_bytes,
                expectation,
            )
    except ExactReceiptRetirementError as exc:
        return result("invalid", False, True, str(exc)), entries, marker_bytes, None

    return (
        result(
            "invalid",
            False,
            True,
            "receipt retirement namespace is unknown, torn, or has changed",
        ),
        entries,
        marker_bytes,
        None,
    )


def inspect_exact_receipt_retirement(
    source_path: Path,
    expected_bytes: bytes,
    *,
    expected_mode: int = RETIREMENT_MODE,
    maximum_receipt_bytes: int = DEFAULT_MAXIMUM_RECEIPT_BYTES,
    independently_authorised_absence: bool = False,
) -> ReceiptRetirementInspection:
    """Inspect without mutation; unknown or malformed states are blocking.

    Absence is not proof that this receipt was retired.  A caller may classify
    an all-absent namespace as complete only when it has separate durable
    authority for idempotence and says so explicitly.
    """

    source = _absolute_path(source_path)
    expected = bytes(expected_bytes)
    if not expected or len(expected) > maximum_receipt_bytes:
        raise ExactReceiptRetirementError("expected receipt bytes are empty or oversized")
    directory_fd = _open_directory(source.parent)
    try:
        inspection, _, _, _ = _inspection(
            source=source,
            expected=expected,
            directory_fd=directory_fd,
            expected_mode=expected_mode,
            maximum=maximum_receipt_bytes,
            independently_authorised_absence=independently_authorised_absence,
        )
        return inspection
    finally:
        os.close(directory_fd)


def inspect_interrupted_receipt_retirement(
    source_path: Path,
    *,
    expected_mode: int = RETIREMENT_MODE,
    maximum_receipt_bytes: int = DEFAULT_MAXIMUM_RECEIPT_BYTES,
) -> ReceiptRetirementInspection:
    """Inspect marker-bound interrupted retirement without supplied bytes.

    This deliberately cannot authorise a fresh source receipt.  It exposes the
    exact hash carried by a valid retirement marker so a caller can bind that
    marker to an independently durable terminal outcome before permitting the
    marker-driven resumer to mutate the namespace.
    """

    source = _absolute_path(source_path)
    directory_fd = _open_directory(source.parent)
    try:
        inspection, _, _, _ = _inspection(
            source=source,
            expected=None,
            directory_fd=directory_fd,
            expected_mode=expected_mode,
            maximum=maximum_receipt_bytes,
            independently_authorised_absence=False,
        )
        return inspection
    finally:
        os.close(directory_fd)


def prepare_exact_receipt_retirement(
    source_path: Path,
    expected_bytes: bytes,
    *,
    mutation_authority: TransactionMutationAuthority | None = None,
    expected_mode: int = RETIREMENT_MODE,
    maximum_receipt_bytes: int = DEFAULT_MAXIMUM_RECEIPT_BYTES,
) -> ReceiptRetirementInspection:
    """Durably publish the exact prepare guard without moving the source.

    The only accepted initial states are an exact fresh source, its exact
    durably staged prepare marker, or the already-published exact prepare
    guard.  This makes re-entry safe across either marker-publication crash
    boundary while refusing absence and every later retirement phase.
    """

    require_transaction_mutation_authority(
        mutation_authority,
        operation="exact receipt retirement preparation",
    )
    source = _absolute_path(source_path)
    expected = bytes(expected_bytes)
    if not expected or len(expected) > maximum_receipt_bytes:
        raise ExactReceiptRetirementError("expected receipt bytes are empty or oversized")
    paths = _retirement_paths(source)
    directory_fd = _open_directory(source.parent)
    try:
        for _ in range(3):
            inspection, entries, _markers, expectation = _inspection(
                source=source,
                expected=expected,
                directory_fd=directory_fd,
                expected_mode=expected_mode,
                maximum=maximum_receipt_bytes,
                independently_authorised_absence=False,
            )
            if not inspection.valid:
                raise ExactReceiptRetirementError(inspection.detail)
            if inspection.phase == "prepared":
                return inspection
            if inspection.phase == "fresh":
                assert expectation is not None
                marker = _canonical_json(
                    _marker_document(
                        source_name=source.name,
                        expectation=expectation,
                        phase="prepared",
                    )
                )
                _stage_new(directory_fd, paths.guard_staging.name, marker)
                continue
            if inspection.phase == "prepare_marker_staged":
                staged = entries["guard_staging"]
                assert staged is not None
                _publish_staged(
                    directory_fd,
                    staging_name=paths.guard_staging.name,
                    final_name=paths.guard.name,
                    expected_entry=staged,
                    maximum=maximum_receipt_bytes,
                )
                continue
            raise ExactReceiptRetirementError(
                "exact receipt preparation refuses retirement phase "
                f"{inspection.phase!r}"
            )
        raise ExactReceiptRetirementError(
            "exact receipt preparation exceeded its bounded transition count"
        )
    finally:
        os.close(directory_fd)


def retire_exact_receipt(
    source_path: Path,
    expected_bytes: bytes,
    *,
    mutation_authority: TransactionMutationAuthority | None = None,
    expected_mode: int = RETIREMENT_MODE,
    maximum_receipt_bytes: int = DEFAULT_MAXIMUM_RECEIPT_BYTES,
    independently_authorised_absence: bool = False,
) -> ReceiptRetirementResult:
    """Retire only ``expected_bytes`` and resume every valid crash state.

    The caller must have completed all protected local transitions before
    invoking this function.  A returned result means every transient
    retirement pathname has been durably removed and the permanent ledger
    retains the exact completed generation.
    """

    require_transaction_mutation_authority(
        mutation_authority,
        operation="exact receipt retirement",
    )
    expected = bytes(expected_bytes)
    if not expected or len(expected) > maximum_receipt_bytes:
        raise ExactReceiptRetirementError("expected receipt bytes are empty or oversized")
    return _run_retirement(
        source_path,
        expected=expected,
        expected_mode=expected_mode,
        maximum_receipt_bytes=maximum_receipt_bytes,
        independently_authorised_absence=independently_authorised_absence,
        require_interrupted=False,
    )


def retire_or_resume_exact_receipt(
    source_path: Path,
    expected_receipt_bytes: bytes,
    *,
    mutation_authority: TransactionMutationAuthority,
    on_retirement_uncertainty: Callable[[], None] | None = None,
) -> ReceiptRetirementResult:
    """Retire one source through the shared fail-closed call boundary.

    A final namespace unlink can complete before the parent-directory fsync
    reports failure.  In that state the pathname barriers may all be absent,
    so callers cannot rely on a later namespace scan to stop remote work.
    Every application call path therefore uses this one wrapper and supplies
    its current-process incident latch callback.  The callback is deliberately
    invoked for every retirement exception: distinguishing a harmless error
    from an uncertain namespace transition after the fact would itself be an
    unsafe availability optimisation.
    """

    try:
        if retirement_auxiliary_barrier_exists(source_path):
            return resume_interrupted_receipt_retirement(
                source_path,
                mutation_authority=mutation_authority,
            )
        return retire_exact_receipt(
            source_path,
            expected_receipt_bytes,
            mutation_authority=mutation_authority,
        )
    except BaseException:
        if on_retirement_uncertainty is not None:
            on_retirement_uncertainty()
        raise


def resume_interrupted_receipt_retirement(
    source_path: Path,
    *,
    mutation_authority: TransactionMutationAuthority | None = None,
    expected_mode: int = RETIREMENT_MODE,
    maximum_receipt_bytes: int = DEFAULT_MAXIMUM_RECEIPT_BYTES,
) -> ReceiptRetirementResult:
    """Resume only a marker-proved interrupted retirement in a fresh process.

    No caller-provided receipt bytes are accepted.  The exact SHA-256, size and
    original filesystem identity are derived from strict canonical staging or
    final markers and cross-checked against any surviving source/cleanup bytes.
    A fresh source, an all-absent namespace, and a namespace with no auxiliary
    proof are deliberately rejected.
    """

    require_transaction_mutation_authority(
        mutation_authority,
        operation="interrupted exact receipt retirement resume",
    )
    return _run_retirement(
        source_path,
        expected=None,
        expected_mode=expected_mode,
        maximum_receipt_bytes=maximum_receipt_bytes,
        independently_authorised_absence=False,
        require_interrupted=True,
    )


def _run_retirement(
    source_path: Path,
    *,
    expected: bytes | None,
    expected_mode: int,
    maximum_receipt_bytes: int,
    independently_authorised_absence: bool,
    require_interrupted: bool,
) -> ReceiptRetirementResult:
    source = _absolute_path(source_path)
    paths = _retirement_paths(source)
    directory_fd = _open_directory(source.parent)
    transitions: list[str] = []
    initial_phase = "unknown"
    bound_expectation: RetirementExpectation | None = None
    try:
        for _ in range(24):
            ledger_path, exchange_path = retirement_ledger_paths(source)
            _recover_ledger_exchange(
                directory_fd,
                source_name=source.name,
                ledger_name=ledger_path.name,
                exchange_name=exchange_path.name,
                maximum=maximum_receipt_bytes,
            )
            inspection, entries, _markers, expectation = _inspection(
                source=source,
                expected=expected,
                directory_fd=directory_fd,
                expected_mode=expected_mode,
                maximum=maximum_receipt_bytes,
                independently_authorised_absence=(
                    independently_authorised_absence or bool(transitions)
                ),
            )
            if initial_phase == "unknown":
                initial_phase = inspection.phase
                if require_interrupted and initial_phase in {
                    "fresh",
                    "complete",
                    "absent_unproven",
                }:
                    raise ExactReceiptRetirementError(
                        "no marker-proved interrupted receipt retirement exists"
                    )
            if not inspection.valid:
                raise ExactReceiptRetirementError(inspection.detail)
            if expectation is not None:
                if (
                    bound_expectation is not None
                    and not _expectations_agree(bound_expectation, expectation)
                ):
                    raise ExactReceiptRetirementError(
                        "receipt retirement expectation changed between transitions"
                    )
                bound_expectation = expectation
            phase = inspection.phase
            if phase in {"complete", "ledger_completed"}:
                if bound_expectation is None:
                    if expected is None:
                        raise ExactReceiptRetirementError(
                            "completed retirement has no independently derived identity"
                        )
                    # Explicitly authorised initial absence has exact bytes but
                    # no filesystem identity to claim; return only its hash.
                    expected_sha256 = hashlib.sha256(expected).hexdigest()
                else:
                    expected_sha256 = bound_expectation.sha256
                return ReceiptRetirementResult(
                    completed=True,
                    resumed=(
                        require_interrupted
                        or initial_phase not in {"fresh", "complete", "ledger_completed"}
                    ),
                    initial_phase=initial_phase,
                    transitions=tuple(transitions),
                    expected_sha256=expected_sha256,
                )
            if phase == "fresh":
                assert expectation is not None
                marker = _canonical_json(
                    _marker_document(
                        source_name=source.name,
                        expectation=expectation,
                        phase="prepared",
                    )
                )
                _stage_new(directory_fd, paths.guard_staging.name, marker)
                transitions.append("prepare_marker_staged")
                continue
            if phase == "prepare_marker_staged":
                staged = entries["guard_staging"]
                assert staged is not None
                _publish_staged(
                    directory_fd,
                    staging_name=paths.guard_staging.name,
                    final_name=paths.guard.name,
                    expected_entry=staged,
                    maximum=maximum_receipt_bytes,
                )
                transitions.append("prepare_guard_published")
                continue
            if phase == "prepared":
                source_entry = entries["source"]
                assert source_entry is not None
                _move_exact_to_cleanup(
                    directory_fd,
                    source_name=source.name,
                    cleanup_name=paths.cleanup.name,
                    expected_entry=source_entry,
                    maximum=maximum_receipt_bytes,
                )
                transitions.append("source_moved_to_cleanup")
                continue
            if phase == "source_moved":
                assert expectation is not None
                committed_marker = _canonical_json(
                    _marker_document(
                        source_name=source.name,
                        expectation=expectation,
                        phase="source_retired",
                    )
                )
                _stage_new(
                    directory_fd,
                    paths.commit_staging.name,
                    committed_marker,
                )
                transitions.append("commit_marker_staged")
                continue
            if phase == "commit_marker_staged":
                staged = entries["commit_staging"]
                assert staged is not None
                _publish_staged(
                    directory_fd,
                    staging_name=paths.commit_staging.name,
                    final_name=paths.commit.name,
                    expected_entry=staged,
                    maximum=maximum_receipt_bytes,
                )
                transitions.append("commit_guard_published")
                continue
            if phase == "source_committed":
                displaced = entries["cleanup"]
                assert displaced is not None
                _unlink_exact_cleanup(
                    directory_fd,
                    cleanup_name=paths.cleanup.name,
                    expected_entry=displaced,
                    maximum=maximum_receipt_bytes,
                )
                transitions.append("displaced_source_removed")
                continue
            if phase == "guards_committed":
                guard_entry = entries["guard"]
                assert guard_entry is not None
                _move_exact_to_cleanup(
                    directory_fd,
                    source_name=paths.guard.name,
                    cleanup_name=paths.cleanup.name,
                    expected_entry=guard_entry,
                    maximum=maximum_receipt_bytes,
                )
                transitions.append("prepare_guard_moved_to_cleanup")
                continue
            if phase == "prepare_guard_moved":
                moved_guard = entries["cleanup"]
                assert moved_guard is not None
                _unlink_exact_cleanup(
                    directory_fd,
                    cleanup_name=paths.cleanup.name,
                    expected_entry=moved_guard,
                    maximum=maximum_receipt_bytes,
                )
                transitions.append("prepare_guard_removed")
                continue
            if phase == "commit_guard_only":
                commit_entry = entries["commit"]
                assert commit_entry is not None
                assert expectation is not None
                _complete_retirement_ledger(
                    source,
                    expectation=expectation,
                    commit_entry=commit_entry,
                    directory_fd=directory_fd,
                    maximum=maximum_receipt_bytes,
                )
                transitions.append("retirement_ledger_completed")
                continue
            if phase == "completed_commit_guard_only":
                commit_entry = entries["commit"]
                assert commit_entry is not None
                _move_exact_to_cleanup(
                    directory_fd,
                    source_name=paths.commit.name,
                    cleanup_name=paths.cleanup.name,
                    expected_entry=commit_entry,
                    maximum=maximum_receipt_bytes,
                )
                transitions.append("commit_guard_moved_to_cleanup")
                continue
            if phase == "commit_guard_moved":
                moved_commit = entries["cleanup"]
                assert moved_commit is not None
                _unlink_exact_cleanup(
                    directory_fd,
                    cleanup_name=paths.cleanup.name,
                    expected_entry=moved_commit,
                    maximum=maximum_receipt_bytes,
                )
                transitions.append("commit_guard_removed")
                continue
            raise ExactReceiptRetirementError(
                f"unhandled receipt retirement phase: {phase}"
            )
        raise ExactReceiptRetirementError(
            "receipt retirement exceeded its bounded transition count"
        )
    finally:
        os.close(directory_fd)
