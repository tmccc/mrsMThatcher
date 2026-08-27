from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import stat
import threading

import pytest

import mrs_bot_health as health


class MutableClock:
    def __init__(self, value: float) -> None:
        self.value = value

    def __call__(self) -> float:
        return self.value


def test_atomic_valid_health_file_replacement_is_private(tmp_path: Path) -> None:
    target = tmp_path / "runtime" / "bot-health.json"
    reporter = health.BotHealthReporter(
        target,
        clock=MutableClock(1_000),
        pid=12345,
        instance_id="instance-a",
    )

    reporter.progress("startup")
    first = json.loads(target.read_text(encoding="utf-8"))
    reporter.progress("main_loop", loop_started=True)
    second = json.loads(target.read_text(encoding="utf-8"))

    assert first["schema_version"] == 1
    assert second["progress_sequence"] == first["progress_sequence"] + 1
    assert second["phase"] == "main_loop"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert list(target.parent.glob(f".{target.name}.*")) == []


def test_no_partial_json_becomes_visible_during_replacements(tmp_path: Path) -> None:
    target = tmp_path / "bot-health.json"
    clock = MutableClock(2_000)
    reporter = health.BotHealthReporter(target, clock=clock)
    reporter.progress("startup")
    finished = threading.Event()
    failures: list[BaseException] = []

    def writer() -> None:
        try:
            for index in range(150):
                clock.value = 2_001 + index
                reporter.progress("main_loop" if index % 2 else "sleep")
        except BaseException as exc:  # pragma: no cover - assertion aid
            failures.append(exc)
        finally:
            finished.set()

    thread = threading.Thread(target=writer)
    thread.start()
    while not finished.is_set():
        try:
            value = json.loads(target.read_text(encoding="utf-8"))
            assert value["schema_version"] == 1
        except BaseException as exc:  # pragma: no cover - assertion aid
            failures.append(exc)
            break
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert failures == []


def test_progress_sequence_timestamp_phase_and_loop_advancement(tmp_path: Path) -> None:
    clock = MutableClock(3_000)
    reporter = health.BotHealthReporter(tmp_path / "health.json", clock=clock)

    reporter.progress("startup")
    first = reporter.snapshot()
    clock.value = 3_010
    reporter.progress("reply_checks", loop_started=True)
    second = reporter.snapshot()
    clock.value = 3_020
    reporter.progress("sleep", loop_completed=True)
    third = reporter.snapshot()

    assert second["progress_sequence"] == first["progress_sequence"] + 1
    assert second["updated_epoch"] == 3_010
    assert second["phase_started_epoch"] == 3_010
    assert second["last_loop_started_epoch"] == 3_010
    assert third["updated_epoch"] == 3_020
    assert third["last_loop_completed_epoch"] == 3_020


def test_paused_and_remote_write_blocked_states(tmp_path: Path) -> None:
    reporter = health.BotHealthReporter(
        tmp_path / "health.json",
        clock=MutableClock(4_000),
    )

    reporter.progress("paused", paused=True)
    assert reporter.snapshot()["paused"] is True
    reporter.progress("remote_write_blocked", remote_write_blocked=True)
    assert reporter.snapshot()["remote_write_blocked"] is True
    reporter.progress(
        "main_loop",
        paused=False,
        remote_write_blocked=False,
    )
    snapshot = reporter.snapshot()
    assert snapshot["paused"] is False
    assert snapshot["remote_write_blocked"] is False


def test_recent_error_counting_and_error_free_expiry(tmp_path: Path) -> None:
    clock = MutableClock(5_000)
    reporter = health.BotHealthReporter(tmp_path / "health.json", clock=clock)

    reporter.record_error(level="ERROR", summary="ApiError in reply checks")
    clock.value = 5_100
    reporter.record_error(level="ERROR", summary="ApiError in reply checks")
    clock.value = 5_200
    reporter.record_error(level="CRITICAL", summary="RuntimeError in reply checks")
    assert reporter.snapshot()["recent_error_count"] == 3

    clock.value = 6_999
    reporter.progress("sleep")
    assert reporter.snapshot()["recent_error_count"] == 3
    clock.value = 7_000
    reporter.progress("main_loop")
    snapshot = reporter.snapshot()
    assert snapshot["recent_error_count"] == 0
    assert snapshot["last_error_epoch"] == 5_200


