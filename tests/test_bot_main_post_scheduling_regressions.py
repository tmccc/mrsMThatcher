"""Regression tests for bot main post scheduling."""

from __future__ import annotations

import copy
import json
import os
import time
from datetime import (
    datetime,
    timedelta,
)
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import (
    isolate_bot_runtime,
    configure_simple_quote_post,
    mock_confirmed_main_post,
    valid_regular_receipt,
    valid_regular_receipt_v2,
    schema_current_main_attempt,
)


pytestmark = pytest.mark.allow_loopback_network


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_pending_schedule_plan_survives_current_configuration_change(
    lane: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempt = schema_current_main_attempt(lane)
    attempt["lifecycle_state"] = "attempting"
    pending = bot.build_confirmed_pending_schedule_receipt(
        attempt,
        post_id="950001" if lane == "quote_image" else "970001",
        confirmation_epoch=1_800_000_100,
    )

    monkeypatch.setattr(bot, "POST_SLEEP_MIN", 1)
    monkeypatch.setattr(bot, "POST_SLEEP_MAX", 2)
    monkeypatch.setattr(bot, "MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS", 1)
    monkeypatch.setattr(bot, "MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS", 2)
    monkeypatch.setattr(bot, "MEME_FALLBACK_HOUR", 17)
    monkeypatch.setattr(bot, "MEME_FALLBACK_MINUTE", 30)
    monkeypatch.setattr(bot, "MEME_SCHEDULE_VERSION", 3)

    assert bot.main_post_attempt_is_semantically_valid(attempt) is True
    assert bot.confirmed_pending_schedule_receipt_is_semantically_valid(
        pending,
        expected_lane=lane,
    )
    if lane == "quote_image":
        receipt = bot.materialize_bound_regular_schedule_receipt(pending)
        assert receipt["next_quote_post_epoch"] == 1_800_007_300
        assert receipt["next_meme_post_epoch"] == 0
        lines_used: set[str] = set()
        images_used: set[str] = set()
        state: dict = {}
        bot.apply_regular_post_receipt(
            receipt,
            lines_used,
            images_used,
            state,
        )
    else:
        receipt = bot.materialize_bound_meme_schedule_receipt(pending)
        next_dt = datetime.fromtimestamp(receipt["next_meme_post_epoch"])
        assert (next_dt.hour, next_dt.minute) == (16, 0)
        state = {}
        bot.apply_meme_post_receipt(receipt, state)
    expected_schedule_version = 0 if lane == "quote_image" else 2
    assert receipt["meme_schedule_version"] == expected_schedule_version
    assert state["meme_schedule_version"] == expected_schedule_version


@pytest.mark.parametrize(
    ("lane", "confirmation_local"),
    [
        (
            "quote_image",
            datetime(
                2026,
                3,
                29,
                12,
                30,
                tzinfo=ZoneInfo(bot.MAIN_POST_SCHEDULE_TIMEZONE),
            ),
        ),
        (
            "quote_image",
            datetime(
                2026,
                10,
                25,
                12,
                30,
                tzinfo=ZoneInfo(bot.MAIN_POST_SCHEDULE_TIMEZONE),
            ),
        ),
        (
            "daily_meme",
            datetime(
                2026,
                3,
                28,
                23,
                30,
                tzinfo=ZoneInfo(bot.MAIN_POST_SCHEDULE_TIMEZONE),
            ),
        ),
        (
            "daily_meme",
            datetime(
                2026,
                10,
                24,
                23,
                30,
                tzinfo=ZoneInfo(bot.MAIN_POST_SCHEDULE_TIMEZONE),
            ),
        ),
    ],
)
def test_current_pending_schedule_replay_ignores_ambient_timezone_across_dst(
    lane: str,
    confirmation_local: datetime,
) -> None:
    """Identical pending bytes have one Europe/London calendar result."""

    if not hasattr(time, "tzset"):
        pytest.skip("ambient timezone switching requires time.tzset")
    confirmation_epoch = int(confirmation_local.timestamp())
    attempt = schema_current_main_attempt(lane)
    attempt["attempt_epoch"] = confirmation_epoch - 60
    attempt["lifecycle_state"] = "attempting"
    assert attempt["schema_version"] == 5
    assert bot.main_post_attempt_is_semantically_valid(attempt)
    pending = bot.build_confirmed_pending_schedule_receipt(
        attempt,
        post_id="950001" if lane == "quote_image" else "970001",
        confirmation_epoch=confirmation_epoch,
        image_summary=str(attempt["recovery_plan"].get("image_summary") or ""),
    )
    pending_bytes = bot.canonical_atomic_json_bytes(pending)
    original_timezone = os.environ.get("TZ")
    outputs: list[dict] = []
    applied_schedules: list[tuple[object, ...]] = []
    try:
        for ambient_timezone in ("UTC", "Pacific/Honolulu", "Asia/Tokyo"):
            os.environ["TZ"] = ambient_timezone
            time.tzset()
            assert bot.canonical_atomic_json_bytes(pending) == pending_bytes
            if lane == "quote_image":
                receipt = bot.materialize_bound_regular_schedule_receipt(
                    copy.deepcopy(pending)
                )
                assert bot.regular_post_receipt_is_semantically_valid(receipt)
                state: dict = {}
                bot.apply_regular_post_receipt(receipt, set(), set(), state)
            else:
                receipt = bot.materialize_bound_meme_schedule_receipt(
                    copy.deepcopy(pending)
                )
                assert bot.meme_post_receipt_is_semantically_valid(receipt)
                state = {}
                bot.apply_meme_post_receipt(receipt, state)
            reloaded_state = json.loads(bot.canonical_atomic_json_bytes(state))
            assert bot.validate_meme_schedule_state(
                reloaded_state,
                path=Path("reloaded-timezone-bound-state.json"),
            )
            outputs.append(receipt)
            applied_schedules.append(
                (
                    state.get("next_meme_post_epoch"),
                    state.get("meme_schedule_version"),
                    state.get("next_meme_schedule_mode"),
                    state.get("next_meme_schedule_date"),
                    state.get("meme_anchor_quote_post_epoch"),
                )
            )
    finally:
        if original_timezone is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_timezone
        time.tzset()

    assert outputs[1:] == [outputs[0], outputs[0]]
    assert applied_schedules[1:] == [applied_schedules[0], applied_schedules[0]]
    if lane == "quote_image":
        assert outputs[0]["meme_schedule_changed_by_quote"] is True
        assert outputs[0]["next_meme_post_epoch"] == confirmation_epoch + 3600
        assert outputs[0]["next_meme_schedule_date"] == (
            confirmation_local.strftime("%Y-%m-%d")
        )
    else:
        expected_next = (confirmation_local + timedelta(days=1)).replace(
            hour=16,
            minute=0,
            second=0,
            microsecond=0,
        )
        assert outputs[0]["next_meme_post_epoch"] == int(
            expected_next.timestamp()
        )
        assert outputs[0]["next_meme_schedule_date"] == expected_next.strftime(
            "%Y-%m-%d"
        )


def test_meme_schedule_producers_and_guards_ignore_ambient_timezone() -> None:
    """Current meme calendar state has one production-zone interpretation."""

    if not hasattr(time, "tzset"):
        pytest.skip("ambient timezone switching requires time.tzset")
    quote_local = datetime(
        2026,
        3,
        29,
        16,
        30,
        tzinfo=ZoneInfo(bot.MAIN_POST_SCHEDULE_TIMEZONE),
    )
    quote_epoch = int(quote_local.timestamp())
    original_timezone = os.environ.get("TZ")
    outputs: list[tuple[dict, dict, bool]] = []
    try:
        for ambient_timezone in ("UTC", "Pacific/Honolulu", "Asia/Tokyo"):
            os.environ["TZ"] = ambient_timezone
            time.tzset()
            fallback = bot.next_meme_schedule_fields({}, quote_epoch)
            after_quote = bot.meme_schedule_fields_after_quote_post(
                {},
                quote_epoch,
                delay=3600,
            )
            reloaded = json.loads(bot.canonical_atomic_json_bytes(after_quote))
            assert bot.validate_meme_schedule_state(
                reloaded,
                path=Path("reloaded-produced-meme-state.json"),
            )
            posted_on_bound_date = bot.meme_posted_on_date(
                {"last_meme_post_epoch": quote_epoch},
                quote_local.strftime("%Y-%m-%d"),
            )
            outputs.append((fallback, after_quote, posted_on_bound_date))
    finally:
        if original_timezone is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_timezone
        time.tzset()

    assert outputs[1:] == [outputs[0], outputs[0]]
    assert outputs[0][2] is True
    assert outputs[0][1]["next_meme_schedule_date"] == "2026-03-29"


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_confirmed_pending_schedule_requires_timezone_bound_schema_v5(
    lane: str,
) -> None:
    legacy = schema_current_main_attempt(lane)
    legacy["schema_version"] = 4
    legacy["lifecycle_state"] = "attempting"
    legacy["recovery_plan"].pop("schedule_timezone")
    assert bot.main_post_attempt_is_semantically_valid(legacy)
    pending = {
        "schema_version": 1,
        "receipt_type": "confirmed_pending_schedule",
        "post_id": "950001" if lane == "quote_image" else "970001",
        "confirmation_epoch": int(legacy["attempt_epoch"]) + 1,
        "source_attempt": legacy,
        "image_summary": str(legacy["recovery_plan"].get("image_summary") or ""),
    }
    assert not bot.confirmed_pending_schedule_receipt_is_semantically_valid(
        pending,
        expected_lane=lane,
    )
    with pytest.raises(RuntimeError, match="pending-schedule receipt is invalid"):
        bot.build_confirmed_pending_schedule_receipt(
            legacy,
            post_id=pending["post_id"],
            confirmation_epoch=pending["confirmation_epoch"],
            image_summary=pending["image_summary"],
        )


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
@pytest.mark.parametrize("timezone_value", [None, "UTC", 0])
def test_current_main_attempt_rejects_unbound_schedule_timezone(
    lane: str,
    timezone_value: object,
) -> None:
    attempt = schema_current_main_attempt(lane)
    if timezone_value is None:
        attempt["recovery_plan"].pop("schedule_timezone")
    else:
        attempt["recovery_plan"]["schedule_timezone"] = timezone_value
    assert bot.main_post_attempt_is_semantically_valid(attempt) is False


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_main_attempt_builder_refuses_unbound_current_plan(lane: str) -> None:
    """The builder cannot silently downgrade a current plan to a legacy schema."""

    current = schema_current_main_attempt(lane)
    recovery_plan = copy.deepcopy(current["recovery_plan"])
    recovery_plan.pop("schedule_timezone")
    with pytest.raises(ValueError, match="bind the production schedule timezone"):
        bot.build_main_post_attempt(
            lane=lane,
            text=str(current["text"]),
            media_ids=list(current["media_ids"]),
            made_with_ai=bool(current["made_with_ai"]),
            selected_identity=copy.deepcopy(current["selected_identity"]),
            recovery_plan=recovery_plan,
            attempt_epoch=int(current["attempt_epoch"]),
        )


def test_legacy_schema4_schedule_validation_ignores_ambient_timezone() -> None:
    """Reader compatibility uses the historical production calendar."""

    if not hasattr(time, "tzset"):
        pytest.skip("ambient timezone switching requires time.tzset")
    next_local = datetime(
        2026,
        3,
        29,
        0,
        30,
        tzinfo=ZoneInfo(bot.MAIN_POST_SCHEDULE_TIMEZONE),
    )
    next_epoch = int(next_local.timestamp())
    legacy = schema_current_main_attempt("quote_image")
    legacy["schema_version"] = 4
    legacy["recovery_plan"].pop("schedule_timezone")
    legacy["recovery_plan"]["meme_schedule_before"] = {
        "last_meme_post_epoch": 0,
        "next_meme_post_epoch": next_epoch,
        "meme_schedule_version": int(bot.MEME_SCHEDULE_VERSION),
        "next_meme_schedule_mode": "fallback",
        "next_meme_schedule_date": "2026-03-29",
        "meme_anchor_quote_post_epoch": 0,
    }
    legacy_bytes = bot.canonical_atomic_json_bytes(legacy)
    original_timezone = os.environ.get("TZ")
    try:
        for ambient_timezone in ("Europe/London", "Pacific/Honolulu", "Asia/Tokyo"):
            os.environ["TZ"] = ambient_timezone
            time.tzset()
            assert bot.canonical_atomic_json_bytes(legacy) == legacy_bytes
            assert bot.main_post_attempt_is_semantically_valid(legacy)
    finally:
        if original_timezone is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_timezone
        time.tzset()


def test_current_attempt_schema_rejects_absurd_bound_schedule_values() -> None:
    regular = schema_current_main_attempt("quote_image")
    regular["recovery_plan"]["quote_delay_seconds"] = (
        bot.MAIN_POST_ATTEMPT_MAX_BOUND_DELAY_SECONDS + 1
    )
    assert bot.main_post_attempt_is_semantically_valid(regular) is False

    meme = schema_current_main_attempt("daily_meme")
    meme["recovery_plan"]["fallback_hour"] = 24
    assert bot.main_post_attempt_is_semantically_valid(meme) is False

    future_regular = schema_current_main_attempt("quote_image")
    future_regular["recovery_plan"]["meme_schedule_version"] = (
        bot.MEME_SCHEDULE_VERSION + 1
    )
    assert bot.main_post_attempt_is_semantically_valid(future_regular) is False

    future_snapshot = schema_current_main_attempt("quote_image")
    future_snapshot["recovery_plan"]["meme_schedule_before"][
        "meme_schedule_version"
    ] = bot.MEME_SCHEDULE_VERSION + 1
    assert bot.main_post_attempt_is_semantically_valid(future_snapshot) is False

    future_meme = schema_current_main_attempt("daily_meme")
    future_meme["recovery_plan"]["meme_schedule_version"] = (
        bot.MEME_SCHEDULE_VERSION + 1
    )
    assert bot.main_post_attempt_is_semantically_valid(future_meme) is False


@pytest.mark.parametrize("lane", ["quote_image", "daily_meme"])
def test_current_full_receipt_rejects_future_schedule_version(
    lane: str,
) -> None:
    attempt = schema_current_main_attempt(lane)
    attempt["lifecycle_state"] = "attempting"
    pending = bot.build_confirmed_pending_schedule_receipt(
        attempt,
        post_id="950001" if lane == "quote_image" else "970001",
        confirmation_epoch=1_800_000_100,
    )
    if lane == "quote_image":
        receipt = bot.materialize_bound_regular_schedule_receipt(pending)
        receipt["meme_schedule_version"] = bot.MEME_SCHEDULE_VERSION + 1
        assert bot.regular_post_receipt_is_semantically_valid(receipt) is False
    else:
        receipt = bot.materialize_bound_meme_schedule_receipt(pending)
        receipt["meme_schedule_version"] = bot.MEME_SCHEDULE_VERSION + 1
        assert bot.meme_post_receipt_is_semantically_valid(receipt) is False


def test_bound_quote_anchored_meme_schedule_accepts_cross_midnight_target() -> None:
    anchor_epoch = int(
        datetime(
            2026,
            7,
            6,
            23,
            50,
            0,
            tzinfo=ZoneInfo(bot.MAIN_POST_SCHEDULE_TIMEZONE),
        ).timestamp()
    )
    target_epoch = anchor_epoch + 3600
    assert bot.safe_bound_schedule_date_str(
        target_epoch,
        bot.MAIN_POST_SCHEDULE_TIMEZONE,
    ) != bot.safe_bound_schedule_date_str(
        anchor_epoch,
        bot.MAIN_POST_SCHEDULE_TIMEZONE,
    )
    snapshot = bot.bound_meme_schedule_state(
        {
            "next_meme_post_epoch": target_epoch,
            "meme_schedule_version": 2,
            "next_meme_schedule_mode": "after_first_quote_after_midday",
            "next_meme_schedule_date": bot.safe_bound_schedule_date_str(
                anchor_epoch,
                bot.MAIN_POST_SCHEDULE_TIMEZONE,
            ),
            "meme_anchor_quote_post_epoch": anchor_epoch,
        },
        schedule_timezone=bot.MAIN_POST_SCHEDULE_TIMEZONE,
    )

    assert bot.bound_meme_schedule_state_is_valid(snapshot) is True
    quote_hash = bot.quote_text_hash("Good quote.")
    attempt = bot.build_main_post_attempt(
        lane="quote_image",
        text="Good quote.",
        media_ids=["media-1"],
        made_with_ai=False,
        selected_identity={
            "quote_hash": quote_hash,
            "line_no": 0,
            "source_line_number": 1,
            "image_basename": "t01.jpg",
            "image_no": 0,
        },
        recovery_plan={
            "quote_delay_seconds": 7200,
            "meme_delay_seconds": 3600,
            "meme_scheduling_enabled": True,
            "meme_trigger_after_hour": 12,
            "meme_schedule_version": 2,
            "schedule_timezone": bot.MAIN_POST_SCHEDULE_TIMEZONE,
            "meme_schedule_before": snapshot,
            "quote_history_after": [quote_hash],
            "image_history_after": ["t01.jpg"],
        },
        attempt_epoch=anchor_epoch,
    )
    assert bot.main_post_attempt_is_semantically_valid(attempt) is True


def test_regular_post_does_not_call_mutating_schedule_helpers_after_confirmation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, _receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    monkeypatch.setattr(bot, "schedule_next_quote_post", lambda *args, **kwargs: pytest.fail("schedule_next_quote_post should not be called"))
    monkeypatch.setattr(bot, "maybe_schedule_meme_after_quote_post", lambda *args, **kwargs: pytest.fail("maybe_schedule_meme_after_quote_post should not be called"))

    bot.post_random_quote(lines_used, images_used, state)

    assert state["next_quote_post_epoch"] > state["last_quote_post_epoch"]


def test_regular_receipt_accepts_all_runtime_meme_schedule_modes() -> None:
    next_meme_epoch = 1_800_086_400
    for mode in sorted(bot.MEME_SCHEDULE_MODES - {"", "after_first_quote_after_midday"}):
        receipt = valid_regular_receipt(
            next_meme_post_epoch=next_meme_epoch,
            next_meme_schedule_mode=mode,
            next_meme_schedule_date=bot.epoch_date_str(next_meme_epoch),
            meme_anchor_quote_post_epoch=0,
            meme_schedule_changed_by_quote=False,
        )
        assert bot.regular_post_receipt_is_semantically_valid(receipt), mode


def test_regular_receipt_distinguishes_quote_created_and_preserved_meme_schedules() -> None:
    quote_epoch = 1_800_000_000
    quote_created = valid_regular_receipt(
        quote_post_epoch=quote_epoch,
        next_quote_post_epoch=quote_epoch + 7200,
        next_meme_post_epoch=quote_epoch + 3600,
        next_meme_schedule_mode="after_first_quote_after_midday",
        next_meme_schedule_date=bot.epoch_date_str(quote_epoch),
        meme_anchor_quote_post_epoch=quote_epoch,
        meme_schedule_changed_by_quote=True,
    )
    assert bot.regular_post_receipt_is_semantically_valid(quote_created)

    anchor_epoch = quote_epoch - 7200
    preserved_overdue = valid_regular_receipt(
        quote_post_epoch=quote_epoch,
        next_quote_post_epoch=quote_epoch + 7200,
        next_meme_post_epoch=quote_epoch - 1800,
        next_meme_schedule_mode="after_first_quote_after_midday",
        next_meme_schedule_date=bot.epoch_date_str(anchor_epoch),
        meme_anchor_quote_post_epoch=anchor_epoch,
        meme_schedule_changed_by_quote=False,
    )
    assert bot.regular_post_receipt_is_semantically_valid(preserved_overdue)


def test_cross_midnight_first_quote_meme_anchor_is_not_rescheduled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot, "MEME_TRIGGER_AFTER_HOUR", 12)
    quote_one = int(datetime(2026, 7, 6, 23, 30).timestamp())
    quote_two = int(datetime(2026, 7, 6, 23, 50).timestamp())
    state = {"next_meme_post_epoch": 0, "last_meme_post_epoch": 0}

    fields = bot.meme_schedule_fields_after_quote_post(state, quote_one, delay=3600)
    assert fields["next_meme_schedule_mode"] == "after_first_quote_after_midday"
    assert fields["next_meme_schedule_date"] == "2026-07-06"
    assert bot.epoch_date_str(fields["next_meme_post_epoch"]) == "2026-07-07"
    state.update(fields)

    assert bot.meme_schedule_fields_after_quote_post(state, quote_two, delay=1800) == {}
    assert state["meme_anchor_quote_post_epoch"] == quote_one
    assert state["next_meme_post_epoch"] == quote_one + 3600


