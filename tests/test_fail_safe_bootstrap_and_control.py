from __future__ import annotations

import builtins
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import mrsMThatcher2 as bot
from remote_write_safety_protocol import ACTIVATION_BASENAME
from tests.helpers.protocol_activation import create_test_protocol_activation


OPERATIONAL_ENTRY_POINTS = (
    "main",
    "run_self_test",
    "run_test_cycle",
    "run_test_main_tick",
    "run_test_post_quote",
    "run_test_post_meme",
)

CLI_MODE_TO_ENTRY_POINT = {
    None: "main",
    "--initialise": "initialise_installation",
    "--self-test": "run_self_test",
    "--test-cycle": "run_test_cycle",
    "--test-main-tick": "run_test_main_tick",
    "--test-post-quote": "run_test_post_quote",
    "--test-post-meme": "run_test_post_meme",
}

CLI_SUBPROCESS_DRIVER = r"""
import json
import sys

import mrsMThatcher2 as bot

events = []
bot.production_bootstrap = lambda: events.append("production_bootstrap")
for name in (
    "main",
    "initialise_installation",
    "run_self_test",
    "run_test_cycle",
    "run_test_main_tick",
    "run_test_post_quote",
    "run_test_post_meme",
):
    setattr(
        bot,
        name,
        (lambda selected: lambda: (events.append(selected), 0)[1])(name),
    )
status = bot.run_cli(sys.argv[1:])
print(json.dumps({"events": events, "status": status}, sort_keys=True))
raise SystemExit(0 if status is None else status)
"""

CLI_ARGV_MUTATION_DRIVER = r"""
import json
import sys

initial_arguments = json.loads(sys.argv[1])
replacement_arguments = json.loads(sys.argv[2])
explicit_arguments = None if len(sys.argv) < 4 else json.loads(sys.argv[3])
sys.argv = ["mrsMThatcher2.py", *initial_arguments]

import mrsMThatcher2 as bot

events = []
bot.production_bootstrap = lambda: events.append("production_bootstrap")
for name in (
    "main",
    "initialise_installation",
    "run_self_test",
    "run_test_cycle",
    "run_test_main_tick",
    "run_test_post_quote",
    "run_test_post_meme",
):
    setattr(
        bot,
        name,
        (lambda selected: lambda: (events.append(selected), 0)[1])(name),
    )

sys.argv = ["mrsMThatcher2.py", *replacement_arguments]
status = (
    bot.run_cli()
    if explicit_arguments is None
    else bot.run_cli(explicit_arguments)
)
print(
    json.dumps(
        {
            "events": events,
            "import_time_arguments": bot.IMPORT_TIME_CLI_ARGUMENTS,
            "self_test_requested": bot.SELF_TEST_REQUESTED,
            "status": status,
        },
        sort_keys=True,
    )
)
"""

CLI_ARGV_ZERO_IMPERSONATION_DRIVER = r"""
import json
import sys

sys.argv = ["--self-test"]
import mrsMThatcher2 as bot

events = []
bot.production_bootstrap = lambda: events.append("production_bootstrap")
bot.main = lambda: events.append("main")
status = bot.run_cli()
print(
    json.dumps(
        {
            "events": events,
            "import_time_arguments": bot.IMPORT_TIME_CLI_ARGUMENTS,
            "self_test_requested": bot.SELF_TEST_REQUESTED,
            "status": status,
        },
        sort_keys=True,
    )
)
"""

TEST_MODE_DIRECT_MUTATION_DRIVER = r"""
import json
import os
import sys

entry_point = sys.argv[1]
transition = sys.argv[2]
sys.argv = ["driver"]
if transition == "late_enable":
    os.environ.pop("MRS_TEST_MODE", None)
else:
    os.environ["MRS_TEST_MODE"] = "1"

import mrsMThatcher2 as bot

if transition == "late_enable":
    os.environ["MRS_TEST_MODE"] = "1"
else:
    os.environ.pop("MRS_TEST_MODE", None)

events = []

class BoundaryReached(Exception):
    pass

bot.require_production_bootstrap = lambda: events.append(
    "require_production_bootstrap"
)
bot.acquire_instance_lock = lambda: (_ for _ in ()).throw(BoundaryReached())
try:
    status = getattr(bot, entry_point)()
except BoundaryReached:
    status = "authorised"
print(
    json.dumps(
        {
            "events": events,
            "import_time_test_mode": bot.IMPORT_TIME_TEST_MODE,
            "status": status,
        },
        sort_keys=True,
    )
)
"""

TEST_MODE_IMPORT_HOOK_DRIVER = r"""
import builtins
import json
import os
import sys

mode = sys.argv[1]
transition = sys.argv[2]
sys.argv = ["mrsMThatcher2.py", mode]
if transition == "late_enable":
    os.environ.pop("MRS_TEST_MODE", None)
else:
    os.environ["MRS_TEST_MODE"] = "1"

original_import = builtins.__import__
hook_fired = False

def mutate_at_first_application_import(name, *args, **kwargs):
    global hook_fired
    if name == "remote_write_safety_protocol" and not hook_fired:
        hook_fired = True
        if transition == "late_enable":
            os.environ["MRS_TEST_MODE"] = "1"
        else:
            os.environ.pop("MRS_TEST_MODE", None)
    return original_import(name, *args, **kwargs)

builtins.__import__ = mutate_at_first_application_import
try:
    import mrsMThatcher2 as bot
finally:
    builtins.__import__ = original_import

events = []
bot.production_bootstrap = lambda: events.append("production_bootstrap")
entry_point = {
    "--test-cycle": "run_test_cycle",
    "--test-main-tick": "run_test_main_tick",
    "--test-post-quote": "run_test_post_quote",
    "--test-post-meme": "run_test_post_meme",
}[mode]
setattr(bot, entry_point, lambda: (events.append(entry_point), 0)[1])
status = bot.run_cli()
print(
    json.dumps(
        {
            "events": events,
            "hook_fired": hook_fired,
            "import_time_test_mode": bot.IMPORT_TIME_TEST_MODE,
            "live_test_mode": os.environ.get("MRS_TEST_MODE") == "1",
            "status": status,
            "test_mode_alias": bot.TEST_MODE,
        },
        sort_keys=True,
    )
)
"""


