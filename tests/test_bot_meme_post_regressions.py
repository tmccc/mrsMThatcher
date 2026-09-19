"""Regression tests for bot meme post."""

from __future__ import annotations

import json
import multiprocessing
import os
from datetime import datetime
from pathlib import Path

import pytest

from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import (
    isolate_bot_runtime,
    mock_confirmed_main_post,
    install_receipt_bound_x_request_stub,
    configure_simple_meme_post,
)


pytestmark = pytest.mark.allow_loopback_network


def test_successful_meme_post_persists_post_and_future_schedule_in_one_state_save(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    meme_path = meme_dir / "001_meme.png"
    meme_path.write_bytes(b"meme")
    saved_states: list[dict] = []
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda path, **_kwargs: "media-1")
    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        lambda _attempt, _authority: None,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: mock_confirmed_main_post(
            kwargs, {"data": {"id": "970001"}}
        ),
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    real_save_state = bot.save_state

    def observe_save(state, **kwargs):
        """Capture the transition while retaining actual durable commit authority."""
        saved_states.append(json.loads(json.dumps(state)))
        return real_save_state(state, **kwargs)

    monkeypatch.setattr(bot, "save_state", observe_save)
    monkeypatch.setattr(bot, "log_event", lambda *args, **kwargs: None)

    state = {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    bot.post_next_meme(state)

    assert len(saved_states) == 1
    saved = saved_states[0]
    assert saved["last_main_post_id"] == "970001"
    assert saved["last_meme_post_epoch"] == 1_800_000_000
    assert "001_meme.png" in saved["posted_meme_filenames"]
    assert saved["next_meme_post_epoch"] > 1_800_000_000


@pytest.mark.parametrize("helper", ["cache_tweet", "record_recent_own_post"])
def test_meme_helper_failure_after_confirmation_leaves_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    helper: str,
) -> None:
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda path, **_kwargs: "media-1")
    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        lambda _attempt, _authority: None,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: mock_confirmed_main_post(
            kwargs, {"data": {"id": "970001"}}
        ),
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, helper, lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(f"{helper} failed")))
    monkeypatch.setattr(bot, "log_event", lambda *args, **kwargs: None)

    state = {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_next_meme(state)

    assert bot.MEME_POST_RECEIPT_FILE.exists()
    assert json.loads(bot.MEME_POST_RECEIPT_FILE.read_text(encoding="utf-8"))["post_id"] == "970001"


def test_daily_meme_missing_post_id_fails_without_success_side_effects(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    events: list[tuple[str, dict[str, object]]] = []
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda path, **_kwargs: "media-1")
    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        lambda _attempt, _authority: None,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: mock_confirmed_main_post(kwargs, {"data": {}}),
    )
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: pytest.fail("save_state should not be called"))
    monkeypatch.setattr(
        bot,
        "log_event",
        lambda event, **kwargs: events.append((event, kwargs)),
    )

    state = {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    with pytest.raises(RuntimeError, match="valid post id"):
        bot.post_next_meme(state)

    assert state == {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    assert events == [
        (
            "daily_meme_failure",
            {
                "status": "failed",
                "stage": "x_post_response_validation",
                "error_type": "RuntimeError",
                "reason": "Daily meme post did not return a valid post id; meme state unchanged",
            },
        )
    ]


def test_meme_post_uses_confirmed_time_across_midnight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda path, **_kwargs: "media-1")
    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        lambda _attempt, _authority: None,
    )
    monkeypatch.setattr(bot, "log_event", lambda *args, **kwargs: None)
    pre_confirm_epoch = int(datetime(2026, 7, 6, 23, 59, 50).timestamp())
    confirmed_epoch = int(datetime(2026, 7, 7, 0, 0, 5).timestamp())
    remote_confirmed = {"value": False}

    def fake_create_post(**kwargs: object) -> dict:
        remote_confirmed["value"] = True
        result = mock_confirmed_main_post(
            kwargs,
            {"data": {"id": "970001"}},
        )
        return result

    def fake_now_epoch() -> int:
        return confirmed_epoch if remote_confirmed["value"] else pre_confirm_epoch

    monkeypatch.setattr(bot, "create_post", fake_create_post)
    monkeypatch.setattr(bot, "now_epoch", fake_now_epoch)

    state = {"next_meme_post_epoch": pre_confirm_epoch, "posted_meme_filenames": []}
    bot.post_next_meme(state)

    assert state["last_meme_post_epoch"] == confirmed_epoch
    assert bot.epoch_date_str(state["last_meme_post_epoch"]) == "2026-07-07"
    assert state["posted_meme_filenames"] == ["001_meme.png"]
    assert state["next_meme_schedule_mode"] == "fallback"
    assert state["next_meme_post_epoch"] > confirmed_epoch
    assert bot.epoch_date_str(state["next_meme_post_epoch"]) == "2026-07-08"


