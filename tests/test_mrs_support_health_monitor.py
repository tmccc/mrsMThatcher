from __future__ import annotations

import copy
import json
import os
from pathlib import Path
import stat
import subprocess

import pytest

import mrs_support_health_monitor as monitor


NOW = 2_000_000_000
MONOTONIC_NOW = 50_000.0


def systemd_config(*, expected: bool = True, interval: int = 900) -> monitor.SystemdComponentConfig:
    return monitor.SystemdComponentConfig(
        id="job",
        timer="mrs-job.timer",
        service="mrs-job.service",
        expected=expected,
        expected_interval_seconds=interval,
        overdue_grace_seconds=300,
        maximum_runtime_seconds=360,
    )


def timer(
    *,
    load: str = "loaded",
    active: str = "active",
    sub: str = "waiting",
    enabled: str = "enabled",
    last: int | None = NOW - 600,
    next_epoch: int | None = NOW + 300,
) -> monitor.TimerObservation:
    return monitor.TimerObservation(
        load_state=load,
        active_state=active,
        sub_state=sub,
        unit_file_state=enabled,
        last_trigger_epoch=last,
        next_trigger_epoch=next_epoch,
        triggers=("mrs-job.service",),
    )


def service(
    *,
    load: str = "loaded",
    active: str = "inactive",
    sub: str = "dead",
    result: str = "success",
    code: int = 1,
    status: int = 0,
    invocation: str = "invocation-a",
    start: int | None = NOW - 610,
    exit_epoch: int | None = NOW - 600,
    runtime: int | None = None,
) -> monitor.ServiceObservation:
    if runtime is not None:
        active = "active"
        sub = "running"
        start_monotonic = int((MONOTONIC_NOW - runtime) * 1_000_000)
        exit_monotonic = 0
        start = NOW - runtime
        exit_epoch = None
    else:
        start_monotonic = 10
        exit_monotonic = 20 if exit_epoch is not None else 0
    return monitor.ServiceObservation(
        load_state=load,
        active_state=active,
        sub_state=sub,
        result=result,
        exec_main_code=code,
        exec_main_status=status,
        invocation_id=invocation,
        start_epoch=start,
        exit_epoch=exit_epoch,
        start_monotonic_usec=start_monotonic,
        exit_monotonic_usec=exit_monotonic,
    )


def classify_systemd(
    *,
    cfg: monitor.SystemdComponentConfig | None = None,
    timer_value: monitor.TimerObservation | None = None,
    service_value: monitor.ServiceObservation | None = None,
    history: dict | None = None,
    timer_error: str | None = None,
) -> tuple[dict, dict]:
    return monitor.evaluate_systemd_component(
        cfg or systemd_config(),
        timer=timer() if timer_value is None else timer_value,
        service=service() if service_value is None else service_value,
        now_epoch=NOW,
        monotonic_now=MONOTONIC_NOW,
        history=history,
        timer_error=timer_error,
    )


def test_successful_inactive_oneshot_with_active_timer_is_healthy() -> None:
    result, _ = classify_systemd()

    assert result["status"] == "healthy"
    assert result["reason"] == "last_run_succeeded"
    assert result["service_substate"] == "dead"
    assert result["service_active"] is False


def test_inactive_dead_alone_is_not_failure() -> None:
    result, _ = classify_systemd(service_value=service(active="inactive", sub="dead"))
    assert result["status"] == "healthy"


def test_expected_timer_missing_is_failed() -> None:
    result, _ = monitor.evaluate_systemd_component(
        systemd_config(),
        timer=None,
        service=service(),
        now_epoch=NOW,
        monotonic_now=MONOTONIC_NOW,
    )
    assert (result["status"], result["reason"]) == ("failed", "timer_missing")


def test_expected_timer_masked_is_failed() -> None:
    result, _ = classify_systemd(timer_value=timer(enabled="masked"))
    assert (result["status"], result["reason"]) == ("failed", "timer_masked")


def test_expected_timer_disabled_is_failed() -> None:
    result, _ = classify_systemd(timer_value=timer(enabled="disabled"))
    assert (result["status"], result["reason"]) == ("failed", "timer_disabled")


def test_explicitly_non_expected_timer_is_ignored_with_diagnostics() -> None:
    result, _ = classify_systemd(
        cfg=systemd_config(expected=False),
        timer_value=timer(enabled="disabled", active="inactive"),
        service_value=service(load="not-found"),
    )
    assert result["status"] == "ignored"
    assert result["timer_enabled"] is False
    assert result["service_loaded"] is False


