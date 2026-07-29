#!/usr/bin/env python3
"""Strict JSON parsing and deterministic serialization for release controls.

Python's standard :mod:`json` decoder accepts duplicate object names using
last-name-wins semantics and also accepts the non-standard numeric constants
``NaN`` and ``Infinity``.  Neither behaviour is suitable for release-control
documents.  This module gives the application-side, non-authoritative
validators one small implementation with explicit failure classes.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class StrictJSONError(ValueError):
    """Base class for a JSON representation rejected before schema validation."""

    def __init__(self, source: str, detail: str) -> None:
        self.source = source
        self.detail = detail
        super().__init__(f"{source}: {detail}")


class MalformedJSONError(StrictJSONError):
    """The input is not syntactically valid UTF-8 JSON."""


class DuplicateObjectNameError(StrictJSONError):
    """A JSON object contains the same member name more than once."""

    def __init__(self, source: str, key: str) -> None:
        self.key = key
        super().__init__(source, f"duplicate JSON object name {key!r}")


class NonFiniteNumberError(StrictJSONError):
    """The input contains a non-standard non-finite numeric constant."""

    def __init__(self, source: str, constant: str) -> None:
        self.constant = constant
        super().__init__(source, f"non-finite JSON number {constant!r}")


def loads(payload: str | bytes, *, source: str = "<memory>") -> Any:
    """Decode one strict JSON value.

    Duplicate member names are rejected by ``object_pairs_hook`` at every
    nesting depth. ``parse_constant`` rejects all non-standard non-finite
    numbers before a schema validator can accidentally accept them.
    """

    if isinstance(payload, bytes):
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise MalformedJSONError(
                source,
                f"input is not UTF-8 at byte {exc.start}",
            ) from exc
    elif isinstance(payload, str):
        text = payload
    else:
        raise TypeError("strict JSON payload must be str or bytes")

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        value: dict[str, Any] = {}
        for key, item in pairs:
            if key in value:
                raise DuplicateObjectNameError(source, key)
            value[key] = item
        return value

    def reject_constant(constant: str) -> None:
        raise NonFiniteNumberError(source, constant)

    try:
        return json.loads(
            text,
            object_pairs_hook=reject_duplicates,
            parse_constant=reject_constant,
        )
    except StrictJSONError:
        raise
    except json.JSONDecodeError as exc:
        raise MalformedJSONError(
            source,
            f"malformed JSON at line {exc.lineno} column {exc.colno}: {exc.msg}",
        ) from exc


def load(path: Path) -> Any:
    """Read and strictly decode one UTF-8 JSON file."""

    return loads(path.read_bytes(), source=str(path))


def canonical_dumps(value: Any, *, indent: int | None = 2) -> str:
    """Return deterministic JSON text and reject non-finite Python values."""

    try:
        return json.dumps(
            value,
            sort_keys=True,
            ensure_ascii=False,
            indent=indent,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise StrictJSONError(
            "<serialization>",
            f"value cannot be represented as strict JSON: {exc}",
        ) from exc


def canonical_json_bytes(value: Any, *, indent: int | None = 2) -> bytes:
    """Return deterministic strict JSON bytes with one trailing newline."""

    return (canonical_dumps(value, indent=indent) + "\n").encode("utf-8")
