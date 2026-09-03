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
        timer_value=timer(next_epoch=NOW + 600),
        service_value=service(result="exit-code", status=7, invocation="failed-run"),
    )
    assert (result["status"], result["reason"]) == ("degraded", "last_run_failed")


@pytest.mark.parametrize("result", ["signal", "timeout", "core-dump"])
def test_signal_timeout_and_core_dump_are_failed(result: str) -> None:
    classified, _ = classify_systemd(
        timer_value=timer(next_epoch=NOW + 600),
        service_value=service(result=result, code=2, status=9, invocation=result)
    )
    assert (classified["status"], classified["reason"]) == (
        "failed",
        "service_hard_failure",
    )


def test_running_oneshot_within_runtime_without_prior_success_is_starting() -> None:
    result, history = classify_systemd(
        timer_value=timer(last=None),
        service_value=service(runtime=120, invocation="first-running"),
        history={},
    )
    assert (result["status"], result["reason"]) == (
        "starting",
        "first_run_in_progress",
    )
    assert result["current_runtime_seconds"] == 120
    assert history["last_success_epoch"] is None


def test_stale_previous_success_does_not_override_running_oneshot() -> None:
    previous = {
        "last_success_epoch": NOW - 3_600,
        "last_completed_invocation_id": "previous-success",
        "last_completed_outcome": "success",
    }
    result, history = classify_systemd(
        service_value=service(runtime=120, invocation="current-running"),
        history=previous,
    )
    assert (result["status"], result["reason"]) == (
        "healthy",
        "current_run_within_limit",
    )
    assert history == previous


def test_running_oneshot_at_exact_runtime_limit_is_healthy() -> None:
    previous = {
        "last_success_epoch": NOW - 3_600,
        "last_completed_invocation_id": "previous-success",
        "last_completed_outcome": "success",
    }
    result, history = classify_systemd(
        service_value=service(runtime=360, invocation="current-at-limit"),
        history=previous,
    )
    assert (result["status"], result["reason"]) == (
        "healthy",
        "current_run_within_limit",
    )
    assert result["current_runtime_seconds"] == 360
    assert history == previous


def test_running_oneshot_one_second_beyond_runtime_limit_is_degraded() -> None:
    previous = {
        "last_success_epoch": NOW - 3_600,
        "last_completed_invocation_id": "previous-success",
        "last_completed_outcome": "success",
    }
    result, history = classify_systemd(
        service_value=service(runtime=361, invocation="current-over-limit"),
        history=previous,
    )
    assert (result["status"], result["reason"]) == (
        "degraded",
        "service_runtime_exceeded",
    )
    assert result["current_runtime_seconds"] == 361
    assert history == previous


def test_previous_nonzero_outcome_remains_while_next_invocation_runs() -> None:
    previous = {
        "last_success_epoch": NOW - 3_600,
        "last_completed_invocation_id": "previous-nonzero",
        "last_completed_outcome": "nonzero",
    }
    result, history = classify_systemd(
        service_value=service(runtime=120, invocation="current-running"),
        history=previous,
    )
    assert (result["status"], result["reason"]) == (
        "degraded",
        "last_run_failed",
    )
    assert history == previous


def test_previous_hard_failure_remains_while_next_invocation_runs() -> None:
    previous = {
        "last_success_epoch": NOW - 3_600,
        "last_completed_invocation_id": "previous-hard-failure",
        "last_completed_outcome": "hard_failure",
    }
    result, history = classify_systemd(
        service_value=service(runtime=120, invocation="current-running"),
        history=previous,
    )
    assert (result["status"], result["reason"]) == (
        "failed",
        "service_hard_failure",
    )
    assert history == previous


def test_timer_overdue_precedes_running_within_runtime_classification() -> None:
    previous = {
        "last_success_epoch": NOW - 3_600,
        "last_completed_invocation_id": "previous-success",
        "last_completed_outcome": "success",
    }
    result, history = classify_systemd(
        timer_value=timer(next_epoch=NOW - 301),
        service_value=service(runtime=120, invocation="current-running"),
        history=previous,
    )
    assert (result["status"], result["reason"]) == (
        "degraded",
        "timer_overdue",
    )
    assert history == previous


