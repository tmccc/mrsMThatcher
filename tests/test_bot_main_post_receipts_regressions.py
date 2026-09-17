"""Regression tests for bot main post receipts."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import (
    isolate_bot_runtime,
    quote_analysis_for_lines,
    configure_simple_quote_post,
    valid_regular_receipt,
    valid_regular_receipt_v2,
    schema_current_main_attempt,
)
from tests.helpers.reply_fixtures import unit_confirmed_reply_receipt


pytestmark = pytest.mark.allow_loopback_network


def test_regular_receipt_v2_restores_authoritative_post_cycle_histories(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posted_quote = bot.quote_text_hash("Good quote.")
    stale_quote = "a" * 64
    posted_image = "t01.jpg"
    stale_image = "t02.jpg"
    receipt = valid_regular_receipt_v2(
        quote_history_after=[posted_quote],
        image_history_after=[posted_image],
    )
    lines_used = {posted_quote, stale_quote}
    images_used = {posted_image, stale_image}
    state: dict = {}
    monkeypatch.setattr(bot, "cache_tweet", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "record_recent_own_post", lambda *args, **kwargs: None)

    bot.apply_regular_post_receipt(receipt, lines_used, images_used, state)

    assert lines_used == {posted_quote}
    assert images_used == {posted_image}

    # Reconciliation is idempotent and cannot restore the pre-reset histories.
    bot.apply_regular_post_receipt(receipt, lines_used, images_used, state)
    assert lines_used == {posted_quote}
    assert images_used == {posted_image}


@pytest.mark.parametrize("schema_version", [2, 3])
def test_stale_regular_receipt_cannot_erase_newer_cycle_histories(
    monkeypatch: pytest.MonkeyPatch,
    schema_version: int,
) -> None:
    stale_quote = bot.quote_text_hash("Good quote.")
    stale_image = "t01.jpg"
    newer_quote = "b" * 64
    newer_image = "t02.jpg"
    receipt = valid_regular_receipt_v2(
        schema_version=schema_version,
        quote_history_after=[stale_quote],
        image_history_after=[stale_image],
        **(
            {
                "next_meme_post_epoch": 0,
                "meme_schedule_version": 0,
                "meme_schedule_changed_by_quote": False,
            }
            if schema_version == 3
            else {}
        ),
    )
    assert bot.regular_post_receipt_is_semantically_valid(receipt)
    lines_used = {stale_quote, newer_quote}
    images_used = {stale_image, newer_image}
    newer_quote_epoch = int(receipt["quote_post_epoch"]) + 10_000
    state = {
        "last_main_post_id": "950002",
        "last_quote_post_epoch": newer_quote_epoch,
        "last_regular_image_filename": newer_image,
        "next_quote_post_epoch": newer_quote_epoch + 7_200,
    }
    monkeypatch.setattr(
        bot,
        "cache_tweet",
        lambda *_args, **_kwargs: pytest.fail(
            "stale receipt must not recache an older post as current"
        ),
    )
    monkeypatch.setattr(
        bot,
        "record_recent_own_post",
        lambda *_args, **_kwargs: pytest.fail(
            "stale receipt must not move an older post to the recent head"
        ),
    )

    bot.apply_regular_post_receipt(receipt, lines_used, images_used, state)

    assert lines_used == {stale_quote, newer_quote}
    assert images_used == {stale_image, newer_image}
    assert state["last_main_post_id"] == "950002"
    assert state["last_quote_post_epoch"] == newer_quote_epoch
    assert state["next_quote_post_epoch"] == newer_quote_epoch + 7_200


def test_stale_meme_receipt_replay_preserves_newer_daily_guard_and_schedule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stale_epoch = 1_800_000_100
    stale_next = 1_800_115_200
    newer_epoch = 1_800_200_000
    newer_next = 1_800_300_000
    receipt = {
        "schema_version": 2,
        "post_id": "970001",
        "meme_basename": "001_meme.png",
        "meme_post_epoch": stale_epoch,
        "next_meme_post_epoch": stale_next,
        "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
        "next_meme_schedule_mode": "fallback",
        "text": bot.MEME_POST_TEXT,
    }
    assert bot.meme_post_receipt_is_semantically_valid(receipt)
    receipt_path = tmp_path / "meme_post_receipt.json"
    bot.atomic_write_json(receipt_path, receipt)
    monkeypatch.setattr(bot, "MEME_POST_RECEIPT_FILE", receipt_path)
    monkeypatch.setattr(
        bot,
        "verify_lane_transport_source_lineage_if_present",
        lambda **_kwargs: True,
    )
    monkeypatch.setattr(bot, "save_state", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        bot,
        "retire_lane_transport_journal_if_present",
        lambda **_kwargs: False,
    )
    monkeypatch.setattr(
        bot,
        "remove_meme_post_receipt",
        lambda _receipt: receipt_path.unlink(),
    )
    monkeypatch.setattr(
        bot,
        "cache_tweet",
        lambda *_args, **_kwargs: pytest.fail(
            "stale meme receipt must not recache an older post as current"
        ),
    )
    monkeypatch.setattr(
        bot,
        "record_recent_own_post",
        lambda *_args, **_kwargs: pytest.fail(
            "stale meme receipt must not move an older post to the recent head"
        ),
    )
    state = {
        "last_main_post_id": "970002",
        "last_meme_post_epoch": newer_epoch,
        "next_meme_post_epoch": newer_next,
        "meme_schedule_version": bot.MEME_SCHEDULE_VERSION,
        "next_meme_schedule_mode": "fallback_future",
        "next_meme_schedule_date": bot.epoch_date_str(newer_next),
        "meme_anchor_quote_post_epoch": 123,
        "posted_meme_filenames": ["002_meme.png"],
    }
    current_date = bot.epoch_date_str(newer_epoch)
    assert bot.meme_posted_on_date(state, current_date)

    assert bot.reconcile_meme_post_receipt(state) is True

    assert state["last_main_post_id"] == "970002"
    assert state["last_meme_post_epoch"] == newer_epoch
    assert state["next_meme_post_epoch"] == newer_next
    assert state["next_meme_schedule_mode"] == "fallback_future"
    assert state["meme_anchor_quote_post_epoch"] == 123
    assert state["posted_meme_filenames"] == [
        "001_meme.png",
        "002_meme.png",
    ]
    assert bot.meme_posted_on_date(state, current_date)
    assert newer_epoch < newer_next
    assert not receipt_path.exists()


def test_regular_receipt_v1_remains_backward_compatible(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    receipt = valid_regular_receipt()
    lines_used = {"a" * 64}
    images_used = {"t02.jpg"}
    state: dict = {}
    monkeypatch.setattr(bot, "cache_tweet", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "record_recent_own_post", lambda *args, **kwargs: None)

    assert bot.regular_post_receipt_is_semantically_valid(receipt)
    bot.apply_regular_post_receipt(receipt, lines_used, images_used, state)

    assert lines_used == {"a" * 64, str(receipt["quote_hash"])}
    assert images_used == {"t02.jpg", str(receipt["image_basename"])}


@pytest.mark.parametrize(
    "patch",
    [
        {"quote_history_after": []},
        {"quote_history_after": ["not-a-hash"]},
        {"quote_history_after": ["a" * 64, "a" * 64]},
        {"image_history_after": []},
        {"image_history_after": ["../t01.jpg"]},
        {"image_history_after": ["t01.jpg", "t01.jpg"]},
    ],
)
def test_regular_receipt_v2_rejects_invalid_authoritative_histories(
    patch: dict,
) -> None:
    receipt = valid_regular_receipt_v2(**patch)

    assert bot.regular_post_receipt_is_semantically_valid(receipt) is False


@pytest.mark.parametrize(
    ("image_basename", "initial_count"),
    [
        ("tg_" + ("a" * 64) + ".png", 2),
        ("t01.jpg", 0),
    ],
)
def test_regular_receipt_reconciliation_preserves_legacy_counter_and_replays_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
    image_basename: str,
    initial_count: int,
) -> None:
    receipt_file = tmp_path / "regular_post_receipt.json"
    receipt = valid_regular_receipt(image_basename=image_basename)
    bot.atomic_write_json(receipt_file, receipt)
    monkeypatch.setattr(bot, "REGULAR_POST_RECEIPT_FILE", receipt_file)
    monkeypatch.setattr(bot, "save_regular_post_protected_state", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "cache_tweet", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "record_recent_own_post", lambda *args, **kwargs: None)
    caplog.set_level(logging.INFO, logger=bot.log.name)
    lines_used: set[str] = set()
    images_used: set[str] = set()
    state = {"original_regular_posts_since_generated_image": initial_count}

    assert bot.reconcile_regular_post_receipt(lines_used, images_used, state) is True
    assert state["original_regular_posts_since_generated_image"] == initial_count
    assert image_basename in images_used
    assert not receipt_file.exists()
    assert "GENERATED_IMAGE_SPACING_STATE_UPDATED" not in caplog.text

    caplog.clear()
    assert bot.reconcile_regular_post_receipt(lines_used, images_used, state) is False
    assert state["original_regular_posts_since_generated_image"] == initial_count
    assert "GENERATED_IMAGE_SPACING_STATE_UPDATED" not in caplog.text


def test_regular_receipt_reapply_preserves_original_legacy_counter(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    receipt = valid_regular_receipt(image_basename="t01.jpg")
    lines_used: set[str] = set()
    images_used: set[str] = set()
    state = {
        "last_quote_post_epoch": receipt["quote_post_epoch"],
        "last_regular_image_filename": "t01.jpg",
        "original_regular_posts_since_generated_image": 1,
    }
    monkeypatch.setattr(bot, "cache_tweet", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "record_recent_own_post", lambda *args, **kwargs: None)
    caplog.set_level(logging.INFO, logger=bot.log.name)

    bot.apply_regular_post_receipt(receipt, lines_used, images_used, state)

    assert state["original_regular_posts_since_generated_image"] == 1
    assert "GENERATED_IMAGE_SPACING_STATE_UPDATED" not in caplog.text


def test_generated_regular_receipt_reapply_does_not_create_retired_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_basename = "tg_" + ("a" * 64) + ".png"
    receipt = valid_regular_receipt(image_basename=image_basename)
    lines_used: set[str] = set()
    images_used: set[str] = set()
    state = {
        "last_quote_post_epoch": receipt["quote_post_epoch"],
        "last_regular_image_filename": image_basename,
    }
    monkeypatch.setattr(bot, "cache_tweet", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "record_recent_own_post", lambda *args, **kwargs: None)

    bot.apply_regular_post_receipt(receipt, lines_used, images_used, state)

    assert "original_regular_posts_since_generated_image" not in state
    assert image_basename in images_used
    assert receipt["quote_hash"] in lines_used


def test_generated_regular_receipt_reapply_preserves_retired_counter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_basename = "tg_" + ("a" * 64) + ".png"
    receipt = valid_regular_receipt(image_basename=image_basename)
    lines_used: set[str] = set()
    images_used: set[str] = set()
    state = {
        "last_quote_post_epoch": receipt["quote_post_epoch"],
        "last_regular_image_filename": image_basename,
        "original_regular_posts_since_generated_image": 2,
    }
    monkeypatch.setattr(bot, "cache_tweet", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "record_recent_own_post", lambda *args, **kwargs: None)

    bot.apply_regular_post_receipt(receipt, lines_used, images_used, state)

    assert state["original_regular_posts_since_generated_image"] == 2
    assert image_basename in images_used
    assert receipt["quote_hash"] in lines_used


def test_confirmed_generated_attempt_retains_ai_identity_and_recovers_idempotently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    template = schema_current_main_attempt("quote_image")
    basename = "tg_" + "b" * 64 + ".png"
    attempt = bot.build_main_post_attempt(
        lane="quote_image",
        text=template["text"],
        media_ids=template["media_ids"],
        made_with_ai=True,
        selected_identity=template["selected_identity"] | {"image_basename": basename},
        recovery_plan=template["recovery_plan"] | {"image_history_after": [basename]},
        attempt_epoch=template["attempt_epoch"],
    )
    pending = bot.build_confirmed_pending_schedule_receipt(
        attempt | {"lifecycle_state": "attempting"},
        post_id="970001",
        confirmation_epoch=1_800_000_100,
    )
    receipt = bot.materialize_bound_regular_schedule_receipt(pending)
    assert bot.regular_post_receipt_is_semantically_valid(receipt)
    assert receipt["source_attempt"]["made_with_ai"] is True
    assert bot.main_post_attempt_payload(receipt["source_attempt"])["made_with_ai"] is True
    assert receipt["image_basename"] == basename
    monkeypatch.setattr(bot, "cache_tweet", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "record_recent_own_post", lambda *args, **kwargs: None)
    lines, images = set(), set()
    state = {"original_regular_posts_since_generated_image": 2}

    bot.apply_regular_post_receipt(receipt, lines, images, state)
    first_state = dict(state)
    bot.apply_regular_post_receipt(receipt, lines, images, state)

    assert state == first_state
    assert state["original_regular_posts_since_generated_image"] == 2
    assert state["last_regular_image_filename"] == basename
    assert state["next_quote_post_epoch"] == receipt["next_quote_post_epoch"]
    assert lines == set(receipt["quote_history_after"])
    assert images == {basename}


def test_current_meme_attempt_binds_image_summary_for_restart_recovery() -> None:
    summary = "A poster with a concise political slogan."
    attempt = bot.build_main_post_attempt(
        lane="daily_meme",
        text=bot.MEME_POST_TEXT,
        media_ids=["media-1"],
        made_with_ai=False,
        selected_identity={"meme_basename": "001_meme.png"},
        recovery_plan={
            "next_schedule_mode": "fallback",
            "meme_schedule_version": 2,
            "fallback_hour": 16,
            "fallback_minute": 0,
            "image_summary": summary,
            "schedule_timezone": bot.MAIN_POST_SCHEDULE_TIMEZONE,
        },
        attempt_epoch=1_800_000_000,
    )
    assert attempt["schema_version"] == 5
    attempting = {**attempt, "lifecycle_state": "attempting"}
    with pytest.raises(RuntimeError):
        bot.build_confirmed_pending_schedule_receipt(
            attempting,
            post_id="970001",
            confirmation_epoch=1_800_000_100,
        )
    pending = bot.build_confirmed_pending_schedule_receipt(
        attempting,
        post_id="970001",
        confirmation_epoch=1_800_000_100,
        image_summary=summary,
    )
    receipt = bot.materialize_bound_meme_schedule_receipt(pending)
    assert receipt["image_summary"] == summary


def test_existing_valid_receipt_is_reconciled_before_next_regular_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, lines_used_file, _images_used_file, receipt_file, lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    old_hash = bot.quote_text_hash("Old quote.")
    receipt = {
        "schema_version": 1,
        "post_id": "940001",
        "quote_hash": old_hash,
        "line_no": 0,
        "source_line_number": 1,
        "text": "Old quote.",
        "image_basename": "t01.jpg",
        "quote_post_epoch": 1_800_000_000,
        "next_quote_post_epoch": 1_800_007_200,
    }
    bot.atomic_write_json(receipt_file, receipt)
    lines_file.write_text("Old quote.\nGood quote.\n", encoding="utf-8")
    monkeypatch.setattr(bot, "load_quote_analysis", lambda: quote_analysis_for_lines(["Old quote.", "Good quote."]))

    bot.post_random_quote(lines_used, images_used, state)

    assert not receipt_file.exists()
    assert old_hash in json.loads(lines_used_file.read_text(encoding="utf-8"))
    assert receipt_file.read_text(encoding="utf-8") if receipt_file.exists() else True


def test_receipt_is_not_removed_when_protected_durable_save_fails(
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
    monkeypatch.setattr(bot, "save_image_used_basenames", lambda *args, **kwargs: (_ for _ in ()).throw(OSError("image save failed")))

    with pytest.raises(OSError):
        bot.reconcile_regular_post_receipt(lines_used, images_used, state)

    assert receipt_file.exists()


def test_protected_durable_saves_complete_before_receipt_removal(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    calls: list[str] = []
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
    monkeypatch.setattr(bot, "save_quote_used_hashes", lambda *args, **kwargs: calls.append("quote"))
    monkeypatch.setattr(bot, "save_image_used_basenames", lambda *args, **kwargs: calls.append("image"))
    monkeypatch.setattr(bot, "save_state", lambda *args, **kwargs: calls.append("state"))
    monkeypatch.setattr(
        bot,
        "remove_regular_post_receipt",
        lambda *_args, **_kwargs: calls.append("remove"),
    )

    assert bot.reconcile_regular_post_receipt(lines_used, images_used, state) is True
    assert calls == ["quote", "image", "state", "remove"]


def test_malformed_receipt_blocks_new_regular_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    receipt_file.write_text("{bad json", encoding="utf-8")

    with pytest.raises(bot.InvalidRegularPostReceipt):
        bot.post_random_quote(lines_used, images_used, state)

    assert receipt_file.exists()
    assert lines_used == set()
    assert images_used == set()


@pytest.mark.parametrize(
    "patch",
    [
        {"post_id": ""},
        {"post_id": "not-a-number"},
        {"quote_hash": "abc"},
        {"quote_post_epoch": "soon"},
        {"quote_post_epoch": 1},
        {"next_quote_post_epoch": "soon"},
        {"next_quote_post_epoch": 1_800_000_000},
        {"next_quote_post_epoch": 1_799_999_999},
        {"text": ""},
        {"text": None},
        {"image_basename": "../t01.jpg"},
        {"text": "Different text"},
        {"next_meme_post_epoch": 1_799_999_999, "next_meme_schedule_date": "2027-01-15", "next_meme_schedule_mode": "fallback"},
        {"next_meme_post_epoch": 1_800_086_400, "next_meme_schedule_date": "2000-01-01", "next_meme_schedule_mode": "fallback"},
        {"next_meme_post_epoch": 1_800_086_400, "next_meme_schedule_date": "2027-01-16", "next_meme_schedule_mode": "surprise"},
        {
            "next_meme_post_epoch": 1_800_000_600,
            "next_meme_schedule_date": "2027-01-15",
            "next_meme_schedule_mode": "after_first_quote_after_midday",
            "meme_anchor_quote_post_epoch": 1,
        },
        {
            "next_meme_post_epoch": 1_800_000_600,
            "next_meme_schedule_date": "2027-01-15",
            "next_meme_schedule_mode": "after_first_quote_after_midday",
            "meme_anchor_quote_post_epoch": 0,
            "meme_schedule_changed_by_quote": False,
        },
        {
            "next_meme_post_epoch": 1_800_000_000,
            "next_meme_schedule_date": bot.epoch_date_str(1_800_000_000),
            "next_meme_schedule_mode": "after_first_quote_after_midday",
            "meme_anchor_quote_post_epoch": 1_800_000_000,
            "meme_schedule_changed_by_quote": False,
        },
    ],
)
def test_semantically_invalid_receipts_block_main_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    patch: dict,
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
    receipt.update(patch)
    bot.atomic_write_json(receipt_file, receipt)

    with pytest.raises(bot.InvalidRegularPostReceipt):
        bot.post_random_quote(lines_used, images_used, state)


def test_receipt_validators_reject_boolean_schema_versions_and_fractional_epochs() -> None:
    regular = valid_regular_receipt()
    assert bot.regular_post_receipt_is_semantically_valid({**regular, "schema_version": True}) is False
    assert bot.regular_post_receipt_is_semantically_valid({**regular, "quote_post_epoch": 1_800_000_000.5}) is False

    meme = {
        "schema_version": 1,
        "post_id": "950001",
        "meme_basename": "meme.png",
        "meme_post_epoch": 1_800_000_000,
        "next_meme_post_epoch": 1_800_086_400,
        "next_meme_schedule_mode": "fallback",
    }
    assert bot.meme_post_receipt_is_semantically_valid({**meme, "schema_version": True}) is False
    assert bot.meme_post_receipt_is_semantically_valid({**meme, "meme_post_epoch": 1_800_000_000.5}) is False

    reply = {
        "schema_version": 1,
        "target_id": "100",
        "reply_post_id": "900000",
        "author_id": "200",
        "reply_epoch": 1_800_000_000,
        "candidate_source": "mention",
        "reply_text": "A reply.",
    }
    assert bot.confirmed_reply_receipt_is_semantically_valid({**reply, "schema_version": True}) is False
    assert bot.confirmed_reply_receipt_is_semantically_valid({**reply, "reply_epoch": 1_800_000_000.5}) is False


@pytest.mark.parametrize(
    "patch",
    [
        {"next_meme_post_epoch": "banana"},
        {
            "next_meme_post_epoch": 1_800_086_400,
            "next_meme_schedule_mode": "fallback",
            "next_meme_schedule_date": bot.epoch_date_str(1_800_086_400),
            "meme_anchor_quote_post_epoch": "banana",
            "meme_schedule_changed_by_quote": False,
        },
        {
            "next_meme_post_epoch": 1_800_086_400,
            "next_meme_schedule_mode": "fallback",
            "next_meme_schedule_date": bot.epoch_date_str(1_800_086_400),
            "meme_anchor_quote_post_epoch": 0,
            "meme_schedule_changed_by_quote": "false",
        },
    ],
)
def test_regular_receipt_malformed_optional_fields_are_invalid_not_exceptions(patch: dict) -> None:
    receipt = valid_regular_receipt(**patch)
    assert bot.regular_post_receipt_is_semantically_valid(receipt) is False


def test_valid_receipt_reconciles_idempotently(
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
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)
    monkeypatch.setattr(
        bot,
        "historical_context_reply",
        {**bot.historical_context_reply, "enabled": True},
    )
    context_calls = []
    def context(**kwargs):
        assert not receipt_file.exists()
        assert bot.HISTORICAL_CONTEXT_REPLY_OUTBOX_FILE.exists()
        context_calls.append(kwargs)
        return {"status": "completed", "reply_post_id": "960001"}
    monkeypatch.setattr(bot, "maybe_post_historical_context_reply", context)
    emissions: list[dict[str, object]] = []

    def observe_recovered_root(**fields: object) -> None:
        assert not receipt_file.exists()
        emissions.append(dict(fields))

    monkeypatch.setattr(bot, "emit_account_root_posted", observe_recovered_root)
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **_kwargs: pytest.fail("receipt recovery must not issue another X request"),
    )

    assert bot.reconcile_regular_post_receipt(lines_used, images_used, state) is True
    assert bot.reconcile_regular_post_receipt(lines_used, images_used, state) is False
    assert len(context_calls) == 1
    assert len(emissions) == 1
    assert emissions[0]["lane"] == "quote_image"
    assert emissions[0]["post_id"] == "950001"
    assert emissions[0]["public_text"] == "Good quote."
    assert lines_used == {bot.quote_text_hash("Good quote.")}
    assert images_used == {"t01.jpg"}


def test_old_receipt_does_not_move_main_post_state_behind_newer_meme(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _lines_used, _images_used, _state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    receipt = {
        "schema_version": 1,
        "post_id": "940002",
        "quote_hash": bot.quote_text_hash("Good quote."),
        "image_basename": "t01.jpg",
        "quote_post_epoch": 1_800_000_000,
        "next_quote_post_epoch": 1_800_007_200,
        "text": "Good quote.",
    }
    bot.atomic_write_json(receipt_file, receipt)
    state = {
        "last_main_post_id": "960001",
        "last_meme_post_epoch": 1_800_000_200,
        "next_meme_post_epoch": 1_800_100_000,
        "next_meme_schedule_mode": "fallback_future",
        "next_meme_schedule_date": "2027-01-18",
    }
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: None)

    assert bot.reconcile_regular_post_receipt(set(), set(), state) is True
    assert state["last_main_post_id"] == "960001"
    assert state["next_meme_post_epoch"] == 1_800_100_000
    assert state["next_meme_schedule_mode"] == "fallback_future"


def test_post_next_meme_blocked_by_unresolved_regular_post_receipt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _lines_used, _images_used, _state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    bot.atomic_write_json(
        receipt_file,
        {
            "schema_version": 1,
            "post_id": "950001",
            "quote_hash": bot.quote_text_hash("Good quote."),
            "image_basename": "t01.jpg",
            "quote_post_epoch": 1_800_000_000,
            "next_quote_post_epoch": 1_800_007_200,
            "text": "Good quote.",
        },
    )

    with pytest.raises(bot.UnresolvedRegularPostReceipt):
        bot.post_next_meme({})


def test_regular_receipt_durable_write_uses_fsync(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    receipt_file = tmp_path / "regular_post_receipt.json"
    calls: list[str] = []
    monkeypatch.setattr(bot, "REGULAR_POST_RECEIPT_FILE", receipt_file)
    monkeypatch.setattr(bot.os, "fsync", lambda fd: calls.append("fsync"))

    bot.write_regular_post_receipt(
        valid_regular_receipt()
    )

    assert receipt_file.exists()
    assert len(calls) >= 2


def test_receipt_writers_self_validate_before_durable_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    regular_receipt_file = tmp_path / "regular_post_receipt.json"
    meme_receipt_file = tmp_path / "meme_post_receipt.json"
    monkeypatch.setattr(bot, "REGULAR_POST_RECEIPT_FILE", regular_receipt_file)
    monkeypatch.setattr(bot, "MEME_POST_RECEIPT_FILE", meme_receipt_file)

    with pytest.raises(RuntimeError, match="semantic validation"):
        bot.write_regular_post_receipt(valid_regular_receipt(next_meme_post_epoch="banana"))
    assert not regular_receipt_file.exists()

    valid_regular_forms = [
        valid_regular_receipt(),
        valid_regular_receipt_v2(),
        valid_regular_receipt(
            next_meme_post_epoch=1_800_086_400,
            next_meme_schedule_mode="fallback",
            next_meme_schedule_date=bot.epoch_date_str(1_800_086_400),
            meme_anchor_quote_post_epoch=0,
            meme_schedule_changed_by_quote=False,
        ),
        valid_regular_receipt(
            next_meme_post_epoch=1_800_003_600,
            next_meme_schedule_mode="after_first_quote_after_midday",
            next_meme_schedule_date=bot.epoch_date_str(1_800_000_000),
            meme_anchor_quote_post_epoch=1_800_000_000,
            meme_schedule_changed_by_quote=True,
        ),
    ]
    for receipt in valid_regular_forms:
        assert bot.regular_post_receipt_is_semantically_valid(receipt)
        bot.write_regular_post_receipt(receipt)
        assert bot.load_regular_post_receipt()[0] == "valid"
        regular_receipt_file.unlink()

    for bad_schema in [{}, {"schema_version": 3}, {"schema_version": "1"}]:
        receipt = valid_regular_receipt()
        receipt.update(bad_schema)
        if "schema_version" not in bad_schema:
            receipt.pop("schema_version", None)
        assert not bot.regular_post_receipt_is_semantically_valid(receipt)
        with pytest.raises(RuntimeError, match="semantic validation"):
            bot.write_regular_post_receipt(receipt)
        assert not regular_receipt_file.exists()

    with pytest.raises(RuntimeError, match="semantic validation"):
        bot.write_meme_post_receipt(
            {
                "schema_version": 1,
                "post_id": "970001",
                "meme_basename": "001_meme.png",
                "meme_post_epoch": 1_800_000_000,
                "next_meme_post_epoch": "banana",
            }
    )
    assert not meme_receipt_file.exists()

    valid_meme_receipt = {
        "schema_version": 1,
        "post_id": "970001",
        "meme_basename": "001_meme.png",
        "meme_post_epoch": 1_800_000_000,
        "next_meme_post_epoch": 1_800_086_400,
        "next_meme_schedule_mode": "fallback",
    }
    assert bot.meme_post_receipt_is_semantically_valid(valid_meme_receipt)
    bot.write_meme_post_receipt(valid_meme_receipt)
    assert bot.load_meme_post_receipt()[0] == "valid"
    meme_receipt_file.unlink()

    for bad_schema in [{}, {"schema_version": 2}, {"schema_version": "1"}]:
        receipt = dict(valid_meme_receipt)
        receipt.update(bad_schema)
        if "schema_version" not in bad_schema:
            receipt.pop("schema_version", None)
        assert not bot.meme_post_receipt_is_semantically_valid(receipt)
        with pytest.raises(RuntimeError, match="semantic validation"):
            bot.write_meme_post_receipt(receipt)
        assert not meme_receipt_file.exists()


def test_regular_receipt_replay_does_not_create_second_post(
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
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: pytest.fail("create_post should not be called after receipt replay"))

    bot.post_random_quote(lines_used, images_used, state)

    assert not receipt_file.exists()
    assert state["next_quote_post_epoch"] == 1_800_007_200


def test_regular_post_state_failure_leaves_receipt_with_future_quote_schedule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    monkeypatch.setattr(
        bot.random,
        "randint",
        lambda low, high: 7200 if low == bot.POST_SLEEP_MIN else low,
    )
    monkeypatch.setattr(bot, "save_state", lambda state, **kwargs: (_ for _ in ()).throw(OSError("state failed")))

    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_random_quote(lines_used, images_used, state)

    receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
    assert receipt["next_quote_post_epoch"] == 1_800_007_200
    assert state["next_quote_post_epoch"] == 1_800_007_200


def test_regular_receipt_removal_failure_keeps_future_quote_schedule(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, _receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    monkeypatch.setattr(
        bot.random,
        "randint",
        lambda low, high: 7200 if low == bot.POST_SLEEP_MIN else low,
    )
    monkeypatch.setattr(
        bot,
        "remove_regular_post_receipt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("remove failed")),
    )

    with pytest.raises(bot.ConfirmedPostLocalPersistenceError):
        bot.post_random_quote(lines_used, images_used, state)

    assert state["next_quote_post_epoch"] == 1_800_007_200
    assert state["next_quote_post_epoch"] > state["last_quote_post_epoch"]


def test_unresolved_meme_receipt_blocks_another_main_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, _receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    bot.atomic_write_json(
        bot.MEME_POST_RECEIPT_FILE,
        {
            "schema_version": 1,
            "post_id": "970001",
            "meme_basename": "001_meme.png",
            "meme_post_epoch": 1_800_000_000,
            "next_meme_post_epoch": 1_800_086_400,
        },
    )

    bot.post_random_quote(lines_used, images_used, state)
    assert state["last_main_post_id"] == "950001"


def test_simultaneous_regular_and_meme_receipts_block_reconciliation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    bot.atomic_write_json(
        receipt_file,
        {
            "schema_version": 1,
            "post_id": "950001",
            "quote_hash": bot.quote_text_hash("Good quote."),
            "image_basename": "t01.jpg",
            "quote_post_epoch": 1_800_000_000,
            "next_quote_post_epoch": 1_800_007_200,
            "text": "Good quote.",
        },
    )
    bot.atomic_write_json(
        bot.MEME_POST_RECEIPT_FILE,
        {
            "schema_version": 1,
            "post_id": "970001",
            "meme_basename": "001_meme.png",
            "meme_post_epoch": 1_800_000_000,
            "next_meme_post_epoch": 1_800_086_400,
        },
    )

    with pytest.raises(bot.InvalidRegularPostReceipt):
        bot.reconcile_main_post_receipts(lines_used, images_used, state)


def test_simultaneous_legacy_confirmed_receipts_block_auxiliary_provider_work(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    regular = valid_regular_receipt()
    meme = {
        "schema_version": 1,
        "post_id": "970001",
        "meme_basename": "001_meme.png",
        "meme_post_epoch": 1_800_000_000,
        "next_meme_post_epoch": 1_800_086_400,
    }
    reply = unit_confirmed_reply_receipt()
    bot.atomic_write_json(bot.REGULAR_POST_RECEIPT_FILE, regular, durable=True)
    bot.atomic_write_json(bot.MEME_POST_RECEIPT_FILE, meme, durable=True)
    bot.atomic_write_json(bot.CONFIRMED_REPLY_RECEIPT_FILE, reply, durable=True)
    monkeypatch.setattr(
        bot.requests,
        "request",
        lambda *_args, **_kwargs: pytest.fail(
            "unresolved legacy receipts must block auxiliary transport"
        ),
    )

    assert bot.load_regular_post_receipt()[0] == "valid"
    assert bot.load_meme_post_receipt()[0] == "valid"
    assert bot.load_confirmed_reply_receipt()[0] == "valid"
    assert bot.ambiguous_remote_post_is_blocking() is True
    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.require_remote_operation_unpaused("auxiliary provider request")


def test_fully_reconciled_receipt_namespace_does_not_create_false_barrier() -> None:
    assert bot.load_regular_post_receipt() == ("absent", None)
    assert bot.load_meme_post_receipt() == ("absent", None)
    assert bot.load_confirmed_reply_receipt() == ("absent", None)
    assert bot.ambiguous_remote_post_is_blocking() is False


def test_post_next_meme_rejects_simultaneous_receipts_without_removing_either(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _lines_used, _images_used, _state, _lines_used_file, _images_used_file, receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    bot.atomic_write_json(
        receipt_file,
        {
            "schema_version": 1,
            "post_id": "950001",
            "quote_hash": bot.quote_text_hash("Good quote."),
            "image_basename": "t01.jpg",
            "quote_post_epoch": 1_800_000_000,
            "next_quote_post_epoch": 1_800_007_200,
            "text": "Good quote.",
        },
    )
    bot.atomic_write_json(
        bot.MEME_POST_RECEIPT_FILE,
        {
            "schema_version": 1,
            "post_id": "970001",
            "meme_basename": "001_meme.png",
            "meme_post_epoch": 1_800_000_000,
            "next_meme_post_epoch": 1_800_086_400,
        },
    )

    with pytest.raises(bot.AmbiguousRemotePostOutcome):
        bot.post_next_meme({})

    assert receipt_file.exists()
    assert bot.MEME_POST_RECEIPT_FILE.exists()


def test_invalid_meme_receipt_blocks_main_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, _receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    bot.MEME_POST_RECEIPT_FILE.write_text("{bad json", encoding="utf-8")

    with pytest.raises(bot.InvalidMemePostReceipt):
        bot.post_random_quote(lines_used, images_used, state)


@pytest.mark.parametrize("next_epoch", [1_800_000_000, 1_799_999_999])
def test_meme_receipt_requires_future_next_epoch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    next_epoch: int,
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
            "next_meme_post_epoch": next_epoch,
        },
    )

    with pytest.raises(bot.InvalidMemePostReceipt):
        bot.reconcile_meme_post_receipt({})
