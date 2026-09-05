"""Stable file observations and strict/canonical JSON input primitives.

Only explicitly supplied paths are read. Sibling-dependent operations receive
named callbacks from the digest at invocation time; no callbacks are retained.
Native-number and Decimal parsing and the two canonical encodings remain
separate contracts. Importing this module performs no file or runtime access.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import stat
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple


def file_sha256(path: Path) -> str:
    """Return the file SHA-256."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _stable_file_identity(metadata: os.stat_result) -> Tuple[int, ...]:
    """Return fields that bind one read-only filesystem observation."""

    return (
        int(metadata.st_dev),
        int(metadata.st_ino),
        int(metadata.st_mode),
        int(metadata.st_nlink),
        int(metadata.st_uid),
        int(metadata.st_size),
        int(metadata.st_mtime_ns),
        int(metadata.st_ctime_ns),
    )


def read_stable_regular_snapshot(
    path: Path,
    *,
    maximum: int,
    stable_file_identity: Callable[[os.stat_result], Tuple[int, ...]],
    require_private: bool = False,
) -> Tuple[bytes, os.stat_result]:
    """Read bytes and metadata from one stable no-follow file observation."""

    path = Path(path)
    before_path = os.lstat(path)
    if not stat.S_ISREG(before_path.st_mode):
        raise ValueError(f"not a regular file: {path.name}")
    if require_private and (
        before_path.st_nlink != 1
        or before_path.st_uid != os.geteuid()
        or stat.S_IMODE(before_path.st_mode) != 0o600
    ):
        raise ValueError(f"unsafe private file metadata: {path.name}")
    if before_path.st_size > maximum:
        raise ValueError(f"file exceeds {maximum} bytes: {path.name}")
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    if not nofollow:
        raise RuntimeError("O_NOFOLLOW is unavailable")
    descriptor = os.open(
        path,
        os.O_RDONLY | nofollow | getattr(os, "O_CLOEXEC", 0),
    )
    try:
        before_fd = os.fstat(descriptor)
        if stable_file_identity(before_fd) != stable_file_identity(before_path):
            raise RuntimeError(f"path changed before open: {path.name}")
        chunks: List[bytes] = []
        observed = 0
        while observed <= maximum:
            chunk = os.read(descriptor, min(8192, maximum + 1 - observed))
            if not chunk:
                break
            chunks.append(chunk)
            observed += len(chunk)
        data = b"".join(chunks)
        after_fd = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    after_path = os.lstat(path)
    if (
        len(data) > maximum
        or len(data) != before_fd.st_size
        or stable_file_identity(before_fd) != stable_file_identity(after_fd)
        or stable_file_identity(after_fd) != stable_file_identity(after_path)
    ):
        raise RuntimeError(f"file changed while read: {path.name}")
    return data, after_fd


def read_stable_regular_bytes(
    path: Path,
    *,
    maximum: int,
    read_snapshot: Callable[..., Tuple[bytes, os.stat_result]],
) -> bytes:
    """Read one bounded regular file twice-bound to its no-follow pathname."""

    data, _metadata = read_snapshot(path, maximum=maximum)
    return data


def read_stable_private_json_bytes(
    path: Path,
    *,
    maximum: int,
    read_snapshot: Callable[..., Tuple[bytes, os.stat_result]],
) -> bytes:
    """Read one stable, owned, single-link mode-0600 JSON authority."""

    data, metadata = read_snapshot(
        path,
        maximum=maximum,
        require_private=True,
    )
    if (
        metadata.st_nlink != 1
        or metadata.st_uid != os.geteuid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise ValueError(f"unsafe private file metadata: {Path(path).name}")
    return data


def canonical_atomic_json_bytes(value: Any) -> bytes:
    """Return the canonical encoding used by conversational receipts."""

    return (json.dumps(value, indent=2, sort_keys=True) + "\n").encode("utf-8")


def canonical_private_json_bytes(value: Any) -> bytes:
    """Return the canonical encoding used by historical reply history."""

    return (
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _strict_json_object(data: bytes, *, label: str) -> Dict[str, Any]:
    """Parse one duplicate-free, finite JSON object."""

    def reject_constant(value: str) -> None:
        raise ValueError(f"{label} contains non-finite number {value}")

    def pairs(items: List[Tuple[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"{label} contains duplicate key {key!r}")
            result[key] = value
        return result

    value = json.loads(
        data.decode("utf-8"),
        object_pairs_hook=pairs,
        parse_float=Decimal,
        parse_constant=reject_constant,
    )
    if not isinstance(value, dict):
        raise ValueError(f"{label} root is not an object")
    return value


def _strict_native_json_value(data: bytes, *, label: str) -> Any:
    """Parse duplicate-free finite UTF-8 JSON with ordinary float types."""

    def reject_constant(value: str) -> None:
        raise ValueError(f"{label} contains non-finite number {value}")

    def parse_finite_float(value: str) -> float:
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError(f"{label} contains non-finite number {value}")
        return parsed

    def pairs(items: List[Tuple[str, Any]]) -> Dict[str, Any]:
        result: Dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError(f"{label} contains duplicate key {key!r}")
            result[key] = value
        return result

    def require_utf8_strings(value: Any) -> None:
        if isinstance(value, str):
            try:
                value.encode("utf-8")
            except UnicodeEncodeError as exc:
                raise ValueError(
                    f"{label} contains a non-UTF-8 string"
                ) from exc
        elif isinstance(value, list):
            for item in value:
                require_utf8_strings(item)
        elif isinstance(value, dict):
            for key, item in value.items():
                require_utf8_strings(key)
                require_utf8_strings(item)

    value = json.loads(
        data.decode("utf-8"),
        object_pairs_hook=pairs,
        parse_float=parse_finite_float,
        parse_constant=reject_constant,
    )
    require_utf8_strings(value)
    return value


def _strict_native_json_object(
    data: bytes,
    *,
    label: str,
    parse_json_value: Callable[..., Any],
) -> Dict[str, Any]:
    """Parse one strict native JSON object."""

    value = parse_json_value(data, label=label)
    if not isinstance(value, dict):
        raise ValueError(f"{label} root is not an object")
    return value
