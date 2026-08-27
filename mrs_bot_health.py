"""Small fail-open progress telemetry for the MrsMThatcher bot process."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import tempfile
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any
import uuid


SCHEMA_VERSION = 1
ERROR_WINDOW_SECONDS = 30 * 60
WRITE_FAILURE_LOG_INTERVAL_SECONDS = 5 * 60
MAX_ERROR_SUMMARY_CHARS = 160
PHASES = frozenset(
    {
        "startup",
        "recovery",
        "main_loop",
        "paused",
        "historical_context",
        "reply_checks",
        "x_read",
        "x_write",
        "ai_call",
        "quote_post",
        "meme_post",
        "remote_write_blocked",
        "sleep",
        "shutdown",
    }
)


class HealthPathError(ValueError):
    """A requested test health path is outside the isolated test roots."""


def _same_or_child(path: Path, parent: Path) -> bool:
    resolved_path = path.expanduser().resolve(strict=False)
    resolved_parent = parent.expanduser().resolve(strict=False)
    return resolved_path == resolved_parent or resolved_parent in resolved_path.parents


def health_file_path_from_environment(
    *,
    test_mode: bool,
    environ: Mapping[str, str] | None = None,
    test_base_dir: Path | None = None,
) -> Path | None:
    """Resolve the progress path, preserving the repository's test isolation."""

    environment = os.environ if environ is None else environ
    explicit = str(environment.get("MRS_BOT_HEALTH_FILE", "")).strip()
    if test_mode and not explicit:
        return None

    if explicit:
        path = Path(explicit).expanduser()
    else:
        runtime_root = str(environment.get("XDG_RUNTIME_DIR", "")).strip()
        if not runtime_root:
            runtime_root = f"/run/user/{os.getuid()}"
        path = Path(runtime_root) / "mrsMThatcher" / "bot-health.json"

    path = path.resolve(strict=False)
    if not test_mode:
        return path

    allowed_roots: list[Path] = []
    for name in ("MRS_PYTEST_RUNTIME_ROOT", "MRS_BASE_DIR"):
        value = str(environment.get(name, "")).strip()
        if value:
            allowed_roots.append(Path(value))
    if test_base_dir is not None:
        allowed_roots.append(Path(test_base_dir))
    if not allowed_roots or not any(
        _same_or_child(path, root) for root in allowed_roots
    ):
        raise HealthPathError(
            "MRS_BOT_HEALTH_FILE must be beneath an explicit isolated test root"
        )
    return path


