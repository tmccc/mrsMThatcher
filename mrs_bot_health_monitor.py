#!/usr/bin/env python3
"""Evaluate MrsMThatcher health once and publish one Home Assistant document."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from collections.abc import Mapping
from typing import Any

from mrs_bot_health import PHASES, atomic_write_json


SCHEMA_VERSION = 1
STARTUP_GRACE_SECONDS = 3 * 60
PAUSED_STOP_GRACE_SECONDS = 10 * 60
DEFAULT_STALE_PROGRESS_SECONDS = 10 * 60
RECENT_ERROR_WINDOW_SECONDS = 30 * 60
INSTANCE_RESTART_WINDOW_SECONDS = 15 * 60
RAPID_INSTANCE_COUNT = 3
MAX_INPUT_BYTES = 64 * 1024
EXPECTED_BOT_SCRIPT = Path(
    "/disks/disk1/etc/mrsMThatcher/mrsMThatcher2.py"
)
DEFAULT_PROJECT_DIR = Path("/disks/disk1/etc/mrsMThatcher")
SYSTEMD_PROPERTIES = (
    "ActiveState",
    "SubState",
    "Result",
    "MainPID",
    "NRestarts",
    "ActiveEnterTimestampMonotonic",
    "ExecMainStartTimestampMonotonic",
)


class MonitorInputError(RuntimeError):
    """One required local observation could not be evaluated safely."""


@dataclass(frozen=True)
class ServiceStatus:
    """Selected systemd properties for the bot wrapper service."""

    active_state: str
    sub_state: str
    result: str
    main_pid: int
    n_restarts: int
    active_enter_monotonic_usec: int
    exec_main_start_monotonic_usec: int

    @property
    def active(self) -> bool:
        """Return whether systemd reports the service fully active."""

        return self.active_state == "active"


@dataclass(frozen=True)
class ProcessObservation:
    """Identity result for the PID recorded by bot progress telemetry."""

    exists: bool
    matches: bool
    reason: str


def _integer(value: object, *, name: str, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        raise MonitorInputError(f"{name} is not a valid integer")
    return value


def _optional_integer(value: object, *, name: str) -> int | None:
    if value is None:
        return None
    return _integer(value, name=name)


def validate_progress_snapshot(value: object, *, now_epoch: int) -> dict[str, Any]:
    """Validate the complete small child telemetry contract."""

    if not isinstance(value, dict):
        raise MonitorInputError("progress snapshot is not an object")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise MonitorInputError("progress snapshot schema is unsupported")
    instance_id = value.get("instance_id")
    if not isinstance(instance_id, str) or not 1 <= len(instance_id) <= 128:
        raise MonitorInputError("progress instance_id is invalid")
    pid = _integer(value.get("pid"), name="pid", minimum=2)
    started = _integer(value.get("started_epoch"), name="started_epoch", minimum=1)
    updated = _integer(value.get("updated_epoch"), name="updated_epoch", minimum=1)
    if updated < started or updated > now_epoch + 60:
        raise MonitorInputError("progress timestamps are inconsistent")
    sequence = _integer(
        value.get("progress_sequence"),
        name="progress_sequence",
    )
    phase = value.get("phase")
    if phase not in PHASES:
        raise MonitorInputError("progress phase is invalid")
    phase_started = _integer(
        value.get("phase_started_epoch"),
        name="phase_started_epoch",
        minimum=1,
    )
    if phase_started > now_epoch + 60:
        raise MonitorInputError("phase timestamp is in the future")
    if type(value.get("paused")) is not bool:
        raise MonitorInputError("paused flag is invalid")
    if type(value.get("remote_write_blocked")) is not bool:
        raise MonitorInputError("remote_write_blocked flag is invalid")
    last_loop_started = _optional_integer(
        value.get("last_loop_started_epoch"),
        name="last_loop_started_epoch",
    )
    last_loop_completed = _optional_integer(
        value.get("last_loop_completed_epoch"),
        name="last_loop_completed_epoch",
    )
    error_count = _integer(
        value.get("recent_error_count"),
        name="recent_error_count",
    )
    last_error = _optional_integer(
        value.get("last_error_epoch"),
        name="last_error_epoch",
    )
    last_error_level = value.get("last_error_level")
    last_error_summary = value.get("last_error_summary")
    if last_error_level is not None and not isinstance(last_error_level, str):
        raise MonitorInputError("last_error_level is invalid")
    if last_error_summary is not None and not isinstance(last_error_summary, str):
        raise MonitorInputError("last_error_summary is invalid")
    return {
        "schema_version": SCHEMA_VERSION,
        "instance_id": instance_id,
        "pid": pid,
        "started_epoch": started,
        "updated_epoch": updated,
        "progress_sequence": sequence,
        "phase": phase,
        "phase_started_epoch": phase_started,
        "paused": value["paused"],
        "remote_write_blocked": value["remote_write_blocked"],
        "last_loop_started_epoch": last_loop_started,
        "last_loop_completed_epoch": last_loop_completed,
        "recent_error_count": error_count,
        "last_error_epoch": last_error,
        "last_error_level": last_error_level,
        "last_error_summary": last_error_summary,
    }


def read_small_json(path: Path) -> object:
    """Read one bounded local JSON document."""

    try:
        size = path.stat().st_size
        if size > MAX_INPUT_BYTES:
            raise MonitorInputError(f"input is larger than {MAX_INPUT_BYTES} bytes")
        return json.loads(path.read_text(encoding="utf-8"))
    except MonitorInputError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise MonitorInputError(f"{type(exc).__name__} while reading {path.name}") from exc


def read_progress(path: Path, *, now_epoch: int) -> dict[str, Any]:
    """Read and validate the child progress snapshot."""

    return validate_progress_snapshot(read_small_json(path), now_epoch=now_epoch)


def read_service_status() -> ServiceStatus:
    """Read only the explicit required properties from the user service."""

    command = [
        "/usr/bin/systemctl",
        "--user",
        "show",
        "mrsMThatcher.service",
        "--no-pager",
        "--property=" + ",".join(SYSTEMD_PROPERTIES),
    ]
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0:
        raise MonitorInputError(
            f"systemctl show failed with exit status {completed.returncode}"
        )
    properties: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in SYSTEMD_PROPERTIES:
            properties[key] = value
    missing = [name for name in SYSTEMD_PROPERTIES if name not in properties]
    if missing:
        raise MonitorInputError("systemctl show omitted required properties")
    try:
        return ServiceStatus(
            active_state=properties["ActiveState"],
            sub_state=properties["SubState"],
            result=properties["Result"],
            main_pid=int(properties["MainPID"] or 0),
            n_restarts=int(properties["NRestarts"] or 0),
            active_enter_monotonic_usec=int(
                properties["ActiveEnterTimestampMonotonic"] or 0
            ),
            exec_main_start_monotonic_usec=int(
                properties["ExecMainStartTimestampMonotonic"] or 0
            ),
        )
    except ValueError as exc:
        raise MonitorInputError("systemctl show returned invalid numeric fields") from exc


def service_age_seconds(
    service: ServiceStatus,
    *,
    monotonic_now: float,
) -> int | None:
    """Return the current activation age from monotonic systemd timestamps."""

    timestamps = [
        value
        for value in (
            service.active_enter_monotonic_usec,
            service.exec_main_start_monotonic_usec,
        )
        if value > 0
    ]
    if not timestamps:
        return None
    return max(0, int(monotonic_now - max(timestamps) / 1_000_000))


def inspect_bot_process(
    pid: int,
    *,
    wrapper_pid: int,
    expected_script: Path = EXPECTED_BOT_SCRIPT,
    proc_root: Path = Path("/proc"),
) -> ProcessObservation:
    """Establish that the snapshot PID is the wrapper's expected Python child."""

    process_dir = proc_root / str(pid)
    if not process_dir.exists():
        return ProcessObservation(False, False, "pid_absent")
    try:
        command = (process_dir / "cmdline").read_bytes().split(b"\0")
        status_lines = (process_dir / "status").read_text(
            encoding="utf-8", errors="strict"
        ).splitlines()
    except (OSError, UnicodeError):
        return ProcessObservation(True, False, "process_unreadable")
    arguments = [
        item.decode("utf-8", "surrogateescape") for item in command if item
    ]
    if not arguments or not Path(arguments[0]).name.startswith("python"):
        return ProcessObservation(True, False, "not_python")
    script_matches = False
    for argument in arguments[1:]:
        try:
            if Path(argument).resolve(strict=False) == expected_script.resolve(
                strict=False
            ):
                script_matches = True
                break
        except (OSError, RuntimeError):
            continue
    if not script_matches:
        return ProcessObservation(True, False, "unexpected_command")
    parent_pid = None
    for line in status_lines:
        if line.startswith("PPid:"):
            try:
                parent_pid = int(line.split(":", 1)[1].strip())
            except ValueError:
                parent_pid = None
            break
    if wrapper_pid <= 0 or parent_pid != wrapper_pid:
        return ProcessObservation(True, False, "unexpected_parent")
    return ProcessObservation(True, True, "expected_python_child")