def test_stale_previous_success_is_degraded_when_service_is_not_running() -> None:
    previous = {
        "last_success_epoch": NOW - 3_600,
        "last_completed_invocation_id": "previous-success",
        "last_completed_outcome": "success",
    }
    result, history = classify_systemd(
        service_value=service(
            start=NOW - 3_610,
            exit_epoch=NOW - 3_600,
            invocation="previous-success",
        ),
        history=previous,
    )
    assert (result["status"], result["reason"]) == (
        "degraded",
        "successful_run_stale",
    )
    assert history == previous


def test_later_successful_invocation_clears_earlier_failure() -> None:
    previous = {
        "last_success_epoch": NOW - 3_600,
        "last_completed_invocation_id": "bad",
        "last_completed_outcome": "nonzero",
    }
    running, history = classify_systemd(
        service_value=service(runtime=120, invocation="good"),
        history=previous,
    )
    completed, history = classify_systemd(
        service_value=service(invocation="good", exit_epoch=NOW - 10),
        timer_value=timer(last=NOW - 10, next_epoch=NOW + 890),
        history=history,
    )
    assert (running["status"], running["reason"]) == (
        "degraded",
        "last_run_failed",
    )
    assert (completed["status"], completed["reason"]) == (
        "healthy",
        "last_run_succeeded",
    )
    assert history["last_success_epoch"] == NOW - 10
    assert history["last_completed_invocation_id"] == "good"
    assert history["last_completed_outcome"] == "success"


def test_daily_calendar_timer_is_not_falsely_stale(monkeypatch) -> None:
    last_success = NOW - 88_800
    next_trigger = NOW + 1_200
    monkeypatch.setattr(
        monitor,
        "_local_utc_offset_seconds",
        lambda epoch: 3_600 if epoch == last_success else 0,
    )
    cfg = systemd_config(interval=86_400)
    result, _ = classify_systemd(
        cfg=cfg,
        timer_value=timer(last=last_success, next_epoch=next_trigger),
        service_value=service(
            start=last_success - 1,
            exit_epoch=last_success,
            invocation="daily",
        ),
    )
    assert result["status"] == "healthy"


def test_actual_next_timer_trigger_beyond_grace_is_overdue() -> None:
    result, _ = classify_systemd(
        cfg=systemd_config(interval=86_400),
        timer_value=timer(last=NOW - 86_000, next_epoch=NOW - 301),
        service_value=service(exit_epoch=NOW - 86_000, invocation="daily-overdue"),
    )
    assert (result["status"], result["reason"]) == ("degraded", "timer_overdue")


def test_actual_next_trigger_wins_during_repeated_autumn_hour(monkeypatch) -> None:
    last_success = NOW - 4_200
    next_trigger = NOW + 600
    monkeypatch.setattr(
        monitor,
        "_local_utc_offset_seconds",
        lambda epoch: 3_600 if epoch == last_success else 0,
    )
    result, _ = classify_systemd(
        cfg=systemd_config(interval=3_600),
        timer_value=timer(last=last_success, next_epoch=next_trigger),
        service_value=service(exit_epoch=last_success, invocation="autumn-hour"),
    )
    assert (result["status"], result["reason"]) == (
        "healthy",
        "last_run_succeeded",
    )


def test_ordinary_future_trigger_does_not_hide_stale_success(monkeypatch) -> None:
    monkeypatch.setattr(monitor, "_local_utc_offset_seconds", lambda _epoch: 0)
    last_success = NOW - 3_600
    result, _ = classify_systemd(
        cfg=systemd_config(interval=900),
        timer_value=timer(last=last_success, next_epoch=NOW + 600),
        service_value=service(exit_epoch=last_success, invocation="ordinary-stale"),
    )
    assert (result["status"], result["reason"]) == (
        "degraded",
        "successful_run_stale",
    )


def test_ordinary_recent_success_with_future_trigger_is_healthy(monkeypatch) -> None:
    monkeypatch.setattr(monitor, "_local_utc_offset_seconds", lambda _epoch: 0)
    last_success = NOW - 600
    result, _ = classify_systemd(
        cfg=systemd_config(interval=900),
        timer_value=timer(last=last_success, next_epoch=NOW + 300),
        service_value=service(exit_epoch=last_success, invocation="ordinary-recent"),
    )
    assert (result["status"], result["reason"]) == (
        "healthy",
        "last_run_succeeded",
    )


