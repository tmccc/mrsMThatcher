#!/usr/bin/env python3
"""Evaluate MrsMThatcher supporting jobs and its website downloader.

This is a short-lived, read-only host monitor.  It inspects explicitly
configured user-systemd pairs and one Docker container, then atomically writes
one compact JSON document for Home Assistant.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Mapping, Sequence


SCHEMA_VERSION = 1
STATE_SCHEMA_VERSION = 1
CONTAINER_GENERATION_CLOCK_TOLERANCE_SECONDS = 0
MAX_CONFIG_BYTES = 64 * 1024
MAX_STATE_BYTES = 64 * 1024
MAX_CYCLE_STATUS_BYTES = 16 * 1024
MAX_COMPONENTS = 32
COMMAND_TIMEOUT_SECONDS = 8
SYSTEMCTL = "/usr/bin/systemctl"
BUSCTL = "/usr/bin/busctl"
DOCKER = "/usr/bin/docker"
SYSTEMD_USEC_INFINITY = (1 << 64) - 1

ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
TIMER_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@:-]{0,199}[.]timer$")
SERVICE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.@:-]{0,199}[.]service$")
SELECTOR_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
ERROR_CLASS_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

TIMER_PROPERTIES = (
    "LoadState",
    "ActiveState",
    "SubState",
    "UnitFileState",
    "LastTriggerUSec",
    "LastTriggerUSecMonotonic",
    "NextElapseUSecRealtime",
    "NextElapseUSecMonotonic",
    "Triggers",
)
SERVICE_PROPERTIES = (
    "LoadState",
    "ActiveState",
    "SubState",
    "Result",
    "ExecMainCode",
    "ExecMainStatus",
    "InvocationID",
    "ExecMainStartTimestamp",
    "ExecMainExitTimestamp",
    "ExecMainStartTimestampMonotonic",
    "ExecMainExitTimestampMonotonic",
)

HARD_SERVICE_RESULTS = {
    "signal",
    "timeout",
    "core-dump",
    "watchdog",
    "oom-kill",
    "start-limit-hit",
    "resources",
    "protocol",
}


class ConfigurationError(ValueError):
    """Raised when the bounded declarative configuration is invalid."""


class ObservationError(RuntimeError):
    """Raised when a configured local component cannot be inspected."""


@dataclass(frozen=True)
class SystemdComponentConfig:
    """Describe one explicitly configured timer/oneshot pair."""

    id: str
    timer: str
    service: str
    expected: bool
    expected_interval_seconds: int
    overdue_grace_seconds: int
    maximum_runtime_seconds: int


@dataclass(frozen=True)
class DockerSelector:
    """Select one container by Compose labels or an exact stable name."""

    compose_project: str | None = None
    compose_service: str | None = None
    container_name: str | None = None


@dataclass(frozen=True)
class DownloaderConfig:
    """Describe the bounded downloader checks and thresholds."""

    id: str
    expected: bool
    selector: DockerSelector
    cycle_status_file: Path
    maximum_cycle_seconds: int
    maximum_success_age_seconds: int
    maximum_progress_age_seconds: int | None
    docker_health_starting_grace_seconds: int
    rapid_restart_count: int
    rapid_restart_window_seconds: int


@dataclass(frozen=True)
class MonitorConfig:
    """Contain all validated support-monitor configuration."""

    systemd_components: tuple[SystemdComponentConfig, ...]
    downloader: DownloaderConfig


@dataclass(frozen=True)
class TimerObservation:
    """Contain the whitelisted properties observed for a user timer."""

    load_state: str
    active_state: str
    sub_state: str
    unit_file_state: str
    last_trigger_epoch: int | None
    next_trigger_epoch: int | None
    triggers: tuple[str, ...]


@dataclass(frozen=True)
class ServiceObservation:
    """Contain the whitelisted properties observed for a user service."""

    load_state: str
    active_state: str
    sub_state: str
    result: str
    exec_main_code: int
    exec_main_status: int
    invocation_id: str
    start_epoch: int | None
    exit_epoch: int | None
    start_monotonic_usec: int
    exit_monotonic_usec: int


@dataclass(frozen=True)
class DockerObservation:
    """Contain only whitelisted Docker container state."""

    container_id: str
    name: str
    image: str
    created_epoch: int | None
    status: str
    running: bool
    restarting: bool
    oom_killed: bool
    dead: bool
    exit_code: int
    started_epoch: int | None
    finished_epoch: int | None
    restart_count: int
    docker_health: str
    compose_project: str | None
    compose_service: str | None


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _require_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise ConfigurationError(f"{label} must be an object")
    return value


def _require_exact_keys(
    value: Mapping[str, Any], *, required: set[str], optional: set[str], label: str
) -> None:
    missing = required - set(value)
    unknown = set(value) - required - optional
    if missing:
        raise ConfigurationError(f"{label} is missing: {', '.join(sorted(missing))}")
    if unknown:
        raise ConfigurationError(f"{label} has unknown fields: {', '.join(sorted(unknown))}")


def _bounded_int(
    value: object, label: str, *, minimum: int = 1, maximum: int
) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ConfigurationError(
            f"{label} must be an integer between {minimum} and {maximum}"
        )
    return value


def _validated_id(value: object, label: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise ConfigurationError(f"{label} is not a valid component ID")
    return value


def validate_config(value: object) -> MonitorConfig:
    """Strictly validate the bounded host configuration."""

    root = _require_mapping(value, "configuration")
    _require_exact_keys(
        root,
        required={"schema_version", "systemd_components", "downloader"},
        optional=set(),
        label="configuration",
    )
    if root["schema_version"] != SCHEMA_VERSION:
        raise ConfigurationError("unsupported configuration schema_version")

    raw_components = root["systemd_components"]
    if not isinstance(raw_components, list) or not 1 <= len(raw_components) <= MAX_COMPONENTS:
        raise ConfigurationError(
            f"systemd_components must contain between 1 and {MAX_COMPONENTS} entries"
        )
    components: list[SystemdComponentConfig] = []
    ids: set[str] = set()
    timers: set[str] = set()
    services: set[str] = set()
    component_keys = {
        "id",
        "timer",
        "service",
        "expected",
        "expected_interval_seconds",
        "overdue_grace_seconds",
        "maximum_runtime_seconds",
    }
    for index, item in enumerate(raw_components):
        label = f"systemd_components[{index}]"
        raw = _require_mapping(item, label)
        _require_exact_keys(raw, required=component_keys, optional=set(), label=label)
        component_id = _validated_id(raw["id"], f"{label}.id")
        timer = raw["timer"]
        service = raw["service"]
        if not isinstance(timer, str) or not TIMER_RE.fullmatch(timer):
            raise ConfigurationError(f"{label}.timer is not a plausible timer unit")
        if not isinstance(service, str) or not SERVICE_RE.fullmatch(service):
            raise ConfigurationError(f"{label}.service is not a plausible service unit")
        if component_id in ids or timer in timers or service in services:
            raise ConfigurationError("component IDs and unit names must be unique")
        if type(raw["expected"]) is not bool:
            raise ConfigurationError(f"{label}.expected must be a boolean")
        ids.add(component_id)
        timers.add(timer)
        services.add(service)
        components.append(
            SystemdComponentConfig(
                id=component_id,
                timer=timer,
                service=service,
                expected=raw["expected"],
                expected_interval_seconds=_bounded_int(
                    raw["expected_interval_seconds"],
                    f"{label}.expected_interval_seconds",
                    maximum=366 * 86_400,
                ),
                overdue_grace_seconds=_bounded_int(
                    raw["overdue_grace_seconds"],
                    f"{label}.overdue_grace_seconds",
                    maximum=31 * 86_400,
                ),
                maximum_runtime_seconds=_bounded_int(
                    raw["maximum_runtime_seconds"],
                    f"{label}.maximum_runtime_seconds",
                    maximum=31 * 86_400,
                ),
            )
        )

    raw_downloader = _require_mapping(root["downloader"], "downloader")
    downloader_required = {
        "id",
        "expected",
        "selector",
        "cycle_status_file",
        "maximum_cycle_seconds",
        "maximum_success_age_seconds",
        "docker_health_starting_grace_seconds",
        "rapid_restart_count",
        "rapid_restart_window_seconds",
    }
    _require_exact_keys(
        raw_downloader,
        required=downloader_required,
        optional={"maximum_progress_age_seconds"},
        label="downloader",
    )
    downloader_id = _validated_id(raw_downloader["id"], "downloader.id")
    if downloader_id in ids:
        raise ConfigurationError("downloader ID duplicates a systemd component ID")
    if type(raw_downloader["expected"]) is not bool:
        raise ConfigurationError("downloader.expected must be a boolean")

    raw_selector = _require_mapping(raw_downloader["selector"], "downloader.selector")
    selector_keys = set(raw_selector)
    if selector_keys == {"compose_project", "compose_service"}:
        project = raw_selector["compose_project"]
        service = raw_selector["compose_service"]
        if not isinstance(project, str) or not SELECTOR_RE.fullmatch(project):
            raise ConfigurationError("compose_project is invalid")
        if not isinstance(service, str) or not SELECTOR_RE.fullmatch(service):
            raise ConfigurationError("compose_service is invalid")
        selector = DockerSelector(compose_project=project, compose_service=service)
    elif selector_keys == {"container_name"}:
        name = raw_selector["container_name"]
        if not isinstance(name, str) or not SELECTOR_RE.fullmatch(name):
            raise ConfigurationError("container_name is invalid")
        selector = DockerSelector(container_name=name)
    else:
        raise ConfigurationError(
            "downloader.selector must contain exactly a Compose project/service "
            "pair or one exact container_name"
        )

    raw_cycle_path = raw_downloader["cycle_status_file"]
    if not isinstance(raw_cycle_path, str) or not raw_cycle_path.startswith("/"):
        raise ConfigurationError("downloader.cycle_status_file must be absolute")
    cycle_path = Path(raw_cycle_path)
    if ".." in cycle_path.parts or len(raw_cycle_path) > 4096:
        raise ConfigurationError("downloader.cycle_status_file is invalid")

    progress_age = raw_downloader.get("maximum_progress_age_seconds")
    if progress_age is not None:
        progress_age = _bounded_int(
            progress_age,
            "downloader.maximum_progress_age_seconds",
            maximum=31 * 86_400,
        )
    downloader = DownloaderConfig(
        id=downloader_id,
        expected=raw_downloader["expected"],
        selector=selector,
        cycle_status_file=cycle_path,
        maximum_cycle_seconds=_bounded_int(
            raw_downloader["maximum_cycle_seconds"],
            "downloader.maximum_cycle_seconds",
            maximum=366 * 86_400,
        ),
        maximum_success_age_seconds=_bounded_int(
            raw_downloader["maximum_success_age_seconds"],
            "downloader.maximum_success_age_seconds",
            maximum=366 * 86_400,
        ),
        maximum_progress_age_seconds=progress_age,
        docker_health_starting_grace_seconds=_bounded_int(
            raw_downloader["docker_health_starting_grace_seconds"],
            "downloader.docker_health_starting_grace_seconds",
            maximum=7 * 86_400,
        ),
        rapid_restart_count=_bounded_int(
            raw_downloader["rapid_restart_count"],
            "downloader.rapid_restart_count",
            maximum=100,
        ),
        rapid_restart_window_seconds=_bounded_int(
            raw_downloader["rapid_restart_window_seconds"],
            "downloader.rapid_restart_window_seconds",
            maximum=7 * 86_400,
        ),
    )
    return MonitorConfig(tuple(components), downloader)


def read_bounded_json(path: Path, *, maximum_bytes: int) -> object:
    """Read one size-bounded local JSON document."""

    try:
        if path.stat().st_size > maximum_bytes:
            raise ObservationError(f"{path.name} exceeds {maximum_bytes} bytes")
        return json.loads(path.read_text(encoding="utf-8"))
    except ObservationError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ObservationError(f"cannot read valid JSON from {path}") from exc


def load_config(path: Path) -> MonitorConfig:
    """Read and validate the private host configuration."""

    try:
        value = read_bounded_json(path, maximum_bytes=MAX_CONFIG_BYTES)
    except ObservationError as exc:
        raise ConfigurationError(str(exc)) from exc
    return validate_config(value)


def _parse_systemd_epoch(value: str) -> int | None:
    if not value or value in {"n/a", "0", "@0"}:
        return None
    match = re.fullmatch(r"@([0-9]+)(?:[.]([0-9]{1,9}))?", value)
    if not match:
        raise ObservationError("systemctl returned an invalid Unix timestamp")
    try:
        epoch = int(match.group(1))
    except (ValueError, OverflowError) as exc:
        raise ObservationError("systemctl returned an invalid Unix timestamp") from exc
    fraction = match.group(2) or ""
    if epoch == 0 and not fraction.strip("0"):
        return None
    if epoch <= 0 or epoch > 253_402_300_799:
        raise ObservationError("systemctl returned an invalid Unix timestamp")
    return epoch


def _parse_iso_epoch(value: str) -> int | None:
    if not value or value.startswith("0001-"):
        return None
    try:
        # Docker emits nanoseconds while this host's Python accepts at most
        # six fractional digits in fromisoformat().
        normalized = re.sub(r"([.][0-9]{6})[0-9]+", r"\1", value)
        return int(datetime.fromisoformat(normalized.replace("Z", "+00:00")).timestamp())
    except (ValueError, OverflowError):
        return None


def _run(
    arguments: Sequence[str], *, runner: Runner = subprocess.run, timeout: int = COMMAND_TIMEOUT_SECONDS
) -> subprocess.CompletedProcess[str]:
    try:
        return runner(
            list(arguments),
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ObservationError(f"{Path(arguments[0]).name} inspection failed") from exc


def _systemd_properties(
    unit: str,
    properties: Sequence[str],
    *,
    timestamp_interface: str,
    timestamp_properties: Sequence[str],
    runner: Runner = subprocess.run,
) -> dict[str, str]:
    arguments = [
        SYSTEMCTL,
        "--user",
        "show",
        unit,
        "--no-pager",
        "--timestamp=unix",
        "--property=" + ",".join(properties),
    ]
    result = _run(arguments, runner=runner)
    legacy_mode = False
    if result.returncode != 0 and "Invalid value: unix" in (result.stderr or ""):
        # systemd 249 does not implement systemctl's Unix timestamp renderer.
        # Retain systemctl for the bounded unit-property inspection, then read
        # just the timestamp properties as raw uint64 microseconds over the
        # same local user-manager D-Bus connection.
        result = _run(
            [argument for argument in arguments if argument != "--timestamp=unix"],
            runner=runner,
        )
        legacy_mode = True
    if result.returncode != 0:
        raise ObservationError(
            f"systemctl could not inspect {unit} (exit {result.returncode})"
        )
    parsed: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in properties:
            parsed[key] = value
    missing = [key for key in properties if key not in parsed]
    if missing:
        raise ObservationError(f"systemctl omitted required properties for {unit}")
    if legacy_mode:
        if parsed.get("LoadState") == "loaded":
            parsed.update(
                _legacy_systemd_timestamps(
                    unit,
                    interface=timestamp_interface,
                    properties=timestamp_properties,
                    runner=runner,
                )
            )
        else:
            parsed.update({name: "0" for name in timestamp_properties})
    return parsed


def _legacy_systemd_timestamps(
    unit: str,
    *,
    interface: str,
    properties: Sequence[str],
    runner: Runner,
) -> dict[str, str]:
    path_result = _run(
        [
            BUSCTL,
            "--user",
            "call",
            "org.freedesktop.systemd1",
            "/org/freedesktop/systemd1",
            "org.freedesktop.systemd1.Manager",
            "GetUnit",
            "s",
            unit,
        ],
        runner=runner,
    )
    path_match = re.fullmatch(r'\s*o "([A-Za-z0-9_/]+)"\s*', path_result.stdout)
    if path_result.returncode != 0 or path_match is None:
        raise ObservationError("systemd D-Bus unit-path inspection failed")
    property_result = _run(
        [
            BUSCTL,
            "--user",
            "get-property",
            "org.freedesktop.systemd1",
            path_match.group(1),
            interface,
            *properties,
        ],
        runner=runner,
    )
    lines = property_result.stdout.splitlines()
    if property_result.returncode != 0 or len(lines) != len(properties):
        raise ObservationError("systemd D-Bus timestamp inspection failed")
    parsed: dict[str, str] = {}
    for name, line in zip(properties, lines):
        match = re.fullmatch(r"t ([0-9]+)", line.strip())
        if match is None:
            raise ObservationError("systemd D-Bus returned an invalid timestamp")
        microseconds = int(match.group(1))
        if microseconds in {0, SYSTEMD_USEC_INFINITY}:
            parsed[name] = "0"
        else:
            seconds, remainder = divmod(microseconds, 1_000_000)
            parsed[name] = f"@{seconds}.{remainder:06d}"
    return parsed


def inspect_timer(unit: str, *, runner: Runner = subprocess.run) -> TimerObservation:
    """Read the required properties of one configured user timer."""

    raw = _systemd_properties(
        unit,
        TIMER_PROPERTIES,
        timestamp_interface="org.freedesktop.systemd1.Timer",
        timestamp_properties=("LastTriggerUSec", "NextElapseUSecRealtime"),
        runner=runner,
    )
    return TimerObservation(
        load_state=raw["LoadState"],
        active_state=raw["ActiveState"],
        sub_state=raw["SubState"],
        unit_file_state=raw["UnitFileState"],
        last_trigger_epoch=_parse_systemd_epoch(raw["LastTriggerUSec"]),
        next_trigger_epoch=_parse_systemd_epoch(raw["NextElapseUSecRealtime"]),
        triggers=tuple(item for item in raw["Triggers"].split() if item),
    )


def _integer_property(raw: Mapping[str, str], key: str) -> int:
    try:
        return int(raw[key] or 0)
    except ValueError as exc:
        raise ObservationError(f"systemctl returned an invalid {key}") from exc


def inspect_service(unit: str, *, runner: Runner = subprocess.run) -> ServiceObservation:
    """Read the required properties of one configured user service."""

    raw = _systemd_properties(
        unit,
        SERVICE_PROPERTIES,
        timestamp_interface="org.freedesktop.systemd1.Service",
        timestamp_properties=("ExecMainStartTimestamp", "ExecMainExitTimestamp"),
        runner=runner,
    )
    return ServiceObservation(
        load_state=raw["LoadState"],
        active_state=raw["ActiveState"],
        sub_state=raw["SubState"],
        result=raw["Result"],
        exec_main_code=_integer_property(raw, "ExecMainCode"),
        exec_main_status=_integer_property(raw, "ExecMainStatus"),
        invocation_id=raw["InvocationID"][:128],
        start_epoch=_parse_systemd_epoch(raw["ExecMainStartTimestamp"]),
        exit_epoch=_parse_systemd_epoch(raw["ExecMainExitTimestamp"]),
        start_monotonic_usec=_integer_property(raw, "ExecMainStartTimestampMonotonic"),
        exit_monotonic_usec=_integer_property(raw, "ExecMainExitTimestampMonotonic"),
    )


def _service_is_running(service: ServiceObservation) -> bool:
    return service.active_state in {"active", "activating", "reloading"} and service.sub_state not in {
        "dead",
        "failed",
    }


def _completed_outcome(service: ServiceObservation) -> str | None:
    if _service_is_running(service) or service.exit_epoch is None:
        return None
    result = service.result.lower()
    if result in HARD_SERVICE_RESULTS or service.exec_main_code in {2, 3, 4, 5, 6}:
        return "hard_failure"
    if service.exec_main_status != 0 or result in {"exit-code", "failure", "condition"}:
        return "nonzero"
    if result == "success" and service.exec_main_status == 0:
        return "success"
    if result and result != "success":
        return "nonzero"
    return None


def _local_utc_offset_seconds(epoch: int) -> int | None:
    try:
        offset = datetime.fromtimestamp(epoch).astimezone().utcoffset()
    except (OSError, OverflowError, TypeError, ValueError):
        return None
    return int(offset.total_seconds()) if offset is not None else None


def _clock_change_extension_seconds(start_epoch: int, end_epoch: int) -> int:
    start_offset = _local_utc_offset_seconds(start_epoch)
    end_offset = _local_utc_offset_seconds(end_epoch)
    if start_offset is None or end_offset is None:
        return 0
    return min(abs(end_offset - start_offset), 3_600)


def _systemd_diagnostics(
    timer: TimerObservation | None,
    service: ServiceObservation | None,
    *,
    current_runtime_seconds: int | None,
    last_success_epoch: int | None,
) -> dict[str, Any]:
    return {
        "timer_loaded": timer is not None and timer.load_state == "loaded",
        "timer_active": timer is not None and timer.active_state == "active",
        "timer_substate": timer.sub_state if timer is not None else None,
        "timer_enabled": timer is not None
        and timer.unit_file_state in {"enabled", "enabled-runtime"},
        "last_trigger_epoch": timer.last_trigger_epoch if timer is not None else None,
        "next_trigger_epoch": timer.next_trigger_epoch if timer is not None else None,
        "service_loaded": service is not None and service.load_state == "loaded",
        "service_active": service is not None and _service_is_running(service),
        "service_substate": service.sub_state if service is not None else None,
        "service_result": service.result if service is not None else None,
        "service_exit_status": service.exec_main_status if service is not None else None,
        "current_runtime_seconds": current_runtime_seconds,
        "last_success_epoch": last_success_epoch,
    }


def evaluate_systemd_component(
    config: SystemdComponentConfig,
    *,
    timer: TimerObservation | None,
    service: ServiceObservation | None,
    now_epoch: int,
    monotonic_now: float,
    history: Mapping[str, Any] | None = None,
    timer_error: str | None = None,
    service_error: str | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Classify one timer/oneshot pair and return its next transient history."""

    previous = dict(history or {})
    last_success = previous.get("last_success_epoch")
    if type(last_success) is not int or last_success <= 0:
        last_success = None
    last_completed_outcome = previous.get("last_completed_outcome")
    last_completed_invocation = previous.get("last_completed_invocation_id")

    outcome = _completed_outcome(service) if service is not None else None
    if outcome is not None and service is not None:
        if service.invocation_id and service.invocation_id != last_completed_invocation:
            last_completed_invocation = service.invocation_id
            last_completed_outcome = outcome
        elif not last_completed_invocation:
            last_completed_invocation = service.invocation_id
            last_completed_outcome = outcome
        if outcome == "success" and service.exit_epoch is not None:
            last_success = service.exit_epoch
            last_completed_outcome = "success"

    next_history = {
        "last_success_epoch": last_success,
        "last_completed_invocation_id": last_completed_invocation,
        "last_completed_outcome": last_completed_outcome,
    }

    runtime: int | None = None
    if service is not None and _service_is_running(service) and service.start_monotonic_usec > 0:
        runtime = max(0, int(monotonic_now - service.start_monotonic_usec / 1_000_000))
    diagnostics = _systemd_diagnostics(
        timer, service, current_runtime_seconds=runtime, last_success_epoch=last_success
    )

    if not config.expected:
        return {"status": "ignored", "reason": "not_expected_active", **diagnostics}, next_history
    if timer_error:
        return {"status": "failed", "reason": "timer_inspection_failed", **diagnostics}, next_history
    if timer is None or timer.load_state == "not-found":
        return {"status": "failed", "reason": "timer_missing", **diagnostics}, next_history
    if timer.load_state != "loaded":
        reason = "timer_masked" if timer.load_state == "masked" else "timer_load_failed"
        return {"status": "failed", "reason": reason, **diagnostics}, next_history
    if timer.unit_file_state in {"masked", "masked-runtime"}:
        return {"status": "failed", "reason": "timer_masked", **diagnostics}, next_history
    if timer.unit_file_state not in {"enabled", "enabled-runtime"}:
        return {"status": "failed", "reason": "timer_disabled", **diagnostics}, next_history
    if timer.active_state not in {"active", "activating"}:
        return {"status": "failed", "reason": "timer_inactive", **diagnostics}, next_history
    if config.service not in timer.triggers:
        return {"status": "failed", "reason": "timer_trigger_mismatch", **diagnostics}, next_history
    if service_error:
        return {"status": "failed", "reason": "service_inspection_failed", **diagnostics}, next_history
    if service is None or service.load_state == "not-found":
        return {"status": "failed", "reason": "service_missing", **diagnostics}, next_history
    if service.load_state != "loaded":
        return {"status": "failed", "reason": "service_load_failed", **diagnostics}, next_history

    if timer.active_state == "activating":
        return {"status": "starting", "reason": "timer_activating", **diagnostics}, next_history
    if runtime is not None and runtime > config.maximum_runtime_seconds:
        return {"status": "degraded", "reason": "service_runtime_exceeded", **diagnostics}, next_history
    if last_completed_outcome == "hard_failure":
        return {"status": "failed", "reason": "service_hard_failure", **diagnostics}, next_history
    if last_completed_outcome == "nonzero":
        return {"status": "degraded", "reason": "last_run_failed", **diagnostics}, next_history
    if timer.next_trigger_epoch is not None:
        if now_epoch > timer.next_trigger_epoch + config.overdue_grace_seconds:
            return {"status": "degraded", "reason": "timer_overdue", **diagnostics}, next_history

    if _service_is_running(service):
        if last_success is None:
            return {"status": "starting", "reason": "first_run_in_progress", **diagnostics}, next_history
        return {"status": "healthy", "reason": "current_run_within_limit", **diagnostics}, next_history
    if last_success is not None:
        nominal_deadline = (
            last_success
            + config.expected_interval_seconds
            + config.overdue_grace_seconds
        )
        effective_deadline = nominal_deadline
        if timer.next_trigger_epoch is not None:
            extension = _clock_change_extension_seconds(
                last_success, timer.next_trigger_epoch
            )
            scheduled_deadline = (
                timer.next_trigger_epoch + config.overdue_grace_seconds
            )
            effective_deadline = max(
                nominal_deadline,
                min(scheduled_deadline, nominal_deadline + extension),
            )
        if now_epoch > effective_deadline:
            return {
                "status": "degraded",
                "reason": "successful_run_stale",
                **diagnostics,
            }, next_history

    if last_success is not None:
        return {"status": "healthy", "reason": "last_run_succeeded", **diagnostics}, next_history
    if timer.last_trigger_epoch is None and (
        timer.next_trigger_epoch is not None
        and now_epoch <= timer.next_trigger_epoch + config.overdue_grace_seconds
    ):
        return {"status": "starting", "reason": "awaiting_first_run", **diagnostics}, next_history
    if timer.last_trigger_epoch is None and timer.next_trigger_epoch is None:
        return {"status": "degraded", "reason": "next_trigger_unavailable", **diagnostics}, next_history
    return {"status": "degraded", "reason": "no_successful_invocation", **diagnostics}, next_history


