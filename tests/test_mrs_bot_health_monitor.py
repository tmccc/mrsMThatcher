from __future__ import annotations

import json
from pathlib import Path
import stat
import subprocess

import pytest

from mrs_bot_health import atomic_write_json
import mrs_bot_health_monitor as monitor


NOW = 2_000_000_000


def service(
    active_state: str = "active",
    *,
    sub_state: str = "running",
    main_pid: int = 12000,
) -> monitor.ServiceStatus:
    return monitor.ServiceStatus(
        active_state=active_state,
        sub_state=sub_state,
        result="success",
        main_pid=main_pid,
        n_restarts=0,
        active_enter_monotonic_usec=1,
        exec_main_start_monotonic_usec=1,
    )


def progress(
    *,
    age: int = 20,
    paused: bool = False,
    remote_write_blocked: bool = False,
    error_count: int = 0,
    last_error_age: int | None = None,
    instance_id: str = "instance-a",
    phase: str = "sleep",
) -> dict:
    updated = NOW - age
    return {
        "schema_version": 1,
        "instance_id": instance_id,
        "pid": 12345,
        "started_epoch": NOW - 3_600,
        "updated_epoch": updated,
        "progress_sequence": 100,
        "phase": phase,
        "phase_started_epoch": updated,
        "paused": paused,
        "remote_write_blocked": remote_write_blocked,
        "last_loop_started_epoch": NOW - 80,
        "last_loop_completed_epoch": NOW - 70,
        "recent_error_count": error_count,
        "last_error_epoch": (
            NOW - last_error_age if last_error_age is not None else None
        ),
        "last_error_level": "ERROR" if last_error_age is not None else None,
        "last_error_summary": (
            "ApiError in mrsMThatcher.reply_checks"
            if last_error_age is not None
            else None
        ),
    }


MATCH = monitor.ProcessObservation(True, True, "expected_python_child")


def classify(
    *,
    service_value: monitor.ServiceStatus | None = None,
    service_age: int | None = 600,
    progress_value: dict | None = None,
    snapshot_error: str | None = None,
    process_value: monitor.ProcessObservation | None = MATCH,
    instance_restarts: int = 0,
) -> dict:
    return monitor.evaluate_health(
        now_epoch=NOW,
        service=service_value or service(),
        service_age=service_age,
        progress=progress() if progress_value is None else progress_value,
        snapshot_error=snapshot_error,
        process=process_value,
        bot_commit="abcdef123456",
        instance_restarts=instance_restarts,
    )


def test_healthy_monitor_classification_and_output_schema() -> None:
    result = classify()

    assert result["status"] == "healthy"
    assert result["reason"] == "ok"
    assert result["progress_age_seconds"] == 20
    assert result["bot_commit"] == "abcdef123456"
    assert set(result) == {
        "schema_version",
        "checked_epoch",
        "status",
        "reason",
        "summary",
        "service_active",
        "service_substate",
        "service_result",
        "wrapper_pid",
        "service_nrestarts",
        "bot_pid",
        "bot_instance_id",
        "bot_commit",
        "progress_age_seconds",
        "phase",
        "phase_age_seconds",
        "last_loop_completed_epoch",
        "recent_error_count",
        "last_error_epoch",
        "last_error_level",
        "last_error_summary",
        "instance_restarts_15m",
    }


def test_starting_grace_without_snapshot() -> None:
    result = monitor.evaluate_health(
        now_epoch=NOW,
        service=service("activating", sub_state="start"),
        service_age=60,
        progress=None,
        snapshot_error="file not found",
        process=None,
        bot_commit="abcdef123456",
        instance_restarts=0,
    )

    assert result["status"] == "starting"
    assert result["reason"] == "awaiting_health_snapshot"


def test_invalid_snapshot_after_startup_grace_is_failed() -> None:
    result = monitor.evaluate_health(
        now_epoch=NOW,
        service=service(),
        service_age=181,
        progress=None,
        snapshot_error="malformed JSON",
        process=None,
        bot_commit="abcdef123456",
        instance_restarts=0,
    )

    assert result["status"] == "failed"
    assert result["reason"] == "health_snapshot_invalid"


def test_fresh_intentional_pause_is_paused() -> None:
    result = classify(progress_value=progress(paused=True))

    assert result["status"] == "paused"
    assert result["reason"] == "intentional_pause"


def test_stopped_while_paused_deployment_grace() -> None:
    result = classify(
        service_value=service("inactive", sub_state="dead", main_pid=0),
        progress_value=progress(age=599, paused=True),
        process_value=None,
    )

    assert result["status"] == "paused"
    assert result["reason"] == "paused_service_stopped_grace"


def test_stopped_while_paused_grace_expiry_is_failed() -> None:
    result = classify(
        service_value=service("inactive", sub_state="dead", main_pid=0),
        progress_value=progress(age=601, paused=True),
        process_value=None,
    )

    assert result["status"] == "failed"
    assert result["reason"] == "service_inactive"


def test_active_process_with_stale_progress_is_stalled() -> None:
    result = classify(progress_value=progress(age=601, phase="ai_call"))

    assert result["status"] == "stalled"
    assert result["reason"] == "progress_stale"
    assert "10 minutes" in result["summary"]


def test_inactive_service_is_failed() -> None:
    result = classify(
        service_value=service("failed", sub_state="failed", main_pid=0),
        progress_value=progress(),
        process_value=None,
    )

    assert result["status"] == "failed"
    assert result["reason"] == "service_inactive"