def test_old_success_stays_stale_across_clock_change(monkeypatch) -> None:
    last_success = NOW - 3 * 86_400
    next_trigger = NOW + 600
    monkeypatch.setattr(
        monitor,
        "_local_utc_offset_seconds",
        lambda epoch: 3_600 if epoch == last_success else 0,
    )
    result, _ = classify_systemd(
        cfg=systemd_config(interval=86_400),
        timer_value=timer(last=last_success, next_epoch=next_trigger),
        service_value=service(exit_epoch=last_success, invocation="old-dst"),
    )
    assert (result["status"], result["reason"]) == (
        "degraded",
        "successful_run_stale",
    )


def test_nominal_interval_is_fallback_when_next_trigger_is_unavailable() -> None:
    result, _ = classify_systemd(
        cfg=systemd_config(interval=3_600),
        timer_value=timer(last=NOW - 4_000, next_epoch=None),
        service_value=service(exit_epoch=NOW - 4_000, invocation="fallback"),
    )
    assert (result["status"], result["reason"]) == (
        "degraded",
        "successful_run_stale",
    )


def test_missing_local_utc_offset_grants_no_extension(monkeypatch) -> None:
    monkeypatch.setattr(
        monitor, "_local_utc_offset_seconds", lambda _epoch: None
    )
    assert monitor._clock_change_extension_seconds(1, 2) == 0


def test_clock_change_extension_is_capped_at_one_hour(monkeypatch) -> None:
    offsets = {1: -7_200, 2: 7_200}
    monkeypatch.setattr(
        monitor, "_local_utc_offset_seconds", lambda epoch: offsets[epoch]
    )
    assert monitor._clock_change_extension_seconds(1, 2) == 3_600


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("@1787849000", 1_787_849_000), ("@1787849000.123456", 1_787_849_000)],
)
def test_unix_systemd_timestamp_parsing(raw: str, expected: int) -> None:
    assert monitor._parse_systemd_epoch(raw) == expected