def test_regular_receipt_validates_cross_midnight_quote_anchored_meme_schedule() -> None:
    quote_epoch = int(datetime(2026, 7, 6, 23, 30).timestamp())
    receipt = valid_regular_receipt(
        quote_post_epoch=quote_epoch,
        next_quote_post_epoch=quote_epoch + 7200,
        next_meme_post_epoch=quote_epoch + 3600,
        next_meme_schedule_mode="after_first_quote_after_midday",
        next_meme_schedule_date="2026-07-06",
        meme_anchor_quote_post_epoch=quote_epoch,
        meme_schedule_changed_by_quote=True,
    )
    assert bot.regular_post_receipt_is_semantically_valid(receipt)


def test_delayed_meme_schedule_modes_clear_stale_quote_anchor() -> None:
    delayed_modes = {
        "delayed_runtime_control",
        "delayed_recent_quote",
        "delayed_write_api_cooldown",
        "delayed_api_error",
        "delayed_exception",
    }
    for mode in delayed_modes:
        state = {
            "next_meme_schedule_mode": "after_first_quote_after_midday",
            "meme_anchor_quote_post_epoch": 1_800_000_000,
        }
        target_epoch = 1_800_010_000
        bot.set_meme_delay_schedule(state, epoch=target_epoch, mode=mode, save=False)
        assert state["next_meme_post_epoch"] == target_epoch
        assert state["next_meme_schedule_mode"] == mode
        assert state["next_meme_schedule_date"] == bot.epoch_date_str(target_epoch)
        assert state["meme_anchor_quote_post_epoch"] == 0


