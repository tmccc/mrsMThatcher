"""Provide durable JSON file I/O through explicit current root dependencies.

Seven root adapters supply current modules, callbacks, limits, logger and error
classes. The state namespace predicate receives its maximum explicitly; only
the root keeps the original definition-time default. Original bodies retain
the distinct state and receipt permissions, stable metadata/byte checks,
strict receipt parsing, ordinary atomic JSON encoding and write/fsync/close
ordering. Canonical receipt serialization is owned here and used directly by
its consumers. State/receipt/marker policy and persistence authority remain
external. Explicit calls inspect supplied paths and read or write files; this
owner retains no callbacks, configuration, state or descriptors and performs no
import-time file, environment, provider or RNG work.
"""

from __future__ import annotations

import json
import stat
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any


def canonical_atomic_json_bytes(value: object) -> bytes:
    """Return the exact byte representation used by ``atomic_write_json``."""
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("utf-8")


def durable_state_namespace_is_owned_single_link_file(
    path: Path,
    *,
    maximum_bytes: int | None,
    os: ModuleType,
) -> bool:
    """Return true only for one current-owner ordinary-file namespace entry."""

    try:
        metadata = os.lstat(path)
    except (FileNotFoundError, OSError):
        return False
    return bool(
        stat.S_ISREG(metadata.st_mode)
        and metadata.st_nlink == 1
        and metadata.st_uid == os.geteuid()
        and not (stat.S_IMODE(metadata.st_mode) & 0o022)
        and (
            maximum_bytes is None
            or metadata.st_size <= maximum_bytes
        )
    )


def read_stable_owned_json_bytes_no_follow(
    path: Path,
    *,
    DURABLE_RUNTIME_JSON_MAX_BYTES: int,
    UnsafeDurableStateNamespace: type[Exception],
    durable_state_namespace_is_owned_single_link_file: Callable[..., bool],
    os: ModuleType,
) -> tuple[bool, bytes | None]:
    """Read one bounded stable owned JSON authority without following links."""

    from mrs_bot_state_generation import directory_identity

    try:
        directory = directory_identity(path.parent)
    except (OSError, ValueError) as exc:
        raise UnsafeDurableStateNamespace('unsafe durable JSON directory') from exc

    try:
        before = os.lstat(path)
    except FileNotFoundError:
        return False, None
    except OSError as exc:
        raise UnsafeDurableStateNamespace(
            f"durable JSON namespace cannot be inspected: {path}"
        ) from exc
    if not durable_state_namespace_is_owned_single_link_file(path):
        raise UnsafeDurableStateNamespace(
            f"durable JSON namespace is not one bounded owned ordinary file: {path}"
        )
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise UnsafeDurableStateNamespace(
            "O_NOFOLLOW is required for durable JSON reads"
        )
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError as exc:
        raise UnsafeDurableStateNamespace(
            f"durable JSON namespace changed before opening: {path}"
        ) from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) & 0o022
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or opened.st_size != before.st_size
            or opened.st_size > DURABLE_RUNTIME_JSON_MAX_BYTES
        ):
            raise UnsafeDurableStateNamespace(
                f"durable JSON identity changed while opening: {path}"
            )
        chunks: list[bytes] = []
        total = 0
        while True:
            block = os.read(
                descriptor,
                min(
                    64 * 1024,
                    DURABLE_RUNTIME_JSON_MAX_BYTES + 1 - total,
                ),
            )
            if not block:
                break
            chunks.append(block)
            total += len(block)
            if total > DURABLE_RUNTIME_JSON_MAX_BYTES:
                raise UnsafeDurableStateNamespace(
                    f"durable JSON exceeds its byte limit: {path}"
                )
        reopened_data = os.pread(descriptor, opened.st_size + 1, 0)
        after_fd = os.fstat(descriptor)
        try:
            after_path = os.lstat(path)
        except OSError as exc:
            raise UnsafeDurableStateNamespace(
                f"durable JSON disappeared while reading: {path}"
            ) from exc
    finally:
        os.close(descriptor)
    stable_fields = (
        "st_dev",
        "st_ino",
        "st_mode",
        "st_nlink",
        "st_uid",
        "st_size",
        "st_ctime_ns",
        "st_mtime_ns",
    )
    if (
        any(getattr(before, field) != getattr(opened, field) for field in stable_fields)
        or any(
            getattr(opened, field) != getattr(after_fd, field)
            for field in stable_fields
        )
        or any(
            getattr(opened, field) != getattr(after_path, field)
            for field in stable_fields
        )
    ):
        raise UnsafeDurableStateNamespace(
            f"durable JSON changed while reading: {path}"
        )
    data = b"".join(chunks)
    if (len(data) != opened.st_size or reopened_data != data
            or directory_identity(path.parent) != directory):
        raise UnsafeDurableStateNamespace(
            f"durable JSON changed while reading: {path}"
        )
    return True, data


