"""Regression tests for bot runtime config logging."""

from __future__ import annotations

import builtins
import io
import json
import hashlib
import logging
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import (
    isolate_bot_runtime,
    image_analysis_for_paths,
)


pytestmark = pytest.mark.allow_loopback_network


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (True, True),
        (False, False),
        ("true", True),
        ("false", False),
        ("1", True),
        ("0", False),
        ("yes", True),
        ("no", False),
        ("on", True),
        ("off", False),
        ("banana", False),
        ([], False),
        ({}, False),
        (None, False),
        ("", False),
    ],
)
def test_control_bool_parses_explicit_values(value: object, expected: bool) -> None:
    assert bot.control_bool({"disable_meme_posts": value}, "disable_meme_posts") is expected


def test_control_bool_missing_value_is_false() -> None:
    assert bot.control_bool({}, "disable_meme_posts") is False


def test_log_json_debug_recursively_redacts_credentials_and_keeps_metadata() -> None:
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    previous_level = bot.log.level
    bot.log.addHandler(handler)
    bot.log.setLevel(logging.DEBUG)
    try:
        bot.log_json_debug(
            "payload",
            {
                "request_id": "request-123",
                "nested": {
                    "API-Key": "api-key-value",
                    "Authorization": "Bearer auth-value",
                    "items": [
                        {"oauth_token": "oauth-value", "status": "harmless"},
                        {"cookieJar": "cookie-value", "count": 3},
                    ],
                },
            },
        )
    finally:
        bot.log.removeHandler(handler)
        bot.log.setLevel(previous_level)

    output = stream.getvalue()
    assert "api-key-value" not in output
    assert "auth-value" not in output
    assert "oauth-value" not in output
    assert "cookie-value" not in output
    assert output.count("[REDACTED]") == 4
    assert "request-123" in output
    assert "harmless" in output
    assert '"count": 3' in output


def test_save_state_debug_logging_uses_value_free_summary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "STATE_FILE", tmp_path / "bot_state.json")
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 0)
    state = bot.default_state()
    state["tweet_cache"] = {"10": {"text": "cached incoming post secret text"}}
    state["pending_ai_reply_drafts"] = {
        "10": {"proposed_reply": "private reply draft text"}
    }
    state["provider_credentials"] = {"api_key": "credential-value"}
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    previous_level = bot.log.level
    bot.log.addHandler(handler)
    bot.log.setLevel(logging.DEBUG)
    try:
        bot.save_state(state)
    finally:
        bot.log.removeHandler(handler)
        bot.log.setLevel(previous_level)

    output = stream.getvalue()
    assert "State summary being saved" in output
    assert "tweet_cache" in output
    assert "pending_ai_reply_drafts" in output
    assert "cached incoming post secret text" not in output
    assert "private reply draft text" not in output
    assert "credential-value" not in output