DOCKER_INSPECT_TEMPLATE = "\n".join(
    (
        "container_id={{json .Id}}",
        "name={{json .Name}}",
        "image={{json .Config.Image}}",
        "created={{json .Created}}",
        "status={{json .State.Status}}",
        "running={{json .State.Running}}",
        "restarting={{json .State.Restarting}}",
        "oom_killed={{json .State.OOMKilled}}",
        "dead={{json .State.Dead}}",
        "exit_code={{json .State.ExitCode}}",
        "started_at={{json .State.StartedAt}}",
        "finished_at={{json .State.FinishedAt}}",
        "restart_count={{json .RestartCount}}",
        '{{with (index .State "Health")}}docker_health={{json (index . "Status")}}{{else}}docker_health="not_configured"{{end}}',
        'compose_project={{json (index .Config.Labels "com.docker.compose.project")}}',
        'compose_service={{json (index .Config.Labels "com.docker.compose.service")}}',
    )
)


def resolve_container_ids(
    selector: DockerSelector, *, runner: Runner = subprocess.run
) -> list[str]:
    """Resolve a stable Docker selector to all matching container IDs."""

    arguments = [DOCKER, "ps", "-a", "--no-trunc"]
    if selector.compose_project is not None:
        arguments.extend(
            (
                "--filter",
                f"label=com.docker.compose.project={selector.compose_project}",
                "--filter",
                f"label=com.docker.compose.service={selector.compose_service}",
                "--format",
                "{{.ID}}",
            )
        )
        result = _run(arguments, runner=runner)
        if result.returncode != 0:
            raise ObservationError("docker could not resolve the Compose selector")
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    arguments.extend(
        (
            "--filter",
            f"name={selector.container_name}",
            "--format",
            "{{.ID}}\t{{.Names}}",
        )
    )
    result = _run(arguments, runner=runner)
    if result.returncode != 0:
        raise ObservationError("docker could not resolve the container name")
    matches = []
    for line in result.stdout.splitlines():
        container_id, separator, name = line.partition("\t")
        if separator and name == selector.container_name:
            matches.append(container_id)
    return matches