def test_all_meme_schedule_modes_preserve_anchor_invariants() -> None:
    target_epoch = 1_800_010_000
    anchor_epoch = int(datetime(2026, 7, 6, 13, 0).timestamp())
    fallback_modes = bot.MEME_SCHEDULE_MODES - {
        "",
        "after_first_quote_after_midday",
        "delayed_runtime_control",
        "delayed_recent_quote",
        "delayed_write_api_cooldown",
        "delayed_api_error",
        "delayed_exception",
    }
    delayed_modes = {
        "delayed_runtime_control",
        "delayed_recent_quote",
        "delayed_write_api_cooldown",
        "delayed_api_error",
        "delayed_exception",
    }

    for mode in fallback_modes:
        fields = bot.next_meme_schedule_fields({}, target_epoch - 1, mode=mode)
        assert fields["next_meme_schedule_mode"] == mode
        assert fields["meme_anchor_quote_post_epoch"] == 0
        assert fields["next_meme_schedule_date"] == bot.epoch_date_str(fields["next_meme_post_epoch"])

    for mode in delayed_modes:
        fields = bot.meme_delay_schedule_fields(target_epoch, mode)
        assert fields["next_meme_schedule_mode"] == mode
        assert fields["meme_anchor_quote_post_epoch"] == 0
        assert fields["next_meme_schedule_date"] == bot.epoch_date_str(target_epoch)

    quote_fields = bot.meme_schedule_fields_after_quote_post({"last_meme_post_epoch": 0}, anchor_epoch, delay=3600)
    assert quote_fields["next_meme_schedule_mode"] == "after_first_quote_after_midday"
    assert quote_fields["meme_anchor_quote_post_epoch"] == anchor_epoch
    assert quote_fields["next_meme_schedule_date"] == bot.epoch_date_str(anchor_epoch)