def reset_control_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "_CONTROL_CACHE", {"signature": None, "data": {}, "has_valid": False, "failure_signature": None})


def prepare_self_test_control_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    control_path: Path,
) -> list[dict]:
    """Make every self-test check except runtime control deterministically pass."""

    lines_path = tmp_path / "mrsMThatcher.txt"
    lines_path.write_text("A test quotation.\n", encoding="utf-8")
    image_path = tmp_path / "quote-image.png"
    image_path.write_bytes(b"not-decoded-by-self-test")

    monkeypatch.setattr(bot, "require_production_bootstrap", lambda: None)
    monkeypatch.setattr(bot, "BASE_DIR", tmp_path)
    monkeypatch.setattr(bot, "LINES_FILE", lines_path)
    monkeypatch.setattr(bot, "IMAGE_GLOB", os.fspath(tmp_path / "*.png"))
    monkeypatch.setattr(bot, "LOCAL_CONFIG_FILE", tmp_path / "missing-local.json")
    monkeypatch.setattr(bot, "CONTROL_FILE", control_path)
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "missing-state.json")
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(bot, "EXTRA_QUOTE_WATCH_FILE", tmp_path / "missing-watch.json")
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", False)
    monkeypatch.setattr(bot, "CONSUMER_KEY", "configured")
    monkeypatch.setattr(bot, "CONSUMER_SECRET", "configured")
    monkeypatch.setattr(bot, "ACCESS_TOKEN", "configured")
    monkeypatch.setattr(bot, "ACCESS_SECRET", "configured")
    monkeypatch.setattr(bot, "MY_USER_ID", "configured")
    monkeypatch.setattr(bot, "MAX_AUTO_REPLIES_PER_DAY", 1)
    monkeypatch.setattr(bot, "MAX_QUOTE_REPLIES_PER_DAY", 1)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 1)
    monkeypatch.setattr(bot, "REPLY_CHECK_EVERY_SECONDS", 1)
    monkeypatch.setattr(bot, "QUOTE_CHECK_EVERY_SECONDS", 1)
    monkeypatch.setattr(bot, "validate_runtime_config_values", lambda _values: [])
    monkeypatch.setattr(bot, "_self_test_warn", lambda *_args, **_kwargs: None)
    reset_control_cache(monkeypatch)

    calls: list[dict] = []
    production_load_control = bot.load_control

    def tracked_load_control() -> dict:
        loaded = production_load_control()
        calls.append(loaded)
        return loaded

    monkeypatch.setattr(bot, "load_control", tracked_load_control)
    return calls


@pytest.mark.parametrize("mode", tuple(CLI_MODE_TO_ENTRY_POINT))
def test_cli_parser_accepts_only_one_documented_mode(mode: str | None) -> None:
    arguments = [] if mode is None else [mode]
    assert bot.parse_cli_mode(arguments) == mode


@pytest.mark.parametrize(
    "arguments",
    [
        ["--unknown"],
        ["positional"],
        ["--self-test=1"],
        ["--self-test", "--self-test"],
        ["--initialise", "--self-test"],
        ["--test-cycle", "--unknown"],
    ],
)
def test_cli_parser_rejects_unknown_duplicate_and_multiple_modes(
    arguments: list[str],
) -> None:
    with pytest.raises(bot.CliUsageError):
        bot.parse_cli_mode(arguments)


@pytest.mark.parametrize("mode,entry_point", CLI_MODE_TO_ENTRY_POINT.items())
def test_literal_subprocess_dispatches_each_documented_cli_mode(
    mode: str | None,
    entry_point: str,
) -> None:
    command = [sys.executable, "-c", CLI_SUBPROCESS_DRIVER]
    if mode is not None:
        command.append(mode)
    result = subprocess.run(
        command,
        cwd=Path(bot.__file__).resolve().parent,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(bot.__file__).resolve().parent),
        },
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "events": ["production_bootstrap", entry_point],
        "status": 0 if mode is not None else None,
    }


@pytest.mark.parametrize(
    "arguments",
    [
        ["--unknown"],
        ["positional"],
        ["--self-test=1"],
        ["--self-test", "--self-test"],
        ["--initialise", "--self-test"],
        ["--test-post-quote", "--unknown"],
    ],
)
def test_literal_subprocess_rejects_complete_invalid_argv_before_bootstrap(
    arguments: list[str],
) -> None:
    result = subprocess.run(
        [sys.executable, "-c", CLI_SUBPROCESS_DRIVER, *arguments],
        cwd=Path(bot.__file__).resolve().parent,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(bot.__file__).resolve().parent),
        },
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 2
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {"events": [], "status": 2}
    assert bot.CLI_USAGE in result.stderr


def test_invalid_cli_argv_reaches_no_bootstrap_or_operational_boundary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    for name in ("production_bootstrap", *CLI_MODE_TO_ENTRY_POINT.values()):
        monkeypatch.setattr(
            bot,
            name,
            lambda *args, _name=name, **kwargs: calls.append(_name),
        )

    assert bot.run_cli(["--self-test", "--test-cycle"]) == 2
    assert calls == []