def fsync_parent_dir(
    path: Path,
    *,
    strict: bool = False,
    log: Any,
    os: ModuleType,
) -> None:
    """Synchronise parent dir."""
    try:
        fd = os.open(str(path.parent), os.O_RDONLY)
    except Exception:
        if strict:
            raise
        log.debug("Could not open parent directory for fsync: %s", path.parent, exc_info=True)
        return
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def atomic_write_json(
    path: Path,
    value: object,
    *,
    durable: bool = False,
    DURABLE_RUNTIME_JSON_MAX_BYTES: int = 64 * 1024 * 1024,
    fsync_parent_dir: Callable[..., None],
    os: ModuleType,
    tempfile: ModuleType,
) -> None:
    """Write JSON atomically and optionally durably."""
    data = canonical_atomic_json_bytes(value)
    if len(data) > DURABLE_RUNTIME_JSON_MAX_BYTES:
        raise ValueError("durable JSON document exceeds reader byte limit")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(data)
            if durable:
                handle.flush()
                os.fsync(handle.fileno())
        os.replace(temporary, path)
        if durable:
            fsync_parent_dir(path, strict=True)
    except BaseException:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def _strict_receipt_json_bytes(
    data: bytes,
    *,
    UnsafeReceiptNamespace: type[Exception],
) -> object:
    """Parse one receipt without duplicate names or non-finite constants."""

    def reject_duplicate_names(pairs: list[tuple[str, object]]) -> dict:
        value: dict[str, object] = {}
        for key, item in pairs:
            if key in value:
                raise UnsafeReceiptNamespace(
                    f"receipt contains duplicate JSON object name: {key}"
                )
            value[key] = item
        return value

    def reject_constant(value: str) -> object:
        raise UnsafeReceiptNamespace(
            f"receipt contains unsupported JSON constant: {value}"
        )

    try:
        return json.loads(
            data.decode("utf-8"),
            object_pairs_hook=reject_duplicate_names,
            parse_constant=reject_constant,
        )
    except UnsafeReceiptNamespace:
        raise
    except Exception as exc:
        raise UnsafeReceiptNamespace("receipt is not valid UTF-8 JSON") from exc