def test_meme_same_local_date_barrier_suppresses_second_remote_create(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_epoch = int(datetime(2026, 7, 7, 9, 0, 0).timestamp())
    first_epoch = int(datetime(2026, 7, 7, 8, 0, 0).timestamp())
    monkeypatch.setattr(bot, "now_epoch", lambda: current_epoch)
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: pytest.fail(
            "a second meme on the confirmed local date must never reach X"
        ),
    )
    state = {
        "last_meme_post_epoch": first_epoch,
        "last_main_post_id": "970001",
        "next_meme_post_epoch": current_epoch,
        "posted_meme_filenames": ["001_meme.png"],
    }

    bot.post_next_meme(state)

    assert state["last_main_post_id"] == "970001"
    assert state["posted_meme_filenames"] == ["001_meme.png"]
    assert bot.epoch_date_str(state["next_meme_post_epoch"]) == "2026-07-08"
    assert state["next_meme_schedule_mode"] == "fallback"


def test_meme_schedule_finalisation_failure_after_confirmation_is_confirmed_local_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda path, **_kwargs: "media-1")
    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        lambda _attempt, _authority: None,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: mock_confirmed_main_post(
            kwargs, {"data": {"id": "970001"}}
        ),
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    original_materialize = bot.materialize_bound_meme_schedule_receipt
    monkeypatch.setattr(
        bot,
        "materialize_bound_meme_schedule_receipt",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("schedule finalisation failed")
        ),
    )
    monkeypatch.setattr(bot, "log_event", lambda *args, **kwargs: None)

    state = {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_next_meme(state)

    status, pending = bot.load_meme_post_receipt()
    assert status == "pending_schedule"
    assert pending is not None
    assert pending["post_id"] == "970001"
    assert state["posted_meme_filenames"] == []

    expected = original_materialize(pending, _validate_result=False)
    monkeypatch.setattr(
        bot,
        "materialize_bound_meme_schedule_receipt",
        original_materialize,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: pytest.fail("confirmed pending receipt must not repost"),
    )
    bot.post_next_meme(state)
    assert not bot.MEME_POST_RECEIPT_FILE.exists()
    assert state["last_main_post_id"] == "970001"
    assert state["last_meme_post_epoch"] == 1_800_000_000
    assert state["posted_meme_filenames"] == ["001_meme.png"]
    assert state["next_meme_post_epoch"] == expected["next_meme_post_epoch"]
    assert bot.epoch_date_str(state["next_meme_post_epoch"]) > bot.epoch_date_str(
        state["last_meme_post_epoch"]
    )


def test_confirmed_meme_state_failure_reconciles_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    receipt_file = tmp_path / "meme_post_receipt.json"
    monkeypatch.setattr(bot, "MEME_POST_RECEIPT_FILE", receipt_file)
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda path, **_kwargs: "media-1")
    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        lambda _attempt, _authority: None,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: mock_confirmed_main_post(
            kwargs, {"data": {"id": "970001"}}
        ),
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    real_save_state = bot.save_state
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: (_ for _ in ()).throw(OSError("state failed")))
    monkeypatch.setattr(bot, "log_event", lambda *args, **kwargs: None)

    state = {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_next_meme(state)

    # The confirmed journal remains a global remote-write barrier until the
    # failed local state transition is replayed and both durable records are
    # retired.
    assert bot.ambiguous_remote_post_is_blocking() is True
    assert receipt_file.exists()
    receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
    assert receipt["next_meme_post_epoch"] > 1_800_000_000
    assert state["next_meme_post_epoch"] > state["last_meme_post_epoch"]

    monkeypatch.setattr(bot, "save_state", real_save_state)
    recovered: dict = {}
    assert bot.reconcile_meme_post_receipt(recovered) is True
    assert bot.reconcile_meme_post_receipt(recovered) is False
    assert recovered["last_main_post_id"] == "970001"
    assert recovered["posted_meme_filenames"] == ["001_meme.png"]
    assert recovered["next_meme_post_epoch"] == receipt["next_meme_post_epoch"]


def test_meme_restart_recovers_confirmation_after_state_save_failure_before_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state, receipt_file = configure_simple_meme_post(tmp_path, monkeypatch)
    (bot.MEME_DIR / "002_meme.png").write_bytes(b"second meme")
    confirmed_epoch = 1_800_000_000
    bot.save_state(state, durable=True)
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: mock_confirmed_main_post(
            kwargs,
            {"data": {"id": "970001"}},
        ),
    )
    original_save_state = bot.save_state
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("injected confirmed-state save failure")
        ),
    )

    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_next_meme(state)

    status, receipt = bot.load_meme_post_receipt()
    assert status == "valid"
    assert receipt is not None and receipt["post_id"] == "970001"
    restarted_state = bot.load_runtime_state()
    assert int(restarted_state.get("last_meme_post_epoch", 0) or 0) == 0

    monkeypatch.setattr(bot, "save_state", original_save_state)
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **_kwargs: pytest.fail(
            "restart must not create a second meme on the confirmed local date"
        ),
    )
    reconciled = bot.reconcile_startup_main_post_receipts(
        set(),
        set(),
        restarted_state,
        confirmed_epoch,
    )

    assert reconciled == {"regular": False, "meme": True}
    assert not receipt_file.exists()
    assert restarted_state["last_main_post_id"] == "970001"
    assert restarted_state["last_meme_post_epoch"] == confirmed_epoch
    assert restarted_state["posted_meme_filenames"] == ["001_meme.png"]
    assert bot.load_runtime_state()["last_meme_post_epoch"] == confirmed_epoch

    bot.post_next_meme(restarted_state)

    assert restarted_state["last_main_post_id"] == "970001"
    assert restarted_state["posted_meme_filenames"] == ["001_meme.png"]
    assert bot.meme_schedule_date_str(
        restarted_state["next_meme_post_epoch"]
    ) > bot.meme_schedule_date_str(confirmed_epoch)


