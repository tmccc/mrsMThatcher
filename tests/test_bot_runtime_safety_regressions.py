"""Regression tests for bot runtime safety."""

from __future__ import annotations

import json
import multiprocessing
import os
from pathlib import Path

import pytest

from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import (
    isolate_regular_post_receipt,
    configure_simple_quote_post,
    valid_regular_receipt,
)
from tests.helpers.reply_fixtures import unit_sending_reply_receipt


pytestmark = pytest.mark.allow_loopback_network


@pytest.mark.parametrize(
    "control_text",
    [
        json.dumps({"disable_all": True}),
        json.dumps({"pause_all": True}),
        "{",
    ],
)
def test_global_pause_leaves_startup_main_receipt_untouched(
    control_text: str,
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
        _lines_file,
    ) = configure_simple_quote_post(tmp_path, monkeypatch)
    receipt = valid_regular_receipt(
        quote_post_epoch=1_784_680_936,
        next_quote_post_epoch=1_784_689_435,
    )
    bot.atomic_write_json(receipt_file, receipt)
    receipt_bytes = receipt_file.read_bytes()
    control_file = tmp_path / "mrsMThatcher.control.json"
    control_file.write_text(control_text, encoding="utf-8")
    monkeypatch.setattr(bot, "CONTROL_FILE", control_file)
    monkeypatch.setattr(
        bot,
        "_CONTROL_CACHE",
        {"signature": None, "data": {}, "has_valid": False, "failure_signature": None},
    )
    monkeypatch.setattr(
        bot,
        "reconcile_main_post_receipts",
        lambda *_args, **_kwargs: pytest.fail(
            "global maintenance pause must precede receipt reconciliation"
        ),
    )

    status = bot.reconcile_startup_main_post_receipts(
        lines_used,
        images_used,
        state,
        1_784_708_283,
    )

    assert status == {"regular": False, "meme": False}
    assert receipt_file.read_bytes() == receipt_bytes


def test_false_global_pause_preserves_startup_receipt_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    control_file = tmp_path / "mrsMThatcher.control.json"
    control_file.write_text(json.dumps({"disable_all": False}), encoding="utf-8")
    monkeypatch.setattr(bot, "CONTROL_FILE", control_file)
    monkeypatch.setattr(
        bot,
        "_CONTROL_CACHE",
        {"signature": None, "data": {}, "has_valid": False, "failure_signature": None},
    )
    expected = {"regular": True, "meme": False}
    monkeypatch.setattr(
        bot,
        "reconcile_main_post_receipts",
        lambda *_args, **_kwargs: expected,
    )

    assert bot.reconcile_startup_main_post_receipts(
        set(),
        set(),
        {},
        1_784_708_283,
    ) is expected


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_fresh_startup_with_uncertain_main_attempt_idles_without_remote_action(
    lane: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "require_production_bootstrap", lambda: None)
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "reconcile_runtime_historical_context_state", lambda: None)
    monkeypatch.setattr(bot, "glob", lambda _pattern: [])
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(bot, "validate_original_editorial_shadow_startup", lambda: None)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda _lines: set())
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda _paths: set())
    monkeypatch.setattr(bot, "current_image_paths", lambda: [])
    state = {"next_quote_post_epoch": 1_800_007_200}
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "seed_recent_own_post_ids_from_cache", lambda _state: None)
    monkeypatch.setattr(bot, "save_state", lambda _state, **_kwargs: None)
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "global_remote_writes_paused", lambda: False)
    monkeypatch.setattr(
        bot,
        "scheduler_epoch_from_state",
        lambda state_arg, key, current: (
            int(state_arg.get(key, current) or current),
            False,
        ),
    )

    if lane == "quote_image":
        attempt = bot.build_main_post_attempt(
            lane=lane,
            text="Good quote.",
            media_ids=["media-1"],
            made_with_ai=False,
            selected_identity={
                "quote_hash": bot.quote_text_hash("Good quote."),
                "line_no": 0,
                "source_line_number": 1,
                "image_basename": "t01.jpg",
                "image_no": 0,
            },
            recovery_plan={
                "quote_delay_seconds": bot.POST_SLEEP_MIN,
                "meme_delay_seconds": None,
                "meme_scheduling_enabled": False,
                "meme_trigger_after_hour": int(bot.MEME_TRIGGER_AFTER_HOUR),
                "meme_schedule_version": int(bot.MEME_SCHEDULE_VERSION),
                "schedule_timezone": bot.MAIN_POST_SCHEDULE_TIMEZONE,
                "meme_schedule_before": bot.bound_meme_schedule_state(
                    {},
                    schedule_timezone=bot.MAIN_POST_SCHEDULE_TIMEZONE,
                ),
                "quote_history_after": [
                    bot.quote_text_hash("Good quote."),
                ],
                "image_history_after": ["t01.jpg"],
            },
        )
        receipt_path = bot.REGULAR_POST_RECEIPT_FILE
    else:
        attempt = bot.build_main_post_attempt(
            lane=lane,
            text=bot.MEME_POST_TEXT,
            media_ids=["media-1"],
            made_with_ai=False,
            selected_identity={"meme_basename": "001_meme.png"},
            recovery_plan={
                "next_schedule_mode": "fallback",
                "meme_schedule_version": int(bot.MEME_SCHEDULE_VERSION),
                "fallback_hour": int(bot.MEME_FALLBACK_HOUR),
                "fallback_minute": int(bot.MEME_FALLBACK_MINUTE),
                "image_summary": "Unit meme image.",
                "schedule_timezone": bot.MAIN_POST_SCHEDULE_TIMEZONE,
            },
        )
        receipt_path = bot.MEME_POST_RECEIPT_FILE
    attempting = {**attempt, "lifecycle_state": "attempting"}
    bot.atomic_write_json(receipt_path, attempting, durable=True)
    receipt_bytes = receipt_path.read_bytes()

    remote_action = tmp_path / "unexpected-remote-action"

    def forbidden_remote_action(*_args: object, **_kwargs: object) -> object:
        remote_action.write_text("reached", encoding="utf-8")
        raise AssertionError("startup ambiguity pause must precede remote lanes")

    for name in (
        "x_request",
        "upload_media",
        "create_post",
        "post_random_quote",
        "post_next_meme",
        "run_reply_lane_checks_for_tick",
        "safely_process_due_historical_context_obligations",
    ):
        monkeypatch.setattr(bot, name, forbidden_remote_action)
    monkeypatch.setattr(bot, "sleep", lambda _seconds: os._exit(81))

    context = multiprocessing.get_context("fork")
    for _restart in range(2):
        process = context.Process(target=bot.main)
        process.start()
        process.join(timeout=10)
        assert process.exitcode == 81
        assert not remote_action.exists()
        assert receipt_path.read_bytes() == receipt_bytes