def test_active_timer_awaiting_first_future_trigger_is_starting() -> None:
    result, _ = classify_systemd(
        timer_value=timer(last=None, next_epoch=NOW + 100),
        service_value=service(invocation="", start=None, exit_epoch=None, code=0),
    )
    assert (result["status"], result["reason"]) == ("starting", "awaiting_first_run")


def test_first_trigger_overdue_beyond_grace_is_degraded() -> None:
    result, _ = classify_systemd(
        timer_value=timer(last=None, next_epoch=NOW - 301),
        service_value=service(invocation="", start=None, exit_epoch=None, code=0),
    )
    assert (result["status"], result["reason"]) == ("degraded", "timer_overdue")


def test_successful_latest_invocation_is_healthy() -> None:
    result, history = classify_systemd(service_value=service(invocation="latest-success"))
    assert result["status"] == "healthy"
    assert history["last_success_epoch"] == NOW - 600


def test_nonzero_latest_invocation_is_degraded() -> None:
    result, _ = classify_systemd(
        service_value=service(result="exit-code", status=7, invocation="failed-run")
    )
    assert (result["status"], result["reason"]) == ("degraded", "last_run_failed")


@pytest.mark.parametrize("result", ["signal", "timeout", "core-dump"])
def test_signal_timeout_and_core_dump_are_failed(result: str) -> None:
    classified, _ = classify_systemd(
        service_value=service(result=result, code=2, status=9, invocation=result)
    )
    assert (classified["status"], classified["reason"]) == (
        "failed",
        "service_hard_failure",
    )


def test_running_oneshot_within_runtime_is_not_a_problem() -> None:
    result, _ = classify_systemd(
        timer_value=timer(last=None),
        service_value=service(runtime=120, invocation="first-running"),
    )
    assert result["status"] == "starting"
    assert result["current_runtime_seconds"] == 120


def test_running_oneshot_beyond_runtime_is_degraded() -> None:
    result, _ = classify_systemd(service_value=service(runtime=361))
    assert (result["status"], result["reason"]) == (
        "degraded",
        "service_runtime_exceeded",
    )


def test_later_successful_invocation_clears_earlier_failure() -> None:
    first, history = classify_systemd(
        service_value=service(result="exit-code", status=2, invocation="bad")
    )
    second, history = classify_systemd(
        service_value=service(invocation="good", exit_epoch=NOW - 10),
        timer_value=timer(last=NOW - 10, next_epoch=NOW + 890),
        history=history,
    )
    assert first["status"] == "degraded"
    assert second["status"] == "healthy"
    assert history["last_completed_outcome"] == "success"


def test_daily_calendar_timer_is_not_falsely_stale() -> None:
    cfg = systemd_config(interval=86_400)
    result, _ = classify_systemd(
        cfg=cfg,
        timer_value=timer(last=NOW - 82_800, next_epoch=NOW + 3_600),
        service_value=service(
            start=NOW - 82_801,
            exit_epoch=NOW - 82_800,
            invocation="daily",
        ),
    )
    assert result["status"] == "healthy"


def test_aggregate_precedence_and_ignored_exclusion() -> None:
    components = {
        "healthy": {"status": "healthy", "reason": "ok"},
        "starting": {"status": "starting", "reason": "first"},
        "degraded": {"status": "degraded", "reason": "late"},
        "failed": {"status": "failed", "reason": "missing"},
        "ignored": {"status": "ignored", "reason": "not_expected_active"},
    }
    assert monitor.aggregate_report(now_epoch=NOW, components=components)["status"] == "failed"
    components.pop("failed")
    assert monitor.aggregate_report(now_epoch=NOW, components=components)["status"] == "degraded"
    components.pop("degraded")
    assert monitor.aggregate_report(now_epoch=NOW, components=components)["status"] == "starting"
    components.pop("starting")
    assert monitor.aggregate_report(now_epoch=NOW, components=components)["status"] == "healthy"


def downloader_config() -> monitor.DownloaderConfig:
    return monitor.DownloaderConfig(
        id="downloader",
        expected=True,
        selector=monitor.DockerSelector(compose_project="site", compose_service="mirror"),
        cycle_status_file=Path("/private/downloader-health.json"),
        maximum_cycle_seconds=7_200,
        maximum_success_age_seconds=604_800,
        maximum_progress_age_seconds=600,
        docker_health_starting_grace_seconds=300,
        rapid_restart_count=3,
        rapid_restart_window_seconds=900,
    )


