"""Read runtime controls and apply global and lane pause policy.

Each root invocation binds a fresh RuntimeControls owner with the current cache,
settings and runtime authorities. Snapshot reading, failure recovery, loading
and pause evaluation call their owned operations directly. The shared pure
contract retains timestamp/document validation and configurable key sets.

Stable-file checks, private result copies, cache mutation, clock sampling and
logging order are unchanged. The owner retains the supplied cache reference,
without taking a snapshot or reading it during construction. Import performs
no file, environment, provider, clock or RNG work.
"""

from __future__ import annotations

import hashlib
import logging
import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

from mrs_bot_local_config import load_strict_runtime_json
from runtime_control_contract import (
    CONTROL_ALLOWED_KEYS,
    CONTROL_BOOLEAN_KEYS,
    CONTROL_METADATA_KEYS,
    CONTROL_TIME_KEYS,
    MAX_CONTROL_EPOCH,
    parse_control_time,
    validate_control_document,
)


@dataclass(frozen=True)
class RuntimeControls:
    """Own stable control snapshots, fail-closed cache results and pause decisions."""

    control_file: Path
    cache: dict[str, object]
    maximum_bytes: int
    absent_error: type[FileNotFoundError]
    os: ModuleType
    log: logging.Logger
    log_json_debug: Callable
    validate_document: Callable[[object], dict]
    now_epoch: Callable[[], int]
    parse_time: Callable[[object], int]
    datetime: type
    log_event: Callable

    def failure_result(self, reason: str, *, signature: object) -> dict:
        """Build the fail-closed result for an invalid runtime-control document."""
        if self.cache.get("failure_signature") != signature:
            self.log.error("Runtime control file %s is unavailable or invalid; failing safe: %s", self.control_file, reason)
            self.cache["failure_signature"] = signature
        if self.cache.get("has_valid"):
            cached = self.cache.get("data", {})
            self.log.debug("Continuing with last valid runtime control document")
            fail_closed = dict(cached) if isinstance(cached, dict) else {}
            fail_closed["disable_all"] = True
            fail_closed["_control_fail_closed"] = True
            return fail_closed
        return {"disable_all": True, "_control_fail_closed": True}

    def stat_identity(self, file_stat: os.stat_result) -> tuple[int, ...]:
        """Return the fields which must remain stable for one control snapshot."""

        return (
            file_stat.st_dev,
            file_stat.st_ino,
            stat.S_IFMT(file_stat.st_mode),
            file_stat.st_size,
            file_stat.st_mtime_ns,
            file_stat.st_ctime_ns,
        )

    def read_snapshot(self) -> tuple[bytes, tuple[object, ...]]:
        """Read one regular, non-symlink control file as a stable byte snapshot."""

        control_path = self.os.path.abspath(self.os.fspath(self.control_file))
        try:
            before_path = self.os.lstat(control_path)
        except FileNotFoundError as exc:
            raise self.absent_error(control_path) from exc
        if not stat.S_ISREG(before_path.st_mode):
            raise ValueError("runtime control must be a regular file")

        nonblock = getattr(self.os, "O_NONBLOCK", 0)
        nofollow = getattr(self.os, "O_NOFOLLOW", 0)
        if not nofollow or not nonblock:
            raise RuntimeError(
                "runtime control requires O_NOFOLLOW and O_NONBLOCK support"
            )
        flags = self.os.O_RDONLY | getattr(self.os, "O_CLOEXEC", 0) | nonblock
        descriptor = self.os.open(control_path, flags | nofollow)
        try:
            before_fd = self.os.fstat(descriptor)
            if not stat.S_ISREG(before_fd.st_mode):
                raise ValueError("runtime control must be a regular file")
            if self.stat_identity(before_path) != self.stat_identity(
                before_fd
            ):
                raise RuntimeError("runtime control changed before it was opened")
            if before_fd.st_size > self.maximum_bytes:
                raise ValueError(
                    f"runtime control exceeds {self.maximum_bytes} bytes"
                )

            def read_document() -> bytes:
                chunks: list[bytes] = []
                observed = 0
                while observed <= self.maximum_bytes:
                    chunk = self.os.read(
                        descriptor,
                        min(8192, self.maximum_bytes + 1 - observed),
                    )
                    if not chunk:
                        break
                    chunks.append(chunk)
                    observed += len(chunk)
                return b"".join(chunks)

            document = read_document()
            middle_fd = self.os.fstat(descriptor)
            if self.stat_identity(before_fd) != self.stat_identity(
                middle_fd
            ):
                raise RuntimeError("runtime control changed during its first read")
            if len(document) != before_fd.st_size:
                raise ValueError(
                    "runtime control length did not match its stable file identity"
                )
            self.os.lseek(descriptor, 0, self.os.SEEK_SET)
            repeated_document = read_document()
            after_fd = self.os.fstat(descriptor)
            after_path = self.os.lstat(control_path)
        finally:
            self.os.close(descriptor)

        if (
            len(document) > self.maximum_bytes
            or len(document) != before_fd.st_size
        ):
            raise ValueError(
                "runtime control length did not match its stable file identity"
            )
        if repeated_document != document:
            raise RuntimeError("runtime control bytes changed during stable read")
        if self.stat_identity(middle_fd) != self.stat_identity(
            after_fd
        ):
            raise RuntimeError("runtime control changed while it was read")

        if self.stat_identity(after_fd) != self.stat_identity(
            after_path
        ):
            raise RuntimeError("runtime control path changed while it was read")

        signature: tuple[object, ...] = (
            control_path,
            *self.stat_identity(after_fd),
            hashlib.sha256(document).hexdigest(),
        )
        return document, signature

    def load(self) -> dict:
        """Load and validate the optional fail-safe runtime-control document."""
        try:
            document, signature = self.read_snapshot()
        except self.absent_error:
            self.cache["signature"] = None
            self.cache["data"] = {}
            self.cache["has_valid"] = False
            self.cache["failure_signature"] = None
            return {}
        except OSError as exc:
            return self.failure_result(
                str(exc),
                signature=("read", type(exc).__name__, str(exc)),
            )
        except Exception as exc:
            return self.failure_result(
                str(exc),
                signature=("snapshot", type(exc).__name__, str(exc)),
            )

        try:
            data = load_strict_runtime_json(
                document,
                label="runtime control",
                parse_floats_as_decimal=True,
            )
            data = self.validate_document(data)
        except Exception as exc:
            return self.failure_result(str(exc), signature=("content", signature, type(exc).__name__, str(exc)))

        changed = (
            self.cache.get("signature") != signature
            or self.cache.get("data") != data
        )
        self.cache["signature"] = signature
        self.cache["data"] = dict(data)
        self.cache["has_valid"] = True
        self.cache["failure_signature"] = None
        if changed:
            self.log.info("Loaded runtime control file %s", self.control_file)
            self.log_json_debug("Runtime control", data)
        return dict(data)

    def boolean(self, data: dict, key: str) -> bool:
        """Return a validated boolean runtime-control value."""
        value = data.get(key)
        if value in (None, ""):
            return False
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            normalized = value.strip().lower()
            if not normalized:
                return False
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
        self.log.warning("Invalid boolean passed directly to control_bool for %s=%r; returning false (runtime loader rejects invalid controls)", key, value)
        return False

    def pause_active(self, data: dict, *keys: str) -> tuple[bool, str, int]:
        """Return whether the runtime-control document currently pauses a lane."""
        current = self.now_epoch()

        for key in keys:
            if self.boolean(data, key):
                return True, key, 0

        for key in [f"{key}_until" for key in keys]:
            if key in data:
                try:
                    until_epoch = self.parse_time(data.get(key))
                except ValueError as exc:
                    self.log.error("Invalid pause passed directly to control_pause_active for %s=%r; runtime loader rejects invalid controls: %s", key, data.get(key), exc)
                    continue
                if until_epoch > current:
                    return True, key, until_epoch

        return False, "", 0

    def lane_paused(self, *lane_keys: str) -> bool:
        """Return whether a named posting lane is paused."""
        data = self.load()
        if not data:
            return False

        expanded_keys = ["disable_all", "pause_all"]
        expanded_keys.extend(lane_keys)

        if any("replies" in key for key in lane_keys):
            expanded_keys.append("pause_replies")
        if "disable_normal_replies" in lane_keys:
            expanded_keys.append("pause_normal_replies")
        if "disable_quote_replies" in lane_keys:
            expanded_keys.append("pause_quote_replies")
        if "disable_hot_post_replies" in lane_keys:
            expanded_keys.append("pause_hot_post_replies")
        if "disable_quote_posts" in lane_keys:
            expanded_keys.append("pause_quote_posts")
        if "disable_meme_posts" in lane_keys:
            expanded_keys.append("pause_meme_posts")

        # Preserve order while de-duplicating aliases.
        expanded_keys = list(dict.fromkeys(expanded_keys))

        active, key, until_epoch = self.pause_active(data, *expanded_keys)
        if active:
            human_until = self.datetime.fromtimestamp(until_epoch).strftime("%Y-%m-%d %H:%M:%S") if until_epoch else "until cleared"
            self.log.warning("Runtime control active: %s pauses %s (%s)", key, ",".join(lane_keys), human_until)
            self.log_event("runtime_control_pause", key=key, lanes=list(lane_keys), until_epoch=until_epoch)
            return True

        return False

    def global_paused(self) -> bool:
        """Return whether the runtime control pauses every remote-write lane."""
        data = self.load()
        if not data:
            return False
        active, _key, _until_epoch = self.pause_active(
            data,
            "disable_all",
            "pause_all",
        )
        return active