def test_main_global_pause_stops_before_every_remote_lane(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    receipt_file = tmp_path / "regular_post_receipt.json"
    receipt_file.write_bytes(b'{"durable":"unchanged"}\n')
    receipt_bytes = receipt_file.read_bytes()
    control_file = tmp_path / "mrsMThatcher.control.json"
    control_file.write_text(json.dumps({"disable_all": True}), encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "REGULAR_POST_RECEIPT_FILE", receipt_file)
    monkeypatch.setattr(bot, "CONTROL_FILE", control_file)
    monkeypatch.setattr(
        bot,
        "_CONTROL_CACHE",
        {"signature": None, "data": {}, "has_valid": False, "failure_signature": None},
    )
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setattr(bot, "block_if_ambiguous_remote_post", lambda: None)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "glob", lambda _pattern: [])
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(bot, "validate_original_editorial_shadow_startup", lambda: None)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda _lines: set())
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda _paths: set())
    monkeypatch.setattr(bot, "current_image_paths", lambda: [])
    state = {"next_quote_post_epoch": 0}
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "seed_recent_own_post_ids_from_cache", lambda _state: None)
    monkeypatch.setattr(bot, "save_state", lambda _state, **_kwargs: None)
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_784_708_283)
    monkeypatch.setattr(
        bot,
        "scheduler_epoch_from_state",
        lambda _state, _key, current: (current, False),
    )

    def remote_lane_reached(*_args, **_kwargs):
        pytest.fail("global maintenance pause must block every remote lane")

    monkeypatch.setattr(bot, "reconcile_main_post_receipts", remote_lane_reached)
    monkeypatch.setattr(bot, "run_reply_lane_checks_for_tick", remote_lane_reached)
    monkeypatch.setattr(bot, "post_random_quote", remote_lane_reached)
    monkeypatch.setattr(bot, "post_next_meme", remote_lane_reached)
    monkeypatch.setattr(bot, "create_post", remote_lane_reached)
    monkeypatch.setattr(bot, "upload_media", remote_lane_reached)
    monkeypatch.setattr(bot, "x_request", remote_lane_reached)
    monkeypatch.setattr(bot, "openai_responses_reply_call", remote_lane_reached)

    class MaintenanceTickComplete(Exception):
        pass

    monkeypatch.setattr(
        bot,
        "sleep",
        lambda seconds: (_ for _ in ()).throw(MaintenanceTickComplete),
    )

    with pytest.raises(MaintenanceTickComplete):
        bot.main()

    assert receipt_file.read_bytes() == receipt_bytes


