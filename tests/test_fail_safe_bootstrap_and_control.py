from __future__ import annotations

import builtins
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


def reset_control_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "_CONTROL_CACHE", {"signature": None, "data": {}, "has_valid": False, "failure_signature": None})


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

    original_stat = Path.stat
    monkeypatch.setattr(Path, "stat", lambda self, *a, **k: (_ for _ in ()).throw(OSError("stat failed")) if self == path else original_stat(self, *a, **k))
    assert bot.load_control()["disable_all"] is True
    monkeypatch.setattr(Path, "stat", original_stat)

    original_open = builtins.open
    monkeypatch.setattr(builtins, "open", lambda value, *a, **k: (_ for _ in ()).throw(OSError("read failed")) if Path(value) == path else original_open(value, *a, **k))
    path.write_text(json.dumps({"disable_all": False, "padding": "changed"}))
    assert bot.load_control()["disable_all"] is True


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


@pytest.mark.parametrize("boundary", ["x_write", "media", "provider", "post"])
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
            "reply_epoch": 1,
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
        if boundary == "x_write":
            bot.x_request(
                "POST",
                "/2/users/123/likes",
                json={"tweet_id": "456"},
            )
        elif boundary == "media":
            image_path = tmp_path / "image.jpg"
            image_path.write_bytes(b"not sent")
            bot.upload_media(str(image_path))
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