@pytest.mark.parametrize(
    "arguments",
    [
        ["--unknown"],
        ["positional"],
        ["--self-test=1"],
        ["--self-test", "--self-test"],
        ["--initialise", "--self-test"],
        ["--test-post-quote", "--unknown"],
    ],
)
def test_real_script_rejects_invalid_argv_before_import_side_effects(
    tmp_path: Path,
    arguments: list[str],
) -> None:
    base_directory = tmp_path / "must-not-be-created"
    log_path = tmp_path / "must-not-be-created.log"
    result = subprocess.run(
        [sys.executable, str(Path(bot.__file__).resolve()), *arguments],
        cwd=tmp_path,
        env={
            **os.environ,
            "MRS_BASE_DIR": str(base_directory),
            "MRS_LOG_FILE": str(log_path),
        },
        text=True,
        capture_output=True,
        timeout=20,
    )

    assert result.returncode == 2
    assert result.stdout == ""
    assert result.stderr.startswith(f"{bot.CLI_USAGE}\n")
    assert not base_directory.exists()
    assert not log_path.exists()


def test_run_cli_refuses_mode_different_from_process_argv_before_bootstrap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(sys, "argv", ["mrsMThatcher2.py"])
    for name in ("production_bootstrap", *CLI_MODE_TO_ENTRY_POINT.values()):
        monkeypatch.setattr(
            bot,
            name,
            lambda *args, _name=name, **kwargs: calls.append(_name),
        )

    assert bot.run_cli(["--self-test"]) == 2
    assert calls == []


@pytest.mark.parametrize(
    "initial_arguments,replacement_arguments,self_test_requested",
    [
        ([], ["--self-test"], False),
        (["--self-test"], [], True),
    ],
)
def test_run_cli_refuses_both_directions_of_post_import_argv_mutation(
    initial_arguments: list[str],
    replacement_arguments: list[str],
    self_test_requested: bool,
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            CLI_ARGV_MUTATION_DRIVER,
            json.dumps(initial_arguments),
            json.dumps(replacement_arguments),
        ],
        cwd=Path(bot.__file__).resolve().parent,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(bot.__file__).resolve().parent),
        },
        text=True,
        capture_output=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "events": [],
        "import_time_arguments": initial_arguments,
        "self_test_requested": self_test_requested,
        "status": 2,
    }
    assert "process argv changed after module import" in result.stderr


def test_argv_zero_cannot_impersonate_a_documented_command_mode() -> None:
    result = subprocess.run(
        [sys.executable, "-c", CLI_ARGV_ZERO_IMPERSONATION_DRIVER],
        cwd=Path(bot.__file__).resolve().parent,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(bot.__file__).resolve().parent),
        },
        text=True,
        capture_output=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "events": ["production_bootstrap", "main"],
        "import_time_arguments": [],
        "self_test_requested": False,
        "status": None,
    }


def test_run_cli_refuses_explicit_argv_different_from_import_time_argv() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            CLI_ARGV_MUTATION_DRIVER,
            "[]",
            "[]",
            '["--self-test"]',
        ],
        cwd=Path(bot.__file__).resolve().parent,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(bot.__file__).resolve().parent),
        },
        text=True,
        capture_output=True,
        timeout=20,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload == {
        "events": [],
        "import_time_arguments": [],
        "self_test_requested": False,
        "status": 2,
    }
    assert "explicit argv must exactly match" in result.stderr


def test_absent_local_config_keeps_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "LOCAL_CONFIG_FILE", tmp_path / "missing.json")
    before = bot.POST_SLEEP_MIN
    bot.apply_local_config()
    assert bot.POST_SLEEP_MIN == before


@pytest.mark.parametrize("content", ["{", "[]", json.dumps({"ENABLE_AUTO_REPLIES": "maybe"}), json.dumps({"POST_SLEEP_MIN": 10_000, "POST_SLEEP_MAX": 5_000})])
def test_existing_invalid_local_config_fails_closed(tmp_path, monkeypatch, content):
    path = tmp_path / "local.json"
    path.write_text(content)
    monkeypatch.setattr(bot, "LOCAL_CONFIG_FILE", path)
    with pytest.raises(bot.LocalConfigError, match=str(path)):
        bot.apply_local_config()


@pytest.mark.parametrize(
    "content",
    [
        '{"POST_SLEEP_MIN":8000,"POST_SLEEP_MIN":9000}',
        '{"POST_SLEEP_MIN":NaN}',
        '{"POST_SLEEP_MIN":Infinity}',
        '{"POST_SLEEP_MIN":-Infinity}',
        '{"POST_SLEEP_MIN":1e999}',
        '{"POST_SLEEP_MIN":-1e999}',
    ],
)
def test_local_config_rejects_duplicate_names_and_nonfinite_constants(
    tmp_path,
    monkeypatch,
    content,
):
    path = tmp_path / "local.json"
    path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(bot, "LOCAL_CONFIG_FILE", path)
    before = bot.POST_SLEEP_MIN

    with pytest.raises(bot.LocalConfigError, match=str(path)):
        bot.apply_local_config()

    assert bot.POST_SLEEP_MIN == before


def test_local_config_strict_loader_rejects_nested_duplicate_before_schema_validation(
    tmp_path,
    monkeypatch,
):
    content = (
        '{"historical_context_reply":{'
        '"enabled":true,'
        '"maximum_length":4000,'
        '"include_meaning":true,'
        '"enabled":false,'
        '"include_source":true,'
        '"include_verification":true'
        '}}'
    )
    path = tmp_path / "local.json"
    path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(bot, "LOCAL_CONFIG_FILE", path)
    before_identity = bot.historical_context_reply
    before_value = dict(before_identity)

    with pytest.raises(ValueError, match="contains a duplicate object name"):
        bot.load_strict_runtime_json(content, label="local config")

    with pytest.raises(
        bot.LocalConfigError,
        match="contains a duplicate object name",
    ):
        bot.apply_local_config()

    assert bot.historical_context_reply is before_identity
    assert bot.historical_context_reply == before_value