def docker_observation(
    *,
    container_id: str = "a" * 64,
    status: str = "running",
    running: bool = True,
    restarting: bool = False,
    oom: bool = False,
    dead: bool = False,
    health: str = "not_configured",
    restart_count: int = 0,
    started_age: int = 1_000,
) -> monitor.DockerObservation:
    return monitor.DockerObservation(
        container_id=container_id,
        name="thatcher-mirror",
        image="thatcher-mirror:test",
        created_epoch=NOW - 2_000,
        status=status,
        running=running,
        restarting=restarting,
        oom_killed=oom,
        dead=dead,
        exit_code=0,
        started_epoch=NOW - started_age,
        finished_epoch=None,
        restart_count=restart_count,
        docker_health=health,
        compose_project="site",
        compose_service="mirror",
    )


def cycle(
    *,
    state: str = "success",
    age: int = 60,
    last_success_age: int | None = 60,
    progress_age: int | None = 10,
) -> dict:
    completed = NOW - age if state != "running" else None
    success = NOW - last_success_age if last_success_age is not None else None
    return {
        "state": state,
        "cycle_started_epoch": NOW - age - (30 if state != "running" else 0),
        "cycle_completed_epoch": completed,
        "last_success_epoch": success,
        "last_progress_epoch": NOW - progress_age if progress_age is not None else None,
        "duration_seconds": 30 if state != "running" else None,
        "item_count": 10,
        "last_error_class": "transient_failure" if state == "failed" else None,
    }


def classify_downloader(
    *,
    observation: monitor.DockerObservation | None = None,
    cycle_value: dict | None = None,
    restart_events: int = 0,
    resolution_count: int = 1,
) -> dict:
    return monitor.evaluate_downloader(
        downloader_config(),
        observation=docker_observation() if observation is None else observation,
        cycle=cycle() if cycle_value is None else cycle_value,
        now_epoch=NOW,
        restart_events=restart_events,
        resolution_count=resolution_count,
    )


def completed(stdout: str = "", *, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, "")