def test_delayed_schedule_followed_by_regular_quote_produces_self_validating_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, _receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    bot.set_meme_delay_schedule(state, epoch=1_800_010_000, mode="delayed_recent_quote", save=False)
    receipts: list[dict] = []
    original_write = bot.write_regular_post_receipt

    def capture_receipt(receipt: dict) -> None:
        if bot.confirmed_pending_schedule_receipt_is_semantically_valid(
            receipt,
            expected_lane="quote_image",
        ):
            original_write(receipt)
            return
        assert bot.regular_post_receipt_is_semantically_valid(receipt)
        receipts.append(json.loads(json.dumps(receipt)))
        original_write(receipt)

    monkeypatch.setattr(bot, "write_regular_post_receipt", capture_receipt)

    bot.post_random_quote(lines_used, images_used, state)

    assert receipts
    assert receipts[0]["schema_version"] == 3
    assert receipts[0]["quote_history_after"] == [
        bot.quote_text_hash("Good quote.")
    ]
    assert receipts[0]["image_history_after"] == ["t01.jpg"]
    assert receipts[0]["next_meme_schedule_mode"] == "delayed_recent_quote"
    assert receipts[0]["meme_schedule_version"] == bot.MEME_SCHEDULE_VERSION
    assert receipts[0]["meme_anchor_quote_post_epoch"] == 0
    assert receipts[0]["meme_schedule_changed_by_quote"] is False