def test_unreadable_local_config_fails_closed(tmp_path, monkeypatch):
    path = tmp_path / "local.json"
    path.write_text("{}")
    monkeypatch.setattr(bot, "LOCAL_CONFIG_FILE", path)
    original_open = builtins.open

    def denied(value, *args, **kwargs):
        if Path(value) == path:
            raise PermissionError("denied")
        return original_open(value, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", denied)
    with pytest.raises(bot.LocalConfigError, match="denied"):
        bot.apply_local_config()


def test_bootstrap_is_explicit_valid_and_idempotent(tmp_path, monkeypatch):
    path = tmp_path / "local.json"
    path.write_text(json.dumps({"POST_SLEEP_MIN": 8000, "POST_SLEEP_MAX": 8200}))
    monkeypatch.setattr(bot, "LOCAL_CONFIG_FILE", path)
    monkeypatch.setattr(bot, "POST_SLEEP_MIN", 7200)
    monkeypatch.setattr(bot, "POST_SLEEP_MAX", 9000)
    monkeypatch.setattr(bot, "_PRODUCTION_BOOTSTRAPPED", False)
    evidence_loads: list[bool] = []
    monkeypatch.setattr(
        bot,
        "reply_evidence_repository",
        lambda: evidence_loads.append(True),
    )
    for key in ("CONSUMER_KEY", "CONSUMER_SECRET", "ACCESS_TOKEN", "ACCESS_SECRET", "MY_USER_ID", "XAI_API_KEY"):
        monkeypatch.setattr(bot, key, "test-value")
    bot.production_bootstrap(configure_file_logging=False)
    path.write_text("{")
    bot.production_bootstrap(configure_file_logging=False)
    assert (bot.POST_SLEEP_MIN, bot.POST_SLEEP_MAX) == (8000, 8200)
    assert evidence_loads == []


@pytest.mark.parametrize("entry_point_name", OPERATIONAL_ENTRY_POINTS)
def test_operational_entry_points_require_bootstrap_before_side_effects(monkeypatch, entry_point_name):
    calls: list[str] = []
    monkeypatch.setattr(bot, "_PRODUCTION_BOOTSTRAPPED", False)
    monkeypatch.setenv("MRS_TEST_MODE", "1")

    for helper_name in (
        "acquire_instance_lock",
        "load_runtime_state",
        "reconcile_main_post_receipts",
        "reconcile_confirmed_reply_receipt",
        "create_post",
        "upload_media",
        "post_random_quote",
        "post_next_meme",
    ):
        monkeypatch.setattr(
            bot,
            helper_name,
            lambda *args, _name=helper_name, **kwargs: calls.append(_name),
        )

    with pytest.raises(RuntimeError, match="Production bootstrap has not completed"):
        getattr(bot, entry_point_name)()

    assert calls == []


@pytest.mark.parametrize(
    "mode,entry_point_name",
    (
        ("--test-cycle", "run_test_cycle"),
        ("--test-main-tick", "run_test_main_tick"),
        ("--test-post-quote", "run_test_post_quote"),
        ("--test-post-meme", "run_test_post_meme"),
    ),
)
@pytest.mark.parametrize("transition", ("late_enable", "late_disable"))
def test_test_mode_authority_is_captured_before_application_imports(
    mode,
    entry_point_name,
    transition,
):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            TEST_MODE_IMPORT_HOOK_DRIVER,
            mode,
            transition,
        ],
        cwd=Path(bot.__file__).resolve().parent,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(bot.__file__).resolve().parent),
        },
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    assert payload["hook_fired"] is True
    if transition == "late_enable":
        assert payload == {
            "events": [],
            "hook_fired": True,
            "import_time_test_mode": False,
            "live_test_mode": True,
            "status": 2,
            "test_mode_alias": False,
        }
    else:
        assert payload == {
            "events": ["production_bootstrap", entry_point_name],
            "hook_fired": True,
            "import_time_test_mode": True,
            "live_test_mode": False,
            "status": 0,
            "test_mode_alias": True,
        }


@pytest.mark.parametrize(
    "entry_point_name",
    (
        "run_test_cycle",
        "run_test_main_tick",
        "run_test_post_quote",
        "run_test_post_meme",
    ),
)
@pytest.mark.parametrize("transition", ("late_enable", "late_disable"))
def test_direct_test_entry_points_gate_before_bootstrap_using_import_authority(
    entry_point_name,
    transition,
):
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            TEST_MODE_DIRECT_MUTATION_DRIVER,
            entry_point_name,
            transition,
        ],
        cwd=Path(bot.__file__).resolve().parent,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(bot.__file__).resolve().parent),
        },
        text=True,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    if transition == "late_enable":
        assert payload == {
            "events": [],
            "import_time_test_mode": False,
            "status": 2,
        }
    else:
        assert payload == {
            "events": ["require_production_bootstrap"],
            "import_time_test_mode": True,
            "status": "authorised",
        }


@pytest.mark.parametrize(
    "mode",
    tuple(sorted(bot.TEST_MODE_REQUIRED_CLI_FLAGS)),
)
def test_real_script_rejects_test_mode_without_import_authority_or_runtime_writes(
    tmp_path,
    mode,
):
    base_directory = tmp_path / "must-not-be-created"
    log_path = tmp_path / "must-not-be-created.log"
    environment = {
        **os.environ,
        "MRS_BASE_DIR": str(base_directory),
        "MRS_LOG_FILE": str(log_path),
    }
    environment.pop("MRS_TEST_MODE", None)
    result = subprocess.run(
        [sys.executable, str(Path(bot.__file__).resolve()), mode],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        timeout=20,
    )

    assert result.returncode == 2
    assert "requires MRS_TEST_MODE=1 before bot import" in result.stderr
    assert not base_directory.exists()
    assert not log_path.exists()


