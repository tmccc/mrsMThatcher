#!/usr/bin/env python3
"""Durable, single-transaction authority for public X post creation.

The lane-specific sending receipt is authoritative while a post is being
prepared.  Immediately before transport this module publishes a second,
payload-bound journal.  That journal remains a restart-visible global barrier
even if the lane receipt is removed after validation.  Process memory is used
only to prevent reuse within one interpreter; it is never the restart barrier.

The journal lifecycle is::

    prepared -> attempting -> confirmed -> retired

An ``attempting`` journal has an intentionally unknown remote outcome after an
abrupt process loss.  A ``confirmed`` journal binds the returned post ID while
the caller completes its lane-specific durable state.  Retirement is allowed
only while the exact confirmed lane receipt is still present, so at least one
restart-visible barrier remains throughout the supported transition.
"""

from __future__ import annotations

import ctypes
import hashlib
import json
import os
import re
import secrets
import stat
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


JOURNAL_BASENAME = "remote_write_transport_journal.json"
FENCE_BASENAME = "remote_write_transport_fence.json"
JOURNAL_SCHEMA_VERSION = 1
JOURNAL_MODE = 0o600
JOURNAL_MAX_BYTES = 128 * 1024
JOURNAL_STAGING_PREFIX = f".{JOURNAL_BASENAME}.transition."
JOURNAL_RETIREMENT_PREFIX = f".{JOURNAL_BASENAME}.retirement-guard."
_RENAME_EXCHANGE = 2
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_LANE_RE = re.compile(r"[a-z][a-z0-9_.:-]{0,79}")
_POST_ID_RE = re.compile(r"\d{1,30}")
_consumed_authorities: set[tuple[str, str]] = set()
_authority_lock = threading.Lock()


class TransportJournalError(RuntimeError):
    """A transport journal is missing, unsafe, conflicting, or stale."""


@dataclass(frozen=True)
class TransportAuthority:
    """One in-process handle bound to an exact durable journal generation."""

    transaction_id: str
    journal_path: str
    journal_sha256: str
    journal_device: int
    journal_inode: int
    fence_path: str
    fence_sha256: str
    fence_device: int
    fence_inode: int
    payload_sha256: str
    lane: str
    source_receipt_basename: str
    lifecycle_state: str


@dataclass(frozen=True)
class JournalSnapshot:
    """One stable, canonical journal observation."""

    document: dict[str, Any]
    data: bytes
    device: int
    inode: int
    sha256: str


@dataclass(frozen=True)
class _StableFile:
    data: bytes
    metadata: os.stat_result