def test_main_total_persistence_loss_latch_stops_later_remote_lanes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setattr(bot, "reconcile_runtime_historical_context_state", lambda: None)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "glob", lambda _pattern: [])
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot, "list_meme_candidates", lambda: [])
    monkeypatch.setattr(bot, "validate_original_editorial_shadow_startup", lambda: None)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda _lines: set())
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda _paths: set())
    monkeypatch.setattr(bot, "current_image_paths", lambda: [])
    state = {
        "next_quote_post_epoch": 1,
        "next_meme_post_epoch": 1,
        "last_quote_post_epoch": 0,
    }
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "reconcile_startup_main_post_receipts", lambda *_args: None)
    monkeypatch.setattr(bot, "seed_recent_own_post_ids_from_cache", lambda _state: None)
    monkeypatch.setattr(bot, "save_state", lambda _state, **_kwargs: None)
    monkeypatch.setattr(bot, "ensure_meme_schedule_initialized", lambda _state: None)
    clock_must_not_run = False

    def controlled_clock() -> int:
        if clock_must_not_run:
            pytest.fail("the safety latch must be checked before the next clock read")
        return 1_784_708_283

    monkeypatch.setattr(bot, "now_epoch", controlled_clock)
    monkeypatch.setattr(
        bot,
        "scheduler_epoch_from_state",
        lambda state_arg, key, current: (int(state_arg.get(key, current)), False),
    )

    context_ticks = 0
    reply_ticks = 0
    quote_attempts = 0

    def context_tick(*_args: object, **_kwargs: object) -> list[dict]:
        nonlocal context_ticks
        context_ticks += 1
        return []

    def reply_tick(
        _state: dict,
        _current: int,
    ) -> tuple[int, int]:
        nonlocal reply_ticks
        reply_ticks += 1
        return 0, 0

    def catastrophic_quote(*_args: object, **_kwargs: object) -> None:
        nonlocal clock_must_not_run, quote_attempts
        quote_attempts += 1
        bot.latch_confirmed_post_persistence_failure(
            lane="quote_image",
            post_id="950001",
            failure_components=["regular_post_receipt", "state"],
        )
        clock_must_not_run = True
        raise bot.UnrecoverableConfirmedPostPersistenceError("confirmed and unrepresented")

    monkeypatch.setattr(bot, "safely_process_due_historical_context_obligations", context_tick)
    monkeypatch.setattr(bot, "run_reply_lane_checks_for_tick", reply_tick)
    monkeypatch.setattr(bot, "post_random_quote", catastrophic_quote)
    monkeypatch.setattr(
        bot,
        "schedule_next_quote_post",
        lambda *_args, **_kwargs: pytest.fail(
            "an unrecoverable confirmed post must never be scheduled for retry"
        ),
    )
    monkeypatch.setattr(
        bot,
        "post_next_meme",
        lambda _state: pytest.fail("meme lane must not run after the safety latch"),
    )
    monkeypatch.setattr(
        bot,
        "atomic_write_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("marker failed")),
    )

    class SafetyBarrierTickComplete(Exception):
        pass

    monkeypatch.setattr(
        bot,
        "sleep",
        lambda seconds: (_ for _ in ()).throw(SafetyBarrierTickComplete),
    )

    with pytest.raises(SafetyBarrierTickComplete):
        bot.main()

    assert quote_attempts == 1
    assert context_ticks == 1
    assert reply_ticks == 1
    assert bot.ambiguous_remote_post_is_blocking() is True
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()