def test_exact_compose_label_selector_resolves_one_container() -> None:
    calls: list[list[str]] = []

    def runner(arguments: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        return completed("a" * 64 + "\n")

    ids = monitor.resolve_container_ids(downloader_config().selector, runner=runner)
    assert ids == ["a" * 64]
    assert "label=com.docker.compose.project=site" in calls[0]
    assert "label=com.docker.compose.service=mirror" in calls[0]


def test_docker_nanosecond_timestamps_are_accepted() -> None:
    assert monitor._parse_iso_epoch("2033-05-18T03:33:20.123456789Z") == NOW


def test_zero_matching_containers_is_failed() -> None:
    result = monitor.evaluate_downloader(
        downloader_config(),
        observation=None,
        cycle=None,
        now_epoch=NOW,
        restart_events=0,
        resolution_count=0,
    )
    assert (result["status"], result["reason"]) == ("failed", "container_absent")


def test_multiple_matching_containers_is_failed() -> None:
    result = classify_downloader(resolution_count=2)
    assert (result["status"], result["reason"]) == (
        "failed",
        "container_selector_ambiguous",
    )


@pytest.mark.parametrize("health", ["not_configured", "healthy"])
def test_running_container_without_or_with_healthy_healthcheck_is_accepted(health: str) -> None:
    result = classify_downloader(observation=docker_observation(health=health))
    assert result["status"] == "healthy"


def test_docker_health_starting_within_grace_is_starting() -> None:
    result = classify_downloader(
        observation=docker_observation(health="starting", started_age=100)
    )
    assert (result["status"], result["reason"]) == (
        "starting",
        "docker_health_starting",
    )


def test_docker_health_starting_beyond_grace_is_degraded() -> None:
    result = classify_downloader(
        observation=docker_observation(health="starting", started_age=301)
    )
    assert (result["status"], result["reason"]) == (
        "degraded",
        "docker_health_starting_stale",
    )


def test_docker_health_unhealthy_is_failed() -> None:
    result = classify_downloader(observation=docker_observation(health="unhealthy"))
    assert (result["status"], result["reason"]) == (
        "failed",
        "docker_health_unhealthy",
    )


@pytest.mark.parametrize(
    ("observation", "reason"),
    [
        (docker_observation(status="exited", running=False), "container_stopped"),
        (docker_observation(status="dead", running=False, dead=True), "container_dead"),
        (docker_observation(status="exited", running=False, oom=True), "container_oom_killed"),
    ],
)
def test_stopped_dead_and_oom_containers_are_failed(
    observation: monitor.DockerObservation, reason: str
) -> None:
    result = classify_downloader(observation=observation)
    assert (result["status"], result["reason"]) == ("failed", reason)


def test_one_planned_container_replacement_is_not_a_crash_loop() -> None:
    first, _ = monitor.observe_docker_restarts(
        {}, docker_observation(container_id="a" * 64), now_epoch=NOW - 60, window_seconds=900
    )
    second, count = monitor.observe_docker_restarts(
        first, docker_observation(container_id="b" * 64), now_epoch=NOW, window_seconds=900
    )
    assert count == 0
    assert len(second["recent_container_ids"]) == 2


def test_three_actual_restart_events_within_window_are_degraded() -> None:
    history, _ = monitor.observe_docker_restarts(
        {}, docker_observation(restart_count=0), now_epoch=NOW - 60, window_seconds=900
    )
    history, count = monitor.observe_docker_restarts(
        history,
        docker_observation(restart_count=3),
        now_epoch=NOW,
        window_seconds=900,
    )
    result = classify_downloader(restart_events=count)
    assert count == 3
    assert (result["status"], result["reason"]) == (
        "degraded",
        "rapid_container_restarts",
    )


def test_fresh_successful_cycle_is_healthy() -> None:
    result = classify_downloader(cycle_value=cycle(state="success"))
    assert (result["status"], result["reason"]) == (
        "healthy",
        "last_cycle_succeeded",
    )


def test_failed_cycle_is_degraded() -> None:
    result = classify_downloader(cycle_value=cycle(state="failed"))
    assert (result["status"], result["reason"]) == (
        "degraded",
        "last_cycle_failed",
    )


def test_running_cycle_within_maximum_is_not_a_problem() -> None:
    result = classify_downloader(cycle_value=cycle(state="running", age=3_600))
    assert result["status"] == "healthy"


def test_initial_running_cycle_within_maximum_is_starting() -> None:
    result = classify_downloader(
        cycle_value=cycle(state="running", age=3_600, last_success_age=None)
    )
    assert result["status"] == "starting"


def test_running_cycle_beyond_maximum_is_degraded() -> None:
    result = classify_downloader(
        cycle_value=cycle(state="running", age=7_201, progress_age=10)
    )
    assert (result["status"], result["reason"]) == (
        "degraded",
        "cycle_runtime_exceeded",
    )


def test_stale_functional_progress_is_degraded() -> None:
    result = classify_downloader(
        cycle_value=cycle(state="running", age=1_000, progress_age=601)
    )
    assert (result["status"], result["reason"]) == (
        "degraded",
        "cycle_progress_stale",
    )


def test_stale_last_success_is_degraded() -> None:
    result = classify_downloader(
        cycle_value=cycle(state="success", last_success_age=604_801)
    )
    assert (result["status"], result["reason"]) == (
        "degraded",
        "successful_cycle_stale",
    )


def test_later_successful_cycle_recovers() -> None:
    failed = classify_downloader(cycle_value=cycle(state="failed"))
    recovered = classify_downloader(cycle_value=cycle(state="success", age=5))
    assert failed["status"] == "degraded"
    assert recovered["status"] == "healthy"


def test_docker_environment_and_credentials_are_never_requested_or_published() -> None:
    assert ".Config.Env" not in monitor.DOCKER_INSPECT_TEMPLATE
    result = classify_downloader()
    encoded = json.dumps(result).lower()
    assert "environment" not in encoded
    assert "credential" not in encoded
    assert "command" not in encoded


def valid_raw_config() -> dict:
    return {
        "schema_version": 1,
        "systemd_components": [
            {
                "id": "job",
                "timer": "mrs-job.timer",
                "service": "mrs-job.service",
                "expected": True,
                "expected_interval_seconds": 900,
                "overdue_grace_seconds": 300,
                "maximum_runtime_seconds": 360,
            }
        ],
        "downloader": {
            "id": "downloader",
            "expected": True,
            "selector": {"compose_project": "site", "compose_service": "mirror"},
            "cycle_status_file": "/private/downloader-health.json",
            "maximum_cycle_seconds": 7_200,
            "maximum_success_age_seconds": 604_800,
            "maximum_progress_age_seconds": 600,
            "docker_health_starting_grace_seconds": 300,
            "rapid_restart_count": 3,
            "rapid_restart_window_seconds": 900,
        },
    }


def test_strict_bounded_configuration_accepts_declarative_example() -> None:
    config = monitor.validate_config(valid_raw_config())
    assert config.systemd_components[0].timer == "mrs-job.timer"
    assert config.downloader.selector.compose_project == "site"


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: value.update(command="echo no"),
        lambda value: value.update(schema_version=2),
        lambda value: value["systemd_components"][0].update(expected=1),
        lambda value: value["systemd_components"][0].update(timer="../../bad.timer"),
        lambda value: value["downloader"].update(cycle_status_file="relative.json"),
        lambda value: value["downloader"].update(maximum_cycle_seconds=0),
        lambda value: value["downloader"].update(
            selector={"container_name": "mirror", "compose_project": "site"}
        ),
    ],
)
def test_invalid_configuration_is_rejected(mutator) -> None:
    value = valid_raw_config()
    mutator(value)
    with pytest.raises(monitor.ConfigurationError):
        monitor.validate_config(value)


