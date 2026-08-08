"""Provide hashing, atomic writes, and structured-file helpers."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
from typing import Any, Iterable


def sha256_file(path: Path) -> str:
    """Return the SHA-256 file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_write(path: Path, write_value: Any) -> None:
    """Commit a unique regular temporary file and fsync its directory."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=path.parent,
    )
    temporary = Path(temporary_name)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError(f"atomic-write temporary is not a regular file: {temporary}")
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            descriptor = -1
            write_value(handle)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory_descriptor = os.open(
            path.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    except BaseException:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        raise


def atomic_write_json(path: Path, value: Any) -> None:
    """Write deterministically formatted JSON with a durable atomic replace."""

    def write_json(handle: Any) -> None:
        json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")

    _atomic_write(path, write_json)


def atomic_write_text(path: Path, text: str) -> None:
    """Write text with a durable atomic replace in the destination directory."""

    def write_text(handle: Any) -> None:
        handle.write(text)

    _atomic_write(path, write_text)


def read_json(path: Path, default: Any = None) -> Any:
    """Read JSON."""
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """Read jsonl."""
    rows = []
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, 1):
            if line.strip():
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError(f"{path}:{line_no} is not a JSON object")
                rows.append(value)
    return rows


def unique_dicts(rows: Iterable[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    """Return the unique dicts."""
    seen = set()
    result = []
    for row in rows:
        key = tuple(row.get(name) for name in keys)
        if key not in seen:
            seen.add(key)
            result.append(row)
    return result