def empty_monitor_state() -> dict[str, Any]:
    """Return an empty transient child-instance history."""

    return {"schema_version": SCHEMA_VERSION, "instances": []}


def read_monitor_state(path: Path) -> dict[str, Any]:
    """Read transient instance history, resetting malformed data safely."""

    if not path.exists():
        return empty_monitor_state()
    try:
        value = read_small_json(path)
    except MonitorInputError:
        return empty_monitor_state()
    if not isinstance(value, dict) or value.get("schema_version") != SCHEMA_VERSION:
        return empty_monitor_state()
    instances = value.get("instances")
    if not isinstance(instances, list):
        return empty_monitor_state()
    clean = []
    for item in instances:
        if (
            isinstance(item, dict)
            and isinstance(item.get("instance_id"), str)
            and type(item.get("first_seen_epoch")) is int
        ):
            clean.append(
                {
                    "instance_id": item["instance_id"][:128],
                    "first_seen_epoch": item["first_seen_epoch"],
                }
            )
    return {"schema_version": SCHEMA_VERSION, "instances": clean}


def observe_instance(
    state: Mapping[str, Any],
    *,
    instance_id: str,
    now_epoch: int,
) -> tuple[dict[str, Any], int]:
    """Record one live instance and return restarts in the rolling window."""

    cutoff = now_epoch - INSTANCE_RESTART_WINDOW_SECONDS
    instances = [
        dict(item)
        for item in state.get("instances", [])
        if isinstance(item, dict)
        and type(item.get("first_seen_epoch")) is int
        and item["first_seen_epoch"] >= cutoff
        and isinstance(item.get("instance_id"), str)
    ]
    if not any(item["instance_id"] == instance_id for item in instances):
        instances.append(
            {"instance_id": instance_id, "first_seen_epoch": now_epoch}
        )
    instances.sort(key=lambda item: item["first_seen_epoch"])
    distinct = len({item["instance_id"] for item in instances})
    return (
        {"schema_version": SCHEMA_VERSION, "instances": instances},
        max(0, distinct - 1),
    )