def test_regular_post_commits_future_quote_schedule_and_receipt_epoch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, _receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    receipts: list[dict] = []
    original_write = bot.write_regular_post_receipt
    monkeypatch.setattr(
        bot.random,
        "randint",
        lambda low, high: 7200 if low == bot.POST_SLEEP_MIN else low,
    )
    def capture_receipt(receipt: dict) -> None:
        if bot.regular_post_receipt_is_semantically_valid(receipt):
            receipts.append(json.loads(json.dumps(receipt)))
        original_write(receipt)

    monkeypatch.setattr(bot, "write_regular_post_receipt", capture_receipt)

    bot.post_random_quote(lines_used, images_used, state)

    assert state["next_quote_post_epoch"] == 1_800_007_200
    assert receipts[0]["next_quote_post_epoch"] == 1_800_007_200


def test_regular_post_uses_confirmed_time_for_noon_meme_schedule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, _receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    pre_confirm_epoch = int(datetime(2026, 7, 6, 11, 59, 50).timestamp())
    confirmed_epoch = int(datetime(2026, 7, 6, 12, 0, 5).timestamp())
    remote_confirmed = {"value": False}

    def fake_create_post(**kwargs: object) -> dict:
        # Model the remote acceptance boundary before the production helper
        # records its confirmation time.
        remote_confirmed["value"] = True
        result = mock_confirmed_main_post(
            kwargs,
            {"data": {"id": "950001"}},
        )
        return result

    def fake_now_epoch() -> int:
        return confirmed_epoch if remote_confirmed["value"] else pre_confirm_epoch

    def fake_randint(low: int, high: int) -> int:
        if low == bot.POST_SLEEP_MIN and high == bot.POST_SLEEP_MAX:
            return 7200
        return 3600

    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot, "MEME_TRIGGER_AFTER_HOUR", 12)
    monkeypatch.setattr(bot, "create_post", fake_create_post)
    monkeypatch.setattr(bot, "now_epoch", fake_now_epoch)
    monkeypatch.setattr(bot.random, "randint", fake_randint)

    bot.post_random_quote(lines_used, images_used, state)

    assert state["last_quote_post_epoch"] == confirmed_epoch
    assert state["next_quote_post_epoch"] == confirmed_epoch + 7200
    assert state["next_meme_schedule_mode"] == "after_first_quote_after_midday"
    assert state["meme_anchor_quote_post_epoch"] == confirmed_epoch
    assert state["next_meme_post_epoch"] == confirmed_epoch + 3600


def test_regular_schedule_finalisation_failure_after_confirmation_is_confirmed_local_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    quote_hash = bot.quote_text_hash("Good quote.")
    original_materialize = bot.materialize_bound_regular_schedule_receipt
    monkeypatch.setattr(
        bot,
        "materialize_bound_regular_schedule_receipt",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("schedule finalisation failed")),
    )

    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_random_quote(lines_used, images_used, state)

    status, pending = bot.load_regular_post_receipt()
    assert status == "pending_schedule"
    assert pending is not None
    assert pending["post_id"] == "950001"
    assert quote_hash not in lines_used
    assert "t01.jpg" not in images_used
    assert "last_main_post_id" not in state

    expected = original_materialize(pending, _validate_result=False)
    monkeypatch.setattr(
        bot,
        "materialize_bound_regular_schedule_receipt",
        original_materialize,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: pytest.fail("confirmed pending receipt must not repost"),
    )
    bot.post_random_quote(lines_used, images_used, state)

    assert not receipt_file.exists()
    assert lines_used == set(expected["quote_history_after"])
    assert images_used == set(expected["image_history_after"])
    assert state["last_main_post_id"] == "950001"
    assert state["next_quote_post_epoch"] == expected["next_quote_post_epoch"]


def test_regular_quote_schedule_failure_after_confirmation_suppresses_quote_lane(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    quote_hash = bot.quote_text_hash("Good quote.")
    monkeypatch.setattr(
        bot.random,
        "randint",
        lambda low, high: 7200 if low == bot.POST_SLEEP_MIN else low,
    )
    original_write = bot.write_regular_post_receipt
    def fail_final_schedule_receipt(receipt: dict) -> None:
        raise RuntimeError("quote schedule receipt failed")

    monkeypatch.setattr(
        bot,
        "write_regular_post_receipt",
        fail_final_schedule_receipt,
    )

    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_random_quote(lines_used, images_used, state)

    status, pending = bot.load_regular_post_receipt()
    assert status == "pending_schedule"
    assert pending is not None
    expected = bot.materialize_bound_regular_schedule_receipt(pending)
    assert expected["next_quote_post_epoch"] == 1_800_007_200
    assert quote_hash not in lines_used
    assert "t01.jpg" not in images_used

    monkeypatch.setattr(bot, "write_regular_post_receipt", original_write)
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: pytest.fail("confirmed pending receipt must not repost"),
    )
    bot.post_random_quote(lines_used, images_used, state)
    assert not receipt_file.exists()
    assert state["next_quote_post_epoch"] == 1_800_007_200
    assert state["last_main_post_id"] == "950001"