@pytest.mark.parametrize(
    ("lane", "failure_kind"),
    [
        ("quote", "ambiguous"),
        ("meme", "ambiguous"),
        ("meme", "unrecoverable"),
    ],
)
def test_main_routes_remote_safety_failures_without_error_retry_bookkeeping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    lane: str,
    failure_kind: str,
) -> None:
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setattr(bot, "reconcile_runtime_historical_context_state", lambda: None)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "glob", lambda _pattern: [])
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot, "list_meme_candidates", lambda: [])
    monkeypatch.setattr(bot, "validate_original_editorial_shadow_startup", lambda: None)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda _lines: set())
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda _paths: set())
    monkeypatch.setattr(bot, "current_image_paths", lambda: [])
    current = 1_784_708_283
    state = {
        "next_quote_post_epoch": 1 if lane == "quote" else current + 3600,
        "next_meme_post_epoch": 1,
        "last_quote_post_epoch": 0,
    }
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "reconcile_startup_main_post_receipts", lambda *_args: None)
    monkeypatch.setattr(bot, "seed_recent_own_post_ids_from_cache", lambda _state: None)
    monkeypatch.setattr(bot, "save_state", lambda _state, **_kwargs: None)
    monkeypatch.setattr(bot, "ensure_meme_schedule_initialized", lambda _state: None)
    monkeypatch.setattr(bot, "now_epoch", lambda: current)
    monkeypatch.setattr(bot, "global_remote_writes_paused", lambda: False)
    monkeypatch.setattr(
        bot,
        "scheduler_epoch_from_state",
        lambda state_arg, key, current: (int(state_arg.get(key, current)), False),
    )
    monkeypatch.setattr(
        bot,
        "safely_process_due_historical_context_obligations",
        lambda *_args, **_kwargs: [],
    )
    monkeypatch.setattr(
        bot,
        "run_reply_lane_checks_for_tick",
        lambda _state, _current: (0, 0),
    )
    monkeypatch.setattr(
        bot,
        "record_api_error",
        lambda *_args, **_kwargs: pytest.fail(
            "remote safety exceptions must not enter API-error bookkeeping"
        ),
    )
    monkeypatch.setattr(
        bot,
        "schedule_next_quote_post",
        lambda *_args, **_kwargs: pytest.fail(
            "remote safety exceptions must not schedule a quote retry"
        ),
    )
    monkeypatch.setattr(
        bot,
        "set_meme_delay_schedule",
        lambda *_args, **_kwargs: pytest.fail(
            "remote safety exceptions must not schedule a meme retry"
        ),
    )
    monkeypatch.setattr(
        bot,
        "atomic_write_json",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("marker failed")),
    )

    def raise_safety_failure() -> None:
        if failure_kind == "ambiguous":
            bot.record_ambiguous_remote_post({"text": "confirmed or ambiguous"})
            raise bot.AmbiguousRemotePostOutcome("ambiguous", service="x")
        bot.latch_confirmed_post_persistence_failure(
            lane="daily_meme",
            post_id="970001",
            failure_components=["meme_post_receipt", "state"],
        )
        raise bot.UnrecoverableConfirmedPostPersistenceError(
            "confirmed and unrepresented"
        )

    if lane == "quote":
        monkeypatch.setattr(
            bot,
            "post_random_quote",
            lambda *_args, **_kwargs: raise_safety_failure(),
        )
        monkeypatch.setattr(
            bot,
            "post_next_meme",
            lambda _state: pytest.fail("meme lane must not run through the safety latch"),
        )
    else:
        monkeypatch.setattr(
            bot,
            "post_random_quote",
            lambda *_args, **_kwargs: pytest.fail("future quote lane must not run"),
        )
        monkeypatch.setattr(bot, "post_next_meme", lambda _state: raise_safety_failure())

    class SafetyBarrierTickComplete(Exception):
        pass

    monkeypatch.setattr(
        bot,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(SafetyBarrierTickComplete),
    )

    with pytest.raises(SafetyBarrierTickComplete):
        bot.main()

    assert bot.ambiguous_remote_post_is_blocking() is True
    assert not bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()


def test_confirmed_persistence_marker_allows_controlled_startup_before_idle_barrier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bot.latch_confirmed_post_persistence_failure(
        lane="quote_image",
        post_id="950001",
        failure_components=["regular_post_receipt", "state"],
    )
    assert bot.AMBIGUOUS_POST_OUTCOME_FILE.exists()
    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", False)
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    order: list[str] = []
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: order.append("lock"))

    class StartupReachedBarrier(RuntimeError):
        pass

    def stop_after_startup_reconciliation() -> None:
        order.append("context_reconcile")
        raise StartupReachedBarrier

    monkeypatch.setattr(
        bot,
        "reconcile_runtime_historical_context_state",
        stop_after_startup_reconciliation,
    )

    with pytest.raises(StartupReachedBarrier):
        bot.main()
    assert order == ["lock", "context_reconcile"]
    with pytest.raises(
        bot.AmbiguousRemotePostOutcome,
        match="remote-write safety barrier",
    ):
        bot.block_if_ambiguous_remote_post()


