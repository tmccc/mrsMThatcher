"""Share local installation and runtime-control setup for bot tests."""
from __future__ import annotations

import os
from pathlib import Path

import pytest

import mrsMThatcher2 as bot
from remote_write_safety_protocol import ACTIVATION_BASENAME
from tests.helpers.protocol_activation import create_test_protocol_activation


def install_paths(
    monkeypatch: pytest.MonkeyPatch,
    base: Path,
    *,
    activate_protocol: bool = True,
) -> None:
    """Install private durable paths and optional protocol activation."""

    monkeypatch.setattr(bot, "BASE_DIR", base)
    monkeypatch.setattr(bot, "STATE_FILE", base / "bot_state.json")
    monkeypatch.setattr(bot, "LINES_USED_FILE", base / "lines_used.json")
    monkeypatch.setattr(bot, "IMAGES_USED_FILE", base / "images_used.json")
    monkeypatch.setattr(bot, "INSTALLATION_MARKER_FILE", base / ".mrsMThatcher.initialised.json")
    monkeypatch.setattr(
        bot,
        "INSTALLATION_IN_PROGRESS_FILE",
        base / ".mrsMThatcher.initialising.json",
    )
    monkeypatch.setattr(bot, "REGULAR_POST_RECEIPT_FILE", base / "regular_post_receipt.json")
    monkeypatch.setattr(bot, "MEME_POST_RECEIPT_FILE", base / "meme_post_receipt.json")
    monkeypatch.setattr(bot, "CONFIRMED_REPLY_RECEIPT_FILE", base / "confirmed_reply_receipt.json")
    monkeypatch.setattr(
        bot,
        "MEDIA_UPLOAD_RECEIPT_FILE",
        base / "remote_media_upload_receipt.json",
    )
    monkeypatch.setattr(bot, "AMBIGUOUS_POST_OUTCOME_FILE", base / "ambiguous_post_outcome.json")
    monkeypatch.setattr(
        bot,
        "AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE",
        base / "ambiguous_post_outcome.restart_barrier.json",
    )
    activation = base / ACTIVATION_BASENAME
    monkeypatch.setattr(
        bot,
        "REMOTE_WRITE_SAFETY_PROTOCOL_ACTIVATION_FILE",
        activation,
    )
    monkeypatch.setattr(
        bot,
        "HISTORICAL_CONTEXT_REPLY_HISTORY_FILE",
        base / "historical_context_reply_history.json",
    )
    monkeypatch.setattr(
        bot,
        "HISTORICAL_CONTEXT_REPLY_RECEIPT_FILE",
        base / "historical_context_reply_receipt.json",
    )
    monkeypatch.setattr(
        bot,
        "HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE",
        base / "historical_context_reply_outbox.json",
    )
    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", False)
    monkeypatch.setattr(bot, "_AMBIGUOUS_MARKER_DURABILITY_UNCERTAIN", False)
    monkeypatch.setattr(bot, "_RETAINED_CONFIRMED_POST_SIGINT_GUARD", None)
    # Low-level transport cases in this module create an exact source receipt
    # without constructing the separate production outbox obligation.  The
    # outbox/source integration is exercised in its dedicated suites; keep this
    # fixture focused on the response and transport-journal boundary.
    monkeypatch.setattr(
        bot,
        "historical_context_outbox_remote_attempt_is_blocking",
        lambda **_kwargs: False,
    )
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 2)
    # Initialisation's real singleton ownership is covered separately; these
    # fixture-level calls exercise the durable namespace transaction itself.
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    if activate_protocol:
        create_test_protocol_activation(activation)


def reset_control_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset the process-local runtime-control cache for one test."""

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
    production_load_control = bot._runtime_control.RuntimeControls.load

    def tracked_load_control(owner) -> dict:
        loaded = production_load_control(owner)
        calls.append(loaded)
        return loaded

    monkeypatch.setattr(bot._runtime_control.RuntimeControls, "load", tracked_load_control)
    return calls