def test_successful_bootstrap_opens_guard_and_operational_dispatch(tmp_path, monkeypatch):
    monkeypatch.setattr(bot, "LOCAL_CONFIG_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "_PRODUCTION_BOOTSTRAPPED", False)
    monkeypatch.setattr(bot, "setup_logging", lambda **_kwargs: bot.log)
    monkeypatch.setattr(bot, "validate_production_credentials", lambda: None)
    monkeypatch.setattr(bot, "reply_evidence_repository", lambda: object())
    monkeypatch.setattr(bot, "SELF_TEST_REQUESTED", False)
    reached_lock: list[bool] = []

    class DispatchReached(Exception):
        pass

    def stop_at_lock() -> None:
        reached_lock.append(True)
        raise DispatchReached

    monkeypatch.setattr(bot, "acquire_instance_lock", stop_at_lock)
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    bot.production_bootstrap()
    bot.require_production_bootstrap()

    with pytest.raises(DispatchReached):
        bot.main()

    assert reached_lock == [True]


def test_failed_bootstrap_leaves_operational_guard_closed(tmp_path, monkeypatch):
    path = tmp_path / "local.json"
    path.write_text("{")
    monkeypatch.setattr(bot, "LOCAL_CONFIG_FILE", path)
    monkeypatch.setattr(bot, "_PRODUCTION_BOOTSTRAPPED", False)
    monkeypatch.setattr(bot, "setup_logging", lambda **_kwargs: bot.log)

    with pytest.raises(bot.LocalConfigError):
        bot.production_bootstrap()

    assert bot._PRODUCTION_BOOTSTRAPPED is False
    with pytest.raises(RuntimeError, match="Production bootstrap has not completed"):
        bot.require_production_bootstrap()


def test_script_invalid_config_exits_before_main(tmp_path):
    script = tmp_path / "mrsMThatcher2.py"
    script.write_bytes(Path(bot.__file__).read_bytes())
    (tmp_path / "mrsMThatcher.local.json").write_text("{")
    env = dict(
        os.environ,
        MRS_BASE_DIR=str(tmp_path),
        PYTHONPATH=str(Path(bot.__file__).resolve().parent),
        X_CONSUMER_KEY="x",
        X_CONSUMER_SECRET="x",
        X_ACCESS_TOKEN="x",
        X_ACCESS_SECRET="x",
        X_MY_USER_ID="1",
        XAI_API_KEY="x",
    )
    result = subprocess.run([sys.executable, str(script)], cwd=tmp_path, env=env, text=True, capture_output=True, timeout=20)
    assert result.returncode != 0
    assert "LocalConfigError" in result.stderr
    assert "Bot starting" not in result.stdout + result.stderr


@pytest.mark.parametrize("value", [True, False, 2.0, 1.9, "2", "", None])
def test_integer_config_rejects_non_json_integers(value):
    with pytest.raises(ValueError, match="JSON integer"):
        bot._coerce_local_config_value("POST_SLEEP_MIN", value, 7200)


def test_integer_config_accepts_valid_ranges():
    assert bot._coerce_local_config_value("POST_SLEEP_MIN", 0, 7200) == 0
    assert bot._coerce_local_config_value("MAX_AUTO_REPLIES_PER_DAY", 2, 24) == 2
    with pytest.raises(ValueError):
        bot._coerce_local_config_value("MAX_AUTO_REPLIES_PER_DAY", 0, 24)


def test_control_malformed_preserves_prior_pause_and_repair_recovers(tmp_path, monkeypatch, caplog):
    path = tmp_path / "control.json"
    path.write_text(json.dumps({"disable_all": True}))
    monkeypatch.setattr(bot, "CONTROL_FILE", path)
    reset_control_cache(monkeypatch)
    assert bot.load_control()["disable_all"] is True
    path.write_text("{")
    first = bot.load_control()
    second = bot.load_control()
    assert first["disable_all"] is True and second["disable_all"] is True
    assert sum("failing safe" in record.message for record in caplog.records) == 1
    path.write_text(json.dumps({"disable_all": False}))
    assert bot.load_control()["disable_all"] is False


def test_same_inode_control_change_cannot_reuse_cached_unpaused_value(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "control.json"
    initial = '{"pause_all":"off"}'
    replacement = '{"pause_all":"yes"}'
    assert len(initial) == len(replacement)
    path.write_text(initial, encoding="utf-8")
    original = path.stat()
    monkeypatch.setattr(bot, "CONTROL_FILE", path)
    reset_control_cache(monkeypatch)
    assert bot.global_remote_writes_paused() is False

    with path.open("r+", encoding="utf-8") as handle:
        handle.seek(0)
        handle.write(replacement)
        handle.truncate()
        handle.flush()
        os.fsync(handle.fileno())
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
    changed = path.stat()
    assert changed.st_ino == original.st_ino
    assert changed.st_size == original.st_size
    assert changed.st_mtime_ns == original.st_mtime_ns

    assert bot.global_remote_writes_paused() is True
    assert bot.load_control()["pause_all"] == "yes"


@pytest.mark.parametrize(
    "content",
    [
        '{"disable_all":true,"disable_all":false}',
        '{"generation":1,"generation":2}',
        '{"disable_all_until":NaN}',
        '{"disable_all_until":Infinity}',
        '{"disable_all_until":-Infinity}',
        '{"disable_all_until":1e999}',
        '{"disable_all_until":-1e999}',
    ],
)
def test_runtime_control_rejects_duplicate_names_and_nonfinite_constants(
    tmp_path,
    monkeypatch,
    content,
):
    path = tmp_path / "control.json"
    path.write_text(content, encoding="utf-8")
    monkeypatch.setattr(bot, "CONTROL_FILE", path)
    reset_control_cache(monkeypatch)

    loaded = bot.load_control()

    assert loaded["disable_all"] is True
    assert loaded["_control_fail_closed"] is True
    assert bot.global_remote_writes_paused() is True


@pytest.mark.parametrize("payload", [[], {"disable_all": "perhaps"}, {"disable_all_until": "not-a-time"}])
def test_control_invalid_without_prior_fails_closed(tmp_path, monkeypatch, payload):
    path = tmp_path / "control.json"
    path.write_text(json.dumps(payload))
    monkeypatch.setattr(bot, "CONTROL_FILE", path)
    reset_control_cache(monkeypatch)
    assert bot.load_control()["disable_all"] is True
    assert bot.lane_paused("disable_quote_posts") is True


@pytest.mark.parametrize(
    "payload",
    [
        {"disable_alll": True},
        {"pause_quote_post": True},
        {"disable_all_until_typo": 2_000_000_000},
        {"unrelated": False},
    ],
)
def test_unknown_runtime_control_keys_fail_closed(
    tmp_path,
    monkeypatch,
    payload,
):
    path = tmp_path / "control.json"
    path.write_text(json.dumps(payload))
    monkeypatch.setattr(bot, "CONTROL_FILE", path)
    reset_control_cache(monkeypatch)

    assert bot.load_control()["disable_all"] is True
    assert bot.global_remote_writes_paused() is True


def test_self_test_rejects_runtime_control_unknown_to_production_schema(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "control.json"
    path.write_text('{"disable_alll":true}', encoding="utf-8")
    control_loads = prepare_self_test_control_case(tmp_path, monkeypatch, path)

    assert bot.run_self_test() == 1
    assert control_loads == [
        {"disable_all": True, "_control_fail_closed": True}
    ]


def test_self_test_rejects_runtime_control_symlink_like_production_loader(
    tmp_path,
    monkeypatch,
):
    target = tmp_path / "control-target.json"
    target.write_text('{"disable_all":false}', encoding="utf-8")
    path = tmp_path / "control.json"
    path.symlink_to(target)
    control_loads = prepare_self_test_control_case(tmp_path, monkeypatch, path)

    assert bot.run_self_test() == 1
    assert control_loads == [
        {"disable_all": True, "_control_fail_closed": True}
    ]


def test_documented_runtime_control_metadata_remains_valid(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "control.json"
    path.write_text(json.dumps({"disable_all": False, "generation": 2}))
    monkeypatch.setattr(bot, "CONTROL_FILE", path)
    reset_control_cache(monkeypatch)

    assert bot.load_control() == {"disable_all": False, "generation": 2}
    assert bot.global_remote_writes_paused() is False


def test_control_stat_and_read_failure_preserve_prior_valid(tmp_path, monkeypatch):
    path = tmp_path / "control.json"
    path.write_text(json.dumps({"disable_all": True}))
    monkeypatch.setattr(bot, "CONTROL_FILE", path)
    reset_control_cache(monkeypatch)
    assert bot.load_control()["disable_all"] is True

    original_lstat = os.lstat
    monkeypatch.setattr(
        os,
        "lstat",
        lambda value, *a, **k: (
            (_ for _ in ()).throw(OSError("stat failed"))
            if os.fspath(value) == os.fspath(path)
            else original_lstat(value, *a, **k)
        ),
    )
    assert bot.load_control()["disable_all"] is True
    monkeypatch.setattr(os, "lstat", original_lstat)

    path.write_text(json.dumps({"disable_all": False}))
    original_open = os.open
    monkeypatch.setattr(
        os,
        "open",
        lambda value, *a, **k: (
            (_ for _ in ()).throw(OSError("read failed"))
            if os.fspath(value) == os.fspath(path)
            else original_open(value, *a, **k)
        ),
    )
    assert bot.load_control()["disable_all"] is True


@pytest.mark.parametrize("kind", ("symlink", "directory", "fifo"))
def test_control_rejects_nonregular_or_symlink_namespace_entries(
    tmp_path,
    monkeypatch,
    kind,
):
    path = tmp_path / "control.json"
    if kind == "symlink":
        target = tmp_path / "target.json"
        target.write_text('{"disable_all":false}', encoding="utf-8")
        path.symlink_to(target)
    elif kind == "directory":
        path.mkdir()
    else:
        os.mkfifo(path)
    monkeypatch.setattr(bot, "CONTROL_FILE", path)
    reset_control_cache(monkeypatch)

    loaded = bot.load_control()

    assert loaded["disable_all"] is True
    assert loaded["_control_fail_closed"] is True


def test_control_cache_is_bound_to_bytes_and_cannot_be_mutated_by_caller(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "control.json"
    document = b'{"disable_all":true}'
    path.write_bytes(document)
    monkeypatch.setattr(bot, "CONTROL_FILE", path)
    reset_control_cache(monkeypatch)

    loaded = bot.load_control()
    assert bot._CONTROL_CACHE["signature"][-1] == hashlib.sha256(document).hexdigest()
    loaded["disable_all"] = False

    assert bot.load_control()["disable_all"] is True


def test_control_path_replacement_during_read_fails_closed(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "control.json"
    path.write_text('{"pause_all":"off"}', encoding="utf-8")
    monkeypatch.setattr(bot, "CONTROL_FILE", path)
    reset_control_cache(monkeypatch)
    assert bot.load_control()["pause_all"] == "off"

    replacement = tmp_path / "replacement.json"
    replacement.write_text('{"pause_all":"yes"}', encoding="utf-8")
    original_fstat = os.fstat
    calls = 0

    def replace_before_second_fstat(descriptor):
        nonlocal calls
        calls += 1
        if calls == 2:
            os.replace(replacement, path)
        return original_fstat(descriptor)

    monkeypatch.setattr(os, "fstat", replace_before_second_fstat)
    loaded = bot.load_control()

    assert loaded["disable_all"] is True
    assert loaded["_control_fail_closed"] is True
    monkeypatch.setattr(os, "fstat", original_fstat)
    assert bot.load_control()["pause_all"] == "yes"


def test_control_disappearance_after_initial_identity_check_does_not_clear_cache(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "control.json"
    path.write_text('{"disable_all":false}', encoding="utf-8")
    monkeypatch.setattr(bot, "CONTROL_FILE", path)
    reset_control_cache(monkeypatch)
    assert bot.load_control()["disable_all"] is False

    original_open = os.open

    def disappear_before_open(value, *args, **kwargs):
        if os.fspath(value) == os.fspath(path):
            path.unlink()
            raise FileNotFoundError(os.fspath(path))
        return original_open(value, *args, **kwargs)

    monkeypatch.setattr(os, "open", disappear_before_open)
    loaded = bot.load_control()

    assert loaded["disable_all"] is True
    assert loaded["_control_fail_closed"] is True
    assert bot._CONTROL_CACHE["has_valid"] is True
    assert bot._CONTROL_CACHE["data"] == {"disable_all": False}


def test_control_disappearance_at_final_path_check_fails_closed_then_absence_clears(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "control.json"
    path.write_text('{"disable_all":false}', encoding="utf-8")
    monkeypatch.setattr(bot, "CONTROL_FILE", path)
    reset_control_cache(monkeypatch)
    assert bot.load_control()["disable_all"] is False

    original_lstat = os.lstat
    calls = 0

    def disappear_at_final_check(value, *args, **kwargs):
        nonlocal calls
        if os.fspath(value) == os.fspath(path):
            calls += 1
            if calls == 2:
                path.unlink()
        return original_lstat(value, *args, **kwargs)

    monkeypatch.setattr(os, "lstat", disappear_at_final_check)
    loaded = bot.load_control()
    assert loaded["disable_all"] is True
    assert loaded["_control_fail_closed"] is True
    assert bot._CONTROL_CACHE["data"] == {"disable_all": False}

    monkeypatch.setattr(os, "lstat", original_lstat)
    assert bot.load_control() == {}
    assert bot._CONTROL_CACHE["has_valid"] is False


def test_control_fifo_swap_before_open_is_nonblocking_and_fails_closed(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "control.json"
    path.write_text('{"disable_all":false}', encoding="utf-8")
    monkeypatch.setattr(bot, "CONTROL_FILE", path)
    reset_control_cache(monkeypatch)
    original_open = os.open

    def swap_to_fifo(value, flags, *args, **kwargs):
        if os.fspath(value) == os.fspath(path):
            path.unlink()
            os.mkfifo(path)
            assert flags & os.O_NONBLOCK
        return original_open(value, flags, *args, **kwargs)

    monkeypatch.setattr(os, "open", swap_to_fifo)
    loaded = bot.load_control()

    assert loaded["disable_all"] is True
    assert loaded["_control_fail_closed"] is True


def test_control_aba_path_swap_cannot_authorise_the_opened_other_inode(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "control.json"
    parked = tmp_path / "parked.json"
    path.write_text('{"disable_all":false}', encoding="utf-8")
    monkeypatch.setattr(bot, "CONTROL_FILE", path)
    reset_control_cache(monkeypatch)
    original_open = os.open

    def open_other_inode_then_restore_path(value, flags, *args, **kwargs):
        if os.fspath(value) != os.fspath(path):
            return original_open(value, flags, *args, **kwargs)
        os.replace(path, parked)
        path.write_text('{"disable_all":false}', encoding="utf-8")
        descriptor = original_open(value, flags, *args, **kwargs)
        path.unlink()
        os.replace(parked, path)
        return descriptor

    monkeypatch.setattr(os, "open", open_other_inode_then_restore_path)
    loaded = bot.load_control()

    assert loaded["disable_all"] is True
    assert loaded["_control_fail_closed"] is True


def test_control_reader_assembles_short_os_reads(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "control.json"
    path.write_text('{"disable_all":true,"generation":7}', encoding="utf-8")
    monkeypatch.setattr(bot, "CONTROL_FILE", path)
    reset_control_cache(monkeypatch)
    original_read = os.read
    monkeypatch.setattr(
        os,
        "read",
        lambda descriptor, count: original_read(descriptor, min(count, 2)),
    )

    assert bot.load_control() == {"disable_all": True, "generation": 7}


def test_control_reader_rejects_premature_eof(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "control.json"
    path.write_text('{"disable_all":false}', encoding="utf-8")
    monkeypatch.setattr(bot, "CONTROL_FILE", path)
    reset_control_cache(monkeypatch)
    original_read = os.read
    reads = 0

    def stop_after_first_chunk(descriptor, count):
        nonlocal reads
        reads += 1
        if reads == 1:
            return original_read(descriptor, min(count, 4))
        return b""

    monkeypatch.setattr(os, "read", stop_after_first_chunk)
    loaded = bot.load_control()

    assert loaded["disable_all"] is True
    assert loaded["_control_fail_closed"] is True


def test_control_rewrite_between_repeat_reads_fails_closed(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "control.json"
    initial = '{"pause_all":"off"}'
    replacement = '{"pause_all":"yes"}'
    assert len(initial) == len(replacement)
    path.write_text(initial, encoding="utf-8")
    monkeypatch.setattr(bot, "CONTROL_FILE", path)
    reset_control_cache(monkeypatch)
    assert bot.load_control()["pause_all"] == "off"
    original = path.stat()
    original_lseek = os.lseek
    mutated = False

    def mutate_before_repeat(descriptor, offset, whence):
        nonlocal mutated
        if not mutated:
            mutated = True
            with path.open("r+", encoding="utf-8") as handle:
                handle.write(replacement)
                handle.truncate()
                handle.flush()
                os.fsync(handle.fileno())
            os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
        return original_lseek(descriptor, offset, whence)

    monkeypatch.setattr(os, "lseek", mutate_before_repeat)
    loaded = bot.load_control()

    assert loaded["disable_all"] is True
    assert loaded["_control_fail_closed"] is True


def test_invalid_control_fallback_uses_private_cached_copy(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "control.json"
    path.write_text(
        '{"disable_all":false,"generation":7}',
        encoding="utf-8",
    )
    monkeypatch.setattr(bot, "CONTROL_FILE", path)
    reset_control_cache(monkeypatch)
    loaded = bot.load_control()
    loaded["generation"] = 999
    path.write_bytes(b"\xff")

    fallback = bot.load_control()

    assert fallback == {
        "disable_all": True,
        "generation": 7,
        "_control_fail_closed": True,
    }


def test_malformed_control_fails_closed_after_cached_unpaused_document(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "control.json"
    path.write_text(json.dumps({"disable_all": False}))
    monkeypatch.setattr(bot, "CONTROL_FILE", path)
    reset_control_cache(monkeypatch)
    assert bot.global_remote_writes_paused() is False

    path.write_text("{")

    failed = bot.load_control()
    assert failed["disable_all"] is True
    assert failed["_control_fail_closed"] is True
    assert bot.global_remote_writes_paused() is True


@pytest.mark.parametrize("boundary", ["x_create", "media", "provider", "post"])
def test_global_pause_is_rechecked_at_remote_boundaries(
    boundary,
    tmp_path,
    monkeypatch,
):
    activation_path = tmp_path / ACTIVATION_BASENAME
    monkeypatch.setattr(
        bot,
        "REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE",
        activation_path,
    )
    monkeypatch.setattr(
        bot,
        "AMBIGUOUS_POST_OUTCOME_FILE",
        tmp_path / "ambiguous_post_outcome.json",
    )
    monkeypatch.setattr(
        bot,
        "AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE",
        tmp_path / "ambiguous_post_outcome.restart_barrier.json",
    )
    monkeypatch.setattr(
        bot,
        "REGULAR_POST_RECEIPT_FILE",
        tmp_path / "regular_post_receipt.json",
    )
    monkeypatch.setattr(
        bot,
        "MEME_POST_RECEIPT_FILE",
        tmp_path / "meme_post_receipt.json",
    )
    monkeypatch.setattr(
        bot,
        "CONFIRMED_REPLY_RECEIPT_FILE",
        tmp_path / "confirmed_reply_receipt.json",
    )
    monkeypatch.setattr(
        bot,
        "HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE",
        tmp_path / "historical_context_reply_receipt.json",
    )
    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", False)
    monkeypatch.setattr(
        bot,
        "_AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN",
        False,
    )
    create_test_protocol_activation(activation_path)

    path = tmp_path / "control.json"
    path.write_text(json.dumps({"disable_all": False}))
    monkeypatch.setattr(bot, "CONTROL_FILE", path)
    reset_control_cache(monkeypatch)
    assert bot.global_remote_writes_paused() is False

    historical_context_receipt = None
    if boundary == "post":
        historical_context_receipt = {
            "schema_version": 1,
            "lifecycle_state": "sending",
            "parent_post_id": "123",
            "quote_id": "a" * 64,
            "reply_text": "blocked",
            "reply_epoch": 1_800_000_000,
            "started_at": "2026-07-31T12:00:00Z",
            "attempt_number": 1,
        }
        bot.atomic_write_json(
            bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE,
            historical_context_receipt,
            durable=True,
        )

    path.write_text(json.dumps({"disable_all": True, "generation": 2}))
    remote_calls: list[str] = []
    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: remote_calls.append("request"),
    )
    monkeypatch.setattr(
        bot.requests,
        "post",
        lambda *_args, **_kwargs: remote_calls.append("post"),
    )

    with pytest.raises(bot.RemoteOperationsPaused, match="Global runtime control"):
        if boundary == "x_create":
            authority = bot.TransportAuthority(
                transaction_id="a" * 64,
                journal_path=str(tmp_path / "remote_write_transport_journal.json"),
                journal_sha256="b" * 64,
                journal_device=1,
                journal_inode=1,
                journal_ctime_ns=1,
                fence_path=str(tmp_path / "remote_write_transport_fence.json"),
                fence_sha256="c" * 64,
                fence_device=1,
                fence_inode=2,
                fence_ctime_ns=1,
                payload_sha256="d" * 64,
                lane="quote_image",
                source_receipt_basename="regular_post_receipt.json",
                source_validator_id="test.pause-boundary",
                lifecycle_state="attempting",
            )
            bot.x_request(
                "POST",
                "/2/tweets",
                ambiguous_write=True,
                _remote_write_authorization=authority,
                json={"text": "blocked"},
            )
        elif boundary == "media":
            image_path = tmp_path / "image.jpg"
            image_path.write_bytes(b"not sent")
            bot.upload_media(str(image_path), lane="quote_image")
        elif boundary == "provider":
            bot.xai_structured_reply_call(
                stage="review",
                model="unit-model",
                system_prompt="system",
                user_prompt="user",
                response_schema={"type": "object", "properties": {}},
                timeout_seconds=1,
                max_output_tokens=10,
                media_context=None,
            )
        else:
            bot.create_post(
                "blocked",
                reply_to_id="123",
                prepared_historical_context_reply_receipt=(
                    historical_context_receipt
                ),
            )

    assert remote_calls == []
    if boundary == "post":
        assert bot.HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE.is_file()


def test_import_from_foreign_cwd_ignores_live_local_config(tmp_path):
    code = "import mrsMThatcher2 as b; print(b._PRODUCTION_BOOTSTRAPPED); print(b.POST_SLEEP_MIN)"
    env = dict(os.environ, PYTHONPATH=str(Path(bot.__file__).resolve().parent))
    result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=env, text=True, capture_output=True, timeout=20, check=True)
    lines = result.stdout.strip().splitlines()
    assert lines[-2:] == ["False", "7200"]