def canonical_json_bytes(value: object) -> bytes:
    """Return the only journal JSON representation."""

    return (
        json.dumps(
            value,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def payload_sha256(payload: Mapping[str, Any]) -> str:
    """Hash an exact remote payload using canonical JSON."""

    return hashlib.sha256(canonical_json_bytes(dict(payload))).hexdigest()


def journal_path_for_receipt(receipt_path: Path) -> Path:
    """Return the one sibling journal shared by all public-create lanes."""

    return Path(receipt_path).parent / JOURNAL_BASENAME


def fence_path_for_journal(journal_path: Path) -> Path:
    """Return the immutable companion barrier for one transport journal."""

    return Path(journal_path).parent / FENCE_BASENAME


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise TransportJournalError(
                f"transport journal contains duplicate JSON key: {key}"
            )
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise TransportJournalError(
        f"transport journal contains invalid JSON constant: {value}"
    )


def _parse_strict_object_bytes(data: bytes, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except TransportJournalError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TransportJournalError(f"{label} is not strict UTF-8 JSON") from exc
    if not isinstance(value, dict):
        raise TransportJournalError(f"{label} must be a JSON object")
    return value


def _parse_canonical(data: bytes, *, label: str) -> dict[str, Any]:
    value = _parse_strict_object_bytes(data, label=label)
    if canonical_json_bytes(value) != data:
        raise TransportJournalError(f"{label} is not canonical JSON")
    return value


def _metadata_identity(value: os.stat_result) -> tuple[int, ...]:
    return (
        int(value.st_dev),
        int(value.st_ino),
        int(value.st_mode),
        int(value.st_nlink),
        int(value.st_uid),
        int(value.st_size),
        int(value.st_ctime_ns),
        int(value.st_mtime_ns),
    )


def _read_all(descriptor: int, maximum: int) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = os.read(descriptor, min(8192, maximum + 1 - total))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > maximum:
            raise TransportJournalError("durable transaction file is oversized")


def _read_stable_regular(
    path: Path,
    *,
    maximum: int,
    expected_mode: int | None = None,
    allowed_link_counts: frozenset[int] = frozenset({1}),
) -> _StableFile:
    """Read one owned, single-link ordinary file without following links."""

    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise TransportJournalError("O_NOFOLLOW is required")
    try:
        before = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise TransportJournalError(f"cannot inspect durable file: {path.name}") from exc
    if (
        not stat.S_ISREG(before.st_mode)
        or before.st_nlink not in allowed_link_counts
        or before.st_uid != os.geteuid()
        or before.st_size <= 0
        or before.st_size > maximum
        or (
            expected_mode is not None
            and stat.S_IMODE(before.st_mode) != expected_mode
        )
    ):
        raise TransportJournalError(f"durable file has unsafe metadata: {path.name}")
    descriptor = os.open(
        path,
        os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        opened = os.fstat(descriptor)
        data = _read_all(descriptor, maximum)
        after_fd = os.fstat(descriptor)
        after_path = os.stat(path, follow_symlinks=False)
    except FileNotFoundError as exc:
        raise TransportJournalError(
            f"durable file changed while inspected: {path.name}"
        ) from exc
    finally:
        os.close(descriptor)
    identity = _metadata_identity(opened)
    if (
        _metadata_identity(before) != identity
        or _metadata_identity(after_fd) != identity
        or _metadata_identity(after_path) != identity
        or len(data) != opened.st_size
    ):
        raise TransportJournalError(
            f"durable file changed while inspected: {path.name}"
        )
    return _StableFile(data=data, metadata=opened)


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    written = 0
    while written < len(view):
        count = os.write(descriptor, view[written:])
        if count <= 0:
            raise OSError("short write while publishing transport journal")
        written += count


def _open_directory(path: Path) -> int:
    try:
        metadata = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise TransportJournalError("transport journal directory is unavailable") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise TransportJournalError("transport journal parent is not a directory")
    return os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))


def _publish_new(path: Path, data: bytes) -> None:
    """Publish a new journal without an overwrite window.

    An interrupted write deliberately leaves a visible invalid final pathname,
    which all callers treat as a global barrier.
    """

    if len(data) > JOURNAL_MAX_BYTES:
        raise TransportJournalError("transport journal exceeds its size limit")
    path.parent.mkdir(parents=True, exist_ok=True)
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(
        path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | nofollow
        | getattr(os, "O_CLOEXEC", 0),
        JOURNAL_MODE,
    )
    try:
        _write_all(descriptor, data)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory_fd = _open_directory(path.parent)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _rename_exchange(directory_fd: int, first: str, second: str) -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    renameat2 = getattr(libc, "renameat2", None)
    if renameat2 is None:
        raise TransportJournalError("renameat2(RENAME_EXCHANGE) is required")
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
            os.fsencode(first),
            directory_fd,
            os.fsencode(second),
            _RENAME_EXCHANGE,
        )
        != 0
    ):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), first, second)