def test_logging_defaults_to_info_and_explicit_debug_remains_supported(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("LOG_LEVEL", raising=False)
    assert bot.setup_logging(configure_file_logging=False).level == logging.INFO

    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    debug_logger = bot.setup_logging(configure_file_logging=False)
    assert debug_logger.level == logging.DEBUG
    assert debug_logger.isEnabledFor(logging.DEBUG)

    monkeypatch.delenv("LOG_LEVEL", raising=False)
    bot.setup_logging(configure_file_logging=False)


def test_single_call_context_and_reply_logs_expose_only_counts_and_hashes(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Keep exact prose and media URLs out of the new pipeline diagnostics."""

    prose = "PRIVATE-SINGLE-CALL-PROSE"
    media_url = "https://pbs.twimg.com/media/private-marker.jpg"
    direct_json_labels: list[str] = []
    original_log_json_debug = bot.log_json_debug

    def record_json_label(label: str, value: object, max_chars: int = 4000) -> None:
        direct_json_labels.append(label)
        original_log_json_debug(label, value, max_chars=max_chars)

    monkeypatch.setattr(bot, "log_json_debug", record_json_label)
    caplog.set_level(logging.DEBUG, logger=bot.log.name)

    prepared_context = bot.build_context_for_reply_ai(
        {
            "id": "920",
            "author_id": "200",
            "conversation_id": "920",
            "text": prose,
            "referenced_tweets": [],
        },
        bot.default_state(),
    )
    assert prepared_context is not None
    context = prepared_context.context
    bot.build_quote_tweet_reply_context(
        {
            "id": "900",
            "author_id": "12345",
            "text": "Original " + prose,
            "attachments": {"media_keys": ["photo-private"]},
            "_attached_media": [
                {
                    "media_key": "photo-private",
                    "type": "photo",
                    "url": media_url,
                }
            ],
        },
        {
            "id": "930",
            "conversation_id": "930",
            "author_id": "200",
            "text": "Commentary " + prose,
        },
    )
    bot._log_validated_single_call_reply(
        target_description="target",
        target_id="920",
        reply=prose,
    )

    messages = "\n".join(record.getMessage() for record in caplog.records)
    assert prose not in messages
    assert media_url not in messages
    assert "visible_turn_count=" in messages
    assert "context_sha256=" in messages
    assert hashlib.sha256(prose.encode("utf-8")).hexdigest() in messages
    assert not any(label.startswith("Single-call") for label in direct_json_labels)
    assert context["incoming_contribution"] == prose


def test_local_config_coercion_accepts_boolean_strings_and_rejects_boolean_ints() -> None:
    assert bot._coerce_local_config_value("ENABLE_AUTO_REPLIES", "false", True) is False
    assert bot._coerce_local_config_value("ENABLE_AUTO_REPLIES", "yes", False) is True

    with pytest.raises(ValueError):
        bot._coerce_local_config_value("MAX_AUTO_REPLIES_PER_DAY", True, 24)


@pytest.mark.parametrize("key,value", [("POST_SLEEP_MIN", -1), ("MAX_AUTO_REPLIES_PER_DAY", 0)])
def test_local_config_rejects_negative_and_nonpositive_timings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    key: str,
    value: int,
) -> None:
    assert f"{key} must be positive" in bot.validate_runtime_config_values({key: value})
    before = apply_local_config_for_test(
        tmp_path,
        monkeypatch,
        {key: value, "ENABLE_AUTO_REPLIES": False},
        initial={"ENABLE_AUTO_REPLIES": True},
        expect_error=True,
    )
    assert getattr(bot, key) == before[key]
    assert bot.ENABLE_AUTO_REPLIES is True


def apply_local_config_for_test(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    data: dict[str, object],
    *,
    initial: dict[str, object] | None = None,
    expect_error: bool = False,
) -> dict[str, object]:
    config_file = tmp_path / "mrsMThatcher.local.json"
    config_file.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr(bot, "LOCAL_CONFIG_FILE", config_file)
    for key, value in (initial or {}).items():
        monkeypatch.setattr(bot, key, value)
    before = {
        key: getattr(bot, key)
        for key in bot.LOCAL_CONFIG_ALLOWED_KEYS
        if hasattr(bot, key)
    }
    if expect_error:
        with pytest.raises(bot.LocalConfigError):
            bot.apply_local_config()
    else:
        bot.apply_local_config()
    return before


def test_local_config_interacting_invalid_overrides_are_atomic(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = apply_local_config_for_test(
        tmp_path,
        monkeypatch,
        {"POST_SLEEP_MIN": 10000, "POST_SLEEP_MAX": 5000},
        initial={"POST_SLEEP_MIN": 7200, "POST_SLEEP_MAX": 9000},
        expect_error=True,
    )

    assert bot.POST_SLEEP_MIN == before["POST_SLEEP_MIN"] == 7200
    assert bot.POST_SLEEP_MAX == before["POST_SLEEP_MAX"] == 9000


def test_local_config_valid_multi_key_override_applies_atomically(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    apply_local_config_for_test(
        tmp_path,
        monkeypatch,
        {
            "POST_SLEEP_MIN": 8000,
            "POST_SLEEP_MAX": 8200,
            "ENABLE_DAILY_MEME_POSTS": False,
            "MAX_MENTIONS_PER_CHECK": 10,
        },
        initial={
            "POST_SLEEP_MIN": 7200,
            "POST_SLEEP_MAX": 9000,
            "ENABLE_DAILY_MEME_POSTS": True,
            "MAX_MENTIONS_PER_CHECK": 5,
        },
    )

    assert bot.POST_SLEEP_MIN == 8000
    assert bot.POST_SLEEP_MAX == 8200
    assert bot.ENABLE_DAILY_MEME_POSTS is False
    assert bot.MAX_MENTIONS_PER_CHECK == 10


def test_local_config_unknown_key_rejects_whole_transaction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = apply_local_config_for_test(
        tmp_path,
        monkeypatch,
        {
            "ENABLE_AUTO_REPLY": False,
            "POST_SLEEP_MIN": 8000,
            "POST_SLEEP_MAX": 8200,
        },
        initial={
            "ENABLE_AUTO_REPLIES": True,
            "POST_SLEEP_MIN": 7200,
            "POST_SLEEP_MAX": 9000,
        },
        expect_error=True,
    )

    assert bot.ENABLE_AUTO_REPLIES is before["ENABLE_AUTO_REPLIES"] is True
    assert bot.POST_SLEEP_MIN == before["POST_SLEEP_MIN"] == 7200
    assert bot.POST_SLEEP_MAX == before["POST_SLEEP_MAX"] == 9000


def test_removed_quote_image_observer_key_is_an_unknown_configuration_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retired_key = "_".join(("quote", "image", "semantic", "veto"))
    before = apply_local_config_for_test(
        tmp_path,
        monkeypatch,
        {
            retired_key: {"enabled": False},
            "POST_SLEEP_MIN": 8000,
            "POST_SLEEP_MAX": 8200,
        },
        initial={"POST_SLEEP_MIN": 7200, "POST_SLEEP_MAX": 9000},
        expect_error=True,
    )

    assert retired_key not in bot.LOCAL_CONFIG_ALLOWED_KEYS
    assert bot.POST_SLEEP_MIN == before["POST_SLEEP_MIN"] == 7200
    assert bot.POST_SLEEP_MAX == before["POST_SLEEP_MAX"] == 9000


def test_bootstrap_and_regular_selection_do_not_load_removed_observer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module_name = ".".join(
        ("semantic_alignment", "_".join(("quote", "image", "semantic", "veto")))
    )
    event_name = "_".join(("quote", "image", "semantic", "veto", "shadow"))
    imported: list[str] = []
    emitted: list[str] = []
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        imported.append(str(name))
        if name == module_name:
            raise AssertionError("retired observer module import attempted")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    monkeypatch.setattr(bot, "TEST_MODE", False)
    monkeypatch.setattr(bot, "SELF_TEST_REQUESTED", False)
    monkeypatch.setattr(bot, "INITIALISE_REQUESTED", False)
    monkeypatch.setattr(bot, "_PRODUCTION_BOOTSTRAPPED", False)
    monkeypatch.setattr(bot, "_HISTORICAL_CONTEXT_RUNTIME_UNAVAILABLE_REASON", None)
    monkeypatch.setattr(bot, "setup_logging", lambda **_kwargs: bot.log)
    monkeypatch.setattr(bot, "initialise_bot_health_reporting", lambda: None)
    monkeypatch.setattr(bot, "apply_local_config", lambda: None)
    monkeypatch.setattr(bot, "validate_runtime_config_values", lambda _values: [])
    monkeypatch.setattr(bot, "load_completed_research_quote_hashes", lambda: {"a" * 64})
    monkeypatch.setattr(
        bot,
        "historical_context_reply_store",
        lambda: SimpleNamespace(history=lambda: {}),
    )
    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {**bot.historical_context_reply, "enabled": False},
    )
    monkeypatch.setattr(bot, "validate_production_credentials", lambda: None)
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda event, **_fields: emitted.append(str(event)),
    )

    bot.production_bootstrap(configure_file_logging=False)

    images = tmp_path / "images"
    images.mkdir()
    paths = [images / "t01.jpg", images / "t02.jpg"]
    for index, path in enumerate(paths):
        path.write_bytes(f"image-{index}".encode())
    metadata = image_analysis_for_paths(
        paths,
        {
            path.name: {
                "description": path.name,
                "seasonality": {"avoid_outside_season_or_occasion": False},
            }
            for path in paths
        },
    )
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(images / "t*"))
    monkeypatch.setattr(bot, "ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING", False)
    monkeypatch.setattr(bot, "load_image_analysis", lambda: metadata)
    monkeypatch.setattr(
        bot,
        "score_image_for_quote",
        lambda *_args: (10.0, {"topics": 10.0}, True),
    )
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 9, 3))
    bot.random.seed(4815)

    selected = bot.choose_matched_unused_image(
        set(),
        {"quote_hash": "c" * 64, "analysis": {}},
        {},
    )

    assert selected["basename"] in {"t01.jpg", "t02.jpg"}
    assert module_name not in imported
    assert event_name not in emitted


def test_local_config_coercion_failure_rejects_whole_transaction(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = apply_local_config_for_test(
        tmp_path,
        monkeypatch,
        {"POST_SLEEP_MIN": 8000, "POST_SLEEP_MAX": 8200, "ENABLE_AUTO_REPLIES": "maybe"},
        initial={
            "POST_SLEEP_MIN": 7200,
            "POST_SLEEP_MAX": 9000,
            "ENABLE_AUTO_REPLIES": True,
        },
        expect_error=True,
    )

    assert bot.POST_SLEEP_MIN == before["POST_SLEEP_MIN"] == 7200
    assert bot.POST_SLEEP_MAX == before["POST_SLEEP_MAX"] == 9000
    assert bot.ENABLE_AUTO_REPLIES is before["ENABLE_AUTO_REPLIES"] is True


def test_local_config_mixed_valid_and_invalid_values_do_not_partially_commit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    before = apply_local_config_for_test(
        tmp_path,
        monkeypatch,
        {"POST_SLEEP_MIN": 8000, "POST_SLEEP_MAX": 8200, "MAX_MENTIONS_PER_CHECK": 1},
        initial={
            "POST_SLEEP_MIN": 7200,
            "POST_SLEEP_MAX": 9000,
            "MAX_MENTIONS_PER_CHECK": 5,
        },
        expect_error=True,
    )

    assert bot.POST_SLEEP_MIN == before["POST_SLEEP_MIN"] == 7200
    assert bot.POST_SLEEP_MAX == before["POST_SLEEP_MAX"] == 9000
    assert bot.MAX_MENTIONS_PER_CHECK == before["MAX_MENTIONS_PER_CHECK"] == 5


def test_local_config_unsupported_key_cannot_override_arbitrary_globals(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    original_func = bot.log_json_debug
    before = apply_local_config_for_test(
        tmp_path,
        monkeypatch,
        {"log_json_debug": None, "X_API_BASE_URL": "https://evil.invalid", "POST_SLEEP_MIN": 7300, "POST_SLEEP_MAX": 7400},
        initial={"POST_SLEEP_MIN": 7200, "POST_SLEEP_MAX": 9000},
        expect_error=True,
    )

    assert bot.log_json_debug is original_func
    assert bot.POST_SLEEP_MIN == before["POST_SLEEP_MIN"] == 7200
    assert bot.POST_SLEEP_MAX == before["POST_SLEEP_MAX"] == 9000
    assert not hasattr(bot, "X_API_BASE_URL")


def test_local_config_existing_production_style_overrides_still_work(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    apply_local_config_for_test(
        tmp_path,
        monkeypatch,
        {
            "ENABLE_AUTO_REPLIES": True,
            "MIN_SECONDS_BETWEEN_REPLIES": 900,
            "MAX_AUTO_REPLIES_PER_DAY": 48,
            "MAX_REPLIES_PER_AUTHOR_PER_DAY": 6,
            "MAX_QUOTE_REPLIES_PER_DAY": 12,
            "POST_SLEEP_MIN": 7200,
            "POST_SLEEP_MAX": 9000,
        },
        initial={
            "ENABLE_AUTO_REPLIES": False,
            "MIN_SECONDS_BETWEEN_REPLIES": 1,
            "MAX_AUTO_REPLIES_PER_DAY": 2,
            "MAX_REPLIES_PER_AUTHOR_PER_DAY": 1,
            "MAX_QUOTE_REPLIES_PER_DAY": 1,
            "POST_SLEEP_MIN": 100,
            "POST_SLEEP_MAX": 200,
        },
    )

    assert bot.ENABLE_AUTO_REPLIES is True
    assert bot.MIN_SECONDS_BETWEEN_REPLIES == 900
    assert bot.MAX_AUTO_REPLIES_PER_DAY == 48
    assert bot.MAX_REPLIES_PER_AUTHOR_PER_DAY == 6
    assert bot.MAX_QUOTE_REPLIES_PER_DAY == 12
    assert bot.POST_SLEEP_MIN == 7200
    assert bot.POST_SLEEP_MAX == 9000


def test_default_reply_spacing_caps_and_lane_timers_match_production_policy() -> None:
    assert bot.MIN_SECONDS_BETWEEN_REPLIES == 900
    assert bot.MAX_AUTO_REPLIES_PER_DAY == 48
    assert bot.MAX_REPLIES_PER_AUTHOR_PER_DAY == 6
    assert bot.MAX_QUOTE_REPLIES_PER_DAY == 12
    assert bot.REPLY_CHECK_EVERY_SECONDS == 900
    assert bot.QUOTE_CHECK_EVERY_SECONDS == 3600
    assert bot.QUOTE_CHECK_SPACING_RETRY_SECONDS == 300


def test_default_reply_caps_are_48_global_6_per_author_and_12_quote() -> None:
    assert bot.MAX_AUTO_REPLIES_PER_DAY == 48
    assert bot.MAX_REPLIES_PER_AUTHOR_PER_DAY == 6
    assert bot.MAX_QUOTE_REPLIES_PER_DAY == 12


def test_retired_generated_configuration_is_ignored_while_original_editorial_remains_active(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    retired = {
        "ENABLE_GENERATED_IMAGE_POOL": True,
        "GENERATED_IMAGE_DIR": str(tmp_path / "unavailable-generated"),
        "GENERATED_IMAGE_GLOB": "*.png",
        "GENERATED_IMAGE_ANALYSIS_FILE": str(tmp_path / "missing-generated.json"),
        "GENERATED_IMAGE_ORIGIN_QUOTE_BOOST": 99,
        "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN": 3,
        "ENABLE_GENERATED_IDENTITY_POLICY_SCORING": True,
        "ENABLE_GENERATED_IDENTITY_POLICY_SHADOW_SCORING": True,
        "GENERATED_IDENTITY_AUDIT_FILE": str(tmp_path / "missing-audit.json"),
        "GENERATED_IDENTITY_SHADOW_SMALL_PENALTY": 99,
        "GENERATED_IDENTITY_SHADOW_STRONG_PENALTY": 999,
    }
    caplog.set_level(logging.INFO, logger=bot.log.name)
    apply_local_config_for_test(
        tmp_path, monkeypatch,
        retired | {"ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING": True},
        initial={"ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING": False},
    )

    assert bot.ENABLE_ORIGINAL_EDITORIAL_SHADOW_SCORING is True
    for key in retired:
        assert not hasattr(bot, key)
        assert key not in bot.LOCAL_CONFIG_ALLOWED_KEYS
        assert key not in bot.SOURCE_DEFAULT_CONFIG_VALUES
        assert f"Ignoring retired generated-image runtime setting {key}" in caplog.text


@pytest.mark.parametrize("old_value", [-1, True, False, 2.0, 2.5, "2", "x"])
def test_retired_generated_spacing_values_are_ignored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, old_value: object,
) -> None:
    apply_local_config_for_test(
        tmp_path, monkeypatch,
        {"GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN": old_value},
    )
    assert not hasattr(bot, "GENERATED_IMAGE_MIN_ORIGINAL_POSTS_BETWEEN")