def load_receipt_json_no_follow(
    path: Path,
    *,
    RECEIPT_JSON_MAX_BYTES: int,
    UnsafeReceiptNamespace: type[Exception],
    _strict_receipt_json_bytes: Callable[[bytes], object],
    os: ModuleType,
) -> tuple[bool, object | None]:
    """Read one bounded stable ordinary receipt, distinguishing true absence.

    A dangling symlink, directory, FIFO, replacement or disappearance after
    initial observation is an unsafe existing authority, never an absent file.
    """
    from mrs_bot_state_generation import directory_identity

    try:
        directory = directory_identity(path.parent)
    except (OSError, ValueError) as exc:
        raise UnsafeReceiptNamespace('unsafe receipt directory') from exc
    try:
        before = os.lstat(path)
    except FileNotFoundError:
        return False, None
    except OSError as exc:
        raise UnsafeReceiptNamespace("receipt namespace cannot be inspected") from exc
    if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1:
        raise UnsafeReceiptNamespace(
            "receipt namespace entry is not one single-link ordinary file"
        )
    if before.st_uid != os.geteuid() or stat.S_IMODE(before.st_mode) != 0o600:
        raise UnsafeReceiptNamespace(
            "receipt namespace entry has unsafe ownership or permissions"
        )
    if before.st_size > RECEIPT_JSON_MAX_BYTES:
        raise UnsafeReceiptNamespace("receipt exceeds its byte limit")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise UnsafeReceiptNamespace("O_NOFOLLOW is required for receipt reads")
    try:
        descriptor = os.open(
            path,
            os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0),
        )
    except OSError as exc:
        raise UnsafeReceiptNamespace("receipt changed before it could be opened") from exc
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_uid != os.geteuid()
            or stat.S_IMODE(opened.st_mode) != 0o600
            or opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
        ):
            raise UnsafeReceiptNamespace("receipt identity changed while opening")
        chunks: list[bytes] = []
        total = 0
        while True:
            block = os.read(
                descriptor,
                min(64 * 1024, RECEIPT_JSON_MAX_BYTES + 1 - total),
            )
            if not block:
                break
            chunks.append(block)
            total += len(block)
            if total > RECEIPT_JSON_MAX_BYTES:
                raise UnsafeReceiptNamespace("receipt exceeds its byte limit")
        after_fd = os.fstat(descriptor)
        try:
            after_path = os.lstat(path)
        except OSError as exc:
            raise UnsafeReceiptNamespace("receipt disappeared while reading") from exc
        if (
            after_fd.st_dev != opened.st_dev
            or after_fd.st_ino != opened.st_ino
            or after_fd.st_nlink != opened.st_nlink
            or after_fd.st_size != opened.st_size
            or after_fd.st_ctime_ns != opened.st_ctime_ns
            or after_fd.st_mtime_ns != opened.st_mtime_ns
            or not stat.S_ISREG(after_path.st_mode)
            or after_path.st_nlink != 1
            or after_path.st_uid != os.geteuid()
            or stat.S_IMODE(after_path.st_mode) != 0o600
            or after_path.st_dev != opened.st_dev
            or after_path.st_ino != opened.st_ino
            or after_path.st_size != opened.st_size
            or after_path.st_ctime_ns != opened.st_ctime_ns
            or after_path.st_mtime_ns != opened.st_mtime_ns
        ):
            raise UnsafeReceiptNamespace("receipt changed while reading")
    finally:
        os.close(descriptor)
    data = b"".join(chunks)
    if len(data) != opened.st_size or directory_identity(path.parent) != directory:
        raise UnsafeReceiptNamespace("receipt read was incomplete")
    value = _strict_receipt_json_bytes(data)
    if canonical_atomic_json_bytes(value) != data:
        raise UnsafeReceiptNamespace("receipt is not canonical JSON")
    return True, value


def durable_create_receipt_json(
    path: Path,
    value: object,
    *,
    RECEIPT_JSON_MAX_BYTES: int,
    UnsafeReceiptNamespace: type[Exception],
    fsync_parent_dir: Callable[..., None],
    os: ModuleType,
) -> None:
    """Publish a new receipt with O_EXCL and prove its exact stable bytes."""
    data = canonical_atomic_json_bytes(value)
    if len(data) > RECEIPT_JSON_MAX_BYTES:
        raise UnsafeReceiptNamespace("receipt exceeds its byte limit")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise UnsafeReceiptNamespace("O_NOFOLLOW is required for receipt creation")
    descriptor = os.open(
        path,
        os.O_RDWR
        | os.O_CREAT
        | os.O_EXCL
        | nofollow
        | getattr(os, "O_CLOEXEC", 0),
        0o600,
    )
    try:
        os.fchmod(descriptor, 0o600)
        offset = 0
        while offset < len(data):
            written = os.write(descriptor, data[offset:])
            if written <= 0:
                raise OSError("short write while publishing receipt")
            offset += written
        os.fsync(descriptor)
        created = os.fstat(descriptor)
        if (
            not stat.S_ISREG(created.st_mode)
            or created.st_nlink != 1
            or created.st_size != len(data)
        ):
            raise UnsafeReceiptNamespace(
                "new receipt has unsafe metadata before publication acknowledgement"
            )
        fsync_parent_dir(path, strict=True)
        try:
            current = os.lstat(path)
        except OSError as exc:
            raise UnsafeReceiptNamespace(
                "new receipt disappeared before publication acknowledgement"
            ) from exc
        reopened_data = os.pread(descriptor, len(data) + 1, 0)
        after_read = os.fstat(descriptor)
        try:
            current_after_read = os.lstat(path)
        except OSError as exc:
            raise UnsafeReceiptNamespace(
                "new receipt disappeared during publication acknowledgement"
            ) from exc
        stable_fields = (
            "st_dev",
            "st_ino",
            "st_mode",
            "st_nlink",
            "st_size",
            "st_ctime_ns",
            "st_mtime_ns",
        )
        if (
            reopened_data != data
            or any(
                getattr(created, field) != getattr(after_read, field)
                or getattr(created, field) != getattr(current, field)
                or getattr(created, field) != getattr(current_after_read, field)
                for field in stable_fields
            )
        ):
            raise UnsafeReceiptNamespace(
                "new receipt changed before publication acknowledgement"
            )
    finally:
        os.close(descriptor)