def _replace_exact(path: Path, *, expected: bytes, replacement: bytes) -> None:
    """Atomically exchange a journal generation and prove the displaced bytes."""

    token = secrets.token_hex(16)
    staging_name = f"{JOURNAL_STAGING_PREFIX}{token}"
    staging = path.parent / staging_name
    descriptor = os.open(
        staging,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        JOURNAL_MODE,
    )
    try:
        _write_all(descriptor, replacement)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    directory_fd = _open_directory(path.parent)
    exchanged = False
    try:
        os.fsync(directory_fd)
        _rename_exchange(directory_fd, path.name, staging_name)
        exchanged = True
        os.fsync(directory_fd)
        displaced = _read_stable_regular(
            staging,
            maximum=JOURNAL_MAX_BYTES,
            expected_mode=JOURNAL_MODE,
        )
        current = _read_stable_regular(
            path,
            maximum=JOURNAL_MAX_BYTES,
            expected_mode=JOURNAL_MODE,
        )
        if displaced.data != expected or current.data != replacement:
            raise TransportJournalError(
                "transport journal changed during atomic lifecycle transition"
            )
        os.unlink(staging_name, dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
        if not exchanged:
            # A failed pre-exchange staging file is itself a fail-closed marker.
            # Remove it only when the still-current final journal is exactly the
            # generation the caller expected.
            try:
                current = _read_stable_regular(
                    path,
                    maximum=JOURNAL_MAX_BYTES,
                    expected_mode=JOURNAL_MODE,
                )
            except Exception:
                current = None
            if current is not None and current.data == expected:
                try:
                    staging.unlink()
                except FileNotFoundError:
                    pass


def _validate_document(
    value: dict[str, Any],
    *,
    expected_kind: str = "mrsMThatcher_remote_write_transport_journal",
) -> None:
    required = {
        "schema_version",
        "document_kind",
        "transaction_id",
        "lifecycle_state",
        "lane",
        "request_method",
        "request_path",
        "remote_payload",
        "remote_payload_sha256",
        "source_receipt",
        "remote_post_id",
    }
    if set(value) != required:
        raise TransportJournalError("transport journal fields are invalid")
    if (
        value.get("schema_version") != JOURNAL_SCHEMA_VERSION
        or value.get("document_kind") != expected_kind
        or value.get("lifecycle_state") not in {"prepared", "attempting", "confirmed"}
        or not _LANE_RE.fullmatch(str(value.get("lane") or ""))
        or value.get("request_method") != "POST"
        or value.get("request_path") != "/2/tweets"
        or not isinstance(value.get("remote_payload"), dict)
        or not _SHA256_RE.fullmatch(str(value.get("remote_payload_sha256") or ""))
        or payload_sha256(value["remote_payload"]) != value["remote_payload_sha256"]
    ):
        raise TransportJournalError("transport journal semantics are invalid")
    source = value.get("source_receipt")
    if (
        not isinstance(source, dict)
        or set(source) != {
            "basename",
            "device",
            "inode",
            "size",
            "sha256",
        }
        or not isinstance(source.get("basename"), str)
        or Path(source["basename"]).name != source["basename"]
        or type(source.get("device")) is not int
        or type(source.get("inode")) is not int
        or type(source.get("size")) is not int
        or source["size"] <= 0
        or not _SHA256_RE.fullmatch(str(source.get("sha256") or ""))
    ):
        raise TransportJournalError("transport journal source receipt is invalid")
    expected_id = hashlib.sha256(
        b"mrsMThatcher-transport-journal-v1\0"
        + str(value["lane"]).encode("utf-8")
        + b"\0"
        + str(source["sha256"]).encode("ascii")
        + b"\0"
        + str(value["remote_payload_sha256"]).encode("ascii")
    ).hexdigest()
    if value.get("transaction_id") != expected_id:
        raise TransportJournalError("transport journal transaction ID is invalid")
    remote_post_id = value.get("remote_post_id")
    if value["lifecycle_state"] == "confirmed":
        if not _POST_ID_RE.fullmatch(str(remote_post_id or "")):
            raise TransportJournalError("confirmed journal has no valid post ID")
    elif remote_post_id is not None:
        raise TransportJournalError("unconfirmed journal contains a post ID")


def _snapshot(
    path: Path,
    *,
    expected_kind: str = "mrsMThatcher_remote_write_transport_journal",
) -> JournalSnapshot:
    inspected = _read_stable_regular(
        path,
        maximum=JOURNAL_MAX_BYTES,
        expected_mode=JOURNAL_MODE,
    )
    document = _parse_canonical(inspected.data, label="transport journal")
    _validate_document(document, expected_kind=expected_kind)
    return JournalSnapshot(
        document=document,
        data=inspected.data,
        device=int(inspected.metadata.st_dev),
        inode=int(inspected.metadata.st_ino),
        sha256=hashlib.sha256(inspected.data).hexdigest(),
    )


def _required_snapshot(path: Path) -> JournalSnapshot:
    """Read a journal generation whose disappearance is itself a fault."""

    try:
        return _snapshot(path)
    except FileNotFoundError as exc:
        raise TransportJournalError(
            "required transport journal disappeared"
        ) from exc


def _required_fence_snapshot(path: Path) -> JournalSnapshot:
    """Read the immutable companion whose disappearance is itself a fault."""

    try:
        return _snapshot(
            path,
            expected_kind="mrsMThatcher_remote_write_transport_fence",
        )
    except FileNotFoundError as exc:
        raise TransportJournalError(
            "required transport fence disappeared"
        ) from exc


def _unsafe_auxiliary_names(path: Path) -> list[str]:
    try:
        names = os.listdir(path.parent)
    except OSError as exc:
        raise TransportJournalError("cannot inspect transport journal directory") from exc
    return sorted(
        name
        for name in names
        if name.startswith(JOURNAL_STAGING_PREFIX)
        or name.startswith(JOURNAL_RETIREMENT_PREFIX)
    )


def inspect_transport_journal(path: Path) -> JournalSnapshot | None:
    """Return one valid journal, or fail on any torn transition pathname."""

    path = Path(path)
    auxiliaries = _unsafe_auxiliary_names(path)
    if auxiliaries:
        raise TransportJournalError(
            "unfinished transport-journal transition blocks remote writes"
        )
    try:
        return _snapshot(path)
    except FileNotFoundError:
        return None


def transport_journal_is_blocking(path: Path) -> bool:
    """Treat either durable companion, invalid state, or a torn transition as a barrier."""

    try:
        path = Path(path)
        journal = inspect_transport_journal(path)
        fence_path = fence_path_for_journal(path)
        try:
            fence = _snapshot(
                fence_path,
                expected_kind="mrsMThatcher_remote_write_transport_fence",
            )
        except FileNotFoundError:
            fence = None
        if journal is None and fence is None:
            return False
        if journal is None or fence is None:
            return True
        if (
            journal.document["transaction_id"]
            != fence.document["transaction_id"]
        ):
            return True
        return True
    except Exception:
        return True


def begin_transport_transaction(
    *,
    receipt_path: Path,
    expected_receipt: Mapping[str, Any],
    lane: str,
    payload: Mapping[str, Any],
) -> TransportAuthority:
    """Publish a payload-bound restart barrier from one exact lane receipt."""

    if not _LANE_RE.fullmatch(str(lane)):
        raise TransportJournalError("transport lane is invalid")
    receipt_path = Path(receipt_path)
    journal_path = journal_path_for_receipt(receipt_path)
    if transport_journal_is_blocking(journal_path):
        raise TransportJournalError("another transport transaction is unresolved")
    try:
        receipt = _read_stable_regular(receipt_path, maximum=JOURNAL_MAX_BYTES)
    except FileNotFoundError as exc:
        raise TransportJournalError("source receipt is not durably present") from exc
    parsed_receipt = _parse_strict_object_bytes(
        receipt.data,
        label="source receipt",
    )
    if parsed_receipt != dict(expected_receipt):
        raise TransportJournalError("source receipt does not match prepared transaction")
    receipt_hash = hashlib.sha256(receipt.data).hexdigest()
    payload_value = dict(payload)
    payload_hash = payload_sha256(payload_value)
    transaction_id = hashlib.sha256(
        b"mrsMThatcher-transport-journal-v1\0"
        + str(lane).encode("utf-8")
        + b"\0"
        + receipt_hash.encode("ascii")
        + b"\0"
        + payload_hash.encode("ascii")
    ).hexdigest()
    document: dict[str, Any] = {
        "schema_version": JOURNAL_SCHEMA_VERSION,
        "document_kind": "mrsMThatcher_remote_write_transport_journal",
        "transaction_id": transaction_id,
        "lifecycle_state": "prepared",
        "lane": str(lane),
        "request_method": "POST",
        "request_path": "/2/tweets",
        "remote_payload": payload_value,
        "remote_payload_sha256": payload_hash,
        "source_receipt": {
            "basename": receipt_path.name,
            "device": int(receipt.metadata.st_dev),
            "inode": int(receipt.metadata.st_ino),
            "size": len(receipt.data),
            "sha256": receipt_hash,
        },
        "remote_post_id": None,
    }
    data = canonical_json_bytes(document)
    _publish_new(journal_path, data)
    snapshot = _required_snapshot(journal_path)
    if snapshot.data != data:
        raise TransportJournalError("published transport journal changed")
    fence_path = fence_path_for_journal(journal_path)
    fence_document = {
        **document,
        "document_kind": "mrsMThatcher_remote_write_transport_fence",
    }
    fence_data = canonical_json_bytes(fence_document)
    _publish_new(fence_path, fence_data)
    fence = _required_fence_snapshot(fence_path)
    if fence.data != fence_data:
        raise TransportJournalError("published transport fence changed")
    return TransportAuthority(
        transaction_id=transaction_id,
        journal_path=str(journal_path.absolute()),
        journal_sha256=snapshot.sha256,
        journal_device=snapshot.device,
        journal_inode=snapshot.inode,
        fence_path=str(fence_path.absolute()),
        fence_sha256=fence.sha256,
        fence_device=fence.device,
        fence_inode=fence.inode,
        payload_sha256=payload_hash,
        lane=str(lane),
        source_receipt_basename=receipt_path.name,
        lifecycle_state="prepared",
    )


def arm_transport_transaction(
    path: Path,
    authority: TransportAuthority,
) -> TransportAuthority:
    """Make the durable outcome explicitly ambiguous before transport."""

    snapshot = _required_snapshot(Path(path))
    fence = _required_fence_snapshot(Path(authority.fence_path))
    if (
        authority.lifecycle_state != "prepared"
        or snapshot.sha256 != authority.journal_sha256
        or (snapshot.device, snapshot.inode)
        != (authority.journal_device, authority.journal_inode)
        or snapshot.document["transaction_id"] != authority.transaction_id
        or snapshot.document["lifecycle_state"] != "prepared"
        or fence.sha256 != authority.fence_sha256
        or (fence.device, fence.inode)
        != (authority.fence_device, authority.fence_inode)
        or fence.document["transaction_id"] != authority.transaction_id
    ):
        raise TransportJournalError("prepared transport authority is stale")
    replacement = {**snapshot.document, "lifecycle_state": "attempting"}
    replacement_data = canonical_json_bytes(replacement)
    _replace_exact(Path(path), expected=snapshot.data, replacement=replacement_data)
    updated = _required_snapshot(Path(path))
    if updated.data != replacement_data:
        raise TransportJournalError("armed transport journal changed")
    return TransportAuthority(
        transaction_id=authority.transaction_id,
        journal_path=authority.journal_path,
        journal_sha256=updated.sha256,
        journal_device=updated.device,
        journal_inode=updated.inode,
        fence_path=authority.fence_path,
        fence_sha256=authority.fence_sha256,
        fence_device=authority.fence_device,
        fence_inode=authority.fence_inode,
        payload_sha256=authority.payload_sha256,
        lane=authority.lane,
        source_receipt_basename=authority.source_receipt_basename,
        lifecycle_state="attempting",
    )


def consume_transport_authority(
    path: Path,
    authority: TransportAuthority,
    *,
    method: str,
    request_path: str,
    payload: Mapping[str, Any],
    expected_receipt_path: Path | None = None,
) -> None:
    """Validate and consume one exact authority immediately before transport."""

    snapshot = _required_snapshot(Path(path))
    fence_path = Path(authority.fence_path)
    fence = _required_fence_snapshot(fence_path)
    payload_hash = payload_sha256(payload)
    canonical_path = Path(path).absolute()
    if expected_receipt_path is not None:
        expected_receipt_path = Path(expected_receipt_path)
        if (
            canonical_path != journal_path_for_receipt(expected_receipt_path).absolute()
            or fence_path.absolute()
            != fence_path_for_journal(canonical_path).absolute()
            or authority.source_receipt_basename != expected_receipt_path.name
            or snapshot.document["source_receipt"]["basename"]
            != expected_receipt_path.name
        ):
            raise TransportJournalError(
                "transport authority is not anchored to the canonical lane receipt"
            )
    if (
        authority.lifecycle_state != "attempting"
        or snapshot.sha256 != authority.journal_sha256
        or (snapshot.device, snapshot.inode)
        != (authority.journal_device, authority.journal_inode)
        or snapshot.document["transaction_id"] != authority.transaction_id
        or snapshot.document["lifecycle_state"] != "attempting"
        or snapshot.document["request_method"] != str(method).upper()
        or snapshot.document["request_path"] != request_path
        or snapshot.document["remote_payload"] != dict(payload)
        or snapshot.document["remote_payload_sha256"] != payload_hash
        or authority.payload_sha256 != payload_hash
        or fence.sha256 != authority.fence_sha256
        or (fence.device, fence.inode)
        != (authority.fence_device, authority.fence_inode)
        or fence.document["transaction_id"] != authority.transaction_id
        or fence.document["remote_payload"] != dict(payload)
        or fence.document["source_receipt"]
        != snapshot.document["source_receipt"]
    ):
        raise TransportJournalError("transport authority does not bind this request")
    key = (str(Path(path).absolute()), authority.transaction_id)
    with _authority_lock:
        if key in _consumed_authorities:
            raise TransportJournalError("transport authority was already consumed")
        _consumed_authorities.add(key)


def confirm_transport_transaction(
    path: Path,
    authority: TransportAuthority,
    *,
    post_id: str,
) -> JournalSnapshot:
    """Bind a valid remote post ID before returning control to the lane."""

    if not _POST_ID_RE.fullmatch(str(post_id or "")):
        raise TransportJournalError("remote confirmation has no valid post ID")
    snapshot = _required_snapshot(Path(path))
    fence = _required_fence_snapshot(Path(authority.fence_path))
    key = (str(Path(path).absolute()), authority.transaction_id)
    if (
        authority.lifecycle_state != "attempting"
        or snapshot.sha256 != authority.journal_sha256
        or (snapshot.device, snapshot.inode)
        != (authority.journal_device, authority.journal_inode)
        or snapshot.document["transaction_id"] != authority.transaction_id
        or snapshot.document["lifecycle_state"] != "attempting"
        or fence.sha256 != authority.fence_sha256
        or (fence.device, fence.inode)
        != (authority.fence_device, authority.fence_inode)
        or fence.document["transaction_id"] != authority.transaction_id
    ):
        raise TransportJournalError("attempting transport authority is stale")
    with _authority_lock:
        if key not in _consumed_authorities:
            raise TransportJournalError(
                "transport authority was not consumed at the request boundary"
            )
    replacement = {
        **snapshot.document,
        "lifecycle_state": "confirmed",
        "remote_post_id": str(post_id),
    }
    replacement_data = canonical_json_bytes(replacement)
    _replace_exact(Path(path), expected=snapshot.data, replacement=replacement_data)
    updated = _required_snapshot(Path(path))
    if updated.data != replacement_data:
        raise TransportJournalError("confirmed transport journal changed")
    return updated


def retire_confirmed_transport_transaction(
    *,
    receipt_path: Path,
    expected_confirmed_receipt: Mapping[str, Any],
    lane: str,
    post_id: str,
) -> None:
    """Retire the journal while an exact confirmed lane receipt remains.

    A hard-link retirement guard preserves the receipt inode while the journal
    is removed.  Any crash leaves either the journal, the guard, or the lane
    receipt visible to the next process.
    """

    receipt_path = Path(receipt_path)
    path = journal_path_for_receipt(receipt_path)
    snapshot = _required_snapshot(path)
    fence_path = fence_path_for_journal(path)
    fence = _required_fence_snapshot(fence_path)
    if (
        snapshot.document["lifecycle_state"] != "confirmed"
        or snapshot.document["lane"] != lane
        or snapshot.document["remote_post_id"] != str(post_id)
        or fence.document["transaction_id"]
        != snapshot.document["transaction_id"]
        or fence.document["source_receipt"]["basename"]
        != receipt_path.name
    ):
        raise TransportJournalError("confirmed journal does not match lane receipt")
    receipt = _read_stable_regular(receipt_path, maximum=JOURNAL_MAX_BYTES)
    parsed = _parse_strict_object_bytes(
        receipt.data,
        label="confirmed receipt",
    )
    if parsed != dict(expected_confirmed_receipt):
        raise TransportJournalError("confirmed receipt does not match transaction")

    directory_fd = _open_directory(path.parent)
    guard_name = f"{JOURNAL_RETIREMENT_PREFIX}{snapshot.document['transaction_id']}"
    try:
        os.link(
            receipt_path.name,
            guard_name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
            follow_symlinks=False,
        )
        os.fsync(directory_fd)
        guard = os.stat(guard_name, dir_fd=directory_fd, follow_symlinks=False)
        current_receipt = os.stat(
            receipt_path.name,
            dir_fd=directory_fd,
            follow_symlinks=False,
        )
        if (
            (guard.st_dev, guard.st_ino)
            != (receipt.metadata.st_dev, receipt.metadata.st_ino)
            or (current_receipt.st_dev, current_receipt.st_ino)
            != (receipt.metadata.st_dev, receipt.metadata.st_ino)
        ):
            raise TransportJournalError("receipt changed during journal retirement")
        os.unlink(path.name, dir_fd=directory_fd)
        os.fsync(directory_fd)
        # Revalidate the canonical receipt after the journal is gone.  The
        # hard-link guard remains a barrier until this check succeeds.
        current = _read_stable_regular(
            receipt_path,
            maximum=JOURNAL_MAX_BYTES,
            allowed_link_counts=frozenset({2}),
        )
        if current.data != receipt.data:
            raise TransportJournalError("receipt changed during journal retirement")
        os.unlink(fence_path.name, dir_fd=directory_fd)
        os.fsync(directory_fd)
        current = _read_stable_regular(
            receipt_path,
            maximum=JOURNAL_MAX_BYTES,
            allowed_link_counts=frozenset({2}),
        )
        if current.data != receipt.data:
            raise TransportJournalError("receipt changed during fence retirement")
        os.unlink(guard_name, dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def reset_consumed_authorities_for_tests() -> None:
    """Clear process-only replay state for isolated tests."""

    with _authority_lock:
        _consumed_authorities.clear()