def test_test_post_quote_reports_confirmed_local_failure_distinctly(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: {})
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda lines: set())
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda images: set())
    monkeypatch.setattr(bot, "current_image_paths", lambda: [])
    monkeypatch.setattr(
        bot,
        "post_random_quote",
        lambda *args, **kwargs: (_ for _ in ()).throw(bot.ConfirmedPostLocalPersistenceError("confirmed")),
    )
    saved: list[dict] = []
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: saved.append(dict(state)))

    assert bot.run_test_post_quote() == 3
    assert saved == [{}]


def test_one_shot_quote_unrecoverable_failure_cannot_exit_on_memory_latch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: {})
    monkeypatch.setattr(bot, "lane_paused", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda _lines: set())
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda _images: set())
    monkeypatch.setattr(bot, "current_image_paths", lambda: [])
    monkeypatch.setattr(
        bot,
        "post_random_quote",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            bot.UnrecoverableConfirmedPostPersistenceError("confirmed")
        ),
    )
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda *_args, **_kwargs: pytest.fail(
            "unrecoverable confirmed post must not use ordinary exit bookkeeping"
        ),
    )
    lanes: list[str] = []

    class ProcessHeld(Exception):
        pass

    def hold(*, lane: str) -> None:
        lanes.append(lane)
        raise ProcessHeld

    monkeypatch.setattr(bot, "wait_for_durable_barrier_before_one_shot_exit", hold)

    with pytest.raises(ProcessHeld):
        bot.run_test_post_quote()

    assert lanes == ["quote_image"]


def test_test_post_quote_migrates_old_meme_schedule_before_post_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    state = {
        "meme_schedule_version": 1,
        "next_meme_post_epoch": 1_800_010_000,
        "next_meme_schedule_mode": "fallback",
        "next_meme_schedule_date": bot.epoch_date_str(1_800_010_000),
        "meme_anchor_quote_post_epoch": 0,
    }
    seen_states: list[dict] = []
    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda lines: set())
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda images: set())
    monkeypatch.setattr(bot, "current_image_paths", lambda: [])
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)
    monkeypatch.setattr(bot, "post_random_quote", lambda lines_used, images_used, state: seen_states.append(json.loads(json.dumps(state))))

    assert bot.run_test_post_quote() == 0
    assert seen_states[0]["meme_schedule_version"] == bot.MEME_SCHEDULE_VERSION
    assert seen_states[0]["next_meme_schedule_mode"] == "fallback_migrated"


def test_test_post_quote_preserves_current_meme_schedule_preflight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    state = {
        "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
        "next_meme_post_epoch": 1_800_010_000,
        "next_meme_schedule_mode": "fallback",
        "next_meme_schedule_date": bot.epoch_date_str(1_800_010_000),
        "meme_anchor_quote_post_epoch": 0,
    }
    before = json.loads(json.dumps(state))
    seen_states: list[dict] = []
    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda lines: set())
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda images: set())
    monkeypatch.setattr(bot, "current_image_paths", lambda: [])
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)
    monkeypatch.setattr(bot, "post_random_quote", lambda lines_used, images_used, state: seen_states.append(json.loads(json.dumps(state))))

    assert bot.run_test_post_quote() == 0
    assert seen_states[0] == before


def test_test_post_quote_load_failure_happens_before_post_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: (_ for _ in ()).throw(RuntimeError("future meme schedule version")))
    monkeypatch.setattr(bot, "post_random_quote", lambda *args, **kwargs: pytest.fail("post_random_quote should not be called"))

    with pytest.raises(RuntimeError, match="future meme schedule version"):
        bot.run_test_post_quote()


def test_test_post_quote_receipt_replay_does_not_create_second_post(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    lines_used, images_used, state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    bot.atomic_write_json(receipt_file, valid_regular_receipt())
    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda lines: lines_used)
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda images: images_used)
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: pytest.fail("create_post should not be called after receipt replay"))

    assert bot.run_test_post_quote() == 0
    assert not receipt_file.exists()
    assert state["last_main_post_id"] == "950001"


def test_test_post_meme_reports_confirmed_local_failure_distinctly(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: {})
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(
        bot,
        "post_next_meme",
        lambda *args, **kwargs: (_ for _ in ()).throw(bot.ConfirmedPostLocalPersistenceError("confirmed")),
    )
    saved: list[dict] = []
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: saved.append(dict(state)))

    assert bot.run_test_post_meme() == 3
    assert saved == [{}]