def test_repeated_errors_are_degraded_but_one_error_is_healthy() -> None:
    degraded = classify(
        progress_value=progress(error_count=3, last_error_age=60)
    )
    healthy = classify(
        progress_value=progress(error_count=1, last_error_age=60)
    )

    assert degraded["status"] == "degraded"
    assert degraded["reason"] == "repeated_errors"
    assert healthy["status"] == "healthy"


def test_remote_write_block_is_degraded() -> None:
    result = classify(progress_value=progress(remote_write_blocked=True))

    assert result["status"] == "degraded"
    assert result["reason"] == "remote_write_blocked"


def test_remote_write_block_is_not_hidden_by_intentional_pause() -> None:
    result = classify(
        progress_value=progress(paused=True, remote_write_blocked=True)
    )

    assert result["status"] == "degraded"
    assert result["reason"] == "remote_write_blocked"


def test_rapid_instance_changes_are_degraded() -> None:
    state = monitor.empty_monitor_state()
    state, restarts = monitor.observe_instance(
        state, instance_id="instance-a", now_epoch=NOW - 120
    )
    state, restarts = monitor.observe_instance(
        state, instance_id="instance-b", now_epoch=NOW - 60
    )
    state, restarts = monitor.observe_instance(
        state, instance_id="instance-c", now_epoch=NOW
    )

    result = classify(
        progress_value=progress(instance_id="instance-c"),
        instance_restarts=restarts,
    )
    assert result["status"] == "degraded"
    assert result["reason"] == "rapid_child_restarts"
    assert result["instance_restarts_15m"] == 2


def test_one_child_restart_is_not_degraded() -> None:
    state, _ = monitor.observe_instance(
        monitor.empty_monitor_state(),
        instance_id="instance-a",
        now_epoch=NOW - 60,
    )
    _state, restarts = monitor.observe_instance(
        state,
        instance_id="instance-b",
        now_epoch=NOW,
    )

    result = classify(
        progress_value=progress(instance_id="instance-b"),
        instance_restarts=restarts,
    )
    assert restarts == 1
    assert result["status"] == "healthy"


def test_missing_or_mismatched_python_child_respects_startup_grace() -> None:
    mismatch = monitor.ProcessObservation(True, False, "unexpected_command")
    starting = classify(
        service_age=600,
        progress_value=progress(age=120),
        process_value=mismatch,
    )
    failed = classify(
        service_age=600,
        progress_value=progress(age=181),
        process_value=mismatch,
    )

    assert starting["status"] == "starting"
    assert failed["status"] == "failed"
    assert failed["reason"] == "python_child_missing"


def test_pid_reuse_and_process_identity_with_fake_proc(tmp_path: Path) -> None:
    expected = tmp_path / "project" / "mrsMThatcher2.py"
    expected.parent.mkdir()
    expected.write_text("# test", encoding="utf-8")
    proc = tmp_path / "proc"
    process = proc / "12345"
    process.mkdir(parents=True)
    (process / "cmdline").write_bytes(
        b"/usr/bin/python3\0" + str(expected).encode() + b"\0"
    )
    (process / "status").write_text("Name:\tpython3\nPPid:\t12000\n", encoding="utf-8")

    matched = monitor.inspect_bot_process(
        12345,
        wrapper_pid=12000,
        expected_script=expected,
        proc_root=proc,
    )
    reused = monitor.inspect_bot_process(
        12345,
        wrapper_pid=12001,
        expected_script=expected,
        proc_root=proc,
    )

    assert matched.matches is True
    assert reused.matches is False
    assert reused.reason == "unexpected_parent"


def test_systemd_show_is_mocked_and_uses_explicit_properties(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def fake_run(command: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
        seen.extend(command)
        output = "\n".join(
            [
                "ActiveState=active",
                "SubState=running",
                "Result=success",
                "MainPID=12000",
                "NRestarts=2",
                "ActiveEnterTimestampMonotonic=1000000",
                "ExecMainStartTimestampMonotonic=900000",
            ]
        )
        return subprocess.CompletedProcess(command, 0, output, "")

    monkeypatch.setattr(monitor.subprocess, "run", fake_run)
    observed = monitor.read_service_status()

    assert observed.active is True
    assert observed.main_pid == 12000
    assert observed.n_restarts == 2
    property_argument = next(item for item in seen if item.startswith("--property="))
    assert set(property_argument.removeprefix("--property=").split(",")) == set(
        monitor.SYSTEMD_PROPERTIES
    )


def test_monitor_output_atomic_replacement_is_world_readable(tmp_path: Path) -> None:
    target = tmp_path / "ha" / "health.json"
    first = classify()
    atomic_write_json(target, first, mode=0o644)
    second = dict(first, checked_epoch=NOW + 60, status="degraded")
    atomic_write_json(target, second, mode=0o644)

    assert json.loads(target.read_text(encoding="utf-8")) == second
    assert stat.S_IMODE(target.stat().st_mode) == 0o644
    assert list(target.parent.glob(f".{target.name}.*")) == []


def test_quiet_but_regularly_progressing_bot_is_healthy() -> None:
    result = classify(
        progress_value=progress(
            age=25,
            phase="sleep",
            error_count=0,
            last_error_age=None,
        )
    )

    assert result["status"] == "healthy"
    assert "reply" not in result["reason"]
    assert "post" not in result["reason"]