def test_health_write_failure_never_propagates_and_is_rate_limited(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monotonic = MutableClock(10)
    warnings: list[str] = []

    def fail_write(*_args: object, **_kwargs: object) -> None:
        raise OSError("synthetic write failure")

    monkeypatch.setattr(health, "atomic_write_json", fail_write)
    reporter = health.BotHealthReporter(
        tmp_path / "health.json",
        monotonic_clock=monotonic,
        write_failure_callback=warnings.append,
    )

    reporter.progress("startup")
    reporter.progress("main_loop")
    assert len(warnings) == 1
    monotonic.value += health.WRITE_FAILURE_LOG_INTERVAL_SECONDS
    reporter.progress("sleep")
    assert len(warnings) == 2


def test_logging_observer_records_safe_summary_without_message(tmp_path: Path) -> None:
    reporter = health.BotHealthReporter(tmp_path / "health.json")
    observer = health.HealthLoggingObserver(reporter)
    record = logging.LogRecord(
        "mrsMThatcher.component",
        logging.ERROR,
        __file__,
        1,
        "sensitive external text must not be copied",
        (),
        (ValueError, ValueError("detail"), None),
        "reply_boundary",
    )

    observer.emit(record)
    snapshot = reporter.snapshot()

    assert snapshot["recent_error_count"] == 1
    assert snapshot["last_error_level"] == "ERROR"
    assert snapshot["last_error_summary"] == (
        "ValueError in mrsMThatcher.component.reply_boundary"
    )
    assert "sensitive" not in snapshot["last_error_summary"]


def test_logging_observer_defers_filesystem_write_until_genuine_progress(
    tmp_path: Path,
) -> None:
    path = tmp_path / "health.json"
    clock = MutableClock(8_000)
    reporter = health.BotHealthReporter(path, clock=clock)
    reporter.progress("reply_checks")
    before = path.read_bytes()
    observer = health.HealthLoggingObserver(reporter)
    record = logging.LogRecord(
        "mrsMThatcher.transport",
        logging.ERROR,
        __file__,
        1,
        "remote transaction detail",
        (),
        None,
        "write_boundary",
    )

    clock.value = 8_010
    observer.emit(record)

    assert path.read_bytes() == before
    assert reporter.snapshot()["recent_error_count"] == 1
    clock.value = 8_020
    reporter.progress("main_loop")
    published = json.loads(path.read_text(encoding="utf-8"))
    assert published["recent_error_count"] == 1
    assert published["last_error_summary"] == (
        "ERROR in mrsMThatcher.transport.write_boundary"
    )


def test_logging_observer_suppresses_recursive_observation() -> None:
    logger = logging.getLogger("mrs-health-recursion-test")
    logger.handlers.clear()
    logger.propagate = False
    logger.setLevel(logging.ERROR)

    class RecursiveReporter:
        calls = 0

        def record_error(self, **_kwargs: object) -> None:
            self.calls += 1
            logger.error("nested error")

    reporter = RecursiveReporter()
    observer = health.HealthLoggingObserver(reporter)  # type: ignore[arg-type]
    logger.addHandler(observer)
    try:
        logger.error("outer error")
    finally:
        logger.removeHandler(observer)

    assert reporter.calls == 1


def test_test_mode_health_output_requires_isolated_path(tmp_path: Path) -> None:
    allowed = tmp_path / "isolated"
    allowed.mkdir()
    inside = allowed / "health.json"
    environment = {
        "MRS_BOT_HEALTH_FILE": str(inside),
        "MRS_PYTEST_RUNTIME_ROOT": str(allowed),
    }

    assert health.health_file_path_from_environment(
        test_mode=True,
        environ=environment,
    ) == inside.resolve()
    assert health.health_file_path_from_environment(
        test_mode=True,
        environ={"MRS_PYTEST_RUNTIME_ROOT": str(allowed)},
    ) is None

    environment["MRS_BOT_HEALTH_FILE"] = "/run/user/1000/mrsMThatcher/bot-health.json"
    with pytest.raises(health.HealthPathError):
        health.health_file_path_from_environment(
            test_mode=True,
            environ=environment,
        )


def test_health_modules_have_no_provider_or_network_client(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    combined = "\n".join(
        (root / name).read_text(encoding="utf-8").lower()
        for name in ("mrs_bot_health.py", "mrs_bot_health_monitor.py")
    )

    assert "import requests" not in combined
    assert "openai" not in combined
    assert "xai" not in combined
    assert "api.x.com" not in combined
