"""Normalise durable state scalar and collection values.

A fresh StateValues owner binds the current logger and epoch cap on
each root call; fixed numeric operations and the shared bounded ID grammar live here; nested scalar and collection operations call their owner directly.
Distinct ID/scalar coercions, validation and logging order, shallow record copies
and epoch-list identity stay unchanged. Full state/schema/reader validation, higher-level mention/cache/receipt
policy and durable I/O remain in their existing locations. The owner retains
no caller values or paths and performs no import-time runtime work or reverse
application import.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from logging import Logger
from pathlib import Path


def bounded_tweet_id_value(value: object, *, allow_empty: bool = False) -> int | None:
    """Parse one bounded string tweet ID without unbounded integer conversion."""
    if allow_empty and value == "":
        return 0
    if type(value) is not str or not re.fullmatch(r"\d{1,30}", value):
        return None
    return int(value)


@dataclass(frozen=True)
class StateValues:
    """Normalize related state values with current diagnostics and epoch bounds."""

    log: Logger
    maximum_epoch: int


    def integer(self, value: object, *, key: str, path: Path) -> int | None:
        """Normalise state int."""
        if isinstance(value, bool):
            self.log.error("State candidate %s has invalid %s boolean value %r; ignoring", path, key, value)
            return None
        if isinstance(value, float) and (not math.isfinite(value) or not value.is_integer()):
            self.log.error("State candidate %s has invalid %s numeric value %r; ignoring", path, key, value)
            return None
        try:
            number = int(value or 0)
        except (TypeError, ValueError, OverflowError):
            self.log.error("State candidate %s has invalid %s value %r; ignoring", path, key, value)
            return None
        if number < 0:
            self.log.error("State candidate %s has negative %s value %r; ignoring", path, key, value)
            return None
        return number

    def epoch(self, value: object, *, key: str, path: Path) -> int | None:
        """Normalise state epoch."""
        number = self.integer(value, key=key, path=path)
        if number is None:
            return None
        if number > self.maximum_epoch:
            self.log.error("State candidate %s has impossible epoch %s=%r; ignoring", path, key, value)
            return None
        return number

    def strings(self, value: object, *, key: str, path: Path) -> list[str] | None:
        """Normalise string list."""
        if not isinstance(value, list):
            self.log.error("State candidate %s has invalid %s type %s; ignoring", path, key, type(value).__name__)
            return None
        return [str(item) for item in value if item is not None]

    def integers(self, value: object, *, key: str, path: Path) -> list[int] | None:
        """Normalise int list."""
        if not isinstance(value, list):
            self.log.error("State candidate %s has invalid %s type %s; ignoring", path, key, type(value).__name__)
            return None
        out: list[int] = []
        for item in value:
            number = self.integer(item, key=key, path=path)
            if number is None:
                return None
            out.append(number)
        return out

    def epochs(self, value: object, *, key: str, path: Path) -> list[int] | None:
        """Normalise epoch list."""
        out = self.integers(value, key=key, path=path)
        if out is None:
            return None
        for number in out:
            if number > self.maximum_epoch:
                self.log.error("State candidate %s has impossible %s epoch item %r; ignoring", path, key, number)
                return None
        return out

    def string_map(self, value: object, *, key: str, path: Path) -> dict[str, str] | None:
        """Normalise string map."""
        if not isinstance(value, dict):
            self.log.error("State candidate %s has invalid %s type %s; ignoring", path, key, type(value).__name__)
            return None
        return {str(k): str(v) for k, v in value.items() if v is not None}

    def integer_map(self, value: object, *, key: str, path: Path) -> dict[str, int] | None:
        """Normalise int map."""
        if not isinstance(value, dict):
            self.log.error("State candidate %s has invalid %s type %s; ignoring", path, key, type(value).__name__)
            return None
        out: dict[str, int] = {}
        for item_key, item_value in value.items():
            number = self.integer(item_value, key=f"{key}.{item_key}", path=path)
            if number is None:
                return None
            out[str(item_key)] = number
        return out

    def record_map(self, value: object, *, key: str, path: Path) -> dict[str, dict] | None:
        """Normalise record map."""
        if not isinstance(value, dict):
            self.log.error("State candidate %s has invalid %s type %s; ignoring", path, key, type(value).__name__)
            return None
        out: dict[str, dict] = {}
        for item_key, item_value in value.items():
            if not isinstance(item_value, dict):
                self.log.error(
                    "State candidate %s has invalid %s.%s type %s; ignoring",
                    path,
                    key,
                    item_key,
                    type(item_value).__name__,
                )
                return None
            out[str(item_key)] = dict(item_value)
        return out

    def optional_scalar(self, value: object, *, key: str, path: Path) -> str | None:
        """Normalise optional scalar."""
        if value is None:
            return ""
        if isinstance(value, (str, int)):
            return str(value)
        self.log.error("State candidate %s has invalid %s type %s; ignoring", path, key, type(value).__name__)
        return None

    def optional_id(self, value: object, *, key: str, path: Path) -> str | None:
        """Normalise optional numeric ID."""
        if value in (None, ""):
            return ""
        text = str(value)
        if bounded_tweet_id_value(text) is not None:
            return text
        self.log.error("State candidate %s has invalid %s value %r; ignoring", path, key, value)
        return None