def test_regular_schedule_failure_replays_exact_bound_meme_delay(
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
    confirmed_epoch = int(datetime(2026, 7, 6, 13, 0, 0).timestamp())
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot, "MEME_TRIGGER_AFTER_HOUR", 12)
    monkeypatch.setattr(bot, "now_epoch", lambda: confirmed_epoch)

    def bound_delays(low: int, high: int) -> int:
        if low == bot.POST_SLEEP_MIN and high == bot.POST_SLEEP_MAX:
            return 7200
        assert (low, high) == (
            bot.MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS,
            bot.MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS,
        )
        return 3600

    monkeypatch.setattr(bot.random, "randint", bound_delays)
    original_materialize = bot.materialize_bound_regular_schedule_receipt
    monkeypatch.setattr(
        bot,
        "materialize_bound_regular_schedule_receipt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("injected post-confirmation schedule failure")
        ),
    )

    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_random_quote(lines_used, images_used, state)

    status, pending = bot.load_regular_post_receipt()
    assert status == "pending_schedule"
    assert pending is not None
    assert pending["source_attempt"]["recovery_plan"]["meme_delay_seconds"] == 3600
    expected = original_materialize(pending, _validate_result=False)
    assert expected["next_meme_post_epoch"] == confirmed_epoch + 3600

    monkeypatch.setattr(
        bot,
        "materialize_bound_regular_schedule_receipt",
        original_materialize,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **_kwargs: pytest.fail(
            "local schedule replay must not create another regular post"
        ),
    )
    bot.post_random_quote(lines_used, images_used, state)

    assert not receipt_file.exists()
    assert state["next_quote_post_epoch"] == confirmed_epoch + 7200
    assert state["next_meme_post_epoch"] == confirmed_epoch + 3600
    assert state["next_meme_schedule_mode"] == "after_first_quote_after_midday"
    assert state["meme_anchor_quote_post_epoch"] == confirmed_epoch
    assert state["next_meme_post_epoch"] != confirmed_epoch + 1800


def test_regular_restart_replays_bound_meme_delay_from_receipt_after_state_save_failure(
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
    confirmed_epoch = int(datetime(2026, 7, 6, 13, 0, 0).timestamp())
    stale_due_epoch = confirmed_epoch - 60
    state.update(
        {
            "next_meme_post_epoch": stale_due_epoch,
            "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
            "next_meme_schedule_mode": "fallback",
            "next_meme_schedule_date": bot.meme_schedule_date_str(
                stale_due_epoch
            ),
            "meme_anchor_quote_post_epoch": 0,
        }
    )
    bot.save_state(state, durable=True)
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot, "MEME_TRIGGER_AFTER_HOUR", 12)
    monkeypatch.setattr(bot, "now_epoch", lambda: confirmed_epoch)

    def bound_delays(low: int, high: int) -> int:
        if (low, high) == (bot.POST_SLEEP_MIN, bot.POST_SLEEP_MAX):
            return 7200
        assert (low, high) == (
            bot.MEME_DELAY_AFTER_MAIN_POST_MIN_SECONDS,
            bot.MEME_DELAY_AFTER_MAIN_POST_MAX_SECONDS,
        )
        return 3600

    monkeypatch.setattr(bot.random, "randint", bound_delays)
    original_save_protected = bot.save_regular_post_protected_state
    monkeypatch.setattr(
        bot,
        "save_regular_post_protected_state",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("injected protected-state save failure")
        ),
    )

    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_random_quote(lines_used, images_used, state)

    status, receipt = bot.load_regular_post_receipt()
    assert status == "valid"
    assert receipt is not None
    assert receipt["source_attempt"]["recovery_plan"]["meme_delay_seconds"] == 3600
    assert receipt["next_meme_post_epoch"] == confirmed_epoch + 3600

    restarted_state = bot.load_runtime_state()
    assert restarted_state["next_meme_post_epoch"] == stale_due_epoch
    monkeypatch.setattr(
        bot,
        "save_regular_post_protected_state",
        original_save_protected,
    )
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **_kwargs: pytest.fail(
            "startup receipt replay must not create another regular post"
        ),
    )

    reconciled = bot.reconcile_startup_main_post_receipts(
        set(),
        set(),
        restarted_state,
        confirmed_epoch + 1,
    )

    assert reconciled == {"regular": True, "meme": False}
    assert not receipt_file.exists()
    assert restarted_state["last_quote_post_epoch"] == confirmed_epoch
    assert restarted_state["next_meme_post_epoch"] == confirmed_epoch + 3600
    assert restarted_state["next_meme_schedule_mode"] == (
        "after_first_quote_after_midday"
    )
    assert restarted_state["meme_anchor_quote_post_epoch"] == confirmed_epoch
    assert restarted_state["next_meme_post_epoch"] != confirmed_epoch + 1800
    assert bot.load_runtime_state()["next_meme_post_epoch"] == (
        confirmed_epoch + 3600
    )


