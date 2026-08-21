from __future__ import annotations

import logging
import os
import subprocess
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

import pytest

import mrsMThatcher2 as bot
from tests.conftest import PRODUCTION_LOG, appended_bytes, process_has_open_path


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
    monkeypatch.setattr(bot, "reply_evidence_repository", lambda: object())


def test_plain_import_does_not_open_production_log():
    assert not process_has_open_path(PRODUCTION_LOG)
    assert managed_file_handlers() == []


def test_temporary_production_handler_uses_expected_rotation_and_is_isolated(
    tmp_path,
):
    target = tmp_path / "production-shaped.log"

    logger = bot.setup_logging(log_path=target)
    logger.warning("temporary production handler marker")
    handlers = [
        handler
        for handler in logger.handlers
        if getattr(handler, bot._MANAGED_LOG_HANDLER_ATTR, False)
        and isinstance(handler, logging.FileHandler)
    ]

    assert logger is bot.log
    assert logger.propagate is False
    assert len(handlers) == 1
    handler = handlers[0]
    assert isinstance(handler, RotatingFileHandler)
    assert Path(handler.baseFilename) == target
    assert handler.maxBytes == bot.PRODUCTION_LOG_MAX_BYTES == 2_000_000
    assert handler.backupCount == bot.PRODUCTION_LOG_BACKUP_COUNT == 100
    assert "temporary production handler marker" in target.read_text()
    assert not process_has_open_path(PRODUCTION_LOG)


def test_small_rotation_limit_reaches_backup_100_without_retaining_101(
    tmp_path,
    monkeypatch,
):
    target = tmp_path / "rotation-ceiling.log"
    monkeypatch.setattr(bot, "PRODUCTION_LOG_MAX_BYTES", 1)
    assert bot.PRODUCTION_LOG_BACKUP_COUNT == 100

    logger = bot.setup_logging(log_path=target)
    for index in range(102):
        logger.warning("temporary rotation record %03d", index)

    assert target.is_file()
    assert target.with_name(f"{target.name}.100").is_file()
    assert not target.with_name(f"{target.name}.101").exists()

    logger.warning("active file remains writable")
    for handler in managed_file_handlers():
        handler.flush()
    assert "active file remains writable" in target.read_text()
    assert not process_has_open_path(PRODUCTION_LOG)


def test_contamination_guard_follows_original_inode_to_rotation_100(tmp_path):
    active = tmp_path / "mrsMThatcher.log"
    active.write_bytes(b"existing production bytes\n")
    original = active.stat()
    offset = original.st_size
    with active.open("ab") as handle:
        handle.write(b"original inode marker\n")
    active.replace(tmp_path / "mrsMThatcher.log.100")
    active.write_bytes(b"new active marker\n")

    appended = appended_bytes(active, inode=original.st_ino, offset=offset)

    assert b"original inode marker" in appended
    assert b"new active marker" in appended


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


def test_module_reload_preserves_sealed_x_request_provider(tmp_path):
    code = """
import importlib
import mrsMThatcher2 as b
import remote_write_transport_journal as journal

provider = journal._configured_x_request_provider_identity()
assert provider is not None
configured = provider()
importlib.reload(b)
current = journal._configured_x_request_provider_identity()
assert current is not None
current_configuration = current()
print(current is provider)
print(
    current_configuration[0] == configured[0]
    and current_configuration[1] is configured[1]
    and current_configuration[2] is configured[2]
    and current_configuration[1] is not b.AUTH
)
try:
    journal._install_configured_x_request_provider(
        lambda: configured
    )
except journal.TransportJournalError:
    print("replacement-rejected")
else:
    print("replacement-accepted")
"""
    env = dict(os.environ, PYTHONPATH=str(Path(bot.__file__).resolve().parent))
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.strip().splitlines()[-3:] == [
        "True",
        "True",
        "replacement-rejected",
    ]
    assert not process_has_open_path(PRODUCTION_LOG)