def test_atomic_output_and_state_replacement_modes(tmp_path: Path) -> None:
    output = tmp_path / "output.json"
    output.write_text("old\n", encoding="utf-8")
    old_inode = output.stat().st_ino
    monitor.atomic_write_json(output, {"new": True}, mode=0o644)
    assert json.loads(output.read_text(encoding="utf-8")) == {"new": True}
    assert output.stat().st_ino != old_inode
    assert stat.S_IMODE(output.stat().st_mode) == 0o644

    state = tmp_path / "runtime" / "state.json"
    monitor.atomic_write_json(state, monitor.empty_transient_state(), mode=0o600)
    assert stat.S_IMODE(state.stat().st_mode) == 0o600
    assert not list(state.parent.glob(".state.json.*"))


def test_malformed_transient_state_resets_safely(tmp_path: Path) -> None:
    path = tmp_path / "state.json"
    path.write_text("{bad", encoding="utf-8")
    assert monitor.read_transient_state(path) == monitor.empty_transient_state()


def test_problem_signature_is_deterministic() -> None:
    left = {
        "b": {"status": "failed", "reason": "missing"},
        "a": {"status": "degraded", "reason": "late"},
    }
    right = dict(reversed(list(left.items())))
    assert monitor.aggregate_report(now_epoch=NOW, components=left)[
        "problem_signature"
    ] == monitor.aggregate_report(now_epoch=NOW, components=right)["problem_signature"]


def test_component_observation_failure_does_not_hide_other_components(monkeypatch) -> None:
    configs = (
        systemd_config(),
        monitor.SystemdComponentConfig(
            id="second",
            timer="mrs-second.timer",
            service="mrs-second.service",
            expected=True,
            expected_interval_seconds=900,
            overdue_grace_seconds=300,
            maximum_runtime_seconds=360,
        ),
    )
    config = monitor.MonitorConfig(configs, downloader_config())

    def inspect_timer(unit: str, **_kwargs: object) -> monitor.TimerObservation:
        if unit == "mrs-job.timer":
            raise monitor.ObservationError("fixture failure")
        value = timer()
        return monitor.TimerObservation(**{**value.__dict__, "triggers": ("mrs-second.service",)})

    monkeypatch.setattr(monitor, "inspect_timer", inspect_timer)
    monkeypatch.setattr(monitor, "inspect_service", lambda _unit, **_kwargs: service())
    monkeypatch.setattr(monitor, "resolve_container_ids", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(monitor, "read_cycle_status", lambda *_args, **_kwargs: cycle())
    report, _ = monitor.evaluate_monitor(
        config,
        state=monitor.empty_transient_state(),
        now_epoch=NOW,
        monotonic_now=MONOTONIC_NOW,
    )
    assert report["components"]["job"]["status"] == "failed"
    assert report["components"]["second"]["status"] == "healthy"
    assert "downloader" in report["components"]


def test_complete_monitor_failure_exits_nonzero_without_output(
    tmp_path: Path, monkeypatch
) -> None:
    output = tmp_path / "output.json"
    monkeypatch.setenv("MRS_SUPPORT_HEALTH_CONFIG", str(tmp_path / "missing.json"))
    monkeypatch.setenv("MRS_SUPPORT_HEALTH_OUTPUT", str(output))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime"))
    assert monitor.main() == 1
    assert not output.exists()


def test_monitor_has_no_network_or_provider_dependency() -> None:
    source = Path(monitor.__file__).read_text(encoding="utf-8")
    for forbidden in (
        "import requests",
        "import httpx",
        "import socket",
        "import openai",
        "import tweepy",
        "api.openai.com",
        "api.x.ai",
        "api.twitter.com",
    ):
        assert forbidden not in source
    assert "shell=True" not in source
