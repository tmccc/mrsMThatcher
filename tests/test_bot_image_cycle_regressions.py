"""Regression tests for bot image cycle."""

from __future__ import annotations

import hashlib
import logging
from datetime import datetime
from pathlib import Path

import pytest

import mrs_bot_asset_metadata as asset_metadata
import mrs_bot_image_selection as image_selection
import mrs_bot_quote_candidates as quote_candidates
import mrs_bot_used_history as used_history
from tests.helpers.quote_candidate_overrides import patch_completed_research_quotes

from tests.helpers.bot_runtime import bot
from tests.helpers.bot_fixtures import (
    patch_main_post_handoff,
    isolate_bot_runtime,
    quote_analysis_for_lines,
    image_analysis_for_paths,
    configure_simple_quote_post,
    mock_confirmed_main_post,
    install_receipt_bound_x_request_stub,
    write_image_analysis,
)


pytestmark = pytest.mark.allow_loopback_network


def patch_metadata(monkeypatch, name, callback):
    monkeypatch.setattr(asset_metadata.AssetMetadata, name, lambda _owner, *args, **kwargs: callback(*args, **kwargs))


def test_post_random_quote_retries_alternate_quote_when_first_has_no_image_match(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict[str, object]]] = []
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_path = image_dir / "t01.jpg"
    image_path.write_bytes(b"fake")
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Bad visual quote.\nGood visual quote.\n", encoding="utf-8")
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "LINES_USED_FILE", tmp_path / "lines_used.json")
    monkeypatch.setattr(bot, "IMAGES_USED_FILE", tmp_path / "images_used.json")
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))
    monkeypatch.setattr(bot.random, "uniform", lambda low, high: low)
    monkeypatch.setattr(bot, "upload_media", lambda path, **_kwargs: "media-1")
    patch_main_post_handoff(monkeypatch, lambda _attempt, _authority: None)
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: mock_confirmed_main_post(
            kwargs, {"data": {"id": "950001"}}
        ),
    )
    monkeypatch.setattr(bot, "maybe_schedule_meme_after_quote_post", lambda state, quote_post_epoch=None, **kwargs: None)
    monkeypatch.setattr(bot, "cache_tweet", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "record_recent_own_post", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "log_event", lambda name, **fields: events.append((name, fields)))
    patch_metadata(
        monkeypatch,
        "load_quote",
        lambda: quote_analysis_for_lines(
            ["Bad visual quote.", "Good visual quote."],
            {
                0: {
                        "archive_image_preferences": {"strong_visual_mismatches": ["formal portrait"]},
                        "seasonality": {"hard_exclude_outside_windows": False, "preferred_windows": [], "relevance": "none"},
                },
                1: {
                        "archive_image_preferences": {},
                        "seasonality": {"hard_exclude_outside_windows": False, "preferred_windows": [], "relevance": "none"},
                },
            },
        ),
    )
    patch_metadata(
        monkeypatch,
        "load_image",
        lambda: image_analysis_for_paths(
            [image_path],
            {
                "t01.jpg": {
                        "description": "Formal portrait in a studio",
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
    state: dict = {}
    bot.post_random_quote(lines_used, images_used, state)

    assert lines_used == {bot.quote_text_hash("Good visual quote.")}
    assert images_used == {"t01.jpg"}
    assert state["last_regular_image_filename"] == "t01.jpg"
    posted = next(fields for name, fields in events if name == "main_post_posted")
    assert posted["quote_hash"] == bot.quote_text_hash("Good visual quote.")
    assert posted["image_hash"] == hashlib.sha256(b"fake").hexdigest()
    root_event = next(
        fields for name, fields in events if name == "account_root_posted"
    )
    assert [name for name, _fields in events].index("main_post_posted") < [
        name for name, _fields in events
    ].index("account_root_posted")
    assert root_event["event_version"] == 1
    assert root_event["lane"] == "quote_image"
    assert root_event["post_id"] == "950001"
    assert root_event["root_post_id"] == "950001"
    assert root_event["conversation_id"] == "950001"
    assert root_event["public_text"] == "Good visual quote."
    assert root_event["visible_text"] == "Good visual quote."
    assert root_event["visible_text_source"] == "public_text"
    assert root_event["publication_authority"] == "confirmed_transport"
    assert not {
        "author_id",
        "account_id",
        "api_key",
        "token",
        "secret",
    } & set(root_event)


def configure_image_cycle_post(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    image_analyses: dict[str, dict],
    quote_analyses: dict[int, dict],
    quotes: list[str] | None = None,
) -> tuple[set[str], set[str], dict, dict[str, Path]]:
    quotes = quotes or ["Quote A.", "Quote B."]
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    image_paths: dict[str, Path] = {}
    for basename in image_analyses:
        path = image_dir / basename
        path.write_bytes(f"original-{basename}".encode("utf-8"))
        image_paths[basename] = path

    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("\n".join(quotes) + "\n", encoding="utf-8")
    analysis_path = tmp_path / "image_analysis.json"
    write_image_analysis(analysis_path, image_analysis_for_paths(list(image_paths.values()), image_analyses))
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "IMAGE_ANALYSIS_FILE", analysis_path)
    monkeypatch.setattr(bot, "LINES_USED_FILE", tmp_path / "lines_used.json")
    monkeypatch.setattr(bot, "IMAGES_USED_FILE", tmp_path / "images_used.json")
    monkeypatch.setattr(bot, "REGULAR_POST_RECEIPT_FILE", tmp_path / "regular_post_receipt.json")
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))
    monkeypatch.setattr(bot, "now_epoch", lambda: 1_800_000_000)
    monkeypatch.setattr(bot.random, "uniform", lambda low, high: low)
    monkeypatch.setattr(bot.random, "randint", lambda low, high: low)
    monkeypatch.setattr(bot, "upload_media", lambda path, **_kwargs: "media-1")
    patch_main_post_handoff(monkeypatch, lambda _attempt, _authority: None)
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: mock_confirmed_main_post(
            kwargs, {"data": {"id": "950001"}}
        ),
    )
    monkeypatch.setattr(bot, "cache_tweet", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "record_recent_own_post", lambda *args, **kwargs: None)
    monkeypatch.setattr(bot, "log_event", lambda *args, **kwargs: None)
    patch_metadata(monkeypatch, "load_quote", lambda: quote_analysis_for_lines(quotes, quote_analyses))
    return set(), set(), {}, image_paths