def test_one_shot_meme_ambiguity_cannot_exit_on_memory_latch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: {})
    monkeypatch.setattr(bot, "lane_paused", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(
        bot,
        "post_next_meme",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            bot.AmbiguousRemotePostOutcome("ambiguous", service="x")
        ),
    )
    monkeypatch.setattr(
        bot,
        "record_api_error",
        lambda *_args, **_kwargs: pytest.fail(
            "ambiguous outcome must not enter ordinary API-error bookkeeping"
        ),
    )
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda *_args, **_kwargs: pytest.fail(
            "ambiguous outcome must not use ordinary exit bookkeeping"
        ),
    )
    lanes: list[str] = []

    class ProcessHeld(Exception):
        pass

    def hold(*, lane: str) -> None:
        lanes.append(lane)
        raise ProcessHeld

    monkeypatch.setattr(bot, "wait_for_durable_barrier_before_one_shot_exit", hold)

    with pytest.raises(ProcessHeld):
        bot.run_test_post_meme()

    assert lanes == ["daily_meme"]


def test_one_shot_memory_only_barrier_waits_for_durable_marker(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(bot, "_AMBIGUOUS_REMOTE_POST_SEEN", True)

    class ProcessHeld(Exception):
        pass

    monkeypatch.setattr(
        bot,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(ProcessHeld),
    )

    with pytest.raises(ProcessHeld):
        bot.wait_for_durable_barrier_before_one_shot_exit(lane="quote_image")


def test_test_main_tick_stops_after_reply_safety_barrier(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = bot.default_state()
    sending = unit_sending_reply_receipt()
    waited: list[str] = []

    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "now_epoch", lambda: 2_000_000_000)
    monkeypatch.setattr(
        bot,
        "scheduler_epoch_from_state",
        lambda state_arg, key, current: (int(state_arg.get(key, 0) or 0), False),
    )

    def trigger_reply_barrier(*_args: object, **_kwargs: object) -> tuple[int, int]:
        bot.write_sending_reply_receipt(sending)
        return 0, 0

    monkeypatch.setattr(bot, "run_reply_lane_checks_for_tick", trigger_reply_barrier)
    monkeypatch.setattr(
        bot,
        "wait_for_durable_barrier_before_one_shot_exit",
        lambda *, lane: waited.append(lane),
    )
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda *_args, **_kwargs: pytest.fail(
            "one-shot tick must not perform ordinary completion save after barrier"
        ),
    )

    assert bot.run_test_main_tick() == 0
    assert waited == ["production_reply_tick"]
    assert bot.load_confirmed_reply_receipt() == ("sending", sending)


@pytest.mark.parametrize(
    ("priority", "first_lane", "failure_type"),
    [
        ("normal", "normal", bot.AmbiguousRemotePostOutcome),
        (
            "normal",
            "normal",
            bot.UnrecoverableConfirmedReplyPersistenceError,
        ),
        ("quote", "quote_tweet", bot.AmbiguousRemotePostOutcome),
        (
            "quote",
            "quote_tweet",
            bot.UnrecoverableConfirmedReplyPersistenceError,
        ),
    ],
)
def test_production_reply_tick_stops_sibling_lane_on_safety_failure(
    monkeypatch: pytest.MonkeyPatch,
    priority: str,
    first_lane: str,
    failure_type: type[BaseException],
) -> None:
    state = bot.default_state()
    state.update(
        {
            "next_reply_lane_priority": priority,
            "last_reply_epoch": 0,
            "last_reply_check_epoch": 0,
            "last_quote_tweet_check_epoch": 0,
        }
    )
    sending = unit_sending_reply_receipt(
        lane="quote_tweet" if first_lane == "quote_tweet" else "mention",
    )
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "ENABLE_QUOTE_TWEET_CHECKS", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "REPLY_CHECK_EVERY_SECONDS", 1)
    monkeypatch.setattr(bot, "QUOTE_CHECK_EVERY_SECONDS", 1)
    monkeypatch.setattr(
        bot,
        "scheduler_epoch_from_state",
        lambda state_arg, key, current: (int(state_arg.get(key, 0) or 0), False),
    )
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda *_args, **_kwargs: pytest.fail(
            "reply safety failure must not consume or save a scheduler interval"
        ),
    )

    def safety_failure(_state: dict) -> str:
        bot.write_sending_reply_receipt(sending)
        if issubclass(failure_type, bot.ApiError):
            raise failure_type("reply safety failure", service="x")
        raise failure_type("reply safety failure")

    def later_lane(_state: dict) -> str:
        pytest.fail("the sibling reply lane must not run after a safety failure")

    monkeypatch.setattr(
        bot,
        "maybe_reply_to_mentions",
        safety_failure if first_lane == "normal" else later_lane,
    )
    monkeypatch.setattr(
        bot,
        "maybe_reply_to_quote_tweets",
        safety_failure if first_lane == "quote_tweet" else later_lane,
    )

    assert bot.run_reply_lane_checks_for_tick(state, 100) == (0, 0)
    assert bot.ambiguous_remote_post_is_blocking() is True
    assert bot.load_confirmed_reply_receipt() == ("sending", sending)


