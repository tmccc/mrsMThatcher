from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest

import mrsMThatcher2 as bot
from tests.conftest import PRODUCTION_LOG, process_has_open_path


def managed_file_handlers() -> list[logging.Handler]:
    return [
        handler
        for handler in bot.log.handlers
        if getattr(handler, "_mrs_mthatcher_handler_kind", None) == "file"
    ]


@pytest.fixture(autouse=True)
def restore_console_logging(monkeypatch):
    monkeypatch.setattr(bot, "_PRODUCTION_BOOTSTRAPPED", False)
    yield
    bot.setup_logging(configure_file_logging=False)


def configure_bootstrap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "LOCAL_CONFIG_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "SELF_TEST_REQUESTED", False)
    monkeypatch.setattr(bot, "validate_production_credentials", lambda: None)


def test_plain_import_does_not_open_production_log():
    assert not process_has_open_path(PRODUCTION_LOG)
    assert managed_file_handlers() == []


def test_pytest_process_refuses_production_state_write():
    assert bot.test_process_production_state_write_blocked(
        Path("/disks/disk1/etc/mrsMThatcher/bot_state.json")
    )
    assert not bot.test_process_production_state_write_blocked(
        Path("/tmp/mrsMThatcher-test/bot_state.json")
    )


def test_bootstrap_without_file_logging_does_not_open_production_log(tmp_path, monkeypatch):
    configure_bootstrap(tmp_path, monkeypatch)
    bot.production_bootstrap(configure_file_logging=False)
    bot.log.warning("isolated no-file marker")
    assert managed_file_handlers() == []
    assert not process_has_open_path(PRODUCTION_LOG)


def test_pytest_process_cannot_explicitly_attach_production_log():
    with pytest.raises(RuntimeError, match="Refusing to attach pytest process"):
        bot.setup_logging(log_path=PRODUCTION_LOG)
    assert not process_has_open_path(PRODUCTION_LOG)


def test_bootstrap_with_temporary_log_writes_only_there(tmp_path, monkeypatch):
    configure_bootstrap(tmp_path, monkeypatch)
    target = tmp_path / "bootstrap.log"
    bot.production_bootstrap(log_path=target)
    bot.log.warning("temporary bootstrap marker")
    assert "temporary bootstrap marker" in target.read_text()
    assert [Path(getattr(handler, "baseFilename")) for handler in managed_file_handlers()] == [target]
    assert not process_has_open_path(PRODUCTION_LOG)


def test_repeated_bootstrap_does_not_duplicate_handlers_or_records(tmp_path, monkeypatch):
    configure_bootstrap(tmp_path, monkeypatch)
    target = tmp_path / "idempotent.log"
    bot.production_bootstrap(log_path=target)
    bot.production_bootstrap(log_path=tmp_path / "ignored.log")
    bot.log.warning("exactly-once-marker")
    assert len(managed_file_handlers()) == 1
    assert target.read_text().count("exactly-once-marker") == 1
    assert not (tmp_path / "ignored.log").exists()


def test_failed_bootstrap_can_log_to_temporary_file_without_live_log(tmp_path, monkeypatch):
    local = tmp_path / "local.json"
    local.write_text("{")
    monkeypatch.setattr(bot, "LOCAL_CONFIG_FILE", local)
    target = tmp_path / "failed.log"
    with pytest.raises(bot.LocalConfigError):
        bot.production_bootstrap(log_path=target)
    assert target.exists()
    assert bot._PRODUCTION_BOOTSTRAPPED is False
    assert not process_has_open_path(PRODUCTION_LOG)


def test_two_temporary_destinations_do_not_cross_contaminate(tmp_path):
    first, second = tmp_path / "first.log", tmp_path / "second.log"
    bot.setup_logging(log_path=first)
    bot.log.warning("first-only")
    bot.setup_logging(log_path=second)
    bot.log.warning("second-only")
    assert "first-only" in first.read_text() and "second-only" not in first.read_text()
    assert "second-only" in second.read_text() and "first-only" not in second.read_text()


def test_logger_does_not_propagate_or_duplicate_records(tmp_path):
    target = tmp_path / "propagation.log"
    bot.setup_logging(log_path=target)
    bot.log.warning("single-propagation-marker")
    assert bot.log.propagate is False
    assert target.read_text().count("single-propagation-marker") == 1


def test_pytest_marker_is_confined_to_temporary_log(tmp_path):
    target = tmp_path / "marker.log"
    bot.setup_logging(log_path=target)
    marker = f"/tmp/pytest-isolation-{os.getpid()}"
    bot.log.warning(marker)
    assert marker in target.read_text()
    assert not process_has_open_path(PRODUCTION_LOG)


def test_module_reload_does_not_accumulate_handlers_or_open_live_log(tmp_path):
    code = (
        "import importlib, logging, mrsMThatcher2 as b; "
        "importlib.reload(b); "
        "print(sum(bool(getattr(h, '_mrs_mthatcher_managed_handler', False)) for h in b.log.handlers)); "
        "print([getattr(h, '_mrs_mthatcher_handler_kind', None) for h in b.log.handlers])"
    )
    env = dict(os.environ, PYTHONPATH=str(Path(bot.__file__).resolve().parent))
    result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env, text=True, capture_output=True, check=True)
    assert result.stdout.strip().splitlines()[-2:] == ["1", "['import_console']"]
    assert not process_has_open_path(PRODUCTION_LOG)


def test_subprocess_bootstrap_uses_temporary_log(tmp_path):
    script = tmp_path / "mrsMThatcher2.py"
    script.write_bytes(Path(bot.__file__).read_bytes())
    (tmp_path / "mrsMThatcher.local.json").write_text("{")
    target = tmp_path / "subprocess.log"
    env = dict(
        os.environ,
        MRS_BASE_DIR=str(tmp_path),
        MRS_LOG_FILE=str(target),
        X_CONSUMER_KEY="dummy",
        X_CONSUMER_SECRET="dummy",
        X_ACCESS_TOKEN="dummy",
        X_ACCESS_SECRET="dummy",
        X_MY_USER_ID="12345",
        XAI_API_KEY="dummy",
    )
    result = subprocess.run([sys.executable, str(script)], cwd=tmp_path, env=env, text=True, capture_output=True)
    assert result.returncode != 0
    assert target.exists()
    assert not process_has_open_path(PRODUCTION_LOG)