def capture_create_post_calls(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    calls: list[dict] = []

    def fake_create_post(**kwargs: object) -> dict:
        calls.append(dict(kwargs))
        return mock_confirmed_main_post(
            kwargs,
            {"data": {"id": "950001"}},
        )

    monkeypatch.setattr(bot, "create_post", fake_create_post)
    return calls


def portrait_analysis() -> dict:
    return {
        "description": "Formal portrait in a studio",
        "pairing": {},
        "themes": [],
        "tone": [],
        "visual_energy": "low",
        "quality": {},
        "seasonality": {"avoid_outside_season_or_occasion": False},
    }


def crowd_scene_analysis() -> dict:
    return {
        "description": "A large crowd scene outside a building",
        "pairing": {},
        "themes": [],
        "tone": [],
        "visual_energy": "low",
        "quality": {},
        "seasonality": {"avoid_outside_season_or_occasion": False},
    }


def quote_rejecting_crowd_scenes() -> dict:
    return {
        "archive_image_preferences": {"strong_visual_mismatches": ["crowd scene"]},
        "seasonality": {"hard_exclude_outside_windows": False, "preferred_windows": [], "relevance": "none"},
        "primary_topics": [],
        "secondary_topics": [],
        "tone": [],
        "visual_energy": "low",
    }


def test_post_random_quote_recovers_when_remaining_cycle_image_cannot_pair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secondary_name = "t99.jpg"
    lines_used, images_used, state, _image_paths = configure_image_cycle_post(
        tmp_path,
        monkeypatch,
        image_analyses={'t01.jpg': portrait_analysis(), secondary_name: crowd_scene_analysis()},
        quote_analyses={0: quote_rejecting_crowd_scenes(), 1: quote_rejecting_crowd_scenes()},
    )
    images_used.add("t01.jpg")
    caplog.set_level(logging.INFO, logger=bot.log.name)

    bot.post_random_quote(lines_used, images_used, state)

    assert images_used == {"t01.jpg"}
    assert state["last_regular_image_filename"] == "t01.jpg"
    assert lines_used == {bot.quote_text_hash("Quote A.")}
    assert "resetting image cycle and retrying once" in caplog.text
    assert "Regular quote/image pairing succeeded after image-cycle recovery" in caplog.text
    assert "last image permitted" not in caplog.text


@pytest.mark.parametrize("seasonal_tail", [False, True])
def test_image_pair_retry_recovers_unused_quote_without_resetting_quote_history(
    tmp_path, monkeypatch, caplog, seasonal_tail,
):
    """Recover an unused quote's image before repeating a posted quotation."""
    texts = ["Already posted quotation.", "Still unused quotation."]
    analyses = {0: {}, 1: quote_rejecting_crowd_scenes()}
    if seasonal_tail:
        texts.append("Christmas quotation.")
        analyses[2] = {"seasonality": {
            "hard_exclude_outside_windows": True,
            "preferred_windows": [{"start_mm_dd": "12-10", "end_mm_dd": "12-28"}],
        }}
    used_hash, unused_hash = map(bot.quote_text_hash, texts[:2])
    secondary_name = "t99.jpg"
    used, images_used, state, _ = configure_image_cycle_post(
        tmp_path, monkeypatch,
        image_analyses={'t01.jpg': portrait_analysis(), secondary_name: crowd_scene_analysis()},
        quote_analyses=analyses, quotes=texts,
    )
    used.add(used_hash)
    images_used.add("t01.jpg")
    patch_completed_research_quotes(monkeypatch, bot, lambda: set(map(bot.quote_text_hash, texts)))
    caplog.set_level(logging.INFO, logger=bot.log.name)
    calls = capture_create_post_calls(monkeypatch)

    bot.post_random_quote(used, images_used, state)

    assert [item["text"] for item in calls] == [texts[1]]
    assert used == {used_hash, unused_hash}
    assert bot.load_used_set(bot.LINES_USED_FILE) == used
    assert images_used == {"t01.jpg"}
    assert state["last_regular_image_filename"] == "t01.jpg"
    assert "Regular quote/image pairing succeeded after image-cycle recovery" in caplog.text
    assert "resetting quote cycle" not in caplog.text


def test_last_image_boundary_fallback_recovers_two_image_cycle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secondary_name = "t99.jpg"
    lines_used, images_used, state, _image_paths = configure_image_cycle_post(
        tmp_path,
        monkeypatch,
        image_analyses={'t01.jpg': portrait_analysis(), secondary_name: crowd_scene_analysis()},
        quote_analyses={0: quote_rejecting_crowd_scenes()},
        quotes=["Only quote."],
    )
    images_used.add("t01.jpg")
    state["last_regular_image_filename"] = "t01.jpg"
    caplog.set_level(logging.INFO, logger=bot.log.name)

    bot.post_random_quote(lines_used, images_used, state)

    assert state["last_regular_image_filename"] == "t01.jpg"
    assert images_used == {"t01.jpg"}
    assert lines_used == {bot.quote_text_hash("Only quote.")}
    assert "retrying once with last image permitted" in caplog.text
    assert "Regular quote/image pairing succeeded after permitting last regular image" in caplog.text


def test_last_image_boundary_avoidance_wins_when_non_last_image_is_viable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secondary_name = "t99.jpg"
    lines_used, images_used, state, _image_paths = configure_image_cycle_post(
        tmp_path,
        monkeypatch,
        image_analyses={'t01.jpg': portrait_analysis(), 't02.jpg': portrait_analysis(), secondary_name: crowd_scene_analysis()},
        quote_analyses={0: quote_rejecting_crowd_scenes()},
        quotes=["Only quote."],
    )
    images_used.update({"t01.jpg", "t02.jpg"})
    state["last_regular_image_filename"] = "t01.jpg"
    caplog.set_level(logging.INFO, logger=bot.log.name)

    bot.post_random_quote(lines_used, images_used, state)

    assert state["last_regular_image_filename"] == "t02.jpg"
    assert images_used == {"t02.jpg"}
    assert "Temporarily excluded last regular image at forced eligible-cycle boundary: t01.jpg" in caplog.text
    assert "retrying once with last image permitted" not in caplog.text


def test_last_image_boundary_fallback_fails_safely_without_fourth_pass(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secondary_name = "t99.jpg"
    lines_used, images_used, state, _image_paths = configure_image_cycle_post(
        tmp_path,
        monkeypatch,
        image_analyses={'t01.jpg': crowd_scene_analysis(), secondary_name: crowd_scene_analysis()},
        quote_analyses={0: quote_rejecting_crowd_scenes()},
        quotes=["Only quote."],
    )
    images_used.add("t01.jpg")
    state["last_regular_image_filename"] = "t01.jpg"
    original_lines = set(lines_used)
    original_images = set(images_used)
    monkeypatch.setattr(bot, "upload_media", lambda path, **_kwargs: pytest.fail("upload_media should not be called"))
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: pytest.fail("create_post should not be called"))
    caplog.set_level(logging.INFO, logger=bot.log.name)

    with pytest.raises(RuntimeError, match="No eligible regular quote/image pair found"):
        bot.post_random_quote(lines_used, images_used, state)

    assert lines_used == original_lines
    assert images_used == original_images
    assert caplog.text.count("Forcing eligible image cycle reset for regular quote/image pairing recovery") == 2
    assert caplog.text.count("retrying once with last image permitted") == 1
    assert "No viable regular quote/image pair found after final last-image recovery fallback" in caplog.text


def test_no_last_image_does_not_trigger_last_image_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secondary_name = "t99.jpg"
    lines_used, images_used, state, _image_paths = configure_image_cycle_post(
        tmp_path,
        monkeypatch,
        image_analyses={'t01.jpg': portrait_analysis(), secondary_name: crowd_scene_analysis()},
        quote_analyses={0: quote_rejecting_crowd_scenes()},
        quotes=["Only quote."],
    )
    images_used.add("t01.jpg")
    caplog.set_level(logging.INFO, logger=bot.log.name)

    bot.post_random_quote(lines_used, images_used, state)

    assert state["last_regular_image_filename"] == "t01.jpg"
    assert "retrying once with last image permitted" not in caplog.text


def test_last_image_boundary_fallback_can_reuse_previous_image(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    good_image = "t98.jpg"
    bad_image = "t99.jpg"
    lines_used, images_used, state, _image_paths = configure_image_cycle_post(
        tmp_path,
        monkeypatch,
        image_analyses={good_image: portrait_analysis(), bad_image: crowd_scene_analysis()},
        quote_analyses={0: quote_rejecting_crowd_scenes()},
        quotes=["Only quote."],
    )
    images_used.add(good_image)
    state["last_regular_image_filename"] = good_image
    create_calls = capture_create_post_calls(monkeypatch)
    caplog.set_level(logging.INFO, logger=bot.log.name)

    bot.post_random_quote(lines_used, images_used, state)

    assert state["last_regular_image_filename"] == good_image
    assert images_used == {good_image}
    assert len(create_calls) == 1
    assert create_calls[0]["made_with_ai"] is False
    assert "retrying once with last image permitted" in caplog.text
    assert "REGULAR_IMAGE_SELECTED source=original" in caplog.text


def test_post_random_quote_image_cycle_recovery_fails_safely_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secondary_name = "t99.jpg"
    lines_used, images_used, _state, _image_paths = configure_image_cycle_post(
        tmp_path,
        monkeypatch,
        image_analyses={'t01.jpg': crowd_scene_analysis(), secondary_name: crowd_scene_analysis()},
        quote_analyses={0: quote_rejecting_crowd_scenes(), 1: quote_rejecting_crowd_scenes()},
    )
    images_used.add("t01.jpg")
    original_lines = set(lines_used)
    original_images = set(images_used)
    monkeypatch.setattr(bot, "upload_media", lambda path, **_kwargs: pytest.fail("upload_media should not be called"))
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: pytest.fail("create_post should not be called"))
    caplog.set_level(logging.INFO, logger=bot.log.name)

    with pytest.raises(RuntimeError, match="No eligible regular quote/image pair found"):
        bot.post_random_quote(lines_used, images_used, {})

    assert lines_used == original_lines
    assert images_used == original_images
    assert caplog.text.count("resetting image cycle and retrying once") == 1
    assert "No viable regular quote/image pair found after image-cycle recovery" in caplog.text


def test_forced_image_cycle_recovery_respects_last_image_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    last_image = "t98.jpg"
    bad_image = "t99.jpg"
    lines_used, images_used, state, _image_paths = configure_image_cycle_post(
        tmp_path,
        monkeypatch,
        image_analyses={'t01.jpg': portrait_analysis(), last_image: portrait_analysis(), bad_image: crowd_scene_analysis()},
        quote_analyses={0: quote_rejecting_crowd_scenes(), 1: quote_rejecting_crowd_scenes()},
    )
    images_used.update({"t01.jpg", last_image})
    state["last_regular_image_filename"] = last_image

    bot.post_random_quote(lines_used, images_used, state)

    assert state["last_regular_image_filename"] == "t01.jpg"
    assert last_image not in images_used
    assert "t01.jpg" in images_used


def test_successful_current_cycle_pair_does_not_force_image_cycle_reset(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secondary_name = "t99.jpg"
    lines_used, images_used, state, _image_paths = configure_image_cycle_post(
        tmp_path,
        monkeypatch,
        image_analyses={'t01.jpg': portrait_analysis(), secondary_name: portrait_analysis()},
        quote_analyses={0: quote_rejecting_crowd_scenes(), 1: quote_rejecting_crowd_scenes()},
    )
    images_used.add("t01.jpg")
    caplog.set_level(logging.INFO, logger=bot.log.name)

    bot.post_random_quote(lines_used, images_used, state)

    assert state["last_regular_image_filename"] == secondary_name
    assert "resetting image cycle and retrying once" not in caplog.text


def test_global_image_failure_does_not_trigger_image_cycle_recovery(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(bot, "reconcile_main_post_receipts", lambda *args, **kwargs: {"regular": False, "meme": False})
    monkeypatch.setattr(used_history.UsedHistory, "quote_used_history_has_legacy_indices", lambda _owner, used: False)
    monkeypatch.setattr(
        quote_candidates.QuoteCandidates,
        "choose",
        lambda _owner, *args, **kwargs: {
            "line_no": 0,
            "quote_hash": bot.quote_text_hash("Good quote."),
            "text": "Good quote.",
            "analysis": {},
        },
    )
    monkeypatch.setattr(
        image_selection.ImageSelection,
        "choose_matched",
        lambda _owner, *args, **kwargs: (_ for _ in ()).throw(bot.GlobalImageUnavailable("no images")),
    )
    caplog.set_level(logging.INFO, logger=bot.log.name)

    with pytest.raises(bot.GlobalImageUnavailable):
        bot.post_random_quote(set(), set(), {})

    assert "resetting image cycle and retrying once" not in caplog.text


def test_original_regular_image_posts_without_made_with_ai(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    lines_used, images_used, state, _lines_used_file, _images_used_file, _receipt_file, _lines_file = configure_simple_quote_post(tmp_path, monkeypatch)
    state["original_regular_posts_since_generated_image"] = 0
    create_calls = capture_create_post_calls(monkeypatch)
    caplog.set_level(logging.INFO, logger=bot.log.name)

    bot.post_random_quote(lines_used, images_used, state)

    assert len(create_calls) == 1
    assert create_calls[0]["made_with_ai"] is False
    assert state["original_regular_posts_since_generated_image"] == 0
    assert "GENERATED_IMAGE_SPACING_STATE_UPDATED" not in caplog.text


@pytest.mark.parametrize(
    "chosen_name,other_name,made_with_ai",
    [
        ("arbitrary.PnG", "t01.jpg", True),
        ("t01.jpg", "arbitrary.PnG", False),
    ],
)
def test_regular_catalog_choice_binds_prepared_attempt_and_requested_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    chosen_name: str,
    other_name: str,
    made_with_ai: bool,
) -> None:
    actual_create_post = bot.create_post
    lines_used, images_used, state, _paths = configure_image_cycle_post(
        tmp_path,
        monkeypatch,
        image_analyses={"t01.jpg": portrait_analysis(), "arbitrary.PnG": portrait_analysis()},
        quote_analyses={0: {"seasonality": {"hard_exclude_outside_windows": False}}},
        quotes=["One quotation."],
    )
    images_used.add(other_name)
    observed = []

    def local_transport(method: str, path: str, **kwargs: object) -> dict:
        status, attempt = bot.load_regular_post_receipt()
        assert status == "sending" and attempt is not None
        observed.append((method, path, attempt, kwargs["json"]))
        return {"data": {"id": "950001"}}

    monkeypatch.setattr(bot, "create_post", actual_create_post)
    install_receipt_bound_x_request_stub(monkeypatch, local_transport)
    bot.post_random_quote(lines_used, images_used, state)

    assert len(observed) == 1
    method, path, attempt, payload = observed[0]
    assert (method, path) == ("POST", "/2/tweets")
    assert attempt["selected_identity"]["image_basename"] == chosen_name
    assert attempt["made_with_ai"] is made_with_ai
    assert payload == bot.main_post_attempt_payload(attempt)
    assert payload.get("made_with_ai") is (True if made_with_ai else None)
    assert state["last_regular_image_filename"] == chosen_name


def test_failed_regular_post_attempt_preserves_legacy_counter(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    quote = "Failed posting quote."
    secondary_name = "t99.jpg"
    lines_used, images_used, state, _image_paths = configure_image_cycle_post(
        tmp_path,
        monkeypatch,
        image_analyses={secondary_name: portrait_analysis()},
        quote_analyses={0: quote_rejecting_crowd_scenes()},
        quotes=[quote],
    )
    state["original_regular_posts_since_generated_image"] = 2
    monkeypatch.setattr(
        bot,
        "create_post",
        lambda **kwargs: mock_confirmed_main_post(
            kwargs,
            {"data": {"id": "banana"}},
        ),
    )
    caplog.set_level(logging.INFO, logger=bot.log.name)

    with pytest.raises(RuntimeError, match="did not return a valid post id"):
        bot.post_random_quote(lines_used, images_used, state)

    assert lines_used == set()
    assert images_used == set()
    assert state["original_regular_posts_since_generated_image"] == 2
    assert "GENERATED_IMAGE_SPACING_STATE_UPDATED" not in caplog.text


def test_post_random_quote_restores_histories_when_all_pair_attempts_fail_after_cycle_resets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    image_dir = tmp_path / "images"
    image_dir.mkdir()
    t01 = image_dir / "t01.jpg"
    t23 = image_dir / "t23.jpg"
    t01.write_bytes(b"fake-1")
    t23.write_bytes(b"fake-23")
    lines_file = tmp_path / "quotes.txt"
    lines_file.write_text("Christmas quote.\nBad quote A.\nBad quote B.\n", encoding="utf-8")
    lines_used_file = tmp_path / "lines_used.json"
    images_used_file = tmp_path / "images_used.json"
    monkeypatch.setattr(bot, "LINES_FILE", lines_file)
    monkeypatch.setattr(bot, "IMAGE_GLOB", str(image_dir / "t*"))
    monkeypatch.setattr(bot, "LINES_USED_FILE", lines_used_file)
    monkeypatch.setattr(bot, "IMAGES_USED_FILE", images_used_file)
    monkeypatch.setattr(bot, "current_datetime", lambda: datetime(2026, 7, 5))
    monkeypatch.setattr(bot.random, "uniform", lambda low, high: low)
    monkeypatch.setattr(bot, "upload_media", lambda path, **_kwargs: pytest.fail("upload_media should not be called"))
    monkeypatch.setattr(bot, "create_post", lambda **kwargs: pytest.fail("create_post should not be called"))
    patch_metadata(
        monkeypatch,
        "load_quote",
        lambda: quote_analysis_for_lines(
            ["Christmas quote.", "Bad quote A.", "Bad quote B."],
            {
                0: {
                        "archive_image_preferences": {},
                        "seasonality": {
                            "hard_exclude_outside_windows": True,
                            "preferred_windows": [{"start": "12-10", "end": "12-28"}],
                            "relevance": "christmas",
                        },
                },
                1: {
                        "archive_image_preferences": {"strong_visual_mismatches": ["formal portrait"]},
                        "seasonality": {"hard_exclude_outside_windows": False, "preferred_windows": [], "relevance": "none"},
                },
                2: {
                        "archive_image_preferences": {"strong_visual_mismatches": ["formal portrait"]},
                        "seasonality": {"hard_exclude_outside_windows": False, "preferred_windows": [], "relevance": "none"},
                },
            },
        ),
    )
    patch_metadata(
        monkeypatch,
        "load_image",
        lambda: image_analysis_for_paths(
            [t01, t23],
            {
                "t01.jpg": {
                        "description": "Formal portrait in a studio",
                        "pairing": {},
                        "themes": [],
                        "tone": [],
                        "visual_energy": "low",
                        "quality": {},
                        "seasonality": {"avoid_outside_season_or_occasion": False},
                },
                "t23.jpg": {
                        "description": "Christmas scene",
                        "pairing": {},
                        "themes": [],
                        "tone": [],
                        "visual_energy": "low",
                        "quality": {},
                        "seasonality": {"avoid_outside_season_or_occasion": True, "occasions": ["christmas"]},
                },
            },
        ),
    )

    lines_used: set[str] = {bot.quote_text_hash("Bad quote A."), bot.quote_text_hash("Bad quote B.")}
    images_used: set[str] = {"t01.jpg"}
    original_lines = set(lines_used)
    original_images = set(images_used)

    with pytest.raises(RuntimeError, match="used histories unchanged"):
        bot.post_random_quote(lines_used, images_used, {})

    assert lines_used == original_lines
    assert images_used == original_images
    assert not lines_used_file.exists()
    assert not images_used_file.exists()