def test_schema_v2_regular_replay_does_not_invent_unbound_meme_schedule(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = valid_regular_receipt_v2(
        next_meme_post_epoch=0,
        next_meme_schedule_mode="",
        next_meme_schedule_date="",
        meme_anchor_quote_post_epoch=0,
        meme_schedule_changed_by_quote=False,
    )
    state = {
        "next_meme_post_epoch": 1_800_001_800,
        "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
        "next_meme_schedule_mode": "delayed_exception",
        "next_meme_schedule_date": bot.epoch_date_str(1_800_001_800),
        "meme_anchor_quote_post_epoch": 0,
    }
    monkeypatch.setattr(
        bot,
        "maybe_schedule_meme_after_quote_post",
        lambda *_args, **_kwargs: pytest.fail(
            "schema-v2 replay must not derive a new random meme schedule"
        ),
    )

    bot.apply_regular_post_receipt(receipt, set(), set(), state)

    assert state["next_meme_post_epoch"] == 0
    assert state["next_meme_schedule_mode"] == ""
    assert state["next_meme_schedule_date"] == ""
    assert state["meme_anchor_quote_post_epoch"] == 0


def test_regular_receipt_replay_restores_future_quote_schedule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    receipt = {
        "schema_version": 1,
        "post_id": "950001",
        "quote_hash": bot.quote_text_hash("Good quote."),
        "image_basename": "t01.jpg",
        "quote_post_epoch": 1_800_000_000,
        "next_quote_post_epoch": 1_800_007_200,
        "text": "Good quote.",
    }
    bot.atomic_write_json(receipt_file, receipt)

    assert bot.reconcile_regular_post_receipt(lines_used, images_used, state) is True
    assert state["next_quote_post_epoch"] == 1_800_007_200


def test_startup_regular_receipt_replay_preserves_newer_production_schedule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    (
        _lines_used,
        _images_used,
        _state,
        _lines_used_file,
        _images_used_file,
        receipt_file,
        _lines_file,
    ) = configure_simple_quote_post(tmp_path, monkeypatch)
    quote_post_epoch = 1_784_680_936  # 2026-07-22 01:42:16 BST
    stale_receipt_next = 1_784_689_435  # 2026-07-22 04:03:55 BST
    newer_state_next = 1_784_714_624  # 2026-07-22 11:03:44 BST
    first_tick_epoch = 1_784_708_283  # 2026-07-22 09:18:03 BST
    receipt = valid_regular_receipt(
        post_id="2079728698731745489",
        image_basename="t24.jpg",
        quote_post_epoch=quote_post_epoch,
        next_quote_post_epoch=stale_receipt_next,
    )
    quote_hash = str(receipt["quote_hash"])
    bot.atomic_write_json(receipt_file, receipt)
    lines_used = {quote_hash}
    images_used = {"t24.jpg"}
    state = {
        "last_main_post_id": "2079728698731745489",
        "last_quote_post_epoch": quote_post_epoch,
        "last_regular_image_filename": "t24.jpg",
        "next_quote_post_epoch": newer_state_next,
    }
    monkeypatch.setattr(
        bot,
        "CONTROL_FILE",
        tmp_path / "missing-control.json",
    )
    monkeypatch.setattr(
        bot,
        "_CONTROL_CACHE",
        {"signature": None, "data": {}, "has_valid": False, "failure_signature": None},
    )
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        lambda **kwargs: {"status": "already_completed"},
    )
    monkeypatch.setattr(
        bot,
        "schedule_next_quote_post",
        lambda *args, **kwargs: pytest.fail("future production schedule must not be replaced"),
    )
    monkeypatch.setattr(bot, "require_established_installation", lambda: None)
    monkeypatch.setattr(bot, "block_if_ambiguous_remote_post", lambda: None)
    monkeypatch.setattr(bot, "acquire_instance_lock", lambda: None)
    monkeypatch.setattr(bot, "glob", lambda pattern: [])
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", False)
    monkeypatch.setattr(bot, "validate_original_editorial_shadow_startup", lambda: None)
    monkeypatch.setattr(bot, "load_quote_used_hashes", lambda lines: lines_used)
    monkeypatch.setattr(bot, "load_image_used_basenames", lambda paths: images_used)
    monkeypatch.setattr(bot, "current_image_paths", lambda: [])
    monkeypatch.setattr(bot, "load_runtime_state", lambda: state)
    monkeypatch.setattr(bot, "seed_recent_own_post_ids_from_cache", lambda state: None)
    monkeypatch.setattr(bot, "now_epoch", lambda: first_tick_epoch)
    monkeypatch.setattr(
        bot,
        "scheduler_epoch_from_state",
        lambda state, key, current: (current, False),
    )
    monkeypatch.setattr(
        bot,
        "run_reply_lane_checks_for_tick",
        lambda state, current: (0, 0),
    )
    monkeypatch.setattr(bot, "ambiguous_remote_post_is_blocking", lambda: False)
    monkeypatch.setattr(
        bot,
        "post_random_quote",
        lambda *args, **kwargs: pytest.fail("first loop tick must not post again"),
    )

    class FirstTickComplete(Exception):
        pass

    monkeypatch.setattr(
        bot,
        "sleep",
        lambda seconds: (_ for _ in ()).throw(FirstTickComplete),
    )

    with pytest.raises(FirstTickComplete):
        bot.main()

    assert state["next_quote_post_epoch"] == newer_state_next
    assert state["next_quote_post_epoch"] > first_tick_epoch
    assert not receipt_file.exists()


def test_startup_regular_receipt_with_missing_state_defers_first_quote_tick(
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
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        lambda **kwargs: {"status": "completed"},
    )
    monkeypatch.setattr(bot.random, "randint", lambda low, high: 7_200)
    first_tick_epoch = 1_784_708_283

    status = bot.reconcile_main_post_receipts(
        lines_used,
        images_used,
        state,
        minimum_next_quote_epoch=first_tick_epoch,
    )

    assert state["last_quote_post_epoch"] == receipt["quote_post_epoch"]
    assert receipt["quote_hash"] in lines_used
    assert receipt["image_basename"] in images_used
    assert status == {"regular": True, "meme": False}
    assert state["next_quote_post_epoch"] == first_tick_epoch + 7_200
    assert state["next_quote_post_epoch"] > first_tick_epoch
    assert not receipt_file.exists()


def test_startup_receipt_persists_future_schedule_before_receipt_removal(
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
    monkeypatch.setattr(
        bot,
        "maybe_post_historical_context_reply",
        lambda **_kwargs: {"status": "completed"},
    )
    monkeypatch.setattr(bot.random, "randint", lambda _low, _high: 7_200)
    monkeypatch.setattr(
        bot,
        "remove_regular_post_receipt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            RuntimeError("simulated removal crash")
        ),
    )
    first_tick_epoch = 1_784_708_283

    with pytest.raises(RuntimeError, match="simulated removal crash"):
        bot.reconcile_main_post_receipts(
            lines_used,
            images_used,
            state,
            minimum_next_quote_epoch=first_tick_epoch,
        )

    durable_state = json.loads(bot.STATE_FILE.read_text(encoding="utf-8"))
    assert durable_state["next_quote_post_epoch"] == first_tick_epoch + 7_200
    assert durable_state["next_quote_post_epoch"] > first_tick_epoch
    assert receipt_file.exists()


@pytest.mark.parametrize("bad_value", [float("inf"), float("-inf"), 1.5, True])
def test_scheduler_epoch_rejects_non_integer_numeric_values(bad_value: object) -> None:
    state = {"last_reply_check_epoch": bad_value}

    assert bot.scheduler_epoch_from_state(
        state,
        "last_reply_check_epoch",
        current=2_000_000_000,
    ) == (0, True)
    assert state["last_reply_check_epoch"] == 0
    assert type(state["last_reply_check_epoch"]) is int


@pytest.mark.parametrize(
    "state",
    [
        {
            "next_meme_post_epoch": 1_800_010_000,
            "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
            "next_meme_schedule_mode": "delayed_recent_quote",
            "next_meme_schedule_date": bot.epoch_date_str(1_800_010_000),
            "meme_anchor_quote_post_epoch": 1_800_000_000,
        },
        {
            "next_meme_post_epoch": 1_800_010_000,
            "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
            "next_meme_schedule_mode": "after_first_quote_after_midday",
            "next_meme_schedule_date": bot.epoch_date_str(1_800_000_000),
            "meme_anchor_quote_post_epoch": 0,
        },
        {
            "next_meme_post_epoch": 1_800_000_000,
            "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
            "next_meme_schedule_mode": "after_first_quote_after_midday",
            "next_meme_schedule_date": bot.epoch_date_str(1_800_000_000),
            "meme_anchor_quote_post_epoch": 1_800_000_000,
        },
        {
            "next_meme_post_epoch": 1_800_010_000,
            "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
            "next_meme_schedule_mode": "fallback",
            "next_meme_schedule_date": "2000-01-01",
            "meme_anchor_quote_post_epoch": 0,
        },
    ],
)
def test_normalise_state_rejects_inconsistent_meme_schedule(state: dict, tmp_path: Path) -> None:
    assert bot.normalise_state_candidate(state, path=tmp_path / "bot_state.json") is None