@pytest.mark.parametrize(
    ("priority", "first_lane", "failure_type"),
    [
        ("normal", "normal", bot.AmbiguousRemotePostOutcome),
        (
            "normal",
            "normal",
            bot.UnrecoverableConfirmedReplyPersistenceError,
        ),
        ("quote", "quote_tweet", bot.AmbiguousRemotePostOutcome),
        (
            "quote",
            "quote_tweet",
            bot.UnrecoverableConfirmedReplyPersistenceError,
        ),
    ],
)
def test_main_reply_safety_failure_reaches_top_of_loop_barrier(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    priority: str,
    first_lane: str,
    failure_type: type[BaseException],
) -> None:
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Good quote.\n", encoding="utf-8")
    current = 1_784_708_283
    state = bot.default_state()
    state.update(
        {
            "next_reply_lane_priority": priority,
            "last_reply_epoch": 0,
            "last_reply_check_epoch": 0,
            "last_quote_tweet_check_epoch": 0,
            "next_quote_post_epoch": 0,
            "next_meme_post_epoch": 0,
            "last_quote_post_epoch": 0,
        }
    )
    sending = unit_sending_reply_receipt(
        lane="quote_tweet" if first_lane == "quote_tweet" else "mention",
    )
    clock_must_not_run = False
    context_ticks = 0
    reply_attempts = 0

    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setattr(bot, "reconcile_runtime_historical_context_state", lambda: None)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "glob", lambda _pattern: [])
    monkeypatch.setattr(bot, "ENABLE_AUTO_REPLIES", True)
    monkeypatch.setattr(bot, "ENABLE_QUOTE_TWEET_CHECKS", True)
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot, "MIN_SECONDS_BETWEEN_REPLIES", 0)
    monkeypatch.setattr(bot, "REPLY_CHECK_EVERY_SECONDS", 1)
    monkeypatch.setattr(bot, "QUOTE_CHECK_EVERY_SECONDS", 1)
    monkeypatch.setattr(bot, "list_meme_candidates", lambda: [])
    monkeypatch.setattr(bot, "validate_original_editorial_shadow_startup", lambda: None)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda _lines: set())
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda _paths: set())
    monkeypatch.setattr(bot, "current_image_paths", lambda: [])
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "reconcile_startup_main_post_receipts", lambda *_args: None)
    monkeypatch.setattr(bot, "seed_recent_own_post_ids_from_cache", lambda _state: None)
    monkeypatch.setattr(bot, "save_state", lambda _state, **_kwargs: None)
    monkeypatch.setattr(bot, "ensure_meme_schedule_initialized", lambda _state: None)
    monkeypatch.setattr(bot, "global_remote_writes_paused", lambda: False)
    monkeypatch.setattr(
        bot,
        "scheduler_epoch_from_state",
        lambda state_arg, key, current: (int(state_arg.get(key, 0) or 0), False),
    )

    def controlled_clock() -> int:
        if clock_must_not_run:
            pytest.fail("the top-of-loop barrier must run before another clock read")
        return current

    def context_tick(*_args: object, **_kwargs: object) -> list[dict]:
        nonlocal context_ticks
        context_ticks += 1
        return []

    def safety_failure(_state: dict) -> str:
        nonlocal clock_must_not_run, reply_attempts
        reply_attempts += 1
        bot.write_sending_reply_receipt(sending)
        clock_must_not_run = True
        if issubclass(failure_type, bot.ApiError):
            raise failure_type("reply safety failure", service="x")
        raise failure_type("reply safety failure")

    def later_lane(_state: dict) -> str:
        pytest.fail("the sibling reply lane must not run after a safety failure")

    monkeypatch.setattr(bot, "now_epoch", controlled_clock)
    monkeypatch.setattr(
        bot,
        "safely_process_due_historical_context_obligations",
        context_tick,
    )
    monkeypatch.setattr(
        bot,
        "maybe_reply_to_mentions",
        safety_failure if first_lane == "normal" else later_lane,
    )
    monkeypatch.setattr(
        bot,
        "maybe_reply_to_quote_tweets",
        safety_failure if first_lane == "quote_tweet" else later_lane,
    )
    monkeypatch.setattr(
        bot,
        "post_random_quote",
        lambda *_args, **_kwargs: pytest.fail(
            "main quote lane must not run after a reply safety failure"
        ),
    )
    monkeypatch.setattr(
        bot,
        "post_next_meme",
        lambda _state: pytest.fail(
            "meme lane must not run after a reply safety failure"
        ),
    )

    class SafetyBarrierTickComplete(Exception):
        pass

    monkeypatch.setattr(
        bot,
        "sleep",
        lambda _seconds: (_ for _ in ()).throw(SafetyBarrierTickComplete),
    )

    with pytest.raises(SafetyBarrierTickComplete):
        bot.main()

    assert reply_attempts == 1
    assert context_ticks == 1
    assert bot.load_confirmed_reply_receipt() == ("sending", sending)
    assert bot.ambiguous_remote_post_is_blocking() is True