def _base_output(
    *,
    now_epoch: int,
    service: ServiceStatus,
    progress: Mapping[str, Any] | None,
    bot_commit: str,
    instance_restarts: int,
) -> dict[str, Any]:
    updated = progress.get("updated_epoch") if progress else None
    phase_started = progress.get("phase_started_epoch") if progress else None
    return {
        "schema_version": SCHEMA_VERSION,
        "checked_epoch": now_epoch,
        "status": "failed",
        "reason": "evaluation_incomplete",
        "summary": "Bot health evaluation did not complete",
        "service_active": service.active,
        "service_substate": service.sub_state,
        "service_result": service.result,
        "wrapper_pid": service.main_pid,
        "service_nrestarts": service.n_restarts,
        "bot_pid": progress.get("pid") if progress else None,
        "bot_instance_id": progress.get("instance_id") if progress else None,
        "bot_commit": bot_commit,
        "progress_age_seconds": (
            max(0, now_epoch - int(updated))
            if isinstance(updated, int)
            else None
        ),
        "phase": progress.get("phase") if progress else None,
        "phase_age_seconds": (
            max(0, now_epoch - int(phase_started))
            if isinstance(phase_started, int)
            else None
        ),
        "last_loop_completed_epoch": (
            progress.get("last_loop_completed_epoch") if progress else None
        ),
        "recent_error_count": (
            progress.get("recent_error_count") if progress else 0
        ),
        "last_error_epoch": (
            progress.get("last_error_epoch") if progress else None
        ),
        "last_error_level": (
            progress.get("last_error_level") if progress else None
        ),
        "last_error_summary": (
            progress.get("last_error_summary") if progress else None
        ),
        "instance_restarts_15m": instance_restarts,
    }


