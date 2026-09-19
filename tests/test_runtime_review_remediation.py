"""Offline regressions for lifecycle, scheduling and one-shot safety findings."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys
from unittest.mock import Mock

import pytest

import mrsMThatcher2 as bot
import mrs_bot_cli_execution as cli
from tests.helpers.bot_fixtures import isolate_bot_runtime  # noqa: F401
from tools.check_python_documentation import maintained_python_files


@pytest.mark.parametrize("root_adapter", [False, True])
@pytest.mark.parametrize("logging_fails", [False, True])
def test_barrier_inspection_errors_remain_alive_until_later_durable_proof(monkeypatch, root_adapter, logging_fails):
    trace = []
    attempts = iter([OSError("unreadable"), ValueError("malformed marker"), OSError("transient"), True])

    def inspect():
        trace.append("inspect")
        result = next(attempts)
        if isinstance(result, Exception):
            raise result
        return result

    logger = Mock()
    if logging_fails:
        logger.critical.side_effect = OSError("logging disk full")
    dependencies = dict(
        durable_remote_write_safety_barrier_exists=inspect,
        remote_write_safety_incident_is_latched=lambda: True,
        sleep=lambda seconds: trace.append(("sleep", seconds)),
        log=logger,
    )
    if root_adapter:
        for name, value in dependencies.items():
            monkeypatch.setattr(bot, name, value)
        bot.wait_for_durable_barrier_before_one_shot_exit(lane="regression")
    else:
        cli.wait_for_durable_barrier_before_one_shot_exit(lane="regression", **dependencies)
    assert trace == ["inspect", ("sleep", 60), "inspect", ("sleep", 60), "inspect", ("sleep", 60), "inspect"]


@pytest.mark.parametrize("failure", [KeyboardInterrupt(), SystemExit(3)])
def test_barrier_inspection_preserves_controlled_baseexception(monkeypatch, failure):
    monkeypatch.setattr(bot, "remote_write_safety_incident_is_latched", lambda: True)
    monkeypatch.setattr(bot, "durable_remote_write_safety_barrier_exists", Mock(side_effect=failure))
    with pytest.raises(type(failure)) as caught:
        bot.wait_for_durable_barrier_before_one_shot_exit(lane="interrupt")
    assert caught.value is failure


@pytest.mark.parametrize("retry,valid", [(-1, False), (0, True), (60, True), (61, False)])
def test_quote_retry_spacing_must_fit_check_interval(retry, valid):
    errors = bot.validate_runtime_config_values({"QUOTE_CHECK_EVERY_SECONDS": 60, "QUOTE_CHECK_SPACING_RETRY_SECONDS": retry})
    assert (not errors) is valid


def test_legacy_future_quote_epoch_repair_is_saved_once_without_poll_loop(monkeypatch):
    current = 2_000_000_000
    state = bot.default_state()
    state["last_quote_tweet_check_epoch"] = current + 999_999
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", False)
    monkeypatch.setattr(bot, "ENABLE_QUOTE_TWEET_CHECKS", True)
    save = Mock()
    poll = Mock(return_value=bot.QUOTE_CHECK_STATUS_SKIPPED_SPACING)
    monkeypatch.setattr(bot, "save_state", save)
    monkeypatch.setattr(bot, "maybe_reply_to_quote_tweets", poll)
    for tick in (current, current + 1, current + 2):
        bot.run_reply_lane_checks_for_tick(state, tick)
    assert state["last_quote_tweet_check_epoch"] == current
    save.assert_called_once_with(state)
    poll.assert_not_called()


@pytest.mark.parametrize("value", ["2030-01-02", "2030-01-02T03:04:05", "2030-01-02 03:04"])
def test_runtime_control_rejects_ambient_timezone_deadlines(value):
    with pytest.raises(ValueError, match="timezone"):
        bot.parse_control_time(value)


def test_runtime_control_explicit_offsets_identify_same_instant():
    assert bot.parse_control_time("2030-01-02T03:04:05+00:00") == bot.parse_control_time("2030-01-02T04:04:05+01:00")
    assert bot.parse_control_time("2030-01-02T03:04:05Z") == bot.parse_control_time("2030-01-02T03:04:05+00:00")


def test_archive_excludes_in_tree_environments_build_outputs_and_caches(tmp_path):
    for directory in (".venv/lib/python3.10/site-packages", "venv", "env/site-packages", "build", "dist", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".cache"):
        target = tmp_path / directory / "undocumented.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("pass\n")
    (tmp_path / "owned.py").write_text('"""Owned source."""\n')
    assert maintained_python_files(tmp_path) == [Path("owned.py")]


def _child_environment(tmp_path):
    return dict(os.environ, MRS_TEST_MODE="1", MRS_BASE_DIR=str(tmp_path), MRS_LOG_FILE=str(tmp_path / "bot.log"), MRS_BOT_HEALTH_FILE=str(tmp_path / "health.json"), PYTHONPATH=str(Path(bot.__file__).parent))


def test_live_reload_refused_before_mutating_lifecycle_authority(tmp_path):
    code = '''
import importlib
import mrsMThatcher2 as bot
bot.production_bootstrap(configure_file_logging=False)
bot.acquire_instance_lock()
prior = (bot._LOCK_FH, bot._STATE_DIR_LOCK_FD, bot._LOCK_SOCKET, bot._BOT_HEALTH_REPORTER, bot.AUTH)
try:
    importlib.reload(bot)
except RuntimeError as exc:
    assert "lifecycle authority" in str(exc)
else:
    raise AssertionError("Live reload accepted")
assert prior == (bot._LOCK_FH, bot._STATE_DIR_LOCK_FD, bot._LOCK_SOCKET, bot._BOT_HEALTH_REPORTER, bot.AUTH)
assert bot._PRODUCTION_BOOTSTRAPPED
'''
    result = subprocess.run([sys.executable, "-c", code], env=_child_environment(tmp_path), capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_rejected_contender_cannot_publish_owner_health_or_restart_counts(tmp_path):
    env = _child_environment(tmp_path)
    code = '''
import sys
import mrsMThatcher2 as bot
bot.production_bootstrap(configure_file_logging=False)
def hold_after_lock():
    print("LOCKED", flush=True)
    sys.stdin.readline()
    raise SystemExit(0)
bot.require_established_installation_after_ledger_recovery = hold_after_lock
bot.main()
'''
    owner = subprocess.Popen([sys.executable, "-c", code], env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert any(owner.stdout.readline().strip() == "LOCKED" for _ in range(10))
        path = tmp_path / "health.json"
        before = path.read_bytes()
        contender = subprocess.run([sys.executable, "-c", code], env=env, input="", capture_output=True, text=True)
        assert contender.returncode == 2, contender.stderr
        assert path.read_bytes() == before
    finally:
        owner.communicate("exit\n", timeout=10)


def test_fixture_creation_uses_private_permissions_independent_of_host_umask(tmp_path):
    directory = tmp_path / "private-state"
    directory.mkdir()
    path = directory / "state.json"
    path.write_text("{}")
    assert directory.stat().st_mode & 0o777 == 0o700
    assert path.stat().st_mode & 0o777 == 0o600