@pytest.mark.parametrize(
    ("priority", "first_lane", "failure_type", "expected_wait_lane"),
    [
        (
            "normal",
            "normal",
            bot.AmbiguousRemotePostOutcome,
            "normal_reply",
        ),
        (
            "normal",
            "normal",
            bot.UnrecoverableConfirmedReplyPersistenceError,
            "normal_reply",
        ),
        (
            "quote",
            "quote_tweet",
            bot.AmbiguousRemotePostOutcome,
            "quote_tweet_reply",
        ),
        (
            "quote",
            "quote_tweet",
            bot.UnrecoverableConfirmedReplyPersistenceError,
            "quote_tweet_reply",
        ),
    ],
)
def test_test_cycle_reply_safety_failure_stops_later_lane(
    monkeypatch: pytest.MonkeyPatch,
    priority: str,
    first_lane: str,
    failure_type: type[BaseException],
    expected_wait_lane: str,
) -> None:
    state = bot.default_state()
    state["next_reply_lane_priority"] = priority
    sending = unit_sending_reply_receipt(
        lane="quote_tweet" if first_lane == "quote_tweet" else "mention",
    )
    waited: list[str] = []

    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(
        bot,
        "save_state",
        lambda *_args, **_kwargs: pytest.fail(
            "test cycle must not perform a final save after reply safety failure"
        ),
    )
    monkeypatch.setattr(
        bot,
        "wait_for_durable_barrier_before_one_shot_exit",
        lambda *, lane: waited.append(lane),
    )

    def safety_failure(_state: dict) -> str:
        bot.write_sending_reply_receipt(sending)
        if issubclass(failure_type, bot.ApiError):
            raise failure_type("reply safety failure", service="x")
        raise failure_type("reply safety failure")

    def later_lane(_state: dict) -> str:
        pytest.fail("the sibling reply lane must not run after a safety failure")

    monkeypatch.setattr(
        bot,
        "maybe_reply_to_mentions",
        safety_failure if first_lane == "normal" else later_lane,
    )
    monkeypatch.setattr(
        bot,
        "maybe_reply_to_quote_tweets",
        safety_failure if first_lane == "quote_tweet" else later_lane,
    )

    assert bot.run_test_cycle() == 0
    assert waited == [expected_wait_lane]
    assert bot.load_confirmed_reply_receipt() == ("sending", sending)


def test_test_post_meme_migrates_old_meme_schedule_before_post_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    state = {
        "meme_schedule_version": 1,
        "next_meme_post_epoch": 1_800_010_000,
        "next_meme_schedule_mode": "fallback",
        "next_meme_schedule_date": bot.epoch_date_str(1_800_010_000),
        "meme_anchor_quote_post_epoch": 0,
    }
    seen_states: list[dict] = []
    monkeypatch.setenv("MRS_TEST_MODE", "1")
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "lane_paused", lambda *args, **kwargs: False)
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)
    monkeypatch.setattr(bot, "post_next_meme", lambda state: seen_states.append(json.loads(json.dumps(state))))

    assert bot.run_test_post_meme() == 0
    assert seen_states[0]["meme_schedule_version"] == bot.MEME_SCHEDULE_VERSION
    assert seen_states[0]["next_meme_schedule_mode"] == "fallback_migrated"