def _classified(
    output: dict[str, Any],
    status: str,
    reason: str,
    summary: str,
) -> dict[str, Any]:
    output.update(status=status, reason=reason, summary=summary)
    return output


def evaluate_health(
    *,
    now_epoch: int,
    service: ServiceStatus,
    service_age: int | None,
    progress: Mapping[str, Any] | None,
    snapshot_error: str | None,
    process: ProcessObservation | None,
    bot_commit: str,
    instance_restarts: int,
    stale_progress_seconds: int = DEFAULT_STALE_PROGRESS_SECONDS,
) -> dict[str, Any]:
    """Classify one already-observed local snapshot without side effects."""

    output = _base_output(
        now_epoch=now_epoch,
        service=service,
        progress=progress,
        bot_commit=bot_commit,
        instance_restarts=instance_restarts,
    )
    progress_age = output["progress_age_seconds"]

    service_is_running_or_starting = service.active_state in {
        "active",
        "activating",
        "reloading",
    }
    if not service_is_running_or_starting:
        if (
            progress is not None
            and progress.get("remote_write_blocked") is True
            and isinstance(progress_age, int)
            and progress_age <= PAUSED_STOP_GRACE_SECONDS
        ):
            return _classified(
                output,
                "degraded",
                "remote_write_blocked",
                "Bot was stopped during deployment grace, but its fresh "
                "remote-write safety barrier still needs attention",
            )
        if (
            progress is not None
            and progress.get("paused") is True
            and isinstance(progress_age, int)
            and progress_age <= PAUSED_STOP_GRACE_SECONDS
        ):
            return _classified(
                output,
                "paused",
                "paused_service_stopped_grace",
                "Bot was stopped after a fresh intentional pause; deployment grace is active",
            )
        return _classified(
            output,
            "failed",
            "service_inactive",
            f"mrsMThatcher.service is {service.active_state}/{service.sub_state}",
        )

    within_startup_grace = (
        service_age is not None and service_age <= STARTUP_GRACE_SECONDS
    )
    if progress is None:
        if within_startup_grace:
            return _classified(
                output,
                "starting",
                "awaiting_health_snapshot",
                "Bot service is starting and has not published a valid health snapshot yet",
            )
        detail = snapshot_error or "health snapshot unavailable"
        return _classified(
            output,
            "failed",
            "health_snapshot_invalid",
            f"Bot service has no valid health snapshot: {detail}",
        )

    if process is None or not process.matches:
        child_restart_grace = within_startup_grace or (
            isinstance(progress_age, int)
            and progress_age <= STARTUP_GRACE_SECONDS
        )
        if child_restart_grace:
            return _classified(
                output,
                "starting",
                "awaiting_python_child",
                "Bot wrapper is starting and the expected Python child is not ready yet",
            )
        process_reason = process.reason if process is not None else "not_observed"
        return _classified(
            output,
            "failed",
            "python_child_missing",
            f"Bot wrapper is active but the recorded Python child does not match ({process_reason})",
        )

    if service.active_state != "active":
        if within_startup_grace:
            return _classified(
                output,
                "starting",
                "service_activating",
                "Bot service is still activating",
            )
        return _classified(
            output,
            "failed",
            "activation_grace_expired",
            "Bot service did not become active within the startup grace",
        )

    if not isinstance(progress_age, int) or progress_age > stale_progress_seconds:
        age_minutes = 0 if not isinstance(progress_age, int) else progress_age // 60
        return _classified(
            output,
            "stalled",
            "progress_stale",
            "Service and Python process are present, but no genuine progress "
            f"has been recorded for {age_minutes} minutes",
        )

    if progress.get("remote_write_blocked") is True:
        return _classified(
            output,
            "degraded",
            "remote_write_blocked",
            "Bot is progressing, but the remote-write safety barrier needs attention",
        )

    if progress.get("paused") is True:
        return _classified(
            output,
            "paused",
            "intentional_pause",
            "Bot is intentionally paused and continues to report progress",
        )

    if instance_restarts >= RAPID_INSTANCE_COUNT - 1:
        return _classified(
            output,
            "degraded",
            "rapid_child_restarts",
            "Bot is progressing, but three distinct Python child instances "
            "were observed within 15 minutes",
        )

    last_error = progress.get("last_error_epoch")
    recent_errors = progress.get("recent_error_count", 0)
    if (
        isinstance(recent_errors, int)
        and recent_errors >= 3
        and isinstance(last_error, int)
        and now_epoch - last_error < RECENT_ERROR_WINDOW_SECONDS
    ):
        return _classified(
            output,
            "degraded",
            "repeated_errors",
            f"Bot is progressing, but {recent_errors} recent errors were recorded",
        )

    return _classified(
        output,
        "healthy",
        "ok",
        "Bot process is running and making progress",
    )