def test_meme_receipt_replay_does_not_create_second_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt_file = tmp_path / "meme_post_receipt.json"
    monkeypatch.setattr(bot, "MEME_POST_RECEIPT_FILE", receipt_file)
    bot.atomic_write_json(
        receipt_file,
        {
            "schema_version": 1,
            "post_id": "970001",
            "meme_basename": "001_meme.png",
            "meme_post_epoch": 1_800_000_000,
            "next_meme_post_epoch": 1_800_086_400,
        },
    )
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: pytest.fail("create_post should not be called after meme receipt replay"))

    state: dict = {}
    bot.post_next_meme(state)

    assert not receipt_file.exists()
    assert state["posted_meme_filenames"] == ["001_meme.png"]
    assert state["next_meme_post_epoch"] == 1_800_086_400


def test_confirmed_meme_receipt_write_failure_keeps_normal_schedule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    (meme_dir / "002_meme.png").write_bytes(b"second meme")
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda path, **_kwargs: "media-1")
    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        lambda _attempt, _authority: None,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: mock_confirmed_main_post(
            kwargs, {"data": {"id": "970001"}}
        ),
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    original_write = bot.write_meme_post_receipt
    monkeypatch.setattr(bot, "write_meme_post_receipt", lambda receipt: (_ for _ in ()).throw(OSError("receipt failed")))
    monkeypatch.setattr(bot, "log_event", lambda *args, **kwargs: None)

    state = {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    with pytest.raises(bot.ConfirmedPostLocalPersistenceError) as caught:
        bot.post_next_meme(state)

    assert type(caught.value) is bot.ConfirmedPostLocalPersistenceError
    status, pending = bot.load_meme_post_receipt()
    assert status == "pending_schedule"
    assert pending is not None and pending["post_id"] == "970001"
    assert state["posted_meme_filenames"] == []
    assert "last_meme_post_epoch" not in state
    monkeypatch.setattr(
        bot,
        "write_meme_post_receipt",
        original_write,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **_kwargs: pytest.fail(
            "same-date restart must not post the second available meme"
        ),
    )
    bot.post_next_meme(state)
    assert state["posted_meme_filenames"] == ["001_meme.png"]


def test_confirmed_meme_sigint_is_delivered_only_after_durable_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    receipt_file = tmp_path / "meme_post_receipt.json"
    monkeypatch.setattr(bot, "MEME_POST_RECEIPT_FILE", receipt_file)
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda _path, **_kwargs: "media-1")
    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        lambda _attempt, _authority: None,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: mock_confirmed_main_post(
            kwargs, {"data": {"id": "970001"}}
        ),
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "log_event", lambda *_args, **_kwargs: None)
    stages: list[str] = []
    original_write = bot.write_meme_post_receipt

    guard_token = object()

    def begin_deferral() -> object:
        stages.append("begin")
        return guard_token

    def write_receipt(receipt: dict) -> None:
        stages.append("receipt")
        original_write(receipt)

    def deliver_pending_sigint(guard: object | None) -> None:
        assert guard is guard_token
        assert receipt_file.exists()
        stages.append("end")
        raise KeyboardInterrupt

    monkeypatch.setattr(bot, "begin_confirmed_post_sigint_deferral", begin_deferral)
    monkeypatch.setattr(bot, "write_meme_post_receipt", write_receipt)
    monkeypatch.setattr(bot, "end_confirmed_post_sigint_deferral", deliver_pending_sigint)

    state = {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    with pytest.raises(KeyboardInterrupt):
        bot.post_next_meme(state)

    assert stages == ["begin", "receipt", "end"]
    assert json.loads(receipt_file.read_text(encoding="utf-8"))["post_id"] == "970001"
    assert bot.ambiguous_remote_post_is_blocking() is True


def test_meme_hard_death_after_remote_acceptance_leaves_restart_barrier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_create_post = bot.create_post
    state, receipt_file = configure_simple_meme_post(tmp_path, monkeypatch)
    remote_acceptance = tmp_path / "meme-remote-accepted"

    def accept_then_hard_exit(
        method: str,
        path: str,
        **_kwargs: object,
    ) -> dict:
        assert method == "POST"
        assert path == "/2/tweets"
        assert receipt_file.exists()
        status, attempt = bot.load_meme_post_receipt()
        assert status == "sending"
        assert attempt is not None
        assert attempt["lifecycle_state"] == "attempting"
        descriptor = os.open(
            remote_acceptance,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        try:
            os.write(descriptor, b"accepted")
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os._exit(74)

    monkeypatch.setattr(bot, "create_post", actual_create_post)
    install_receipt_bound_x_request_stub(monkeypatch, accept_then_hard_exit)
    context = multiprocessing.get_context("fork")
    process = context.Process(target=bot.post_next_meme, args=(state,))
    process.start()
    process.join(timeout=10)
    assert process.exitcode == 74
    assert remote_acceptance.read_bytes() == b"accepted"

    status, attempt = bot.load_meme_post_receipt()
    assert status == "sending"
    assert attempt is not None
    assert attempt["lane"] == "daily_meme"
    assert attempt["lifecycle_state"] == "attempting"
    assert attempt["selected_identity"] == {"meme_basename": "001_meme.png"}
    assert attempt["recovery_plan"] == {
        "next_schedule_mode": "fallback",
        "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
        "fallback_hour": bot.MEME_FALLBACK_HOUR,
        "fallback_minute": bot.MEME_FALLBACK_MINUTE,
        "image_summary": (
            "Anti-socialist meme image. Original filename: 001_meme.png."
        ),
        "schedule_timezone": bot.MAIN_POST_SCHEDULE_TIMEZONE,
    }
    assert bot.ambiguous_remote_post_is_blocking() is True

    retry_calls = 0

    def forbidden_retry(**_kwargs: object) -> dict:
        nonlocal retry_calls
        retry_calls += 1
        return {"data": {"id": "970002"}}

    monkeypatch.setattr(bot, "create_post", forbidden_retry)
    for _restart in range(2):
        with pytest.raises(bot.AmbiguousRemotePostOutcome):
            bot.post_next_meme(state)
    assert retry_calls == 0


def test_meme_generic_4xx_retains_sending_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_create_post = bot.create_post
    state, _receipt_file = configure_simple_meme_post(tmp_path, monkeypatch)

    def definite_failure(
        method: str,
        path: str,
        **_kwargs: object,
    ) -> dict:
        assert method == "POST"
        assert path == "/2/tweets"
        status, attempt = bot.load_meme_post_receipt()
        assert status == "sending"
        assert attempt is not None
        assert attempt["lifecycle_state"] == "attempting"
        raise bot.ApiError(
            "definite rejection",
            service="x",
            status_code=400,
            request_method="POST",
            request_path="/2/tweets",
        )

    monkeypatch.setattr(bot, "create_post", actual_create_post)
    install_receipt_bound_x_request_stub(monkeypatch, definite_failure)
    with pytest.raises(bot.ApiError, match="definite rejection"):
        bot.post_next_meme(state)

    status, attempt = bot.load_meme_post_receipt()
    assert status == "sending"
    assert attempt is not None
    assert attempt["lifecycle_state"] == "attempting"
    assert bot.ambiguous_remote_post_is_blocking() is True


@pytest.mark.parametrize(
    "failure_kind",
    ["http_500", "status_unknown", "unexpected", "transport"],
)
def test_meme_uncertain_remote_failure_retains_attempt_and_blocks_retry(
    failure_kind: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_create_post = bot.create_post
    actual_x_request = bot.x_request
    state, _receipt_file = configure_simple_meme_post(tmp_path, monkeypatch)
    attempts = 0

    def uncertain_request(
        _method: str,
        _path: str,
        **_kwargs: object,
    ) -> dict:
        nonlocal attempts
        attempts += 1
        if failure_kind == "http_500":
            raise bot.ApiError("uncertain 500", service="x", status_code=500)
        if failure_kind == "status_unknown":
            raise bot.ApiError("unknown status", service="x")
        raise RuntimeError("unexpected transport wrapper failure")

    monkeypatch.setattr(bot, "create_post", actual_create_post)
    if failure_kind == "transport":
        monkeypatch.setattr(bot, "x_request", actual_x_request)

        def transport_failure(*_args: object, **_kwargs: object) -> object:
            nonlocal attempts
            attempts += 1
            raise bot.requests.ConnectionError("connection dropped")

        monkeypatch.setattr(bot.requests, "request", transport_failure)
        expected_error: type[BaseException] = bot.AmbiguousRemotePostOutcome
    else:
        install_receipt_bound_x_request_stub(monkeypatch, uncertain_request)
        expected_error = (
            RuntimeError
            if failure_kind == "unexpected"
            else bot.ApiError
        )

    with pytest.raises(expected_error):
        bot.post_next_meme(state)

    status, attempt = bot.load_meme_post_receipt()
    assert status == "sending"
    assert attempt is not None
    assert attempt["lifecycle_state"] == "attempting"
    assert attempts == 1

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.post_next_meme(state)
    assert attempts == 1


def test_meme_normal_success_atomically_promotes_sending_attempt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual_create_post = bot.create_post
    state, receipt_file = configure_simple_meme_post(tmp_path, monkeypatch)
    observed_attempt: dict = {}

    def confirmed_create(
        method: str,
        path: str,
        **_kwargs: object,
    ) -> dict:
        assert method == "POST"
        assert path == "/2/tweets"
        status, attempt = bot.load_meme_post_receipt()
        assert status == "sending"
        assert attempt is not None
        assert attempt["lifecycle_state"] == "attempting"
        observed_attempt.update(attempt)
        return {"data": {"id": "970001"}}

    monkeypatch.setattr(bot, "create_post", actual_create_post)
    install_receipt_bound_x_request_stub(monkeypatch, confirmed_create)
    monkeypatch.setattr(
        bot, "remove_meme_post_receipt", lambda *_args, **_kwargs: None
    )
    bot.post_next_meme(state)

    status, confirmed = bot.load_meme_post_receipt()
    assert status == "valid"
    assert confirmed is not None
    assert confirmed["post_id"] == "970001"
    assert confirmed["attempt_id"] == observed_attempt["attempt_id"]
    assert (
        confirmed["attempt_payload_sha256"]
        == observed_attempt["payload_sha256"]
    )
    assert receipt_file.exists()


def test_meme_ambiguous_create_without_marker_uses_durable_attempt_barrier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_atomic_write = bot.atomic_write_json
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda _path, **_kwargs: "media-1")
    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        lambda _attempt, _authority: None,
    )
    monkeypatch.setattr(bot, "log_event", lambda *_args, **_kwargs: None)

    def ambiguous_create(**kwargs: object) -> dict:
        mock_confirmed_main_post(kwargs, {})
        bot.record_ambiguous_remote_post({"text": bot.MEME_POST_TEXT})
        raise bot.AmbiguousRemotePostOutcome("ambiguous", service="x")

    monkeypatch.setattr(bot, "create_post", ambiguous_create)
    def fail_only_ambiguous_marker(
        path: Path,
        value: object,
        **kwargs: object,
    ) -> None:
        if Path(path) == bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE:
            raise OSError("marker failed")
        original_atomic_write(path, value, **kwargs)

    monkeypatch.setattr(bot, "atomic_write_json", fail_only_ambiguous_marker)
    guard_token = object()
    ended_guards: list[object | None] = []
    monkeypatch.setattr(bot, "begin_confirmed_post_sigint_deferral", lambda: guard_token)
    monkeypatch.setattr(
        bot,
        "end_confirmed_post_sigint_deferral",
        lambda guard: ended_guards.append(guard),
    )

    state = {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.post_next_meme(state)

    assert bot.ambiguous_remote_post_is_blocking() is True
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert ended_guards == [guard_token]


def test_meme_emergency_canonical_state_survives_backup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda _path, **_kwargs: "media-1")
    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        lambda _attempt, _authority: None,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: mock_confirmed_main_post(
            kwargs, {"data": {"id": "970001"}}
        ),
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(
        bot,
        "promote_main_post_attempt_to_confirmed_pending_schedule",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("receipt failed")),
    )
    monkeypatch.setattr(
        bot,
        "write_latest_state_backup",
        lambda **_kwargs: (_ for _ in ()).throw(OSError("backup failed")),
    )
    monkeypatch.setattr(bot, "log_event", lambda *_args, **_kwargs: None)

    state = {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    with pytest.raises(bot.ConfirmedPostLocalPersistenceError) as caught:
        bot.post_next_meme(state)

    assert type(caught.value) is bot.ConfirmedPostLocalPersistenceError
    assert bot.json_file_matches(bot.STATE_FILE, state) is True
    assert bot.ambiguous_remote_post_is_blocking() is False


def test_meme_total_persistence_loss_latches_all_remote_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    remote_calls = 0
    original_create_post = bot.create_post

    def confirmed_create(**kwargs: object) -> dict:
        nonlocal remote_calls
        remote_calls += 1
        return mock_confirmed_main_post(
            kwargs,
            {"data": {"id": "970001"}},
        )

    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda _path, **_kwargs: "media-1")
    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        lambda _attempt, _authority: None,
    )
    monkeypatch.setattr(bot, "create_post", confirmed_create)
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(
        bot,
        "promote_main_post_attempt_to_confirmed_pending_schedule",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("receipt failed")),
    )
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("state failed")),
    )
    monkeypatch.setattr(bot, "log_event", lambda *_args, **_kwargs: None)

    state = {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    with pytest.raises(bot.UnrecoverableConfirmedPostPersistenceError):
        bot.post_next_meme(state)

    assert remote_calls == 1
    assert bot.ambiguous_remote_post_is_blocking() is True
    marker = json.loads(bot.AMBIGUOUS_POST_OUTCOME_FILE.read_text(encoding="utf-8"))
    assert marker["outcome"] == "confirmed_remote_post_local_persistence_failed"
    assert marker["lane"] == "daily_meme"
    assert marker["post_id"] == "970001"
    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", False)
    assert bot.ambiguous_remote_post_is_blocking() is True
    boundary_calls = 0

    def unexpected_boundary(*_args: object, **_kwargs: object) -> dict:
        nonlocal boundary_calls
        boundary_calls += 1
        pytest.fail("remote boundary must not be reached after the durable safety marker")

    install_receipt_bound_x_request_stub(monkeypatch, unexpected_boundary)
    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        original_create_post("must not be sent")
    assert remote_calls == 1
    assert boundary_calls == 0


def test_meme_total_persistence_and_marker_loss_uses_durable_attempt_barrier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_atomic_write = bot.atomic_write_json
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda _path, **_kwargs: "media-1")
    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        lambda _attempt, _authority: None,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: mock_confirmed_main_post(
            kwargs, {"data": {"id": "970001"}}
        ),
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(
        bot,
        "promote_main_post_attempt_to_confirmed_pending_schedule",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("receipt failed")),
    )
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("state failed")),
    )
    def fail_only_ambiguous_marker(
        path: Path,
        value: object,
        **kwargs: object,
    ) -> None:
        if Path(path) == bot.AMBIGUOUS_POST_OUTCOME_SUCCESSOR_FILE:
            raise OSError("marker failed")
        original_atomic_write(path, value, **kwargs)

    monkeypatch.setattr(bot, "atomic_write_json", fail_only_ambiguous_marker)
    monkeypatch.setattr(bot, "log_event", lambda *_args, **_kwargs: None)
    guard_token = object()
    ended_guards: list[object | None] = []
    monkeypatch.setattr(bot, "begin_confirmed_post_sigint_deferral", lambda: guard_token)
    monkeypatch.setattr(
        bot,
        "end_confirmed_post_sigint_deferral",
        lambda guard: ended_guards.append(guard),
    )

    state = {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    with pytest.raises(bot.UnrecoverableConfirmedPostPersistenceError):
        bot.post_next_meme(state)

    assert bot.ambiguous_remote_post_is_blocking() is True
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert ended_guards == [guard_token]


def test_confirmed_meme_with_incomplete_emergency_state_latches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda _path, **_kwargs: "media-1")
    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        lambda _attempt, _authority: None,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: mock_confirmed_main_post(
            kwargs,
            {"data": {"id": "970001"}},
        ),
    )
    clock_calls = 0

    def clock_fails_after_attempt_persistence() -> int:
        nonlocal clock_calls
        clock_calls += 1
        if clock_calls == 1:
            return 1_800_000_000
        raise RuntimeError("clock failed after confirmation")

    monkeypatch.setattr(bot, "now_epoch", clock_fails_after_attempt_persistence)
    saved_states: list[dict] = []
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda state, **_kwargs: saved_states.append(json.loads(json.dumps(state))),
    )
    monkeypatch.setattr(bot, "log_event", lambda *_args, **_kwargs: None)

    state = {
        "last_main_post_id": "960000",
        "last_meme_post_epoch": 1_700_000_000,
        "next_meme_post_epoch": 1_700_086_400,
        "posted_meme_filenames": [],
    }
    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_next_meme(state)

    assert saved_states == []
    status, receipt = bot.load_meme_post_receipt()
    assert status == "valid"
    assert receipt is not None
    assert receipt["post_id"] == "970001"
    assert bot.ambiguous_remote_post_is_blocking() is True
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()


def test_meme_receipt_removal_failure_keeps_future_meme_schedule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    meme_dir = tmp_path / "memes"
    meme_dir.mkdir()
    (meme_dir / "001_meme.png").write_bytes(b"meme")
    monkeypatch.setattr(bot, "MEME_DIR", meme_dir)
    monkeypatch.setattr(bot, "MEME_ANALYSIS_FILE", tmp_path / "missing.json")
    monkeypatch.setattr(bot, "upload_media", lambda path, **_kwargs: "media-1")
    monkeypatch.setattr(
        bot,
        "handoff_confirmed_media_upload_to_main_attempt",
        lambda _attempt, _authority: None,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: mock_confirmed_main_post(
            kwargs, {"data": {"id": "970001"}}
        ),
    )
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(
        bot,
        "remove_meme_post_receipt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("remove failed")),
    )
    monkeypatch.setattr(bot, "log_event", lambda *args, **kwargs: None)

    state = {"next_meme_post_epoch": 1_799_999_000, "posted_meme_filenames": []}
    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_next_meme(state)

    assert state["next_meme_post_epoch"] > state["last_meme_post_epoch"]
    assert state["meme_anchor_quote_post_epoch"] == 0