@pytest.mark.parametrize("mismatch", ("endpoint", "auth"))
def test_provider_installer_rejects_configuration_mismatch_before_mutation(
    tmp_path,
    mismatch,
):
    code = f"""
import sys
from urllib3.util import Timeout
import remote_write_transport_journal as journal

owner = sys.modules[__name__]
owner.IMPORT_TIME_TEST_MODE = False
expected_auth = object()
owner.AUTH = expected_auth
fingerprint = (
    "sealed-x-request-provider-reload-v1",
    False,
    "https://api.x.com/2/tweets",
    60.0,
    10.0,
    "0" * 64,
)
provider_url = (
    "http://127.0.0.1:9/2/tweets"
    if {mismatch!r} == "endpoint"
    else "https://api.x.com/2/tweets"
)
provider_auth = object() if {mismatch!r} == "auth" else expected_auth
try:
    journal._install_configured_x_request_provider(
        lambda: (provider_url, provider_auth, Timeout(total=60.0, connect=10.0)),
        owner_module=owner,
        reload_fingerprint=fingerprint,
    )
except journal.TransportJournalError as exc:
    print("install-rejected", "sealed app config" in str(exc))
else:
    print("install-accepted", False)
print(journal._configured_x_request_reload_record(owner)[0])
"""
    env = dict(
        os.environ,
        PYTHONPATH=str(Path(bot.__file__).resolve().parent),
        MRS_TEST_MODE="0",
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.strip().splitlines()[-2:] == [
        "install-rejected True",
        "unconfigured",
    ]


def test_preinstalled_provider_from_wrong_module_owner_blocks_bot_import(
    tmp_path,
):
    code = """
import sys
import requests
from urllib3.util import Timeout
import remote_write_transport_journal as journal

requests.request = lambda *_args, **_kwargs: (_ for _ in ()).throw(
    AssertionError("untrusted preinstallation must not perform network I/O")
)
owner = sys.modules[__name__]
owner.IMPORT_TIME_TEST_MODE = False
owner.AUTH = object()
fingerprint = (
    "sealed-x-request-provider-reload-v1",
    False,
    "https://api.x.com/2/tweets",
    60.0,
    10.0,
    "0" * 64,
)
journal._install_configured_x_request_provider(
    lambda: (
        "https://api.x.com/2/tweets",
        owner.AUTH,
        Timeout(total=60.0, connect=10.0),
    ),
    owner_module=owner,
    reload_fingerprint=fingerprint,
)
try:
    import mrsMThatcher2
except RuntimeError as exc:
    print("import-rejected", "changed sealed X request" in str(exc))
else:
    print("import-accepted", False)
"""
    env = dict(
        os.environ,
        PYTHONPATH=str(Path(bot.__file__).resolve().parent),
        MRS_TEST_MODE="0",
        MRS_BASE_DIR=str(tmp_path),
        MRS_LOG_FILE=str(tmp_path / "wrong-owner.log"),
        X_API_BASE_URL="https://api.x.com",
        MRS_REQUEST_TIMEOUT_SECONDS="60",
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.strip().splitlines()[-1] == "import-rejected True"


def test_provider_installer_evaluates_dynamic_callable_only_once(tmp_path):
    code = """
import sys
from urllib3.util import Timeout
import remote_write_transport_journal as journal

owner = sys.modules[__name__]
owner.IMPORT_TIME_TEST_MODE = True
owner.AUTH = object()
timeout = Timeout(total=60.0, connect=10.0)
fingerprint = (
    "sealed-x-request-provider-reload-v1",
    True,
    "http://127.0.0.1:9/2/tweets",
    60.0,
    10.0,
    "0" * 64,
)
calls = []
def switching_provider():
    calls.append(len(calls) + 1)
    if len(calls) == 1:
        return fingerprint[2], owner.AUTH, timeout
    return "http://127.0.0.1:10/2/tweets", object(), object()

journal._install_configured_x_request_provider(
    switching_provider,
    owner_module=owner,
    reload_fingerprint=fingerprint,
)
provider = journal._configured_x_request_provider_identity()
assert provider is not None
first = provider()
second = provider()
print(calls)
print(
    first == second,
    first[0] == fingerprint[2],
    first[1] is owner.AUTH,
    first[2] is timeout,
    journal._configured_x_request_is_sealed_test_loopback(),
)
"""
    env = dict(
        os.environ,
        PYTHONPATH=str(Path(bot.__file__).resolve().parent),
        MRS_TEST_MODE="1",
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.strip().splitlines()[-2:] == [
        "[1]",
        "True True True True True",
    ]


@pytest.mark.parametrize(
    "bot_global_attack",
    ("false-sentinel", "deleted-sentinel", "poisoned-fingerprint"),
)
def test_live_provider_cannot_reload_as_test_loopback(
    tmp_path,
    bot_global_attack,
):
    attack = {
        "false-sentinel": (
            "b._X_REQUEST_PROVIDER_INSTALLED_BY_MODULE = False"
        ),
        "deleted-sentinel": """
for name in (
    "_X_REQUEST_PROVIDER_INSTALLED_BY_MODULE",
    "_SEALED_X_REQUEST_PROVIDER_RELOAD_FINGERPRINT",
):
    if hasattr(b, name):
        delattr(b, name)
""",
        "poisoned-fingerprint": """
b._X_REQUEST_PROVIDER_INSTALLED_BY_MODULE = True
b._SEALED_X_REQUEST_PROVIDER_RELOAD_FINGERPRINT = (
    b._x_request_provider_reload_fingerprint_from_environment()
)
""",
    }[bot_global_attack]
    code = f"""
import importlib
import os
import mrsMThatcher2 as b
import remote_write_transport_journal as journal

provider = journal._configured_x_request_provider_identity()
assert provider is not None
configured = provider()
b.requests.request = lambda *_args, **_kwargs: (_ for _ in ()).throw(
    AssertionError("reload must not perform network I/O")
)
os.environ.update(
    {{
        "MRS_TEST_MODE": "1",
        "X_API_BASE_URL": "http://127.0.0.1:9",
        "X_UPLOAD_BASE_URL": "http://127.0.0.1:9",
        "XAI_API_BASE_URL": "http://127.0.0.1:9/v1",
    }}
)
{attack}
try:
    importlib.reload(b)
except RuntimeError as exc:
    print("reload-rejected", "changed sealed X request" in str(exc))
else:
    print("reload-accepted", False)
print(
    b.TEST_MODE is False,
    b.IMPORT_TIME_TEST_MODE is False,
    b.X_BASE == "https://api.x.com",
    b.endpoint_is_loopback(b.X_BASE) is False,
)
current = journal._configured_x_request_provider_identity()
assert current is not None
current_configuration = current()
print(
    current is provider,
    current_configuration[0] == configured[0],
    current_configuration[1] is configured[1],
    current_configuration[2] is configured[2],
)
"""
    env = dict(
        os.environ,
        PYTHONPATH=str(Path(bot.__file__).resolve().parent),
        MRS_TEST_MODE="0",
        MRS_BASE_DIR=str(tmp_path),
        MRS_LOG_FILE=str(tmp_path / "reload-live.log"),
        X_API_BASE_URL="https://api.x.com",
        X_UPLOAD_BASE_URL="https://upload.twitter.com",
        XAI_API_BASE_URL="https://api.x.ai/v1",
        MRS_REQUEST_TIMEOUT_SECONDS="60",
        X_CONSUMER_KEY="reload-key",
        X_CONSUMER_SECRET="reload-secret",
        X_ACCESS_TOKEN="reload-token",
        X_ACCESS_SECRET="reload-access-secret",
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.strip().splitlines()[-3:] == [
        "reload-rejected True",
        "True True True True",
        "True True True True",
    ]


def test_live_provider_cannot_gain_test_lock_bypass_from_mutable_bot_globals(
    tmp_path,
):
    code = """
import os
import mrsMThatcher2 as b
import remote_write_transport_journal as journal

provider = journal._configured_x_request_provider_identity()
assert provider is not None
configured = provider()
b.requests.request = lambda *_args, **_kwargs: (_ for _ in ()).throw(
    AssertionError("lock preflight must not perform network I/O")
)
b.TEST_MODE = True
b.IMPORT_TIME_TEST_MODE = True
b.X_BASE = "http://127.0.0.1:9"
b.X_UPLOAD_BASE = "http://127.0.0.1:9"
b.XAI_BASE = "http://127.0.0.1:9/v1"
b.OPENAI_BASE = "http://127.0.0.1:9/v1"
os.environ.pop("MRS_ALLOW_LIVE_ENDPOINTS_IN_TEST", None)
print(b.test_mode_excludes_live_remote_writes())
try:
    b.require_instance_lock_for_remote_write("synthetic write")
except RuntimeError as exc:
    print("lock-required", "instance lock" in str(exc))
else:
    print("lock-bypassed", False)
current = journal._configured_x_request_provider_identity()
assert current is not None
current_configuration = current()
print(
    current is provider,
    configured[0] == "https://api.x.com/2/tweets",
    current_configuration[0] == configured[0],
    current_configuration[1] is configured[1],
    current_configuration[2] is configured[2],
)
"""
    env = dict(
        os.environ,
        PYTHONPATH=str(Path(bot.__file__).resolve().parent),
        MRS_TEST_MODE="0",
        MRS_BASE_DIR=str(tmp_path),
        MRS_LOG_FILE=str(tmp_path / "mutable-test-globals.log"),
        X_API_BASE_URL="https://api.x.com",
        X_UPLOAD_BASE_URL="https://upload.twitter.com",
        XAI_API_BASE_URL="https://api.x.ai/v1",
        MRS_REQUEST_TIMEOUT_SECONDS="60",
        X_CONSUMER_KEY="reload-key",
        X_CONSUMER_SECRET="reload-secret",
        X_ACCESS_TOKEN="reload-token",
        X_ACCESS_SECRET="reload-access-secret",
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.strip().splitlines()[-3:] == [
        "False",
        "lock-required True",
        "True True True True True",
    ]


@pytest.mark.parametrize(
    "environment_update",
    (
        {"X_API_BASE_URL": "http://127.0.0.1:10"},
        {"MRS_REQUEST_TIMEOUT_SECONDS": "30"},
        {"X_ACCESS_TOKEN": "different-reload-token"},
    ),
    ids=("endpoint", "timeout", "credential"),
)
def test_reload_rejects_changed_sealed_provider_configuration(
    tmp_path,
    environment_update,
):
    code = f"""
import importlib
import os
import mrsMThatcher2 as b
import remote_write_transport_journal as journal

provider = journal._configured_x_request_provider_identity()
assert provider is not None
configured = provider()
old_globals = (
    b.TEST_MODE,
    b.X_BASE,
    b.REQUEST_TIMEOUT_SECONDS,
    b.ACCESS_TOKEN,
)
b.requests.request = lambda *_args, **_kwargs: (_ for _ in ()).throw(
    AssertionError("reload must not perform network I/O")
)
os.environ.update({environment_update!r})
try:
    importlib.reload(b)
except RuntimeError as exc:
    print("reload-rejected", "changed sealed X request" in str(exc))
else:
    print("reload-accepted", False)
current = journal._configured_x_request_provider_identity()
assert current is not None
current_configuration = current()
print(old_globals == (b.TEST_MODE, b.X_BASE, b.REQUEST_TIMEOUT_SECONDS, b.ACCESS_TOKEN))
print(
    current is provider,
    current_configuration[0] == configured[0],
    current_configuration[1] is configured[1],
    current_configuration[2] is configured[2],
)
"""
    fake = "http://127.0.0.1:9"
    env = dict(
        os.environ,
        PYTHONPATH=str(Path(bot.__file__).resolve().parent),
        MRS_TEST_MODE="1",
        MRS_BASE_DIR=str(tmp_path),
        MRS_LOG_FILE=str(tmp_path / "reload-mismatch.log"),
        X_API_BASE_URL=fake,
        X_UPLOAD_BASE_URL=fake,
        XAI_API_BASE_URL=f"{fake}/v1",
        MRS_REQUEST_TIMEOUT_SECONDS="60",
        X_CONSUMER_KEY="reload-key",
        X_CONSUMER_SECRET="reload-secret",
        X_ACCESS_TOKEN="reload-token",
        X_ACCESS_SECRET="reload-access-secret",
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.strip().splitlines()[-3:] == [
        "reload-rejected True",
        "True",
        "True True True True",
    ]


def test_initial_test_import_rejects_percent_suffixed_loopback_host(tmp_path):
    code = """
import requests
requests.request = lambda *_args, **_kwargs: (_ for _ in ()).throw(
    AssertionError("import must not perform network I/O")
)
import mrsMThatcher2
"""
    fake = "http://127.0.0.1:9"
    env = dict(
        os.environ,
        PYTHONPATH=str(Path(bot.__file__).resolve().parent),
        MRS_TEST_MODE="1",
        MRS_BASE_DIR=str(tmp_path),
        MRS_LOG_FILE=str(tmp_path / "percent-host.log"),
        X_API_BASE_URL="http://127.0.0.1%40.attacker.invalid",
        X_UPLOAD_BASE_URL=fake,
        XAI_API_BASE_URL=f"{fake}/v1",
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 2
    assert "Refusing to run in MRS_TEST_MODE with live endpoint" in (
        result.stdout + result.stderr
    )


def test_subprocess_bootstrap_uses_temporary_log(tmp_path):
    script = tmp_path / "mrsMThatcher2.py"
    script.write_bytes(Path(bot.__file__).read_bytes())
    (tmp_path / "mrsMThatcher.local.json").write_text("{")
    target = tmp_path / "subprocess.log"
    env = dict(
        os.environ,
        PYTHONPATH=str(Path(bot.__file__).resolve().parent),
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