def inspect_container(container_id: str, *, runner: Runner = subprocess.run) -> DockerObservation:
    """Inspect only the whitelisted state of one Docker container."""

    if not re.fullmatch(r"[0-9a-f]{12,64}", container_id):
        raise ObservationError("docker returned an invalid container ID")
    result = _run(
        [DOCKER, "inspect", "--format", DOCKER_INSPECT_TEMPLATE, container_id],
        runner=runner,
    )
    if result.returncode != 0:
        raise ObservationError("docker inspect failed")
    raw: dict[str, Any] = {}
    try:
        for line in result.stdout.splitlines():
            key, separator, encoded = line.partition("=")
            if separator:
                raw[key] = json.loads(encoded)
        required = {
            "container_id",
            "name",
            "image",
            "created",
            "status",
            "running",
            "restarting",
            "oom_killed",
            "dead",
            "exit_code",
            "started_at",
            "finished_at",
            "restart_count",
            "docker_health",
            "compose_project",
            "compose_service",
        }
        if set(raw) != required:
            raise ValueError("missing inspect fields")
        return DockerObservation(
            container_id=str(raw["container_id"]),
            name=str(raw["name"]).lstrip("/")[:128],
            image=str(raw["image"])[:256],
            created_epoch=_parse_iso_epoch(str(raw["created"])),
            status=str(raw["status"])[:32],
            running=raw["running"] is True,
            restarting=raw["restarting"] is True,
            oom_killed=raw["oom_killed"] is True,
            dead=raw["dead"] is True,
            exit_code=int(raw["exit_code"]),
            started_epoch=_parse_iso_epoch(str(raw["started_at"])),
            finished_epoch=_parse_iso_epoch(str(raw["finished_at"])),
            restart_count=max(0, int(raw["restart_count"])),
            docker_health=str(raw["docker_health"] or "not_configured")[:32],
            compose_project=(
                str(raw["compose_project"])[:128]
                if raw["compose_project"] is not None
                else None
            ),
            compose_service=(
                str(raw["compose_service"])[:128]
                if raw["compose_service"] is not None
                else None
            ),
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ObservationError("docker inspect returned invalid whitelisted fields") from exc


def validate_cycle_status(value: object, *, now_epoch: int) -> dict[str, Any]:
    """Validate and reduce one downloader cycle-status document."""

    if not isinstance(value, dict):
        raise ObservationError("downloader cycle status is not an object")
    allowed = {
        "schema_version",
        "cycle_started_epoch",
        "cycle_completed_epoch",
        "last_success_epoch",
        "last_progress_epoch",
        "state",
        "duration_seconds",
        "item_count",
        "last_error_class",
    }
    if set(value) - allowed or value.get("schema_version") != SCHEMA_VERSION:
        raise ObservationError("downloader cycle status schema is invalid")
    state = value.get("state")
    if state not in {"running", "success", "failed"}:
        raise ObservationError("downloader cycle state is invalid")

    def epoch(name: str, *, required: bool) -> int | None:
        item = value.get(name)
        if item is None and not required:
            return None
        if type(item) is not int or item <= 0 or item > now_epoch + 300:
            raise ObservationError(f"downloader cycle {name} is invalid")
        return item

    started = epoch("cycle_started_epoch", required=True)
    completed = epoch("cycle_completed_epoch", required=state != "running")
    success = epoch("last_success_epoch", required=False)
    progress = epoch("last_progress_epoch", required=False)
    duration = value.get("duration_seconds")
    if duration is not None and (type(duration) is not int or duration < 0 or duration > 366 * 86_400):
        raise ObservationError("downloader cycle duration is invalid")
    item_count = value.get("item_count")
    if type(item_count) is not int or not 0 <= item_count <= 100_000_000:
        raise ObservationError("downloader cycle item_count is invalid")
    error_class = value.get("last_error_class")
    if error_class is not None and (
        not isinstance(error_class, str) or not ERROR_CLASS_RE.fullmatch(error_class)
    ):
        raise ObservationError("downloader cycle error class is invalid")
    if completed is not None and started is not None and completed < started:
        raise ObservationError("downloader cycle completion predates its start")
    if progress is not None and started is not None and progress < started:
        raise ObservationError("downloader cycle progress predates its start")
    if state == "running" and completed is not None:
        raise ObservationError("running downloader cycle has a completion time")
    if state == "success":
        if completed is None or success != completed or error_class is not None:
            raise ObservationError("successful downloader cycle is inconsistent")
    if state == "failed" and completed is None:
        raise ObservationError("failed downloader cycle has no completion time")
    return {
        "state": state,
        "cycle_started_epoch": started,
        "cycle_completed_epoch": completed,
        "last_success_epoch": success,
        "last_progress_epoch": progress,
        "duration_seconds": duration,
        "item_count": item_count,
        "last_error_class": error_class,
    }


def read_cycle_status(path: Path, *, now_epoch: int) -> dict[str, Any]:
    """Read one bounded downloader cycle-status document."""

    return validate_cycle_status(
        read_bounded_json(path, maximum_bytes=MAX_CYCLE_STATUS_BYTES),
        now_epoch=now_epoch,
    )


def cycle_evidence_epoch(cycle: Mapping[str, Any]) -> int:
    """Return the latest generation-relevant timestamp in a validated cycle."""

    state = cycle.get("state")
    fields_by_state = {
        "running": ("last_progress_epoch", "cycle_started_epoch"),
        "success": (
            "cycle_completed_epoch",
            "last_success_epoch",
            "last_progress_epoch",
            "cycle_started_epoch",
        ),
        "failed": (
            "cycle_completed_epoch",
            "last_progress_epoch",
            "last_success_epoch",
            "cycle_started_epoch",
        ),
    }
    fields = fields_by_state.get(state)
    if fields is None:
        raise ObservationError("downloader cycle state is invalid")
    evidence = [cycle.get(field) for field in fields]
    timestamps = [item for item in evidence if type(item) is int and item > 0]
    if not timestamps:
        raise ObservationError("downloader cycle has no generation evidence")
    return max(timestamps)


def observe_docker_restarts(
    history: Mapping[str, Any] | None,
    observation: DockerObservation,
    *,
    now_epoch: int,
    window_seconds: int,
) -> tuple[dict[str, Any], int, int]:
    """Update bounded restart events and distinct container generations."""

    previous = dict(history or {})
    previous_id = previous.get("container_id")
    previous_count = previous.get("restart_count")
    raw_events = previous.get("restart_events", [])
    events = [
        item
        for item in raw_events
        if type(item) is int and now_epoch - window_seconds <= item <= now_epoch + 300
    ][:100]
    raw_identities = previous.get("recent_container_ids", [])
    identities = [
        item
        for item in raw_identities
        if isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and type(item.get("first_seen_epoch")) is int
        and item["first_seen_epoch"] >= now_epoch - window_seconds
        and item["first_seen_epoch"] <= now_epoch + 300
    ][-100:]

    distinct_identities: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for item in identities:
        identity = item["id"][:64]
        if identity not in seen_ids:
            distinct_identities.append(
                {"id": identity, "first_seen_epoch": item["first_seen_epoch"]}
            )
            seen_ids.add(identity)
    identities = distinct_identities

    if previous_id == observation.container_id and type(previous_count) is int:
        delta = max(0, observation.restart_count - previous_count)
        events.extend([now_epoch] * min(delta, 100 - len(events)))
    current_id = observation.container_id[:64]
    if current_id not in seen_ids:
        identities.append({"id": current_id, "first_seen_epoch": now_epoch})
        identities = identities[-100:]
    events = [item for item in events if item >= now_epoch - window_seconds][-100:]
    next_history = {
        "container_id": observation.container_id[:64],
        "restart_count": observation.restart_count,
        "restart_events": events,
        "recent_container_ids": identities,
    }
    return next_history, len(events), len(identities)


def _docker_diagnostics(
    observation: DockerObservation | None,
    cycle: Mapping[str, Any] | None,
    *,
    restart_events: int,
    container_generations: int,
    now_epoch: int,
) -> dict[str, Any]:
    last_success = cycle.get("last_success_epoch") if cycle is not None else None
    cycle_state = cycle.get("state") if cycle is not None else None
    if cycle_state == "running" and cycle is not None:
        cycle_age = max(0, now_epoch - int(cycle["cycle_started_epoch"]))
    elif type(last_success) is int:
        cycle_age = max(0, now_epoch - last_success)
    else:
        cycle_age = None
    evidence_epoch: int | None = None
    if cycle is not None:
        try:
            evidence_epoch = cycle_evidence_epoch(cycle)
        except ObservationError:
            pass
    container_started = observation.started_epoch if observation is not None else None
    current_container: bool | None = None
    if cycle is not None:
        current_container = (
            container_started is not None
            and evidence_epoch is not None
            and evidence_epoch
            >= container_started - CONTAINER_GENERATION_CLOCK_TOLERANCE_SECONDS
        )
    return {
        "container_present": observation is not None,
        "container_running": observation.running if observation is not None else False,
        "container_status": observation.status if observation is not None else None,
        "container_name": observation.name if observation is not None else None,
        "container_image": observation.image if observation is not None else None,
        "docker_health": observation.docker_health if observation is not None else None,
        "restart_events_window": restart_events,
        "container_generations_window": container_generations,
        "container_started_epoch": container_started,
        "cycle_evidence_epoch": evidence_epoch,
        "cycle_status_current_container": current_container,
        "cycle_state": cycle_state,
        "cycle_started_epoch": cycle.get("cycle_started_epoch") if cycle is not None else None,
        "cycle_completed_epoch": cycle.get("cycle_completed_epoch") if cycle is not None else None,
        "last_progress_epoch": cycle.get("last_progress_epoch") if cycle is not None else None,
        "last_success_epoch": last_success,
        "cycle_age_seconds": cycle_age,
        "cycle_item_count": cycle.get("item_count") if cycle is not None else None,
        "last_error_class": cycle.get("last_error_class") if cycle is not None else None,
    }


def evaluate_downloader(
    config: DownloaderConfig,
    *,
    observation: DockerObservation | None,
    cycle: Mapping[str, Any] | None,
    now_epoch: int,
    restart_events: int,
    container_generations: int = 1,
    resolution_count: int = 1,
    observation_error: str | None = None,
    cycle_error: str | None = None,
) -> dict[str, Any]:
    """Classify Docker and functional-cycle state for the downloader."""

    diagnostics = _docker_diagnostics(
        observation,
        cycle,
        restart_events=restart_events,
        container_generations=container_generations,
        now_epoch=now_epoch,
    )
    if not config.expected:
        return {"status": "ignored", "reason": "not_expected_active", **diagnostics}
    if observation_error:
        return {"status": "failed", "reason": "docker_inspection_failed", **diagnostics}
    if resolution_count == 0:
        return {"status": "failed", "reason": "container_absent", **diagnostics}
    if resolution_count > 1:
        return {"status": "failed", "reason": "container_selector_ambiguous", **diagnostics}
    if observation is None:
        return {"status": "failed", "reason": "container_absent", **diagnostics}
    if observation.oom_killed:
        return {"status": "failed", "reason": "container_oom_killed", **diagnostics}
    if observation.dead or observation.status == "dead":
        return {"status": "failed", "reason": "container_dead", **diagnostics}
    if not observation.running:
        reason = "container_restarting" if observation.restarting else "container_stopped"
        return {"status": "failed", "reason": reason, **diagnostics}
    if observation.docker_health == "unhealthy":
        return {"status": "failed", "reason": "docker_health_unhealthy", **diagnostics}

    if observation.started_epoch is None:
        return {
            "status": "failed",
            "reason": "container_start_time_unavailable",
            **diagnostics,
        }
    container_age = max(0, now_epoch - observation.started_epoch)
    if cycle_error or cycle is None:
        if container_age <= config.docker_health_starting_grace_seconds:
            return {"status": "starting", "reason": "awaiting_cycle_status", **diagnostics}
        return {"status": "failed", "reason": "cycle_status_invalid", **diagnostics}

    try:
        evidence_epoch = cycle_evidence_epoch(cycle)
    except ObservationError:
        if container_age <= config.docker_health_starting_grace_seconds:
            return {"status": "starting", "reason": "awaiting_cycle_status", **diagnostics}
        return {"status": "failed", "reason": "cycle_status_invalid", **diagnostics}
    if (
        evidence_epoch
        < observation.started_epoch - CONTAINER_GENERATION_CLOCK_TOLERANCE_SECONDS
    ):
        if container_age <= config.docker_health_starting_grace_seconds:
            return {
                "status": "starting",
                "reason": "awaiting_current_container_cycle",
                **diagnostics,
            }
        return {
            "status": "failed",
            "reason": "cycle_status_from_previous_container",
            **diagnostics,
        }

    state = cycle["state"]
    last_success = cycle.get("last_success_epoch")
    if state == "failed":
        return {"status": "degraded", "reason": "last_cycle_failed", **diagnostics}
    if state == "running":
        cycle_age = max(0, now_epoch - int(cycle["cycle_started_epoch"]))
        progress = cycle.get("last_progress_epoch")
        progress_anchor = (
            progress if type(progress) is int else int(cycle["cycle_started_epoch"])
        )
        if (
            config.maximum_progress_age_seconds is not None
            and now_epoch - progress_anchor > config.maximum_progress_age_seconds
        ):
            return {"status": "degraded", "reason": "cycle_progress_stale", **diagnostics}
        if cycle_age > config.maximum_cycle_seconds:
            return {"status": "degraded", "reason": "cycle_runtime_exceeded", **diagnostics}
        if (
            type(last_success) is int
            and now_epoch - last_success > config.maximum_success_age_seconds
        ):
            return {"status": "degraded", "reason": "successful_cycle_stale", **diagnostics}

    if state == "success" and (
        type(last_success) is not int
        or now_epoch - last_success > config.maximum_success_age_seconds
    ):
        return {"status": "degraded", "reason": "successful_cycle_stale", **diagnostics}
    if observation.docker_health == "starting":
        if container_age <= config.docker_health_starting_grace_seconds:
            return {"status": "starting", "reason": "docker_health_starting", **diagnostics}
        return {"status": "degraded", "reason": "docker_health_starting_stale", **diagnostics}
    if restart_events >= config.rapid_restart_count:
        return {"status": "degraded", "reason": "rapid_container_restarts", **diagnostics}
    if container_generations >= config.rapid_restart_count:
        return {
            "status": "degraded",
            "reason": "rapid_container_replacements",
            **diagnostics,
        }
    if state == "running" and last_success is None:
        return {"status": "starting", "reason": "initial_cycle_running", **diagnostics}
    if state == "running":
        return {"status": "healthy", "reason": "current_cycle_progressing", **diagnostics}
    return {"status": "healthy", "reason": "last_cycle_succeeded", **diagnostics}


def empty_transient_state() -> dict[str, Any]:
    """Return a fresh bounded monitor state."""

    return {"schema_version": STATE_SCHEMA_VERSION, "systemd": {}, "docker": {}}


def read_transient_state(path: Path) -> dict[str, Any]:
    """Read monitor state, safely resetting malformed or old data."""

    if not path.exists():
        return empty_transient_state()
    try:
        value = read_bounded_json(path, maximum_bytes=MAX_STATE_BYTES)
    except ObservationError:
        return empty_transient_state()
    if (
        not isinstance(value, dict)
        or value.get("schema_version") != STATE_SCHEMA_VERSION
        or not isinstance(value.get("systemd"), dict)
        or not isinstance(value.get("docker"), dict)
    ):
        return empty_transient_state()
    return {
        "schema_version": STATE_SCHEMA_VERSION,
        "systemd": {
            str(key)[:64]: item
            for key, item in list(value["systemd"].items())[:MAX_COMPONENTS]
            if isinstance(item, dict)
        },
        "docker": value["docker"],
    }


def atomic_write_json(path: Path, value: object, *, mode: int) -> None:
    """Atomically replace one JSON document with the requested file mode."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = -1
    temporary_path: str | None = None
    try:
        descriptor, temporary_path = tempfile.mkstemp(
            dir=path.parent, prefix=f".{path.name}.", text=True
        )
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            json.dump(value, stream, sort_keys=True, separators=(",", ":"))
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
        directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_path is not None:
            try:
                os.unlink(temporary_path)
            except FileNotFoundError:
                pass


def _component_problem_detail(component_id: str, component: Mapping[str, Any]) -> str:
    label = component_id.replace("_", " ")
    reason = str(component.get("reason", "unknown")).replace("_", " ")
    return f"{label}: {reason}"


def aggregate_report(*, now_epoch: int, components: Mapping[str, Mapping[str, Any]]) -> dict[str, Any]:
    """Build aggregate precedence, summary, and deterministic signature."""

    expected = [item for item in components.values() if item.get("status") != "ignored"]
    statuses = {str(item.get("status")) for item in expected}
    if "failed" in statuses:
        status = "failed"
    elif "degraded" in statuses:
        status = "degraded"
    elif "starting" in statuses:
        status = "starting"
    else:
        status = "healthy"
    problems = [
        component_id
        for component_id, item in components.items()
        if item.get("status") in {"failed", "degraded"}
    ]
    signatures = sorted(
        f"{component_id}:{components[component_id].get('reason', 'unknown')}"
        for component_id in problems
    )
    signature = hashlib.sha256("\n".join(signatures).encode("utf-8")).hexdigest()[:16]
    if problems:
        count = len(problems)
        summary = f"{count} supporting component{'s' if count != 1 else ''} need attention"
    elif status == "starting":
        summary = "Supporting components are awaiting an initial successful run"
    else:
        summary = "All expected supporting components are healthy"
    return {
        "schema_version": SCHEMA_VERSION,
        "checked_epoch": now_epoch,
        "status": status,
        "summary": summary,
        "problem_signature": signature,
        "problem_count": len(problems),
        "problem_components": sorted(problems),
        "problem_details": [
            _component_problem_detail(component_id, components[component_id])
            for component_id in sorted(problems)
        ],
        "components": dict(components),
    }


def evaluate_monitor(
    config: MonitorConfig,
    *,
    state: Mapping[str, Any],
    now_epoch: int,
    monotonic_now: float,
    runner: Runner = subprocess.run,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Observe every configured component and return report plus next state."""

    components: dict[str, dict[str, Any]] = {}
    next_systemd: dict[str, dict[str, Any]] = {}
    systemd_history = state.get("systemd", {})
    if not isinstance(systemd_history, dict):
        systemd_history = {}
    for item in config.systemd_components:
        timer: TimerObservation | None = None
        service: ServiceObservation | None = None
        timer_error = None
        service_error = None
        try:
            timer = inspect_timer(item.timer, runner=runner)
        except Exception as exc:  # isolate one configured component
            timer_error = type(exc).__name__
        try:
            service = inspect_service(item.service, runner=runner)
        except Exception as exc:  # isolate one configured component
            service_error = type(exc).__name__
        component, history = evaluate_systemd_component(
            item,
            timer=timer,
            service=service,
            now_epoch=now_epoch,
            monotonic_now=monotonic_now,
            history=systemd_history.get(item.id),
            timer_error=timer_error,
            service_error=service_error,
        )
        components[item.id] = component
        next_systemd[item.id] = history

    downloader = config.downloader
    docker_history = state.get("docker", {})
    if not isinstance(docker_history, dict):
        docker_history = {}
    docker_observation: DockerObservation | None = None
    docker_error = None
    resolution_count = 0
    restart_events = 0
    container_generations = 0
    next_docker = dict(docker_history)
    try:
        identities = resolve_container_ids(downloader.selector, runner=runner)
        resolution_count = len(identities)
        if resolution_count == 1:
            docker_observation = inspect_container(identities[0], runner=runner)
            next_docker, restart_events, container_generations = observe_docker_restarts(
                docker_history,
                docker_observation,
                now_epoch=now_epoch,
                window_seconds=downloader.rapid_restart_window_seconds,
            )
    except Exception as exc:  # Docker failure must not hide systemd results
        docker_error = type(exc).__name__

    cycle = None
    cycle_error = None
    try:
        cycle = read_cycle_status(downloader.cycle_status_file, now_epoch=now_epoch)
    except Exception as exc:
        cycle_error = type(exc).__name__
    components[downloader.id] = evaluate_downloader(
        downloader,
        observation=docker_observation,
        cycle=cycle,
        now_epoch=now_epoch,
        restart_events=restart_events,
        container_generations=container_generations,
        resolution_count=resolution_count,
        observation_error=docker_error,
        cycle_error=cycle_error,
    )

    next_state = {
        "schema_version": STATE_SCHEMA_VERSION,
        "systemd": next_systemd,
        "docker": next_docker,
    }
    return aggregate_report(now_epoch=now_epoch, components=components), next_state


def _required_absolute_environment_path(name: str, default: str | None = None) -> Path:
    raw = os.environ.get(name, default)
    if not raw:
        raise ConfigurationError(f"{name} is required")
    path = Path(raw)
    if not path.is_absolute() or ".." in path.parts:
        raise ConfigurationError(f"{name} must be an absolute path")
    return path


def main() -> int:
    """Run one monitor poll and publish its Home Assistant JSON document."""

    try:
        config_path = _required_absolute_environment_path(
            "MRS_SUPPORT_HEALTH_CONFIG",
            str(Path.home() / ".config/mrsMThatcher/support-health-monitor.json"),
        )
        output_path = _required_absolute_environment_path("MRS_SUPPORT_HEALTH_OUTPUT")
        # systemctl --user and busctl --user rely on the inherited runtime
        # directory, but the last observed oneshot outcomes must survive a
        # user-manager or host restart.  systemd does not retain completed
        # service metadata across that boundary.
        _required_absolute_environment_path("XDG_RUNTIME_DIR")
        state_root = _required_absolute_environment_path(
            "XDG_STATE_HOME", str(Path.home() / ".local/state")
        )
        state_dir = state_root / "mrsMThatcher/support-health-monitor"
        state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(state_dir, 0o700)
        state_path = state_dir / "support-health-state.json"
        config = load_config(config_path)
        state = read_transient_state(state_path)
        report, next_state = evaluate_monitor(
            config,
            state=state,
            now_epoch=int(time.time()),
            monotonic_now=time.monotonic(),
        )
        atomic_write_json(state_path, next_state, mode=0o600)
        atomic_write_json(output_path, report, mode=0o644)
        return 0
    except Exception as exc:
        print(f"mrs-support-health-monitor: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
