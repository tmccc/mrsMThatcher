"""Regression tests for bot regular post."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import (
    isolate_regular_post_receipt,
    quote_analysis_for_lines,
    image_analysis_for_paths,
    configure_simple_quote_post,
    mock_confirmed_main_post,
    install_receipt_bound_x_request_stub,
)


pytestmark = pytest.mark.allow_loopback_network


def test_post_random_quote_requires_created_post_id_before_marking_histories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"fake")
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    lines_used_file = tmp_path / "lines_used.json"
    images_used_file = tmp_path / "images_used.json"
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "LINES_USED_FILE", lines_used_file)
    monkeypatch.setattr(bot, "IMAGES_USED_FILE", images_used_file)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))
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
    monkeypatch.setattr(bot, "maybe_schedule_meme_after_quote_post", lambda *args, **kwargs: pytest.fail("meme scheduling should not be called"))
    monkeypatch.setattr(bot, "cache_tweet", lambda *args, **kwargs: pytest.fail("cache_tweet should not be called"))
    monkeypatch.setattr(bot, "record_recent_own_post", lambda *args, **kwargs: pytest.fail("record_recent_own_post should not be called"))
    monkeypatch.setattr(bot, "log_event", lambda event, **kwargs: events.append((event, kwargs)))
    monkeypatch.setattr(
        bot,
        "load_quote_analysis",
        lambda: quote_analysis_for_lines(
            ["Good quote."],
            {
                0: {
                        "archive_image_preferences": {},
                        "seasonality": {"hard_exclude_outside_windows": False, "preferred_windows": [], "relevance": "none"},
                }
            },
        ),
    )
    monkeypatch.setattr(
        bot,
        "load_image_analysis",
        lambda: image_analysis_for_paths(
            [image_path],
            {
                "t01.jpg": {
                        "description": "A usable image",
                        "pairing": {},
                        "themes": [],
                        "tone": [],
                        "visual_energy": "low",
                        "quality": {},
                        "seasonality": {"avoid_outside_season_or_occasion": False},
                }
            },
        ),
    )

    lines_used: set[int] = set()
    images_used: set[str] = set()
    state = {"last_regular_image_filename": "previous.jpg"}

    with pytest.raises(RuntimeError, match="valid post id"):
        bot.post_random_quote(lines_used, images_used, state)

    assert lines_used == set()
    assert images_used == set()
    assert state["last_regular_image_filename"] == "previous.jpg"
    assert not lines_used_file.exists()
    assert not images_used_file.exists()
    assert events == []


@pytest.mark.parametrize("failure", ["save_state", "quote_history", "image_history"])
def test_confirmed_regular_post_receipt_recovers_local_persistence_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    lines_used, images_used, state, lines_used_file, images_used_file, receipt_file, lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    quote_hash = bot.quote_text_hash("Good quote.")
    original_save_state = bot.save_state
    original_save_quote = bot.save_quote_used_hashes
    original_save_image = bot.save_image_used_basenames

    if failure == "save_state":
        monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: (_ for _ in ()).throw(OSError("state write failed")))
    elif failure == "quote_history":
        monkeypatch.setattr(bot, "save_quote_used_hashes", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("quote history failed")))
        monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)
    else:
        monkeypatch.setattr(bot, "save_image_used_basenames", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("image history failed")))
        monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)

    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_random_quote(lines_used, images_used, state)

    # The confirmed journal and receipt remain a global barrier until the
    # protected local transition is replayed; no unrelated lane may overtake
    # this locally recoverable transaction.
    assert bot.ambiguous_remote_post_is_blocking() is True
    assert receipt_file.exists()
    assert quote_hash in lines_used
    assert "t01.jpg" in images_used

    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None if failure != "save_state" else original_save_state(state, **kwargs))
    monkeypatch.setattr(bot, "save_quote_used_hashes", original_save_quote)
    monkeypatch.setattr(bot, "save_image_used_basenames", original_save_image)

    reconciled_lines: set[str] = set()
    reconciled_images: set[str] = set()
    reconciled_state: dict = {}
    assert bot.reconcile_regular_post_receipt(reconciled_lines, reconciled_images, reconciled_state) is True
    assert bot.ambiguous_remote_post_is_blocking() is False
    assert not receipt_file.exists()
    assert json.loads(lines_used_file.read_text(encoding="utf-8")) == [quote_hash]
    assert json.loads(images_used_file.read_text(encoding="utf-8")) == ["t01.jpg"]
    assert quote_hash in reconciled_lines
    assert "t01.jpg" in reconciled_images

    lines_file.write_text("Good quote.\nNew quote.\n", encoding="utf-8")
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines(["Good quote.", "New quote."]))
    candidates = bot.quote_candidates_for_current_cycle(reconciled_lines)
    assert [candidate["text"] for candidate in candidates] == ["New quote."]


def test_receipt_write_failure_leaves_confirmed_assets_marked_in_memory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, _receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    quote_hash = bot.quote_text_hash("Good quote.")
    monkeypatch.setattr(bot, "write_regular_post_receipt", lambda receipt: (_ for _ in ()).throw(OSError("receipt failed")))

    with pytest.raises(
        bot.ConfirmedPostLocalPersistenceError,
        match="pending-schedule receipt remains",
    ) as caught:
        bot.post_random_quote(lines_used, images_used, state)

    assert type(caught.value) is bot.ConfirmedPostLocalPersistenceError
    assert bot.ambiguous_remote_post_is_blocking() is True
    assert quote_hash not in lines_used
    assert "t01.jpg" not in images_used
    status, pending = bot.load_regular_post_receipt()
    assert status == "pending_schedule"
    assert pending is not None and pending["post_id"] == "950001"


def test_regular_no_receipt_emergency_representation_reloads_completely(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        lines_used,
        images_used,
        state,
        _lines_used_file,
        _images_used_file,
        receipt_file,
        lines_file,
    ) = configure_simple_quote_post(tmp_path, monkeypatch)
    pending_receipts: list[dict] = []
    posted_events: list[dict[str, object]] = []
    monkeypatch.setattr(
        bot,
        "emit_account_root_posted",
        lambda **fields: posted_events.append(dict(fields)),
    )

    def fail_receipt(attempt: dict, **kwargs: object) -> None:
        pending_receipts.append(
            bot.build_confirmed_pending_schedule_receipt(attempt, **kwargs)
        )
        raise OSError("receipt failed")

    monkeypatch.setattr(
        bot,
        "promote_main_post_attempt_to_confirmed_pending_schedule",
        fail_receipt,
    )

    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_random_quote(lines_used, images_used, state)

    assert posted_events == []
    assert not receipt_file.exists()
    reloaded_state = bot.load_runtime_state()
    reloaded_lines = bot.load_quote_used_hashes(
        lines_file.read_text(encoding="utf-8").splitlines(keepends=True)
    )
    image_paths = bot.current_image_paths()
    reloaded_images = bot.load_image_used_basenames(image_paths)
    quote_hash = bot.quote_text_hash("Good quote.")

    assert bot.confirmed_regular_emergency_representation_is_complete(
        post_id="950001",
        post_epoch=1_800_000_000,
        quote_hash=quote_hash,
        image_basename="t01.jpg",
        lines_used=reloaded_lines,
        images_used=reloaded_images,
        state=reloaded_state,
        main_post_attempt=pending_receipts[0]["source_attempt"],
    )
    assert reloaded_state["next_quote_post_epoch"] > reloaded_state["last_quote_post_epoch"]


def test_regular_emergency_canonical_state_survives_backup_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, *_paths = configure_simple_quote_post(tmp_path, monkeypatch)
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

    with pytest.raises(bot.ConfirmedPostLocalPersistenceError) as caught:
        bot.post_random_quote(lines_used, images_used, state)

    assert type(caught.value) is bot.ConfirmedPostLocalPersistenceError
    assert bot.json_file_matches(bot.STATE_FILE, state) is True
    assert bot.ambiguous_remote_post_is_blocking() is False


def test_regular_emergency_parent_fsync_failure_still_latches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, *_paths = configure_simple_quote_post(tmp_path, monkeypatch)
    monkeypatch.setattr(
        bot,
        "promote_main_post_attempt_to_confirmed_pending_schedule",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("receipt failed")),
    )
    original_fsync_parent_dir = bot.fsync_parent_dir

    def fail_state_parent_fsync(path: Path, *, strict: bool = False) -> None:
        if path == bot.STATE_FILE:
            raise OSError("state parent fsync failed")
        original_fsync_parent_dir(path, strict=strict)

    monkeypatch.setattr(bot, "fsync_parent_dir", fail_state_parent_fsync)

    with pytest.raises(bot.UnrecoverableConfirmedPostPersistenceError):
        bot.post_random_quote(lines_used, images_used, state)

    assert bot.json_file_matches(bot.STATE_FILE, state) is True
    assert bot.ambiguous_remote_post_is_blocking() is True
    marker = json.loads(bot.AMBIGUOUS_POST_OUTCOME_FILE.read_text(encoding="utf-8"))
    assert "state" in marker["failure_components"]


@pytest.mark.parametrize(
    "failed_components",
    [
        {"quote_history"},
        {"image_history"},
        {"state"},
        {"quote_history", "image_history"},
        {"quote_history", "state"},
        {"image_history", "state"},
        {"quote_history", "image_history", "state"},
    ],
)
def test_receipt_write_failure_emergency_persistence_attempts_all_components(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failed_components: set[str],
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, _receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    attempts: list[str] = []
    monkeypatch.setattr(
        bot,
        "promote_main_post_attempt_to_confirmed_pending_schedule",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("receipt failed")),
    )

    def maybe_fail(name: str) -> None:
        attempts.append(name)
        if name in failed_components:
            raise OSError(f"{name} failed")

    monkeypatch.setattr(bot, "save_quote_used_hashes", lambda *args, **kwargs: maybe_fail("quote_history"))
    monkeypatch.setattr(bot, "save_image_used_basenames", lambda *args, **kwargs: maybe_fail("image_history"))
    monkeypatch.setattr(bot, "save_state", lambda *args, **kwargs: maybe_fail("state"))

    with pytest.raises(bot.UnrecoverableConfirmedPostPersistenceError) as excinfo:
        bot.post_random_quote(lines_used, images_used, state)

    assert attempts == ["quote_history", "image_history", "state"]
    assert bot.ambiguous_remote_post_is_blocking() is True
    marker = json.loads(bot.AMBIGUOUS_POST_OUTCOME_FILE.read_text(encoding="utf-8"))
    assert marker["outcome"] == "confirmed_remote_post_local_persistence_failed"
    assert marker["lane"] == "quote_image"
    assert marker["post_id"] == "950001"
    assert marker["failure_components"] == sorted({"regular_post_receipt", *failed_components})
    for name in failed_components:
        assert name in str(excinfo.value)
    assert state["next_quote_post_epoch"] > state["last_quote_post_epoch"]


def test_regular_total_persistence_loss_latches_when_marker_write_also_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_atomic_write = bot.atomic_write_json
    original_create_post = bot.create_post
    lines_used, images_used, state, *_paths = configure_simple_quote_post(tmp_path, monkeypatch)
    remote_calls = 0

    def confirmed_create(**kwargs: object) -> dict:
        nonlocal remote_calls
        remote_calls += 1
        return mock_confirmed_main_post(
            kwargs,
            {"data": {"id": "950001"}},
        )

    monkeypatch.setattr(bot, "create_post", confirmed_create)
    monkeypatch.setattr(
        bot,
        "promote_main_post_attempt_to_confirmed_pending_schedule",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("receipt failed")),
    )
    monkeypatch.setattr(
        bot,
        "save_quote_used_hashes",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("quote history failed")),
    )
    monkeypatch.setattr(
        bot,
        "save_image_used_basenames",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("image history failed")),
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
    guard_token = object()
    ended_guards: list[object | None] = []
    monkeypatch.setattr(
        bot,
        "begin_confirmed_post_sigint_deferral",
        lambda: guard_token,
    )
    monkeypatch.setattr(
        bot,
        "end_confirmed_post_sigint_deferral",
        lambda guard: ended_guards.append(guard),
    )

    with pytest.raises(bot.UnrecoverableConfirmedPostPersistenceError):
        bot.post_random_quote(lines_used, images_used, state)

    assert remote_calls == 1
    assert bot.ambiguous_remote_post_is_blocking() is True
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    assert ended_guards == [guard_token]
    boundary_calls = 0

    def unexpected_boundary(*_args: object, **_kwargs: object) -> dict:
        nonlocal boundary_calls
        boundary_calls += 1
        pytest.fail("remote boundary must not be reached after the safety latch")

    install_receipt_bound_x_request_stub(monkeypatch, unexpected_boundary)
    with pytest.raises(bot.AmbiguousRemotePostOutcome, match="in-process remote-write safety latch"):
        original_create_post("must not be sent")
    assert remote_calls == 1
    assert boundary_calls == 0


def test_confirmed_regular_post_fallback_helper_failure_latches(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, *_paths = configure_simple_quote_post(tmp_path, monkeypatch)
    monkeypatch.setattr(
        bot,
        "promote_main_post_attempt_to_confirmed_pending_schedule",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("receipt failed")),
    )
    monkeypatch.setattr(
        bot,
        "apply_state_fields",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("schedule update failed")),
    )

    with pytest.raises(bot.UnrecoverableConfirmedPostPersistenceError):
        bot.post_random_quote(lines_used, images_used, state)

    marker = json.loads(bot.AMBIGUOUS_POST_OUTCOME_FILE.read_text(encoding="utf-8"))
    assert "quote_schedule_state" in marker["failure_components"]
    assert "meme_schedule_state" in marker["failure_components"]
    assert bot.ambiguous_remote_post_is_blocking() is True


@pytest.mark.parametrize("helper", ["cache_tweet", "record_recent_own_post"])
def test_regular_post_helper_failure_after_confirmation_leaves_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    helper: str,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    monkeypatch.setattr(bot, helper, lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError(f"{helper} failed")))

    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_random_quote(lines_used, images_used, state)

    assert receipt_file.exists()
    assert json.loads(receipt_file.read_text(encoding="utf-8"))["post_id"] == "950001"
    assert state["next_quote_post_epoch"] > state["last_quote_post_epoch"]


def test_regular_post_invalid_non_numeric_post_id_restores_histories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, _receipt_file, _lines_file = configure_simple_quote_post(
        tmp_path,
        monkeypatch,
        create_response={"data": {"id": "banana"}},
    )

    with pytest.raises(RuntimeError, match="valid post id"):
        bot.post_random_quote(lines_used, images_used, state)

    assert lines_used == set()
    assert images_used == set()
    assert "last_main_post_id" not in state


@pytest.mark.parametrize(
    ("failure", "expected_exception"),
    [
        ("global_image", bot.GlobalImageUnavailable),
        ("unsafe_image_migration", bot.UnsafeImageHistoryMigration),
        ("unexpected_image", ValueError),
        ("upload", OSError),
        ("create", OSError),
        ("invalid_post_id", RuntimeError),
    ],
)
def test_pre_confirmation_failures_restore_histories_after_quote_cycle_reset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    expected_exception: type[Exception],
) -> None:
    lines_used = {"old-line"}
    images_used = {"old-image"}
    state: dict = {}
    monkeypatch.setattr(bot, "reconcile_main_post_receipts", lambda *args, **kwargs: {"regular": False, "meme": False})
    monkeypatch.setattr(bot, "quote_used_history_has_legacy_indices", lambda used: False)

    def reset_then_quote(used: set, *, excluded_quote_hashes: set[str] | None = None, allow_cycle_reset: bool = True) -> dict:
        assert allow_cycle_reset is True
        used.clear()
        return {"line_no": 0, "quote_hash": bot.quote_text_hash("Good quote."), "text": "Good quote.", "analysis": {}}

    monkeypatch.setattr(bot, "choose_unused_line_candidate", reset_then_quote)

    if failure == "global_image":
        monkeypatch.setattr(bot, "choose_matched_unused_image", lambda *args, **kwargs: (_ for _ in ()).throw(bot.GlobalImageUnavailable("no images")))
    elif failure == "unsafe_image_migration":
        monkeypatch.setattr(bot, "choose_matched_unused_image", lambda *args, **kwargs: (_ for _ in ()).throw(bot.UnsafeImageHistoryMigration("unsafe")))
    elif failure == "unexpected_image":
        monkeypatch.setattr(bot, "choose_matched_unused_image", lambda *args, **kwargs: (_ for _ in ()).throw(ValueError("scoring failed")))
    else:
        monkeypatch.setattr(
            bot,
            "choose_matched_unused_image",
            lambda *args, **kwargs: {"image_no": 0, "path": "image.jpg", "basename": "image.jpg", "score": 1.0},
        )
        if failure == "upload":
            monkeypatch.setattr(bot, "upload_media", lambda path, **_kwargs: (_ for _ in ()).throw(OSError("upload failed")))
        else:
            monkeypatch.setattr(bot, "upload_media", lambda path, **_kwargs: "media-1")
            monkeypatch.setattr(
                bot,
                "handoff_confirmed_media_upload_to_main_attempt",
                lambda _attempt, _authority: None,
            )
            if failure == "create":
                monkeypatch.setattr(bot, "create_post", lambda **kwargs: (_ for _ in ()).throw(OSError("create failed")))
            elif failure == "invalid_post_id":
                monkeypatch.setattr(
                    bot,
                    "create_post",
                    lambda **kwargs: mock_confirmed_main_post(
                        kwargs,
                        {"data": {"id": "banana"}},
                    ),
                )

    with pytest.raises(expected_exception):
        bot.post_random_quote(lines_used, images_used, state)

    assert lines_used == {"old-line"}
    assert images_used == {"old-image"}
    assert "last_main_post_id" not in state
