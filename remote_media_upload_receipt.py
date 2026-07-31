#!/usr/bin/env python3
"""Restart-visible authority and receipt for one remote media upload.

The caller chooses a lane-specific receipt pathname.  Publishing both a
``sending`` receipt and an immutable payload-bound companion fence must precede
transport.  A hard process loss after publication therefore leaves two
independent unresolved pathnames for the next process.  The receipt lifecycle
is::

    sending -> confirmed -> retired

``retired`` is represented by absence of both media companions, but retirement
is permitted only while an exact main-post receipt is protected by a hard-link
guard.  The media receipt is removed first and the immutable fence second.  At
least one durable barrier consequently remains throughout the supported
retirement transition.

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


SCHEMA_VERSION = 1
DOCUMENT_KIND = "mrsMThatcher_remote_media_upload_receipt"
FENCE_DOCUMENT_KIND = "mrsMThatcher_remote_media_upload_fence"
RECEIPT_MODE = 0o600
RECEIPT_MAX_BYTES = 128 * 1024
IMAGE_MAX_BYTES = 256 * 1024 * 1024
TRANSITION_PREFIX = ".remote-media-upload.transition."
RETIREMENT_GUARD_PREFIX = ".remote-media-upload.main-post-guard."
_RENAME_EXCHANGE = 2
_SHA256_RE = re.compile(r"[0-9a-f]{64}")
_MIME_RE = re.compile(r"image/[a-z0-9][a-z0-9.+-]{0,63}")
_MEDIA_ID_RE = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
_ALLOWED_LANES = frozenset({"quote_image", "daily_meme"})
_consumed_authorities: set[
    tuple[str, int, int, str, int, int, str, str]
] = set()
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
    receipt_sha256: str
    fence_path: str
    fence_device: int
    fence_inode: int
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
    receipt_sha256: str
    fence_path: str
    fence_device: int
    fence_inode: int
    fence_sha256: str
    lane: str
    media_id: str
    lifecycle_state: str = "confirmed"


@dataclass(frozen=True)
class MediaReceiptSnapshot:
    """A stable, strict and canonical receipt observation."""

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


def _replace_exact(path: Path, *, expected: bytes, replacement: bytes) -> None:
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
        if displaced.data != expected or current.data != replacement:
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
            if current is not None and current.data == expected:
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
        value.get("schema_version") != SCHEMA_VERSION
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
        != {"basename", "device", "inode", "size", "sha256", "mime_type"}
        or not isinstance(image.get("basename"), str)
        or Path(image["basename"]).name != image["basename"]
        or type(image.get("device")) is not int
        or type(image.get("inode")) is not int
        or type(image.get("size")) is not int
        or image["device"] < 0
        or image["inode"] <= 0
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
        receipt_sha256=snapshot.sha256,
        fence_path=str(fence_path),
        fence_device=fence.device,
        fence_inode=fence.inode,
        fence_sha256=fence.sha256,
        lane=str(snapshot.document["lane"]),
        media_id=str(snapshot.document["remote_media_id"]),
    )


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
    return MediaUploadAuthority(
        transaction_id=transaction_id,
        receipt_path=str(receipt_path),
        receipt_device=snapshot.device,
        receipt_inode=snapshot.inode,
        receipt_sha256=snapshot.sha256,
        fence_path=str(fence_path),
        fence_device=fence.device,
        fence_inode=fence.inode,
        fence_sha256=fence.sha256,
        image_sha256=image_hash,
        payload_metadata_sha256=metadata_hash,
        lane=lane,
    )


def consume_media_upload_authority(
    receipt_path: Path,
    authority: MediaUploadAuthority,
    *,
    image_path: Path,
    lane: str,
    mime_type: str,
    payload_metadata: Mapping[str, Any],
) -> None:
    """Consume exact dev/inode/hash-bound authority immediately pre-transport."""

    receipt_path = _normalised_path(receipt_path)
    image_path = _normalised_path(image_path)
    snapshot = _required_snapshot(receipt_path)
    fence_path = fence_path_for_receipt(receipt_path)
    fence = _required_fence_snapshot(fence_path)
    metadata_hash = _metadata_hash(dict(payload_metadata))
    if (
        authority.lifecycle_state != "sending"
        or authority.receipt_path != str(receipt_path)
        or authority.receipt_device != snapshot.device
        or authority.receipt_inode != snapshot.inode
        or authority.receipt_sha256 != snapshot.sha256
        or authority.fence_path != str(fence_path)
        or authority.fence_device != fence.device
        or authority.fence_inode != fence.inode
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
    try:
        image = _read_stable_regular(image_path, maximum=IMAGE_MAX_BYTES)
    except FileNotFoundError as exc:
        raise MediaUploadReceiptError("bound source image disappeared") from exc
    expected_image = snapshot.document["image"]
    if (
        image_path.name != expected_image["basename"]
        or int(image.metadata.st_dev) != expected_image["device"]
        or int(image.metadata.st_ino) != expected_image["inode"]
        or len(image.data) != expected_image["size"]
        or hashlib.sha256(image.data).hexdigest() != expected_image["sha256"]
        or authority.image_sha256 != expected_image["sha256"]
    ):
        raise MediaUploadReceiptError("source image identity changed before transport")
    key = (
        str(receipt_path),
        snapshot.device,
        snapshot.inode,
        snapshot.sha256,
        fence.device,
        fence.inode,
        fence.sha256,
        authority.transaction_id,
    )
    with _authority_lock:
        if key in _consumed_authorities:
            raise MediaUploadReceiptError("media-upload authority was already consumed")
        _consumed_authorities.add(key)


def confirm_media_upload(
    receipt_path: Path,
    authority: MediaUploadAuthority,
    *,
    media_id: str,
) -> ConfirmedMediaUpload:
    """Atomically replace exact ``sending`` state with a confirmed media ID."""

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
        snapshot.sha256,
        fence.device,
        fence.inode,
        fence.sha256,
        authority.transaction_id,
    )
    with _authority_lock:
        consumed = key in _consumed_authorities
    if (
        not consumed
        or authority.lifecycle_state != "sending"
        or authority.receipt_path != str(receipt_path)
        or authority.receipt_device != snapshot.device
        or authority.receipt_inode != snapshot.inode
        or authority.receipt_sha256 != snapshot.sha256
        or authority.fence_path != str(fence_path)
        or authority.fence_device != fence.device
        or authority.fence_inode != fence.inode
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
    )
    confirmed = _required_snapshot(receipt_path)
    if confirmed.data != replacement_data:
        raise MediaUploadReceiptError("confirmed media receipt changed")
    return ConfirmedMediaUpload(
        transaction_id=authority.transaction_id,
        receipt_path=str(receipt_path),
        receipt_device=confirmed.device,
        receipt_inode=confirmed.inode,
        receipt_sha256=confirmed.sha256,
        fence_path=str(fence_path),
        fence_device=fence.device,
        fence_inode=fence.inode,
        fence_sha256=fence.sha256,
        lane=authority.lane,
        media_id=str(media_id),
    )


def _inspect_main_post_receipt(
    path: Path,
    *,
    expected: bytes,
    allowed_link_counts: frozenset[int],
) -> _StableFile:
    if not expected or len(expected) > RECEIPT_MAX_BYTES:
        raise MediaUploadReceiptError("expected main-post receipt bytes are invalid")
    inspected = _read_stable_regular(
        path,
        maximum=RECEIPT_MAX_BYTES,
        allowed_link_counts=allowed_link_counts,
    )
    # Parse strictly as an object, but retain exact bytes as the authority.
    value = _parse_strict_json(inspected.data, label="main-post receipt")
    if not isinstance(value, dict):
        raise MediaUploadReceiptError("main-post receipt must be a JSON object")
    if inspected.data != expected:
        raise MediaUploadReceiptError("main-post receipt does not match expected bytes")
    return inspected


def retire_confirmed_media_upload(
    receipt_path: Path,
    confirmation: ConfirmedMediaUpload,
    *,
    main_post_receipt_path: Path,
    expected_main_post_receipt_bytes: bytes,
) -> None:
    """Retire media state while a hard-linked exact main receipt remains.

    The two receipts must share a directory.  A guard hard link is made durable
    before the media receipt is removed.  At every interruption point at least
    the media receipt, main-post pathname, or its exact hard-link guard remains.
    """

    receipt_path = _normalised_path(receipt_path)
    main_post_receipt_path = _normalised_path(main_post_receipt_path)
    if receipt_path.parent != main_post_receipt_path.parent:
        raise MediaUploadReceiptError("media and main-post receipts must be siblings")
    snapshot = _required_snapshot(receipt_path)
    fence_path = fence_path_for_receipt(receipt_path)
    fence = _required_fence_snapshot(fence_path)
    if (
        confirmation.lifecycle_state != "confirmed"
        or confirmation.receipt_path != str(receipt_path)
        or confirmation.receipt_device != snapshot.device
        or confirmation.receipt_inode != snapshot.inode
        or confirmation.receipt_sha256 != snapshot.sha256
        or confirmation.fence_path != str(fence_path)
        or confirmation.fence_device != fence.device
        or confirmation.fence_inode != fence.inode
        or confirmation.fence_sha256 != fence.sha256
        or confirmation.transaction_id != snapshot.document["transaction_id"]
        or confirmation.transaction_id != fence.document["transaction_id"]
        or confirmation.lane != snapshot.document["lane"]
        or confirmation.lane != fence.document["lane"]
        or confirmation.media_id != snapshot.document["remote_media_id"]
        or snapshot.document["lifecycle_state"] != "confirmed"
        or fence.document["lifecycle_state"] != "sending"
    ):
        raise MediaUploadReceiptError("confirmed media receipt identity is stale")
    main_value = _parse_strict_json(
        expected_main_post_receipt_bytes,
        label="main-post receipt",
    )
    if not isinstance(main_value, dict):
        raise MediaUploadReceiptError("main-post receipt must be a JSON object")
    selected_identity = main_value.get("selected_identity")
    selected_basename = (
        selected_identity.get(
            "image_basename"
            if confirmation.lane == "quote_image"
            else "meme_basename"
        )
        if isinstance(selected_identity, dict)
        else None
    )
    if (
        main_value.get("lifecycle_state") != "sending"
        or main_value.get("lane") != confirmation.lane
        or main_value.get("media_ids") != [confirmation.media_id]
        or selected_basename != snapshot.document["image"]["basename"]
    ):
        raise MediaUploadReceiptError(
            "main-post receipt does not bind the confirmed media upload"
        )
    main_receipt = _inspect_main_post_receipt(
        main_post_receipt_path,
        expected=expected_main_post_receipt_bytes,
        allowed_link_counts=frozenset({1}),
    )
    token = secrets.token_hex(16)
    guard_name = f"{RETIREMENT_GUARD_PREFIX}{token}"
    guard_path = receipt_path.parent / guard_name
    directory_fd = _open_directory(receipt_path.parent)
    try:
        os.link(
            main_post_receipt_path.name,
            guard_name,
            src_dir_fd=directory_fd,
            dst_dir_fd=directory_fd,
            follow_symlinks=False,
        )
        os.fsync(directory_fd)
        main_linked = _inspect_main_post_receipt(
            main_post_receipt_path,
            expected=expected_main_post_receipt_bytes,
            allowed_link_counts=frozenset({2}),
        )
        guard = _inspect_main_post_receipt(
            guard_path,
            expected=expected_main_post_receipt_bytes,
            allowed_link_counts=frozenset({2}),
        )
        expected_identity = (
            int(main_receipt.metadata.st_dev),
            int(main_receipt.metadata.st_ino),
        )
        if (
            (int(main_linked.metadata.st_dev), int(main_linked.metadata.st_ino))
            != expected_identity
            or (int(guard.metadata.st_dev), int(guard.metadata.st_ino))
            != expected_identity
        ):
            raise MediaUploadReceiptError("main-post retirement guard identity changed")
        current_media = _required_snapshot(receipt_path)
        if (
            current_media.device != snapshot.device
            or current_media.inode != snapshot.inode
            or current_media.data != snapshot.data
        ):
            raise MediaUploadReceiptError("media receipt changed before retirement")
        os.unlink(receipt_path.name, dir_fd=directory_fd)
        os.fsync(directory_fd)
        main_linked = _inspect_main_post_receipt(
            main_post_receipt_path,
            expected=expected_main_post_receipt_bytes,
            allowed_link_counts=frozenset({2}),
        )
        guard = _inspect_main_post_receipt(
            guard_path,
            expected=expected_main_post_receipt_bytes,
            allowed_link_counts=frozenset({2}),
        )
        if (
            (int(main_linked.metadata.st_dev), int(main_linked.metadata.st_ino))
            != expected_identity
            or (int(guard.metadata.st_dev), int(guard.metadata.st_ino))
            != expected_identity
        ):
            raise MediaUploadReceiptError("main-post receipt changed during retirement")
        current_fence = _required_fence_snapshot(fence_path)
        if (
            current_fence.device != fence.device
            or current_fence.inode != fence.inode
            or current_fence.data != fence.data
        ):
            raise MediaUploadReceiptError("media fence changed before retirement")
        os.unlink(fence_path.name, dir_fd=directory_fd)
        os.fsync(directory_fd)
        main_linked = _inspect_main_post_receipt(
            main_post_receipt_path,
            expected=expected_main_post_receipt_bytes,
            allowed_link_counts=frozenset({2}),
        )
        guard = _inspect_main_post_receipt(
            guard_path,
            expected=expected_main_post_receipt_bytes,
            allowed_link_counts=frozenset({2}),
        )
        if (
            (int(main_linked.metadata.st_dev), int(main_linked.metadata.st_ino))
            != expected_identity
            or (int(guard.metadata.st_dev), int(guard.metadata.st_ino))
            != expected_identity
        ):
            raise MediaUploadReceiptError(
                "main-post receipt changed during fence retirement"
            )
        os.unlink(guard_name, dir_fd=directory_fd)
        os.fsync(directory_fd)
        final_main = _inspect_main_post_receipt(
            main_post_receipt_path,
            expected=expected_main_post_receipt_bytes,
            allowed_link_counts=frozenset({1}),
        )
        if (
            int(final_main.metadata.st_dev),
            int(final_main.metadata.st_ino),
        ) != expected_identity:
            raise MediaUploadReceiptError("main-post receipt changed after retirement")
    finally:
        os.close(directory_fd)
