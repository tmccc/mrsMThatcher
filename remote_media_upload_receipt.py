#!/usr/bin/env python3
"""Restart-visible authority and receipt for one remote media upload.

The caller chooses a lane-specific receipt pathname.  Publishing both a
``sending`` receipt and an immutable payload-bound companion fence must precede
transport.  A hard process loss after publication therefore leaves two
independent unresolved pathnames for the next process.  The receipt lifecycle
is::

    sending -> confirmed -> retired

``retired`` is represented by absence of both media companions.  Retirement is
permitted only after a payload-bound tweet journal and its immutable fence have
been durably published and independently verified.  The media receipt is
removed first and the immutable media fence second, while both tweet owners are
revalidated around every destructive step.  A fresh process can therefore
inspect and resume an interrupted transition without trusting process memory
or the mutable main-post receipt pathname.

This module performs no file discovery, network operation, scheduling, or
application-state mutation beyond the caller-specified receipt and its bounded
transition guards.
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

from transaction_mutation_authority import (
    TransactionMutationAuthority,
    require_transaction_mutation_authority,
)


SCHEMA_VERSION = 1
DOCUMENT_KIND = "mrsMThatcher_remote_media_upload_receipt"
FENCE_DOCUMENT_KIND = "mrsMThatcher_remote_media_upload_fence"
RECEIPT_MODE = 0o600
RECEIPT_MAX_BYTES = 128 * 1024
IMAGE_MAX_BYTES = 256 * 1024 * 1024
TRANSITION_PREFIX = ".remote-media-upload.transition."
# Kept as a fail-closed legacy auxiliary prefix so any interrupted receipt-
# guarded retirement from an older candidate cannot be mistaken for a clear
# state.  New retirement does not create these pathnames.
RETIREMENT_GUARD_PREFIX = ".remote-media-upload.main-post-guard."
_RENAME_EXCHANGE = 2
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_MIME_RE = re.compile(r"image/[a-z0-9][a-z0-9.+-]{0,63}")
_MEDIA_ID_RE = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_POST_ID_RE = re.compile(r"\d{1,30}")
_VALIDATOR_ID_RE = re.compile(r"[a-z][a-z0-9_.:-]{2,159}")
_ALLOWED_LANES = frozenset({"quote_image", "daily_meme"})
_AuthorityKey = tuple[str, int, int, int, str, int, int, int, str, str]
_consumed_authorities: dict[
    _AuthorityKey,
    tuple[int, "MediaUploadAuthority"],
] = {}
_issued_untransmitted_authorities: dict[
    _AuthorityKey,
    tuple[int, "MediaUploadAuthority"],
] = {}
_aborting_authorities: set[_AuthorityKey] = set()
_authority_lock = threading.Lock()


class MediaUploadReceiptError(RuntimeError):
    """A media receipt or one of its bound inputs is unsafe or stale."""


@dataclass(frozen=True)
class MediaUploadAuthority:
    """One-shot authority bound to exact durable receipt and fence inodes."""

    transaction_id: str
    receipt_path: str
    receipt_device: int
    receipt_inode: int
    receipt_ctime_ns: int
    receipt_sha256: str
    fence_path: str
    fence_device: int
    fence_inode: int
    fence_ctime_ns: int
    fence_sha256: str
    image_sha256: str
    payload_metadata_sha256: str
    lane: str
    lifecycle_state: str = "sending"


@dataclass(frozen=True)
class ConfirmedMediaUpload:
    """Identity of one exact durable ``confirmed`` receipt generation."""

    transaction_id: str
    receipt_path: str
    receipt_device: int
    receipt_inode: int
    receipt_ctime_ns: int
    receipt_sha256: str
    fence_path: str
    fence_device: int
    fence_inode: int
    fence_ctime_ns: int
    fence_sha256: str
    lane: str
    media_id: str
    lifecycle_state: str = "confirmed"


@dataclass(frozen=True)
class ReceiptBoundMediaPayload:
    """Immutable bytes proven to match one exact sending receipt generation.

    Callers must pass ``data`` itself to the multipart encoder.  ``basename``
    is display metadata only; no later transport proof is permitted to reopen
    a pathname obtained from ``basename`` or a file object's ``.name``.
    """

    transaction_id: str
    receipt_path: str
    receipt_device: int
    receipt_inode: int
    receipt_ctime_ns: int
    receipt_sha256: str
    fence_path: str
    fence_device: int
    fence_inode: int
    fence_ctime_ns: int
    fence_sha256: str
    data: bytes
    basename: str
    mime_type: str
    size: int
    sha256: str
    payload_metadata_sha256: str
    lane: str


@dataclass(frozen=True)
class MediaHandoffAuthority:
    """Exact independent transport pair which owns confirmed media.

    This authority is derived from the already-published tweet transport
    journal and its immutable fence.  It is deliberately independent of the
    mutable canonical main-post receipt, so loss of that pathname cannot leave
    media retirement without a restart-visible owner.
    """

    media_transaction_id: str
    media_id: str
    lane: str
    media_receipt_path: str
    transport_transaction_id: str
    transport_journal_path: str
    transport_journal_device: int
    transport_journal_inode: int
    transport_journal_ctime_ns: int
    transport_journal_sha256: str
    transport_fence_path: str
    transport_fence_device: int
    transport_fence_inode: int
    transport_fence_ctime_ns: int
    transport_fence_sha256: str
    source_receipt_basename: str


@dataclass(frozen=True)
class MediaRetirementState:
    """Inspectable and restart-reconstructible media retirement state."""

    state: str
    media_receipt_present: bool
    media_fence_present: bool
    handoff_journal_present: bool
    handoff_fence_present: bool
    media_transaction_id: str
    media_id: str
    lane: str


@dataclass(frozen=True)
class MediaReceiptSnapshot:
    """A stable, strict and canonical receipt observation."""

    document: dict[str, Any]
    data: bytes
    device: int
    inode: int
    ctime_ns: int
    sha256: str


@dataclass(frozen=True)
class _StableFile:
    data: bytes
    metadata: os.stat_result


def canonical_json_bytes(value: object) -> bytes:
    """Return the sole permitted receipt and payload-metadata encoding."""

    try:
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
    except (TypeError, ValueError) as exc:
        raise MediaUploadReceiptError("value is not strict canonical JSON") from exc


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise MediaUploadReceiptError(
                f"media receipt contains duplicate JSON key: {key}"
            )
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise MediaUploadReceiptError(
        f"media receipt contains invalid JSON constant: {value}"
    )


def _parse_strict_json(data: bytes, *, label: str) -> Any:
    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_strict_object,
            parse_constant=_reject_constant,
        )
    except MediaUploadReceiptError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MediaUploadReceiptError(f"{label} is not strict UTF-8 JSON") from exc


def _parse_canonical_document(data: bytes) -> dict[str, Any]:
    value = _parse_strict_json(data, label="media receipt")
    if not isinstance(value, dict):
        raise MediaUploadReceiptError("media receipt must be a JSON object")
    if canonical_json_bytes(value) != data:
        raise MediaUploadReceiptError("media receipt is not canonical JSON")
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
        chunk = os.read(descriptor, min(1024 * 1024, maximum + 1 - total))
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)
        total += len(chunk)
        if total > maximum:
            raise MediaUploadReceiptError("durable media file is oversized")


def _read_stable_regular(
    path: Path,
    *,
    maximum: int,
    expected_mode: int | None = None,
    allowed_link_counts: frozenset[int] = frozenset({1}),
) -> _StableFile:
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise MediaUploadReceiptError("O_NOFOLLOW is required")
    try:
        before = os.stat(path, follow_symlinks=False)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise MediaUploadReceiptError(
            f"cannot inspect durable file: {path.name}"
        ) from exc
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
        raise MediaUploadReceiptError(f"durable file has unsafe metadata: {path.name}")
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
        raise MediaUploadReceiptError(
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
        raise MediaUploadReceiptError(
            f"durable file changed while inspected: {path.name}"
        )
    return _StableFile(data=data, metadata=opened)


def _write_all(descriptor: int, data: bytes) -> None:
    view = memoryview(data)
    written = 0
    while written < len(view):
        count = os.write(descriptor, view[written:])
        if count <= 0:
            raise OSError("short write while publishing media receipt")
        written += count


def _open_directory(path: Path) -> int:
    try:
        metadata = os.stat(path, follow_symlinks=False)
    except OSError as exc:
        raise MediaUploadReceiptError("media receipt directory is unavailable") from exc
    if not stat.S_ISDIR(metadata.st_mode):
        raise MediaUploadReceiptError("media receipt parent is not a directory")
    return os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))


def _normalised_path(path: Path) -> Path:
    value = Path(path)
    if not value.name or value.name in {".", ".."}:
        raise MediaUploadReceiptError("media receipt path is invalid")
    return value.absolute()


def fence_path_for_receipt(receipt_path: Path) -> Path:
    """Return the fixed immutable companion pathname for a media receipt."""

    receipt_path = _normalised_path(receipt_path)
    return receipt_path.parent / f"{receipt_path.name}.fence.json"


def _publish_new(path: Path, data: bytes) -> None:
    if len(data) > RECEIPT_MAX_BYTES:
        raise MediaUploadReceiptError("media receipt exceeds its size limit")
    validation_fd = _open_directory(path.parent)
    os.close(validation_fd)
    descriptor = os.open(
        path,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        RECEIPT_MODE,
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
        raise MediaUploadReceiptError("renameat2(RENAME_EXCHANGE) is required")
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


def _replace_exact(
    path: Path,
    *,
    expected: bytes,
    replacement: bytes,
    expected_device: int,
    expected_inode: int,
    expected_ctime_ns: int,
) -> None:
    token = secrets.token_hex(16)
    staging_name = f"{TRANSITION_PREFIX}{token}"
    staging = path.parent / staging_name
    descriptor = os.open(
        staging,
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_CLOEXEC", 0),
        RECEIPT_MODE,
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
        before_exchange = _read_stable_regular(
            path,
            maximum=RECEIPT_MAX_BYTES,
            expected_mode=RECEIPT_MODE,
        )
        if (
            before_exchange.data != expected
            or (
                int(before_exchange.metadata.st_dev),
                int(before_exchange.metadata.st_ino),
                int(before_exchange.metadata.st_ctime_ns),
            )
            != (expected_device, expected_inode, expected_ctime_ns)
        ):
            raise MediaUploadReceiptError(
                "media receipt changed before atomic lifecycle transition"
            )
        _rename_exchange(directory_fd, path.name, staging_name)
        exchanged = True
        os.fsync(directory_fd)
        displaced = _read_stable_regular(
            staging,
            maximum=RECEIPT_MAX_BYTES,
            expected_mode=RECEIPT_MODE,
        )
        current = _read_stable_regular(
            path,
            maximum=RECEIPT_MAX_BYTES,
            expected_mode=RECEIPT_MODE,
        )
        # RENAME_EXCHANGE advances ctime on the displaced inode.  Its ctime was
        # proved immediately before exchange; post-exchange device/inode proves
        # that no different pathname generation won the remaining interval.
        if (
            displaced.data != expected
            or (
                int(displaced.metadata.st_dev),
                int(displaced.metadata.st_ino),
            )
            != (expected_device, expected_inode)
            or current.data != replacement
        ):
            raise MediaUploadReceiptError(
                "media receipt changed during atomic lifecycle transition"
            )
        os.unlink(staging_name, dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)
        if not exchanged:
            # A staging file is a deliberate restart-visible barrier unless the
            # original generation is still exact and cleanup succeeds.
            try:
                current = _read_stable_regular(
                    path,
                    maximum=RECEIPT_MAX_BYTES,
                    expected_mode=RECEIPT_MODE,
                )
            except Exception:
                current = None
            if (
                current is not None
                and current.data == expected
                and (
                    int(current.metadata.st_dev),
                    int(current.metadata.st_ino),
                    int(current.metadata.st_ctime_ns),
                )
                == (expected_device, expected_inode, expected_ctime_ns)
            ):
                try:
                    staging.unlink()
                except FileNotFoundError:
                    pass


def _metadata_hash(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_json_bytes(dict(value))).hexdigest()


def _transaction_id(
    *,
    lane: str,
    image_basename: str,
    image_size: int,
    image_sha256: str,
    mime_type: str,
    payload_metadata_sha256: str,
) -> str:
    return hashlib.sha256(
        b"mrsMThatcher-media-upload-v1\0"
        + lane.encode("ascii")
        + b"\0"
        + image_basename.encode("utf-8")
        + b"\0"
        + str(image_size).encode("ascii")
        + b"\0"
        + image_sha256.encode("ascii")
        + b"\0"
        + mime_type.encode("ascii")
        + b"\0"
        + payload_metadata_sha256.encode("ascii")
    ).hexdigest()


def _validate_document(
    value: dict[str, Any],
    *,
    expected_kind: str = DOCUMENT_KIND,
) -> None:
    required = {
        "schema_version",
        "document_kind",
        "transaction_id",
        "lifecycle_state",
        "lane",
        "image",
        "payload_metadata",
        "payload_metadata_sha256",
        "remote_media_id",
    }
    if set(value) != required:
        raise MediaUploadReceiptError("media receipt fields are invalid")
    lane = value.get("lane")
    lifecycle = value.get("lifecycle_state")
    metadata = value.get("payload_metadata")
    metadata_hash = value.get("payload_metadata_sha256")
    if (
        type(value.get("schema_version")) is not int
        or value.get("schema_version") != SCHEMA_VERSION
        or value.get("document_kind") != expected_kind
        or lane not in _ALLOWED_LANES
        or lifecycle not in {"sending", "confirmed"}
        or not isinstance(metadata, dict)
        or not _SHA256_RE.fullmatch(str(metadata_hash or ""))
        or _metadata_hash(metadata) != metadata_hash
    ):
        raise MediaUploadReceiptError("media receipt semantics are invalid")
    image = value.get("image")
    if (
        not isinstance(image, dict)
        or set(image)
        != {
            "basename",
            "device",
            "inode",
            "ctime_ns",
            "size",
            "sha256",
            "mime_type",
        }
        or not isinstance(image.get("basename"), str)
        or Path(image["basename"]).name != image["basename"]
        or type(image.get("device")) is not int
        or type(image.get("inode")) is not int
        or type(image.get("ctime_ns")) is not int
        or type(image.get("size")) is not int
        or image["device"] < 0
        or image["inode"] <= 0
        or image["ctime_ns"] < 0
        or image["size"] <= 0
        or not _SHA256_RE.fullmatch(str(image.get("sha256") or ""))
        or not _MIME_RE.fullmatch(str(image.get("mime_type") or ""))
    ):
        raise MediaUploadReceiptError("media receipt image identity is invalid")
    expected_id = _transaction_id(
        lane=str(lane),
        image_basename=image["basename"],
        image_size=image["size"],
        image_sha256=image["sha256"],
        mime_type=image["mime_type"],
        payload_metadata_sha256=str(metadata_hash),
    )
    if value.get("transaction_id") != expected_id:
        raise MediaUploadReceiptError("media receipt transaction ID is invalid")
    media_id = value.get("remote_media_id")
    if expected_kind == FENCE_DOCUMENT_KIND and lifecycle != "sending":
        raise MediaUploadReceiptError("immutable media fence lifecycle is invalid")
    if lifecycle == "confirmed":
        if not _MEDIA_ID_RE.fullmatch(str(media_id or "")):
            raise MediaUploadReceiptError("confirmed media receipt has no valid media ID")
    elif media_id is not None:
        raise MediaUploadReceiptError("sending media receipt contains a media ID")


def _snapshot(
    path: Path,
    *,
    expected_kind: str = DOCUMENT_KIND,
) -> MediaReceiptSnapshot:
    inspected = _read_stable_regular(
        path,
        maximum=RECEIPT_MAX_BYTES,
        expected_mode=RECEIPT_MODE,
    )
    document = _parse_canonical_document(inspected.data)
    _validate_document(document, expected_kind=expected_kind)
    return MediaReceiptSnapshot(
        document=document,
        data=inspected.data,
        device=int(inspected.metadata.st_dev),
        inode=int(inspected.metadata.st_ino),
        ctime_ns=int(inspected.metadata.st_ctime_ns),
        sha256=hashlib.sha256(inspected.data).hexdigest(),
    )


def _required_snapshot(path: Path) -> MediaReceiptSnapshot:
    try:
        return _snapshot(path)
    except FileNotFoundError as exc:
        raise MediaUploadReceiptError("required media receipt disappeared") from exc


def _required_fence_snapshot(path: Path) -> MediaReceiptSnapshot:
    try:
        return _snapshot(path, expected_kind=FENCE_DOCUMENT_KIND)
    except FileNotFoundError as exc:
        raise MediaUploadReceiptError("required media fence disappeared") from exc


def _auxiliary_names(path: Path) -> list[str]:
    try:
        names = os.listdir(path.parent)
    except OSError as exc:
        raise MediaUploadReceiptError("cannot inspect media receipt directory") from exc
    return sorted(
        name
        for name in names
        if name.startswith(TRANSITION_PREFIX)
        or name.startswith(RETIREMENT_GUARD_PREFIX)
    )


def inspect_media_upload_receipt(path: Path) -> MediaReceiptSnapshot | None:
    """Return a valid receipt; reject any invalid or torn transition state."""

    path = _normalised_path(path)
    if _auxiliary_names(path):
        raise MediaUploadReceiptError(
            "unfinished media-receipt transition blocks remote writes"
        )
    try:
        return _snapshot(path)
    except FileNotFoundError:
        return None


def media_upload_receipt_is_blocking(path: Path) -> bool:
    """Treat either companion, invalid state, or torn transition as a barrier."""

    try:
        path = _normalised_path(path)
        receipt = inspect_media_upload_receipt(path)
        fence_path = fence_path_for_receipt(path)
        try:
            fence = _snapshot(fence_path, expected_kind=FENCE_DOCUMENT_KIND)
        except FileNotFoundError:
            fence = None
        if receipt is None and fence is None:
            return False
        if receipt is None or fence is None:
            return True
        if receipt.document["transaction_id"] != fence.document["transaction_id"]:
            return True
        return True
    except Exception:
        return True


def media_upload_has_valid_restart_barrier(path: Path) -> bool:
    """Return whether a strict media receipt or immutable fence is readable.

    Blocking inspection failures remain fail closed through
    :func:`media_upload_receipt_is_blocking`, but they must not be promoted to
    proof that restart-persistent authority exists.  This stricter predicate is
    used only when deciding whether a retained confirmed-post signal guard may
    be released.
    """

    try:
        path = _normalised_path(path)
        # Listing first proves that the owning directory itself is currently
        # inspectable.  A transition name need not be accepted as durable
        # authority: the strict receipt/fence objects below are sufficient when
        # either survives.
        os.listdir(path.parent)
    except Exception:
        return False
    receipt: MediaReceiptSnapshot | None
    fence: MediaReceiptSnapshot | None
    try:
        receipt = _snapshot(path)
    except Exception:
        receipt = None
    try:
        fence = _snapshot(
            fence_path_for_receipt(path),
            expected_kind=FENCE_DOCUMENT_KIND,
        )
    except Exception:
        fence = None
    return receipt is not None or fence is not None


def load_confirmed_media_upload(path: Path) -> ConfirmedMediaUpload | None:
    """Return an exact confirmed generation, or fail on unresolved sending state."""

    path = _normalised_path(path)
    snapshot = inspect_media_upload_receipt(path)
    if snapshot is None:
        if media_upload_receipt_is_blocking(path):
            raise MediaUploadReceiptError(
                "media-upload fence remains without its receipt"
            )
        return None
    fence_path = fence_path_for_receipt(path)
    fence = _required_fence_snapshot(fence_path)
    if snapshot.document["lifecycle_state"] != "confirmed":
        raise MediaUploadReceiptError("media upload is not durably confirmed")
    if (
        fence.document["lifecycle_state"] != "sending"
        or fence.document["transaction_id"]
        != snapshot.document["transaction_id"]
        or fence.document["lane"] != snapshot.document["lane"]
        or fence.document["image"] != snapshot.document["image"]
        or fence.document["payload_metadata"]
        != snapshot.document["payload_metadata"]
        or fence.document["payload_metadata_sha256"]
        != snapshot.document["payload_metadata_sha256"]
    ):
        raise MediaUploadReceiptError(
            "confirmed media receipt does not match immutable fence"
        )
    return ConfirmedMediaUpload(
        transaction_id=str(snapshot.document["transaction_id"]),
        receipt_path=str(path),
        receipt_device=snapshot.device,
        receipt_inode=snapshot.inode,
        receipt_ctime_ns=snapshot.ctime_ns,
        receipt_sha256=snapshot.sha256,
        fence_path=str(fence_path),
        fence_device=fence.device,
        fence_inode=fence.inode,
        fence_ctime_ns=fence.ctime_ns,
        fence_sha256=fence.sha256,
        lane=str(snapshot.document["lane"]),
        media_id=str(snapshot.document["remote_media_id"]),
    )


def _authority_key_from_snapshots(
    receipt_path: Path,
    receipt: MediaReceiptSnapshot,
    fence: MediaReceiptSnapshot,
    transaction_id: str,
) -> _AuthorityKey:
    """Return the process registry key for one exact companion pair."""

    return (
        str(_normalised_path(receipt_path)),
        receipt.device,
        receipt.inode,
        receipt.ctime_ns,
        receipt.sha256,
        fence.device,
        fence.inode,
        fence.ctime_ns,
        fence.sha256,
        transaction_id,
    )


def _authority_key_from_authority(
    authority: MediaUploadAuthority,
) -> _AuthorityKey:
    """Return the exact pair key embedded in a media authority."""

    return (
        authority.receipt_path,
        authority.receipt_device,
        authority.receipt_inode,
        authority.receipt_ctime_ns,
        authority.receipt_sha256,
        authority.fence_device,
        authority.fence_inode,
        authority.fence_ctime_ns,
        authority.fence_sha256,
        authority.transaction_id,
    )


def _validate_exact_sending_pair(
    receipt_path: Path,
    authority: MediaUploadAuthority,
) -> tuple[MediaReceiptSnapshot, MediaReceiptSnapshot, _AuthorityKey]:
    """Validate the complete exact sending pair bound to ``authority``."""

    receipt_path = _normalised_path(receipt_path)
    if _auxiliary_names(receipt_path):
        raise MediaUploadReceiptError(
            "unfinished media-receipt transition blocks authority use"
        )
    snapshot = _required_snapshot(receipt_path)
    fence_path = fence_path_for_receipt(receipt_path)
    fence = _required_fence_snapshot(fence_path)
    expected_fence = {
        **snapshot.document,
        "document_kind": FENCE_DOCUMENT_KIND,
    }
    key = _authority_key_from_snapshots(
        receipt_path,
        snapshot,
        fence,
        authority.transaction_id,
    )
    if (
        authority.lifecycle_state != "sending"
        or authority.receipt_path != str(receipt_path)
        or authority.fence_path != str(fence_path)
        or key != _authority_key_from_authority(authority)
        or authority.transaction_id != snapshot.document["transaction_id"]
        or authority.transaction_id != fence.document["transaction_id"]
        or authority.lane != snapshot.document["lane"]
        or snapshot.document["lifecycle_state"] != "sending"
        or fence.document["lifecycle_state"] != "sending"
        or fence.document != expected_fence
        or authority.image_sha256 != snapshot.document["image"]["sha256"]
        or authority.payload_metadata_sha256
        != snapshot.document["payload_metadata_sha256"]
    ):
        raise MediaUploadReceiptError(
            "media-upload authority does not bind the exact sending pair"
        )
    return snapshot, fence, key


def begin_media_upload(
    *,
    receipt_path: Path,
    image_path: Path,
    lane: str,
    mime_type: str,
    payload_metadata: Mapping[str, Any],
) -> MediaUploadAuthority:
    """Durably publish one deterministic ``sending`` receipt with O_EXCL."""

    if lane not in _ALLOWED_LANES:
        raise MediaUploadReceiptError("media-upload lane is invalid")
    if not _MIME_RE.fullmatch(str(mime_type)):
        raise MediaUploadReceiptError("media MIME type is invalid")
    receipt_path = _normalised_path(receipt_path)
    image_path = _normalised_path(image_path)
    if _auxiliary_names(receipt_path):
        raise MediaUploadReceiptError("unfinished media-receipt transition exists")
    if media_upload_receipt_is_blocking(receipt_path):
        raise MediaUploadReceiptError("media-upload receipt is already unresolved")
    try:
        image = _read_stable_regular(image_path, maximum=IMAGE_MAX_BYTES)
    except FileNotFoundError as exc:
        raise MediaUploadReceiptError("source image is not durably present") from exc
    image_hash = hashlib.sha256(image.data).hexdigest()
    metadata_value = dict(payload_metadata)
    metadata_hash = _metadata_hash(metadata_value)
    transaction_id = _transaction_id(
        lane=lane,
        image_basename=image_path.name,
        image_size=len(image.data),
        image_sha256=image_hash,
        mime_type=mime_type,
        payload_metadata_sha256=metadata_hash,
    )
    document: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "document_kind": DOCUMENT_KIND,
        "transaction_id": transaction_id,
        "lifecycle_state": "sending",
        "lane": lane,
        "image": {
            "basename": image_path.name,
            "device": int(image.metadata.st_dev),
            "inode": int(image.metadata.st_ino),
            "ctime_ns": int(image.metadata.st_ctime_ns),
            "size": len(image.data),
            "sha256": image_hash,
            "mime_type": mime_type,
        },
        "payload_metadata": metadata_value,
        "payload_metadata_sha256": metadata_hash,
        "remote_media_id": None,
    }
    data = canonical_json_bytes(document)
    try:
        _publish_new(receipt_path, data)
    except FileExistsError as exc:
        raise MediaUploadReceiptError("media-upload receipt already exists") from exc
    snapshot = _required_snapshot(receipt_path)
    if snapshot.data != data:
        raise MediaUploadReceiptError("published media receipt changed")
    fence_path = fence_path_for_receipt(receipt_path)
    fence_document = {**document, "document_kind": FENCE_DOCUMENT_KIND}
    fence_data = canonical_json_bytes(fence_document)
    try:
        _publish_new(fence_path, fence_data)
    except FileExistsError as exc:
        raise MediaUploadReceiptError("media-upload fence already exists") from exc
    fence = _required_fence_snapshot(fence_path)
    if fence.data != fence_data:
        raise MediaUploadReceiptError("published media fence changed")
    authority = MediaUploadAuthority(
        transaction_id=transaction_id,
        receipt_path=str(receipt_path),
        receipt_device=snapshot.device,
        receipt_inode=snapshot.inode,
        receipt_ctime_ns=snapshot.ctime_ns,
        receipt_sha256=snapshot.sha256,
        fence_path=str(fence_path),
        fence_device=fence.device,
        fence_inode=fence.inode,
        fence_ctime_ns=fence.ctime_ns,
        fence_sha256=fence.sha256,
        image_sha256=image_hash,
        payload_metadata_sha256=metadata_hash,
        lane=lane,
    )
    key = _authority_key_from_snapshots(
        receipt_path,
        snapshot,
        fence,
        transaction_id,
    )
    with _authority_lock:
        if (
            key in _issued_untransmitted_authorities
            or key in _consumed_authorities
            or key in _aborting_authorities
        ):
            raise MediaUploadReceiptError(
                "media-upload authority generation is already registered"
            )
        _issued_untransmitted_authorities[key] = (os.getpid(), authority)
    return authority


def _validate_sending_authority(
    receipt_path: Path,
    authority: MediaUploadAuthority,
    *,
    lane: str,
    mime_type: str,
    payload_metadata: Mapping[str, Any],
) -> tuple[MediaReceiptSnapshot, MediaReceiptSnapshot, str]:
    """Validate one exact sending generation without consuming it."""

    receipt_path = _normalised_path(receipt_path)
    snapshot, fence, _key = _validate_exact_sending_pair(
        receipt_path,
        authority,
    )
    fence_path = fence_path_for_receipt(receipt_path)
    metadata_hash = _metadata_hash(dict(payload_metadata))
    if (
        authority.lifecycle_state != "sending"
        or authority.receipt_path != str(receipt_path)
        or authority.receipt_device != snapshot.device
        or authority.receipt_inode != snapshot.inode
        or authority.receipt_ctime_ns != snapshot.ctime_ns
        or authority.receipt_sha256 != snapshot.sha256
        or authority.fence_path != str(fence_path)
        or authority.fence_device != fence.device
        or authority.fence_inode != fence.inode
        or authority.fence_ctime_ns != fence.ctime_ns
        or authority.fence_sha256 != fence.sha256
        or authority.transaction_id != snapshot.document["transaction_id"]
        or authority.transaction_id != fence.document["transaction_id"]
        or authority.lane != lane
        or snapshot.document["lifecycle_state"] != "sending"
        or fence.document["lifecycle_state"] != "sending"
        or snapshot.document["lane"] != lane
        or fence.document["lane"] != lane
        or snapshot.document["image"]["mime_type"] != mime_type
        or fence.document["image"] != snapshot.document["image"]
        or authority.payload_metadata_sha256 != metadata_hash
        or snapshot.document["payload_metadata_sha256"] != metadata_hash
        or snapshot.document["payload_metadata"] != dict(payload_metadata)
        or fence.document["payload_metadata"] != dict(payload_metadata)
        or fence.document["payload_metadata_sha256"] != metadata_hash
    ):
        raise MediaUploadReceiptError("media-upload authority does not bind request")
    return snapshot, fence, metadata_hash


def bind_media_upload_payload(
    receipt_path: Path,
    authority: MediaUploadAuthority,
    *,
    image_path: Path,
    lane: str,
    mime_type: str,
    payload_metadata: Mapping[str, Any],
) -> ReceiptBoundMediaPayload:
    """Copy and bind the exact bytes which the multipart request will send.

    The stable source pathname is opened only here.  The returned immutable
    byte string is the transmission body and the later authority-consumption
    check hashes those bytes directly.  This closes the former interval in
    which a validated pathname (or a misleading file-object ``.name``) could
    refer to bytes different from those actually read by ``requests``.
    """

    receipt_path = _normalised_path(receipt_path)
    image_path = _normalised_path(image_path)
    snapshot, fence, metadata_hash = _validate_sending_authority(
        receipt_path,
        authority,
        lane=lane,
        mime_type=mime_type,
        payload_metadata=payload_metadata,
    )
    try:
        image = _read_stable_regular(image_path, maximum=IMAGE_MAX_BYTES)
    except FileNotFoundError as exc:
        raise MediaUploadReceiptError("bound source image disappeared") from exc
    expected_image = snapshot.document["image"]
    if (
        image_path.name != expected_image["basename"]
        or int(image.metadata.st_dev) != expected_image["device"]
        or int(image.metadata.st_ino) != expected_image["inode"]
        or int(image.metadata.st_ctime_ns) != expected_image["ctime_ns"]
        or len(image.data) != expected_image["size"]
        or hashlib.sha256(image.data).hexdigest() != expected_image["sha256"]
        or authority.image_sha256 != expected_image["sha256"]
    ):
        raise MediaUploadReceiptError("source image identity changed before transport")
    # ``bytes`` is immutable.  Force a distinct, bounded allocation so a
    # mutable buffer owned by the caller can never be the transmitted proof.
    payload_data = bytes(memoryview(image.data))
    return ReceiptBoundMediaPayload(
        transaction_id=authority.transaction_id,
        receipt_path=str(receipt_path),
        receipt_device=snapshot.device,
        receipt_inode=snapshot.inode,
        receipt_ctime_ns=snapshot.ctime_ns,
        receipt_sha256=snapshot.sha256,
        fence_path=str(fence_path_for_receipt(receipt_path)),
        fence_device=fence.device,
        fence_inode=fence.inode,
        fence_ctime_ns=fence.ctime_ns,
        fence_sha256=fence.sha256,
        data=payload_data,
        basename=str(expected_image["basename"]),
        mime_type=str(expected_image["mime_type"]),
        size=int(expected_image["size"]),
        sha256=str(expected_image["sha256"]),
        payload_metadata_sha256=metadata_hash,
        lane=lane,
    )


def consume_media_upload_authority(
    receipt_path: Path,
    authority: MediaUploadAuthority,
    *,
    payload: ReceiptBoundMediaPayload,
    lane: str,
    mime_type: str,
    payload_metadata: Mapping[str, Any],
) -> ReceiptBoundMediaPayload:
    """Consume authority for the exact immutable multipart body.

    This function intentionally has no image pathname parameter and never
    opens a file.  Its successful return is the sole body the caller may pass
    to the multipart encoder.
    """

    if not isinstance(payload, ReceiptBoundMediaPayload):
        raise MediaUploadReceiptError(
            "media transport requires a receipt-bound immutable payload"
        )
    receipt_path = _normalised_path(receipt_path)
    snapshot, fence, metadata_hash = _validate_sending_authority(
        receipt_path,
        authority,
        lane=lane,
        mime_type=mime_type,
        payload_metadata=payload_metadata,
    )
    expected_image = snapshot.document["image"]
    if type(payload.data) is not bytes:
        raise MediaUploadReceiptError(
            "immutable media payload body must be exact bytes"
        )
    actual_hash = hashlib.sha256(payload.data).hexdigest()
    if (
        payload.transaction_id != authority.transaction_id
        or payload.receipt_path != str(receipt_path)
        or payload.receipt_device != snapshot.device
        or payload.receipt_inode != snapshot.inode
        or payload.receipt_ctime_ns != snapshot.ctime_ns
        or payload.receipt_sha256 != snapshot.sha256
        or payload.fence_path != str(fence_path_for_receipt(receipt_path))
        or payload.fence_device != fence.device
        or payload.fence_inode != fence.inode
        or payload.fence_ctime_ns != fence.ctime_ns
        or payload.fence_sha256 != fence.sha256
        or payload.basename != expected_image["basename"]
        or payload.mime_type != mime_type
        or payload.size != len(payload.data)
        or payload.size != expected_image["size"]
        or payload.sha256 != actual_hash
        or payload.sha256 != expected_image["sha256"]
        or payload.sha256 != authority.image_sha256
        or payload.payload_metadata_sha256 != metadata_hash
        or payload.lane != lane
    ):
        raise MediaUploadReceiptError(
            "immutable media payload does not bind the sending receipt"
        )
    key = (
        str(receipt_path),
        snapshot.device,
        snapshot.inode,
        snapshot.ctime_ns,
        snapshot.sha256,
        fence.device,
        fence.inode,
        fence.ctime_ns,
        fence.sha256,
        authority.transaction_id,
    )
    with _authority_lock:
        if key in _consumed_authorities:
            raise MediaUploadReceiptError("media-upload authority was already consumed")
        if key in _aborting_authorities:
            raise MediaUploadReceiptError(
                "media-upload authority abort is in progress"
            )
        issued = _issued_untransmitted_authorities.get(key)
        if (
            issued is None
            or issued[0] != os.getpid()
            or issued[1] is not authority
        ):
            raise MediaUploadReceiptError(
                "media-upload authority was not issued in this process"
            )
        del _issued_untransmitted_authorities[key]
        _consumed_authorities[key] = (os.getpid(), authority)
    return payload


def abort_untransmitted_media_upload(
    receipt_path: Path,
    authority: MediaUploadAuthority,
    *,
    mutation_authority: TransactionMutationAuthority | None = None,
) -> None:
    """Retire an exact process-issued pair proven not to reach transport.

    The caller must possess the original in-process authority and independent
    mutation authority.  Authority consumption and abort claiming are atomic
    with respect to each other.  The mutable receipt is removed and durably
    synced first; the immutable fence remains the restart barrier until it is
    revalidated, removed and durably synced.

    This transition is intentionally not restart-resumable.  A fresh process
    cannot reconstruct process-only proof that transport was never attempted,
    so any surviving sending companion remains a manual-reconciliation
    barrier.
    """

    require_transaction_mutation_authority(
        mutation_authority,
        operation="untransmitted media-upload abort",
    )
    receipt_path = _normalised_path(receipt_path)
    if not isinstance(authority, MediaUploadAuthority):
        raise MediaUploadReceiptError(
            "untransmitted media-upload abort requires typed authority"
        )
    if authority.receipt_path != str(receipt_path):
        raise MediaUploadReceiptError(
            "untransmitted media-upload authority targets another receipt"
        )
    key = _authority_key_from_authority(authority)
    with _authority_lock:
        if key in _consumed_authorities:
            raise MediaUploadReceiptError(
                "consumed media-upload authority cannot be aborted"
            )
        if key in _aborting_authorities:
            raise MediaUploadReceiptError(
                "media-upload authority abort is already in progress"
            )
        issued = _issued_untransmitted_authorities.get(key)
        if (
            issued is None
            or issued[0] != os.getpid()
            or issued[1] is not authority
        ):
            raise MediaUploadReceiptError(
                "untransmitted media-upload authority was not issued in this process"
            )
        del _issued_untransmitted_authorities[key]
        _aborting_authorities.add(key)

    directory_fd: int | None = None
    try:
        snapshot, fence, current_key = _validate_exact_sending_pair(
            receipt_path,
            authority,
        )
        if current_key != key:
            raise MediaUploadReceiptError(
                "untransmitted media-upload generation changed"
            )
        directory_fd = _open_directory(receipt_path.parent)

        # Re-read the complete pair immediately before the first destructive
        # boundary.  The process-only abort claim prevents a supported
        # authority consumer from winning the remaining interval.
        current_receipt, current_fence, immediate_key = (
            _validate_exact_sending_pair(receipt_path, authority)
        )
        if (
            immediate_key != key
            or current_receipt.data != snapshot.data
            or current_fence.data != fence.data
        ):
            raise MediaUploadReceiptError(
                "media-upload pair changed immediately before abort"
            )
        require_transaction_mutation_authority(
            mutation_authority,
            operation="untransmitted media-upload receipt removal",
        )
        os.unlink(receipt_path.name, dir_fd=directory_fd)
        os.fsync(directory_fd)

        if _auxiliary_names(receipt_path):
            raise MediaUploadReceiptError(
                "unfinished media-receipt transition appeared during abort"
            )
        try:
            os.stat(receipt_path, follow_symlinks=False)
        except FileNotFoundError:
            pass
        except OSError as exc:
            raise MediaUploadReceiptError(
                "cannot verify media receipt removal"
            ) from exc
        else:
            raise MediaUploadReceiptError(
                "media receipt reappeared during untransmitted abort"
            )

        fence_path = fence_path_for_receipt(receipt_path)
        surviving_fence = _required_fence_snapshot(fence_path)
        if (
            surviving_fence.data != fence.data
            or surviving_fence.device != authority.fence_device
            or surviving_fence.inode != authority.fence_inode
            or surviving_fence.ctime_ns != authority.fence_ctime_ns
            or surviving_fence.sha256 != authority.fence_sha256
        ):
            raise MediaUploadReceiptError(
                "media-upload fence changed before untransmitted abort"
            )
        require_transaction_mutation_authority(
            mutation_authority,
            operation="untransmitted media-upload fence removal",
        )
        os.unlink(fence_path.name, dir_fd=directory_fd)
        os.fsync(directory_fd)

        for retired_path in (receipt_path, fence_path):
            try:
                os.stat(retired_path, follow_symlinks=False)
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise MediaUploadReceiptError(
                    "cannot verify untransmitted media-upload retirement"
                ) from exc
            raise MediaUploadReceiptError(
                "untransmitted media-upload companion survived retirement"
            )
    finally:
        if directory_fd is not None:
            os.close(directory_fd)
        with _authority_lock:
            _aborting_authorities.discard(key)


def confirm_media_upload(
    receipt_path: Path,
    authority: MediaUploadAuthority,
    *,
    mutation_authority: TransactionMutationAuthority | None = None,
    media_id: str,
) -> ConfirmedMediaUpload:
    """Atomically replace exact ``sending`` state with a confirmed media ID."""

    require_transaction_mutation_authority(
        mutation_authority,
        operation="media upload receipt confirmation",
    )
    if not _MEDIA_ID_RE.fullmatch(str(media_id)):
        raise MediaUploadReceiptError("remote media ID is invalid")
    receipt_path = _normalised_path(receipt_path)
    snapshot = _required_snapshot(receipt_path)
    fence_path = fence_path_for_receipt(receipt_path)
    fence = _required_fence_snapshot(fence_path)
    key = (
        str(receipt_path),
        snapshot.device,
        snapshot.inode,
        snapshot.ctime_ns,
        snapshot.sha256,
        fence.device,
        fence.inode,
        fence.ctime_ns,
        fence.sha256,
        authority.transaction_id,
    )
    with _authority_lock:
        consumed = _consumed_authorities.get(key)
    if (
        consumed is None
        or consumed[0] != os.getpid()
        or consumed[1] is not authority
        or authority.lifecycle_state != "sending"
        or authority.receipt_path != str(receipt_path)
        or authority.receipt_device != snapshot.device
        or authority.receipt_inode != snapshot.inode
        or authority.receipt_ctime_ns != snapshot.ctime_ns
        or authority.receipt_sha256 != snapshot.sha256
        or authority.fence_path != str(fence_path)
        or authority.fence_device != fence.device
        or authority.fence_inode != fence.inode
        or authority.fence_ctime_ns != fence.ctime_ns
        or authority.fence_sha256 != fence.sha256
        or authority.transaction_id != snapshot.document["transaction_id"]
        or authority.transaction_id != fence.document["transaction_id"]
        or snapshot.document["lifecycle_state"] != "sending"
        or fence.document["lifecycle_state"] != "sending"
    ):
        raise MediaUploadReceiptError("sending media-upload authority is stale")
    replacement = {
        **snapshot.document,
        "lifecycle_state": "confirmed",
        "remote_media_id": str(media_id),
    }
    replacement_data = canonical_json_bytes(replacement)
    _replace_exact(
        receipt_path,
        expected=snapshot.data,
        replacement=replacement_data,
        expected_device=snapshot.device,
        expected_inode=snapshot.inode,
        expected_ctime_ns=snapshot.ctime_ns,
    )
    confirmed = _required_snapshot(receipt_path)
    if confirmed.data != replacement_data:
        raise MediaUploadReceiptError("confirmed media receipt changed")
    return ConfirmedMediaUpload(
        transaction_id=authority.transaction_id,
        receipt_path=str(receipt_path),
        receipt_device=confirmed.device,
        receipt_inode=confirmed.inode,
        receipt_ctime_ns=confirmed.ctime_ns,
        receipt_sha256=confirmed.sha256,
        fence_path=str(fence_path),
        fence_device=fence.device,
        fence_inode=fence.inode,
        fence_ctime_ns=fence.ctime_ns,
        fence_sha256=fence.sha256,
        lane=authority.lane,
        media_id=str(media_id),
    )


def _transport_owner_snapshot(
    path: Path,
    *,
    expected_kind: str,
) -> tuple[dict[str, Any], _StableFile, str]:
    """Read and validate the bounded transport-owner vocabulary."""

    inspected = _read_stable_regular(
        path,
        maximum=RECEIPT_MAX_BYTES,
        expected_mode=RECEIPT_MODE,
    )
    value = _parse_strict_json(inspected.data, label="transport handoff owner")
    if not isinstance(value, dict) or canonical_json_bytes(value) != inspected.data:
        raise MediaUploadReceiptError("transport handoff owner is not canonical JSON")
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
        "source_validation",
        "remote_post_id",
        "confirmation_epoch",
    }
    source = value.get("source_receipt")
    source_validation = value.get("source_validation")
    payload = value.get("remote_payload")
    if (
        set(value) != required
        or type(value.get("schema_version")) is not int
        or value.get("schema_version") != 2
        or value.get("document_kind") != expected_kind
        or not _SHA256_RE.fullmatch(str(value.get("transaction_id") or ""))
        or value.get("lifecycle_state")
        not in {"prepared", "attempting", "confirmed"}
        or value.get("lane") not in _ALLOWED_LANES
        or value.get("request_method") != "POST"
        or value.get("request_path") != "/2/tweets"
        or not isinstance(payload, dict)
        or not _SHA256_RE.fullmatch(str(value.get("remote_payload_sha256") or ""))
        or _metadata_hash(payload) != value.get("remote_payload_sha256")
        or not isinstance(source, dict)
        or set(source)
        != {"basename", "device", "inode", "ctime_ns", "size", "sha256"}
        or not isinstance(source.get("basename"), str)
        or Path(source["basename"]).name != source["basename"]
        or type(source.get("device")) is not int
        or type(source.get("inode")) is not int
        or type(source.get("ctime_ns")) is not int
        or type(source.get("size")) is not int
        or source["device"] < 0
        or source["inode"] <= 0
        or source["ctime_ns"] < 0
        or source["size"] <= 0
        or not _SHA256_RE.fullmatch(str(source.get("sha256") or ""))
        or not isinstance(source_validation, dict)
        or set(source_validation)
        != {"validator_id", "receipt_sha256", "payload_sha256"}
        or not _VALIDATOR_ID_RE.fullmatch(
            str(source_validation.get("validator_id") or "")
        )
        or source_validation.get("receipt_sha256") != source["sha256"]
        or source_validation.get("payload_sha256")
        != value["remote_payload_sha256"]
    ):
        raise MediaUploadReceiptError("transport handoff owner semantics are invalid")
    expected_transaction_id = hashlib.sha256(
        b"mrsMThatcher-transport-journal-v2\0"
        + str(value["lane"]).encode("utf-8")
        + b"\0"
        + str(source["sha256"]).encode("ascii")
        + b"\0"
        + str(value["remote_payload_sha256"]).encode("ascii")
        + b"\0"
        + str(source_validation["validator_id"]).encode("utf-8")
    ).hexdigest()
    remote_post_id = value.get("remote_post_id")
    confirmation_epoch = value.get("confirmation_epoch")
    if value["transaction_id"] != expected_transaction_id:
        raise MediaUploadReceiptError(
            "transport handoff owner transaction ID is invalid"
        )
    if value["lifecycle_state"] == "confirmed":
        if (
            not _POST_ID_RE.fullmatch(str(remote_post_id or ""))
            or type(confirmation_epoch) is not int
            or confirmation_epoch < 0
        ):
            raise MediaUploadReceiptError(
                "confirmed transport handoff owner is invalid"
            )
    elif remote_post_id is not None or confirmation_epoch is not None:
        raise MediaUploadReceiptError(
            "unconfirmed transport handoff owner contains confirmation data"
        )
    return value, inspected, hashlib.sha256(inspected.data).hexdigest()


def _validate_transport_handoff(
    authority: MediaHandoffAuthority,
) -> tuple[dict[str, Any], dict[str, Any]]:
    journal_path = _normalised_path(Path(authority.transport_journal_path))
    fence_path = _normalised_path(Path(authority.transport_fence_path))
    journal, journal_file, journal_hash = _transport_owner_snapshot(
        journal_path,
        expected_kind="mrsMThatcher_remote_write_transport_journal",
    )
    fence, fence_file, fence_hash = _transport_owner_snapshot(
        fence_path,
        expected_kind="mrsMThatcher_remote_write_transport_fence",
    )
    immutable_fields = (
        "transaction_id",
        "lane",
        "request_method",
        "request_path",
        "remote_payload",
        "remote_payload_sha256",
        "source_receipt",
    )
    media = journal.get("remote_payload", {}).get("media")
    if (
        journal_path.parent != fence_path.parent
        or journal_path.parent != Path(authority.media_receipt_path).parent
        or authority.transport_journal_device != int(journal_file.metadata.st_dev)
        or authority.transport_journal_inode != int(journal_file.metadata.st_ino)
        or authority.transport_journal_ctime_ns
        != int(journal_file.metadata.st_ctime_ns)
        or authority.transport_journal_sha256 != journal_hash
        or authority.transport_fence_device != int(fence_file.metadata.st_dev)
        or authority.transport_fence_inode != int(fence_file.metadata.st_ino)
        or authority.transport_fence_ctime_ns
        != int(fence_file.metadata.st_ctime_ns)
        or authority.transport_fence_sha256 != fence_hash
        or authority.transport_transaction_id != journal["transaction_id"]
        or authority.transport_transaction_id != fence["transaction_id"]
        or any(journal.get(field) != fence.get(field) for field in immutable_fields)
        or journal["lane"] != authority.lane
        or journal["source_receipt"]["basename"]
        != authority.source_receipt_basename
        or not isinstance(media, dict)
        or media.get("media_ids") != [authority.media_id]
    ):
        raise MediaUploadReceiptError(
            "independent transport handoff authority is stale or mismatched"
        )
    return journal, fence


def bind_media_handoff_to_transport(
    receipt_path: Path,
    confirmation: ConfirmedMediaUpload | None,
    *,
    transport_journal_path: Path,
    transport_fence_path: Path,
    source_receipt_path: Path,
) -> MediaHandoffAuthority:
    """Bind media retirement to a prepublished tweet journal/fence pair.

    ``confirmation`` may be omitted after a hard interruption which already
    removed the confirmed media receipt.  In that case the immutable media
    fence supplies the media transaction identity and the tweet journal
    supplies the confirmed media ID, allowing a fresh interpreter to continue
    retirement without trusting process memory.
    """

    receipt_path = _normalised_path(receipt_path)
    transport_journal_path = _normalised_path(transport_journal_path)
    transport_fence_path = _normalised_path(transport_fence_path)
    source_receipt_path = _normalised_path(source_receipt_path)
    if not (
        receipt_path.parent
        == transport_journal_path.parent
        == transport_fence_path.parent
        == source_receipt_path.parent
    ):
        raise MediaUploadReceiptError("media handoff files must be siblings")
    try:
        journal, journal_file, journal_hash = _transport_owner_snapshot(
            transport_journal_path,
            expected_kind="mrsMThatcher_remote_write_transport_journal",
        )
        fence_owner, fence_file, fence_hash = _transport_owner_snapshot(
            transport_fence_path,
            expected_kind="mrsMThatcher_remote_write_transport_fence",
        )
    except MediaUploadReceiptError:
        raise
    except (FileNotFoundError, OSError) as exc:
        raise MediaUploadReceiptError(
            "tweet transport handoff owner is missing or cannot be inspected"
        ) from exc
    if journal["lifecycle_state"] != "prepared" or fence_owner[
        "lifecycle_state"
    ] != "prepared":
        raise MediaUploadReceiptError(
            "media handoff requires a pre-transport prepared owner pair"
        )
    media = journal.get("remote_payload", {}).get("media")
    media_ids = media.get("media_ids") if isinstance(media, dict) else None
    if (
        journal["transaction_id"] != fence_owner["transaction_id"]
        or journal["lane"] != fence_owner["lane"]
        or journal["remote_payload"] != fence_owner["remote_payload"]
        or journal["remote_payload_sha256"]
        != fence_owner["remote_payload_sha256"]
        or journal["source_receipt"] != fence_owner["source_receipt"]
        or journal["source_receipt"]["basename"] != source_receipt_path.name
        or not isinstance(media_ids, list)
        or len(media_ids) != 1
        or not _MEDIA_ID_RE.fullmatch(str(media_ids[0]))
    ):
        raise MediaUploadReceiptError(
            "tweet transport pair does not bind one confirmed media upload"
        )
    media_id = str(media_ids[0])
    fence_path = fence_path_for_receipt(receipt_path)
    try:
        receipt = _snapshot(receipt_path)
    except FileNotFoundError:
        receipt = None
    try:
        media_fence = _snapshot(fence_path, expected_kind=FENCE_DOCUMENT_KIND)
    except FileNotFoundError:
        media_fence = None
    if receipt is None and media_fence is None:
        raise MediaUploadReceiptError("confirmed media handoff is already retired")
    if receipt is not None and media_fence is None:
        raise MediaUploadReceiptError("confirmed media receipt lost its fence")
    media_document = receipt.document if receipt is not None else media_fence.document
    assert media_document is not None
    if (
        journal["lane"] != media_document["lane"]
        or (receipt is not None and receipt.document["lifecycle_state"] != "confirmed")
        or (receipt is not None and receipt.document["remote_media_id"] != media_id)
        or (media_fence is not None and media_fence.document["lifecycle_state"] != "sending")
        or (
            receipt is not None
            and media_fence is not None
            and receipt.document["transaction_id"]
            != media_fence.document["transaction_id"]
        )
        or (
            receipt is not None
            and media_fence is not None
            and (
                receipt.document["lane"] != media_fence.document["lane"]
                or receipt.document["image"] != media_fence.document["image"]
                or receipt.document["payload_metadata"]
                != media_fence.document["payload_metadata"]
                or receipt.document["payload_metadata_sha256"]
                != media_fence.document["payload_metadata_sha256"]
            )
        )
    ):
        raise MediaUploadReceiptError(
            "confirmed media state does not match tweet transport owner"
        )
    if confirmation is not None:
        if (
            confirmation.lifecycle_state != "confirmed"
            or confirmation.transaction_id != media_document["transaction_id"]
            or confirmation.media_id != media_id
            or confirmation.lane != journal["lane"]
            or receipt is None
            or confirmation.receipt_device != receipt.device
            or confirmation.receipt_inode != receipt.inode
            or confirmation.receipt_ctime_ns != receipt.ctime_ns
            or confirmation.receipt_sha256 != receipt.sha256
            or media_fence is None
            or confirmation.fence_path != str(fence_path)
            or confirmation.fence_device != media_fence.device
            or confirmation.fence_inode != media_fence.inode
            or confirmation.fence_ctime_ns != media_fence.ctime_ns
            or confirmation.fence_sha256 != media_fence.sha256
        ):
            raise MediaUploadReceiptError("confirmed media identity is stale")
    authority = MediaHandoffAuthority(
        media_transaction_id=str(media_document["transaction_id"]),
        media_id=media_id,
        lane=str(journal["lane"]),
        media_receipt_path=str(receipt_path),
        transport_transaction_id=str(journal["transaction_id"]),
        transport_journal_path=str(transport_journal_path),
        transport_journal_device=int(journal_file.metadata.st_dev),
        transport_journal_inode=int(journal_file.metadata.st_ino),
        transport_journal_ctime_ns=int(journal_file.metadata.st_ctime_ns),
        transport_journal_sha256=journal_hash,
        transport_fence_path=str(transport_fence_path),
        transport_fence_device=int(fence_file.metadata.st_dev),
        transport_fence_inode=int(fence_file.metadata.st_ino),
        transport_fence_ctime_ns=int(fence_file.metadata.st_ctime_ns),
        transport_fence_sha256=fence_hash,
        source_receipt_basename=source_receipt_path.name,
    )
    _validate_transport_handoff(authority)
    return authority


def inspect_media_retirement_state(
    receipt_path: Path,
    handoff: MediaHandoffAuthority,
) -> MediaRetirementState:
    """Inspect a retirement step without mutating or trusting process state."""

    receipt_path = _normalised_path(receipt_path)
    if handoff.media_receipt_path != str(receipt_path):
        raise MediaUploadReceiptError("media handoff receipt path is stale")
    _validate_transport_handoff(handoff)
    fence_path = fence_path_for_receipt(receipt_path)
    try:
        receipt = _snapshot(receipt_path)
    except FileNotFoundError:
        receipt = None
    try:
        fence = _snapshot(fence_path, expected_kind=FENCE_DOCUMENT_KIND)
    except FileNotFoundError:
        fence = None
    if receipt is not None and fence is None:
        raise MediaUploadReceiptError(
            "media fence disappeared before the confirmed receipt"
        )
    for item in (receipt, fence):
        if item is not None and (
            item.document["transaction_id"] != handoff.media_transaction_id
            or item.document["lane"] != handoff.lane
        ):
            raise MediaUploadReceiptError("media retirement generation changed")
    if receipt is not None and (
        receipt.document["lifecycle_state"] != "confirmed"
        or receipt.document["remote_media_id"] != handoff.media_id
    ):
        raise MediaUploadReceiptError("media retirement receipt is not confirmed")
    if fence is not None and fence.document["lifecycle_state"] != "sending":
        raise MediaUploadReceiptError("media retirement fence changed")
    state = (
        "not_started"
        if receipt is not None and fence is not None
        else "receipt_retired"
        if fence is not None
        else "retired"
    )
    return MediaRetirementState(
        state=state,
        media_receipt_present=receipt is not None,
        media_fence_present=fence is not None,
        handoff_journal_present=True,
        handoff_fence_present=True,
        media_transaction_id=handoff.media_transaction_id,
        media_id=handoff.media_id,
        lane=handoff.lane,
    )


def retire_confirmed_media_upload(
    receipt_path: Path,
    handoff: MediaHandoffAuthority,
    *,
    mutation_authority: TransactionMutationAuthority | None = None,
) -> MediaRetirementState:
    """Idempotently retire media companions under an independent owner pair.

    The prepublished tweet journal and fence are revalidated before and after
    every destructive step.  If an unlink or parent-directory fsync is
    interrupted, a fresh interpreter can reconstruct ``handoff`` from the
    owner pair and the surviving immutable media fence, inspect the exact
    phase, and continue without relying on the vanished main-receipt pathname.
    """

    require_transaction_mutation_authority(
        mutation_authority,
        operation="confirmed media upload retirement",
    )
    receipt_path = _normalised_path(receipt_path)
    state = inspect_media_retirement_state(receipt_path, handoff)
    directory_fd = _open_directory(receipt_path.parent)
    try:
        if state.media_receipt_present:
            current = _required_snapshot(receipt_path)
            if (
                current.document["transaction_id"] != handoff.media_transaction_id
                or current.document["remote_media_id"] != handoff.media_id
                or current.document["lifecycle_state"] != "confirmed"
            ):
                raise MediaUploadReceiptError(
                    "media receipt changed immediately before retirement"
                )
            os.unlink(receipt_path.name, dir_fd=directory_fd)
            os.fsync(directory_fd)
            state = inspect_media_retirement_state(receipt_path, handoff)
        if state.media_fence_present:
            fence_path = fence_path_for_receipt(receipt_path)
            current_fence = _required_fence_snapshot(fence_path)
            if (
                current_fence.document["transaction_id"]
                != handoff.media_transaction_id
                or current_fence.document["lifecycle_state"] != "sending"
            ):
                raise MediaUploadReceiptError(
                    "media fence changed immediately before retirement"
                )
            os.unlink(fence_path.name, dir_fd=directory_fd)
            os.fsync(directory_fd)
            state = inspect_media_retirement_state(receipt_path, handoff)
        if state.state != "retired":
            raise MediaUploadReceiptError("media retirement did not complete")
        return state
    finally:
        os.close(directory_fd)


def resume_interrupted_confirmed_media_retirement(
    receipt_path: Path,
    *,
    mutation_authority: TransactionMutationAuthority | None = None,
    transport_journal_path: Path,
    transport_fence_path: Path,
    source_receipt_path: Path,
) -> MediaRetirementState | None:
    """Finish only the restart-proved ``receipt_retired`` media phase.

    This is the narrow fresh-process entry point for a crash after the
    confirmed media receipt was durably removed but before its immutable media
    fence was retired.  The independently prepared tweet journal/fence pair
    remains untouched and therefore continues to block every remote-write lane;
    this helper neither aborts nor retransmits that public-create transaction.

    Absence of both media companions is not treated as proof that this helper
    owns a transition, while a still-present media receipt belongs to the normal
    in-process handoff path.  Every malformed, ambiguous, or mismatched state
    raises instead of being selected heuristically.
    """

    require_transaction_mutation_authority(
        mutation_authority,
        operation="interrupted confirmed media retirement resume",
    )
    receipt_path = _normalised_path(receipt_path)
    receipt = inspect_media_upload_receipt(receipt_path)
    if receipt is not None:
        return None
    fence_path = fence_path_for_receipt(receipt_path)
    try:
        _snapshot(fence_path, expected_kind=FENCE_DOCUMENT_KIND)
    except FileNotFoundError:
        return None

    handoff = bind_media_handoff_to_transport(
        receipt_path,
        None,
        transport_journal_path=transport_journal_path,
        transport_fence_path=transport_fence_path,
        source_receipt_path=source_receipt_path,
    )
    state = inspect_media_retirement_state(receipt_path, handoff)
    if state.state != "receipt_retired":
        raise MediaUploadReceiptError(
            "fresh-process media retirement is not in receipt-retired state"
        )
    return retire_confirmed_media_upload(
        receipt_path,
        handoff,
        mutation_authority=mutation_authority,
    )