def atomic_write_json(path: Path, document: Mapping[str, Any], *, mode: int) -> None:
    """Replace one compact JSON document atomically from the same directory."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor = -1
    temporary_name: str | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{target.name}.",
            dir=target.parent,
        )
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            json.dump(
                dict(document),
                stream,
                ensure_ascii=True,
                allow_nan=False,
                separators=(",", ":"),
            )
        os.replace(temporary_name, target)
        temporary_name = None
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


class BotHealthReporter:
    """Record progress only when the foreground bot path reaches a boundary."""

    def __init__(
        self,
        path: Path,
        *,
        clock: Callable[[], float] = time.time,
        monotonic_clock: Callable[[], float] = time.monotonic,
        pid: int | None = None,
        instance_id: str | None = None,
        write_failure_callback: Callable[[str], None] | None = None,
    ) -> None:
        """Create one process-instance reporter without writing a heartbeat."""

        self.path = Path(path)
        self._clock = clock
        self._monotonic_clock = monotonic_clock
        self._write_failure_callback = write_failure_callback
        self._last_write_failure_report: float | None = None
        self._lock = threading.RLock()
        started = int(clock())
        self._document: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "instance_id": instance_id or uuid.uuid4().hex,
            "pid": int(os.getpid() if pid is None else pid),
            "started_epoch": started,
            "updated_epoch": started,
            "progress_sequence": 0,
            "phase": "startup",
            "phase_started_epoch": started,
            "paused": False,
            "remote_write_blocked": False,
            "last_loop_started_epoch": None,
            "last_loop_completed_epoch": None,
            "recent_error_count": 0,
            "last_error_epoch": None,
            "last_error_level": None,
            "last_error_summary": None,
        }

    def snapshot(self) -> dict[str, Any]:
        """Return a shallow copy of the scalar telemetry document."""

        with self._lock:
            return dict(self._document)

    def progress(
        self,
        phase: str,
        *,
        paused: bool | None = None,
        remote_write_blocked: bool | None = None,
        loop_started: bool = False,
        loop_completed: bool = False,
    ) -> None:
        """Advance telemetry at one genuine foreground execution boundary."""

        if phase not in PHASES:
            raise ValueError(f"unsupported bot health phase: {phase}")
        with self._lock:
            current = int(self._clock())
            if phase != self._document["phase"]:
                self._document["phase"] = phase
                self._document["phase_started_epoch"] = current
            if paused is not None:
                self._document["paused"] = paused is True
            if remote_write_blocked is not None:
                self._document["remote_write_blocked"] = (
                    remote_write_blocked is True
                )
            if loop_started:
                self._document["last_loop_started_epoch"] = current
            if loop_completed:
                self._document["last_loop_completed_epoch"] = current
            last_error = self._document["last_error_epoch"]
            if (
                isinstance(last_error, int)
                and current - last_error >= ERROR_WINDOW_SECONDS
            ):
                self._document["recent_error_count"] = 0
            self._document["updated_epoch"] = current
            self._document["progress_sequence"] += 1
            self._write_fail_open()

    def record_error(self, *, level: str, summary: str) -> None:
        """Observe one error record without changing any bot decision."""

        with self._lock:
            current = int(self._clock())
            previous = self._document["last_error_epoch"]
            if (
                isinstance(previous, int)
                and current - previous < ERROR_WINDOW_SECONDS
            ):
                self._document["recent_error_count"] += 1
            else:
                self._document["recent_error_count"] = 1
            self._document["last_error_epoch"] = current
            self._document["last_error_level"] = str(level)[:16]
            self._document["last_error_summary"] = _safe_one_line(summary)
            self._write_fail_open()

    def _write_fail_open(self) -> None:
        try:
            atomic_write_json(self.path, self._document, mode=0o600)
            self._last_write_failure_report = None
        except Exception as exc:
            callback = self._write_failure_callback
            if callback is None:
                return
            current = self._monotonic_clock()
            if (
                self._last_write_failure_report is not None
                and current - self._last_write_failure_report
                < WRITE_FAILURE_LOG_INTERVAL_SECONDS
            ):
                return
            self._last_write_failure_report = current
            try:
                callback(
                    "Bot health telemetry write failed "
                    f"({type(exc).__name__}); bot operation continues"
                )
            except Exception:
                pass


def _safe_one_line(value: object) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        text = "Error in unknown component"
    return text[:MAX_ERROR_SUMMARY_CHARS]


class HealthLoggingObserver(logging.Handler):
    """Observe ERROR/CRITICAL records without emitting any log record itself."""

    def __init__(self, reporter: BotHealthReporter) -> None:
        """Attach the destination reporter at ERROR level."""

        super().__init__(level=logging.ERROR)
        self.reporter = reporter
        self._local = threading.local()

    def emit(self, record: logging.LogRecord) -> None:
        """Record safe error identity while swallowing every observer failure."""

        if getattr(self._local, "active", False):
            return
        self._local.active = True
        try:
            exception_name = None
            if record.exc_info and record.exc_info[0] is not None:
                exception_name = getattr(
                    record.exc_info[0], "__name__", "Exception"
                )
            component = str(record.name or "unknown")
            if record.funcName:
                component = f"{component}.{record.funcName}"
            summary = f"{exception_name or record.levelname} in {component}"
            self.reporter.record_error(
                level=record.levelname,
                summary=summary,
            )
        except Exception:
            pass
        finally:
            self._local.active = False