def test_old_meme_schedule_version_survives_load_until_migration(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_file = tmp_path / "bot_state.json"
    old_state = {
        "meme_schedule_version": 1,
        "next_meme_post_epoch": 1_800_010_000,
        "next_meme_schedule_mode": "fallback",
        "next_meme_schedule_date": bot.epoch_date_str(1_800_010_000),
        "meme_anchor_quote_post_epoch": 0,
    }
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 1)
    bot.atomic_write_json(state_file, old_state)

    loaded = bot.load_state()

    assert loaded["meme_schedule_version"] == 1
    assert loaded["next_meme_post_epoch"] == 1_800_010_000


def test_ensure_meme_schedule_initialized_migrates_old_version(monkeypatch: pytest.MonkeyPatch) -> None:
    state = {
        "meme_schedule_version": 1,
        "next_meme_post_epoch": 1_800_010_000,
        "next_meme_schedule_mode": "fallback",
        "next_meme_schedule_date": bot.epoch_date_str(1_800_010_000),
        "meme_anchor_quote_post_epoch": 0,
    }
    saved: list[dict] = []
    monkeypatch.setattr(bot, "ENABLE_DAILY_MEME_POSTS", True)
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: saved.append(json.loads(json.dumps(state))))

    bot.ensure_meme_schedule_initialized(state)

    assert state["meme_schedule_version"] == bot.MEME_SCHEDULE_VERSION
    assert state["next_meme_schedule_mode"] == "fallback_migrated"
    assert state["meme_anchor_quote_post_epoch"] == 0
    assert bot.validate_meme_schedule_state(state, path=Path("state.json"))
    assert saved


def test_future_meme_schedule_version_is_rejected(tmp_path: Path) -> None:
    state = {
        "meme_schedule_version": 999,
        "next_meme_post_epoch": 1_800_010_000,
        "next_meme_schedule_mode": "fallback",
        "next_meme_schedule_date": bot.epoch_date_str(1_800_010_000),
        "meme_anchor_quote_post_epoch": 0,
    }

    assert bot.normalise_state_candidate(state, path=tmp_path / "bot_state.json") is None


def test_current_meme_schedule_version_valid_state_is_unchanged(tmp_path: Path) -> None:
    state = {
        "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
        "next_meme_post_epoch": 1_800_010_000,
        "next_meme_schedule_mode": "fallback",
        "next_meme_schedule_date": bot.epoch_date_str(1_800_010_000),
        "meme_anchor_quote_post_epoch": 0,
    }

    normalised = bot.normalise_state_candidate(state, path=tmp_path / "bot_state.json")

    assert normalised is not None
    for key, value in state.items():
        assert normalised[key] == value


def test_current_active_meme_schedule_must_be_receipt_compatible(tmp_path: Path) -> None:
    bad_state = {
        "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
        "next_meme_post_epoch": 100,
        "next_meme_schedule_mode": "fallback",
        "next_meme_schedule_date": bot.epoch_date_str(100),
        "meme_anchor_quote_post_epoch": 0,
    }
    assert bot.normalise_state_candidate(bad_state, path=tmp_path / "bot_state.json") is None

    good_state = {
        "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
        "next_meme_post_epoch": 1_800_010_000,
        "next_meme_schedule_mode": "fallback",
        "next_meme_schedule_date": bot.epoch_date_str(1_800_010_000),
        "meme_anchor_quote_post_epoch": 0,
    }
    normalised = bot.normalise_state_candidate(good_state, path=tmp_path / "bot_state.json")
    assert normalised is not None

    receipt = valid_regular_receipt(
        next_meme_post_epoch=normalised["next_meme_post_epoch"],
        next_meme_schedule_mode=normalised["next_meme_schedule_mode"],
        next_meme_schedule_date=normalised["next_meme_schedule_date"],
        meme_anchor_quote_post_epoch=normalised["meme_anchor_quote_post_epoch"],
        meme_schedule_changed_by_quote=False,
    )
    assert bot.regular_post_receipt_is_semantically_valid(receipt)


def test_too_old_current_active_meme_schedule_recovers_from_backup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE", state_file)
    monkeypatch.setattr(bot, "STATE_BACKUP_COUNT", 1)
    bot.atomic_write_json(
        state_file,
        {
            "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
            "next_meme_post_epoch": 100,
            "next_meme_schedule_mode": "fallback",
            "next_meme_schedule_date": bot.epoch_date_str(100),
            "meme_anchor_quote_post_epoch": 0,
        },
    )
    bot.atomic_write_json(
        tmp_path / "bot_state.json.bak1",
        {
            "replied_to_ids": ["from-bak1"],
            "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
            "next_meme_post_epoch": 1_800_010_000,
            "next_meme_schedule_mode": "fallback",
            "next_meme_schedule_date": bot.epoch_date_str(1_800_010_000),
            "meme_anchor_quote_post_epoch": 0,
        },
    )

    recovered = bot.load_state()

    assert recovered["replied_to_ids"] == ["from-bak1"]
    assert recovered["next_meme_post_epoch"] == 1_800_010_000


def test_schedule_next_quote_post_uses_configured_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    state: dict = {}
    monkeypatch.setattr(bot.random, "randint", lambda low, high: 123)
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)

    bot.schedule_next_quote_post(state, from_epoch=1_000)

    assert state["next_quote_post_epoch"] == 1_123


def test_sanitize_next_reply_lane_priority_normalizes_invalid_value() -> None:
    state = {"next_reply_lane_priority": "sideways"}

    assert bot.sanitize_next_reply_lane_priority(state) is True
    assert state["next_reply_lane_priority"] == "normal"


def test_sanitize_next_reply_lane_priority_accepts_valid_value() -> None:
    state = {"next_reply_lane_priority": "quote"}

    assert bot.sanitize_next_reply_lane_priority(state) is False
    assert state["next_reply_lane_priority"] == "quote"