def current_commit(project_dir: Path) -> str:
    """Return the current production checkout commit for display only."""

    try:
        completed = subprocess.run(
            ["/usr/bin/git", "-C", str(project_dir), "rev-parse", "--short=12", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    commit = completed.stdout.strip()
    return commit if completed.returncode == 0 and commit else "unknown"


def _positive_environment_integer(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise MonitorInputError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise MonitorInputError(f"{name} must be a positive integer")
    return value


def _runtime_root() -> Path:
    value = os.environ.get("XDG_RUNTIME_DIR", "").strip()
    return Path(value or f"/run/user/{os.getuid()}")


def _test_path_allowed(path: Path) -> bool:
    resolved = path.resolve(strict=False)
    roots = [
        Path(value).resolve(strict=False)
        for name in ("MRS_PYTEST_RUNTIME_ROOT", "MRS_BASE_DIR")
        if (value := os.environ.get(name, "").strip())
    ]
    return bool(
        roots
        and any(resolved == root or root in resolved.parents for root in roots)
    )


def _configured_path(name: str, default: Path | None = None) -> Path:
    raw = os.environ.get(name, "").strip()
    if not raw:
        if default is None:
            raise MonitorInputError(f"{name} is required")
        path = default
    else:
        path = Path(raw).expanduser()
    path = path.resolve(strict=False)
    if os.environ.get("MRS_TEST_MODE") == "1" and not _test_path_allowed(path):
        raise MonitorInputError(f"{name} is outside the isolated test roots")
    return path


def fatal_output(
    *,
    now_epoch: int,
    summary: str,
    service: ServiceStatus | None = None,
    bot_commit: str = "unknown",
) -> dict[str, Any]:
    """Build a publishable failed document for a fatal local evaluation error."""

    fallback = service or ServiceStatus("unknown", "unknown", "unknown", 0, 0, 0, 0)
    output = _base_output(
        now_epoch=now_epoch,
        service=fallback,
        progress=None,
        bot_commit=bot_commit,
        instance_restarts=0,
    )
    return _classified(output, "failed", "monitor_evaluation_error", summary[:240])


def run_once() -> tuple[Path, dict[str, Any]]:
    """Observe, classify, and atomically publish one monitor evaluation."""

    now_epoch = int(time.time())
    output_path = _configured_path("MRS_BOT_HEALTH_MONITOR_OUTPUT")
    runtime = _runtime_root()
    progress_path = _configured_path(
        "MRS_BOT_HEALTH_FILE",
        runtime / "mrsMThatcher" / "bot-health.json",
    )
    state_path = _configured_path(
        "MRS_BOT_HEALTH_MONITOR_STATE_FILE",
        runtime / "mrsMThatcher" / "monitor-state.json",
    )
    project_dir = _configured_path("MRS_BOT_PROJECT_DIR", DEFAULT_PROJECT_DIR)
    expected_script = project_dir / "mrsMThatcher2.py"
    commit = current_commit(project_dir)
    service: ServiceStatus | None = None
    try:
        stale_seconds = _positive_environment_integer(
            "MRS_BOT_HEALTH_STALE_SECONDS",
            DEFAULT_STALE_PROGRESS_SECONDS,
        )
        service = read_service_status()
        age = service_age_seconds(service, monotonic_now=time.monotonic())
        progress = None
        snapshot_error = None
        try:
            progress = read_progress(progress_path, now_epoch=now_epoch)
        except MonitorInputError as exc:
            snapshot_error = str(exc)
        process = None
        if progress is not None:
            process = inspect_bot_process(
                int(progress["pid"]),
                wrapper_pid=service.main_pid,
                expected_script=expected_script,
            )
        monitor_state = read_monitor_state(state_path)
        instance_restarts = 0
        if progress is not None and process is not None and process.matches:
            monitor_state, instance_restarts = observe_instance(
                monitor_state,
                instance_id=str(progress["instance_id"]),
                now_epoch=now_epoch,
            )
        result = evaluate_health(
            now_epoch=now_epoch,
            service=service,
            service_age=age,
            progress=progress,
            snapshot_error=snapshot_error,
            process=process,
            bot_commit=commit,
            instance_restarts=instance_restarts,
            stale_progress_seconds=stale_seconds,
        )
        try:
            atomic_write_json(state_path, monitor_state, mode=0o600)
        except OSError as exc:
            result = _classified(
                result,
                "failed",
                "monitor_state_write_failed",
                f"Health monitor transient state could not be updated ({type(exc).__name__})",
            )
    except Exception as exc:
        result = fatal_output(
            now_epoch=now_epoch,
            service=service,
            bot_commit=commit,
            summary=f"Health monitor evaluation failed ({type(exc).__name__})",
        )
    atomic_write_json(output_path, result, mode=0o644)
    return output_path, result


def main(argv: list[str] | None = None) -> int:
    """Run the argument-free one-shot command-line evaluator."""

    arguments = sys.argv[1:] if argv is None else argv
    if arguments:
        print("usage: mrs_bot_health_monitor.py", file=sys.stderr)
        return 2
    try:
        output_path, result = run_once()
    except Exception as exc:
        print(
            f"MrsMThatcher health monitor could not publish status ({type(exc).__name__})",
            file=sys.stderr,
        )
        return 1
    print(
        json.dumps(
            {
                "output": str(output_path),
                "status": result["status"],
                "reason": result["reason"],
            },
            separators=(",", ":"),
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
