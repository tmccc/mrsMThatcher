"""Provide hashing, atomic writes, and structured-file helpers."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable


def sha256_file(path: Path) -> str:
    """Return the SHA-256 file."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: Path, value: Any) -> None:
    """Write JSON atomically and optionally durably."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, ensure_ascii=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def atomic_write_text(path: Path, text: str) -> None:
    """Write text atomically."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


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