def test_invalid_systemd_timestamp_fails_inspection() -> None:
    def runner(
        _arguments: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        return completed(timer_show_output(last="Thu 2026-08-27 20:00:00 BST"))

    with pytest.raises(monitor.ObservationError, match="invalid Unix timestamp"):
        monitor.inspect_timer("mrs-job.timer", runner=runner)


def test_systemctl_show_requests_unix_timestamps() -> None:
    calls: list[list[str]] = []

    def runner(
        arguments: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        return completed(timer_show_output())

    inspected = monitor.inspect_timer("mrs-job.timer", runner=runner)
    assert inspected.last_trigger_epoch == NOW - 600
    assert "--timestamp=unix" in calls[0]


def test_systemd_249_fallback_reads_raw_dbus_microseconds() -> None:
    calls: list[list[str]] = []

    def runner(
        arguments: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        if "--timestamp=unix" in arguments:
            return completed(returncode=1, stderr="Invalid value: unix.\n")
        if arguments[0] == monitor.SYSTEMCTL:
            return completed(
                timer_show_output(
                    last="Thu 2026-08-27 20:00:00 BST",
                    next_value="Thu 2026-08-27 20:15:00 BST",
                )
            )
        if "GetUnit" in arguments:
            return completed('o "/org/freedesktop/systemd1/unit/mrs_2djob_2etimer"\n')
        return completed(f"t {(NOW - 600) * 1_000_000}\nt {(NOW + 300) * 1_000_000}\n")

    inspected = monitor.inspect_timer("mrs-job.timer", runner=runner)
    assert inspected.last_trigger_epoch == NOW - 600
    assert inspected.next_trigger_epoch == NOW + 300
    assert any(call[0] == monitor.BUSCTL for call in calls)


def test_systemd_249_fallback_treats_usec_infinity_as_unavailable() -> None:
    def runner(
        arguments: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        if "--timestamp=unix" in arguments:
            return completed(returncode=1, stderr="Invalid value: unix.\n")
        if arguments[0] == monitor.SYSTEMCTL:
            return completed(
                timer_show_output(
                    last="Thu 2026-08-27 20:00:00 BST",
                    next_value="",
                )
            )
        if "GetUnit" in arguments:
            return completed('o "/org/freedesktop/systemd1/unit/mrs_2djob_2etimer"\n')
        return completed(
            f"t {(NOW - 600) * 1_000_000}\n"
            f"t {monitor.SYSTEMD_USEC_INFINITY}\n"
        )

    inspected = monitor.inspect_timer("mrs-job.timer", runner=runner)

    assert inspected.last_trigger_epoch == NOW - 600
    assert inspected.next_trigger_epoch is None


def test_systemd_249_fallback_preserves_missing_unit_classification() -> None:
    calls: list[list[str]] = []

    def runner(
        arguments: list[str], **_kwargs: object
    ) -> subprocess.CompletedProcess[str]:
        calls.append(arguments)
        if "--timestamp=unix" in arguments:
            return completed(returncode=1, stderr="Invalid value: unix.\n")
        return completed(
            timer_show_output().replace("LoadState=loaded", "LoadState=not-found")
        )

    inspected = monitor.inspect_timer("mrs-missing.timer", runner=runner)
    classified, _ = monitor.evaluate_systemd_component(
        systemd_config(),
        timer=inspected,
        service=service(),
        now_epoch=NOW,
        monotonic_now=MONOTONIC_NOW,
    )
    assert (classified["status"], classified["reason"]) == (
        "failed",
        "timer_missing",
    )
    assert all(call[0] != monitor.BUSCTL for call in calls)


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


def downloader_config(
    *,
    maximum_cycle_seconds: int = 7_200,
    maximum_progress_age_seconds: int = 600,
) -> monitor.DownloaderConfig:
    return monitor.DownloaderConfig(
        id="downloader",
        expected=True,
        selector=monitor.DockerSelector(compose_project="site", compose_service="mirror"),
        cycle_status_file=Path("/private/downloader-health.json"),
        maximum_cycle_seconds=maximum_cycle_seconds,
        maximum_success_age_seconds=604_800,
        maximum_progress_age_seconds=maximum_progress_age_seconds,
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
    started_age: int | None = 10_000,
) -> monitor.DockerObservation:
    return monitor.DockerObservation(
        container_id=container_id,
        name="thatcher-mirror",
        image="thatcher-mirror:test",
        created_epoch=NOW - 20_000,
        status=status,
        running=running,
        restarting=restarting,
        oom_killed=oom,
        dead=dead,
        exit_code=0,
        started_epoch=NOW - started_age if started_age is not None else None,
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
    started_age: int | None = None,
) -> dict:
    completed = NOW - age if state != "running" else None
    success = NOW - last_success_age if last_success_age is not None else None
    if started_age is None:
        started_age = age + (30 if state != "running" else 0)
    return {
        "state": state,
        "cycle_started_epoch": NOW - started_age,
        "cycle_completed_epoch": completed,
        "last_success_epoch": success,
        "last_progress_epoch": NOW - progress_age if progress_age is not None else None,
        "duration_seconds": 30 if state != "running" else None,
        "item_count": 10,
        "last_error_class": "transient_failure" if state == "failed" else None,
    }


def classify_downloader(
    *,
    cfg: monitor.DownloaderConfig | None = None,
    observation: monitor.DockerObservation | None = None,
    cycle_value: dict | None = None,
    restart_events: int = 0,
    container_generations: int = 1,
    resolution_count: int = 1,
) -> dict:
    return monitor.evaluate_downloader(
        cfg or downloader_config(),
        observation=docker_observation() if observation is None else observation,
        cycle=cycle() if cycle_value is None else cycle_value,
        now_epoch=NOW,
        restart_events=restart_events,
        container_generations=container_generations,
        resolution_count=resolution_count,
    )


def completed(
    stdout: str = "", *, returncode: int = 0, stderr: str = ""
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def timer_show_output(
    *,
    last: str | None = None,
    next_value: str | None = None,
) -> str:
    values = {
        "LoadState": "loaded",
        "ActiveState": "active",
        "SubState": "waiting",
        "UnitFileState": "enabled",
        "LastTriggerUSec": last or f"@{NOW - 600}",
        "LastTriggerUSecMonotonic": "1000000",
        "NextElapseUSecRealtime": next_value or f"@{NOW + 300}.123456",
        "NextElapseUSecMonotonic": "2000000",
        "Triggers": "mrs-job.service",
    }
    return "\n".join(f"{name}={values[name]}" for name in monitor.TIMER_PROPERTIES) + "\n"


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
    first, restart_count, generations = monitor.observe_docker_restarts(
        {}, docker_observation(container_id="a" * 64), now_epoch=NOW - 60, window_seconds=900
    )
    assert restart_count == 0
    assert generations == 1
    second, count, generations = monitor.observe_docker_restarts(
        first, docker_observation(container_id="b" * 64), now_epoch=NOW, window_seconds=900
    )
    assert count == 0
    assert generations == 2
    assert len(second["recent_container_ids"]) == 2
    assert classify_downloader(container_generations=generations)["status"] == "healthy"


def test_three_actual_restart_events_within_window_are_degraded() -> None:
    history, _, _ = monitor.observe_docker_restarts(
        {}, docker_observation(restart_count=0), now_epoch=NOW - 60, window_seconds=900
    )
    history, count, generations = monitor.observe_docker_restarts(
        history,
        docker_observation(restart_count=3),
        now_epoch=NOW,
        window_seconds=900,
    )
    result = classify_downloader(
        restart_events=count, container_generations=generations
    )
    assert count == 3
    assert (result["status"], result["reason"]) == (
        "degraded",
        "rapid_container_restarts",
    )


def test_three_rapid_container_generations_are_degraded() -> None:
    history, _, _ = monitor.observe_docker_restarts(
        {}, docker_observation(container_id="a" * 64), now_epoch=NOW - 120, window_seconds=900
    )
    history, _, _ = monitor.observe_docker_restarts(
        history,
        docker_observation(container_id="b" * 64),
        now_epoch=NOW - 60,
        window_seconds=900,
    )
    history, restarts, generations = monitor.observe_docker_restarts(
        history,
        docker_observation(container_id="c" * 64),
        now_epoch=NOW,
        window_seconds=900,
    )
    result = classify_downloader(
        restart_events=restarts, container_generations=generations
    )
    assert generations == 3
    assert result["container_generations_window"] == 3
    assert (result["status"], result["reason"]) == (
        "degraded",
        "rapid_container_replacements",
    )


def test_old_container_generations_are_pruned_outside_window() -> None:
    history, _, _ = monitor.observe_docker_restarts(
        {}, docker_observation(container_id="a" * 64), now_epoch=NOW - 2_000, window_seconds=900
    )
    history, _, _ = monitor.observe_docker_restarts(
        history,
        docker_observation(container_id="b" * 64),
        now_epoch=NOW - 1_000,
        window_seconds=900,
    )
    _, restarts, generations = monitor.observe_docker_restarts(
        history,
        docker_observation(container_id="c" * 64),
        now_epoch=NOW,
        window_seconds=900,
    )
    assert restarts == 0
    assert generations == 1
    assert classify_downloader(container_generations=generations)["status"] == "healthy"


def test_repeated_polls_of_same_container_keep_one_generation() -> None:
    history: dict = {}
    for observed_at in (NOW - 120, NOW - 60, NOW):
        history, restarts, generations = monitor.observe_docker_restarts(
            history,
            docker_observation(container_id="a" * 64),
            now_epoch=observed_at,
            window_seconds=900,
        )
    assert restarts == 0
    assert generations == 1
    assert len(history["recent_container_ids"]) == 1


def test_stopped_container_failure_precedes_replacement_warning() -> None:
    result = classify_downloader(
        observation=docker_observation(status="exited", running=False),
        restart_events=3,
        container_generations=3,
    )
    assert (result["status"], result["reason"]) == (
        "failed",
        "container_stopped",
    )


def test_malformed_and_expired_generation_history_is_discarded() -> None:
    history = {
        "container_id": "a" * 64,
        "restart_count": 0,
        "restart_events": ["bad", NOW - 901],
        "recent_container_ids": [
            "bad",
            {"id": "a" * 64, "first_seen_epoch": "bad"},
            {"id": "a" * 64, "first_seen_epoch": NOW - 901},
        ],
    }
    next_history, restarts, generations = monitor.observe_docker_restarts(
        history,
        docker_observation(container_id="a" * 64),
        now_epoch=NOW,
        window_seconds=900,
    )
    assert restarts == 0
    assert generations == 1
    assert next_history["recent_container_ids"] == [
        {"id": "a" * 64, "first_seen_epoch": NOW}
    ]


def test_success_twenty_seconds_before_start_within_grace_is_starting() -> None:
    old_success = cycle(
        state="success",
        age=120,
        last_success_age=120,
        progress_age=None,
        started_age=150,
    )
    result = classify_downloader(
        observation=docker_observation(started_age=100), cycle_value=old_success
    )
    assert (result["status"], result["reason"]) == (
        "starting",
        "awaiting_current_container_cycle",
    )
    assert result["cycle_status_current_container"] is False
    assert result["container_started_epoch"] - result["cycle_evidence_epoch"] == 20


def test_success_twenty_seconds_before_start_after_grace_is_failed() -> None:
    old_success = cycle(
        state="success",
        age=321,
        last_success_age=321,
        progress_age=None,
        started_age=351,
    )
    result = classify_downloader(
        observation=docker_observation(started_age=301), cycle_value=old_success
    )
    assert (result["status"], result["reason"]) == (
        "failed",
        "cycle_status_from_previous_container",
    )
    assert result["container_started_epoch"] - result["cycle_evidence_epoch"] == 20


def test_previous_container_failure_is_not_current_cycle_failure() -> None:
    old_failure = cycle(
        state="failed",
        age=400,
        last_success_age=500,
        progress_age=None,
        started_age=430,
    )
    result = classify_downloader(
        observation=docker_observation(started_age=301), cycle_value=old_failure
    )
    assert (result["status"], result["reason"]) == (
        "failed",
        "cycle_status_from_previous_container",
    )
    assert result["reason"] != "last_cycle_failed"


def test_current_container_running_cycle_is_accepted() -> None:
    current = cycle(
        state="running", age=900, last_success_age=None, progress_age=10
    )
    result = classify_downloader(
        observation=docker_observation(started_age=1_000), cycle_value=current
    )
    assert (result["status"], result["reason"]) == (
        "starting",
        "initial_cycle_running",
    )
    assert result["cycle_status_current_container"] is True


def test_current_container_success_cycle_is_accepted() -> None:
    result = classify_downloader(
        observation=docker_observation(started_age=1_000),
        cycle_value=cycle(state="success", age=60, progress_age=None),
    )
    assert (result["status"], result["reason"]) == (
        "healthy",
        "last_cycle_succeeded",
    )
    assert result["cycle_status_current_container"] is True


def test_cycle_evidence_equal_to_container_start_is_accepted() -> None:
    result = classify_downloader(
        observation=docker_observation(started_age=100),
        cycle_value=cycle(
            state="success",
            age=100,
            last_success_age=100,
            progress_age=None,
            started_age=130,
        ),
    )
    assert (result["status"], result["reason"]) == (
        "healthy",
        "last_cycle_succeeded",
    )
    assert monitor.CONTAINER_GENERATION_CLOCK_TOLERANCE_SECONDS == 0
    assert result["cycle_status_current_container"] is True
    assert result["container_started_epoch"] == result["cycle_evidence_epoch"]


def test_cycle_evidence_one_second_before_start_within_grace_is_rejected() -> None:
    result = classify_downloader(
        observation=docker_observation(started_age=100),
        cycle_value=cycle(
            state="success",
            age=101,
            last_success_age=101,
            progress_age=None,
            started_age=131,
        ),
    )
    assert (result["status"], result["reason"]) == (
        "starting",
        "awaiting_current_container_cycle",
    )
    assert result["cycle_status_current_container"] is False
    assert result["container_started_epoch"] - result["cycle_evidence_epoch"] == 1


def test_cycle_evidence_one_second_before_start_after_grace_is_failed() -> None:
    result = classify_downloader(
        observation=docker_observation(started_age=301),
        cycle_value=cycle(
            state="success",
            age=302,
            last_success_age=302,
            progress_age=None,
            started_age=332,
        ),
    )
    assert (result["status"], result["reason"]) == (
        "failed",
        "cycle_status_from_previous_container",
    )
    assert result["cycle_status_current_container"] is False
    assert result["container_started_epoch"] - result["cycle_evidence_epoch"] == 1


def test_missing_container_start_time_fails_closed() -> None:
    result = classify_downloader(observation=docker_observation(started_age=None))
    assert (result["status"], result["reason"]) == (
        "failed",
        "container_start_time_unavailable",
    )


def test_same_container_id_restart_invalidates_old_cycle_evidence() -> None:
    container_id = "d" * 64
    old_success = cycle(
        state="success",
        age=321,
        last_success_age=321,
        progress_age=None,
        started_age=351,
    )
    before = classify_downloader(
        observation=docker_observation(
            container_id=container_id, started_age=1_000
        ),
        cycle_value=old_success,
    )
    after = classify_downloader(
        observation=docker_observation(container_id=container_id, started_age=301),
        cycle_value=old_success,
    )
    assert before["status"] == "healthy"
    assert (after["status"], after["reason"]) == (
        "failed",
        "cycle_status_from_previous_container",
    )
    assert after["cycle_status_current_container"] is False
    assert after["container_started_epoch"] - after["cycle_evidence_epoch"] == 20


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


def test_initial_cycle_without_progress_before_deadline_is_starting() -> None:
    cfg = downloader_config(
        maximum_cycle_seconds=2_592_000, maximum_progress_age_seconds=1_800
    )
    result = classify_downloader(
        cfg=cfg,
        cycle_value=cycle(
            state="running",
            age=1_799,
            last_success_age=None,
            progress_age=None,
        ),
    )
    assert (result["status"], result["reason"]) == (
        "starting",
        "initial_cycle_running",
    )


def test_initial_cycle_without_progress_after_deadline_is_degraded() -> None:
    cfg = downloader_config(
        maximum_cycle_seconds=2_592_000, maximum_progress_age_seconds=1_800
    )
    result = classify_downloader(
        cfg=cfg,
        cycle_value=cycle(
            state="running",
            age=1_801,
            last_success_age=None,
            progress_age=None,
        ),
    )
    assert (result["status"], result["reason"]) == (
        "degraded",
        "cycle_progress_stale",
    )


def test_cycle_with_prior_success_without_new_progress_becomes_stale() -> None:
    cfg = downloader_config(
        maximum_cycle_seconds=2_592_000, maximum_progress_age_seconds=1_800
    )
    result = classify_downloader(
        cfg=cfg,
        cycle_value=cycle(
            state="running",
            age=1_801,
            last_success_age=100,
            progress_age=None,
        ),
    )
    assert (result["status"], result["reason"]) == (
        "degraded",
        "cycle_progress_stale",
    )


def test_recent_progress_overrides_older_cycle_start() -> None:
    cfg = downloader_config(
        maximum_cycle_seconds=2_592_000, maximum_progress_age_seconds=1_800
    )
    result = classify_downloader(
        cfg=cfg,
        cycle_value=cycle(
            state="running", age=5_000, last_success_age=100, progress_age=10
        ),
    )
    assert (result["status"], result["reason"]) == (
        "healthy",
        "current_cycle_progressing",
    )


def test_progress_deadline_applies_during_thirty_day_runtime_allowance() -> None:
    cfg = downloader_config(
        maximum_cycle_seconds=2_592_000, maximum_progress_age_seconds=1_800
    )
    result = classify_downloader(
        cfg=cfg,
        cycle_value=cycle(
            state="running",
            age=20_000,
            last_success_age=None,
            progress_age=1_801,
        ),
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


def test_monitor_history_survives_runtime_directory_change(
    tmp_path: Path, monkeypatch
) -> None:
    state_root = tmp_path / "state"
    output = tmp_path / "output.json"
    monkeypatch.setenv("MRS_SUPPORT_HEALTH_CONFIG", str(tmp_path / "config.json"))
    monkeypatch.setenv("MRS_SUPPORT_HEALTH_OUTPUT", str(output))
    monkeypatch.setenv("XDG_STATE_HOME", str(state_root))
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime-one"))
    monkeypatch.setattr(monitor, "load_config", lambda _path: object())

    observed_states: list[dict] = []

    def evaluate(_config: object, *, state: dict, **_kwargs: object) -> tuple[dict, dict]:
        observed_states.append(copy.deepcopy(state))
        next_state = monitor.empty_transient_state()
        next_state["systemd"]["daily_job"] = {
            "last_success_epoch": NOW,
            "last_completed_invocation_id": "prior-boot-success",
            "last_completed_outcome": "success",
        }
        return {"schema_version": 1}, next_state

    monkeypatch.setattr(monitor, "evaluate_monitor", evaluate)

    assert monitor.main() == 0
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path / "runtime-two"))
    assert monitor.main() == 0

    state_path = (
        state_root
        / "mrsMThatcher"
        / "support-health-monitor"
        / "support-health-state.json"
    )
    assert observed_states[0] == monitor.empty_transient_state()
    assert observed_states[1]["systemd"]["daily_job"]["last_success_epoch"] == NOW
    assert stat.S_IMODE(state_path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(state_path.stat().st_mode) == 0o600


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
